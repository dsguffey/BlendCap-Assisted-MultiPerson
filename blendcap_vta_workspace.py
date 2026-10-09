"""Blender-independent scope and hit testing for a VTA Review Timeline area."""

from __future__ import annotations

import math
from dataclasses import dataclass

if __package__:
    from .blendcap_vta_timeline import hit_test_keys, lane_layout
    from .blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for
else:
    from blendcap_vta_timeline import hit_test_keys, lane_layout
    from blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for


WORKSPACE_NAME = "Video to Animation"
ROW_HEIGHT = 26.0
# Blender's native Dope Sheet reserves the top of WINDOW for its frame ruler.
# Keep the first VTA key below that event zone, not merely below our drawn text.
TOP_PADDING = 62.0


@dataclass(frozen=True)
class TimelineMetrics:
    """Logical sizes converted once to region pixels using Blender's UI scale."""

    ui_scale: float = 1.0
    text_size: float = 13.0

    @property
    def font_size(self):
        return self.text_size * self.ui_scale

    @property
    def row_height(self):
        return max(ROW_HEIGHT, self.text_size + 12) * self.ui_scale

    @property
    def top_padding(self):
        return max(TOP_PADDING, self.text_size * 2 + 18) * self.ui_scale

    @property
    def key_radius(self):
        return 7 * self.ui_scale

    @property
    def hit_radius(self):
        return 9 * self.ui_scale


def grid_step(frame_span, pixel_width, *, minimum_pixels=48):
    """Choose a 1/2/5 cadence with bounded line count at any zoom."""
    target = max(1.0, float(frame_span) * minimum_pixels / max(1, pixel_width))
    scale = 10 ** math.floor(math.log10(target))
    return next(value for value in (scale, 2 * scale, 5 * scale, 10 * scale)
                if value >= target)


def review_area(workspace, screen):
    if screen is None:
        return None
    areas = [area for area in screen.areas
             if role_for(workspace, screen, area) == ROLE_TIMELINE]
    return areas[0] if len(areas) == 1 else None


def is_review_region(workspace, screen, area, region):
    return (area is not None and area == review_area(workspace, screen)
            and getattr(region, "type", None) in {"WINDOW", "CHANNELS"})


def character_lanes(view, height, first=0, *, metrics=TimelineMetrics()):
    if view is None:
        return ()
    visible = max(1, int((height - metrics.top_padding) // metrics.row_height))
    first = min(max(0, first), max(0, len(view.characters) - visible))
    subset = type("VisibleRows", (), {"characters": view.characters[first:first + visible]})()
    return lane_layout(subset, top=height - metrics.top_padding,
                       row_height=metrics.row_height)


def hit_character(view, height, y, first=0, *, metrics=TimelineMetrics()):
    return next((character_id for character_id, center in
                 character_lanes(view, height, first, metrics=metrics)
                 if abs(center - y) <= metrics.row_height / 2), None)


def characters_in_box(view, height, start, end, first=0, *, metrics=TimelineMetrics()):
    """Channel-region marquee selects displayed row centers, never timeline keys."""
    low, high = sorted((start[1], end[1]))
    return tuple(cid for cid, center in character_lanes(view, height, first, metrics=metrics)
                 if low <= center <= high)


def derived_mark_color(state):
    """Categorical match/unresolved convention, not confidence or selection."""
    return (.55, .68, .72, .55) if state == 'visible' else (.48, .5, .52, .4)


def motion_interval_rects(view, view2d, width, height, offset=0, first=0,
                          *, metrics=TimelineMetrics()):
    """Paint-only inclusive frame cells; shares scaled Character row geometry."""
    lanes = dict(character_lanes(view, height, first, metrics=metrics))
    rectangles = []
    for interval in getattr(view, 'untrusted_motion_intervals', ()):
        y = lanes.get(interval.character_id)
        if y is None:
            continue
        x0 = view2d.view_to_region(interval.start_frame + offset - .5, 0, clip=False)[0]
        x1 = view2d.view_to_region(interval.end_frame + offset + .5, 0, clip=False)[0]
        low, high = sorted((x0, x1))
        if high < 0 or low > width:
            continue
        rectangles.append((interval.character_id, max(0, low), min(width, high),
                           y - metrics.row_height / 2 + metrics.ui_scale,
                           y + metrics.row_height / 2 - metrics.ui_scale))
    return tuple(rectangles)


def hit_review_key(view, view2d, height, point, offset=0, first=0,
                   *, metrics=TimelineMetrics()):
    if view is None:
        return None
    return hit_test_keys(
        view, character_lanes(view, height, first, metrics=metrics),
        frame_to_x=lambda frame: view2d.view_to_region(frame + offset, 0, clip=False)[0],
        point=point,
        radius=metrics.hit_radius,
    )


def review_keys_in_box(view, view2d, width, height, start, end, offset=0, first=0,
                       *, metrics=TimelineMetrics()):
    """Select visible durable-key centers, in either drag direction."""
    if view is None:
        return ()
    x0, x1 = sorted((start[0], end[0]))
    y0, y1 = sorted((start[1], end[1]))
    lanes = dict(character_lanes(view, height, first, metrics=metrics))
    selected = []
    for key in view.keys:
        y = lanes.get(key.character_id)
        x = view2d.view_to_region(key.source_frame + offset, 0, clip=False)[0]
        if (y is not None and 8 * metrics.ui_scale <= x <= width - 8 * metrics.ui_scale
                and x0 <= x <= x1 and y0 <= y <= y1):
            selected.append(key.keyframe_id)
    return tuple(sorted(selected))
