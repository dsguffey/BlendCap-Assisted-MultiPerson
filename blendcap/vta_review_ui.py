"""First visible Blender shell for private VTA current-frame review."""

from __future__ import annotations

from pathlib import Path
import json
import math
import os
import subprocess
import textwrap
import traceback
from types import SimpleNamespace

import bpy

from ..blendcap_vta_review import (
    PrivateCoreBackend,
    ReviewLocation,
    SelectionState,
    VtaReviewUiError,
    HARD_STATE_LABELS,
    character_name_exists,
    companion_root_for_blend,
    confirmation_lines,
    hard_state_confirmation_lines,
    map_vse_frame_to_source,
    map_visible_analysis_range,
    tracking_update_error,
    tracking_update_success,
    user_message,
    visible_frame,
    validate_output_mapping,
)
from ..blendcap_vta_runtime import discover_vta_core
from ..blendcap_vta_analysis_runtime import (
    AnalysisRuntimeUnavailable, resolve_analysis_runtime,
)
from ..blendcap_vta_analyze_worker import STATUS_NAME, STOP_NAME
from ..blendcap_vta_timeline import ReviewKeySelection, TimelineCharacterSelection, key_at
from ..blendcap_vta_area_mode import ROLE_PREVIEW, ROLE_TIMELINE, WORKSPACE_TAG, role_for
from . import vta_invalidation, vta_overlay, vta_timeline_overlay
from .vta_presentation import Presentation, source_identity, pointer, at_frame, file_signature
from .helpers import (
    _active_video_strip, _python_exe, _resolve_video_path, _strip_visible_end, addon_prefs,
)


_selection = SelectionState()
_view = None
_location = None
_status = ""
_status_is_error = False
_backend = None
_backend_location = None
_backend_root = None
_project_signature = None
_cache_signature = None
_timeline = None
_timeline_identity = None
_presentation_scene = None
_active_review_scene = None
_scene_sessions = {}
_validated_sources = {}
_observer_tokens = {}
_display = Presentation()
_display_backend = None
_review_key_selection = ReviewKeySelection()
_character_selections = {}
_repair_selections = {}
_analysis_process = None
_analysis_progress_started = False
_analysis_root = None
_analysis_total = 0
_analysis_start = None
_analysis_end = None
_analysis_fallback_status = None
_NO_SELECTION = "__blendcap_vta_no_selection__"
_candidate_enum_items = [(_NO_SELECTION, "No candidate", "Refresh the current frame")]
_character_enum_items = [(_NO_SELECTION, "No Character", "Refresh the current frame")]
_syncing_properties = False
_overlap_cycle = vta_overlay.OverlapCycle()
_reconstruction_process = None
_reconstruction_location = None
_reconstruction_log = None
_reconstruction_scene = None
_reconstruction_range = None
_generation_status = ''
_reconstruction_stop = None
_generation_job = None
_generation_details = ''
_operation_feedback = {}
_character_scroll = {}
_pending_presentation = None
# Python-only session memory: source/project scope and stable Character ID,
# never a candidate, RNA property, saved key or .blend field.
_last_assignment_target = None


@bpy.app.handlers.persistent
def _reset_repeat_assignment_session(_unused=None):
    global _last_assignment_target
    _last_assignment_target = None


@bpy.app.handlers.persistent
def _cancel_generation_before_load(_unused=None):
    if _reconstruction_process is not None and _reconstruction_process.poll() is None:
        _cancel_generation_request()


def _managed_context(context):
    workspace = getattr(context, 'workspace', None)
    screen = getattr(context, 'screen', None)
    return bool((workspace is not None and workspace.get(WORKSPACE_TAG) == 1)
                or (hasattr(screen, 'areas') and role_for(workspace, screen,
                            getattr(context, 'area', None)) in {ROLE_PREVIEW, ROLE_TIMELINE}))


def _requires_owned_scene(context):
    if hasattr(getattr(context, 'workspace', None), 'blendcap_vta_loaded_scene'):
        return True
    if _managed_context(context):
        return True
    # Preserve the initial legacy source/preparation workflow. Once this file
    # has owned Scenes, the sidebar must not resurrect unowned review state
    # merely because the user changed to an ordinary workspace.
    return (isinstance(getattr(context, 'scene', None), bpy.types.Scene)
            and any(s.get('vta_scene_timing_version') == 1 for s in bpy.data.scenes))


def _context_for_main_scene(context):
    from .vta_scene_session import main_scene, scene_context
    return scene_context(context,main_scene(context))


def loaded_context_matches(context, target=None):
    # Unregistered host-independent unit adapters retain their prior contract.
    # Every enabled Blender workspace has the registered session properties.
    workspace = getattr(context,'workspace',None)
    if not hasattr(workspace,'blendcap_vta_loaded_scene'):
        return True
    from .vta_scene_session import matches
    return matches(context,target)


def require_loaded_context(context):
    if not loaded_context_matches(context):
        from .vta_scene_session import warning
        raise VtaReviewUiError(warning(context) or 'Enter VTA Mode / Load VTA Scene first.')


def record_scene_source(scene, info):
    """Cache validated metadata, never an authority decision or disk write."""
    identity = source_identity(scene, bpy.data.filepath)
    _validated_sources[pointer(scene)] = (identity, (info[0], dict(info[1]), info[2], info[3]),
        (file_signature(info[2].companion_root/'project_state.json'), file_signature(info[2].source_path)))


def cached_scene_source(context):
    from .vta_scene_session import main_scene
    scene = main_scene(context)
    entry = _validated_sources.get(pointer(scene)) if scene is not None else None
    identity = source_identity(scene, bpy.data.filepath) if scene is not None else None
    return entry[1] if entry and identity is not None and entry[0] == identity else None


def _source_files_changed(context, info):
    from .vta_scene_session import main_scene
    entry = _validated_sources.get(pointer(main_scene(context)))
    stamp = (file_signature(info[2].companion_root/'project_state.json'), file_signature(info[2].source_path))
    return entry is None or entry[2] != stamp


def _activate_review_scene(scene):
    """Invalidate presentation only; retain each Scene's transient selections."""
    global _active_review_scene, _view, _location, _timeline, _timeline_identity
    global _project_signature, _cache_signature, _presentation_scene
    global _selection, _review_key_selection, _character_selections, _repair_selections
    global _last_assignment_target
    if scene is _active_review_scene:
        return False
    if _active_review_scene is not None:
        try:
            _scene_sessions[_active_review_scene.as_pointer()] = (_selection, _review_key_selection,
                _character_selections, _repair_selections, _last_assignment_target)
        except ReferenceError:
            pass  # A deleted Scene cannot retain a session.
    (_selection, _review_key_selection, _character_selections, _repair_selections,
     _last_assignment_target) = _scene_sessions.get(scene.as_pointer() if scene else None,
        (SelectionState(), ReviewKeySelection(), {}, {}, None))
    _active_review_scene = scene
    _view = _location = _timeline = _timeline_identity = None
    _project_signature = _cache_signature = _presentation_scene = None
    _rebuild_enum_items()
    return True


def _presentation_current(context):
    """Fail closed before a timer runs; never draw another Scene/frame's data."""
    if not _requires_owned_scene(context):
        return True
    if not loaded_context_matches(context):
        return False
    from .vta_scene_session import main_scene
    scene = main_scene(context)
    if scene is not _presentation_scene or _location is None:
        return False
    if scene.frame_current != _location.blender_frame:
        return False
    if (hasattr(getattr(context, 'workspace', None), 'blendcap_vta_loaded_scene') and
            cached_scene_source(context) is None):
        return False
    if (not hasattr(getattr(context, 'workspace', None), 'blendcap_vta_loaded_scene') and
            isinstance(scene, bpy.types.Scene) and
            _vse_source_path(_active_video_strip(scene.sequence_editor)) != _location.source_path):
        return False
    workspace = getattr(context, 'workspace', None)
    if role_for(workspace, getattr(context, 'screen', None), getattr(context, 'area', None)) == ROLE_PREVIEW:
        return (getattr(workspace, 'sequencer_scene', None) in (None, scene))
    return True


def _source_actions_available(context):
    if _analysis_locked():
        return False
    if not _requires_owned_scene(context):
        return True  # Existing legacy source workflow outside VTA-managed editors.
    if not loaded_context_matches(context):
        return False
    try:
        from .vta_scene_timing import owns_scene
        bound = _context_for_main_scene(context)
        info = cached_scene_source(bound)
        if info is None:
            if hasattr(getattr(context, 'workspace', None), 'blendcap_vta_loaded_scene'):
                return False
            info = _timing_source(bound)  # Unregistered legacy unit adapters.
        pid, source, _location, _strip = info
        return owns_scene(bound.scene, pid, source)
    except Exception:
        return False


@bpy.app.handlers.persistent
def _reset_scene_presentation(_unused=None):
    """Clear content/session state, never workspace roles or Blender context.

    Add-on enable runs inside RestrictBlend, where context.scene/data are
    unavailable. The runtime observer later reads context without choosing it.
    This same Python-only reset is also safe during file loading.
    """
    global _active_review_scene, _pending_presentation, _presentation_scene
    global _view, _location, _timeline, _timeline_identity, _project_signature, _cache_signature
    global _selection, _review_key_selection, _character_selections, _repair_selections
    global _last_assignment_target
    global _display_backend
    _scene_sessions.clear()
    _validated_sources.clear()
    _observer_tokens.clear()
    _display.clear()
    _display_backend = None
    _load_scene_enum_items.clear()
    _operation_feedback.clear()
    _active_review_scene = None
    _pending_presentation = None
    _view = _location = _timeline = _timeline_identity = _presentation_scene = None
    _project_signature = _cache_signature = None
    _selection = SelectionState()
    _review_key_selection = ReviewKeySelection()
    _character_selections = {}
    _character_scroll.clear()
    _repair_selections = {}
    _last_assignment_target = None
    _rebuild_enum_items()


def _observer_identity(context):
    from .vta_scene_session import main_scene, MODE, LAST
    scene = main_scene(context)
    workspace = context.workspace
    info = cached_scene_source(context)
    old_entry = _validated_sources.get(pointer(scene))
    location = info[2] if info else old_entry[1][2] if old_entry else None
    # Stats only, no model loads. This slow observation is never used in draws
    # or frame handlers; it detects independent canonical/evidence file edits.
    stamp = _display.signature(location) if location else None
    if location is None:
        try:
            root = companion_root_for_blend(bpy.data.filepath)
            strip = scene.sequence_editor.active_strip if scene.sequence_editor else None
            path = _vse_source_path(strip)
            stamp = (file_signature(root/'project_state.json'), file_signature(path) if path else None)
        except (VtaReviewUiError, OSError):
            pass
    return (pointer(scene), scene.frame_current, pointer(workspace),
        getattr(workspace, MODE, False), pointer(getattr(workspace, LAST, None)),
        pointer(workspace.sequencer_scene), source_identity(scene, bpy.data.filepath),
        loaded_context_matches(context), stamp,
        getattr(context.window_manager, 'blendcap_vta_show_suppressed', False),
        _configured_core_location(context))


def _observe_context(context):
    key = pointer(context.window)
    token = _observer_identity(context)
    if _observer_tokens.get(key) != token:
        refresh(context, check_disk=True)
        _observer_tokens[key] = _observer_identity(context)


def _scene_observer_timer():
    """Passive review refresh only. No Scene/editor/camera binding writes."""
    for window in tuple(bpy.context.window_manager.windows):
        with bpy.context.temp_override(window=window):
            context = bpy.context
            if _managed_context(context):
                _observe_context(context)
            else:
                # Opted-in editors also exist outside the bundled preset.
                area = next((a for a in context.screen.areas if role_for(
                    context.workspace, context.screen, a) in {ROLE_PREVIEW, ROLE_TIMELINE}), None)
                if area is not None:
                    with bpy.context.temp_override(area=area):
                        _observe_context(bpy.context)
    return 0.2


@bpy.app.handlers.persistent
def _frame_presentation_changed(scene, _depsgraph=None):
    """Correct-frame in-memory review before redraw, no ID/Scene/disk writes."""
    if _display_backend is None:
        return
    for window in tuple(bpy.context.window_manager.windows):
        if window.scene != scene:
            continue
        with bpy.context.temp_override(window=window):
            context = bpy.context
            if loaded_context_matches(context) and cached_scene_source(context) is not None:
                refresh(context, memory_only=True)


def _assignment_scope():
    if _view is None or _location is None:
        return None
    return (_location.companion_root, _location.source_path, _view.source_id)


def _remember_assignment(character_id):
    global _last_assignment_target
    _last_assignment_target = (*_assignment_scope(), character_id)


def _repeat_character_id():
    global _last_assignment_target
    if _last_assignment_target is None or _assignment_scope() is None:
        return None
    if (_last_assignment_target[:-1] != _assignment_scope() or
            not any(c.character_id == _last_assignment_target[-1] for c in _view.characters)):
        _reset_repeat_assignment_session()
        return None
    return _last_assignment_target[-1]


def _repeat_assignment_context(context):
    return (getattr(getattr(context, 'area', None), 'type', None) == 'SEQUENCE_EDITOR'
            and getattr(getattr(context, 'space_data', None), 'view_type', None)
            in {'PREVIEW', 'SEQUENCER_PREVIEW'}
            and role_for(getattr(context, 'workspace', None),
                         getattr(context, 'screen', None), context.area) == ROLE_PREVIEW)


def _presentation_timer():
    """Blender ID writes belong to a main-thread timer, never panel refresh."""
    global _pending_presentation, _status, _status_is_error
    pending, _pending_presentation = _pending_presentation, None
    if pending is None:
        return None
    scene, root = pending
    if _requires_owned_scene(bpy.context) and not loaded_context_matches(bpy.context):
        return None
    if _location is None or _location.companion_root != root:
        return None
    try:
        _sync_properties(bpy.context)
        from .vta_skeletal_output import mark_stale_outputs
        mark_stale_outputs(root)
    except Exception as exc:
        # Presentation failure must not erase a successfully queried review.
        _status, _status_is_error = 'Review display update failed: ' + user_message(exc), True
    return None


def _queue_presentation(context, scene=None):
    global _pending_presentation
    _pending_presentation = (scene, _location.companion_root) if _location else None
    if not bpy.app.timers.is_registered(_presentation_timer):
        bpy.app.timers.register(_presentation_timer, first_interval=0.0)


def _configured_core_location(context) -> str:
    prefs = addon_prefs(context)
    if prefs is None:
        return ""
    value = getattr(prefs, "vta_core_location", "") or ""
    return bpy.path.abspath(value) if value else ""


def _get_backend(context, *, force: bool = False):
    global _backend, _backend_location, _backend_root
    configured = _configured_core_location(context)
    cached_root_missing = _backend_root is not None and not (
        _backend_root / "vta_core" / "__init__.py"
    ).is_file()
    if force or _backend is None or configured != _backend_location or cached_root_missing:
        resolution = discover_vta_core(configured)
        if not resolution.available:
            _backend = None
            _backend_location = configured
            _backend_root = None
            raise VtaReviewUiError(resolution.message)
        _backend = PrivateCoreBackend(core=resolution.core)
        _backend_location = configured
        _backend_root = resolution.root
    return _backend


def _location_from_context(context, *, allow_outside=False) -> ReviewLocation:
    scene = getattr(context, "scene", None)
    if scene is None:
        raise VtaReviewUiError("Open a Blender scene with a selected movie strip.")
    props = scene.blendcap_props
    if props.video_source != 'VSE':
        raise VtaReviewUiError(
            "VTA review requires Video Editor Strip source mode for exact frame mapping."
        )
    seq_ed = scene.sequence_editor
    strip = _active_video_strip(seq_ed)
    if strip is None or getattr(strip, "type", "") != 'MOVIE':
        raise VtaReviewUiError("Select the matching movie strip in the Video Sequencer.")
    resolved = _resolve_video_path(context)
    if resolved is None:
        raise VtaReviewUiError("The active VSE source cannot be resolved.")
    source_path, source_offset, _max_frames, visible_start, _channel, mode = resolved
    if mode != 'VSE':
        raise VtaReviewUiError("The active source is not an exact VSE mapping.")
    source_frame = map_vse_frame_to_source(
        blender_frame=int(visible_start) if allow_outside else int(scene.frame_current),
        visible_start=int(visible_start),
        visible_end=_strip_visible_end(strip),
        source_offset=int(source_offset),
    )
    if allow_outside:
        source_frame += int(scene.frame_current) - int(visible_start)
    return ReviewLocation(
        companion_root=companion_root_for_blend(bpy.data.filepath),
        source_path=Path(source_path).resolve(),
        source_frame=source_frame,
        blender_frame=int(scene.frame_current),
    )


def _timing_source(context):
    """Hash-validated source metadata, independent of current playhead bounds."""
    location = _location_from_context(context, allow_outside=True)
    backend = _get_backend(context)
    project = backend._core.load_project_state(location.companion_root).to_dict()
    digest = backend._source_hash(location.source_path)
    sources = [s for s in project['sources'] if s.get('video_sha256') == digest]
    if len(sources) != 1:
        raise VtaReviewUiError('Active clip does not uniquely match the VTA project source.')
    strip = _active_video_strip(context.scene.sequence_editor)
    from .vta_scene_timing import validate_source_strip
    validate_source_strip(strip)
    if not math.isclose(float(strip.fps), sources[0]['fps'], rel_tol=1e-6, abs_tol=1e-3):
        raise VtaReviewUiError('Movie FPS differs from the VTA source metadata; use the original source clip. No Scene was retimed.')
    return project['project_id'], sources[0], location, strip




_load_scene_enum_items = []


def build_load_scene_choices(context):
    global _load_scene_enum_items
    from .vta_scene_session import validate
    items = []
    for scene in bpy.data.scenes:
        try:
            validate(context, scene)
        except Exception:
            continue
        items.append((scene.name, scene.name, 'Load this metadata-owned VTA Scene'))
    _load_scene_enum_items = items
    return _load_scene_enum_items


def load_scene_items(_self, _context):
    """RNA draw callback: no validation, scans, project loads or filesystem."""
    return _load_scene_enum_items


class BLENDCAP_OT_vta_load_scene(bpy.types.Operator):
    bl_idname = 'blendcap.vta_load_scene'
    bl_label = 'Load VTA Scene'
    bl_description = 'Explicitly restore the selected VTA Scene and workspace once'
    scene_name: bpy.props.EnumProperty(name='VTA Scene', items=load_scene_items)

    @classmethod
    def poll(cls, context):
        return not _analysis_locked()

    def invoke(self, context, event):
        from .vta_scene_session import main_scene
        items = build_load_scene_choices(context)
        if not items:
            self.report({'ERROR'}, 'No valid VTA Scene available. Use New VTA Scene.')
            return {'CANCELLED'}
        active = main_scene(context)
        self.scene_name = active.name if active and active.name in {i[0] for i in items} else items[0][0]
        if len(items) == 1:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, 'scene_name')

    def execute(self, context):
        try:
            from .vta_scene_session import load
            load(context, bpy.data.scenes.get(self.scene_name))
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_enter_mode(bpy.types.Operator):
    bl_idname = 'blendcap.vta_enter_mode'
    bl_label = 'Enter VTA Mode'
    bl_description = 'Load the last successfully loaded VTA Scene once'

    @classmethod
    def poll(cls, context):
        return not _analysis_locked()

    def execute(self, context):
        try:
            from .vta_scene_session import enter
            enter(context)
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_exit_mode(bpy.types.Operator):
    bl_idname = 'blendcap.vta_exit_mode'
    bl_label = 'Exit VTA Mode'
    bl_description = 'Stop VTA review; preserve Scene, remembered target and editor configuration'

    def execute(self, context):
        from .vta_scene_session import exit_mode
        exit_mode(context)
        vta_invalidation.request_redraw()
        return {'FINISHED'}


def draw_scene_workflow(layout, context):
    for command in ('enter_mode', 'exit_mode', 'load_scene', 'new_scene'):
        layout.operator('blendcap.vta_' + command)
    from .vta_scene_session import warning
    message = warning(context)
    if message:
        layout.label(text=message, icon='INFO')


class BLENDCAP_OT_vta_new_scene(bpy.types.Operator):
    bl_idname = 'blendcap.vta_new_scene'
    bl_label = 'New VTA Scene'
    bl_description = 'Create and load a blank VTA-owned Scene; no source or animation is copied'

    @classmethod
    def poll(cls, context):
        return not _analysis_locked()

    def execute(self, context):
        try:
            if _analysis_locked():
                raise VtaReviewUiError('Unavailable while VTA analysis or generation is running.')
            from .vta_scene_session import create_blank, load
            scene = create_blank()
            try:
                load(context,scene)
            except Exception:
                bpy.data.scenes.remove(scene,do_unlink=True)
                raise
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_choose_clip(bpy.types.Operator):
    bl_idname = 'blendcap.vta_choose_clip'
    bl_label = 'Choose Clip'
    bl_description = 'Configure a blank VTA Scene; clear the current source before choosing another'
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default='*.mp4;*.mov;*.mkv;*.avi;*.webm', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        from .vta_scene_session import main_scene, source_bound, owned
        return (not _analysis_locked() and loaded_context_matches(context)
                and owned(main_scene(context)) and not source_bound(main_scene(context)))

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            from .vta_source_lifecycle import configure
            configure(context, self.filepath)
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_clear_source(bpy.types.Operator):
    bl_idname = 'blendcap.vta_clear_source'
    bl_label = 'Clear Source'
    bl_description = 'Detach this Scene source/content; preserve project history and Blender datablocks'

    @classmethod
    def poll(cls, context):
        from .vta_scene_session import main_scene, source_bound
        return (not _analysis_locked() and loaded_context_matches(context)
                and source_bound(main_scene(context)))

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            from .vta_source_lifecycle import clear
            clear(context)
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


def draw_vta_source(layout, context):
    from .vta_scene_session import main_scene, source_bound
    scene = main_scene(context)
    row = layout.row(align=True)
    if source_bound(scene):
        owned_strips = [s for s in scene.sequence_editor.strips if
                        s.get('vta_source_strip_version') == 1 and
                        s.get('vta_project_id') == scene.get('vta_project_id') and
                        s.get('vta_source_id') == scene.get('vta_source_id')] if scene.sequence_editor else []
        strip = owned_strips[0] if len(owned_strips) == 1 else _active_video_strip(scene.sequence_editor)
        path = _vse_source_path(strip)
        row.label(text=path.name if path else 'Bound VTA source', icon='FILE_MOVIE')
        row.operator('blendcap.vta_clear_source', text='', icon='X')
    else:
        row.operator('blendcap.vta_choose_clip', text='Choose Clip...', icon='FILE_MOVIE')


class BLENDCAP_OT_vta_match_source_fps(bpy.types.Operator):
    bl_idname = 'blendcap.vta_match_source_fps'
    bl_label = 'Match FPS to Source'
    bl_description = 'Match only this owned Scene playback rate; never move keys or repair ranges'

    @classmethod
    def poll(cls, context):
        try:
            from .vta_scene_timing import owns_scene, source_rate
            if not loaded_context_matches(context):
                return False
            context = _context_for_main_scene(context)
            info = cached_scene_source(context)
            if info is None:
                if hasattr(getattr(context, 'workspace', None), 'blendcap_vta_loaded_scene'):
                    return False
                info = _timing_source(context)
            pid, source, _location, _strip = info
            source_rate(source)
            return not _analysis_locked() and owns_scene(context.scene, pid, source)
        except Exception:
            return False

    def execute(self, context):
        try:
            from .vta_scene_timing import match_source_rate
            require_loaded_context(context)
            context = _context_for_main_scene(context)
            if _analysis_locked():
                raise VtaReviewUiError('Unavailable while VTA analysis or generation is running.')
            pid, source, _location, _strip = _timing_source(context)
            match_source_rate(context.scene, pid, source)
            vta_invalidation.request_redraw()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, user_message(error))
            return {'CANCELLED'}


def draw_scene_timing(layout, context, *, menu=False):
    try:
        from .vta_scene_timing import owns_scene
        info = cached_scene_source(context)
        if info is None:
            raise VtaReviewUiError('Load a validated VTA Scene first.')
        pid, source, _location, _strip = info
        owned = owns_scene(context.scene, pid, source)
        if not menu:
            layout.label(text=f'Scene: {context.scene.render.fps / context.scene.render.fps_base:g} fps · Source: {source["fps"]:g} fps')
    except Exception:
        owned = False
    row = layout.row()
    row.enabled = BLENDCAP_OT_vta_match_source_fps.poll(context)
    row.operator('blendcap.vta_match_source_fps', text='Match FPS to Source')


def _vse_source_path(strip) -> Path | None:
    """Resolve the source identity stored by a VSE movie strip."""
    if strip is None or getattr(strip, "type", "") != 'MOVIE':
        return None
    if "blendcap_source" in strip:
        value = str(strip["blendcap_source"])
    else:
        value = getattr(strip, "filepath", "")
    if not value:
        return None
    return Path(bpy.path.abspath(value)).resolve()


def _active_movie_strips(scene):
    sequence_editor = getattr(scene, "sequence_editor", None)
    strip = _active_video_strip(sequence_editor)
    return (strip,) if getattr(strip, "type", "") == 'MOVIE' else ()


def _owning_vse_scene(context, location: ReviewLocation):
    """Find the actual VSE owner of the hash-validated review source."""
    try:
        return vta_overlay.find_scene_owning_source(
            bpy.data.scenes,
            location.source_path,
            _active_movie_strips,
            _vse_source_path,
            preferred_scene=getattr(context, "scene", None),
        )
    except ValueError as exc:
        raise VtaReviewUiError(str(exc)) from exc


def _is_timeline_context(context) -> bool:
    area = getattr(context, 'area', None)
    return (getattr(area, 'type', None) == 'DOPESHEET_EDITOR' and
            role_for(getattr(context, 'workspace', None),
                     getattr(context, 'screen', None), area) == ROLE_TIMELINE)


def timeline_character_selection(context):
    """Transient selection owned by one Dope Sheet and source, never ProjectState."""
    if not _is_timeline_context(context) or _timeline is None or _location is None:
        return None
    pointer = lambda item: item.as_pointer() if hasattr(item, 'as_pointer') else id(item)
    key = (pointer(context.screen), pointer(context.area))
    identity = (str(_location.companion_root), _timeline.source_id,
                str(_location.source_path))
    previous = _character_selections.get(key)
    if previous is None or previous[0] != identity:
        _character_scroll.pop(key, None)
        previous = (identity, TimelineCharacterSelection())
        _character_selections[key] = previous
    previous[1].reconcile(_timeline)
    return previous[1]


def current_character_id(context=None):
    if context is not None and _is_timeline_context(context):
        selection = timeline_character_selection(context)
        return selection.active_id if selection is not None else None
    return _selection.character_id


def _use_timeline_active(context):
    if _is_timeline_context(context):
        _selection.character_id = current_character_id(context) or ''
        _sync_properties(context)


def tracking_character_ids(context=None):
    """Use the opted-in Dope Sheet's actual set, never the sidebar active value."""
    context = context or bpy.context
    if not _is_timeline_context(context):
        from ..blendcap_vta_workspace import review_area
        area = review_area(getattr(context, 'workspace', None), getattr(context, 'screen', None))
        if area is None:
            return ()
        context = SimpleNamespace(area=area, screen=context.screen,
                                  workspace=getattr(context, 'workspace', None))
    selection = timeline_character_selection(context)
    return selection.character_ids if selection is not None else ()


def selected_repair_range(context):
    if not _is_timeline_context(context) or _timeline is None or _location is None:
        return None
    pointer = lambda item: item.as_pointer() if hasattr(item, 'as_pointer') else id(item)
    key = (pointer(context.screen), pointer(context.area))
    identity = (str(_location.companion_root), _timeline.source_id, str(_location.source_path),
                _location.blender_frame - _location.source_frame)
    saved = _repair_selections.get(key)
    if saved is None:
        return None
    if saved[0] != identity or saved[1] not in _timeline.repair_ranges:
        _repair_selections.pop(key, None)
        return None
    return saved[1]


def clear_repair_selection(context):
    if getattr(context, 'screen', None) is None or getattr(context, 'area', None) is None:
        return
    pointer = lambda item: item.as_pointer() if hasattr(item, 'as_pointer') else id(item)
    _repair_selections.pop((pointer(context.screen), pointer(context.area)), None)


def select_repair_range(context, interval):
    """Inspect one exact existing range; preserve other rows, keys and time."""
    selection = timeline_character_selection(context)
    if selection is None or interval not in _timeline.repair_ranges:
        return False
    selection.select_box(_timeline, (interval.character_id,), extend=True,
                         active_id=interval.character_id)
    _selection.character_id = interval.character_id
    pointer = lambda item: item.as_pointer() if hasattr(item, 'as_pointer') else id(item)
    key = (pointer(context.screen), pointer(context.area))
    identity = (str(_location.companion_root), _timeline.source_id, str(_location.source_path),
                _location.blender_frame - _location.source_frame)
    _repair_selections[key] = (identity, interval)
    context.window_manager.blendcap_vta_motion_start = visible_frame(_location, interval.start_frame)
    context.window_manager.blendcap_vta_motion_end = visible_frame(_location, interval.end_frame)
    context.window_manager.blendcap_vta_repair_mode = interval.mode
    _sync_properties(context)
    vta_invalidation.request_redraw()
    return True


def _sync_properties(context) -> None:
    global _syncing_properties
    if _syncing_properties:
        return
    if _is_timeline_context(context):
        _selection.character_id = current_character_id(context) or ''
        _character_scroll.pop((pointer(context.screen), pointer(context.area)), None)
    else:
        sync_character_to_timeline(context)
    wm = context.window_manager
    _syncing_properties = True
    try:
        wm.blendcap_vta_candidate = _selection.observation_id or _NO_SELECTION
        wm.blendcap_vta_character = _selection.character_id or _NO_SELECTION
    finally:
        _syncing_properties = False
    vta_invalidation.request_redraw()


def overlay_state():
    """Read-only draw callback state; never queries or mutates private core."""
    return ((_view, _selection.observation_id) if _presentation_current(bpy.context)
            else (None, ''))


def timeline_state(context=None):
    if not _presentation_current(context or bpy.context):
        return (None, (), None, 0, 0)
    offset = 0 if _location is None else _location.blender_frame - _location.source_frame
    return (_timeline, _review_key_selection.keyframe_ids,
            _review_key_selection.active_keyframe_id, offset, _lane_scroll(context))


def _lane_scroll(context=None) -> int:
    """Focus the selected Character automatically; no exposed pagination knob."""
    active = current_character_id(context)
    if context is not None:
        saved = _character_scroll.get((pointer(getattr(context, 'screen', None)), pointer(getattr(context, 'area', None))))
        if saved and saved[0] == active:
            return saved[1]
    if _timeline is None or not active:
        return 0
    return next((index for index, item in enumerate(_timeline.characters)
                 if item.character_id == active), 0)


def sync_character_to_timeline(context):
    """Preview/sidebar selection updates existing row state, never navigation."""
    cid = _selection.character_id
    if (_syncing_properties or not cid or _timeline is None or _location is None
            or _is_timeline_context(context) or not _presentation_current(context)):
        return False
    changed = False
    for area in getattr(getattr(context, 'screen', None), 'areas', ()):
        target = SimpleNamespace(area=area, screen=context.screen, workspace=context.workspace)
        if not _is_timeline_context(target):
            continue
        selection = timeline_character_selection(target)
        if selection is None or cid not in {c.character_id for c in _timeline.characters}:
            continue
        first = _lane_scroll(target)
        selection.click(_timeline, cid)
        _character_scroll[(pointer(context.screen), pointer(area))] = (cid, first)
        changed = True
    if changed:
        vta_invalidation.request_redraw()
    return changed


def next_generic_character_name(view) -> str:
    """Choose an unused, domain-neutral name without renaming existing data."""
    names = {item.display_name.casefold() for item in view.characters}
    number = 1
    while f"character {number}" in names:
        number += 1
    return f"Character {number}"


def _analysis_status_for(root: Path, source_id: str | None = None) -> dict | None:
    path = root / "analysis" / STATUS_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        if source_id is not None and value.get("source_id") not in (None, source_id):
            return None
        return value
    except (OSError, ValueError):
        return None


def _analysis_locked(root: Path | None = None) -> bool:
    """One conservative VTA mutation lock, including an external worker."""
    if _analysis_process is not None and _analysis_process.poll() is None:
        return True
    if _reconstruction_process is not None and _reconstruction_process.poll() is None:
        return True
    active_root = root or getattr(_location, "companion_root", None)
    return bool(active_root is not None and
                (active_root / "analysis" / "vta-analysis.lock").exists())


def _analysis_percent(status: dict | None) -> float:
    if not isinstance(status, dict):
        return 0.0
    first, last = status.get("start_frame"), status.get("end_frame")
    completed = status.get("completed_in_requested_range")
    if not all(type(value) is int for value in (first, last, completed)) or last < first:
        return 0.0
    return 100.0 * min(max(completed, 0), last - first + 1) / (last - first + 1)


def _analysis_error_summary(detail: str) -> str:
    if "analysis status file" in detail.lower() or "vta-analysis-status.json" in detail.lower():
        return "Analysis failed: could not update its status file."
    return "Analysis failed. Open Error Details for the cause."


def _require_analysis_idle() -> None:
    if _analysis_locked():
        raise VtaReviewUiError("Unavailable while candidate analysis is running.")


def _edit_available(operator, *, quiet=False) -> bool:
    try:
        _require_analysis_idle()
        return True
    except VtaReviewUiError as exc:
        if not quiet:
            operator.report({'ERROR'}, str(exc))
        return False


def _analysis_timer():
    """Redraw persistent progress while the worker owns durable checkpoints."""
    global _analysis_process, _analysis_progress_started, _cache_signature
    global _analysis_fallback_status
    process = _analysis_process
    if process is None:
        return None
    manager = bpy.context.window_manager
    status = _analysis_status_for(_analysis_root) if _analysis_root is not None else None
    if hasattr(manager, "blendcap_vta_analysis_progress"):
        manager.blendcap_vta_analysis_progress = _analysis_percent(status)
    if _analysis_progress_started and status is not None:
        count = status.get("completed_in_requested_range")
        if isinstance(count, int):
            try:
                manager.progress_update(max(0, min(count, _analysis_total)))
            except RuntimeError:
                pass
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    exit_code = process.poll()
    if exit_code is None:
        return 0.5
    current_status = (status is not None and
                      status.get("pid") == getattr(process, "pid", None))
    if (exit_code != 0 and _analysis_root is not None
            and (not current_status or status.get("state") not in
                 {"failed", "cancelled", "complete"})):
        detail = "Analysis worker exited before it could publish a final status."
        try:
            lines = (_analysis_root / "analysis" / "vta-analysis.log").read_text(
                encoding="utf-8", errors="replace").splitlines()
            detail = next((line for line in reversed(lines)
                           if line.startswith("VTA analysis failed:")), detail)
        except OSError:
            pass
        _analysis_fallback_status = {
            "state": "failed", "error": detail,
            "start_frame": _analysis_start, "end_frame": _analysis_end,
            "completed_in_requested_range":
                status.get("completed_in_requested_range", 0) if status else 0,
            "total_analyzed_frames": status.get("total_analyzed_frames") if status else None,
            "source_coverage": status.get("source_coverage") if status else None,
        }
    _analysis_process = None
    _cache_signature = None  # force a fresh candidate projection after final checkpoint
    if _analysis_progress_started:
        try:
            manager.progress_end()
        except RuntimeError:
            pass
        _analysis_progress_started = False
    return None


def _configured_analysis_paths(context):
    prefs = addon_prefs(context)
    def preference_path(name):
        value = getattr(prefs, name, "") if prefs is not None else ""
        return bpy.path.abspath(value) if value else ""

    try:
        runtime = resolve_analysis_runtime(
            _backend_root, Path(_python_exe()),
            configured_python=preference_path("vta_analysis_python"),
            configured_source=preference_path("vta_kinesia_root"),
            configured_checkpoint=preference_path("vta_sam3_checkpoint"),
        )
    except AnalysisRuntimeUnavailable as exc:
        raise VtaReviewUiError(str(exc)) from exc
    return runtime.python, runtime.source, runtime.checkpoint


class BLENDCAP_OT_vta_analyze(bpy.types.Operator):
    bl_idname = "blendcap.vta_analyze"
    @classmethod
    def poll(cls, context):
        return _source_actions_available(context)
    bl_label = "Analyze Candidates"
    bl_description = "Analyze missing video frames with the existing offline SAM3 provider"

    def execute(self, context):
        global _analysis_process, _analysis_progress_started, _analysis_root, _analysis_total
        global _analysis_start, _analysis_end, _analysis_fallback_status
        global _status, _status_is_error
        try:
            require_loaded_context(context)
            context = _context_for_main_scene(context)
            location = _location_from_context(context)
            if _analysis_locked(location.companion_root):
                raise VtaReviewUiError("Unavailable while candidate analysis is running.")
            backend = _get_backend(context)
            source_root = _backend_root
            if source_root is None:
                raise VtaReviewUiError("Configure the private VTA core location.")
            first_source, last_source = backend.source_coverage(location)
            scope = context.window_manager.blendcap_vta_analyze_scope
            if scope == 'RANGE':
                start, end = map_visible_analysis_range(
                    location, first_source, last_source,
                    context.window_manager.blendcap_vta_analyze_start,
                    context.window_manager.blendcap_vta_analyze_end,
                )
            else:
                start, end = first_source, last_source
            python, kinesia, checkpoint = _configured_analysis_paths(context)
            root = location.companion_root
            command = [
                str(python), str(Path(__file__).resolve().parents[1] /
                                 "blendcap_vta_analyze_worker.py"),
                "--core-root", str(source_root), "--kinesia-root", str(kinesia),
                "--checkpoint", str(checkpoint), "--companion-root", str(root),
                "--video", str(location.source_path),
            ]
            if scope == 'RANGE':
                command += ["--start", str(start), "--end", str(end)]
            log_path = root / "analysis" / "vta-analysis.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONNOUSERSITE="1")
            with log_path.open("a", encoding="utf-8") as log:
                _analysis_process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    cwd=str(Path(__file__).resolve().parents[1]), env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            _analysis_root = root
            _analysis_total = end - start + 1
            _analysis_start, _analysis_end = start, end
            _analysis_fallback_status = None
            manager = context.window_manager
            manager.blendcap_vta_analysis_progress = 0.0
            if hasattr(manager, "progress_begin"):
                try:
                    manager.progress_begin(0, _analysis_total)
                    _analysis_progress_started = True
                except RuntimeError:
                    _analysis_progress_started = False
            if not bpy.app.timers.is_registered(_analysis_timer):
                bpy.app.timers.register(_analysis_timer, first_interval=0.5)
            _status = (f"Analyzing Blender frames {visible_frame(location, start)}–"
                       f"{visible_frame(location, end)} (inclusive).")
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


class BLENDCAP_OT_vta_analyze_stop(bpy.types.Operator):
    bl_idname = "blendcap.vta_analyze_stop"
    bl_label = "Cancel Analysis"
    bl_description = "Cancel between frames; completed candidate checkpoints remain valid"

    def execute(self, context):
        try:
            root = _location_from_context(context).companion_root
            status = _analysis_status_for(root, _view.source_id if _view else None)
            if status is None or status.get("state") not in {"analyzing", "running"}:
                raise VtaReviewUiError("No candidate analysis is running for this source.")
            (root / "analysis" / STOP_NAME).write_text("stop\n", encoding="utf-8")
            self.report({'INFO'}, "Cancel requested; completed frame checkpoints will remain.")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, user_message(exc))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_copy_analysis_error(bpy.types.Operator):
    bl_idname = "blendcap.vta_copy_analysis_error"
    bl_label = "Copy Error Details"
    bl_description = "Copy the full analysis error to the clipboard"

    def execute(self, context):
        try:
            root = _location_from_context(context).companion_root
            status = (_analysis_fallback_status if root == _analysis_root
                      and _analysis_fallback_status is not None
                      else _analysis_status_for(root))
            detail = status.get("error") if status else None
            if not isinstance(detail, str) or not detail:
                raise VtaReviewUiError("No analysis error details are available.")
            context.window_manager.clipboard = detail
            self.report({'INFO'}, "Analysis error details copied.")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, user_message(exc))
            return {'CANCELLED'}


def ensure_vta_preview_context(context, owning_scene) -> int:
    """One-shot explicit Load setup in exclusively VTA-managed VSE workspaces.

    Blender's selector is WORKSPACE-wide, not per area. If ordinary VSE areas
    share the workspace, preserve their choice and let VTA fail closed on a
    mismatch. No observer calls this; no other workspace or Scene strip changes.
    """
    workspace = getattr(context, "workspace", None)
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    areas = getattr(screen, "areas", ()) if screen is not None else ()
    screens = getattr(workspace, 'screens', ()) if workspace is not None else ()
    sequencers = [(screen, area) for screen in screens for area in screen.areas
                  if area.type == 'SEQUENCE_EDITOR']
    if not sequencers or any(role_for(workspace, screen, area) != ROLE_PREVIEW
                             for screen, area in sequencers):
        return 0
    changed = int(workspace.sequencer_scene != owning_scene)
    # One-shot explicit Load setup. Observers never repair this pointer.
    workspace.sequencer_scene = owning_scene
    for area in areas:
        if area.type == 'SEQUENCE_EDITOR':
            area.tag_redraw()
    return changed


def _rebuild_enum_items() -> None:
    global _candidate_enum_items, _character_enum_items
    if _view is None:
        _candidate_enum_items = [(_NO_SELECTION, "No candidate", "Refresh the current frame")]
        _character_enum_items = [(_NO_SELECTION, "No Character", "Refresh the current frame")]
        return
    _candidate_enum_items = [(_NO_SELECTION, "Select candidate...", "No candidate is selected")]
    _candidate_enum_items.extend(
        (
            row.observation_id,
            row.label,
            f"Assigned: {row.assigned_character_name}"
            if row.assigned_character_name else "Unassigned",
        )
        for row in _view.candidates
    )
    _character_enum_items = [(_NO_SELECTION, "Select Character...", "No Character is selected")]
    _character_enum_items.extend(
        (
            row.character_id,
            row.display_name + (
                f" ({HARD_STATE_LABELS[row.decision_state]})"
                if row.decision_state in HARD_STATE_LABELS
                else " (occupied)" if not row.available else ""
            ),
            row.character_id,
        )
        for row in _view.characters
    )


def refresh(context, *, force: bool = False, target_scene=None, check_disk=False,
            memory_only=False, validated_info=None) -> bool:
    global _view, _location, _project_signature, _status, _status_is_error
    global _timeline, _timeline_identity, _cache_signature, _presentation_scene, _display_backend
    try:
        info = None
        if _requires_owned_scene(context):
            if not loaded_context_matches(context, target_scene):
                _activate_review_scene(None)
                from .vta_scene_session import warning
                raise VtaReviewUiError(warning(context) or 'Enter VTA Mode / Load VTA Scene first.')
            context = _context_for_main_scene(context)
            _activate_review_scene(context.scene)
            from .vta_scene_timing import owns_scene
            from .vta_scene_session import owned, source_bound
            scene = context.scene
            if not owned(scene):
                raise VtaReviewUiError('Load a metadata-owned VTA Scene / New VTA Scene.')
            if not source_bound(scene):
                _activate_review_scene(None)
                _status = 'Blank VTA Scene; choose a clip in Video Source.'
                _status_is_error = False
                return True
            info = validated_info or cached_scene_source(context)
            if force or info is None or (check_disk and _source_files_changed(context, info)):
                if memory_only:
                    return False
                # Draw does not perform initial validation. Observer/Load does.
                if not force and not check_disk and hasattr(context.workspace, 'blendcap_vta_loaded_scene') and info is None:
                    return False
                try:
                    info = validated_info or _timing_source(context)
                    pid, source, _source_location, _strip = info
                    if not owns_scene(scene, pid, source):
                        raise VtaReviewUiError('Active Scene ownership does not match the validated VTA source.')
                except Exception:
                    # Preserve only the old paths for cheap change observation;
                    # failed validation must not leave controls enabled by an
                    # old successful metadata entry.
                    entry = _validated_sources.get(pointer(scene))
                    if entry:
                        _validated_sources[pointer(scene)] = (None, entry[1], entry[2])
                    raise
                record_scene_source(scene, info)
        elif _active_review_scene is not None and isinstance(getattr(context, 'scene', None), bpy.types.Scene):
            _activate_review_scene(context.scene)
        if memory_only and _display_backend is None:
            return False
        backend = (_display_backend if memory_only or (not force and not check_disk and _display_backend is not None)
                   else _get_backend(context, force=force))
        _display_backend = backend
        location = at_frame(info, context.scene) if info else _location_from_context(context)
        capable = info is not None and callable(getattr(backend, 'frame_from_context', None))
        model_changed = False
        if capable:
            if memory_only and not _display.current(backend, location):
                return False
            if force or check_disk or not _display.current(backend, location):
                if memory_only:
                    return False
                model_changed = _display.observe(backend, location, info[1], force=force)
            project_signature = _display.stamp[0] if _display.stamp else None
            cache_signature = _display.stamp[2] if _display.stamp else None
        else:
            # Legacy host-independent test/adapters retain the original API.
            project_path = location.companion_root / "project_state.json"
            project_signature = file_signature(project_path)
            cache_path = (location.companion_root / "analysis" /
                          f"{_view.analysis_revision_id}.json") if _view is not None else None
            cache_signature = file_signature(cache_path) if cache_path else None
        key_changed = (
            _location is None
            or _location.companion_root != location.companion_root
            or _location.source_path != location.source_path
            or _location.source_frame != location.source_frame
            or _project_signature != project_signature
            or _cache_signature != cache_signature
            or _presentation_scene is not getattr(context, 'scene', None)
        )
        timeline_identity = (location.companion_root, location.source_path, project_signature, cache_signature)
        if force or model_changed or timeline_identity != _timeline_identity or _timeline is None:
            _timeline = _display.review_timeline(backend) if capable else backend.query_timeline(location)
            _timeline_identity = timeline_identity
            _review_key_selection.reconcile(_timeline)
        if force or model_changed or key_changed or _view is None:
            query = _display.frame if capable else lambda owner, loc, **kw: owner.query(loc, **kw)
            _view = query(backend, location, show_suppressed=getattr(
                context.window_manager, 'blendcap_vta_show_suppressed', False))
            _location = location
            _project_signature = project_signature
            _cache_signature = cache_signature
            _presentation_scene = getattr(context, 'scene', None)
            _selection.accept_review(_view)
            _repeat_character_id()  # deletion/project switch invalidates only session memory
            _rebuild_enum_items()
            # Query validates the source hash before presentation changes.
            # Queue only on changes: a timer redraw must not queue itself again.
            _queue_presentation(context, context.scene if info else _owning_vse_scene(context, location))
        if force:
            _status = "Review refreshed from canonical state."
            _status_is_error = False
        return True
    except Exception as exc:
        _view = None
        _location = None
        _project_signature = None
        _cache_signature = None
        _timeline = None
        _timeline_identity = None
        _review_key_selection.clear()
        _character_selections.clear()
        _repair_selections.clear()
        _selection.review_key = None
        _selection.observation_id = ""
        _rebuild_enum_items()
        _queue_presentation(context)
        _status = user_message(exc)
        _status_is_error = True
        return False


def _accept_mutated_review(context, refreshed) -> None:
    """Requery keys and frame review together before notifying every surface."""
    global _view
    _view = refreshed
    _selection.accept_review(refreshed)
    if not refresh(context, force=True):
        raise VtaReviewUiError(_status)
    vta_invalidation.request_redraw()


def candidate_items(_self, context):
    return _candidate_enum_items


def character_items(_self, context):
    return _character_enum_items


def _candidate_changed(self, _context):
    if _syncing_properties:
        return
    candidate = self.blendcap_vta_candidate
    if candidate == _NO_SELECTION:
        _selection.observation_id = ""
    elif _view is not None and _selection.select_observation(_view, candidate):
        character = _selection.character_id or _NO_SELECTION
        if self.blendcap_vta_character != character:
            self.blendcap_vta_character = character
    sync_character_to_timeline(_context)
    vta_invalidation.request_redraw()


def _character_changed(self, _context):
    if _syncing_properties:
        return
    _selection.character_id = (
        "" if self.blendcap_vta_character == _NO_SELECTION else self.blendcap_vta_character
    )
    sync_character_to_timeline(_context)
    vta_invalidation.request_redraw()


def _overlay_enabled(context) -> bool:
    if not _presentation_current(context):
        return False
    scene = getattr(context, "sequencer_scene", None) or getattr(context, "scene", None)
    return bool(
        scene is not None
        and role_for(getattr(context, 'workspace', None),
                     getattr(context, 'screen', None),
                     getattr(context, 'area', None)) == ROLE_PREVIEW
        and getattr(scene, "blendcap_show_vta_candidate_overlay", True)
    )


def select_overlay_candidate_at(context, point) -> bool:
    """Select a current visible hit without assigning or persisting anything."""
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    space = getattr(context, "space_data", None)
    if not vta_overlay.is_vse_preview_region(
        getattr(area, "type", None),
        getattr(region, "type", None),
        getattr(space, "view_type", None),
    ):
        return False
    if not _overlay_enabled(context):
        return False
    if not refresh(context) or _view is None:
        return False
    regions = vta_overlay.build_overlay_region_candidates(
        _view, _selection.observation_id, context.region.view2d
    )
    observation_id = _overlap_cycle.select(regions, point,
        (str(_location.companion_root), _view.source_id,
         _view.analysis_revision_id, _view.source_frame))
    if observation_id is None:
        return False
    selected = _selection.select_observation(_view, observation_id)
    if selected:
        try:
            _sync_properties(context)
        except (AttributeError, TypeError, ValueError):
            selected = False
    synchronized = bool(
        selected
        and _selection.observation_id == observation_id
        and getattr(context.window_manager, "blendcap_vta_candidate", None)
        == observation_id
    )
    return synchronized


class BLENDCAP_OT_vta_candidate_select(bpy.types.Operator):
    """Select one visible candidate while the VTA Preview tool is active."""
    bl_idname = "blendcap.vta_candidate_select"
    bl_label = "Select VTA Candidate"
    bl_description = "Select the VTA candidate under the pointer for inspection"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        space = getattr(context, "space_data", None)
        return (_overlay_enabled(context) and vta_overlay.is_vse_preview_region(
            getattr(area, "type", None),
            getattr(region, "type", None),
            getattr(space, "view_type", None),
        ))

    def invoke(self, context, event):
        if not BLENDCAP_OT_vta_candidate_select.poll(context):
            return {'PASS_THROUGH'}
        if getattr(event, 'value', None) == 'CLICK_DRAG':
            # This explicit review tool is read-only. Consume left drags so
            # Blender's Preview strip-transform gesture cannot move the image
            # (and its derived overlay) during ordinary candidate inspection.
            return {'FINISHED'}
        return (
            {'FINISHED'}
            if select_overlay_candidate_at(
                context, (event.mouse_region_x, event.mouse_region_y)
            )
            else {'PASS_THROUGH'}
        )


def _show_suppressed_changed(self, context):
    refresh(context, force=True)


def _feedback_key(operation, context=None):
    context = context or bpy.context
    scene = getattr(getattr(context, 'window', None), 'scene', getattr(context, 'scene', None))
    return pointer(scene), source_identity(scene, getattr(bpy.data, 'filepath', '')), operation


def clear_operation_feedback(operation, context=None):
    _operation_feedback.pop(_feedback_key(operation, context), None)


def set_operation_feedback(operation, primary, details, *, context=None, disposition='', log=None, error=True):
    context = context or bpy.context
    _operation_feedback[_feedback_key(operation, context)] = dict(primary=primary,details=details,
        disposition=disposition,log=log,error=error)
    wm = getattr(context, 'window_manager', None)
    field = 'blendcap_vta_' + operation + '_details'
    if wm is not None and hasattr(wm, field):
        setattr(wm, field, False)
    vta_invalidation.request_redraw()


def draw_operation_feedback(layout, context, operation):
    item = _operation_feedback.get(_feedback_key(operation, context))
    if item is None:
        return
    layout.label(text=item['primary'], icon='ERROR' if item['error'] else 'INFO')
    if item['disposition']:
        layout.label(text=item['disposition'])
    field = 'blendcap_vta_' + operation + '_details'
    expanded = getattr(context.window_manager, field, False)
    layout.prop(context.window_manager, field, text='Details', icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT', emboss=False)
    if expanded:
        for paragraph in item['details'].splitlines():
            for line in textwrap.wrap(paragraph, width=52, break_long_words=True):
                layout.label(text=line)
        row = layout.row()
        row.enabled = item['log'] is not None
        row.operator('blendcap.vta_generation_details', text='Go to Log', icon='FILE_FOLDER').operation = operation
        if item['log'] is None:
            layout.label(text='No log was created for this validation.')


def open_operation_log(path):
    """User command only; explicit argument vector, no shell or clipboard."""
    from ..blendcap_vta_generation_ipc import filesystem_path
    path = Path(path).resolve()
    if os.name == 'nt' and os.path.isfile(filesystem_path(path)):
        subprocess.Popen(['explorer.exe', '/select,' + str(path)])
    else:
        folder = path.parent
        if not folder.is_dir():
            raise ValueError('Log folder is unavailable: ' + str(folder))
        bpy.ops.wm.path_open(filepath=str(folder))


def _generation_failure(exc, *, cancelled=False, context=None, location=None, reconstruction_started=None):
    global _status, _status_is_error, _generation_status, _generation_details, _reconstruction_log
    from .vta_generation import failure_message
    diagnostic = str(exc)
    recovery = failure_message(diagnostic, cancelled).removeprefix('Couldn’t update the animation. Your existing animation was kept unchanged. ')
    details = recovery if recovery.startswith(diagnostic) else diagnostic + '\n' + recovery
    started = _generation_job is not None if reconstruction_started is None else reconstruction_started
    details += '\n' + ('Reconstruction started; prior animation was retained.' if started else 'Reconstruction did not start; prior animation was retained.')
    if _reconstruction_log is None and location is not None:
        try:
            from uuid import uuid4
            from ..blendcap_vta_generation_ipc import filesystem_path
            log = location.companion_root/'reconstruction'/('check-' + uuid4().hex[:16] + '.log')
            os.makedirs(filesystem_path(log.parent), exist_ok=True)
            with open(filesystem_path(log), 'x', encoding='utf-8') as handle:
                handle.write(details + '\n')
            _reconstruction_log = log
        except OSError:
            pass  # A diagnostic write cannot replace the actual validation failure.
    _generation_details = details + ('\nLog: ' + str(_reconstruction_log) if _reconstruction_log else '')
    _status = ('Update cancelled.' if cancelled else 'Couldn’t update the animation.') + ' Your existing animation was kept unchanged.'
    _status_is_error = not cancelled
    _generation_status = 'Update cancelled; existing animation unchanged.' if cancelled else 'Update failed; existing animation unchanged.'
    set_operation_feedback('animation', 'Update cancelled.' if cancelled else 'Couldn’t update the animation.',
        _generation_details, context=context, disposition='Your existing animation was kept unchanged.',
        log=_reconstruction_log,error=not cancelled)


def _generation_log_lines(*, complete=False):
    from ..blendcap_vta_generation_ipc import filesystem_path
    if _reconstruction_log is None or not os.path.isfile(filesystem_path(_reconstruction_log)):
        return []
    with open(filesystem_path(_reconstruction_log), encoding='utf-8', errors='replace') as handle:
        raw = handle.read()
    # The worker writes large READY JSON. A concurrent read may end mid-write;
    # consume only newline-terminated records, never cancel on a partial marker.
    return (raw if complete else raw[:raw.rfind('\n') + 1]).splitlines()


def _cancel_generation_request(job_id=None):
    job = _generation_job
    if job is None or job_id not in (None, job['id']):
        return False
    from ..blendcap_vta_generation_ipc import cancel_request
    cancel_request(job)
    return True


def _send_generation_decision(job_id, migration):
    job = _generation_job
    if job is None or job['id'] != job_id:
        raise VtaReviewUiError('This update is no longer waiting for confirmation. Retry Generate Animation Range.')
    from ..blendcap_vta_generation_ipc import publish_decision
    publish_decision(job, migration)


class BLENDCAP_OT_vta_update_animation(bpy.types.Operator):
    bl_idname = 'blendcap.vta_update_animation'
    bl_label = 'Existing animation needs to be updated'
    bl_description = 'Update existing animation safely, then complete the requested range'

    def invoke(self, context, event):
        if _generation_job is None or _generation_job.get('decided'):
            return {'CANCELLED'}
        self.job_id = _generation_job['id']
        return context.window_manager.invoke_props_dialog(self, width=480,
            confirm_text='Update Animation and Continue')

    def draw(self, context):
        layout = self.layout
        layout.label(text='Some animation was created with an older method or settings.')
        layout.label(text='VTA must update it before safely completing this range.')
        migration = _generation_job.get('required') if _generation_job else None
        if migration:
            offset = _generation_job['offset']
            layout.label(text=f'Update existing animation: {migration[0]+offset}–{migration[1]+offset}.')
        layout.label(text='Your existing animation stays unchanged unless all work succeeds.')

    def execute(self, context):
        global _generation_status
        # A delayed dialog from an old request cannot cancel or repaint a new one.
        if (_generation_job is None or _generation_job['id'] != getattr(self, 'job_id', None)
                or _generation_job.get('cancelled')):
            return {'CANCELLED'}
        try:
            if context.scene != _reconstruction_scene:
                raise VtaReviewUiError('The target Scene changed. Cancel and retry in the intended VTA Scene.')
            require_loaded_context(context)
            _send_generation_decision(self.job_id, _generation_job['required'])
            _generation_status = 'Updating existing animation, then the requested range…'
            return {'FINISHED'}
        except Exception as exc:
            _generation_job['error'] = str(exc)
            _cancel_generation_request(self.job_id)
            _generation_failure(exc)
            return {'CANCELLED'}

    def cancel(self, context):
        if (_generation_job and _generation_job['id'] == getattr(self, 'job_id', None)
                and not _generation_job.get('decided')):
            _cancel_generation_request(self.job_id)
            _generation_failure('User declined animation update', cancelled=True)


class BLENDCAP_OT_vta_generation_details(bpy.types.Operator):
    bl_idname = 'blendcap.vta_generation_details'
    bl_label = 'Go to Log'
    bl_description = 'Open the relevant log folder; highlight the log file on Windows'
    operation: bpy.props.EnumProperty(items=(('animation','Animation',''),('tracking','Tracking',''),('repair','Repair','')), default='animation')

    def execute(self, context):
        item = _operation_feedback.get(_feedback_key(getattr(self,'operation','animation'), context))
        path = item['log'] if item else None
        if path is None:
            return {'CANCELLED'}
        try:
            open_operation_log(path)
        except (OSError, ValueError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def _request_generation_confirmation():
    return bpy.ops.blendcap.vta_update_animation('INVOKE_DEFAULT')


def _reconstruction_timer():
    global _reconstruction_process, _status, _status_is_error, _generation_status
    global _generation_job
    process = _reconstruction_process
    if process is None:
        return None
    if process.poll() is None:
        if _reconstruction_log is not None:
            lines = _generation_log_lines()
            job = _generation_job
            ready = next((line[10:] for line in lines if line.startswith('VTA_READY=')), None)
            if job is not None and not job.get('ready') and not job.get('cancelled') and ready:
                job['ready'] = True
                try:
                    if bpy.context.scene != _reconstruction_scene:
                        raise VtaReviewUiError('Target Scene changed during generation. Retry in the intended VTA Scene.')
                    require_loaded_context(bpy.context)
                    validate_output_mapping(_reconstruction_location, _location_from_context(bpy.context, allow_outside=True))
                    payload = json.loads(ready)
                    if payload.get('generation_id') != job['id']:
                        raise VtaReviewUiError('Generation message belongs to a different request. Retry generation.')
                    if payload['plan'] != job['plan']:
                        raise VtaReviewUiError('Review changed during generation. Retry Generate Animation Range.')
                    from .vta_generation import migration_range
                    backend = _get_backend(bpy.context)
                    incoming = backend._core.reconstruction_fingerprint(payload['reconstruction'], job['plan']['source_video_sha256'])
                    required = migration_range(_reconstruction_scene, job['plan'], incoming, job['domain'], job['offset'])
                    job['required'] = required
                    if required is None:
                        raise VtaReviewUiError('Existing animation changed while preparing the update. Retry generation.')
                    else:
                        _generation_status = 'Existing animation needs to be updated. Confirm the update or cancel.'
                        result = _request_generation_confirmation()
                        if 'CANCELLED' in result:
                            raise VtaReviewUiError('Return to the target VTA Scene, then retry Generate Animation Range.')
                except Exception as exc:
                    job['error'] = str(exc)
                    _cancel_generation_request(job['id'])
                    _generation_failure(exc)
            if job and not job.get('cancelled') and ('VTA_CALIBRATION_READY=' + job['id']) in lines:
                _generation_status = f'Generating animation {_reconstruction_range}…'
            progress = next((line[13:] for line in reversed(lines) if line.startswith('VTA_PROGRESS=')), None)
            if progress and not (job and job.get('cancelled')):
                _generation_status = f'Animation {_reconstruction_range or "Whole Source"}: {progress}'
        vta_invalidation.request_redraw()
        return 0.5
    _reconstruction_process = None
    try:
        job = _generation_job
        lines = _generation_log_lines(complete=True)
        cancelled = bool(job and (job.get('cancelled') or ('VTA_CANCELLED=' + job['id']) in lines))
        cancelled = cancelled or bool(_reconstruction_stop and _reconstruction_stop.is_file())
        if cancelled:
            error = job.get('error') if job else None
            _generation_failure(error or 'User cancelled this generation', cancelled=not error)
            return None
        if process.returncode:
            lines = _generation_log_lines(complete=True)
            raise VtaReviewUiError(next((line for line in reversed(lines) if line.strip()), 'Reconstruction worker could not finish.'))
        lines = _generation_log_lines(complete=True)
        output = next(Path(line.split('=', 1)[1]) for line in reversed(lines)
                      if line.startswith('VTA_OUTPUT='))
        from .vta_skeletal_output import import_reviewed_output
        if _reconstruction_scene is None or _reconstruction_scene.name not in bpy.data.scenes:
            raise VtaReviewUiError('Animation target Scene changed; output remains on disk.')
        scene = _reconstruction_scene
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            validate_output_mapping(_reconstruction_location, _location_from_context(bpy.context, allow_outside=True))
            if _generation_job is None:
                rigs, report = import_reviewed_output(output, _reconstruction_location,
                                                     _get_backend(bpy.context), configure_views=False)
            else:
                from .vta_generation import import_generation
                paths = [Path(line.split('=', 1)[1]) for line in lines if line.startswith('VTA_OUTPUT=')]
                rigs, report = import_generation(paths, _reconstruction_location, _get_backend(bpy.context),
                                                migration=_generation_job.get('migration', False))
        unavailable = sum(s['state'] == 'unavailable' for c in report['characters'] for s in c['schedule'])
        _status = f'Generated {len(rigs)} Character armature(s). {unavailable} unavailable Character-frames.'
        _status_is_error = False
        _generation_status = f'Animation {_reconstruction_range or "Whole Source"}: complete.'
        clear_operation_feedback('animation')
        from .vta_workspace import snap_first_output
        if job:
            try:
                snap_first_output(bpy.context, scene, job.get('first_output', False), job.get('workspace'))
            except Exception:
                # Animation has already committed. A presentation convenience
                # must never falsely report transaction failure/unchanged output.
                _status += ' Use Camera View to show the new animation.'
    except Exception as exc:
        cancelled = _reconstruction_stop is not None and _reconstruction_stop.is_file()
        error = _generation_job.get('error') if _generation_job else None
        _generation_failure(error or exc, cancelled=cancelled and not error)
    finally:
        if _generation_job is not None:
            from ..blendcap_vta_generation_ipc import cleanup_request
            cleanup_request(_generation_job)
        _generation_job = None
        vta_invalidation.request_redraw()
    vta_invalidation.request_redraw()
    return None


class BLENDCAP_OT_vta_generate_animation(bpy.types.Operator):
    bl_idname = 'blendcap.vta_generate_animation'
    @classmethod
    def poll(cls, context):
        # The same operator exposes cooperative Cancel. Other actions still
        # fail the execute-time edit guard while a job holds the source lock.
        return ((_reconstruction_process is not None and _reconstruction_process.poll() is None)
                or _source_actions_available(context))
    bl_label = 'Generate Animation Range'
    bl_description = 'Explicitly reconstruct reviewed Character boxes; no identity decisions or tracking changes'

    action: bpy.props.EnumProperty(items=(('RANGE', 'Frame Range', ''), ('START_CURRENT', 'Start = Current Frame', ''),
        ('END_CURRENT', 'End = Current Frame', ''), ('CANCEL', 'Cancel Generation', 'Stop between model calls')), default='RANGE')

    def execute(self, context):
        global _reconstruction_process, _reconstruction_location, _reconstruction_log
        global _reconstruction_scene, _reconstruction_range, _generation_status
        global _reconstruction_stop
        global _generation_job, _generation_details
        global _status, _status_is_error
        if self.action == 'CANCEL':
            if _reconstruction_process is None or _reconstruction_process.poll() is not None:
                return {'CANCELLED'}
            try:
                if not _cancel_generation_request():
                    return {'CANCELLED'}
                _generation_status = f'Animation {_reconstruction_range or "Whole Source"}: stopping after current work…'
                return {'FINISHED'}
            except Exception as exc:
                self.report({'ERROR'}, user_message(exc))
                return {'CANCELLED'}
        if not _edit_available(self, quiet=True):
            _generation_failure('Another operation is running. Wait or cancel it before generating.', context=context)
            return {'CANCELLED'}
        if self.action in ('START_CURRENT', 'END_CURRENT'):
            name = 'blendcap_vta_animation_' + ('start' if self.action == 'START_CURRENT' else 'end')
            setattr(context.window_manager, name, context.scene.frame_current)
            return {'FINISHED'}
        # Settle a worker that finished just before its next timer tick, then
        # start with fresh request-scoped state (never inherit a previous Cancel).
        if _reconstruction_process is not None and _reconstruction_process.poll() is not None:
            _reconstruction_timer()
        _generation_job = None
        _reconstruction_log = _reconstruction_stop = None
        _generation_details = ''
        clear_operation_feedback('animation', context)
        try:
            location = _location_from_context(context)
            backend = _get_backend(context)
            project, cache, _revision = backend._load_context(location)
            from ..blendcap_vta_generation_ipc import preflight_generation_paths
            preflight_generation_paths(location.companion_root)
            from .vta_generation import plan_options
            start = context.window_manager.blendcap_vta_animation_start
            end = context.window_manager.blendcap_vta_animation_end
            offset = location.blender_frame - location.source_frame
            source_range = (start - offset, end - offset)
            domain = (cache.source.start_frame, cache.source.end_frame)
            options = plan_options(source_range, domain)
            if getattr(context.window_manager, 'blendcap_vta_generation_scope', 'ALL') == 'SELECTED':
                ids = tracking_character_ids(context)
                if not ids:
                    raise VtaReviewUiError('Select Character rows before selected-Character generation, or choose All Reviewed.')
                options['character_ids'] = ids
            plan = backend._core.build_skeletal_plan(project, cache, **options)
            backend._core.validate_skeletal_repairs(project, plan, frame_offset=offset)
            visible_range = f'{start}–{end}'
            bundle = Path(__file__).resolve().parents[1]
            python = Path(_python_exe())
            if not python.is_file() or not (bundle / 'checkpoints/sam-3d-body-dinov3/model.ckpt').is_file():
                raise VtaReviewUiError('Skeletal environment unavailable. Install BlendCap body models in Preferences.')
            # Generation never activates a Scene or repairs editor choices.
            require_loaded_context(context)
            location = _location_from_context(context)
            command = [str(python), str(bundle / 'blendcap_vta_reconstruct_worker.py'),
                       '--core-root', str(_backend_root), '--blendcap-root', str(bundle),
                       '--companion-root', str(location.companion_root), '--video', str(location.source_path)]
            mode = context.scene.blendcap_props.fov_mode
            if mode not in ('FIRST', 'EVERY'):
                raise VtaReviewUiError('Reviewed VTA animation supports Sample first frame or Sample every frame; manual focal entry is not supported.')
            command += ['--fov-mode', mode]
            if 'frame_range' in options:
                command += ['--start-frame', str(source_range[0]), '--end-frame', str(source_range[1])]
            for cid in options.get('character_ids', ()):
                command += ['--character-id', cid]
            from .vta_generation import owners, fingerprint
            from ..blendcap_vta_generation_ipc import filesystem_path, new_request
            previous = sorted({fingerprint(o) for o in owners(context.scene, plan['project_id'], plan['source_id'])})
            job = new_request(location.companion_root, decision_required=bool(previous))
            job.update(plan=plan, domain=domain, offset=offset,
                       workspace=getattr(context, 'workspace', None),
                       first_output=not any(o.type == 'ARMATURE' for o in owners(context.scene, plan['project_id'], plan['source_id'])))
            _reconstruction_log = job['log']
            os.makedirs(filesystem_path(_reconstruction_log.parent), exist_ok=True)
            _reconstruction_stop = job['stop']
            command += ['--stop-file', str(job['stop']), '--generation-id', job['id']]
            for value in previous:
                command += ['--previous-fingerprint', value]
            if job['decision'] is not None:
                command += ['--decision-file', str(job['decision'])]
            environment = os.environ.copy()
            environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1')
            with open(filesystem_path(_reconstruction_log), 'x', encoding='utf-8') as log:
                _reconstruction_process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, cwd=str(bundle), env=environment,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _reconstruction_location = location
            _reconstruction_scene = context.scene
            _reconstruction_range = visible_range
            job['process'] = _reconstruction_process
            _generation_job = job
            _generation_details = 'Log: ' + str(_reconstruction_log)
            if not bpy.app.timers.is_registered(_reconstruction_timer):
                bpy.app.timers.register(_reconstruction_timer, first_interval=0.5)
            _generation_status = f'Checking source calibration for animation {visible_range}…'
            _status, _status_is_error = _generation_status, False
            return {'FINISHED'}
        except Exception as exc:
            _generation_failure(exc, context=context, location=locals().get('location'), reconstruction_started=False)
            return {'CANCELLED'}


class BLENDCAP_OT_vta_suppress_candidate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_suppress_candidate'
    bl_label = 'Suppress Candidate'
    bl_description = 'Ignore this candidate during automatic continuation; retain raw evidence for Restore'

    @classmethod
    def description(cls, context, properties):
        return ('Restore this candidate for automatic continuation; raw evidence was retained'
                if properties.restore else cls.bl_description)

    restore: bpy.props.BoolProperty(default=False)

    def invoke(self, context, event):
        try:
            if not _edit_available(self):
                return {'CANCELLED'}
            if not refresh(context) or _location is None or not _selection.observation_id:
                raise VtaReviewUiError('Select a current candidate first.')
            self._location = _location
            self._request = _get_backend(context).prepare_suppression(
                _location, _selection.observation_id, suppressed=not self.restore)
            if self._request.assigned_character_ids:
                return context.window_manager.invoke_props_dialog(self, width=420)
            return self.execute(context)
        except (VtaReviewUiError, OSError) as exc:
            self.report({'ERROR'}, user_message(exc))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text='Candidate is assigned to a Character.')
        self.layout.label(text='Suppress it and clear that current assignment?')
        self.layout.label(text='The original key is retained in suppression history.')

    def execute(self, context):
        global _status, _status_is_error
        try:
            if not _edit_available(self):
                return {'CANCELLED'}
            if (not hasattr(self, '_request')
                    or _location_from_context(context) != self._location):
                raise VtaReviewUiError('Selection or source changed; refresh and retry.')
            _get_backend(context).apply_suppression(self._location, self._request, confirmed=True)
            refresh(context, force=True)
            _status = ('Candidate restored. Use Update Tracking explicitly.' if self.restore
                       else 'Candidate suppressed. Raw evidence retained.')
            _status_is_error = False
            return {'FINISHED'}
        except (VtaReviewUiError, OSError) as exc:
            self.report({'ERROR'}, user_message(exc))
            return {'CANCELLED'}


class BLENDCAP_WST_vta_candidate_select_preview(bpy.types.WorkSpaceTool):
    """Explicit candidate-selection tool for standalone VSE Preview."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_context_mode = 'PREVIEW'
    bl_idname = "blendcap.vta_candidate_select_preview"
    bl_label = "Select VTA Candidate"
    bl_description = "Select a visible VTA candidate for inspection"
    bl_icon = "ops.generic.select"
    bl_widget = None
    bl_keymap = (
        (BLENDCAP_OT_vta_candidate_select.bl_idname,
         {"type": 'LEFTMOUSE', "value": 'CLICK'}, None),
        (BLENDCAP_OT_vta_candidate_select.bl_idname,
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG'}, None),
    )


class BLENDCAP_WST_vta_candidate_select_combined(bpy.types.WorkSpaceTool):
    """Explicit candidate-selection tool for combined Sequencer & Preview."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_context_mode = 'SEQUENCER_PREVIEW'
    bl_idname = "blendcap.vta_candidate_select_combined"
    bl_label = "Select VTA Candidate"
    bl_description = "Select a visible VTA candidate for inspection"
    bl_icon = "ops.generic.select"
    bl_widget = None
    bl_keymap = (
        (BLENDCAP_OT_vta_candidate_select.bl_idname,
         {"type": 'LEFTMOUSE', "value": 'CLICK'}, None),
        (BLENDCAP_OT_vta_candidate_select.bl_idname,
         {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG'}, None),
    )


_registered_tools = []


def _register_preview_tools() -> None:
    if _registered_tools:
        return
    specifications = (
        (BLENDCAP_WST_vta_candidate_select_preview, {"builtin.select"}, True),
        (BLENDCAP_WST_vta_candidate_select_combined, {"builtin.select_box"}, False),
    )
    try:
        for tool, after, group in specifications:
            bpy.utils.register_tool(
                tool, after=after, separator=True, group=group
            )
            _registered_tools.append(tool)
    except Exception:
        _unregister_preview_tools()
        raise


def _unregister_preview_tools() -> None:
    for tool in reversed(_registered_tools):
        try:
            bpy.utils.unregister_tool(tool)
        except (RuntimeError, ValueError):
            pass
    _registered_tools.clear()


class BLENDCAP_OT_vta_review_refresh(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_refresh"
    bl_label = "Refresh VTA Review"
    bl_description = "Reload the current frame review from canonical VTA state"

    def execute(self, context):
        if refresh(context, force=True):
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        self.report({'ERROR'}, _status)
        return {'CANCELLED'}


class BLENDCAP_OT_vta_review_assign(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_assign"
    bl_label = "Assign"
    bl_description = "Assign the selected candidate; confirm if existing assignments must change"

    @classmethod
    def poll(cls, context):
        return (_presentation_current(context) and not _analysis_locked() and _view is not None
                and _selection.can_assign(_view) and _location is not None)

    def invoke(self, context, _event):
        global _view, _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        self._confirmed_request = None
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        intent = _selection.assignment_intent(_view)
        if intent is None:
            _status = "Select a candidate and a different Character to assign."
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        if not intent.requires_confirmation:
            return self.execute(context)
        try:
            request, current_view = _get_backend(context).prepare_reassignment(
                _location,
                observation_id=_selection.observation_id,
                character_id=_selection.character_id,
            )
            current_intent = _selection.assignment_intent(current_view)
            if current_intent is None or not current_intent.requires_confirmation:
                raise VtaReviewUiError(
                    "The assignment changed. Refresh and select again."
                )
            self._confirmed_request = request
            self._confirmation_location = _location
            self._confirmation_lines = confirmation_lines(current_intent)
            _view = current_view
            _rebuild_enum_items()
            _sync_properties(context)
            return context.window_manager.invoke_props_dialog(
                self, width=460, confirm_text="Reassign"
            )
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}

    def draw(self, _context):
        for line in getattr(self, "_confirmation_lines", ()):
            self.layout.label(text=line)

    def execute(self, context):
        global _view, _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        intent = None if _view is None else _selection.assignment_intent(_view)
        if intent is None or _location is None:
            _status = "Select a candidate and a different Character to assign."
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        observation_id = _selection.observation_id
        character_id = _selection.character_id
        try:
            confirmed_request = getattr(self, "_confirmed_request", None)
            if confirmed_request is None:
                if intent.requires_confirmation:
                    raise VtaReviewUiError("Use Assign in the panel to confirm this reassignment.")
                _receipt, refreshed = _get_backend(context).apply(
                    _location,
                    observation_id=observation_id,
                    character_id=character_id,
                )
            else:
                if (
                    _location != getattr(self, "_confirmation_location", None)
                    or confirmed_request.observation_id != observation_id
                    or confirmed_request.character_id != character_id
                ):
                    raise VtaReviewUiError("The review selection changed during confirmation.")
                _receipt, refreshed = _get_backend(context).apply_confirmed_reassignment(
                    _location, confirmed_request
                )
            _accept_mutated_review(context, refreshed)
            character = next(
                row.display_name for row in refreshed.characters
                if row.character_id == character_id
            )
            verb = "Reassigned" if confirmed_request is not None else "Assigned"
            _status = f"{verb} candidate to {character}."
            _status_is_error = False
            _remember_assignment(character_id)
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


class BLENDCAP_OT_vta_repeat_assignment(bpy.types.Operator):
    bl_idname = 'blendcap.vta_repeat_assignment'
    bl_label = 'Repeat Last Assignment'
    bl_description = 'Assign the current candidate to the last explicitly assigned Character and advance one frame'

    @classmethod
    def poll(cls, context):
        target = _repeat_character_id()
        if not _presentation_current(context) or target is None or _analysis_locked() or not _repeat_assignment_context(context):
            return False
        intent = SelectionState()
        intent.character_id = target
        intent.observation_id = _selection.observation_id
        return intent.can_assign(_view)

    def _prepare(self, context, *, executing=False):
        if not _edit_available(self):
            return False
        if not _repeat_assignment_context(context):
            self.report({'ERROR'}, 'Use Repeat Last Assignment in the VTA Preview.')
            return False
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return False
        target = _repeat_character_id()
        if target is None:
            self.report({'ERROR'}, 'Assign a candidate to a Character first in this session.')
            return False
        try:
            if getattr(self, '_repeat_location', None) is not None and (
                    self._repeat_location != _location or self._repeat_target != target):
                raise VtaReviewUiError('The repeat target or review frame changed. Select again.')
            # Do not overwrite a changed selection while a confirmation is open:
            # normal Assign must reject it using its existing request guards.
            if not (executing and getattr(self, '_confirmed_request', None) is not None):
                _selection.select_character(_view, target)
                _sync_properties(context)
            if not _selection.can_assign(_view):
                raise VtaReviewUiError('Select one valid candidate for Repeat Last Assignment.')
            scene = context.scene
            strip = _active_video_strip(scene.sequence_editor)
            if strip is None:
                raise VtaReviewUiError('Select the matching movie strip in the Video Sequencer.')
            _first, last = _get_backend(context).source_coverage(_location)
            final_frame = min(visible_frame(_location, last), _strip_visible_end(strip) - 1)
            if not _location.blender_frame <= final_frame:
                raise VtaReviewUiError('The current frame is outside the source review range.')
            self._repeat_target = target
            self._repeat_location = _location
            self._repeat_scene = scene
            self._repeat_next_frame = min(_location.blender_frame + 1, final_frame)
            return True
        except Exception as exc:
            self.report({'ERROR'}, user_message(exc))
            return False

    def invoke(self, context, event):
        if not self._prepare(context):
            return {'CANCELLED'}
        # Same preparation, confirmation dialog, persistence and stale guards.
        return BLENDCAP_OT_vta_review_assign.invoke(self, context, event)

    def draw(self, context):
        BLENDCAP_OT_vta_review_assign.draw(self, context)

    def execute(self, context):
        if not self._prepare(context, executing=True):
            return {'CANCELLED'}
        result = BLENDCAP_OT_vta_review_assign.execute(self, context)
        if result == {'FINISHED'} and self._repeat_next_frame != self._repeat_location.blender_frame:
            # Only after durable assignment success; no candidate is carried forward.
            _selection.observation_id = ''
            self._repeat_scene.frame_set(self._repeat_next_frame)
            if not refresh(context, force=True):
                self.report({'WARNING'}, 'Assignment saved; refresh VTA Review for the next frame.')
            else:
                _sync_properties(context)
        return result


class BLENDCAP_OT_vta_review_hard_state(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_hard_state"
    bl_label = "Mark Review State"
    bl_description = "Persist an explicit per-Character decision without assigning a candidate"

    decision_kind: bpy.props.EnumProperty(
        name="Decision",
        items=(
            ('occluded', "Occluded", "Present but not independently observable"),
            ('no-valid-candidate', "No Valid Candidate", "No trustworthy detector box"),
            ('not-visible', "Not Visible", "Character is not visible on this frame"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return (
            _presentation_current(context) and not _analysis_locked()
            and _view is not None
            and _location is not None
            and _selection.character_id in {item.character_id for item in _view.characters}
        )

    def invoke(self, context, _event):
        global _view, _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        self._confirmed_request = None
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        intent = _selection.hard_state_intent(_view, self.decision_kind)
        if intent is None:
            self.report({'INFO'}, "This Character already has that decision or is unavailable.")
            return {'CANCELLED'}
        try:
            request, current_view = _get_backend(context).prepare_hard_state(
                _location, character_id=_selection.character_id,
                state=self.decision_kind,
            )
            current_intent = _selection.hard_state_intent(current_view, self.decision_kind)
            if (
                current_intent is None
                or current_intent.requires_confirmation != request.requires_confirmation
            ):
                raise VtaReviewUiError("The review decision changed. Refresh and select again.")
            _view = current_view
            _rebuild_enum_items()
            _sync_properties(context)
            if not request.requires_confirmation:
                return self.execute(context)
            self._confirmed_request = request
            self._confirmation_location = _location
            self._confirmation_lines = hard_state_confirmation_lines(current_intent)
            return context.window_manager.invoke_props_dialog(
                self, width=460, confirm_text="Replace Decision"
            )
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}

    def draw(self, _context):
        for line in getattr(self, "_confirmation_lines", ()):
            self.layout.label(text=line)

    def execute(self, context):
        global _view, _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        intent = _selection.hard_state_intent(_view, self.decision_kind)
        if intent is None or _location is None:
            self.report({'INFO'}, "This Character already has that decision or is unavailable.")
            return {'CANCELLED'}
        try:
            request = getattr(self, "_confirmed_request", None)
            if request is None:
                request, _current_view = _get_backend(context).prepare_hard_state(
                    _location, character_id=_selection.character_id,
                    state=self.decision_kind,
                )
                if request.requires_confirmation:
                    raise VtaReviewUiError("Use the review button to confirm replacing existing truth.")
                confirmed = False
            else:
                if (
                    _location != getattr(self, "_confirmation_location", None)
                    or request.character_id != _selection.character_id
                    or request.state.value != self.decision_kind
                ):
                    raise VtaReviewUiError("The review selection changed during confirmation.")
                confirmed = True
            _receipt, refreshed = _get_backend(context).apply_hard_state(
                _location, request, confirmed=confirmed
            )
            _accept_mutated_review(context, refreshed)
            _status = f"Recorded {HARD_STATE_LABELS[self.decision_kind]} for the selected Character."
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


class BLENDCAP_OT_vta_review_create_character(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_create_character"
    bl_label = "Create Character"
    bl_description = "Create and persist a semantic Character in the VTA project"

    @classmethod
    def poll(cls, _context):
        return _presentation_current(_context) and not _analysis_locked() and (not _managed_context(_context) or _view is not None)

    def execute(self, context):
        global _view, _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        name = ((context.window_manager.blendcap_vta_new_character_name or "").strip()
                or next_generic_character_name(_view))
        if character_name_exists(_view, name):
            _status = (
                f"Character {name!r} already exists. Select it from the Character dropdown "
                "or enter a different name."
            )
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        try:
            character, refreshed = _get_backend(context).create_character(
                _location, display_name=name
            )
            _view = refreshed
            if not _selection.select_character(refreshed, character.character_id):
                raise VtaReviewUiError("The new Character is not available after reload.")
            context.window_manager.blendcap_vta_new_character_name = ""
            _accept_mutated_review(context, refreshed)
            _status = f"Created Character {character.display_name}. Select a candidate and Assign it separately."
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


def select_timeline_character(context, character_id: str, *, extend=False) -> bool:
    """Share Current Character across Preview, sidebar, VSE, and VTA timeline."""
    if _timeline is None or character_id not in {
        item.character_id for item in _timeline.characters
    }:
        return False
    selection = timeline_character_selection(context)
    if selection is not None:
        if not selection.click(_timeline, character_id, extend=extend):
            return False
        _selection.character_id = selection.active_id or ''
    else:
        _selection.character_id = character_id
    clear_repair_selection(context)
    _sync_properties(context)
    return True


def select_timeline_characters(context, character_ids, *, extend=False) -> bool:
    selection = timeline_character_selection(context)
    if selection is None or not selection.select_box(_timeline, character_ids, extend=extend):
        return False
    _selection.character_id = selection.active_id or ''
    clear_repair_selection(context)
    _sync_properties(context)
    return True


def select_timeline_key(context, key_id: str, *, extend: bool = False,
                        activate: bool = False) -> bool:
    """Select a durable key and Character without changing the current frame."""
    if _timeline is None or _location is None:
        return False
    selected = (_review_key_selection.activate(_timeline, key_id) if activate
                else _review_key_selection.click(_timeline, key_id, extend=extend))
    if not selected:
        return False
    clear_repair_selection(context)
    key = key_at(_timeline, _review_key_selection.active_keyframe_id)
    if key is None:
        _sync_properties(context)
        return True
    selection = timeline_character_selection(context)
    if selection is not None:
        selection.select_box(_timeline, (key.character_id,), extend=extend or activate,
                             active_id=key.character_id)
    _selection.character_id = key.character_id
    _sync_properties(context)
    return True


def select_timeline_keys(context, key_ids, *, extend: bool = False) -> bool:
    """Apply a transient marquee selection without changing authority or time."""
    if _timeline is None or _location is None:
        return False
    if not _review_key_selection.select_box(_timeline, key_ids, extend=extend):
        return False
    clear_repair_selection(context)
    key = key_at(_timeline, _review_key_selection.active_keyframe_id)
    selection = timeline_character_selection(context)
    if selection is not None:
        characters = tuple(item.character_id for item in _timeline.keys
                           if item.keyframe_id in _review_key_selection.keyframe_ids)
        selection.select_box(_timeline, characters, extend=extend,
                             active_id=key.character_id if key else None)
    if key is not None:
        _selection.character_id = key.character_id
    _sync_properties(context)
    return True


def go_to_selected_timeline_key(context) -> bool:
    """Explicit time navigation, separate from ordinary key selection."""
    if _timeline is None or _location is None:
        return False
    key = key_at(_timeline, _review_key_selection.active_keyframe_id)
    if key is None:
        return False
    scene = _owning_vse_scene(context, _location)
    target_frame = key.source_frame + _location.blender_frame - _location.source_frame
    scene.frame_set(target_frame)
    active_scene = getattr(context, "scene", None)
    if active_scene is not None and active_scene is not scene:
        active_scene.frame_set(target_frame)
    refresh(context, force=True)
    _sync_properties(context)
    return True


def select_review_key_at(context, point, *, extend: bool = False,
                         activate: bool = False) -> bool:
    """Select one projected VSE key by durable identity without navigation."""
    if _timeline is None or _location is None:
        return False
    region = getattr(context, "region", None)
    if region is None:
        return False
    key_id = vta_timeline_overlay.hit_key(
        _timeline, region.view2d, region.height, point,
        _location.blender_frame - _location.source_frame,
        _lane_scroll(),
        vta_timeline_overlay.occupied_strip_channels(context),
    )
    return (select_timeline_key(context, key_id, extend=extend, activate=activate)
            if key_id is not None else False)


def _open_native_sequencer_context_menu():
    bpy.ops.wm.call_menu('INVOKE_DEFAULT', name="SEQUENCER_MT_context_menu")


class BLENDCAP_OT_vta_review_key_select(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_key_select"
    bl_label = "Select VTA Review Key"
    bl_description = "Select one authoritative VTA key without changing the frame"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (_timeline is not None
                and role_for(getattr(context, 'workspace', None),
                             getattr(context, 'screen', None),
                             getattr(context, 'area', None)) == ROLE_PREVIEW
                and vta_timeline_overlay.is_timeline_region(
            getattr(context, "area", None), getattr(context, "region", None),
            getattr(context, "space_data", None),
        ))

    def invoke(self, context, event):
        if event.type == 'RIGHTMOUSE':
            if not select_review_key_at(
                context, (event.mouse_region_x, event.mouse_region_y), activate=True
            ):
                return {'PASS_THROUGH'}
            # Open Blender's existing context menu; never replace strip-context
            # behavior on empty space or outside VTA Preview mode.
            try:
                _open_native_sequencer_context_menu()
            except RuntimeError:
                # If Blender cannot open its menu here, let its ordinary
                # right-click binding handle the same event.
                return {'PASS_THROUGH'}
            return {'FINISHED'}
        if select_review_key_at(
            context, (event.mouse_region_x, event.mouse_region_y), extend=bool(event.ctrl)
        ):
            return {'FINISHED'}
        if not event.ctrl:
            _review_key_selection.clear()
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


_review_context_keymap = None
_repeat_assignment_keymap = None


def _register_repeat_assignment_keymap():
    global _repeat_assignment_keymap
    config = getattr(bpy.context.window_manager.keyconfigs, 'addon', None)
    if config is not None and _repeat_assignment_keymap is None:
        # Blender's generic VSE map is dispatched in Preview and combined view.
        keymap = config.keymaps.new(name='Video Sequence Editor',
                                   space_type='SEQUENCE_EDITOR', region_type='WINDOW')
        item = keymap.keymap_items.new(BLENDCAP_OT_vta_repeat_assignment.bl_idname,
                                      'J', 'PRESS', repeat=False)
        _repeat_assignment_keymap = (keymap, item)


def _unregister_repeat_assignment_keymap():
    global _repeat_assignment_keymap
    if _repeat_assignment_keymap is not None:
        keymap, item = _repeat_assignment_keymap
        keymap.keymap_items.remove(item)
        _repeat_assignment_keymap = None


def _register_review_context_keymap():
    """Hit-test VTA keys in Sequencer WINDOW; misses pass to Blender unchanged.

    This is independent of the active WorkSpaceTool. Native strip selection and
    empty-space behavior remain available via the operator's PASS_THROUGH path.
    """
    global _review_context_keymap
    keyconfig = getattr(bpy.context.window_manager.keyconfigs, "addon", None)
    if keyconfig is None or _review_context_keymap is not None:
        return
    keymap = keyconfig.keymaps.new(
        name='Sequencer', space_type='SEQUENCE_EDITOR', region_type='WINDOW'
    )
    items = tuple(
        keymap.keymap_items.new(
            BLENDCAP_OT_vta_review_key_select.bl_idname, button, 'PRESS', **modifiers
        ) for button, modifiers in (
            ('LEFTMOUSE', {}), ('LEFTMOUSE', {'ctrl': True}), ('RIGHTMOUSE', {}),
        )
    )
    _review_context_keymap = (keymap, items)


def _unregister_review_context_keymap():
    global _review_context_keymap
    if _review_context_keymap is not None:
        keymap, items = _review_context_keymap
        for item in items:
            keymap.keymap_items.remove(item)
        _review_context_keymap = None


_MANAGEMENT_ACTIONS = (
    ("clear-assignment", "Clear Assignment", "Remove this Character's current candidate assignment"),
    ("clear-review-key", "Clear Review Key", "Remove one selected authoritative review key"),
    ("clear-selected-review-keys", "Clear Selected Review Keys", "Remove selected VTA keys across frames"),
    ("clear-all-review-keys-on-frame", "Clear All Review Keys on Frame", "Remove all VTA keys on this frame"),
    ("rename-character", "Rename Character", "Change display name, keeping the stable Character ID"),
    ("delete-character", "Delete Character", "Remove the Character and all associated project data"),
)


class BLENDCAP_OT_vta_review_manage(bpy.types.Operator):
    bl_idname = "blendcap.vta_review_manage"
    bl_label = "Manage VTA Review"
    bl_description = "Change authoritative VTA review keys or a Character"

    action: bpy.props.EnumProperty(name="Action", items=_MANAGEMENT_ACTIONS)
    new_name: bpy.props.StringProperty(name="Character Name")

    @classmethod
    def poll(cls, _context):
        return _presentation_current(_context) and not _analysis_locked() and _view is not None and _location is not None

    def _arguments(self):
        if self.action == "clear-assignment":
            return dict(character_id=_selection.character_id,
                        source_id=_view.source_id, frame=_view.source_frame)
        if self.action == "clear-review-key":
            ids = _review_key_selection.keyframe_ids
            if len(ids) != 1:
                raise VtaReviewUiError("Select exactly one VTA review key to clear.")
            return dict(keyframe_ids=ids)
        if self.action == "clear-selected-review-keys":
            return dict(keyframe_ids=_review_key_selection.keyframe_ids)
        if self.action == "clear-all-review-keys-on-frame":
            return dict(source_id=_view.source_id, frame=_view.source_frame)
        if self.action == "rename-character":
            return dict(character_id=_selection.character_id, display_name=self.new_name)
        return dict(character_id=_selection.character_id)

    def invoke(self, context, _event):
        global _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        self._pending_request = None
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        try:
            _use_timeline_active(context)
            if self.action == "rename-character":
                row = next((item for item in _view.characters
                            if item.character_id == _selection.character_id), None)
                if row is None:
                    raise VtaReviewUiError("Select a Character to rename.")
                self.new_name = row.display_name
            request = _get_backend(context).prepare_management(
                _location, self.action, **self._arguments(),
            )
            self._pending_request = request
            self._pending_location = _location
            character = next((item for item in _view.characters
                              if item.character_id == _selection.character_id), None)
            self._pending_character_name = None if character is None else character.display_name
            assigned = next((item for item in _view.candidates
                             if character is not None
                             and item.observation_id == character.occupied_observation_id), None)
            self._pending_assignment_label = None if assigned is None else f"Candidate {assigned.position}"
            return context.window_manager.invoke_props_dialog(
                self, width=480,
                confirm_text="Rename" if self.action == "rename-character" else "Confirm Removal",
            )
        except Exception as exc:
            _status = user_message(exc); _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}

    def draw(self, _context):
        if self.action == "rename-character":
            self.layout.prop(self, "new_name")
        elif self.action == "delete-character":
            name = getattr(self, "_pending_character_name", "this Character")
            self.layout.label(text=f"Delete {name} and ALL associated project information?")
            self.layout.label(text="This includes every review key and derived Character data.")
        elif self.action == "clear-assignment":
            name = getattr(self, "_pending_character_name", "selected Character")
            candidate = getattr(self, "_pending_assignment_label", "candidate")
            self.layout.label(text=f"Remove {name}'s assignment to {candidate} on this frame?")
        else:
            request = getattr(self, "_pending_request", None)
            count = 0 if request is None else request.affected_count
            self.layout.label(text=f"Remove {count} authoritative VTA review key(s)?")
            self.layout.label(text="The result is No Decision; Characters remain.")

    def execute(self, context):
        global _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        request = getattr(self, "_pending_request", None)
        if request is None or getattr(self, "_pending_location", None) is None:
            self.report({'ERROR'}, "Use the confirmation dialog for review management.")
            return {'CANCELLED'}
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        try:
            if self.action == "rename-character":
                updated = _get_backend(context).prepare_management(
                    self._pending_location, self.action,
                    character_id=request.character_id, display_name=self.new_name,
                )
                if updated.project_sha256 != request.project_sha256:
                    raise VtaReviewUiError("Project changed while the rename dialog was open.")
                request = updated
            if _location != self._pending_location:
                raise VtaReviewUiError("The VTA review location changed during confirmation.")
            _use_timeline_active(context)
            if self.action in {"clear-assignment", "rename-character", "delete-character"}:
                if _selection.character_id != request.character_id:
                    raise VtaReviewUiError("The selected Character changed during confirmation.")
            if self.action in {"clear-review-key", "clear-selected-review-keys"}:
                if _review_key_selection.keyframe_ids != request.keyframe_ids:
                    raise VtaReviewUiError("The selected review keys changed during confirmation.")
            _get_backend(context).apply_management(
                self._pending_location, request, confirmed=True,
            )
            refresh(context, force=True)
            _status = f"VTA review action completed: {self.action}."
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc); _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


def _action_availability(context=None):
    """Sequencer review availability shared by panel, header, and context."""
    character = next((item for item in _view.characters
                      if item.character_id == current_character_id(context)), None) if _view else None
    selected_keys = _review_key_selection.keyframe_ids
    frame_has_key = bool(_view and _timeline and any(
        key.source_frame == _view.source_frame for key in _timeline.keys
    ))
    if _analysis_locked() or not _presentation_current(context or bpy.context):
        return {action: False for action, _label, _description in _MANAGEMENT_ACTIONS} | {
            "update-tracking": False,
        }
    return {
        "clear-assignment": bool(character and character.occupied_observation_id),
        "clear-review-key": len(selected_keys) == 1,
        "clear-selected-review-keys": bool(selected_keys),
        "clear-all-review-keys-on-frame": frame_has_key,
        "rename-character": character is not None,
        "delete-character": character is not None,
        "update-tracking": bool(tracking_character_ids(context)) and _view is not None and _location is not None,
    }


def _draw_management_actions(layout, *, actions=None, availability=None):
    availability = _action_availability() if availability is None else availability
    for action, label, _description in _MANAGEMENT_ACTIONS:
        if actions is not None and action not in actions:
            continue
        row = layout.row()
        row.enabled = availability[action]
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("blendcap.vta_review_manage", text=label).action = action


class BLENDCAP_MT_vta_commands(bpy.types.Menu):
    bl_label = "VTA Commands"
    bl_idname = "BLENDCAP_MT_vta_commands"

    def draw(self, context):
        layout = self.layout
        layout.operator('blendcap.vta_snap_source_camera', text='Snap to Source Camera', icon='CAMERA_DATA')
        draw_scene_timing(layout, context, menu=True)
        analyze = layout.row()
        analyze.enabled = _source_actions_available(context)
        analyze.operator("blendcap.vta_analyze", text="Analyze Candidates")
        animate = layout.row()
        animate.enabled = _source_actions_available(context)
        animate.operator('blendcap.vta_generate_animation', text='Generate Animation Range').action = 'RANGE'
        available = refresh(context)
        repeat = layout.row()
        repeat.enabled = available and BLENDCAP_OT_vta_repeat_assignment.poll(context)
        repeat.operator_context = 'INVOKE_DEFAULT'
        repeat.operator('blendcap.vta_repeat_assignment', text='Repeat Last Assignment')
        if not available:
            layout.label(text="VTA review unavailable", icon='INFO')
            return
        availability = _action_availability()
        layout.separator()
        layout.label(text="Review Keys")
        _draw_management_actions(layout, actions={
            "clear-assignment", "clear-review-key", "clear-selected-review-keys",
            "clear-all-review-keys-on-frame",
        }, availability=availability)
        layout.separator()
        layout.label(text="Character")
        _draw_management_actions(layout, actions={
            "rename-character", "delete-character",
        }, availability=availability)
        layout.separator()
        tracking = layout.row()
        tracking.enabled = availability["update-tracking"]
        tracking.operator("blendcap.vta_update_tracking", text="Update Tracking")


def _draw_vta_header(self, context):
    """Add one Sequencer-scoped menu beside Blender's native editor menus."""
    if getattr(getattr(context, "space_data", None), "view_type", None) not in {
        'PREVIEW', 'SEQUENCER', 'SEQUENCER_PREVIEW'
    }:
        return
    if role_for(getattr(context, 'workspace', None),
                getattr(context, 'screen', None),
                getattr(context, 'area', None)) != ROLE_PREVIEW:
        return
    self.layout.separator()
    self.layout.menu(BLENDCAP_MT_vta_commands.bl_idname, text="VTA Commands")


def _draw_vta_context(self, context):
    """Add the same submenu to Blender's normal Sequencer context menu."""
    if getattr(getattr(context, "space_data", None), "view_type", None) not in {
        'PREVIEW', 'SEQUENCER', 'SEQUENCER_PREVIEW'
    }:
        return
    if role_for(getattr(context, 'workspace', None),
                getattr(context, 'screen', None),
                getattr(context, 'area', None)) != ROLE_PREVIEW:
        return
    self.layout.separator()
    self.layout.menu(BLENDCAP_MT_vta_commands.bl_idname, text="VTA Commands")


def _set_menu_hook(menu, callback):
    """Replace only our own stale/reloaded callback; retain Blender's hooks."""
    for item in tuple(getattr(menu.draw, "_draw_funcs", ())):
        if (getattr(item, "__name__", None) == callback.__name__
                and getattr(item, "__module__", "").endswith(".vta_review_ui")):
            menu.remove(item)
    menu.append(callback)


def _remove_menu_hook(menu, callback):
    for item in tuple(getattr(menu.draw, "_draw_funcs", ())):
        if (getattr(item, "__name__", None) == callback.__name__
                and getattr(item, "__module__", "").endswith(".vta_review_ui")):
            menu.remove(item)


def _remove_legacy_vta_menu_hook(menu):
    for item in tuple(getattr(menu.draw, "_draw_funcs", ())):
        if (getattr(item, "__name__", None) == "_draw_vta_menu"
                and getattr(item, "__module__", "").endswith(".vta_review_ui")):
            menu.remove(item)


class BLENDCAP_OT_vta_motion_trust(bpy.types.Operator):
    bl_idname = "blendcap.vta_motion_trust"
    bl_label = "Edit Repair Range"
    bl_description = "Reject generated animation on the selected inclusive frames; identity and tracking stay independent"
    action: bpy.props.EnumProperty(items=(
        ('FREEZE', 'Freeze', 'Use the most recent trusted pose for rejected frames until normal generated animation resumes'),
        ('INTERPOLATE', 'Interpolate', 'Repair selected frames; valid unrepaired anchors must be outside both endpoints'),
        ('RESIZE', 'Apply Start / End', 'Resize the selected repair in place; do not merge overlapping repairs'),
        ('CHANGE_MODE', 'Change Mode', 'Change the selected exact range mode'),
        ('REMOVE', 'Remove Selected Range', 'Allow normal generation throughout the selected range'),
        ('CLEAR', 'Clear Frame Range', 'Subtract inclusive frames from repair ranges'),
        ('START_CURRENT', 'Set Start to Current Frame', ''),
        ('END_CURRENT', 'Set End to Current Frame', '')), default='FREEZE')

    @classmethod
    def description(cls, context, properties):
        return {'FREEZE': 'Use the most recent trusted pose for rejected frames until normal generated animation resumes',
                'INTERPOLATE': 'Repair selected frames; valid unrepaired anchors must be outside the repair range',
                'RESIZE': 'Apply edited Start / End to the selected repair without changing its type'}.get(properties.action, cls.bl_description)

    def execute(self, context):
        global _status, _status_is_error
        clear_operation_feedback('repair', context)
        if not _edit_available(self, quiet=True):
            set_operation_feedback('repair','Couldn’t edit the repair.', 'Another operation is running. Wait or cancel it before editing repairs.',
                                   context=context,disposition='Existing repair ranges were kept unchanged.')
            return {'CANCELLED'}
        try:
            if not _is_timeline_context(context):
                raise VtaReviewUiError("Use repair ranges in the VTA Dope Sheet.")
            location = _location_from_context(context)
            wm = context.window_manager
            if self.action in {'START_CURRENT', 'END_CURRENT'}:
                field = ('blendcap_vta_motion_start' if self.action == 'START_CURRENT'
                         else 'blendcap_vta_motion_end')
                setattr(wm, field, location.blender_frame)
                return {'FINISHED'}
            if self.action not in {'FREEZE', 'INTERPOLATE', 'CLEAR', 'CHANGE_MODE', 'REMOVE', 'RESIZE'}:
                raise VtaReviewUiError("Unknown repair action.")
            selection = timeline_character_selection(context)
            if selection is None or not selection.character_ids:
                raise VtaReviewUiError("Select Character rows in the VTA Dope Sheet before editing repair ranges.")
            targets = selection.character_ids
            selected_range = selected_repair_range(context)
            if self.action in {'CHANGE_MODE', 'REMOVE', 'RESIZE'} and selected_range is None:
                raise VtaReviewUiError('Select a repair range first.')
            if not refresh(context, force=True):
                raise VtaReviewUiError(_status)
            selection = timeline_character_selection(context)
            if selection is None or selection.character_ids != targets or _location != location:
                raise VtaReviewUiError("The repair selection or source changed; select the rows again.")
            start, end = wm.blendcap_vta_motion_start, wm.blendcap_vta_motion_end
            editing_selected = selected_range is not None and self.action in {'CHANGE_MODE', 'REMOVE', 'RESIZE', 'FREEZE', 'INTERPOLATE'}
            if editing_selected:
                if selected_repair_range(context) != selected_range:
                    raise VtaReviewUiError('The selected repair range changed; select it again.')
                targets = (selected_range.character_id,)
                if self.action != 'RESIZE':
                    start, end = (visible_frame(location, f) for f in (selected_range.start_frame, selected_range.end_frame))
            action = ('resize' if self.action == 'RESIZE' else 'clear' if self.action in {'CLEAR', 'REMOVE'}
                      else 'change-mode' if editing_selected else 'mark')
            mode = (wm.blendcap_vta_repair_mode if self.action == 'CHANGE_MODE'
                    else selected_range.mode if self.action == 'RESIZE'
                    else 'freeze' if self.action == 'FREEZE' else 'interpolate' if self.action == 'INTERPOLATE' else None)
            _get_backend(context).set_motion_trust_for_characters(
                location, targets, start, end, action=action, mode=mode,
                **({'previous_range': (selected_range.start_frame, selected_range.end_frame)} if action == 'resize' else {}))
            if not refresh(context, force=True):
                set_operation_feedback('repair','Repair saved; refresh unavailable.',
                    'Refresh review to display the saved repair.', context=context)
                return {'FINISHED'}
            if editing_selected and self.action != 'REMOVE':
                new_range = next((i for i in _timeline.repair_ranges if i.character_id == selected_range.character_id
                                 and visible_frame(location, i.start_frame) == start
                                 and visible_frame(location, i.end_frame) == end and i.mode == mode), None)
                if new_range is not None:
                    select_repair_range(context, new_range)
            vta_invalidation.request_redraw()
            _status = 'Repair ranges saved. Generate Animation to recompute repair; identity and tracking retained.'
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status, _status_is_error = user_message(exc), True
            set_operation_feedback('repair','Couldn’t edit the repair.',_status,context=context,
                                   disposition='Existing repair ranges were kept unchanged.')
            return {'CANCELLED'}


def draw_cleanup_commands(layout, context, *, menu=False):
    """The menu and N-panel share operators and target availability."""
    selection = timeline_character_selection(context)
    available = _is_timeline_context(context) and _location is not None and not _analysis_locked()
    for action, label in (
            ('START_CURRENT', 'Set Start to Current Frame' if menu else 'Start = Current Frame'),
            ('END_CURRENT', 'Set End to Current Frame' if menu else 'End = Current Frame'),
            ('FREEZE', 'Freeze'), ('INTERPOLATE', 'Interpolate'), ('CLEAR', 'Clear Frame Range'),
            ('RESIZE', 'Apply Start / End'), ('REMOVE', 'Remove Selected Range')):
        row = layout.row()
        row.enabled = available and (action.endswith('CURRENT') or
                                    bool(selection and selection.character_ids))
        interval = selected_repair_range(context)
        if action in {'RESIZE', 'REMOVE'}:
            row.enabled = available and selected_repair_range(context) is not None
        if interval is not None and action in {'FREEZE', 'INTERPOLATE'}:
            row.enabled = row.enabled and interval.mode != action.lower()
        row.operator('blendcap.vta_motion_trust', text=label).action = action


def draw_motion_trust(layout, context):
    """Explicit pose rejection, independent of identity and tracking."""
    layout.label(text='Repair Animation')
    interval = selected_repair_range(context)
    if interval is not None:
        layout.label(text=f'Selected repair: {interval.mode.title()}, frames {visible_frame(_location, interval.start_frame)}–{visible_frame(_location, interval.end_frame)}')
    else:
        layout.label(text='No repair selected: buttons create a repair.')
    wm = context.window_manager
    controls = layout.column()
    controls.enabled = _location is not None and not _analysis_locked()
    row = controls.row(align=True)
    row.prop(wm, 'blendcap_vta_motion_start', text='Start Frame')
    row.prop(wm, 'blendcap_vta_motion_end', text='End Frame')
    draw_cleanup_commands(layout, context)
    layout.label(text='Interpolate anchors must be outside the repair range.')
    draw_operation_feedback(layout, context, 'repair')
    layout.label(text='Clear allows normal generation; regeneration retains rejection')
    active = current_character_id(context)
    selected = next((c for c in _view.characters if c.character_id == active), None) if _view else None
    if selected is not None and selected.pose_rejected:
        layout.label(text='Generated Animation Rejected', icon='INFO')
    if _timeline and _location:
        for interval in _timeline.repair_ranges:
            if interval.character_id == active:
                layout.label(text=f'{interval.mode.title()}: {visible_frame(_location, interval.start_frame)}–{visible_frame(_location, interval.end_frame)}')


class BLENDCAP_OT_vta_update_tracking(bpy.types.Operator):
    """Explicitly derive continuity from cached candidates within local hard keys."""

    bl_idname = "blendcap.vta_update_tracking"
    bl_label = "Update Tracking"
    bl_description = "Update independent tracking for every selected Character near this frame"

    @classmethod
    def poll(cls, context):
        return bool(_presentation_current(context) and _view is not None and _location is not None and
                    not _analysis_locked() and tracking_character_ids(context))

    def execute(self, context):
        global _status, _status_is_error
        clear_operation_feedback('tracking', context)
        if not _edit_available(self, quiet=True):
            set_operation_feedback('tracking','Couldn’t update tracking.','Another operation is running. Wait or cancel it before tracking.',
                                   context=context,disposition='Existing tracking was kept unchanged.')
            return {'CANCELLED'}
        try:
            location = _location_from_context(context)
            targets = tracking_character_ids(context)
            if not targets:
                raise VtaReviewUiError("Select Character rows before Update Tracking.")
            if not refresh(context, force=True):
                raise VtaReviewUiError("Refresh VTA Review before Update Tracking.")
            if tracking_character_ids(context) != targets or _location != location:
                raise VtaReviewUiError("Review selection changed; retry Update Tracking.")
            outcomes, _review, _timeline_result = _get_backend(context).update_tracking_for_characters(
                location, targets
            )
            if not refresh(context, force=True):
                _status = f"Tracking saved, but review refresh failed: {_status}"
                _status_is_error = True
                set_operation_feedback('tracking','Tracking saved; refresh unavailable.',_status,context=context)
                return {'FINISHED'}
            names = {c.character_id: c.display_name for c in _view.characters}
            _status = '\n'.join(f'{names.get(item.character_id, item.character_id)}: ' +
                              (tracking_update_success(item.update, frame_offset=location.blender_frame - location.source_frame)
                               if item.update is not None else 'Not updated — ' + tracking_update_error(VtaReviewUiError(item.error)))
                              for item in outcomes)
            _status_is_error = any(item.update is None for item in outcomes)
            set_operation_feedback('tracking','Tracking incomplete.' if _status_is_error else 'Tracking updated.',_status,
                                   context=context,error=_status_is_error)
            return {'FINISHED'} if any(item.update is not None for item in outcomes) else {'CANCELLED'}
        except Exception as exc:
            if not isinstance(exc, VtaReviewUiError):
                traceback.print_exc()
            _status = tracking_update_error(exc)
            _status_is_error = True
            set_operation_feedback('tracking','Couldn’t update tracking.',_status,context=context,
                                   disposition='Existing tracking was kept unchanged.')
            return {'CANCELLED'}


class VIEW3D_PT_blendcap_vta_review(bpy.types.Panel):
    bl_label = "VTA Review"
    bl_idname = "VIEW3D_PT_blendcap_vta_review"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlendCap'
    bl_parent_id = "VIEW3D_PT_blendcap_ui"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        refresh(context)
        layout = self.layout
        layout.operator('blendcap.open_vta_workspace', text='Open VTA Workspace',
                        icon='WORKSPACE')
        draw_scene_workflow(layout, context)
        layout.operator('blendcap.vta_snap_source_camera', text='Snap to Source Camera', icon='CAMERA_DATA')
        draw_scene_timing(layout, context)
        layout.operator('blendcap.vta_camera_depth', text='Move Along Camera Depth')
        from .vta_workspace import blank_animation_guidance
        for line in blank_animation_guidance(context):
            layout.label(text=line, icon='INFO' if line.startswith('No ') else 'NONE')
        if _location is not None:
            layout.label(text=f"Blender frame {_location.blender_frame}")
        if _view is None:
            scoped_error = any(key[0] == _feedback_key('animation', context)[0] for key in _operation_feedback)
            layout.label(text="Review unavailable" if scoped_error else (_status or "Review unavailable"), icon='ERROR')
            layout.operator("blendcap.vta_review_refresh", icon='FILE_REFRESH')
        else:
            if _view.state == 'analyzed-empty':
                layout.label(text="Current frame: Analyzed, 0 candidates")
            elif _view.state == 'analyzed-with-candidates':
                layout.label(text=f"Current frame: Analyzed, {len(_view.candidates)} candidates")
            else:
                layout.label(text="Current frame: Not Analyzed")


class _VtaReviewSubpanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlendCap'
    bl_parent_id = "VIEW3D_PT_blendcap_vta_review"


class VIEW3D_PT_blendcap_vta_analyze(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Analyze"
    bl_idname = "VIEW3D_PT_blendcap_vta_analyze"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        location = _location
        if location is None:
            try:
                location = _location_from_context(context)
            except VtaReviewUiError:
                pass
        layout.prop(wm, "blendcap_vta_analyze_scope", text="Coverage")
        if wm.blendcap_vta_analyze_scope == 'RANGE':
            row = layout.row(align=True)
            row.prop(wm, "blendcap_vta_analyze_start", text="Start Frame")
            row.prop(wm, "blendcap_vta_analyze_end", text="End Frame")
        expected = None
        if location is not None and _backend is not None:
            try:
                first, last = _backend.source_coverage(location)
                if wm.blendcap_vta_analyze_scope == 'RANGE':
                    start, end = map_visible_analysis_range(
                        location, first, last,
                        wm.blendcap_vta_analyze_start, wm.blendcap_vta_analyze_end,
                    )
                else:
                    start, end = first, last
                expected = end - start + 1
            except VtaReviewUiError as exc:
                layout.label(text=str(exc), icon='ERROR')
        if expected is not None:
            layout.label(text=f"Inclusive range: {expected} frames")
        active = _analysis_locked(location.companion_root if location else None)
        launch = layout.row()
        launch.enabled = not active and expected is not None
        launch.operator("blendcap.vta_analyze", text="Analyze Candidates", icon='VIEWZOOM')
        if active:
            layout.label(text="Unavailable while candidate analysis is running.", icon='INFO')
        if _status_is_error and "Analysis environment unavailable" in _status:
            layout.label(text="CUDA SAM3 runtime unavailable", icon='ERROR')
            layout.operator("screen.userpref_show", text="Open BlendCap Preferences")
        root = location.companion_root if location is not None else None
        status = _analysis_status_for(
            root, _view.source_id if _view else None
        ) if root is not None else None
        if (root is not None and root == _analysis_root and not active
                and _analysis_fallback_status is not None):
            status = _analysis_fallback_status
        if (active and root == _analysis_root and _analysis_process is not None
                and (status is None or status.get("pid") != _analysis_process.pid)):
            status = {"state": "starting", "start_frame": _analysis_start,
                      "end_frame": _analysis_end, "completed_in_requested_range": 0}
        box = layout.box()
        state = "idle" if status is None else str(status.get("state", "unknown"))
        if state in {"analyzing", "starting", "running"}:
            box.label(text="Analyzing…", icon='TIME')
        else:
            box.label(text=f"Analysis: {state.title()}",
                      icon='ERROR' if state == 'failed' else 'INFO')
        if status is not None:
            start, end = status.get("start_frame"), status.get("end_frame")
            if location is not None and isinstance(start, int) and isinstance(end, int):
                box.label(text=f"Blender frames {visible_frame(location, start)}–"
                               f"{visible_frame(location, end)} (inclusive)")
                total = end - start + 1
                completed = status.get("completed_in_requested_range")
                if isinstance(completed, int) and total > 0:
                    box.label(text=f"Current run: {completed} / {total} frames")
                    bar = box.row()
                    bar.enabled = False
                    bar.prop(wm, "blendcap_vta_analysis_progress", text="Progress", slider=True)
            newly, reused = status.get("newly_analyzed"), status.get("skipped")
            if isinstance(newly, int) and isinstance(reused, int):
                box.label(text=f"New this run: {newly}; reused cached: {reused}")
            current = status.get("current_frame")
            if location is not None and isinstance(current, int):
                box.label(text=f"Most recent Blender frame: {visible_frame(location, current)}")
            if isinstance(status.get("total_analyzed_frames"), int):
                coverage = status.get("source_coverage")
                source_total = (coverage[1] - coverage[0] + 1
                                if isinstance(coverage, list) and len(coverage) == 2
                                and all(type(value) is int for value in coverage) else None)
                suffix = f" / {source_total}" if source_total is not None else ""
                box.label(text=f"Total analyzed: {status['total_analyzed_frames']}{suffix} frames")
            if state in {"analyzing", "running"}:
                box.operator("blendcap.vta_analyze_stop", text="Cancel Analysis")
            if state == "failed":
                detail = str(status.get("error", "See analysis log"))
                box.label(text=_analysis_error_summary(detail), icon='ERROR')
                disclosure = box.row()
                disclosure.prop(wm, "blendcap_vta_error_details", text="Error Details",
                                icon=('TRIA_DOWN' if wm.blendcap_vta_error_details
                                      else 'TRIA_RIGHT'), emboss=False)
                if wm.blendcap_vta_error_details:
                    details = box.box()
                    for line in textwrap.wrap(detail, width=66, break_long_words=True):
                        details.label(text=line)
                    details.operator("blendcap.vta_copy_analysis_error", text="Copy Details",
                                     icon='COPYDOWN')
        if _view is not None:
            box.label(text=f"Current frame: {_view.state_label}")
        layout.label(text="Provider: offline SAM3 (CUDA)")


class VIEW3D_PT_blendcap_vta_characters(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Characters"
    bl_idname = "VIEW3D_PT_blendcap_vta_characters"

    def draw(self, context):
        layout = self.layout
        if _view is None:
            layout.label(text="Load a VTA project to manage Characters.")
            return
        layout.prop(context.window_manager, "blendcap_vta_character", text="Character")
        layout.label(text=f"{len(_view.characters)} Characters")
        create_row = layout.row(align=True)
        create_row.enabled = not _analysis_locked()
        create_row.prop(context.window_manager, "blendcap_vta_new_character_name", text="New")
        create_row.operator("blendcap.vta_review_create_character", text="Create", icon='ADD')
        layout.label(text=f"Blank name creates {next_generic_character_name(_view)}")
        _draw_management_actions(layout, actions={"rename-character", "delete-character"})


class VIEW3D_PT_blendcap_vta_current_frame(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Current Frame Review"
    bl_idname = "VIEW3D_PT_blendcap_vta_current_frame"

    def draw(self, context):
        layout = self.layout
        if _view is None:
            layout.label(text="Current review unavailable.")
            return
        selected_character = next(
            (item for item in _view.characters if item.character_id == _selection.character_id),
            None,
        )
        layout.label(text=(f"Active Character: {selected_character.display_name}"
                           if selected_character else "Select a Character above"), icon='USER')
        if selected_character is not None and selected_character.pose_rejected:
            layout.label(text="Generated Animation Rejected", icon='INFO')
        assignment_box = layout.box()
        assignment_box.label(text="Candidate Assignment")
        for candidate in _view.candidates:
            if candidate.assigned_character_name:
                assignment_box.label(
                    text=f"Candidate {candidate.position}: {candidate.assigned_character_name}",
                    icon='CHECKMARK',
                )
        assignment_box.prop(context.window_manager, "blendcap_vta_candidate", text="Selected Candidate")
        assignment_box.prop(context.window_manager, 'blendcap_vta_show_suppressed',
                            text='Show Suppressed Candidates')
        selected_row = next((c for c in _view.candidates
                             if c.observation_id == _selection.observation_id), None)
        suppress_row = assignment_box.row()
        suppress_row.enabled = selected_row is not None and not _analysis_locked()
        op = suppress_row.operator('blendcap.vta_suppress_candidate',
            text='Restore Candidate' if selected_row and selected_row.suppressed else 'Suppress Candidate')
        op.restore = bool(selected_row and selected_row.suppressed)
        if selected_character is not None:
            state = selected_character.decision_state
            if state in HARD_STATE_LABELS:
                assignment_box.label(text=f"{selected_character.display_name} — {HARD_STATE_LABELS[state]}", icon='INFO')
            elif state in ('visible', 'partially-visible'):
                assigned = next((candidate for candidate in _view.candidates
                                 if candidate.observation_id == selected_character.occupied_observation_id), None)
                label = (f"Assigned to Candidate {assigned.position}" if assigned
                         else "Candidate Assignment (stored evidence)")
                assignment_box.label(text=f"{selected_character.display_name} — {label}", icon='INFO')
            elif selected_character.derived_state == 'visible':
                derived = next((candidate for candidate in _view.candidates
                                if candidate.observation_id == selected_character.derived_observation_id), None)
                label = (f"Derived: Candidate {derived.position}" if derived
                         else "Derived: candidate outside current view")
                assignment_box.label(text=f"{selected_character.display_name} — {label}", icon='INFO')
            elif selected_character.derived_state == 'unresolved':
                assignment_box.label(text=f"{selected_character.display_name} — Derived: Unresolved", icon='INFO')
            else:
                assignment_box.label(text=f"{selected_character.display_name} — No Decision", icon='INFO')
        assign = assignment_box.row(align=True)
        assign.enabled = not _analysis_locked() and _selection.can_assign(_view)
        assign.operator("blendcap.vta_review_assign", text="Assign", icon='CHECKMARK')
        _draw_management_actions(assignment_box, actions={"clear-assignment"})
        decision_box = layout.box()
        decision_box.label(text="Mark Active Character")
        hard_row = decision_box.row(align=True)
        for kind, label in (("occluded", "Occluded"), ("not-visible", "Not Visible")):
            choice = hard_row.row(align=True)
            choice.enabled = (not _analysis_locked()
                              and _selection.hard_state_intent(_view, kind) is not None)
            choice.operator("blendcap.vta_review_hard_state", text=label).decision_kind = kind
        no_valid = decision_box.row(align=True)
        no_valid.enabled = (not _analysis_locked() and
                            _selection.hard_state_intent(_view, "no-valid-candidate") is not None)
        no_valid.operator(
            "blendcap.vta_review_hard_state", text="No Valid Candidate"
        ).decision_kind = "no-valid-candidate"
        keys_box = layout.box()
        keys_box.label(text="Review Keys")
        _draw_management_actions(keys_box, actions={
            "clear-review-key", "clear-selected-review-keys",
        })


class VIEW3D_PT_blendcap_vta_tracking(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Update Tracking"
    bl_idname = "VIEW3D_PT_blendcap_vta_tracking"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.enabled = _action_availability(context)["update-tracking"]
        row.operator("blendcap.vta_update_tracking", text="Update Tracking", icon='FILE_REFRESH')
        layout.label(text="Nearest hard keys bound the interval")
        layout.label(text="Otherwise: about 5 seconds each way")
        draw_operation_feedback(layout, context, 'tracking')


class VIEW3D_PT_blendcap_vta_animation(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = 'Animation'
    bl_idname = 'VIEW3D_PT_blendcap_vta_animation'

    def draw(self, context):
        info = cached_scene_source(context)
        if info:
            source = info[1]
            offset = info[2].blender_frame - info[2].source_frame
            self.layout.label(text=f'Source frames: {source["start_frame"]+offset}–{source["end_frame"]+offset}. Choose this range for the full source.')
        controls = self.layout.column()
        controls.enabled = _view is not None and not _analysis_locked()
        controls.prop(context.window_manager, 'blendcap_vta_generation_scope', text='Characters')
        bounds = controls.row(align=True)
        bounds.prop(context.window_manager, 'blendcap_vta_animation_start')
        bounds.prop(context.window_manager, 'blendcap_vta_animation_end')
        current = controls.row(align=True)
        current.operator('blendcap.vta_generate_animation', text='Start = Current Frame').action = 'START_CURRENT'
        current.operator('blendcap.vta_generate_animation', text='End = Current Frame').action = 'END_CURRENT'
        controls.operator('blendcap.vta_generate_animation', text='Generate Animation Range').action = 'RANGE'
        self.layout.label(text='Reviewed Characters only; Freeze / Interpolate repair rejected poses.')
        if any(o.get('vta_output_stale') for o in context.scene.objects):
            self.layout.label(text='Review changed since generation. Generate the affected range to update it.', icon='INFO')
        feedback = _operation_feedback.get(_feedback_key('animation', context))
        if _generation_status and feedback is None:
            self.layout.label(text=_generation_status, icon='TIME' if _reconstruction_process is not None else 'INFO')
        if _reconstruction_process is not None and _reconstruction_process.poll() is None:
            self.layout.operator('blendcap.vta_generate_animation', text='Cancel Generation').action = 'CANCEL'
        draw_operation_feedback(self.layout, context, 'animation')


class VIEW3D_PT_blendcap_vta_display(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Display"
    bl_idname = "VIEW3D_PT_blendcap_vta_display"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "blendcap_show_vta_candidate_overlay", text="Candidate Boxes")
        layout.label(text="Review keys: click in VTA Review Timeline")
        layout.label(text="Legacy Sequencer lanes hide over strips")
        layout.label(text="◆ Assignment  ○ Occluded  □ No Valid  × Not Visible")
        layout.label(text=f"Selected review keys: {len(_review_key_selection.keyframe_ids)}")


class VIEW3D_PT_blendcap_vta_advanced(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = "Advanced"
    bl_idname = "VIEW3D_PT_blendcap_vta_advanced"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        prefs = addon_prefs(context)
        if prefs is not None:
            settings = layout.column()
            settings.enabled = not _analysis_locked()
            settings.prop(prefs, "vta_core_location", text="Private Core")
            settings.prop(prefs, "vta_analysis_python", text="Analysis Python")
            settings.prop(prefs, "vta_kinesia_root", text="SAM3 Source")
            settings.prop(prefs, "vta_sam3_checkpoint", text="SAM3 Weights")
        if _view is not None:
            selected = next((item for item in _view.candidates
                             if item.observation_id == _selection.observation_id), None)
            if selected is not None:
                layout.label(text=f"Observation ID: {selected.observation_id}")
            layout.label(text=f"Analysis revision: {_view.analysis_revision_id}")
        layout.operator("blendcap.vta_review_refresh", text="Refresh", icon='FILE_REFRESH')


classes = (
    BLENDCAP_OT_vta_generate_animation,
    BLENDCAP_OT_vta_update_animation,
    BLENDCAP_OT_vta_generation_details,
    VIEW3D_PT_blendcap_vta_animation,
    BLENDCAP_OT_vta_suppress_candidate,
    BLENDCAP_OT_vta_candidate_select,
    BLENDCAP_OT_vta_review_key_select,
    BLENDCAP_OT_vta_analyze,
    BLENDCAP_OT_vta_analyze_stop,
    BLENDCAP_OT_vta_copy_analysis_error,
    BLENDCAP_OT_vta_review_refresh,
    BLENDCAP_OT_vta_review_assign,
    BLENDCAP_OT_vta_repeat_assignment,
    BLENDCAP_OT_vta_review_hard_state,
    BLENDCAP_OT_vta_review_create_character,
    BLENDCAP_OT_vta_review_manage,
    BLENDCAP_OT_vta_update_tracking,
    BLENDCAP_OT_vta_motion_trust,
    BLENDCAP_MT_vta_commands,
    VIEW3D_PT_blendcap_vta_review,
    VIEW3D_PT_blendcap_vta_analyze,
    VIEW3D_PT_blendcap_vta_characters,
    VIEW3D_PT_blendcap_vta_current_frame,
    VIEW3D_PT_blendcap_vta_tracking,
    VIEW3D_PT_blendcap_vta_display,
    VIEW3D_PT_blendcap_vta_advanced,
)


def register_properties() -> None:
    bpy.types.WorkSpace.blendcap_vta_mode_enabled = bpy.props.BoolProperty(default=False)
    bpy.types.WorkSpace.blendcap_vta_loaded_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    _reset_repeat_assignment_session()
    _reset_scene_presentation()
    if _reset_repeat_assignment_session not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reset_repeat_assignment_session)
    if _reset_scene_presentation not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reset_scene_presentation)
    if _cancel_generation_before_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_cancel_generation_before_load)
    if _frame_presentation_changed not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame_presentation_changed)
    if not bpy.app.timers.is_registered(_scene_observer_timer):
        bpy.app.timers.register(_scene_observer_timer, first_interval=0.0, persistent=True)
    bpy.types.WindowManager.blendcap_vta_animation_start = bpy.props.IntProperty(
        name='Animation Start', description='Inclusive Blender frame', default=1)
    bpy.types.WindowManager.blendcap_vta_animation_end = bpy.props.IntProperty(
        name='Animation End', description='Inclusive Blender frame', default=20)
    bpy.types.WindowManager.blendcap_vta_show_suppressed = bpy.props.BoolProperty(
        name='Show Suppressed Candidates', default=False, update=_show_suppressed_changed)
    bpy.types.WindowManager.blendcap_vta_generation_scope = bpy.props.EnumProperty(
        name='Generation Scope', items=(('ALL', 'All Reviewed', 'Generate all reviewed Characters'),
        ('SELECTED', 'Selected Rows', 'Generate only selected VTA Character rows; incompatible calibration still requires source-wide migration')),
        default='ALL')
    for operation in ('animation','tracking','repair'):
        setattr(bpy.types.WindowManager, 'blendcap_vta_' + operation + '_details',
                bpy.props.BoolProperty(name='Details', default=False))
    bpy.types.WindowManager.blendcap_vta_motion_start = bpy.props.IntProperty(
        name="Start Frame", description="Inclusive repair start; Apply Start / End saves selected range edits", default=1)
    bpy.types.WindowManager.blendcap_vta_motion_end = bpy.props.IntProperty(
        name="End Frame", description="Inclusive repair end; Interpolate anchors must lie outside both endpoints", default=1)
    bpy.types.WindowManager.blendcap_vta_repair_mode = bpy.props.EnumProperty(
        name='Repair Mode', items=(('freeze', 'Freeze', ''), ('interpolate', 'Interpolate', '')), default='freeze')
    bpy.types.Scene.blendcap_show_vta_candidate_overlay = bpy.props.BoolProperty(
        name="Show VTA Candidate Overlay", default=True,
        description="Show immutable VTA FrameReview candidates in the Sequencer Preview")
    bpy.types.WindowManager.blendcap_vta_analyze_scope = bpy.props.EnumProperty(
        name="Analyze Coverage", items=(
            ('WHOLE', "Whole Source", "Analyze all missing source frames"),
            ('RANGE', "Frame Range", "Analyze missing frames in an inclusive Blender frame range"),
        ), default='WHOLE',
    )
    bpy.types.WindowManager.blendcap_vta_analyze_start = bpy.props.IntProperty(
        name="Start Blender Frame", default=0, min=0)
    bpy.types.WindowManager.blendcap_vta_analyze_end = bpy.props.IntProperty(
        name="End Blender Frame", default=0, min=0)
    bpy.types.WindowManager.blendcap_vta_analysis_progress = bpy.props.FloatProperty(
        name="Analyze Progress", default=0.0, min=0.0, max=100.0,
        subtype='PERCENTAGE', description="Completed portion of the current Analyze range")
    bpy.types.WindowManager.blendcap_vta_error_details = bpy.props.BoolProperty(
        name="Error Details", default=False,
        description="Show the technical Analyze error and a copy button")
    bpy.types.WindowManager.blendcap_vta_candidate = bpy.props.EnumProperty(
        name="Candidate",
        items=candidate_items,
        update=_candidate_changed,
    )
    bpy.types.WindowManager.blendcap_vta_character = bpy.props.EnumProperty(
        name="Character",
        items=character_items,
        update=_character_changed,
    )
    bpy.types.WindowManager.blendcap_vta_new_character_name = bpy.props.StringProperty(
        name="New Character",
        description="Optional display name; blank creates the next numbered Character",
        default="",
    )
    _register_preview_tools()
    _register_review_context_keymap()
    _register_repeat_assignment_keymap()
    vta_overlay.register(overlay_state)
    vta_timeline_overlay.register(timeline_state)
    _remove_legacy_vta_menu_hook(bpy.types.SEQUENCER_MT_context_menu)
    _remove_legacy_vta_menu_hook(bpy.types.SEQUENCER_MT_editor_menus)
    _set_menu_hook(bpy.types.SEQUENCER_MT_context_menu, _draw_vta_context)
    _set_menu_hook(bpy.types.SEQUENCER_MT_editor_menus, _draw_vta_header)


def unregister_properties() -> None:
    # Cooperative release also covers the worker waiting for consent.
    if _reconstruction_process is not None and _reconstruction_process.poll() is None and _reconstruction_stop:
        _cancel_generation_request()
    _reset_scene_presentation()
    _reset_repeat_assignment_session()
    if _reset_repeat_assignment_session in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reset_repeat_assignment_session)
    if _reset_scene_presentation in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reset_scene_presentation)
    if _cancel_generation_before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_cancel_generation_before_load)
    if _frame_presentation_changed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_presentation_changed)
    if bpy.app.timers.is_registered(_scene_observer_timer):
        bpy.app.timers.unregister(_scene_observer_timer)
    del bpy.types.WorkSpace.blendcap_vta_mode_enabled
    del bpy.types.WorkSpace.blendcap_vta_loaded_scene
    _scene_sessions.clear()
    _unregister_repeat_assignment_keymap()
    global _pending_presentation
    _pending_presentation = None
    if bpy.app.timers.is_registered(_presentation_timer):
        bpy.app.timers.unregister(_presentation_timer)
    del bpy.types.WindowManager.blendcap_vta_animation_start
    del bpy.types.WindowManager.blendcap_vta_animation_end
    del bpy.types.WindowManager.blendcap_vta_show_suppressed
    del bpy.types.WindowManager.blendcap_vta_generation_scope
    for operation in ('animation','tracking','repair'):
        delattr(bpy.types.WindowManager, 'blendcap_vta_' + operation + '_details')
    if bpy.app.timers.is_registered(_reconstruction_timer):
        bpy.app.timers.unregister(_reconstruction_timer)
    global _analysis_progress_started
    _character_selections.clear()
    _repair_selections.clear()
    del bpy.types.WindowManager.blendcap_vta_motion_start
    del bpy.types.WindowManager.blendcap_vta_motion_end
    del bpy.types.WindowManager.blendcap_vta_repair_mode
    _remove_menu_hook(bpy.types.SEQUENCER_MT_context_menu, _draw_vta_context)
    _remove_menu_hook(bpy.types.SEQUENCER_MT_editor_menus, _draw_vta_header)
    vta_timeline_overlay.unregister()
    vta_overlay.unregister()
    if bpy.app.timers.is_registered(_analysis_timer):
        bpy.app.timers.unregister(_analysis_timer)
    if _analysis_progress_started:
        try:
            bpy.context.window_manager.progress_end()
        except RuntimeError:
            pass
        _analysis_progress_started = False
    _unregister_review_context_keymap()
    _unregister_preview_tools()
    del bpy.types.Scene.blendcap_show_vta_candidate_overlay
    del bpy.types.WindowManager.blendcap_vta_analyze_scope
    del bpy.types.WindowManager.blendcap_vta_analyze_start
    del bpy.types.WindowManager.blendcap_vta_analyze_end
    del bpy.types.WindowManager.blendcap_vta_analysis_progress
    del bpy.types.WindowManager.blendcap_vta_error_details
    del bpy.types.WindowManager.blendcap_vta_candidate
    del bpy.types.WindowManager.blendcap_vta_character
    del bpy.types.WindowManager.blendcap_vta_new_character_name
