"""Blender-independent scope and hit testing for a VTA Review Timeline area."""

from __future__ import annotations

import math

if __package__:
    from .blendcap_vta_timeline import hit_test_keys, lane_layout
    from .blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for
else:
    from blendcap_vta_timeline import hit_test_keys, lane_layout
    from blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, role_for


WORKSPACE_NAME = "Video to Animation"
ROW_HEIGHT = 26.0
TOP_PADDING = 28.0


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


def character_lanes(view, height, first=0):
    if view is None:
        return ()
    visible = max(1, int((height - TOP_PADDING) // ROW_HEIGHT))
    first = min(max(0, first), max(0, len(view.characters) - visible))
    subset = type("VisibleRows", (), {"characters": view.characters[first:first + visible]})()
    return lane_layout(subset, top=height - TOP_PADDING, row_height=ROW_HEIGHT)


def hit_character(view, height, y, first=0):
    return next((character_id for character_id, center in character_lanes(view, height, first)
                 if abs(center - y) <= ROW_HEIGHT / 2), None)


def hit_review_key(view, view2d, height, point, offset=0, first=0):
    if view is None:
        return None
    return hit_test_keys(
        view, character_lanes(view, height, first),
        frame_to_x=lambda frame: view2d.view_to_region(frame + offset, 0, clip=False)[0],
        point=point,
    )
