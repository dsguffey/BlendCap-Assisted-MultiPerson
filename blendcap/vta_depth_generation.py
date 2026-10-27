"""Apply the derived policy to fresh staged output, before native user offsets."""
import json

import bpy
from mathutils import Vector

from ..blendcap_vta_depth_policy import previous_policy, require_compatible, make_policy, fixed_point


def preflight(objects, mode, source_hash, *, bounded):
    for obj in objects:
        saved = previous_policy(obj.get('vta_depth_policy'), source_hash, obj['vta_character_id'])
        require_compatible(mode, saved, bounded=bounded)


def apply(obj, camera_matrix, schedule, offset, mode, source_hash, character_id, previous=None):
    """Only a fresh canonical rig/Action is mutable here. Camera/owner are read-only."""
    saved = previous_policy(previous.get('vta_depth_policy'), source_hash, character_id) if previous else None
    samples = [s['frame'] for s in schedule if s['state'] != 'unavailable']
    if not samples:
        raise ValueError('Depth policy needs an available generated pose')
    if obj.parent is not None or obj.constraints or any(b.constraints for b in obj.pose.bones):
        raise ValueError('Depth policy requires an unconstrained fresh staged rig')
    root, hips = obj.pose.bones.get('Root'), obj.pose.bones.get('Hips')
    if root is None or hips is None or root.parent is not None:
        raise ValueError('Depth policy requires the generated Root/Hips skeleton')
    origin = camera_matrix.translation
    forward = (camera_matrix.to_3x3() @ Vector((0,0,-1))).normalized()
    def point(frame):
        bpy.context.scene.frame_set(frame + offset)
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return evaluated.matrix_world @ evaluated.pose.bones['Hips'].head
    first = point(samples[0])
    policy = make_policy(mode, source_hash, character_id, previous=saved,
                         frame=samples[0], depth=float((first-origin).dot(forward)))
    if mode == 'FIXED':
        basis = (obj.matrix_world @ root.bone.matrix_local).to_3x3().inverted()
        for frame in samples:
            current = point(frame)
            desired = Vector(fixed_point(tuple(current),tuple(origin),tuple(forward),policy['reference_depth']))
            root.location += basis @ (desired-current)
            root.keyframe_insert('location', frame=frame+offset)
        from .retarget.fcurves import _iter_action_fcurves
        path = root.path_from_id('location')
        for curve in _iter_action_fcurves(obj.animation_data.action):
            if curve.data_path == path:
                for key in curve.keyframe_points:key.interpolation='CONSTANT'
                curve.update()
    obj['vta_depth_policy'] = json.dumps(policy,sort_keys=True)
    return policy
