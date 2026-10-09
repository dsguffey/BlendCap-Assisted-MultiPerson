"""Blender-only import of immutable reviewed reconstruction, never review authority."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import bpy
from .helpers import _import_bvh
from .retarget.fcurves import _iter_action_fcurves
_file_hash_cache = {}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cached_hash(path):
    path = Path(path).resolve()
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _file_hash_cache.get(path)
    if cached is None or cached[:2] != signature:
        cached = (*signature, _hash(path))
        _file_hash_cache[path] = cached
    return cached[2]


def _inside(root, name):
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('Skeletal artifact must be a relative filename')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Skeletal artifact is missing or outside its output directory')
    return path


def retime_reviewed_action(obj, source_frames, schedule, offset):
    """Copy final imported pose channels, including root, to exact source time.

    CONSTANT samples deliberately permit visible reacquisition jumps. Hold is
    a copy of ALL evaluated BVH channels, not a timeline marker. Missing spans
    are hidden, not animated arbitrary rest poses.
    """
    action = obj.animation_data.action
    curves = list(_iter_action_fcurves(action))
    if (not curves or not source_frames or len(set(source_frames)) != len(source_frames)
            or source_frames != sorted(source_frames)):
        raise ValueError('Skeletal action has no uniquely mapped pose samples')
    sample_indices = {frame: index + 1 for index, frame in enumerate(source_frames)}
    copied = [(fc, {frame: fc.evaluate(index) for frame, index in sample_indices.items()})
              for fc in curves]
    for fc, values in copied:
        samples = [(s['frame'] + offset, values[s['pose_source_frame']])
                   for s in schedule if s['pose_source_frame'] is not None]
        while fc.keyframe_points:
            fc.keyframe_points.remove(fc.keyframe_points[-1], fast=True)
        fc.keyframe_points.add(len(samples))
        fc.keyframe_points.foreach_set('co', [value for pair in samples for value in pair])
        for point in fc.keyframe_points:
            point.interpolation = 'CONSTANT'
        fc.update()
    for sample in schedule:
        unavailable = sample['state'] == 'unavailable'
        obj.hide_viewport = unavailable
        obj.hide_render = unavailable
        obj.keyframe_insert('hide_viewport', frame=sample['frame'] + offset)
        obj.keyframe_insert('hide_render', frame=sample['frame'] + offset)
    for fc in _iter_action_fcurves(action):
        for point in fc.keyframe_points:
            point.interpolation = 'CONSTANT'


def import_reviewed_output(path, location, backend):
    root = Path(path).resolve().parent
    expected_root = (location.companion_root / 'reconstruction').resolve()
    if not root.is_relative_to(expected_root):
        raise ValueError('Skeletal output is outside this project companion')
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if report.get('schema') != 'vta-reviewed-skeletal-output' or report.get('version') != 1:
        raise ValueError('Unsupported skeletal output schema')
    project, cache, _revision = backend._load_context(location)
    plan = backend._core.build_skeletal_plan(project, cache)
    if report['plan'] != plan:
        raise ValueError('Skeletal output is stale; Generate Animation again from current review')
    expected = {c['character_id']: c for c in plan['characters']}
    records = report['characters']
    if len(records) != len(expected) or {r['character_id'] for r in records} != set(expected):
        raise ValueError('Skeletal output Character set differs from reviewed plan')
    paths = {}
    for record in records:
        if record['schedule'] != backend._core.resolve_pose_schedule(
                expected[record['character_id']]['samples'], record['source_frames']):
            raise ValueError('Skeletal schedule differs from reviewed Hold policy')
        if record['bvh'] is not None:
            bvh = _inside(root, record['bvh'])
            if _hash(bvh) != record['bvh_sha256']:
                raise ValueError('Skeletal BVH hash changed')
            paths[record['character_id']] = bvh
    prior = [o for o in bpy.context.scene.objects if o.get('vta_project_id') == plan['project_id']
             and o.get('vta_source_id') == plan['source_id'] and o.type == 'ARMATURE']
    created = []
    original_frame = bpy.context.scene.frame_current
    active = bpy.context.view_layer.objects.active
    active_name = active.name if active is not None else None
    active_character = active.get('vta_character_id') if active in prior else None
    selected_names = tuple(o.name for o in bpy.context.selected_objects)
    selected_characters = {o.get('vta_character_id') for o in prior if o.select_get()}
    try:
        for record in records:
            cid = record['character_id']
            if cid not in paths:
                continue
            # Fresh unique import: never delete an unrelated/user-named object.
            _import_bvh(str(paths[cid]), armature_name='VTA-import-' + uuid4().hex)
            obj = bpy.context.active_object
            if obj is None or obj.type != 'ARMATURE':
                raise ValueError('BVH import did not create a real armature')
            created.append(obj)
            retime_reviewed_action(obj, record['source_frames'], record['schedule'],
                                  location.blender_frame - location.source_frame)
            previous = next((o for o in prior if o.get('vta_character_id') == cid), None)
            if previous is not None:
                obj.matrix_world = previous.matrix_world.copy()
            obj['vta_character_id'], obj['vta_project_id'] = cid, plan['project_id']
            obj['vta_source_id'] = plan['source_id']
            obj['vta_output_file'] = str(Path(path).resolve())
            obj['vta_project_file_hash'] = _hash(location.companion_root / 'project_state.json')
            obj['vta_cache_file_hash'] = _hash(location.companion_root / 'analysis' / (cache.revision_id + '.json'))
            obj['vta_analysis_revision_id'] = cache.revision_id
            obj['vta_output_stale'] = False
            obj.name = 'VTA ' + record['display_name'] + ' [' + cid + ']'
        # Commit only after all new armatures validate. Old generated output is
        # retained on disk; only owned Blender projections are replaced.
        for obj in prior:
            bpy.data.objects.remove(obj, do_unlink=True)
        for obj in created:
            label = expected[obj['vta_character_id']]['display_name']
            obj.name = 'VTA ' + label + ' [' + obj['vta_character_id'] + ']'
    except Exception:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        raise
    finally:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for name in selected_names:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj.select_set(True)
        for obj in created:
            if obj.get('vta_character_id') in selected_characters:
                obj.select_set(True)
        active_obj = (next((o for o in created if o['vta_character_id'] == active_character), None)
                      if active_character is not None else bpy.data.objects.get(active_name or ''))
        if active_obj is not None:
            bpy.context.view_layer.objects.active = active_obj
        bpy.context.scene.frame_set(original_frame)
    return tuple(created), report


def mark_stale_outputs(companion_root):
    """Keep previous output, visibly mark stale after review or evidence changes."""
    root = Path(companion_root)
    outputs = [o for o in bpy.data.objects if o.get('vta_output_file')
               and Path(o['vta_output_file']).resolve().is_relative_to((root / 'reconstruction').resolve())]
    if not outputs:
        return False
    current = _cached_hash(root / 'project_state.json')
    for obj in outputs:
        cache = root / 'analysis' / (obj['vta_analysis_revision_id'] + '.json')
        obj['vta_output_stale'] = (obj['vta_project_file_hash'] != current
                                  or not cache.is_file() or obj['vta_cache_file_hash'] != _cached_hash(cache))
    return any(o['vta_output_stale'] for o in outputs)
