"""Host-owned CFR playback timing; source frame identity never changes."""
import math
from pathlib import Path

import bpy

from .vta_scene_root import validate_scene_root, supported_parent, find_scene_root

SCENE_TAG = 'vta_scene_timing_version'
SCENE_VERSION = 1


def source_rate(source):
    if source.get('variable_frame_rate') or source.get('vfr'):
        raise ValueError('Variable-frame-rate sources need a separate timing policy; no conversion was performed.')
    value = source.get('fps')
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Source playback rate is unavailable or invalid.')
    nominal = max(1, round(value))
    limits = bpy.types.RenderSettings.bl_rna.properties['fps']
    if not limits.hard_min <= nominal <= limits.hard_max:
        raise ValueError('Source playback rate cannot be represented by Blender.')
    base = nominal / value
    limits = bpy.types.RenderSettings.bl_rna.properties['fps_base']
    if not limits.hard_min <= base <= limits.hard_max:
        raise ValueError('Source playback rate cannot be represented by Blender.')
    return nominal, base


def owns_scene(scene, project_id, source):
    return (type(scene.get(SCENE_TAG)) is int and scene.get(SCENE_TAG) == SCENE_VERSION
            and scene.get('vta_project_id') == project_id
            and scene.get('vta_source_id') == source['source_id']
            and scene.get('vta_source_video_sha256') == source['video_sha256'])


def match_source_rate(scene, project_id, source):
    """Explicit timing only; never touch Actions, frame bounds, or state files."""
    if not owns_scene(scene, project_id, source):
        raise ValueError('Use the matching VTA-owned Scene; unrelated Scenes cannot be retimed.')
    fps, base = source_rate(source)
    before = scene.render.fps, scene.render.fps_base
    try:
        scene.render.fps, scene.render.fps_base = fps, base
        if not math.isclose(scene.render.fps / scene.render.fps_base, source['fps'], rel_tol=1e-7, abs_tol=1e-6):
            raise ValueError('Blender cannot store this source rate accurately enough.')
    except Exception:
        scene.render.fps, scene.render.fps_base = before
        raise


def _copy_action(owner):
    ad = owner.animation_data
    if ad is not None:
        if ad.drivers or ad.nla_tracks:
            raise ValueError('VTA Scene copy cannot safely isolate drivers or NLA; preserve the original Scene.')
        if ad.action is not None:
            slot = ad.action_slot.identifier if ad.action_slot is not None else None
            ad.action = ad.action.copy()
            if slot is not None:
                ad.action_slot = next(s for s in ad.action.slots if s.identifier == slot)


def validate_source_strip(strip):
    if (strip is None or strip.type != 'MOVIE' or strip.use_reverse_frames or strip.retiming_keys
            or strip.modifiers):
        raise ValueError('Use an unretimed movie strip to prepare a VTA Scene; no source remapping was performed.')
    if strip.frame_final_start - strip.frame_offset_start != 1:
        raise ValueError('VTA timing requires Blender frame = source frame + 1; keys were not moved.')


def prepare_scene(scene, project_id, source, source_path, strip, *, new=False):
    """Reuse a matching owned Scene, or clone ONLY scoped VTA content.

    Never adopt a user Scene by its name/content. Originals remain linked only
    to their original Scene; copied rigs/data/Actions are independently editable.
    Caller switches the window only after this transaction succeeds.
    """
    source_rate(source)
    validate_source_strip(strip)
    if not new and owns_scene(scene, project_id, source):
        return scene
    if SCENE_TAG in scene and scene.get(SCENE_TAG) != SCENE_VERSION:
        raise ValueError('Unsupported VTA Scene ownership version; no Scene was changed.')
    # Several independent working Scenes may intentionally share one source.
    # Never choose an arbitrary sibling; an active owner is already unambiguous.
    matches = [] if new else [s for s in bpy.data.scenes if owns_scene(s, project_id, source)]
    if len(matches) > 1:
        raise ValueError('Multiple VTA Scenes own this source; select the desired one in the main Scene selector.')
    if matches:
        return matches[0]
    if scene.animation_data and scene.animation_data.action:
        from .retarget.fcurves import _iter_action_fcurves
        if any(f.data_path.startswith(strip.path_from_id()) for f in _iter_action_fcurves(scene.animation_data.action)):
            raise ValueError('Animated source-strip presentation cannot be copied safely; no Scene animation was migrated.')
    # The supported host domain is always B=S+1. A differently placed/trimmed
    # legacy output must not be moved or resampled implicitly.
    path = Path(source_path)
    if not path.is_file():
        raise ValueError('Source video is missing; no Scene was created.')
    find_scene_root(scene, project_id, source['source_id'])
    targets = [o for o in scene.objects if o.get('vta_project_id') == project_id
               and o.get('vta_source_id') == source['source_id']]
    for obj in targets:
        if obj.type == 'EMPTY':
            validate_scene_root(obj, project_id, source['source_id'])
        elif obj.type not in {'CAMERA', 'ARMATURE'} or not supported_parent(obj):
            raise ValueError('Unsupported owned object in VTA Scene copy; preserve the original Scene.')
        if obj.constraints or (obj.type == 'ARMATURE' and any(b.constraints for b in obj.pose.bones)):
            raise ValueError('VTA Scene copy cannot safely isolate transform constraints.')
        for owner in (obj, obj.data) if obj.data else (obj,):
            ad = owner.animation_data
            if ad and (ad.drivers or ad.nla_tracks):
                raise ValueError('VTA Scene copy cannot safely isolate drivers or NLA.')
    if any(o.parent and o.parent not in targets for o in targets):
        raise ValueError('VTA Scene copy cannot preserve an unrelated parent; no objects were migrated.')
    before = {kind: set(getattr(bpy.data, kind)) for kind in ('scenes','objects','armatures','cameras','actions')}
    try:
        working = bpy.data.scenes.new('VTA - ' + path.stem)
        working[SCENE_TAG] = SCENE_VERSION
        working['vta_project_id'] = project_id
        working['vta_source_id'] = source['source_id']
        working['vta_source_video_sha256'] = source['video_sha256']
        match_source_rate(working, project_id, source)
        for field in ('resolution_x','resolution_y','resolution_percentage','pixel_aspect_x','pixel_aspect_y'):
            setattr(working.render, field, getattr(scene.render, field))
        working.frame_start, working.frame_end = 1, source['end_frame'] + 1
        # Do not clone legacy Scene controls/pointers to unrelated user rigs.
        # Initialize the typed RNA group, not a shadow custom ID property.
        # Suppress only the interactive source-mode warning during staging;
        # its callback addresses this new Scene via self.id_data.
        if hasattr(working, 'blendcap_props'):
            from . import properties
            previous_default = properties._SETTING_DEFAULT_SOURCE
            properties._SETTING_DEFAULT_SOURCE = True
            try:
                working.blendcap_props.video_source = 'VSE'
            finally:
                properties._SETTING_DEFAULT_SOURCE = previous_default
        if 'blendcap_vta_timeline_text_size' in scene:
            working['blendcap_vta_timeline_text_size'] = scene['blendcap_vta_timeline_text_size']
        working.use_preview_range = scene.use_preview_range
        working.frame_preview_start, working.frame_preview_end = scene.frame_preview_start, scene.frame_preview_end
        copies = {}
        for obj in targets:
            copy = obj.copy()
            working.collection.objects.link(copy)
            copies[obj] = copy
            _copy_action(copy)
            if obj.data is not None:
                copy.data = obj.data.copy()
                _copy_action(copy.data)
        for obj, copy in copies.items():
            copy.parent = copies.get(obj.parent)
            copy.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
            copy.matrix_basis = obj.matrix_basis.copy()
        cameras = [o for o in copies.values() if o.type == 'CAMERA']
        if len(cameras) > 1:
            raise ValueError('Multiple VTA source cameras own this source.')
        working.camera = cameras[0] if cameras else None
        movie = working.sequence_editor_create().strips.new_movie(strip.name, str(path), channel=strip.channel, frame_start=1)
        working.sequence_editor.active_strip = movie
        # Copy source-strip presentation only. Never copy Scene animation or
        # other strips/effects. Retimed/reversed sources were rejected above.
        for owner, old in ((movie.transform, strip.transform),(movie.crop, strip.crop)):
            for prop in old.bl_rna.properties:
                if prop.identifier != 'rna_type' and not prop.is_readonly:
                    setattr(owner, prop.identifier, getattr(old, prop.identifier))
        working.frame_set(scene.frame_current, subframe=scene.frame_subframe)
        return working
    except Exception:
        for kind in ('scenes','objects','armatures','cameras','actions'):
            for item in set(getattr(bpy.data, kind)) - before[kind]:
                getattr(bpy.data, kind).remove(item, do_unlink=True)
        raise
