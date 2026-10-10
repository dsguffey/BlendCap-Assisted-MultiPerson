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


def merge_reviewed_range(previous, segment, start, end):
    """Stage a copied Action. Integer-frame boundaries are guarded, not blended.

    Owned generated Actions are constant, single-slot, modifier-free channels.
    Reject edited/interpolated/NLA output rather than change its outside motion.
    The caller commits the returned Action only after all Characters stage.
    """
    data = previous.animation_data
    if data is None or data.action is None or data.nla_tracks or data.drivers:
        raise ValueError('Range update requires a generated Action without NLA or drivers')
    old = data.action
    curves = list(_iter_action_fcurves(old))
    if len(old.slots) != 1 or any(fc.modifiers or any(p.interpolation != 'CONSTANT'
            for p in fc.keyframe_points) for fc in curves):
        raise ValueError('Range update cannot safely preserve edited/interpolated animation; use a separate generated rig')
    incoming = {(fc.data_path, fc.array_index): fc for fc in _iter_action_fcurves(segment)} if segment else {}
    keys = {(fc.data_path, fc.array_index) for fc in curves}
    if segment and keys != set(incoming):
        raise ValueError('Range update skeletal channels differ from previous output')
    result = old.copy()
    try:
        for fc in _iter_action_fcurves(result):
            before, after = fc.evaluate(start - 1), fc.evaluate(end + 1)
            outside = {p.co.x for p in fc.keyframe_points if p.co.x < start or p.co.x > end}
            # Removing reallocates Blender's RNA array: never retain point
            # wrappers across removals, even when iterating a Python tuple.
            for index in range(len(fc.keyframe_points) - 1, -1, -1):
                point = fc.keyframe_points[index]
                if start <= point.co.x <= end:
                    fc.keyframe_points.remove(point, fast=True)
            new = incoming.get((fc.data_path, fc.array_index))
            if new:
                for point in new.keyframe_points:
                    if start <= point.co.x <= end:
                        fc.keyframe_points.insert(point.co.x, point.co.y, options={'FAST'}).interpolation = 'CONSTANT'
            elif fc.data_path in ('hide_viewport', 'hide_render'):
                fc.keyframe_points.insert(start, 1, options={'FAST'}).interpolation = 'CONSTANT'
            for frame, value in ((start - 1, before), (end + 1, after)):
                if frame not in outside:
                    fc.keyframe_points.insert(frame, value, options={'FAST'}).interpolation = 'CONSTANT'
            fc.update()
        return result
    except Exception:
        bpy.data.actions.remove(result)
        raise


def _same_skeleton(previous, fresh):
    return (set(previous.data.bones.keys()) == set(fresh.data.bones.keys())
        and all(previous.data.bones[b.name].matrix_local == b.matrix_local
                and (previous.data.bones[b.name].parent.name if previous.data.bones[b.name].parent else None)
                    == (b.parent.name if b.parent else None)
                and previous.pose.bones[b.name].rotation_mode == fresh.pose.bones[b.name].rotation_mode
                for b in fresh.data.bones))


def _remove_staged(obj):
    armature = obj.data
    action = obj.animation_data.action if obj.animation_data else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if armature.users == 0:
        bpy.data.armatures.remove(armature)
    if action is not None and action.users == 0 and not action.use_fake_user:
        bpy.data.actions.remove(action)


def _copy_final_hold_seed(obj, seed, schedule, root, offset):
    """Reuse final original BVH channels, not a renormalized NPZ re-export.

    Body export normalization can depend on the sampled span. A seed-only
    export is not guaranteed to reproduce an earlier whole-span final root.
    """
    path = (Path(root) / seed['output']).resolve()
    report = json.loads(path.read_text(encoding='utf-8'))
    record = next(c for c in report['characters'] if c['character_id'] == obj['vta_character_id'])
    bvh = _inside(path.parent, record['bvh'])
    if _hash(bvh) != record['bvh_sha256']:
        raise ValueError('Hold seed BVH hash changed')
    before = set(bpy.data.objects)
    _import_bvh(str(bvh), armature_name='VTA-seed-' + uuid4().hex)
    original = bpy.context.active_object
    try:
        if (original is None or original in before or original.type != 'ARMATURE'
                or not _same_skeleton(obj, original)):
            raise ValueError('Hold seed skeleton differs from range output')
        index = record['source_frames'].index(seed['frame']) + 1
        channels = {(fc.data_path, fc.array_index): fc.evaluate(index)
                    for fc in _iter_action_fcurves(original.animation_data.action)}
        held = {s['frame'] + offset for s in schedule if s['state'] == 'held'
                and s['pose_source_frame'] == seed['frame']}
        for fc in _iter_action_fcurves(obj.animation_data.action):
            key = fc.data_path, fc.array_index
            if fc.data_path in ('hide_viewport', 'hide_render'):
                continue
            if key not in channels:
                raise ValueError('Hold seed skeletal channels differ')
            for point in fc.keyframe_points:
                if point.co.x in held:
                    point.co.y = channels[key]
            fc.update()
    finally:
        if original is not None and original not in before:
            _remove_staged(original)
        bpy.context.view_layer.objects.active = obj


def import_reviewed_output(path, location, backend):
    root = Path(path).resolve().parent
    expected_root = (location.companion_root / 'reconstruction').resolve()
    if not root.is_relative_to(expected_root):
        raise ValueError('Skeletal output is outside this project companion')
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if report.get('schema') != 'vta-reviewed-skeletal-output' or report.get('version') not in (1, 2):
        raise ValueError('Unsupported skeletal output schema')
    project, cache, _revision = backend._load_context(location)
    bounded = report['version'] == 2
    options = {'frame_range': report['plan']['source_range']} if bounded else {}
    plan = backend._core.build_skeletal_plan(project, cache, **options)
    if report['plan'] != plan:
        raise ValueError('Skeletal output is stale; Generate Animation again from current review')
    expected = {c['character_id']: c for c in plan['characters']}
    records = report['characters']
    if len(records) != len(expected) or {r['character_id'] for r in records} != set(expected):
        raise ValueError('Skeletal output Character set differs from reviewed plan')
    paths = {}
    for record in records:
        character = expected[record['character_id']]
        seed = record.get('hold_seed') if bounded else None
        if seed:
            from blendcap_vta_reconstruct_worker import validate_hold_seed
            validate_hold_seed(plan, character, seed, location.companion_root)
        inferred = [f for f in record['source_frames'] if seed is None or f != seed['frame']]
        if record['schedule'] != backend._core.resolve_pose_schedule(
                character['samples'], inferred, **({'initial_pose_frame': seed['frame']} if seed else {})):
            raise ValueError('Skeletal schedule differs from reviewed Hold policy')
        if record['bvh'] is not None:
            bvh = _inside(root, record['bvh'])
            if _hash(bvh) != record['bvh_sha256']:
                raise ValueError('Skeletal BVH hash changed')
            paths[record['character_id']] = bvh
    if not paths or not any(s['state'] != 'unavailable' for r in records for s in r['schedule']):
        raise ValueError('Animation has no successful trustworthy pose or reusable Hold; prior output retained')
    prior = [o for o in bpy.context.scene.objects if o.get('vta_project_id') == plan['project_id']
             and o.get('vta_source_id') == plan['source_id'] and o.type == 'ARMATURE']
    created = []
    staged = []
    committed = []
    before_objects = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    before_armatures = set(bpy.data.armatures)
    offset = location.blender_frame - location.source_frame
    bounds = [f + offset for f in plan['source_range']] if bounded else None
    original_frame = bpy.context.scene.frame_current
    original_limits = bpy.context.scene.frame_start, bpy.context.scene.frame_end
    active = bpy.context.view_layer.objects.active
    active_name = active.name if active is not None else None
    active_character = active.get('vta_character_id') if active in prior else None
    selected_names = tuple(o.name for o in bpy.context.selected_objects)
    selected_characters = {o.get('vta_character_id') for o in prior if o.select_get()}
    try:
        for record in records:
            cid = record['character_id']
            owned = [o for o in prior if o.get('vta_character_id') == cid]
            if len(owned) > 1:
                raise ValueError('Multiple generated rigs own this Character; resolve duplicate output first')
            previous = owned[0] if owned else None
            if cid not in paths:
                if bounded and previous is not None:
                    staged.append((previous, merge_reviewed_range(previous, None, *bounds), None))
                continue
            # Fresh unique import: never delete an unrelated/user-named object.
            before_import = set(bpy.data.objects)
            _import_bvh(str(paths[cid]), armature_name='VTA-import-' + uuid4().hex)
            obj = bpy.context.active_object
            if obj is None or obj in before_import or obj.type != 'ARMATURE':
                raise ValueError('BVH import did not create a real armature')
            created.append(obj)
            retime_reviewed_action(obj, record['source_frames'], record['schedule'],
                                  offset)
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
            if record.get('hold_seed'):
                _copy_final_hold_seed(obj, record['hold_seed'], record['schedule'], location.companion_root, offset)
            if bounded:
                if previous is not None:
                    if not _same_skeleton(previous, obj):
                        raise ValueError('Range update skeleton differs; previous animation retained')
                    staged.append((previous, merge_reviewed_range(previous, obj.animation_data.action, *bounds), obj))
                else:
                    for frame in (bounds[0] - 1, bounds[1] + 1):
                        obj.hide_viewport = obj.hide_render = True
                        obj.keyframe_insert('hide_viewport', frame=frame)
                        obj.keyframe_insert('hide_render', frame=frame)
                    for fc in _iter_action_fcurves(obj.animation_data.action):
                        for point in fc.keyframe_points:
                            point.interpolation = 'CONSTANT'
        # Commit only after all new armatures validate. Old generated output is
        # retained on disk; only owned Blender projections are replaced.
        for previous, action, fresh in staged:
            snapshot = {key: previous[key] for key in previous.keys() if key.startswith('vta_')}
            committed.append((previous, previous.animation_data.action, snapshot))
            previous.animation_data.action = action
            previous.animation_data.action_slot = action.slots[0]
            if fresh is not None:
                for key in fresh.keys():
                    if key.startswith('vta_'):
                        previous[key] = fresh[key]
            history = json.loads(previous.get('vta_output_history', '[]'))
            history.append({'output': str(Path(path).resolve()), 'blender_range': bounds})
            previous['vta_output_history'] = json.dumps(history)
        if not bounded:
            for obj in prior:
                if obj.get('vta_character_id') in paths:
                    bpy.data.objects.remove(obj, do_unlink=True)
        else:
            for previous, action, fresh in staged:
                if fresh is not None:
                    created.remove(fresh)
                    _remove_staged(fresh)
            created.extend(previous for previous, _action, _fresh in staged)
        for obj in created:
            label = expected[obj['vta_character_id']]['display_name']
            obj.name = 'VTA ' + label + ' [' + obj['vta_character_id'] + ']'
    except Exception:
        for obj, action, snapshot in reversed(committed):
            obj.animation_data.action = action
            obj.animation_data.action_slot = action.slots[0]
            for key in tuple(obj.keys()):
                if key.startswith('vta_'):
                    del obj[key]
            for key, value in snapshot.items():
                obj[key] = value
        # Includes objects created by an importer that raised before returning.
        for obj in set(bpy.data.objects) - before_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for action in set(bpy.data.actions) - before_actions:
            if action.users == 0:
                bpy.data.actions.remove(action)
        for armature in set(bpy.data.armatures) - before_armatures:
            if armature.users == 0:
                bpy.data.armatures.remove(armature)
        created.clear()
        raise
    finally:
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = original_limits
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
    """Safe operator/timer ONLY. Never call from a draw/refresh callback."""
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
