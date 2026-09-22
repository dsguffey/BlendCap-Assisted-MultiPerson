"""VSE Preview candidate overlay; presentation-only and Blender-local."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayCandidate:
    observation_id: str
    label: str
    box_xyxy: tuple[float, float, float, float]
    assigned_character_name: str | None
    selected: bool


@dataclass(frozen=True)
class OverlayRegionCandidate:
    """One current-review candidate projected into VSE region pixels."""
    observation_id: str
    label: str
    region_box: tuple[float, float, float, float]
    assigned_character_name: str | None
    selected: bool


def build_overlay_candidates(view, selected_observation_id: str) -> tuple[OverlayCandidate, ...]:
    """Project only immutable review rows; order is never an identity key."""
    if view is None or view.state != "analyzed-with-candidates":
        return ()
    return tuple(
        OverlayCandidate(
            row.observation_id,
            row.label + (f" — {row.assigned_character_name}" if row.assigned_character_name else ""),
            row.box_xyxy,
            row.assigned_character_name,
            row.observation_id == selected_observation_id,
        ) for row in view.candidates
    )


def source_box_to_region(view2d, box_xyxy, source_width, source_height):
    """Canonical lower-left source pixels -> VSE Preview region pixels.

    VSE's View2D owns zoom, pan, aspect, and letterboxing; its view coordinate
    system is likewise lower-left, so no independent Y flip is introduced.
    """
    x1, y1, x2, y2 = box_xyxy
    half_width = float(source_width) / 2.0
    half_height = float(source_height) / 2.0
    p1 = view2d.view_to_region(x1 - half_width, y1 - half_height, clip=False)
    p2 = view2d.view_to_region(x2 - half_width, y2 - half_height, clip=False)
    return (float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]))


def build_overlay_region_candidates(view, selected_observation_id: str, view2d):
    """Use the draw transform once for both rendering and click hit-testing."""
    if view is None or view.source_width is None or view.source_height is None:
        return ()
    return tuple(
        OverlayRegionCandidate(
            item.observation_id,
            item.label,
            source_box_to_region(
                view2d, item.box_xyxy, view.source_width, view.source_height
            ),
            item.assigned_character_name,
            item.selected,
        )
        for item in build_overlay_candidates(view, selected_observation_id)
    )


def hit_test_overlay_candidates(candidates, point, *, enabled: bool = True):
    """Return a durable observation ID using the overlay overlap policy.

    The smallest containing display box wins; equal-area boxes are ordered by
    observation ID.  This is interaction policy only and never changes the
    candidate inventory or its identity.
    """
    if not enabled:
        return None
    px, py = point
    hits = []
    for candidate in candidates:
        x1, y1, x2, y2 = candidate.region_box
        left, right = sorted((x1, x2))
        bottom, top = sorted((y1, y2))
        if left <= px <= right and bottom <= py <= top:
            area = (right - left) * (top - bottom)
            hits.append((area, candidate.observation_id))
    return min(hits)[1] if hits else None


def hit_test_review_candidate(
    view, selected_observation_id: str, view2d, point, *, enabled: bool = True
):
    """Hit-test only the currently supplied immutable FrameReview."""
    return hit_test_overlay_candidates(
        build_overlay_region_candidates(view, selected_observation_id, view2d),
        point,
        enabled=enabled,
    )


def is_vse_preview_region(area_type, region_type, view_type) -> bool:
    """True only for Blender's dedicated VSE video-canvas region."""
    return (
        area_type == 'SEQUENCE_EDITOR'
        and region_type == 'PREVIEW'
        and view_type in {'PREVIEW', 'SEQUENCER_PREVIEW'}
    )


_handler = None
_state_provider = None


def find_scene_owning_source(
    scenes, source_path, strips_for_scene, source_path_for_strip, *, preferred_scene=None
):
    """Return the unique VSE scene whose authoritative strip owns ``source_path``.

    The caller supplies Blender-specific strip accessors so this ownership rule
    remains testable without importing Blender.  A currently active, matching
    scene wins only when it is itself one of the matching owners; no scene name
    or arbitrary active scene is used as a fallback.
    """
    matches = []
    for scene in scenes:
        for strip in strips_for_scene(scene):
            if source_path_for_strip(strip) == source_path:
                matches.append(scene)
                break
    if preferred_scene in matches:
        return preferred_scene
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("No VSE movie strip owns the VTA review source.")
    raise ValueError("More than one VSE scene owns the VTA review source.")


def bind_vse_preview_scene(workspace, areas, owning_scene) -> int:
    """Select ``owning_scene`` through Blender's workspace VSE selector.

    Blender 5.2 exposes this selector as ``WorkSpace.sequencer_scene``.  It is
    not a property of ``SpaceSequenceEditor``.  Preview areas are redrawn so a
    just-created GUI area observes the new workspace binding immediately.
    """
    if workspace is None or owning_scene is None:
        return 0
    changed = 0
    if getattr(workspace, "sequencer_scene", None) is not owning_scene:
        workspace.sequencer_scene = owning_scene
        changed += 1
    for area in areas:
        if getattr(area, "type", None) != 'SEQUENCE_EDITOR':
            continue
        space = area.spaces.active
        if getattr(space, "view_type", None) not in {'PREVIEW', 'SEQUENCER_PREVIEW'}:
            continue
        # This is deliberately separate from the scene binding above.  Zero
        # means no VSE channel filter; it does not select a Blender Scene.
        if getattr(space, "display_channel", 0) != 0:
            space.display_channel = 0
            changed += 1
        area.tag_redraw()
    return changed


def register(state_provider) -> None:
    global _handler, _state_provider
    _state_provider = state_provider
    if _handler is None:
        import bpy
        _handler = bpy.types.SpaceSequenceEditor.draw_handler_add(
            _draw, (), 'PREVIEW', 'POST_PIXEL'
        )


def unregister() -> None:
    global _handler, _state_provider
    if _handler is not None:
        import bpy
        bpy.types.SpaceSequenceEditor.draw_handler_remove(_handler, 'PREVIEW')
    _handler = None
    _state_provider = None


def request_redraw() -> None:
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'SEQUENCE_EDITOR':
                    area.tag_redraw()
    except Exception:
        pass


def _draw() -> None:
    try:
        import bpy, blf, gpu
        from gpu_extras.batch import batch_for_shader
        area, region = bpy.context.area, bpy.context.region
        if area is None or region is None or area.type != 'SEQUENCE_EDITOR':
            return
        space = area.spaces.active
        preview_scene = getattr(bpy.context, "sequencer_scene", None) or bpy.context.scene
        if space.view_type not in {'PREVIEW', 'SEQUENCER_PREVIEW'} or not getattr(preview_scene, 'blendcap_show_vta_candidate_overlay', True):
            return
        view, selected = _state_provider() if _state_provider else (None, "")
        if view is None or view.source_width is None or view.source_height is None:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for item in build_overlay_region_candidates(view, selected, region.view2d):
            x1, y1, x2, y2 = item.region_box
            color = (1.0, .75, .1, 1.0) if item.selected else (.1, .85, 1.0, 1.0)
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": ((x1,y1),(x2,y1),(x2,y2),(x1,y2),(x1,y1))})
            shader.bind(); shader.uniform_float("color", color); batch.draw(shader)
            blf.position(0, x1 + 4, y2 + 4, 0); blf.size(0, 13); blf.color(0, *color); blf.draw(0, item.label)
    except Exception:
        return
