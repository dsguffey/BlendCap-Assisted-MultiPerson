"""Blender-only import of immutable reviewed reconstruction, never review authority."""
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import bpy
from mathutils import Matrix, Vector, Euler, Quaternion
from .helpers import _import_bvh
from .retarget.fcurves import _iter_action_fcurves
from .vta_scene_root import find_scene_root, ensure_scene_root, supported_parent, parent_to_scene_root
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


def retime_reviewed_action(obj, source_frames, schedule, offset, *, repair_math=None):
    """Copy final imported pose channels, including root, to exact source time.

    Freeze copies all registered raw channels. Interpolate uses quaternion
    rotations and linear root translation, not Euler component blending. Missing spans
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
    final = {}
    # Capture every raw value before mutation; neither prior repair nor object
    # placement is an input to this derived output-stage operation.
    raw = {(fc.data_path, fc.array_index): values for fc, values in copied}
    rotation_groups = {}
    for bone in obj.pose.bones:
        suffix = 'rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else 'rotation_euler'
        path = bone.path_from_id() + '.' + suffix
        if any(fc.data_path == path for fc in curves):
            rotation_groups[path] = (bone.rotation_mode, 4 if suffix == 'rotation_quaternion' else 3)
    for sample in schedule:
        if sample['state'] != 'interpolated':
            continue
        if repair_math is None:
            raise ValueError('Interpolate requires the private repair math contract')
        left, right, weight = sample['left_pose_frame'], sample['right_pose_frame'], sample['weight']
        for fc, values in copied:
            path, axis = fc.data_path, fc.array_index
            if path in rotation_groups:
                continue
            if not path.endswith(('.location', '.scale')):
                raise ValueError('Unsupported generated repair channel: ' + path)
            final[(sample['frame'], path, axis)] = values[left] + weight * (values[right] - values[left])
        for path, (mode, size) in rotation_groups.items():
            a, b = (tuple(raw[(path, axis)][f] for axis in range(size)) for f in (left, right))
            ea = Euler(a, mode) if size == 3 else None
            qa, qb = (ea.to_quaternion(), Euler(b, mode).to_quaternion()) if size == 3 else (a, b)
            q = Quaternion(repair_math.quaternion_slerp(tuple(qa), tuple(qb), weight))
            values = tuple(q.to_euler(mode, ea)) if size == 3 else tuple(q)
            for axis, value in enumerate(values):
                final[(sample['frame'], path, axis)] = value
    for fc, values in copied:
        samples = [(s['frame'] + offset, final[(s['frame'], fc.data_path, fc.array_index)]
                    if s['state'] == 'interpolated' else values[s['pose_source_frame']])
                   for s in schedule if s['state'] != 'unavailable']
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


_OBJECT_TRANSFORMS = {'location': 3, 'rotation_euler': 3,
                      'rotation_quaternion': 4, 'rotation_axis_angle': 4, 'scale': 3}


def _user_transform_curve(fc):
    """Exact Object Mode paths only; never pose, delta or custom channels."""
    return fc.data_path in _OBJECT_TRANSFORMS and 0 <= fc.array_index < _OBJECT_TRANSFORMS[fc.data_path]


def _regenerable_curves(previous, segment, preserve_object_transforms):
    data = previous.animation_data
    if data is None or data.action is None or data.nla_tracks or data.drivers:
        raise ValueError('Range update requires a generated Action without NLA or drivers')
    old = data.action
    curves = list(_iter_action_fcurves(old))
    if (len(old.slots) != 1 or len(old.layers) != 1 or len(old.layers[0].strips) != 1
            or len({(fc.data_path, fc.array_index) for fc in curves}) != len(curves)
            or any(fc.modifiers or fc.sampled_points or
                (not (preserve_object_transforms and _user_transform_curve(fc))
                 and any(p.interpolation != 'CONSTANT' for p in fc.keyframe_points)) for fc in curves)):
        raise ValueError('Range update cannot safely preserve edited/interpolated animation; use a separate generated rig')
    if preserve_object_transforms:
        if (previous.type != 'ARMATURE' or not supported_parent(previous) or previous.constraints
                or any(b.constraints for b in previous.pose.bones)):
            raise ValueError('Prior VTA rig has parents or transform constraints; cannot register output safely')
        # Unknown/custom/delta channels do not become implicitly user-owned.
        bone_paths = {b.path_from_id() + '.' + suffix: size
                      for b in previous.pose.bones for suffix, size in _OBJECT_TRANSFORMS.items()}
        if any(not _user_transform_curve(fc) and not (
                fc.data_path in ('hide_viewport', 'hide_render') and fc.array_index == 0
                or fc.data_path in bone_paths and 0 <= fc.array_index < bone_paths[fc.data_path])
               for fc in curves):
            raise ValueError('Range update skeletal channels differ from previous output')
    generated = [fc for fc in curves if not (preserve_object_transforms and _user_transform_curve(fc))]
    incoming = {(fc.data_path, fc.array_index): fc for fc in _iter_action_fcurves(segment)} if segment else {}
    if segment and {(fc.data_path, fc.array_index) for fc in generated} != set(incoming):
        raise ValueError('Range update skeletal channels differ from previous output')
    return old, incoming


def merge_reviewed_range(previous, segment, start, end, source_bounds=None,
                         *, preserve_object_transforms=False, full=False):
    """Stage a copied Action. Integer-frame boundaries are guarded, not blended.

    Owned generated channels are constant, single-slot, modifier-free channels.
    Supported Object Mode curves are copied verbatim, never retimed or trimmed.
    Reject edited/interpolated/NLA output rather than change its outside motion.
    The caller commits the returned Action only after all Characters stage.
    """
    old, incoming = _regenerable_curves(previous, segment, preserve_object_transforms)
    result = old.copy()
    try:
        for fc in _iter_action_fcurves(result):
            if preserve_object_transforms and _user_transform_curve(fc):
                continue
            before, after = fc.evaluate(start - 1), fc.evaluate(end + 1)
            outside = {p.co.x for p in fc.keyframe_points if p.co.x < start or p.co.x > end}
            # Removing reallocates Blender's RNA array: never retain point
            # wrappers across removals, even when iterating a Python tuple.
            for index in range(len(fc.keyframe_points) - 1, -1, -1):
                point = fc.keyframe_points[index]
                if full or start <= point.co.x <= end:
                    fc.keyframe_points.remove(point, fast=True)
            new = incoming.get((fc.data_path, fc.array_index))
            if new:
                for point in new.keyframe_points:
                    if full or start <= point.co.x <= end:
                        fc.keyframe_points.insert(point.co.x, point.co.y, options={'FAST'}).interpolation = 'CONSTANT'
            elif fc.data_path in ('hide_viewport', 'hide_render'):
                fc.keyframe_points.insert(start, 1, options={'FAST'}).interpolation = 'CONSTANT'
            for frame, value in (() if full else ((start - 1, before), (end + 1, after))):
                if source_bounds is not None and not source_bounds[0] <= frame <= source_bounds[1]:
                    continue  # no legacy-pose preservation guard outside the video
                if frame not in outside:
                    fc.keyframe_points.insert(frame, value, options={'FAST'}).interpolation = 'CONSTANT'
            fc.update()
        return result
    except Exception:
        bpy.data.actions.remove(result)
        raise


def limit_source_visibility(action, first, last, *, preserve_object_transforms=False):
    """No pose samples outside footage; visibility guards are not body poses."""
    for fc in _iter_action_fcurves(action):
        if preserve_object_transforms and _user_transform_curve(fc):
            continue
        for index in range(len(fc.keyframe_points) - 1, -1, -1):
            if not first <= fc.keyframe_points[index].co.x <= last:
                fc.keyframe_points.remove(fc.keyframe_points[index], fast=True)
        if fc.data_path in ('hide_viewport', 'hide_render'):
            for frame in (first - 1, last + 1):
                fc.keyframe_points.insert(frame, 1, options={'FAST'}).interpolation = 'CONSTANT'
        fc.update()


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


def scene_translation(reference):
    return Matrix.Translation(reference['reference']['translation_m']) if reference else Matrix.Identity(4)


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


def register_camera_root(obj, samples):
    """Undo exporter translation normalization; keep every reconstructed rotation.

    Blender's BVH importer already baked model (X,Y,Z) -> (X,-Z,Y)
    into bone rest/pose frames. Align the pelvis to the raw camera point in
    that same basis, expressing the correction in Root's rest-local axes.
    This cancels take-wide grounding/detrending, not pose or limb motion.
    """
    bpy.context.view_layer.update()
    if obj.parent is not None or obj.matrix_world != Matrix.Identity(4):
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


def stage_source_camera(plan, records, location, bounded, bounds, reference=None):
    """Stage an owned calibrated camera; never alter the user's scene Camera."""
    owned = [o for o in bpy.context.scene.objects if o.type == 'CAMERA'
        and o.get('vta_project_id') == plan['project_id'] and o.get('vta_source_id') == plan['source_id']]
    if len(owned) > 1:
        raise ValueError('Multiple VTA source cameras own this source')
    previous = owned[0] if owned else None
    rotation = Matrix.Rotation(math.pi / 2, 4, 'X')
    saved = json.loads(previous.data['vta_scene_reference']) if previous is not None and previous.data.get('vta_scene_reference') else None
    if saved is not None and reference != saved:
        raise ValueError('Established VTA scene reference must not be replaced by a range update')
    previous_matrix = scene_translation(saved) @ rotation
    matrix = scene_translation(reference) @ rotation
    if previous is not None and (not supported_parent(previous) or previous.constraints or previous.animation_data is not None
            or previous.matrix_world != previous_matrix or previous.data.type != 'PERSP'
            or previous.data.sensor_fit != 'HORIZONTAL' or previous.data.sensor_width != 36
            or previous.data.shift_x != 0 or previous.data.shift_y != 0):
        raise ValueError('VTA source camera was edited; preserve it and use a separate scene')
    focal = {}
    for record in records:
        for sample in (record.get('camera_registration') or {}).get('samples', []):
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
    if reference is not None:
        data['vta_scene_reference'] = json.dumps(reference, sort_keys=True)
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


def import_reviewed_output(path, location, backend, *, configure_views=True):
    root = Path(path).resolve().parent
    expected_root = (location.companion_root / 'reconstruction').resolve()
    if not root.is_relative_to(expected_root):
        raise ValueError('Skeletal output is outside this project companion')
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if report.get('schema') != 'vta-reviewed-skeletal-output' or report.get('version') not in (1, 2, 3, 4):
        raise ValueError('Unsupported skeletal output schema; regenerate with ProjectState 0.8')
    project, cache, _revision = backend._load_context(location)
    bounded = (report['plan'].get('generation_scope') == 'range'
               or report['version'] == 2 or report['plan'].get('version') == 2)
    options = {'frame_range': report['plan']['source_range']} if bounded else {}
    plan = backend._core.build_skeletal_plan(project, cache, **options)
    if report['plan'] != plan:
        raise ValueError('Skeletal output is stale; Generate Animation again from current review')
    reconstruction = report.get('reconstruction')
    fingerprint = backend._core.reconstruction_fingerprint(reconstruction, plan.get('source_video_sha256'))
    expected = {c['character_id']: c for c in plan['characters']}
    records = report['characters']
    if len(records) != len(expected) or {r['character_id'] for r in records} != set(expected):
        raise ValueError('Skeletal output Character set differs from reviewed plan')
    paths = {}
    registration = {}
    for record in records:
        character = expected[record['character_id']]
        if record['schedule'] != backend._core.resolve_pose_schedule(
                character['samples'], record['source_frames'],
                repair_context=character.get('repair_context', ())):
            raise ValueError('Skeletal schedule differs from authoritative repair policy')
        if record['bvh'] is not None:
            bvh = _inside(root, record['bvh'])
            if _hash(bvh) != record['bvh_sha256']:
                raise ValueError('Skeletal BVH hash changed')
            paths[record['character_id']] = bvh
        if report['version'] == 4:
            registration[record['character_id']] = validate_camera_registration(
                record, plan, root, backend._core, reconstruction)
    if not paths or not any(s['state'] != 'unavailable' for r in records for s in r['schedule']):
        raise ValueError('Animation has no successful trustworthy or repaired pose; prior output retained')
    prior = [o for o in bpy.context.scene.objects if o.get('vta_project_id') == plan['project_id']
             and o.get('vta_source_id') == plan['source_id'] and o.type == 'ARMATURE']
    owners = [*prior, *(o.data for o in bpy.context.scene.objects if o.type == 'CAMERA'
        and o.get('vta_project_id') == plan['project_id'] and o.get('vta_source_id') == plan['source_id'])]
    previous_fingerprints = [o.get('vta_reconstruction_fingerprint', 'legacy-per-frame-intrinsics') for o in owners]
    backend._core.require_compatible_reconstruction(fingerprint, previous_fingerprints, bounded=bounded)
    if not bounded and any(value != fingerprint for value in previous_fingerprints):
        if any(o.get('vta_character_id') not in paths for o in prior):
            raise ValueError('Calibration migration requires whole-source replacement for every existing Character; previous animation retained')
    reference = None
    source_bounds = None
    if report['version'] == 4:
        cameras = [o for o in bpy.context.scene.objects if o.type == 'CAMERA'
            and o.get('vta_project_id') == plan['project_id'] and o.get('vta_source_id') == plan['source_id']]
        if len(cameras) > 1:
            raise ValueError('Multiple VTA source cameras own this source')
        reference = resolve_scene_reference(plan, records, cameras[0] if cameras else None, backend._core, path)
        if prior and (not cameras or not cameras[0].data.get('vta_scene_reference')):
            raise ValueError('Existing VTA rigs lack a fixed registration base; preserve them in a separate scene before calibrated regeneration')
        offset = location.blender_frame - location.source_frame
        source_bounds = [cache.source.start_frame + offset, cache.source.end_frame + offset]
    created = []
    staged = []
    committed = []
    hierarchy_before = []
    view_state = []
    before_objects = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    before_armatures = set(bpy.data.armatures)
    before_cameras = set(bpy.data.cameras)
    before_images = set(bpy.data.images)
    scene = bpy.context.scene
    camera_before = scene.camera
    prior_display = [(o, o.show_in_front) for o in prior]
    camera_matrix_before = camera_before.matrix_world.copy() if camera_before else None
    owned_camera_matrices = [(o, o.matrix_world.copy()) for o in scene.objects if o.type == 'CAMERA'
        and o.get('vta_project_id') == plan['project_id'] and o.get('vta_source_id') == plan['source_id']]
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
        if report['version'] == 4:
            from .vta_workspace import source_camera_view_state, configure_source_camera_views
            view_state = source_camera_view_state()
            find_scene_root(scene, plan['project_id'], plan['source_id'])
            # Includes owned Characters with no new successful pose this run.
            for obj in prior:
                _regenerable_curves(obj, None, True)
        for record in records:
            cid = record['character_id']
            owned = [o for o in prior if o.get('vta_character_id') == cid]
            if len(owned) > 1:
                raise ValueError('Multiple generated rigs own this Character; resolve duplicate output first')
            previous = owned[0] if owned else None
            if cid not in paths:
                if bounded and previous is not None:
                    staged.append((previous, merge_reviewed_range(previous, None, *bounds,
                        source_bounds=source_bounds, preserve_object_transforms=True), None))
                continue
            # Fresh unique import: never delete an unrelated/user-named object.
            before_import = set(bpy.data.objects)
            _import_bvh(str(paths[cid]), armature_name='VTA-import-' + uuid4().hex)
            obj = bpy.context.active_object
            if obj is None or obj in before_import or obj.type != 'ARMATURE':
                raise ValueError('BVH import did not create a real armature')
            created.append(obj)
            if report['version'] == 4:
                register_camera_root(obj, registration[cid])
            retime_reviewed_action(obj, record['source_frames'], record['schedule'],
                                  offset, repair_math=backend._core)
            obj['vta_character_id'], obj['vta_project_id'] = cid, plan['project_id']
            obj['vta_source_id'] = plan['source_id']
            obj['vta_output_file'] = str(Path(path).resolve())
            obj['vta_project_file_hash'] = _hash(location.companion_root / 'project_state.json')
            obj['vta_cache_file_hash'] = _hash(location.companion_root / 'analysis' / (cache.revision_id + '.json'))
            obj['vta_analysis_revision_id'] = cache.revision_id
            obj['vta_output_stale'] = False
            obj['vta_reconstruction_fingerprint'] = fingerprint
            if report['version'] == 4:
                obj['vta_camera_registration_version'] = 1
                obj.show_in_front = True
            obj.name = 'VTA ' + record['display_name'] + ' [' + cid + ']'
            if previous is not None:
                if not _same_skeleton(previous, obj):
                    raise ValueError('Range update skeleton differs; previous animation retained')
                merge_bounds = bounds if bounded else (0, 0)  # full replaces all generated keys
                staged.append((previous, merge_reviewed_range(previous, obj.animation_data.action,
                    *merge_bounds, source_bounds=source_bounds, preserve_object_transforms=True,
                    full=not bounded), obj))
            elif bounded:
                for frame in (bounds[0] - 1, bounds[1] + 1):
                    obj.hide_viewport = obj.hide_render = True
                    obj.keyframe_insert('hide_viewport', frame=frame)
                for fc in _iter_action_fcurves(obj.animation_data.action):
                    for point in fc.keyframe_points:
                        point.interpolation = 'CONSTANT'
        if report['version'] == 4:
            for obj in created:
                obj.matrix_world = scene_translation(reference)
                limit_source_visibility(obj.animation_data.action, *source_bounds)
            for previous, action, fresh in staged:
                limit_source_visibility(action, *source_bounds, preserve_object_transforms=True)
            camera_previous, camera_stage = stage_source_camera(plan, records, location, bounded, bounds, reference)
            camera_stage.data['vta_reconstruction_fingerprint'] = fingerprint
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
            for obj in prior:
                obj.show_in_front = True
            if camera_previous is not None:
                camera_data_before = camera_previous.data
                camera_previous.data = camera_stage.data
                camera_previous.matrix_world = camera_stage.matrix_world.copy()
                scene.camera = camera_previous
                bpy.data.objects.remove(camera_stage, do_unlink=True)
                camera_stage = None
            else:
                scene.camera = camera_stage
            scene.render.resolution_x, scene.render.resolution_y = plan['width'], plan['height']
            scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
        if report['version'] == 4:
            # Fresh temporary rigs have completed registration unparented.
            # Adopt only surviving owned objects; never the replacement temps.
            scene_root = ensure_scene_root(scene, plan['project_id'], plan['source_id'])
            replaced = {fresh for _previous, _action, fresh in staged if fresh is not None}
            targets = [*prior, *(obj for obj in created if obj not in replaced), scene.camera]
            for obj in targets:
                hierarchy_before.append((obj, obj.parent, obj.parent_type, obj.parent_bone,
                                         obj.matrix_parent_inverse.copy()))
                parent_to_scene_root(obj, scene_root)
            # Presentation is part of the same transaction, not a fallible
            # post-commit step that could leave a partially adopted scene.
            if configure_views:
                configure_source_camera_views(scene)
        for previous, action, fresh in staged:
            if fresh is not None:
                created.remove(fresh)
                _remove_staged(fresh)
        created.extend(previous for previous, _action, _fresh in staged)
        for obj in created:
            label = expected[obj['vta_character_id']]['display_name']
            obj.name = 'VTA ' + label + ' [' + obj['vta_character_id'] + ']'
    except Exception:
        for owner, field, value in view_state:
            setattr(owner, field, value)
        # LRS/Actions were never rewritten by parenting. Restore hierarchy before
        # camera world rollback; new roots are removed by the datablock cleanup.
        for obj, parent, parent_type, parent_bone, inverse in reversed(hierarchy_before):
            obj.parent, obj.parent_type, obj.parent_bone = parent, parent_type, parent_bone
            obj.matrix_parent_inverse = inverse
        scene.camera = camera_before
        for obj, in_front in prior_display:
            obj.show_in_front = in_front
        if camera_before is not None:
            camera_before.matrix_world = camera_matrix_before
        for obj, matrix in owned_camera_matrices:
            obj.matrix_world = matrix
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
