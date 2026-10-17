"""Host-owned generation recovery; never modifies review or reconstruction policy."""
import json
from pathlib import Path

import bpy

from .retarget.fcurves import _iter_action_fcurves

LEGACY = 'legacy-per-frame-intrinsics'


def worker_failure(code,lines):
    """An abruptly stopped worker's final warning is not its exception."""
    errors=[line for line in lines if line.startswith(('RuntimeError:','ValueError:','OSError:',
        'FileNotFoundError:','MemoryError:','torch.OutOfMemoryError:','Fatal Python error:','Windows fatal exception:'))]
    if errors:return errors[-1]
    unsigned=code & 0xffffffff
    detail=f'Generation worker exited unexpectedly (exit code {code}, 0x{unsigned:08X}).'
    if unsigned in (0xC0000017,0xC000009A,0xC000012D):detail+=' System memory/commit limit was exhausted.'
    elif unsigned==0xC0000005:detail+=' Native access violation; see the crash log.'
    return detail+' Last diagnostic (not necessarily the cause): '+next((s for s in reversed(lines) if s.strip()),'No diagnostic output.')


def source_bounds(cache, location):
    offset = location.blender_frame - location.source_frame
    return cache.source.start_frame + offset, cache.source.end_frame + offset


def plan_options(requested, domain):
    if requested[0] > requested[1]:
        raise ValueError('Animation Start must not exceed Animation End.')
    if requested[0] < domain[0] or requested[1] > domain[1]:
        raise ValueError(f'Choose an animation range within source frames {domain[0]}–{domain[1]}.')
    return {} if tuple(requested) == tuple(domain) else {'frame_range': tuple(requested)}


def owners(scene, project_id, source_id):
    return [o for o in scene.objects if o.type in ('ARMATURE', 'CAMERA')
            and o.get('vta_project_id') == project_id and o.get('vta_source_id') == source_id]


def fingerprint(obj):
    return (obj.data if obj.type == 'CAMERA' else obj).get('vta_reconstruction_fingerprint', LEGACY)


def migration_range(scene, plan, incoming, domain, offset):
    """Smallest contiguous interval covering every old pose/visible frame.

    Calibration is source-wide, so all existing Character output and the shared
    camera must migrate together, even if only one owner has a different stamp.
    User Object Mode curves and hidden availability guard keys are not coverage.
    """
    targets = owners(scene, plan['project_id'], plan['source_id'])
    if not any(fingerprint(o) != incoming for o in targets):
        return None
    selected=plan.get('character_scope')
    if selected is not None and any(o.type=='ARMATURE' and o.get('vta_character_id') not in selected for o in targets):
        raise ValueError('Shared camera calibration needs an update. Select all existing Characters for this source and retry; unselected animation was not changed.')
    from .vta_camera_cleanup import require_source_aligned
    require_source_aligned(scene, [o.get('vta_character_id') for o in targets if o.type == 'ARMATURE'],
                          plan['project_id'], plan['source_id'])
    frames = set()
    for obj in targets:
        if obj.type != 'ARMATURE':
            continue
        data = obj.animation_data
        if data is None or data.action is None or data.nla_tracks or data.drivers:
            raise ValueError('Existing animation cannot be updated safely with NLA or drivers. Keep it in a separate scene.')
        curves = list(_iter_action_fcurves(data.action))
        frames.update(float(p.co.x) - offset for fc in curves
                      if fc.data_path.startswith('pose.bones[') for p in fc.keyframe_points)
        visibility = [fc for fc in curves if fc.data_path in ('hide_viewport', 'hide_render')]
        # Include held/extrapolated visible output, not merely explicit pose keys.
        frames.update(f for f in range(domain[0], domain[1] + 1)
                      if not visibility or any(fc.evaluate(f + offset) < .5 for fc in visibility))
    if any(f != int(f) or f < domain[0] or f > domain[1] for f in frames):
        raise ValueError('Existing generated pose keys lie outside the source. Keep this animation in a separate scene before updating.')
    if not frames:
        return tuple(plan['source_range'])  # camera-only prior output
    return int(min(frames)), int(max(frames))


def failure_message(detail, cancelled=False):
    if cancelled:
        return 'Update cancelled. Your existing animation is unchanged.'
    if 'animation start must not exceed' in detail.lower() or 'choose an animation range within' in detail.lower():
        reason = detail + ' Adjust the range and retry.'
    elif any(message in detail.lower() for message in ('project path is too long','select character rows','select at least one character','select all existing characters')):
        reason = detail
    elif 'environment unavailable' in detail.lower() or 'runtime is incomplete' in detail.lower():
        reason = 'Install the body models/runtime in BlendCap Preferences, then retry.'
    elif 'interpolate' in detail.lower() and ('pose' in detail.lower() or 'anchor' in detail.lower()):
        reason = detail + ' Adjust the repair endpoints or review its neighboring poses, then retry.'
    elif any(word in detail.lower() for word in ('fingerprint', 'incompatible')):
        reason = 'Existing animation needs to be updated to the current reconstruction method. Retry Generate Animation Range and accept the update.'
    elif 'calibration' in detail.lower():
        reason = 'Source-camera calibration could not be established. Check the installed body and FOV models, then retry; see Details for the cause.'
    elif 'manual focal' in detail.lower():
        reason = 'Choose Sample first frame or Sample every frame for FOV, then retry. Manual focal entry is not supported for reviewed VTA animation.'
    elif 'review' in detail.lower() and ('changed' in detail.lower() or 'stale' in detail.lower()):
        reason = 'Review changed during generation. Retry Generate Animation Range.'
    elif 'no trustworthy' in detail.lower() or 'no successful' in detail.lower():
        reason = 'Review a usable candidate or provide valid repair anchors in this range, then retry.'
    else:
        reason = 'Check the source, reviewed poses and repair endpoints, then retry. More information is available in Details.'
    return 'Couldn’t update the animation. Your existing animation was kept unchanged. ' + reason


def import_generation(paths, location, backend, *, migration=False):
    """Publish migration + requested output in one rollback boundary.

    Worker failures never enter here. Existing generated Actions/camera data are
    kept alive until both imports succeed; no review files are written.
    """
    from . import vta_skeletal_output as output
    if not migration:
        if len(paths) != 1:
            raise ValueError('Unexpected generation output count')
        return output.import_reviewed_output(paths[0], location, backend, configure_views=False)
    if len(paths) != 2:
        raise ValueError('Update requires migration and requested output; previous animation retained')
    reports = [json.loads(Path(p).read_text(encoding='utf-8')) for p in paths]
    stamps = [backend._core.reconstruction_fingerprint(r.get('reconstruction'),
              r['plan'].get('source_video_sha256')) for r in reports]
    if stamps[0] == LEGACY or stamps[0] != stamps[1]:
        raise ValueError('Migration and requested reconstruction fingerprints differ')
    scene = bpy.context.scene
    first = reports[0]['plan']
    if any(r['plan']['project_id'] != first['project_id'] or r['plan']['source_id'] != first['source_id'] for r in reports):
        raise ValueError('Migration and requested output belong to different sources')
    targets = owners(scene, first['project_id'], first['source_id'])
    snapshots = []
    for obj in targets:
        data = obj.animation_data
        snapshots.append((obj, obj.data, data.action if data else None, data.action_slot if data else None,
            {k:obj[k] for k in obj.keys() if k.startswith('vta_')}, obj.parent,
            obj.parent_type, obj.parent_bone, obj.matrix_parent_inverse.copy(), obj.matrix_basis.copy(),
            obj.show_in_front, obj.hide_viewport, obj.hide_render, obj.name))
    before = {kind:set(getattr(bpy.data, kind)) for kind in ('objects','actions','armatures','cameras','images')}
    camera = scene.camera
    render = (scene.render.resolution_x, scene.render.resolution_y, scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)
    try:
        output.import_reviewed_output(paths[0], location, backend, configure_views=False, migration_approved=True)
        return output.import_reviewed_output(paths[1], location, backend, configure_views=False)
    except Exception:
        scene.camera = camera
        for obj, old_data, action, slot, meta, parent, ptype, bone, inverse, basis, front, hidden, render_hidden, name in snapshots:
            obj.data = old_data
            if obj.animation_data:
                obj.animation_data.action = action
                if action:
                    obj.animation_data.action_slot = slot
            for key in tuple(obj.keys()):
                if key.startswith('vta_'):
                    del obj[key]
            for key, value in meta.items():
                obj[key] = value
            obj.parent, obj.parent_type, obj.parent_bone = parent, ptype, bone
            obj.matrix_parent_inverse, obj.matrix_basis = inverse, basis
            obj.show_in_front, obj.hide_viewport, obj.hide_render = front, hidden, render_hidden
            obj.name = name
        (scene.render.resolution_x, scene.render.resolution_y, scene.render.pixel_aspect_x, scene.render.pixel_aspect_y) = render
        for obj in set(bpy.data.objects) - before['objects']:
            bpy.data.objects.remove(obj, do_unlink=True)
        for kind in ('armatures','cameras','actions','images'):
            for item in set(getattr(bpy.data, kind)) - before[kind]:
                if item.users == 0:
                    getattr(bpy.data, kind).remove(item)
        raise
