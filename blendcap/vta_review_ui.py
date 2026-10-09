"""First visible Blender shell for private VTA current-frame review."""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import textwrap
import traceback

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
from ..blendcap_vta_area_mode import ROLE_PREVIEW, ROLE_TIMELINE, role_for
from . import vta_invalidation, vta_overlay, vta_timeline_overlay
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
_review_key_selection = ReviewKeySelection()
_character_selections = {}
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


def _location_from_context(context) -> ReviewLocation:
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
        blender_frame=int(scene.frame_current),
        visible_start=int(visible_start),
        visible_end=_strip_visible_end(strip),
        source_offset=int(source_offset),
    )
    return ReviewLocation(
        companion_root=companion_root_for_blend(bpy.data.filepath),
        source_path=Path(source_path).resolve(),
        source_frame=source_frame,
        blender_frame=int(scene.frame_current),
    )


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


def _sync_properties(context) -> None:
    global _syncing_properties
    if _syncing_properties:
        return
    if _is_timeline_context(context):
        _selection.character_id = current_character_id(context) or ''
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
    return _view, _selection.observation_id


def timeline_state(context=None):
    offset = 0 if _location is None else _location.blender_frame - _location.source_frame
    return (_timeline, _review_key_selection.keyframe_ids,
            _review_key_selection.active_keyframe_id, offset, _lane_scroll(context))


def _lane_scroll(context=None) -> int:
    """Focus the selected Character automatically; no exposed pagination knob."""
    active = current_character_id(context)
    if _timeline is None or not active:
        return 0
    return next((index for index, item in enumerate(_timeline.characters)
                 if item.character_id == active), 0)


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


def _edit_available(operator) -> bool:
    try:
        _require_analysis_idle()
        return True
    except VtaReviewUiError as exc:
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
    bl_label = "Analyze Candidates"
    bl_description = "Analyze missing video frames with the existing offline SAM3 provider"

    def execute(self, context):
        global _analysis_process, _analysis_progress_started, _analysis_root, _analysis_total
        global _analysis_start, _analysis_end, _analysis_fallback_status
        global _status, _status_is_error
        try:
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
    """Bind existing VSE Preview areas to the owning scene's workspace slot."""
    workspace = getattr(context, "workspace", None)
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    areas = getattr(screen, "areas", ()) if screen is not None else ()
    return vta_overlay.bind_vse_preview_scene(workspace, areas, owning_scene)


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


def refresh(context, *, force: bool = False) -> bool:
    global _view, _location, _project_signature, _status, _status_is_error
    global _timeline, _timeline_identity, _cache_signature
    try:
        backend = _get_backend(context, force=force)
        location = _location_from_context(context)
        project_path = location.companion_root / "project_state.json"
        try:
            project_stat = project_path.stat()
            project_signature = (project_stat.st_size, project_stat.st_mtime_ns)
        except OSError:
            project_signature = None
        cache_path = (location.companion_root / "analysis" /
                      f"{_view.analysis_revision_id}.json") if _view is not None else None
        try:
            cache_stat = cache_path.stat() if cache_path is not None else None
            cache_signature = ((cache_stat.st_size, cache_stat.st_mtime_ns)
                               if cache_stat is not None else None)
        except OSError:
            cache_signature = None
        key_changed = (
            _location is None
            or _location.companion_root != location.companion_root
            or _location.source_path != location.source_path
            or _location.source_frame != location.source_frame
            or _project_signature != project_signature
            or _cache_signature != cache_signature
        )
        timeline_identity = (location.companion_root, location.source_path, project_signature)
        if force or timeline_identity != _timeline_identity or _timeline is None:
            _timeline = backend.query_timeline(location)
            _timeline_identity = timeline_identity
            _review_key_selection.reconcile(_timeline)
        if force or key_changed or _view is None:
            _view = backend.query(location, show_suppressed=getattr(
                context.window_manager, 'blendcap_vta_show_suppressed', False))
            _location = location
            _project_signature = project_signature
            _cache_signature = cache_signature
            _selection.accept_review(_view)
            _rebuild_enum_items()
            _sync_properties(context)
            from .vta_skeletal_output import mark_stale_outputs
            mark_stale_outputs(location.companion_root)
        # Query validates the source hash against canonical private state before
        # this selection is allowed to affect the Blender review workspace.
        ensure_vta_preview_context(context, _owning_vse_scene(context, location))
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
        _selection.review_key = None
        _selection.observation_id = ""
        _rebuild_enum_items()
        _sync_properties(context)
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
    vta_invalidation.request_redraw()


def _character_changed(self, _context):
    if _syncing_properties:
        return
    _selection.character_id = (
        "" if self.blendcap_vta_character == _NO_SELECTION else self.blendcap_vta_character
    )
    vta_invalidation.request_redraw()


def _overlay_enabled(context) -> bool:
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


def _reconstruction_timer():
    global _reconstruction_process, _status, _status_is_error
    process = _reconstruction_process
    if process is None:
        return None
    if process.poll() is None:
        vta_invalidation.request_redraw()
        return 0.5
    _reconstruction_process = None
    try:
        if process.returncode:
            raise VtaReviewUiError('Animation generation failed. See the project reconstruction log: '
                                   + str(_reconstruction_log))
        lines = _reconstruction_log.read_text(encoding='utf-8', errors='replace').splitlines()
        output = next(Path(line.split('=', 1)[1]) for line in reversed(lines)
                      if line.startswith('VTA_OUTPUT='))
        from .vta_skeletal_output import import_reviewed_output
        if _reconstruction_scene is None or _reconstruction_scene.name not in bpy.data.scenes:
            raise VtaReviewUiError('Animation target Scene changed; output remains on disk.')
        scene = _reconstruction_scene
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            validate_output_mapping(_reconstruction_location, _location_from_context(bpy.context))
            rigs, report = import_reviewed_output(output, _reconstruction_location,
                                                 _get_backend(bpy.context))
        unavailable = sum(s['state'] == 'unavailable' for c in report['characters'] for s in c['schedule'])
        _status = f'Generated {len(rigs)} Character armature(s). {unavailable} unavailable Character-frames.'
        _status_is_error = False
    except Exception as exc:
        _status, _status_is_error = user_message(exc), True
    vta_invalidation.request_redraw()
    return None


class BLENDCAP_OT_vta_generate_animation(bpy.types.Operator):
    bl_idname = 'blendcap.vta_generate_animation'
    bl_label = 'Generate / Update Animation'
    bl_description = 'Explicitly reconstruct reviewed Character boxes; no identity decisions or tracking changes'

    def execute(self, context):
        global _reconstruction_process, _reconstruction_location, _reconstruction_log
        global _reconstruction_scene
        global _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        try:
            location = _location_from_context(context)
            backend = _get_backend(context)
            project, cache, _revision = backend._load_context(location)
            backend._core.build_skeletal_plan(project, cache)
            bundle = Path(__file__).resolve().parents[1]
            python = Path(_python_exe())
            if not python.is_file() or not (bundle / 'checkpoints/sam-3d-body-dinov3/model.ckpt').is_file():
                raise VtaReviewUiError('Skeletal environment unavailable. Install BlendCap body models in Preferences.')
            command = [str(python), str(bundle / 'blendcap_vta_reconstruct_worker.py'),
                       '--core-root', str(_backend_root), '--blendcap-root', str(bundle),
                       '--companion-root', str(location.companion_root), '--video', str(location.source_path)]
            from uuid import uuid4
            _reconstruction_log = location.companion_root / 'reconstruction' / ('generation-' + uuid4().hex + '.log')
            _reconstruction_log.parent.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1')
            with _reconstruction_log.open('x', encoding='utf-8') as log:
                _reconstruction_process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, cwd=str(bundle), env=environment,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _reconstruction_location = location
            _reconstruction_scene = context.scene
            if not bpy.app.timers.is_registered(_reconstruction_timer):
                bpy.app.timers.register(_reconstruction_timer, first_interval=0.5)
            _status, _status_is_error = 'Generating reviewed skeletal animation…', False
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, user_message(exc))
            return {'CANCELLED'}


class BLENDCAP_OT_vta_suppress_candidate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_suppress_candidate'
    bl_label = 'Suppress Candidate'
    bl_description = 'Reversibly exclude this observation; raw detector evidence is retained'

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
        return (not _analysis_locked() and _view is not None
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
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


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
            not _analysis_locked()
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
        return not _analysis_locked()

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
    _sync_properties(context)
    return True


def select_timeline_characters(context, character_ids, *, extend=False) -> bool:
    selection = timeline_character_selection(context)
    if selection is None or not selection.select_box(_timeline, character_ids, extend=extend):
        return False
    _selection.character_id = selection.active_id or ''
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
        return not _analysis_locked() and _view is not None and _location is not None

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
    if _analysis_locked():
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
        "update-tracking": character is not None and _location is not None,
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
        analyze = layout.row()
        analyze.enabled = not _analysis_locked()
        analyze.operator("blendcap.vta_analyze", text="Analyze Candidates")
        animate = layout.row()
        animate.enabled = not _analysis_locked()
        animate.operator('blendcap.vta_generate_animation', text='Generate / Update Animation')
        if not refresh(context):
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
        'SEQUENCER', 'SEQUENCER_PREVIEW'
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
        'SEQUENCER', 'SEQUENCER_PREVIEW'
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
    bl_label = "Edit Hold Range"
    bl_description = "Edit selected Dope Sheet Characters' Hold ranges; never run tracking automatically"
    action: bpy.props.EnumProperty(items=(
        ('MARK', 'Hold Animation', ''), ('CLEAR', 'Clear Hold Range', ''),
        ('START_CURRENT', 'Set Hold Start to Current Frame', ''),
        ('END_CURRENT', 'Set Hold End to Current Frame', '')), default='MARK')

    def execute(self, context):
        global _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        try:
            if not _is_timeline_context(context):
                raise VtaReviewUiError("Use Hold in the VTA Dope Sheet.")
            location = _location_from_context(context)
            wm = context.window_manager
            if self.action in {'START_CURRENT', 'END_CURRENT'}:
                field = ('blendcap_vta_motion_start' if self.action == 'START_CURRENT'
                         else 'blendcap_vta_motion_end')
                setattr(wm, field, location.blender_frame)
                return {'FINISHED'}
            if self.action not in {'MARK', 'CLEAR'}:
                raise VtaReviewUiError("Unknown Hold action.")
            selection = timeline_character_selection(context)
            if selection is None or not selection.character_ids:
                raise VtaReviewUiError("Select Character rows in the VTA Dope Sheet before editing Hold.")
            targets = selection.character_ids
            if not refresh(context, force=True):
                raise VtaReviewUiError(_status)
            selection = timeline_character_selection(context)
            if selection is None or selection.character_ids != targets or _location != location:
                raise VtaReviewUiError("The Hold selection or source changed; select the rows again.")
            _get_backend(context).set_motion_trust_for_characters(
                location, targets,
                wm.blendcap_vta_motion_start, wm.blendcap_vta_motion_end,
                action='mark' if self.action == 'MARK' else 'clear')
            if not refresh(context, force=True):
                self.report({'WARNING'}, "Hold saved; refresh review to display it.")
                return {'FINISHED'}
            vta_invalidation.request_redraw()
            _status = (f"Marked {len(targets)} Character(s) for Hold. Identity keys retained."
                       if self.action == 'MARK' else
                       "Hold range cleared. Use Update Tracking explicitly when ready.")
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status, _status_is_error = user_message(exc), True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


def draw_cleanup_commands(layout, context, *, menu=False):
    """The menu and N-panel share operators and target availability."""
    selection = timeline_character_selection(context)
    available = _is_timeline_context(context) and _location is not None and not _analysis_locked()
    for action, label in (
            ('START_CURRENT', 'Set Hold Start to Current Frame' if menu else 'Start = Current Frame'),
            ('END_CURRENT', 'Set Hold End to Current Frame' if menu else 'End = Current Frame'),
            ('MARK', 'Hold Animation'), ('CLEAR', 'Clear Hold Range')):
        row = layout.row()
        row.enabled = available and (action.endswith('CURRENT') or
                                    bool(selection and selection.character_ids))
        row.operator('blendcap.vta_motion_trust', text=label).action = action


def draw_motion_trust(layout, context):
    """Hold is UI vocabulary; the authoritative backend name is unchanged."""
    layout.label(text='Hold Animation')
    wm = context.window_manager
    controls = layout.column()
    controls.enabled = _location is not None and not _analysis_locked()
    row = controls.row(align=True)
    row.prop(wm, 'blendcap_vta_motion_start', text='Hold Start')
    row.prop(wm, 'blendcap_vta_motion_end', text='Hold End')
    draw_cleanup_commands(layout, context)
    layout.label(text='Clear subtracts the inclusive range; tracking stays explicit')
    active = current_character_id(context)
    selected = next((c for c in _view.characters if c.character_id == active), None) if _view else None
    if selected is not None and selected.motion_untrusted:
        layout.label(text='Animation Held', icon='INFO')
    if _timeline and _location:
        for interval in _timeline.untrusted_motion_intervals:
            if interval.character_id == active:
                layout.label(text=f'Hold: {visible_frame(_location, interval.start_frame)}–{visible_frame(_location, interval.end_frame)}')


class BLENDCAP_OT_vta_update_tracking(bpy.types.Operator):
    """Explicitly derive continuity from cached candidates within local hard keys."""

    bl_idname = "blendcap.vta_update_tracking"
    bl_label = "Update Tracking"
    bl_description = "Recompute non-authoritative tracking near this frame from existing analysis"

    def execute(self, context):
        global _status, _status_is_error
        if not _edit_available(self):
            return {'CANCELLED'}
        try:
            location = _location_from_context(context)
            _use_timeline_active(context)
            if not _selection.character_id:
                raise VtaReviewUiError("Select a Character before Update Tracking.")
            result, _review, _timeline_result = _get_backend(context).update_tracking(
                location, _selection.character_id
            )
            if not refresh(context, force=True):
                _status = f"Tracking saved, but review refresh failed: {_status}"
                _status_is_error = True
                self.report({'WARNING'}, _status)
                return {'FINISHED'}
            _status = tracking_update_success(
                result, frame_offset=location.blender_frame - location.source_frame,
            )
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            if not isinstance(exc, VtaReviewUiError):
                traceback.print_exc()
            _status = tracking_update_error(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
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
        if _location is not None:
            layout.label(text=f"Blender frame {_location.blender_frame}")
        if _view is None:
            layout.label(text=_status or "Review unavailable", icon='ERROR')
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
        if selected_character is not None and selected_character.motion_untrusted:
            layout.label(text="Animation Held", icon='INFO')
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
        row.enabled = _action_availability()["update-tracking"]
        row.operator("blendcap.vta_update_tracking", text="Update Tracking", icon='FILE_REFRESH')
        layout.label(text="Nearest hard keys bound the interval")
        layout.label(text="Otherwise: about 5 seconds each way")
        if _status:
            for index, line in enumerate(textwrap.wrap(_status, width=54)):
                layout.label(text=line, icon=('ERROR' if _status_is_error else 'INFO')
                             if index == 0 else 'NONE')


class VIEW3D_PT_blendcap_vta_animation(_VtaReviewSubpanel, bpy.types.Panel):
    bl_label = 'Animation'
    bl_idname = 'VIEW3D_PT_blendcap_vta_animation'

    def draw(self, context):
        row = self.layout.row()
        row.enabled = _view is not None and not _analysis_locked()
        row.operator('blendcap.vta_generate_animation')
        self.layout.label(text='Reviewed Characters only; Hold freezes pose and root.')
        if any(o.get('vta_output_stale') for o in bpy.data.objects):
            self.layout.label(text='Animation is stale. Generate / Update explicitly.', icon='ERROR')
        if _reconstruction_process is not None:
            self.layout.label(text='Generating animation…', icon='TIME')


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
    VIEW3D_PT_blendcap_vta_animation,
    BLENDCAP_OT_vta_suppress_candidate,
    BLENDCAP_OT_vta_candidate_select,
    BLENDCAP_OT_vta_review_key_select,
    BLENDCAP_OT_vta_analyze,
    BLENDCAP_OT_vta_analyze_stop,
    BLENDCAP_OT_vta_copy_analysis_error,
    BLENDCAP_OT_vta_review_refresh,
    BLENDCAP_OT_vta_review_assign,
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
    bpy.types.WindowManager.blendcap_vta_show_suppressed = bpy.props.BoolProperty(
        name='Show Suppressed Candidates', default=False, update=_show_suppressed_changed)
    bpy.types.WindowManager.blendcap_vta_motion_start = bpy.props.IntProperty(
        name="Hold Start", description="Inclusive Blender frame", default=1)
    bpy.types.WindowManager.blendcap_vta_motion_end = bpy.props.IntProperty(
        name="Hold End", description="Inclusive Blender frame", default=1)
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
    vta_overlay.register(overlay_state)
    vta_timeline_overlay.register(timeline_state)
    _remove_legacy_vta_menu_hook(bpy.types.SEQUENCER_MT_context_menu)
    _remove_legacy_vta_menu_hook(bpy.types.SEQUENCER_MT_editor_menus)
    _set_menu_hook(bpy.types.SEQUENCER_MT_context_menu, _draw_vta_context)
    _set_menu_hook(bpy.types.SEQUENCER_MT_editor_menus, _draw_vta_header)


def unregister_properties() -> None:
    del bpy.types.WindowManager.blendcap_vta_show_suppressed
    if bpy.app.timers.is_registered(_reconstruction_timer):
        bpy.app.timers.unregister(_reconstruction_timer)
    global _analysis_progress_started
    _character_selections.clear()
    del bpy.types.WindowManager.blendcap_vta_motion_start
    del bpy.types.WindowManager.blendcap_vta_motion_end
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
