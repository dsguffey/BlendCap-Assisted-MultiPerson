"""Blender-only import of immutable reviewed reconstruction, never review authority."""
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import bpy
from mathutils import Matrix, Vector
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


def validate_camera_registration(record, plan, root, core):
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


def register_camera_root(obj, samples):
    """Undo exporter translation normalization; keep every reconstructed rotation.

    Blender's BVH importer already baked model (X,Y,Z) -> (X,-Z,Y)
    into bone rest/pose frames. Align the pelvis to the raw camera point in
    that same basis, expressing the correction in Root's rest-local axes.
    This cancels take-wide grounding/detrending, not pose or limb motion.
    """
    bpy.context.view_layer.update()
    if obj.matrix_world != Matrix.Identity(4):
        raise ValueError('Registered VTA output requires the canonical unedited object transform')
    root, hips = obj.pose.bones.get('Root'), obj.pose.bones.get('Hips')
    if root is None or hips is None or root.parent is not None:
        raise ValueError('Camera registration requires the existing Root/Hips skeleton')
    local_from_world = root.bone.matrix_local.to_3x3().inverted()
    for index, sample in enumerate(samples, 1):
        bpy.context.scene.frame_set(index)
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        current = evaluated.matrix_world @ evaluated.pose.bones['Hips'].head
        root.location += local_from_world @ (Vector(sample['pelvis_world_m']) - current)
        root.keyframe_insert('location', frame=index)


def stage_source_camera(plan, records, location, bounded, bounds):
    """Stage an owned calibrated camera; never alter the user's scene Camera."""
    owned = [o for o in bpy.context.scene.objects if o.type == 'CAMERA'
        and o.get('vta_project_id') == plan['project_id'] and o.get('vta_source_id') == plan['source_id']]
    if len(owned) > 1:
        raise ValueError('Multiple VTA source cameras own this source')
    previous = owned[0] if owned else None
    matrix = Matrix.Rotation(math.pi / 2, 4, 'X')
    if previous is not None and (previous.parent is not None or previous.constraints or previous.animation_data is not None
            or previous.matrix_world != matrix or previous.data.type != 'PERSP'
            or previous.data.sensor_fit != 'HORIZONTAL' or previous.data.sensor_width != 36
            or previous.data.shift_x != 0 or previous.data.shift_y != 0):
        raise ValueError('VTA source camera was edited; preserve it and use a separate scene')
    focal = {}
    for record in records:
        for sample in record.get('camera_registration', {}).get('samples', []):
            frame, value = sample['frame'], sample['focal_px']
            if frame in focal and not math.isclose(focal[frame], value, rel_tol=1e-6):
                raise ValueError('Characters disagree on shared source-camera calibration')
            focal[frame] = value
    if not focal:
        raise ValueError('No source-camera calibration is available')
    lens_limits = bpy.types.Camera.bl_rna.properties['lens']
    if any(not lens_limits.hard_min <= value * 36 / plan['width'] <= lens_limits.hard_max
           for value in focal.values()):
        raise ValueError('Source focal length cannot be represented by the Blender camera')
    data = bpy.data.cameras.new('VTA source calibration')
    camera = bpy.data.objects.new('VTA Source Camera', data)
    bpy.context.scene.collection.objects.link(camera)
    camera.matrix_world = matrix
    camera['vta_project_id'], camera['vta_source_id'] = plan['project_id'], plan['source_id']
    data.type, data.sensor_fit, data.sensor_width = 'PERSP', 'HORIZONTAL', 36
    data.clip_start, data.clip_end = .01, 10000
    offset = location.blender_frame - location.source_frame
    for frame in range(plan['source_range'][0], plan['source_range'][1] + 1):
        earlier = [f for f in focal if f <= frame]
        value = focal[max(earlier) if earlier else min(focal)]
        if previous is not None and bounded and frame not in focal:
            curves = list(_iter_action_fcurves(previous.data.animation_data.action))
            data.lens = next(fc for fc in curves if fc.data_path == 'lens').evaluate(frame + offset)
        else:
            data.lens = value * 36 / plan['width']
        data.keyframe_insert('lens', frame=frame + offset)
    for fc in _iter_action_fcurves(data.animation_data.action):
        for point in fc.keyframe_points:
            point.interpolation = 'CONSTANT'
    if bounded and previous is not None:
        action = merge_reviewed_range(previous.data, data.animation_data.action, *bounds)
        data.animation_data.action = action
        data.animation_data.action_slot = action.slots[0]
    data.show_background_images = True
    background = data.background_images.new()
    background.source = 'IMAGE'
    background.image = bpy.data.images.load(str(location.source_path), check_existing=True)
    background.image_user.frame_start = offset
    background.image_user.frame_duration = plan['source_range'][1] + 1 if not bounded else max(
        plan['source_range'][1] + 1, background.image.frame_duration)
    background.image_user.use_auto_refresh = True
    background.alpha, background.display_depth = 1, 'BACK'
    return previous, camera


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
        if report.get('version') == 3:
            register_camera_root(original, record['camera_registration']['samples'])
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
    if report.get('schema') != 'vta-reviewed-skeletal-output' or report.get('version') not in (1, 2, 3):
        raise ValueError('Unsupported skeletal output schema')
    project, cache, _revision = backend._load_context(location)
    bounded = report['version'] == 2 or (report['version'] == 3 and report['plan']['version'] == 2)
    options = {'frame_range': report['plan']['source_range']} if bounded else {}
    plan = backend._core.build_skeletal_plan(project, cache, **options)
    if report['plan'] != plan:
        raise ValueError('Skeletal output is stale; Generate Animation again from current review')
    expected = {c['character_id']: c for c in plan['characters']}
    records = report['characters']
    if len(records) != len(expected) or {r['character_id'] for r in records} != set(expected):
        raise ValueError('Skeletal output Character set differs from reviewed plan')
    paths = {}
    registration = {}
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
        if report['version'] == 3:
            registration[record['character_id']] = validate_camera_registration(record, plan, root, backend._core)
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
    before_cameras = set(bpy.data.cameras)
    before_images = set(bpy.data.images)
    scene = bpy.context.scene
    camera_before = scene.camera
    render_before = (scene.render.resolution_x, scene.render.resolution_y,
                     scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)
    camera_stage, camera_previous, camera_data_before = None, None, None
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
            if report['version'] == 3:
                register_camera_root(obj, registration[cid])
            retime_reviewed_action(obj, record['source_frames'], record['schedule'],
                                  offset)
            if previous is not None:
                obj.matrix_world = previous.matrix_world.copy()
                if report['version'] == 3 and previous.matrix_world != Matrix.Identity(4):
                    raise ValueError('Prior VTA object transform was edited; cannot register a bounded update')
                if report['version'] == 3 and (previous.parent is not None or previous.constraints
                        or any(b.constraints for b in previous.pose.bones)):
                    raise ValueError('Prior VTA rig has transform constraints; cannot register output safely')
            obj['vta_character_id'], obj['vta_project_id'] = cid, plan['project_id']
            obj['vta_source_id'] = plan['source_id']
            obj['vta_output_file'] = str(Path(path).resolve())
            obj['vta_project_file_hash'] = _hash(location.companion_root / 'project_state.json')
            obj['vta_cache_file_hash'] = _hash(location.companion_root / 'analysis' / (cache.revision_id + '.json'))
            obj['vta_analysis_revision_id'] = cache.revision_id
            obj['vta_output_stale'] = False
            if report['version'] == 3:
                obj['vta_camera_registration_version'] = 1
                obj.show_in_front = True
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
        if report['version'] == 3:
            camera_previous, camera_stage = stage_source_camera(plan, records, location, bounded, bounds)
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
        if camera_stage is not None:
            if camera_previous is not None:
                camera_data_before = camera_previous.data
                camera_previous.data = camera_stage.data
                scene.camera = camera_previous
                bpy.data.objects.remove(camera_stage, do_unlink=True)
                camera_stage = None
            else:
                scene.camera = camera_stage
            scene.render.resolution_x, scene.render.resolution_y = plan['width'], plan['height']
            scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
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
        scene.camera = camera_before
        if camera_data_before is not None:
            camera_previous.data = camera_data_before
        (scene.render.resolution_x, scene.render.resolution_y,
         scene.render.pixel_aspect_x, scene.render.pixel_aspect_y) = render_before
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
        for armature in set(bpy.data.armatures) - before_armatures:
            if armature.users == 0:
                bpy.data.armatures.remove(armature)
        for camera in set(bpy.data.cameras) - before_cameras:
            if camera.users == 0:
                bpy.data.cameras.remove(camera)
        # Camera data owns its Action until the data itself is removed.
        for action in set(bpy.data.actions) - before_actions:
            if action.users == 0:
                bpy.data.actions.remove(action)
        for image in set(bpy.data.images) - before_images:
            if image.users == 0:
                bpy.data.images.remove(image)
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
