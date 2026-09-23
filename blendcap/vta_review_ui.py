"""First visible Blender shell for private VTA current-frame review."""

from __future__ import annotations

from pathlib import Path

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
    user_message,
)
from ..blendcap_vta_runtime import discover_vta_core
from . import vta_overlay
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


def _sync_properties(context) -> None:
    wm = context.window_manager
    wm.blendcap_vta_candidate = _selection.observation_id or _NO_SELECTION
    wm.blendcap_vta_character = _selection.character_id or _NO_SELECTION
    vta_overlay.request_redraw()


def overlay_state():
    """Read-only draw callback state; never queries or mutates private core."""
    return _view, _selection.observation_id


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


def _overlay_enabled(context) -> bool:
    scene = getattr(context, "sequencer_scene", None) or getattr(context, "scene", None)
    return bool(
        scene is not None
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
    observation_id = vta_overlay.hit_test_overlay_candidates(
        regions, point
    )
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
        return vta_overlay.is_vse_preview_region(
            getattr(area, "type", None),
            getattr(region, "type", None),
            getattr(space, "view_type", None),
        )

    def invoke(self, context, event):
        return (
            {'FINISHED'}
            if select_overlay_candidate_at(
                context, (event.mouse_region_x, event.mouse_region_y)
            )
            else {'PASS_THROUGH'}
        )


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
         {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
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
         {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
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
        return _view is not None and _selection.can_assign(_view) and _location is not None

    def invoke(self, context, _event):
        global _view, _status, _status_is_error
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
            _view = refreshed
            _selection.accept_review(refreshed)
            _rebuild_enum_items()
            _sync_properties(context)
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
            _view is not None
            and _location is not None
            and _selection.character_id in {item.character_id for item in _view.characters}
        )

    def invoke(self, context, _event):
        global _view, _status, _status_is_error
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
            _view = refreshed
            _selection.accept_review(refreshed)
            _rebuild_enum_items()
            _sync_properties(context)
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

    def execute(self, context):
        global _view, _status, _status_is_error
        if not refresh(context, force=True):
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        name = (context.window_manager.blendcap_vta_new_character_name or "").strip()
        if not name:
            _status = "Enter a non-blank Character name."
            _status_is_error = True
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
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
            _rebuild_enum_items()
            _sync_properties(context)
            _status = f"Created Character {character.display_name}. Select a candidate and Assign it separately."
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
        layout.prop(context.scene, "blendcap_show_vta_candidate_overlay", text="Show VTA Candidate Overlay")
        for candidate in _view.candidates:
            if candidate.assigned_character_name:
                layout.label(
                    text=f"{candidate.label} - Assigned: {candidate.assigned_character_name}",
                    icon='CHECKMARK',
                )
        layout.prop(context.window_manager, "blendcap_vta_candidate", text="Candidate")
        layout.prop(context.window_manager, "blendcap_vta_character", text="Character")
        selected_character = next(
            (item for item in _view.characters if item.character_id == _selection.character_id),
            None,
        )
        if selected_character is not None:
            state = selected_character.decision_state
            if state in HARD_STATE_LABELS:
                layout.label(text=f"Current decision: {HARD_STATE_LABELS[state]}", icon='INFO')
            elif state in ('visible', 'partially-visible'):
                layout.label(text="Current decision: Candidate assigned", icon='INFO')
            else:
                layout.label(text="Current decision: Not reviewed", icon='INFO')
        create_row = layout.row(align=True)
        create_row.prop(
            context.window_manager,
            "blendcap_vta_new_character_name",
            text="New Character",
        )
        create_row.operator(
            "blendcap.vta_review_create_character", text="Create", icon='ADD'
        )
        row = layout.row(align=True)
        row.operator("blendcap.vta_review_refresh", text="Refresh", icon='FILE_REFRESH')
        assign = row.row(align=True)
        assign.enabled = _selection.can_assign(_view)
        assign.operator("blendcap.vta_review_assign", text="Assign", icon='CHECKMARK')
        layout.label(text="Mark selected Character on this frame:")
        hard_row = layout.row(align=True)
        for kind, label in (("occluded", "Occluded"), ("not-visible", "Not Visible")):
            choice = hard_row.row(align=True)
            choice.enabled = _selection.hard_state_intent(_view, kind) is not None
            choice.operator("blendcap.vta_review_hard_state", text=label).decision_kind = kind
        no_valid = layout.row(align=True)
        no_valid.enabled = _selection.hard_state_intent(_view, "no-valid-candidate") is not None
        no_valid.operator(
            "blendcap.vta_review_hard_state", text="No Valid Candidate"
        ).decision_kind = "no-valid-candidate"
        if not _view.characters:
            layout.label(text="No existing Characters in this VTA project.", icon='INFO')
        if _status:
            layout.label(text=_status, icon=('ERROR' if _status_is_error else 'INFO'))


classes = (
    BLENDCAP_OT_vta_candidate_select,
    BLENDCAP_OT_vta_review_refresh,
    BLENDCAP_OT_vta_review_assign,
    BLENDCAP_OT_vta_review_hard_state,
    BLENDCAP_OT_vta_review_create_character,
    VIEW3D_PT_blendcap_vta_review,
)


def register_properties() -> None:
    bpy.types.Scene.blendcap_show_vta_candidate_overlay = bpy.props.BoolProperty(
        name="Show VTA Candidate Overlay", default=True,
        description="Show immutable VTA FrameReview candidates in the Sequencer Preview")
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
        description="Display name for a new semantic Character",
        default="",
    )
    _register_preview_tools()
    vta_overlay.register(overlay_state)


def unregister_properties() -> None:
    vta_overlay.unregister()
    _unregister_preview_tools()
    del bpy.types.Scene.blendcap_show_vta_candidate_overlay
    del bpy.types.WindowManager.blendcap_vta_candidate
    del bpy.types.WindowManager.blendcap_vta_character
    del bpy.types.WindowManager.blendcap_vta_new_character_name
