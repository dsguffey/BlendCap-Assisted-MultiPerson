"""Presentation-only review lanes in the VSE timeline WINDOW region.

The lanes use a narrow VTA-owned pixel gutter and the Sequencer View2D for X.
No native channel, strip, playhead, or project authority is created.
"""

from __future__ import annotations

from ..blendcap_vta_timeline import hit_test_keys
from ..blendcap_vta_workspace import authoritative_key_style
if __package__ == 'blendcap':
    from blendcap_vta_area_mode import ROLE_PREVIEW, role_for
else:
    from ..blendcap_vta_area_mode import ROLE_PREVIEW, role_for


GUTTER_WIDTH = 152
ROW_HEIGHT = 24.0
# Reserve a full lane of headroom for the native playhead/frame labels above
# our first lane. The WINDOW height already excludes Blender's other regions.
TOP_PADDING = ROW_HEIGHT + 16.0
BOTTOM_PADDING = 14.0

_handler = None
_state_provider = None


def is_timeline_region(area, region, space) -> bool:
    return (
        getattr(area, "type", None) == 'SEQUENCE_EDITOR'
        and getattr(region, "type", None) == 'WINDOW'
        and getattr(space, "view_type", None) in {'SEQUENCER', 'SEQUENCER_PREVIEW'}
    )


def visible_lanes(view, region_height: int, scroll: int = 0,
                  *, view2d=None, occupied_channels=()):
    count = max(1, int((region_height - TOP_PADDING - BOTTOM_PADDING) // ROW_HEIGHT))
    first = min(max(0, scroll), max(0, len(view.characters) - count))
    selected = view.characters[first:first + count]
    top = region_height - TOP_PADDING
    lanes = tuple((item.character_id, top - ROW_HEIGHT * (index + .5))
                  for index, item in enumerate(selected))
    if view2d is None:
        return lanes
    # WINDOW shares native strip channel Y. Never draw or hit-test a VTA lane
    # over an occupied strip channel; no fake spacer strips or region hacks.
    return tuple((character_id, y) for character_id, y in lanes
                 if not any(channel - .1 <= view2d.region_to_view(0, y)[1] <= channel + 1.1
                            for channel in occupied_channels))


def hit_key(view, view2d, region_height, point, frame_offset, scroll=0,
            occupied_channels=()):
    lanes = visible_lanes(view, region_height, scroll, view2d=view2d,
                          occupied_channels=occupied_channels)
    return hit_test_keys(
        view, lanes,
        frame_to_x=lambda frame: view2d.view_to_region(frame + frame_offset, 0, clip=False)[0],
        point=point,
    )


def selection_outline_style(key_id, selected_ids, active_id):
    """Non-color hierarchy: active key has a corner mark; group peers do not."""
    if key_id == active_id:
        return "active"
    return "selected" if key_id in selected_ids else None


def occupied_strip_channels(context):
    scene = getattr(context, "sequencer_scene", None) or getattr(context, "scene", None)
    editor = getattr(scene, "sequence_editor", None)
    return tuple(sorted({int(strip.channel) for strip in getattr(editor, "strips", ())}))


def register(state_provider) -> None:
    global _handler, _state_provider
    _state_provider = state_provider
    if _handler is None:
        import bpy
        _handler = bpy.types.SpaceSequenceEditor.draw_handler_add(
            _draw, (), 'WINDOW', 'POST_PIXEL'
        )


def unregister() -> None:
    global _handler, _state_provider
    if _handler is not None:
        import bpy
        bpy.types.SpaceSequenceEditor.draw_handler_remove(_handler, 'WINDOW')
    _handler = None
    _state_provider = None


def _draw() -> None:
    try:
        import bpy, blf, gpu
        from gpu_extras.batch import batch_for_shader

        context = bpy.context
        area, region, space = context.area, context.region, context.space_data
        if not is_timeline_region(area, region, space) or _state_provider is None:
            return
        if role_for(context.workspace, context.screen, area) != ROLE_PREVIEW:
            return
        view, selected_ids, active_id, frame_offset, scroll = _state_provider()
        if view is None:
            return
        lanes = visible_lanes(view, region.height, scroll, view2d=region.view2d,
                              occupied_channels=occupied_strip_channels(context))
        if not lanes:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        def line(vertices, color):
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": vertices})
            shader.bind(); shader.uniform_float("color", color); batch.draw(shader)

        def fill(vertices, color):
            batch = batch_for_shader(shader, 'TRIS', {"pos": vertices})
            shader.bind(); shader.uniform_float("color", color); batch.draw(shader)

        lane_by_id = dict(lanes)
        name_by_id = {item.character_id: item.display_name for item in view.characters}
        for char_id, y in lanes:
            fill(((0, y + 11), (GUTTER_WIDTH, y + 11), (0, y - 11),
                  (GUTTER_WIDTH, y + 11), (GUTTER_WIDTH, y - 11), (0, y - 11)),
                 (.09, .12, .16, .91))
            blf.position(0, 6, y - 5, 0); blf.size(0, 12)
            blf.color(0, .9, .9, .93, 1)
            blf.draw(0, name_by_id[char_id][:24])
        # Secondary low-contrast ticks are derived, never selectable hard keys.
        for derived in getattr(view, "derived", ()):
            y = lane_by_id.get(derived.character_id)
            if y is None:
                continue
            x = region.view2d.view_to_region(
                derived.source_frame + frame_offset, 0, clip=False
            )[0]
            if GUTTER_WIDTH + 4 <= x <= region.width - 4:
                tint = (.42, .67, .74, .48) if derived.state == "visible" else (.5, .5, .56, .35)
                line(((x, y - 3), (x, y + 3)), tint)
        for key in view.keys:
            y = lane_by_id.get(key.character_id)
            if y is None:
                continue
            x = region.view2d.view_to_region(key.source_frame + frame_offset, 0, clip=False)[0]
            if x < GUTTER_WIDTH + 8 or x > region.width - 8:
                continue
            color = {
                "visible": (.4, .9, .4, 1),
                "partially-visible": (.4, .9, .4, 1),
                "occluded": (.95, .7, .15, 1),
                "no-valid-candidate": (.25, .8, 1, 1),
                "not-visible": (1, .45, .45, 1),
            }[key.state]
            color, pending = authoritative_key_style(key, color)
            if pending:
                line(((x-4,y-10),(x+4,y-10)),color)
            outline = selection_outline_style(key.keyframe_id, selected_ids, active_id)
            if outline is not None:
                line(((x - 10, y - 10), (x + 10, y - 10), (x + 10, y + 10),
                      (x - 10, y + 10), (x - 10, y - 10)),
                     (1, 1, 1, 1) if outline == "active" else (.65, .7, .76, .8))
                if outline == "active":
                    # Small upper-right corner notch distinguishes focus even
                    # in monochrome screenshots without overpowering the key.
                    line(((x + 4, y + 10), (x + 10, y + 10), (x + 10, y + 4)),
                         (1, 1, 1, 1))
            if key.is_assignment:  # filled diamond
                fill(((x, y + 7), (x + 7, y), (x, y - 7),
                      (x, y + 7), (x, y - 7), (x - 7, y)), color)
            elif key.state == "occluded":  # hollow circle
                line(tuple((x + dx, y + dy) for dx, dy in (
                    (0, 7), (5, 5), (7, 0), (5, -5), (0, -7),
                    (-5, -5), (-7, 0), (-5, 5), (0, 7))), color)
            elif key.state == "no-valid-candidate":  # hollow square
                line(((x - 6, y - 6), (x + 6, y - 6), (x + 6, y + 6),
                      (x - 6, y + 6), (x - 6, y - 6)), color)
            else:  # not visible: X
                line(((x - 6, y - 6), (x + 6, y + 6)), color)
                line(((x - 6, y + 6), (x + 6, y - 6)), color)
    except Exception:
        # The overlay is never allowed to break Blender's Sequencer draw.
        return
