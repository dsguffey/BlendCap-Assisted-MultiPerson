"""GPL body/FOV provider translation only; no VTA domain or Blender objects.

Extracted from the accepted reviewed reconstruction worker. Provider integrations
remain public; private orchestration consumes only neutral numbers/artifacts.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib,os,json
from pathlib import Path

def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def bbox_for_backend(box, height):
    x1, y1, x2, y2 = box
    return (x1, height-y2, x2, height-y1)


def export_command(python, bundle, pose_path, bvh_path):
    return [str(python), str(bundle / 'npz_to_bvh.py'), '--input', str(pose_path),
            '--output', str(bvh_path), '--mhr-model',
            str(bundle / 'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt'),
            '--export', 'body', '--rest-pose', 'tpose', '--root-motion', '--no-center',
            '--no-foot-lock', '--smooth', '0', '--smooth-root', '0', '--smooth-vertical', '0']


def infer_reviewed_pose(estimator, np, torch, rgb, evidence, height, cam_int=None):
    """One exact reviewed box, one aligned pose; no detector or continuity fallback."""
    box = np.asarray([bbox_for_backend(evidence['box_xyxy'], height)], dtype=np.float32)
    with torch.inference_mode():
        options = {} if cam_int is None else {'cam_int': cam_int}
        result = estimator.process_one_image(rgb, bboxes=box, inference_type='full', **options)
    if not isinstance(result, list) or len(result) != 1:
        raise RuntimeError('Explicit one-box reconstruction did not return exactly one pose')
    person = result[0]
    if not np.array_equal(np.asarray(person['bbox']), box[0]):
        raise RuntimeError('Reconstruction returned a different candidate box')
    arrays = {key: np.asarray(person[key]) for key in
              ('pred_global_rots', 'pred_joint_coords', 'pred_cam_t')}
    rots, joints, cam = arrays.values()
    if (rots.ndim != 3 or rots.shape[1:] != (3,3) or joints.shape != (rots.shape[0],3)
            or cam.shape != (3,)):
        raise RuntimeError('Reconstruction returned malformed skeletal channels')
    if not all(np.isfinite(value).all() for value in arrays.values()):
        return None
    focal = np.asarray(person.get('focal_length'))
    if focal.size != 1 or not np.issubdtype(focal.dtype, np.number) or not np.isfinite(focal).all() or focal.item() <= 0:
        raise RuntimeError('Reconstruction requires a finite positive source focal_length')
    arrays['focal_length'] = float(focal.item())
    if cam_int is not None and abs(arrays['focal_length'] - float(cam_int[0, 0, 0])) > 1e-3:
        raise RuntimeError('Reconstruction did not use the fixed source calibration')
    return arrays


def runtime_identity(bundle, provenance, estimator, torch):
    """Actual offline model/code/config identity, never a path or a UI label alone."""
    from importlib.metadata import version, PackageNotFoundError
    def package(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return None
    trees = {}
    for name in ('models/fast-sam-3d-body', 'models/dinov3_hub'):
        folder = bundle / name
        if name == 'models/dinov3_hub':
            override = os.environ.get('DINOV3_HUB_DIR')
            default = folder / 'facebookresearch_dinov3_main'
            if override and Path(override).resolve() != default.resolve():
                folder = Path(override)
        files = {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob('*.py'))}
        if not files:
            raise RuntimeError('Installed reconstruction provider code is missing: ' + name)
        trees[name] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if os.environ.get('FOV_TRT', '0') == '1':
        engine = bundle/'models/fast-sam-3d-body/checkpoints/moge_trt/moge_dinov2_encoder_fp16.engine'
        trees['fov_trt_engine'] = digest(engine) if engine.is_file() else None
        trees['tensorrt_version'] = package('tensorrt')
    size = os.environ.get('FOV_MODEL', 's')
    short = {'s': 'vits', 'b': 'vitb', 'l': 'vitl'}.get(size, 'vitl')
    fov = Path(os.environ.get('BLENDCAP_MOGE_BASE', str(bundle/'checkpoints'))) / ('moge-2-'+short+'-normal/model.pt')
    settings = {key: os.environ.get(key, default) for key, default in {
        'FOV_MODEL': 's', 'FOV_FAST': '1', 'FOV_LEVEL': '0', 'FOV_SIZE': '0', 'FOV_TRT': '0',
        'SKIP_KEYPOINT_PROMPT': '1', 'BODY_INTERM_PRED_LAYERS': '0,1,2',
        'HAND_INTERM_PRED_LAYERS': '0,1', 'MHR_NO_CORRECTIVES': '1', 'GPU_HAND_PREP': '1',
        'LAYER_DTYPE': 'fp32', 'USE_ORT': '0', 'USE_COMPILE': '0',
        'USE_COMPILE_BACKBONE': '0', 'DECODER_COMPILE': '0'}.items()}
    return {'backend': 'Fast-SAM-3D-Body', 'inference_type': 'full', 'settings': settings,
        'asset_hashes': {key: provenance[key] for key in ('checkpoint_sha256', 'mhr_sha256',
            'config_sha256', 'worker_sha256', 'capture_source_sha256', 'export_source_sha256',
            'private_plan_source_sha256', 'registration_source_sha256', 'calibration_source_sha256')},
        'provider_code': trees, 'fov_checkpoint_sha256': digest(fov) if fov.is_file() else None,
        'fov_available': estimator.fov_estimator is not None, 'device': str(estimator.device),
        'packages': {name: package(name) for name in ('torch', 'numpy', 'opencv-python', 'moge')},
        'torch_cuda': getattr(getattr(torch, 'version', None), 'cuda', None),
        'reconstruction_algorithm': 'reviewed-box-full-raw-v1'}
