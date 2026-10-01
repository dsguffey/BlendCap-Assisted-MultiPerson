"""Provisional VTA workspace and ProjectState-backed Dope Sheet presentation.

Only a tagged workspace's unique Dope Sheet is a VTA timeline.  No Action,
F-curve, Blender keyframe, or tracking authority is created here.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..blendcap_vta_workspace import (
    WORKSPACE_NAME, WORKSPACE_TAG, ROW_HEIGHT, character_lanes,
    hit_character, hit_review_key, is_review_region, review_area,
)
from . import vta_invalidation


_window_handler = None
_channel_handler = None
_header_registered = False
_keymaps = []


def _ui():
    from . import vta_review_ui
    return vta_review_ui


def _scope(context):
    return is_review_region(
        getattr(context, "workspace", None), getattr(context, "screen", None),
        getattr(context, "area", None), getattr(context, "region", None),
    )


def _state(context):
    ui = _ui()
    view, selected, active, offset, first = ui.timeline_state()
    return view, selected, active, offset, first, ui._selection.character_id


def _draw_header(self, context):
    host = review_area(getattr(context, 'workspace', None),
                       getattr(context, 'screen', None))
    if host is not None and host == getattr(context, 'area', None):
        self.layout.separator()
        self.layout.label(text='VTA Review Timeline', icon='KEY_HLT')


def _line(shader, batch_for_shader, points, color):
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _fill(shader, batch_for_shader, x0, y0, x1, y1, color):
    batch = batch_for_shader(shader, 'TRIS', {"pos": (
        (x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1),
    )})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_channels():
    try:
        import blf, gpu
        from gpu_extras.batch import batch_for_shader
        context = bpy.context
        if not _scope(context) or context.region.type != 'CHANNELS':
            return
        view, _selected, _active, _offset, first, current = _state(context)
        if view is None:
            return
        region = context.region
        names = {item.character_id: item.display_name for item in view.characters}
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for character_id, y in character_lanes(view, region.height, first):
            active = character_id == current
            _fill(shader, batch_for_shader, 0, y - 12, region.width, y + 12,
                  (.17, .27, .34, .96) if active else (.12, .14, .17, .94))
            if active:
                _fill(shader, batch_for_shader, 0, y - 12, 4, y + 12,
                      (.95, .9, .45, 1))
            blf.position(0, 10, y - 5, 0)
            blf.size(0, 12)
            blf.color(0, 1, 1, 1, 1)
            blf.draw(0, names[character_id][:36])
    except Exception:
        # A presentation callback must never break stock Blender drawing.
        return


def _draw_window():
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        context = bpy.context
        if not _scope(context) or context.region.type != 'WINDOW':
            return
        view, selected, active, offset, first, current = _state(context)
        if view is None:
            return
        region = context.region
        lanes = dict(character_lanes(view, region.height, first))
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for character_id, y in lanes.items():
            _fill(shader, batch_for_shader, 0, y - 12, region.width, y + 12,
                  (.14, .18, .21, .36) if character_id == current
                  else (.08, .10, .12, .24))
        frame_x = lambda frame: region.view2d.view_to_region(
            frame + offset, 0, clip=False)[0]
        for mark in view.derived:
            y = lanes.get(mark.character_id)
            if y is None:
                continue
            x = frame_x(mark.source_frame)
            if 0 <= x < region.width:
                _line(shader, batch_for_shader, ((x, y - 3), (x, y + 3)),
                      (.55, .68, .72, .55) if mark.state == 'visible'
                      else (.48, .5, .52, .4))
        for key in view.keys:
            y = lanes.get(key.character_id)
            if y is None:
                continue
            x = frame_x(key.source_frame)
            if not 8 <= x <= region.width - 8:
                continue
            color = {
                'visible': (.45, .95, .48, 1),
                'partially-visible': (.45, .95, .48, 1),
                'occluded': (1, .75, .25, 1),
                'no-valid-candidate': (.3, .8, 1, 1),
                'not-visible': (1, .48, .48, 1),
            }[key.state]
            if key.keyframe_id in selected:
                _line(shader, batch_for_shader,
                      ((x - 10, y - 10), (x + 10, y - 10), (x + 10, y + 10),
                       (x - 10, y + 10), (x - 10, y - 10)),
                      (1, 1, 1, 1) if key.keyframe_id == active
                      else (.7, .73, .77, .9))
            if key.is_assignment:
                batch = batch_for_shader(shader, 'TRIS', {"pos": (
                    (x, y + 7), (x + 7, y), (x, y - 7),
                    (x, y + 7), (x, y - 7), (x - 7, y),
                )})
                shader.bind(); shader.uniform_float('color', color); batch.draw(shader)
            elif key.state == 'occluded':
                _line(shader, batch_for_shader, ((x - 6, y), (x, y + 6),
                      (x + 6, y), (x, y - 6), (x - 6, y)), color)
            elif key.state == 'no-valid-candidate':
                _line(shader, batch_for_shader, ((x - 6, y - 6), (x + 6, y - 6),
                      (x + 6, y + 6), (x - 6, y + 6), (x - 6, y - 6)), color)
            else:
                _line(shader, batch_for_shader, ((x - 6, y - 6),
                      (x + 6, y + 6)), color)
                _line(shader, batch_for_shader, ((x - 6, y + 6),
                      (x + 6, y - 6)), color)
    except Exception:
        return


class BLENDCAP_OT_open_vta_workspace(bpy.types.Operator):
    bl_idname = 'blendcap.open_vta_workspace'
    bl_label = 'Open VTA Workspace'
    bl_description = 'Open the provisional Video to Animation review workspace'

    def execute(self, context):
        matches = [item for item in bpy.data.workspaces if item.get(WORKSPACE_TAG) == 1]
        if len(matches) > 1:
            self.report({'ERROR'}, 'Multiple VTA workspaces are tagged; choose one manually.')
            return {'CANCELLED'}
        if matches:
            workspace = matches[0]
        else:
            if WORKSPACE_NAME in bpy.data.workspaces:
                self.report({'ERROR'}, 'A workspace named Video to Animation already exists; it was not replaced.')
                return {'CANCELLED'}
            path = Path(__file__).resolve().parent.parent / 'vta_workspace.blend'
            if not path.is_file():
                self.report({'ERROR'}, 'Bundled VTA workspace is unavailable.')
                return {'CANCELLED'}
            try:
                with bpy.data.libraries.load(str(path), link=False) as (source, dest):
                    if WORKSPACE_NAME not in source.workspaces:
                        raise ValueError('Bundled file has no VTA workspace.')
                    dest.workspaces = [WORKSPACE_NAME]
                workspace = dest.workspaces[0]
            except (OSError, RuntimeError, ValueError) as exc:
                self.report({'ERROR'}, f'Could not open VTA workspace: {exc}')
                return {'CANCELLED'}
            if workspace is None or workspace.get(WORKSPACE_TAG) != 1:
                self.report({'ERROR'}, 'Bundled workspace is missing its VTA scope tag.')
                return {'CANCELLED'}
        context.window.workspace = workspace
        vta_invalidation.request_redraw()
        self.report({'INFO'}, 'Video to Animation workspace opened. Layout is provisional.')
        return {'FINISHED'}


class BLENDCAP_MT_vta_timeline_context(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_context'
    bl_label = 'VTA Review Key'

    def draw(self, _context):
        self.layout.operator_context = 'INVOKE_DEFAULT'
        self.layout.operator('blendcap.vta_review_manage', text='Clear Review Key').action = 'clear-review-key'
        self.layout.operator('blendcap.vta_review_manage', text='Clear Selected Review Keys').action = 'clear-selected-review-keys'


class BLENDCAP_OT_vta_timeline_select(bpy.types.Operator):
    bl_idname = 'blendcap.vta_timeline_select'
    bl_label = 'Select VTA Timeline Item'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _scope(context)

    def invoke(self, context, event):
        ui = _ui()
        view, _selected, _active, offset, first, _current = _state(context)
        if view is None:
            return {'PASS_THROUGH'}
        point = (event.mouse_region_x, event.mouse_region_y)
        if context.region.type == 'CHANNELS':
            if event.type == 'LEFTMOUSE':
                character_id = hit_character(view, context.region.height, point[1], first)
                if character_id and ui.select_timeline_character(context, character_id):
                    return {'FINISHED'}
            return {'PASS_THROUGH'}
        key_id = hit_review_key(view, context.region.view2d,
                                context.region.height, point, offset, first)
        if key_id is None:
            return {'PASS_THROUGH'}
        if ui.select_timeline_key(context, key_id, extend=bool(event.ctrl),
                                  activate=event.type == 'RIGHTMOUSE'):
            if event.type == 'RIGHTMOUSE':
                bpy.ops.wm.call_menu('INVOKE_DEFAULT', name=BLENDCAP_MT_vta_timeline_context.bl_idname)
            return {'FINISHED'}
        return {'PASS_THROUGH'}


def register():
    global _window_handler, _channel_handler, _header_registered
    if _window_handler is None:
        _window_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_window, (), 'WINDOW', 'POST_PIXEL')
    if _channel_handler is None:
        _channel_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_channels, (), 'CHANNELS', 'POST_PIXEL')
    if not _header_registered:
        bpy.types.DOPESHEET_HT_header.append(_draw_header)
        _header_registered = True
    config = getattr(bpy.context.window_manager.keyconfigs, 'addon', None)
    if config is not None and not _keymaps:
        for region in ('WINDOW', 'CHANNELS'):
            keymap = config.keymaps.new(name='Dopesheet', space_type='DOPESHEET_EDITOR',
                                        region_type=region)
            items = [keymap.keymap_items.new(
                BLENDCAP_OT_vta_timeline_select.bl_idname, button, 'PRESS', **modifiers,
            ) for button, modifiers in (("LEFTMOUSE", {}),
                                       ("LEFTMOUSE", {"ctrl": True}),
                                       ("RIGHTMOUSE", {}))]
            _keymaps.append((keymap, items))


def unregister():
    global _window_handler, _channel_handler, _header_registered
    for keymap, items in _keymaps:
        for item in items:
            keymap.keymap_items.remove(item)
    _keymaps.clear()
    if _header_registered:
        bpy.types.DOPESHEET_HT_header.remove(_draw_header)
        _header_registered = False
    if _window_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_window_handler, 'WINDOW')
        _window_handler = None
    if _channel_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_channel_handler, 'CHANNELS')
        _channel_handler = None
