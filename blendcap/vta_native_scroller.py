"""Keep Blender's unrelated channel scroller out of the MoCap row canvas.

The Dope Sheet computes its native vertical extent from native animation
channels, while the MoCap canvas paints ProjectState rows. Filter those native
channels only during MoCap Mode, and never serialize the temporary filter.
"""

from __future__ import annotations

import bpy

from ..blendcap_vta_area_mode import ROLE_TIMELINE, WORKSPACE_TAG, configured_role_for


_FILTER = '__STUDIOFREE_MOCAP_HIDE_NATIVE_CHANNELS__'
_saved = {}  # Editor pointer tuple -> (prior text, summary, invert flag).
_suspended = set()  # Temporarily restored for Blender's save writer.


def _pointer(value):
    return value.as_pointer() if hasattr(value, 'as_pointer') else id(value)


def _editors(workspaces=None, *, active_only=False):
    for workspace in bpy.data.workspaces if workspaces is None else workspaces:
        if workspace.get(WORKSPACE_TAG) != 1:
            continue
        if active_only and not getattr(workspace, 'blendcap_vta_mode_enabled', False):
            continue
        for screen in workspace.screens:
            for area in screen.areas:
                if configured_role_for(workspace, screen, area) != ROLE_TIMELINE:
                    continue
                yield ((_pointer(workspace), _pointer(screen), _pointer(area)),
                       area.spaces.active.dopesheet)


def activate(workspace=None):
    """Make the existing right-edge MoCap elevator the only visible one."""
    scope = None if workspace is None else (workspace,)
    for key, filters in _editors(scope, active_only=True):
        if key not in _saved:
            _saved[key] = (filters.filter_text if filters.filter_text != _FILTER else '',
                           filters.show_summary, filters.use_filter_invert)
        filters.filter_text = _FILTER
        filters.show_summary = False
        filters.use_filter_invert = False


def deactivate(workspace=None):
    scope = None if workspace is None else (workspace,)
    for key, filters in _editors(scope):
        previous = _saved.pop(key, None)
        if previous is None:
            continue
        if filters.filter_text == _FILTER:
            filters.filter_text = previous[0]
            filters.show_summary = previous[1]
            filters.use_filter_invert = previous[2]
        _suspended.discard(key)


@bpy.app.handlers.persistent
def _save_pre(*_args):
    # Store the user's normal Dope Sheet filters in the .blend, even if it is
    # saved with MoCap Mode active. The temporary search must not leak to disk.
    _suspended.clear()
    for key, filters in _editors():
        previous = _saved.get(key)
        if previous is not None and filters.filter_text == _FILTER:
            filters.filter_text, filters.show_summary, filters.use_filter_invert = previous
            _suspended.add(key)


@bpy.app.handlers.persistent
def _save_post(*_args):
    for key, filters in _editors(active_only=True):
        if key in _suspended:
            filters.filter_text = _FILTER
            filters.show_summary = False
            filters.use_filter_invert = False
    _suspended.clear()


@bpy.app.handlers.persistent
def _load_post(*_args):
    _saved.clear()
    _suspended.clear()
    activate()


def register():
    for handlers, callback in ((bpy.app.handlers.save_pre, _save_pre),
                               (bpy.app.handlers.save_post, _save_post),
                               (bpy.app.handlers.load_post, _load_post)):
        if callback not in handlers:
            handlers.append(callback)
    activate()  # Also covers enabling the addon in an already-open MoCap file.


def unregister():
    deactivate()
    for handlers, callback in ((bpy.app.handlers.save_pre, _save_pre),
                               (bpy.app.handlers.save_post, _save_post),
                               (bpy.app.handlers.load_post, _load_post)):
        if callback in handlers:
            handlers.remove(callback)
    _saved.clear()
    _suspended.clear()
