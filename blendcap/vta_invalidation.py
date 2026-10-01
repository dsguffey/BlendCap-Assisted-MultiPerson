"""One redraw notification for all VTA presentation surfaces.

This never queries or changes ProjectState.  Redrawing a stock editor does
not install VTA drawing there; each presentation layer has its own scope.
"""

SURFACE_TYPES = frozenset({"SEQUENCE_EDITOR", "DOPESHEET_EDITOR", "VIEW_3D"})


def invalidate_windows(windows):
    count = 0
    for window in windows:
        for area in window.screen.areas:
            if area.type in SURFACE_TYPES:
                area.tag_redraw()
                count += 1
    return count


def request_redraw():
    try:
        import bpy
        return invalidate_windows(bpy.context.window_manager.windows)
    except (AttributeError, RuntimeError):
        return 0
