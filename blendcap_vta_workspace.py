"""Blender-independent scope and hit testing for the provisional VTA workspace.

The workspace tag is durable; an Area pointer is never saved.  The unique
Dope Sheet in a tagged workspace is the review host.  An ambiguous layout
fails closed, leaving Blender's ordinary Dope Sheet untouched.
"""

from __future__ import annotations

if __package__:
    from .blendcap_vta_timeline import hit_test_keys, lane_layout
else:
    from blendcap_vta_timeline import hit_test_keys, lane_layout


WORKSPACE_NAME = "Video to Animation"
WORKSPACE_TAG = "blendcap_vta_review_workspace"
ROW_HEIGHT = 26.0
TOP_PADDING = 28.0


def review_area(workspace, screen):
    if workspace is None or screen is None or workspace.get(WORKSPACE_TAG) != 1:
        return None
    if not any(item is screen or item == screen for item in workspace.screens):
        return None
    areas = [area for area in screen.areas
             if getattr(area, "type", None) == "DOPESHEET_EDITOR"
             and getattr(area, "ui_type", None) == "DOPESHEET"]
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
