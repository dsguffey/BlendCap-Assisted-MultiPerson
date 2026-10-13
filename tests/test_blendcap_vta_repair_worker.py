"""Actual worker flow with deterministic fake model I/O; no inference or downloads."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from contextlib import nullcontext
from unittest import mock
import unittest

import blendcap_vta_reconstruct_worker as worker


class RepairWorkerTests(unittest.TestCase):
    def exercise(self, mode, missing_right=False, fov_mode='EVERY', failed_calibration=False):
        private = Path(__file__).resolve().parents[2] / 'Video-to-Animation'
        if not (private/'vta_core').is_dir():
            self.skipTest('private core checkout is required for the worker integration contract')
        sys.path.insert(0, str(private))
        import vta_core
        import vta_core.source_decode as decode
        import vta_core.source_preflight as preflight
        import numpy as np
        samples = [dict(frame=f, mode='infer', repair_mode=mode if 23 <= f <= 25 else None,
                        continuity=0, reason='raw', evidence={'box_xyxy':[0,0,10,10]})
                   for f in range(22,27)]
        plan = {'height':10, 'width':10, 'fps':24, 'version':3,
                'characters':[{'character_id':'white','display_name':'White',
                    'samples':samples[1:4], 'repair_context':[samples[0],samples[4]]}]}
        original = copy.deepcopy(plan)
        calls = []
        matrices = []
        def infer(_estimator, _np, _torch, rgb, _evidence, _height, cam_int=None):
            frame = int(rgb[0,0,0])
            calls.append(frame)
            matrices.append(None if cam_int is None else cam_int.copy())
            if missing_right and frame == 26:
                return None
            return {'pred_global_rots':np.tile(np.eye(3),(2,1,1)),
                    'pred_joint_coords':np.full((2,3),float(frame)),
                    'pred_cam_t':np.array([0.,0.,2.]), 'focal_length':100.}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            bundle, root, video = folder/'bundle', folder/'project', folder/'video.mp4'
            for relative in ('checkpoints/sam-3d-body-dinov3/model.ckpt',
                             'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt',
                             'checkpoints/sam-3d-body-dinov3/model_config.yaml',
                             'checkpoints/yolo/yolo11m-pose.pt', 'save_mhr_data.py','npz_to_bvh.py'):
                path = bundle/relative
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('test fixture')
            project = SimpleNamespace(to_dict=lambda:{'active_analysis_revision_by_source':{'source':'revision'}})
            source = vta_core.SourceReference('source',24.,0,30,'0'*64,10,10)
            def export(command, **kwargs):
                Path(command[command.index('--output')+1]).write_text('synthetic raw BVH')
            args = SimpleNamespace(core_root=str(private),blendcap_root=str(bundle),
                companion_root=str(root),video=str(video),start_frame=23,end_frame=25,fov_mode=fov_mode)
            camera = mock.Mock(side_effect=RuntimeError('calibration failed') if failed_calibration else None,
                               return_value=np.asarray([[[100.,0,5],[0,100.,5],[0,0,1]]],dtype=np.float32))
            fake_runtime = SimpleNamespace(load_estimator_fast=lambda:SimpleNamespace(device='test-only',
                fov_estimator=SimpleNamespace(get_cam_intrinsics=camera) if fov_mode=='FIRST' else None))
            fake_cv = SimpleNamespace(COLOR_BGR2RGB=1,cvtColor=lambda pixels,_:pixels)
            with mock.patch.dict(os.environ, {}), mock.patch.dict(sys.modules,
                    {'save_mhr_data':fake_runtime,'cv2':fake_cv,'torch':SimpleNamespace(
                        inference_mode=nullcontext,float32=np.float32,as_tensor=np.asarray)}), \
                    mock.patch.object(vta_core,'load_project_state',return_value=project), \
                    mock.patch.object(vta_core,'load_analysis_cache',return_value=object()), \
                    mock.patch.object(vta_core,'build_skeletal_plan',side_effect=lambda *_a,**_k:copy.deepcopy(plan)), \
                    mock.patch('blendcap_vta_analyze_worker._source_for_video',return_value=source), \
                    mock.patch.object(preflight,'preflight_source'), \
                    mock.patch.object(decode,'decode_source_frame',side_effect=lambda _v,r:
                        SimpleNamespace(pixels_bgr=np.full((10,10,3),r.frame,dtype=np.uint8))), \
                    mock.patch.object(worker,'infer_reviewed_pose',side_effect=infer), \
                    mock.patch.object(worker,'runtime_identity',return_value={
                        'test-provider':'unchanged','fov_checkpoint_sha256':'a'*64}), \
                    mock.patch.object(worker.subprocess,'run',side_effect=export) as exporter:
                if failed_calibration:
                    with self.assertRaisesRegex(RuntimeError,'calibration failed'):
                        worker.run(args)
                    self.assertFalse(list(root.rglob('output.json')))
                    self.assertFalse(list(root.rglob('*.npz')))
                    self.assertFalse(list((root/'reconstruction/calibration').glob('*.json')))
                    exporter.assert_not_called()
                    self.assertFalse((root/'analysis/vta-analysis.lock').exists())
                    self.assertEqual(calls,[])
                    return
                if missing_right:
                    with self.assertRaisesRegex(vta_core.StateModelError,'valid trustworthy pose is required on both sides'):
                        worker.run(args)
                    self.assertFalse(list(root.rglob('output.json')))
                    self.assertFalse(list(root.rglob('*.npz')))
                    exporter.assert_not_called()
                else:
                    report = json.loads(worker.run(args).read_text())
                    self.assertEqual(report['version'],4)
                    record = report['characters'][0]
                    expected_frames=list(range(22,27 if mode=='interpolate' else 26))
                    self.assertEqual(record['source_frames'],expected_frames)
                    self.assertEqual([s['state'] for s in record['schedule']],
                                     ['frozen' if mode=='freeze' else 'interpolated']*3)
                    with np.load(next(root.rglob('*.npz')),allow_pickle=False) as raw:
                        self.assertEqual(raw['frame_indices'].tolist(),expected_frames)
                        # Rejected finite raw poses survive separately from repaired animation.
                        self.assertEqual(raw['joint_coords'][:,0,0].tolist(),expected_frames)
                    if fov_mode=='FIRST':
                        fingerprint=report['reconstruction']['fingerprint']
                        again=json.loads(worker.run(args).read_text())
                        self.assertEqual(again['reconstruction']['fingerprint'],fingerprint)
                        camera.assert_called_once()
                        self.assertEqual(int(camera.call_args.args[0][0,0,0]),0)
                        self.assertTrue(all(np.array_equal(matrix,matrices[0]) for matrix in matrices))
                    else:
                        camera.assert_not_called()
                        self.assertTrue(all(matrix is None for matrix in matrices))
                self.assertFalse((root/'analysis/vta-analysis.lock').exists())
                self.assertEqual(plan,original)  # No implicit trust changes.
                self.assertEqual(calls[:3],[23,24,25])  # Rejection did not suppress reconstruction.
                self.assertIn(22,calls)
                if mode=='interpolate':
                    self.assertIn(26,calls)

    def test_interpolate_reconstructs_rejected_raw_and_context_before_publication(self):
        self.exercise('interpolate')

    def test_missing_right_anchor_never_exports_or_publishes_partial_output(self):
        self.exercise('interpolate',missing_right=True)

    def test_first_interpolate_anchors_and_repeated_bounded_runs_reuse_source_zero(self):
        self.exercise('interpolate',fov_mode='FIRST')

    def test_first_freeze_anchor_reuses_source_zero(self):
        self.exercise('freeze',fov_mode='FIRST')

    def test_first_calibration_failure_never_publishes_pose_or_partial_output(self):
        self.exercise('interpolate',fov_mode='FIRST',failed_calibration=True)
