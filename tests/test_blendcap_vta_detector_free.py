"""Real loader/estimator control flow with tiny model I/O; no GPU or weights."""
import builtins
from contextlib import contextmanager, nullcontext, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest import mock

import numpy as np

from blendcap_vta_reconstruct_worker import infer_reviewed_pose


ROOT = Path(__file__).resolve().parents[1]
FAST = ROOT / 'models/fast-sam-3d-body'


class Tensor:
    def __init__(self, value):
        self.value = np.asarray(value)

    def __getitem__(self, key):
        return Tensor(self.value[key])

    def cpu(self):
        return self

    def numpy(self):
        return self.value

    def to(self, *_):
        return self

    def clone(self):
        return Tensor(self.value.copy())


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def runtime():
    """Execute the actual modules, substituting only heavy dependencies/I/O."""
    cuda = NS(is_available=lambda: True, empty_cache=mock.Mock(), synchronize=mock.Mock())
    torch = NS(cuda=cuda, no_grad=lambda: lambda f: f, inference_mode=nullcontext)
    transforms = NS(Compose=mock.Mock(), GetBBoxCenterScale=mock.Mock(),
                    TopdownAffine=mock.Mock(), VisionTransformWrapper=mock.Mock())
    cv = NS(COLOR_RGB2BGR=1, COLOR_BGR2RGB=2, cvtColor=lambda image, _: image)
    body = ModuleType('sam_3d_body')
    tools = ModuleType('tools')
    tools.__path__ = [str(FAST / 'tools')]
    yolo_model = mock.Mock(return_value=[])
    yolo_model.model_name = 'yolo11m-pose.pt'
    yolo = mock.Mock(return_value=yolo_model)
    modules = {'torch': torch, 'cv2': cv, 'pkg_resources': ModuleType('pkg_resources'),
        'sam_3d_body': body, 'sam_3d_body.data.transforms': transforms,
        'sam_3d_body.data.utils.io': NS(load_image=mock.Mock()),
        'sam_3d_body.data.utils.prepare_batch': NS(prepare_batch=mock.Mock()),
        'sam_3d_body.utils': NS(recursive_to=lambda value, _: value),
        'torchvision.transforms': NS(ToTensor=mock.Mock()), 'tools': tools,
        'tools.build_fov_estimator': NS(FOVEstimator=mock.Mock(return_value=None))}
    original_import = builtins.__import__
    imports = []

    def guarded_import(name, *args, **kwargs):
        imports.append(name)
        if name.split('.')[0] in ('ultralytics', 'thop') or name == 'tools.build_detector':
            raise AssertionError('Forbidden detector import: ' + name)
        return original_import(name, *args, **kwargs)

    with mock.patch.dict(os.environ, USE_ORT='0', USE_COMPILE='0', MHR_USE_CUDA_GRAPH='0'), \
            mock.patch.object(sys, 'path', sys.path.copy()), \
            mock.patch.dict(sys.modules, modules), \
            mock.patch.object(builtins, '__import__', side_effect=guarded_import), \
            redirect_stdout(io.StringIO()):
        estimator_module = load_source('_vta_estimator_test', FAST / 'sam_3d_body/sam_3d_body_estimator.py')
        model = NS(device='test-device', head_pose=NS(faces=Tensor([])),
                   _initialize_batch=mock.Mock(), run_inference=mock.Mock())
        cfg = NS(MODEL=NS(IMAGE_SIZE=(8, 8)))
        body.load_sam_3d_body = mock.Mock(return_value=(model, cfg))
        body.SAM3DBodyEstimator = estimator_module.SAM3DBodyEstimator
        loader = load_source('_vta_loader_test', ROOT / 'save_mhr_data.py')
        with mock.patch.object(Path, 'exists', autospec=True, return_value=True):
            # Actual construction; do not read a model or a checkpoint.
            estimator = loader.load_estimator_fast()
        yield NS(loader=loader, estimator=estimator, module=estimator_module, model=model,
                 body=body, torch=torch, imports=imports, yolo=yolo, yolo_model=yolo_model)


def install_pose_io(rt):
    seen = []

    def prepare(image, transform, boxes, masks, scores):
        seen.append((image.copy(), boxes.copy(), masks, scores, transform))
        return {'img': np.zeros((1, 1, 3, 8, 8)), 'bbox': Tensor(boxes[None]),
                'cam_int': Tensor(np.eye(3)[None])}

    rt.module.prepare_batch = prepare
    out = {key: np.zeros((1, 3)) for key in ('pred_keypoints_3d', 'pred_keypoints_2d',
        'pred_vertices', 'pred_pose_raw', 'global_rot', 'body_pose', 'hand', 'scale', 'shape', 'face')}
    out.update(focal_length=np.asarray([700.]), pred_cam_t=np.asarray([[0., 0., 2.]]),
               pred_joint_coords=np.zeros((1, 2, 3)), joint_global_rots=np.eye(3)[None, None].repeat(2, axis=1))
    hand = {'bbox_center': NS(flatten=lambda *_: np.asarray([[2., 2.]])),
            'bbox_scale': NS(flatten=lambda *_: np.asarray([[1., 1.]]))}
    rt.model.run_inference.return_value = ({'mhr': out}, hand, hand, None, None)
    return seen


class DetectorFreeTests(unittest.TestCase):
    def test_worker_cli_owns_utf8_logging_without_ultralytics_side_effects(self):
        import blendcap_vta_reconstruct_worker as worker
        out_bytes, err_bytes = io.BytesIO(), io.BytesIO()
        args = ['worker', '--core-root', 'core', '--blendcap-root', 'bundle',
                '--companion-root', 'project', '--video', 'video.mp4']
        with io.TextIOWrapper(out_bytes, encoding='cp1252') as out, \
                io.TextIOWrapper(err_bytes, encoding='cp1252') as err, \
                mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err), \
                mock.patch.object(sys, 'argv', args), \
                mock.patch.object(worker, 'run', side_effect=lambda _: print('\u250c\u2500\u2510')) as run:
            worker.main()
            out.flush()
            run.assert_called_once()
            self.assertEqual(out.encoding, 'utf-8')
            self.assertEqual(err.encoding, 'utf-8')
            self.assertIn('\u250c\u2500\u2510', out_bytes.getvalue().decode('utf-8'))

    def test_loader_skips_detector_imports_weights_and_construction(self):
        with runtime() as rt:
            original_open = builtins.open
            touched = []

            def check(path):
                p = Path(path)
                if 'yolo' in p.parts or p.name.startswith('yolo11'):
                    touched.append(str(p))
                    raise AssertionError('YOLO path must not be accessed')

            def exists(path):
                check(path)
                return True  # Body checkpoint exists; no physical weights.

            def open_file(path, *args, **kwargs):
                check(path)
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, 'exists', exists), \
                    mock.patch.object(builtins, 'open', side_effect=open_file):
                estimator = rt.loader.load_estimator_fast()
            self.assertFalse(hasattr(estimator, 'detector'))
            self.assertFalse(hasattr(estimator, 'require_bboxes'))
            self.assertEqual(touched, [])
            self.assertFalse(any(n.split('.')[0] in ('ultralytics', 'thop') for n in rt.imports))
            self.assertNotIn('tools.build_detector', rt.imports)
            rt.yolo.assert_not_called()
            rt.body.load_sam_3d_body.assert_called_with(
                checkpoint_path=str(rt.loader.CKPT_DIR / 'sam-3d-body-dinov3/model.ckpt'),
                mhr_path=str(rt.loader.CKPT_DIR / 'sam-3d-body-dinov3/assets/mhr_model.pt'),
                device='cuda')
            self.assertEqual(rt.body.load_sam_3d_body.call_count, 2)
            sys.modules['tools.build_fov_estimator'].FOVEstimator.assert_has_calls(
                [mock.call(name='moge2', device='cuda')] * 2)

    def test_reviewed_pose_preserves_exact_normalized_inputs_and_repeat_output(self):
        evidence = {'box_xyxy': [10, 20, 80, 200]}
        image = np.zeros((300, 100, 3), dtype=np.uint8)
        results, normalized = [], []
        for _ in range(2):
            with runtime() as rt:
                seen = install_pose_io(rt)
                results.append(infer_reviewed_pose(rt.estimator, np, rt.torch, image, evidence, 300))
                normalized.append(seen[0][1])
                self.assertEqual(seen[0][2:4], (None, None))
                self.assertEqual(rt.model.run_inference.call_args.kwargs['inference_type'], 'full')
                self.assertNotIn('hand_box_source', rt.model.run_inference.call_args.kwargs)
                self.assertNotIn('yolo_pose_keypoints', rt.model.run_inference.call_args.kwargs)
                rt.yolo_model.assert_not_called()
        np.testing.assert_array_equal(normalized[0], normalized[1])
        np.testing.assert_array_equal(normalized[1], [[10, 100, 80, 280]])
        for key in results[0]:
            np.testing.assert_array_equal(results[0][key], results[1][key])

    def test_missing_reviewed_boxes_fail_before_any_fallback_or_gpu_work(self):
        with runtime() as rt:
            with self.assertRaisesRegex(RuntimeError, 'requires explicit bboxes; detector inference is disabled'):
                rt.estimator.process_one_image(np.zeros((8, 8, 3)))
            rt.torch.cuda.empty_cache.assert_not_called()
            rt.model.run_inference.assert_not_called()

    def test_empty_and_malformed_boxes_never_fall_back_to_detection(self):
        with runtime() as rt:
            for boxes in (np.empty((0, 4)), np.asarray([1, 2, 3]),
                          np.asarray([[0, 0, float('nan'), 3]]),
                          np.asarray([[2, 2, 1, 3]]), np.asarray([['a', 'b', 'c', 'd']])):
                with self.subTest(boxes=boxes), self.assertRaises(ValueError):
                    rt.estimator.process_one_image(np.zeros((8, 8, 3)), bboxes=boxes)
            rt.model.run_inference.assert_not_called()
            rt.yolo.assert_not_called()

    def test_worker_missing_or_malformed_evidence_never_invokes_estimator(self):
        estimator = NS(process_one_image=mock.Mock())
        for evidence in ({}, {'box_xyxy': None}, {'box_xyxy': [1, 2, 3]}):
            with self.subTest(evidence=evidence), self.assertRaises((KeyError, TypeError, ValueError)):
                infer_reviewed_pose(estimator, np, NS(inference_mode=nullcontext),
                                    np.zeros((8, 8, 3)), evidence, 8)
        estimator.process_one_image.assert_not_called()

    def test_no_opt_in_or_default_can_restore_retired_detection(self):
        with runtime() as rt:
            with self.assertRaises(TypeError):
                rt.loader.load_estimator_fast(load_detector=True)
            with self.assertRaises(TypeError):
                rt.module.SAM3DBodyEstimator(rt.model, rt.estimator.cfg, human_detector=mock.Mock())
            self.assertFalse((FAST / 'tools/build_detector.py').exists())
            self.assertFalse(hasattr(rt.loader, 'main'))
            self.assertFalse(hasattr(rt.loader, 'run_preview'))
            self.assertFalse(hasattr(rt.loader, 'load_detector_only'))

    def test_missing_body_checkpoint_still_fails_without_fallback(self):
        with runtime() as rt:
            with mock.patch.object(Path, 'exists', return_value=False), self.assertRaises(SystemExit):
                rt.loader.load_estimator_fast()
            self.assertEqual(rt.body.load_sam_3d_body.call_count, 1)


if __name__ == '__main__':
    unittest.main()
