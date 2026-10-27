"""Provider-neutral derived root-depth policy; no host, inference or evidence mutation."""
import hashlib
import json
import math

MODES = ('FIXED', 'AUTOMATIC')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def validate_policy(record, source_hash, character_id):
    if not isinstance(record, dict):
        raise ValueError('Invalid generated Depth policy; previous animation retained')
    payload = {k:v for k,v in record.items() if k != 'sha256'}
    if (set(payload) != {'version','mode','source_video_sha256','character_id','reference_frame','reference_depth'}
            or payload['version'] != 1 or payload['mode'] not in MODES
            or payload['source_video_sha256'] != source_hash or payload['character_id'] != character_id
            or record.get('sha256') != _digest(payload)):
        raise ValueError('Generated Depth policy identity/checksum changed; previous animation retained')
    frame, depth = payload['reference_frame'], payload['reference_depth']
    if payload['mode'] == 'FIXED':
        if (type(frame) is not int or frame < 0 or type(depth) not in (int,float)
                or not math.isfinite(depth) or depth <= 0):
            raise ValueError('Fixed Depth requires a finite positive reference and source frame')
    elif frame is not None or depth is not None:
        raise ValueError('Automatic Depth must not carry a fixed reference')
    return record


def previous_policy(saved, source_hash, character_id):
    if saved is None:
        return None  # Existing unmarked generated output has Automatic semantics.
    try:
        record = json.loads(saved)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid generated Depth policy; previous animation retained') from exc
    return validate_policy(record, source_hash, character_id)


def require_compatible(mode, previous, *, bounded):
    if mode not in MODES:
        raise ValueError('Unsupported generated Depth mode')
    old = previous['mode'] if previous is not None else 'AUTOMATIC'
    if bounded and old != mode:
        raise ValueError('Changing Depth mode requires generating the whole source for the selected '
                         'Characters. Set Animation Start/End to the source bounds and retry; '
                         'previous animation and user Depth keys were retained.')


def make_policy(mode, source_hash, character_id, *, previous=None, frame=None, depth=None):
    require_compatible(mode, previous, bounded=False)
    if mode == 'FIXED' and previous is not None and previous['mode'] == 'FIXED':
        return validate_policy(previous, source_hash, character_id).copy()
    payload = dict(version=1, mode=mode, source_video_sha256=source_hash,
                   character_id=character_id, reference_frame=frame if mode=='FIXED' else None,
                   reference_depth=depth if mode=='FIXED' else None)
    return validate_policy({**payload,'sha256':_digest(payload)}, source_hash, character_id)


def fixed_point(point, origin, forward, reference):
    """Keep the perpendicular component exactly; use actual optical forward, not world Z."""
    if (any(len(v) != 3 for v in (point,origin,forward))
            or any(type(x) not in (float,int) or not math.isfinite(x) for v in (point,origin,forward) for x in v)
            or type(reference) not in (int,float) or not math.isfinite(reference) or reference <= 0):
        raise ValueError('Depth policy requires finite positions, optical axis and positive reference')
    norm = math.sqrt(sum(x*x for x in forward))
    if norm < 1e-8:
        raise ValueError('Depth policy optical axis must be nonzero')
    axis = tuple(x/norm for x in forward)
    depth = sum((p-c)*n for p,c,n in zip(point,origin,axis))
    return tuple(p+(reference-depth)*n for p,n in zip(point,axis))
