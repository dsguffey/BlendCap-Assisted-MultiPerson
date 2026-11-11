"""Blender-independent scope and hit testing for a VTA Review Timeline area."""

from __future__ import annotations

import math
from dataclasses import dataclass

if __package__:
    from .blendcap_vta_visuals import TRACKING_COLOR, UNRESOLVED_COLOR
    from .blendcap_vta_timeline import hit_test_keys, lane_layout
    from .blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for
else:
    from blendcap_vta_visuals import TRACKING_COLOR, UNRESOLVED_COLOR
    from blendcap_vta_timeline import hit_test_keys, lane_layout
    from blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for


WORKSPACE_NAME = "Video to Animation"
ROW_HEIGHT = 26.0
# Blender's native Dope Sheet reserves the top of WINDOW for its frame ruler.
# Keep the first VTA key below that event zone, not merely below our drawn text.
TOP_PADDING = 62.0
# Keep the marker lane at the bottom, just above the native scrollbar. Native
# marker events are routed through a scoped addon entry in Blender's Markers map.
SCROLLBAR_HEIGHT = 16.0
MARKER_HEIGHT = 28.0


@dataclass(frozen=True)
class TimelineMetrics:
    """Logical sizes converted once to region pixels using Blender's UI scale."""

    ui_scale: float = 1.0
    text_size: float = 13.0
    scroll_rows: float | None = None

    @property
    def bottom_padding(self):
        return (SCROLLBAR_HEIGHT + MARKER_HEIGHT) * self.ui_scale

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


@dataclass(frozen=True)
class ChannelRow:
    character_id: str
    kind: str
    y: float


def channel_items(view, expanded=()):
    expanded = frozenset(expanded)
    return tuple((c.character_id, kind) for c in getattr(view, 'characters', ())
                 for kind in (('character', 'depth') if c.character_id in expanded else ('character',)))


def channel_rows(view, height, first=0, *, metrics=TimelineMetrics(), expanded=()):
    """One shared geometry for painting and hit tests; first remains a Character index."""
    if view is None:
        return ()
    rows = channel_items(view, expanded)
    if not rows:
        return ()
    capacity = max(0.0, (height - metrics.top_padding - metrics.bottom_padding) / metrics.row_height)
    if capacity <= 0:return ()
    if metrics.scroll_rows is not None:
        position = min(max(0.0, metrics.scroll_rows), max(0.0, len(rows)-capacity))
        start = int(math.floor(position))
        stop = min(len(rows), int(math.ceil(position + capacity)))
        return tuple(ChannelRow(rows[i][0], rows[i][1],
                                height-metrics.top_padding-metrics.row_height*(i-position+.5))
                     for i in range(start, stop))
    visible = int(capacity)
    if not visible:return ()
    first = min(max(0, first), len(view.characters)-1)
    cid = view.characters[first].character_id
    start = next(i for i, (identity, kind) in enumerate(rows) if identity == cid and kind == 'character')
    start = min(start, max(0, len(rows)-visible))
    if rows[start][1] == 'depth':
        # Prefer the next complete Character group over backfilling an orphan.
        # Backtracking can otherwise hide the focused Character's Depth row
        # in a viewport with room for three rows but two expanded Characters.
        start += 1
    return tuple(ChannelRow(cid, kind, height-metrics.top_padding-metrics.row_height*(i+.5))
                 for i, (cid, kind) in enumerate(rows[start:start+visible]))


def character_lanes(view, height, first=0, *, metrics=TimelineMetrics(), expanded=()):
    return tuple((r.character_id, r.y) for r in channel_rows(
        view, height, first, metrics=metrics, expanded=expanded) if r.kind == 'character')


def hit_character(view, height, y, first=0, *, metrics=TimelineMetrics(), expanded=()):
    if not metrics.bottom_padding <= y <= height-metrics.top_padding:
        return None
    return next((character_id for character_id, center in
                 character_lanes(view, height, first, metrics=metrics, expanded=expanded)
                 if abs(center - y) <= metrics.row_height / 2), None)


def characters_in_box(view, height, start, end, first=0, *, metrics=TimelineMetrics(), expanded=()):
    """Channel-region marquee selects displayed row centers, never timeline keys."""
    low, high = sorted((start[1], end[1]))
    return tuple(cid for cid, center in character_lanes(view, height, first, metrics=metrics, expanded=expanded)
                 if low <= center <= high and metrics.bottom_padding <= center <= height-metrics.top_padding)


def derived_mark_color(state):
    """Categorical match/unresolved convention, not confidence or selection."""
    return TRACKING_COLOR if state in ('visible', 'partially-visible') else UNRESOLVED_COLOR


def authoritative_key_style(key, color):
    """Pending is valid human truth: cyan plus an underline, never a warning."""
    pending = getattr(key, 'tracking_state', 'pending') == 'pending'
    return ((.15, .92, .95, 1) if pending else color), pending


def repair_range_colors(mode):
    """Categorical repair colors, independent of identity/selection/confidence."""
    if mode == 'freeze':
        return (.34, .23, .10, 1), (.95, .70, .30, .85)
    if mode == 'interpolate':
        return (.25, .14, .36, 1), (.78, .53, .98, .85)
    raise ValueError('Unknown repair range mode')


def motion_interval_rects(view, view2d, width, height, offset=0, first=0,
                          *, metrics=TimelineMetrics(), include_interval=False, expanded=()):
    """Inclusive frame cells shared by painting and exact interval hit-testing."""
    lanes = dict(character_lanes(view, height, first, metrics=metrics, expanded=expanded))
    rectangles = []
    for interval in getattr(view, 'repair_ranges', ()):
        y = lanes.get(interval.character_id)
        if y is None:
            continue
        x0 = view2d.view_to_region(interval.start_frame + offset - .5, 0, clip=False)[0]
        x1 = view2d.view_to_region(interval.end_frame + offset + .5, 0, clip=False)[0]
        low, high = sorted((x0, x1))
        if high < 0 or low > width:
            continue
        rectangles.append((interval if include_interval else interval.character_id, max(0, low), min(width, high),
                           y - metrics.row_height / 2 + metrics.ui_scale,
                           y + metrics.row_height / 2 - metrics.ui_scale))
    return tuple(rectangles)


def hit_motion_interval(view, view2d, width, height, point, offset=0, first=0,
                        *, metrics=TimelineMetrics(), expanded=()):
    if not metrics.bottom_padding <= point[1] <= height-metrics.top_padding:
        return None
    # Keys take precedence at their local symbol hit area, even inside a repair range.
    if hit_review_key(view, view2d, height, point, offset, first, metrics=metrics, expanded=expanded) is not None:
        return None
    return next((interval for interval, x0, x1, y0, y1 in motion_interval_rects(
        view, view2d, width, height, offset, first, metrics=metrics, include_interval=True, expanded=expanded)
        if x0 <= point[0] <= x1 and y0 <= point[1] <= y1), None)


def hit_review_key(view, view2d, height, point, offset=0, first=0,
                   *, metrics=TimelineMetrics(), expanded=()):
    if view is None or not metrics.bottom_padding <= point[1] <= height-metrics.top_padding:
        return None
    return hit_test_keys(
        view, character_lanes(view, height, first, metrics=metrics, expanded=expanded),
        frame_to_x=lambda frame: view2d.view_to_region(frame + offset, 0, clip=False)[0],
        point=point,
        radius=metrics.hit_radius,
    )


def review_keys_in_box(view, view2d, width, height, start, end, offset=0, first=0,
                       *, metrics=TimelineMetrics(), expanded=()):
    """Select visible durable-key centers, in either drag direction."""
    if view is None:
        return ()
    x0, x1 = sorted((start[0], end[0]))
    y0, y1 = sorted((start[1], end[1]))
    lanes = dict(character_lanes(view, height, first, metrics=metrics, expanded=expanded))
    selected = []
    for key in view.keys:
        y = lanes.get(key.character_id)
        x = view2d.view_to_region(key.source_frame + offset, 0, clip=False)[0]
        if (y is not None and 8 * metrics.ui_scale <= x <= width - 8 * metrics.ui_scale
                and x0 <= x <= x1 and y0 <= y <= y1
                and metrics.bottom_padding <= y <= height-metrics.top_padding):
            selected.append(key.keyframe_id)
    return tuple(sorted(selected))
