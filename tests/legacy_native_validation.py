"""Frozen GPL callback validators for historical test fixtures only."""
import types
def install(module):
    for function in (resolve_scene_reference,validate_camera_registration):
        setattr(module,function.__name__,types.FunctionType(function.__code__,module.__dict__,function.__name__,function.__defaults__))

def resolve_scene_reference(plan, records, previous, core, output_path):
    """Reuse the fixed derived reference; bounded updates never recenter it."""
    saved = previous.data.get('vta_scene_reference') if previous is not None else None
    if saved is not None:
        reference = json.loads(saved)
        evidence = reference['reference']
        origin_report = Path(reference['output_file'])
        if not origin_report.is_file() or _hash(origin_report) != reference['output_sha256']:
            raise ValueError('VTA scene-reference reconstruction changed or is unavailable')
        origin = json.loads(origin_report.read_text(encoding='utf-8'))
        original_samples = [{'character_id': r['character_id'],
            'samples': r['camera_registration']['samples']} for r in origin['characters']
            if (r.get('camera_registration') or {}).get('samples')]
        expected = core.scene_reference_from_samples(original_samples, plan['width'], plan['height'])
        if (evidence != expected or reference['source_video_sha256'] != plan['source_video_sha256']
                or origin['plan']['source_video_sha256'] != plan['source_video_sha256']):
            raise ValueError('VTA scene reference is incompatible or was edited')
        return reference
    participants = [{'character_id': r['character_id'],
                     'samples': r['camera_registration']['samples']}
                    for r in records if (r.get('camera_registration') or {}).get('samples')]
    return {'reference': core.scene_reference_from_samples(participants, plan['width'], plan['height']),
            'source_video_sha256': plan['source_video_sha256'],
            'output_file': str(Path(output_path).resolve()), 'output_sha256': _hash(output_path)}


def validate_camera_registration(record, plan, root, core, reconstruction=None):
    """Bind derived calibration to the hashed raw NPZ, not object placement."""
    import numpy as np
    registration = record.get('camera_registration')
    if not record['source_frames']:
        if registration is not None:
            raise ValueError('Empty pose record must not contain camera registration')
        return []
    path = _inside(root, record['npz'])
    if _hash(path) != record['npz_sha256']:
        raise ValueError('Camera registration NPZ hash changed')
    with np.load(path, allow_pickle=False) as raw:
        if reconstruction is not None:
            if ('reconstruction_fingerprint' not in raw
                    or raw['reconstruction_fingerprint'].item() != reconstruction['fingerprint']):
                raise ValueError('Raw reconstruction calibration fingerprint differs from output')
            if reconstruction['mode'] == 'FIRST':
                if not np.allclose(raw['focal_length'], reconstruction['calibration']['intrinsics'][0][0],
                                   atol=1e-3, rtol=0):
                    raise ValueError('Raw FIRST focal lengths differ from fixed source calibration')
        if list(raw['frame_indices']) != record['source_frames']:
            raise ValueError('Camera registration source frames differ from NPZ')
        samples = [core.source_camera_sample(f, (raw['joint_coords'][i, 1] + raw['cam_t'][i]).tolist(),
            float(raw['focal_length'][i]), plan['width'], plan['height'])
            for i, f in enumerate(record['source_frames'])]
    expected = {'version': 1,
        'convention': 'camera-centred-metres; world=(cv-x,cv-z,-cv-y); centred-square-pixels',
        'samples': samples}
    if registration != expected:
        raise ValueError('Camera registration differs from raw reconstruction calibration')
    return samples
