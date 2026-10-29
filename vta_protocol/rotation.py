"""Neutral rotation result validation. SPDX-License-Identifier: CC0-1.0"""
import hashlib
import json
import math

ALGORITHM = 'masked-background-rotation-v4-analysis-skips'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def validate_request(request):
    if request.get('algorithm') != ALGORITHM:
        raise ValueError('Unsupported rotation solve algorithm.')
    for field in ('scene_id', 'project_id', 'source_id', 'video_sha256', 'cache_sha256', 'calibration'):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError('Rotation solve lacks source/Scene provenance.')
    for field in ('width', 'height'):
        if type(request.get(field)) is not int or request[field] < 32:
            raise ValueError('Invalid rotation source dimensions.')
    first, last = request.get('range', (None, None))
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise ValueError('Invalid rotation source-frame range.')
    if (not isinstance(request.get('focal_px'), (float, int))
            or not math.isfinite(request['focal_px']) or request['focal_px'] <= 0):
        raise ValueError('Rotation cleanup requires fixed positive source intrinsics.')
    if type(request.get('offset')) is not int:
        raise ValueError('Rotation solve needs an exact Blender/source frame offset.')
    if not isinstance(request.get('fps'),(float,int)) or not math.isfinite(request['fps']) or request['fps']<=0:
        raise ValueError('Rotation solve needs source playback timing.')
    matrix=request.get('source_camera_matrix')
    if (not isinstance(matrix,list) or len(matrix)!=4 or any(not isinstance(row,list) or len(row)!=4 for row in matrix)
            or any(not isinstance(v,(int,float)) or not math.isfinite(v) for row in matrix for v in row)
            or matrix[3]!=[0,0,0,1]):
        raise ValueError('Rotation solve needs the fixed Blender source-camera world matrix.')
    return request


def validate_solve(solve, request):
    import numpy as np
    validate_request(request)
    if (solve.get('schema') != 'vta-camera-rotation' or solve.get('version') != 3
            or solve.get('request') != request or solve.get('request_sha256') != digest(request)
            or solve.get('translation') != 'not-estimated'
            or solve.get('convention') != 'cv-right-down-forward; world-to-camera; first-frame-identity'):
        raise ValueError('Camera rotation solve belongs to different Scene/source data.')
    payload = {k:v for k,v in solve.items() if k != 'sha256'}
    if solve.get('sha256') != digest(payload):
        raise ValueError('Camera rotation solve checksum changed.')
    count = request['range'][1]-request['range'][0]+1
    raw = solve.get('rotations')
    if not isinstance(raw,list) or len(raw) != count or raw[0] is None:
        raise ValueError('Camera rotation solve has incomplete frame coverage.')
    skipped = solve.get('skipped')
    expected = [request['range'][0]+i for i,r in enumerate(raw) if r is None]
    if (not isinstance(skipped,list) or any(not isinstance(row,dict)
            or type(row.get('frame')) is not int or not isinstance(row.get('reason'),str)
            or not row['reason'] for row in skipped)
            or [row['frame'] for row in skipped] != expected):
        raise ValueError('Camera rotation skip coverage is inconsistent.')
    # Identity placeholders are only for numerical validation/consumers. A null
    # entry MUST be skipped by the bake, never treated as an estimated rotation.
    rotations = np.asarray([np.eye(3) if r is None else r for r in raw], dtype=float)
    if rotations.shape != (count, 3, 3) or not np.isfinite(rotations).all():
        raise ValueError('Camera rotation solve has incomplete frame coverage.')
    if (not np.allclose(rotations[0], np.eye(3), atol=1e-8)
            or not np.allclose(rotations.transpose(0,2,1) @ rotations, np.eye(3), atol=1e-6)
            or not np.allclose(np.linalg.det(rotations), 1., atol=1e-6)):
        raise ValueError('Invalid camera rotation matrices.')
    components=solve.get('components')
    if not isinstance(components,list) or len(components)!=count:
        raise ValueError('Camera component coverage is incomplete.')
    for index,row in enumerate(components):
        if (not isinstance(row,dict) or row.get('frame')!=request['range'][0]+index
                or set(row.get('accepted',{}))!={'pan','tilt','roll'}
                or any(type(v) is not bool for v in row['accepted'].values())
                or row['accepted']['pan']!=row['accepted']['tilt']
                or (row['accepted']['pan'] and not row['accepted']['roll'])
                or (raw[index] is None)!= (not any(row['accepted'].values()))):
            raise ValueError('Invalid camera component acceptance metadata.')
        if any(not passed and not isinstance(row.get('reasons',{}).get(name),str)
               for name,passed in row['accepted'].items()):
            raise ValueError('Rejected camera components need review reasons.')
        if raw[index] is not None and not row['accepted']['pan']:
            r=rotations[index]
            if not np.allclose(r[2],[0,0,1],atol=1e-8) or not np.allclose(r[:,2],[0,0,1],atol=1e-8):
                raise ValueError('Roll-only cleanup contains unsupported pan/tilt.')
    return rotations
