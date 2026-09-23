"""Presentation-only review lanes in the VSE timeline WINDOW region.

The lanes use a narrow VTA-owned pixel gutter and the Sequencer View2D for X.
No native channel, strip, playhead, or project authority is created.
"""

from __future__ import annotations

from ..blendcap_vta_timeline import hit_test_keys


GUTTER_WIDTH = 152
ROW_HEIGHT = 24.0
TOP_PADDING = 25.0
BOTTOM_PADDING = 14.0

_handler = None
_state_provider = None


def is_timeline_region(area, region, space) -> bool:
    return (
        getattr(area, "type", None) == 'SEQUENCE_EDITOR'
        and getattr(region, "type", None) == 'WINDOW'
        and getattr(space, "view_type", None) in {'SEQUENCER', 'SEQUENCER_PREVIEW'}
    )


def visible_lanes(view, region_height: int, scroll: int = 0):
    count = max(1, int((region_height - TOP_PADDING - BOTTOM_PADDING) // ROW_HEIGHT))
    selected = view.characters[max(0, scroll):max(0, scroll) + count]
    top = region_height - TOP_PADDING
    return tuple((item.character_id, top - ROW_HEIGHT * (index + .5))
                 for index, item in enumerate(selected))


def hit_key(view, view2d, region_height, point, frame_offset, scroll=0):
    lanes = visible_lanes(view, region_height, scroll)
    return hit_test_keys(
        view, lanes,
        frame_to_x=lambda frame: view2d.view_to_region(frame + frame_offset, 0, clip=False)[0],
        point=point,
    )


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
        view, selected_ids, frame_offset, scroll = _state_provider()
        if view is None:
            return
        lanes = visible_lanes(view, region.height, scroll)
        if not lanes:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        def line(vertices, color):
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": vertices})
            shader.bind(); shader.uniform_float("color", color); batch.draw(shader)

        def fill(vertices, color):
            batch = batch_for_shader(shader, 'TRIS', {"pos": vertices})
            shader.bind(); shader.uniform_float("color", color); batch.draw(shader)

        # VTA-owned gutter sits inside the timeline WINDOW. It never edits the
        # native channel-name region or changes strip/channel ownership.
        fill(((0, region.height), (GUTTER_WIDTH, region.height),
              (0, region.height - TOP_PADDING),
              (GUTTER_WIDTH, region.height), (GUTTER_WIDTH, region.height - TOP_PADDING),
              (0, region.height - TOP_PADDING)), (.07, .09, .12, .94))
        blf.position(0, 7, region.height - 18, 0)
        blf.size(0, 12); blf.color(0, .9, .9, .9, 1)
        blf.draw(0, "VTA REVIEW")
        lane_by_id = dict(lanes)
        name_by_id = {item.character_id: item.display_name for item in view.characters}
        for char_id, y in lanes:
            fill(((0, y + 11), (GUTTER_WIDTH, y + 11), (0, y - 11),
                  (GUTTER_WIDTH, y + 11), (GUTTER_WIDTH, y - 11), (0, y - 11)),
                 (.09, .12, .16, .91))
            line(((GUTTER_WIDTH, y - 12), (region.width, y - 12)), (.4, .4, .45, .5))
            blf.position(0, 6, y - 5, 0); blf.size(0, 12)
            blf.color(0, .9, .9, .93, 1)
            blf.draw(0, name_by_id[char_id][:24])
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
            if key.keyframe_id in selected_ids:
                line(((x - 10, y - 10), (x + 10, y - 10), (x + 10, y + 10),
                      (x - 10, y + 10), (x - 10, y - 10)), (1, 1, 1, 1))
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
