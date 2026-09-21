"""First visible Blender shell for private VTA current-frame review."""

from __future__ import annotations

from pathlib import Path

import bpy

from ..blendcap_vta_review import (
    PrivateCoreBackend,
    ReviewLocation,
    SelectionState,
    VtaReviewUiError,
    companion_root_for_blend,
    map_vse_frame_to_source,
    user_message,
)
from ..blendcap_vta_runtime import discover_vta_core
from .helpers import _active_video_strip, _resolve_video_path, _strip_visible_end, addon_prefs


_selection = SelectionState()
_view = None
_location = None
_status = ""
_status_is_error = False
_backend = None
_backend_location = None
_backend_root = None
_project_signature = None
_NO_SELECTION = "__blendcap_vta_no_selection__"
_candidate_enum_items = [(_NO_SELECTION, "No candidate", "Refresh the current frame")]
_character_enum_items = [(_NO_SELECTION, "No Character", "Refresh the current frame")]


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
    scene = context.scene
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


def _sync_properties(context) -> None:
    wm = context.window_manager
    wm.blendcap_vta_candidate = _selection.observation_id or _NO_SELECTION
    wm.blendcap_vta_character = _selection.character_id or _NO_SELECTION


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
            row.display_name + (" (occupied)" if not row.available else ""),
            row.character_id,
        )
        for row in _view.characters
    )


def refresh(context, *, force: bool = False) -> bool:
    global _view, _location, _project_signature, _status, _status_is_error
    try:
        backend = _get_backend(context, force=force)
        location = _location_from_context(context)
        project_path = location.companion_root / "project_state.json"
        try:
            project_stat = project_path.stat()
            project_signature = (project_stat.st_size, project_stat.st_mtime_ns)
        except OSError:
            project_signature = None
        key_changed = (
            _location is None
            or _location.companion_root != location.companion_root
            or _location.source_path != location.source_path
            or _location.source_frame != location.source_frame
            or _project_signature != project_signature
        )
        if force or key_changed or _view is None:
            _view = backend.query(location)
            _location = location
            _project_signature = project_signature
            _selection.accept_review(_view)
            _rebuild_enum_items()
            _sync_properties(context)
        if force:
            _status = "Review refreshed from canonical state."
            _status_is_error = False
        return True
    except Exception as exc:
        _view = None
        _location = None
        _project_signature = None
        _selection.review_key = None
        _selection.observation_id = ""
        _rebuild_enum_items()
        _sync_properties(context)
        _status = user_message(exc)
        _status_is_error = True
        return False


def candidate_items(_self, context):
    return _candidate_enum_items


def character_items(_self, context):
    return _character_enum_items


def _candidate_changed(self, _context):
    _selection.observation_id = (
        "" if self.blendcap_vta_candidate == _NO_SELECTION else self.blendcap_vta_candidate
    )


def _character_changed(self, _context):
    _selection.character_id = (
        "" if self.blendcap_vta_character == _NO_SELECTION else self.blendcap_vta_character
    )


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
    bl_description = "Assign the selected durable observation to the selected Character"

    @classmethod
    def poll(cls, context):
        return _view is not None and _selection.can_assign(_view) and _location is not None

    def execute(self, context):
        global _view, _status, _status_is_error
        if not refresh(context):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        if _view is None or _location is None or not _selection.can_assign(_view):
            _status = "Select one unassigned candidate and one available Character."
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        observation_id = _selection.observation_id
        character_id = _selection.character_id
        try:
            _receipt, refreshed = _get_backend(context).apply(
                _location,
                observation_id=observation_id,
                character_id=character_id,
            )
            _view = refreshed
            _selection.accept_review(refreshed)
            _rebuild_enum_items()
            _sync_properties(context)
            character = next(
                row.display_name for row in refreshed.characters
                if row.character_id == character_id
            )
            _status = f"Assigned candidate to {character}."
            _status_is_error = False
            self.report({'INFO'}, _status)
            return {'FINISHED'}
        except Exception as exc:
            _status = user_message(exc)
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}


class VIEW3D_PT_blendcap_vta_review(bpy.types.Panel):
    bl_label = "VTA Current Frame Review"
    bl_idname = "VIEW3D_PT_blendcap_vta_review"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlendCap'
    bl_parent_id = "VIEW3D_PT_blendcap_ui"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        refresh(context)
        layout = self.layout
        if _location is not None:
            layout.label(text=f"Blender Frame: {_location.blender_frame}")
            layout.label(text=f"Source Frame: {_location.source_frame}")
        if _view is None:
            layout.label(text=_status or "Review unavailable", icon='ERROR')
            prefs = addon_prefs(context)
            if prefs is not None:
                layout.prop(prefs, "vta_core_location", text="Video to Animation Core")
            layout.operator("blendcap.vta_review_refresh", icon='FILE_REFRESH')
            return
        layout.label(text=f"Review: {_view.state_label}")
        for candidate in _view.candidates:
            if candidate.assigned_character_name:
                layout.label(
                    text=f"{candidate.label} - Assigned: {candidate.assigned_character_name}",
                    icon='CHECKMARK',
                )
        layout.prop(context.window_manager, "blendcap_vta_candidate", text="Candidate")
        layout.prop(context.window_manager, "blendcap_vta_character", text="Character")
        row = layout.row(align=True)
        row.operator("blendcap.vta_review_refresh", text="Refresh", icon='FILE_REFRESH')
        assign = row.row(align=True)
        assign.enabled = _selection.can_assign(_view)
        assign.operator("blendcap.vta_review_assign", text="Assign", icon='CHECKMARK')
        if not _view.characters:
            layout.label(text="No existing Characters in this VTA project.", icon='INFO')
        if _status:
            layout.label(text=_status, icon=('ERROR' if _status_is_error else 'INFO'))


classes = (
    BLENDCAP_OT_vta_review_refresh,
    BLENDCAP_OT_vta_review_assign,
    VIEW3D_PT_blendcap_vta_review,
)


def register_properties() -> None:
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


def unregister_properties() -> None:
    del bpy.types.WindowManager.blendcap_vta_candidate
    del bpy.types.WindowManager.blendcap_vta_character
