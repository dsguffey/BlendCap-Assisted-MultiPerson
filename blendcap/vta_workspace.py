"""Per-editor VTA modes and ProjectState-backed Dope Sheet presentation.

The bundled workspace is an optional preset. No Action, F-curve, Blender
keyframe, or tracking authority is created here.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..blendcap_vta_workspace import (
    WORKSPACE_NAME, WORKSPACE_TAG, ROW_HEIGHT, character_lanes, grid_step,
    hit_character, hit_review_key, is_review_region, review_area,
)
from ..blendcap_vta_area_mode import (
    ROLE_PREVIEW, ROLE_TIMELINE, initialize_workspace_preset, role_for, set_role,
)
from . import vta_invalidation


_window_handler = None
_channel_handler = None
_header_registered = False
_preview_header_registered = False
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
    area = getattr(context, 'area', None)
    if area is None or area.type != 'DOPESHEET_EDITOR' or area.ui_type != 'DOPESHEET':
        return
    # Header draw contexts use the HEADER region, while _scope intentionally
    # accepts only the canvas regions. The editor role is the mode authority.
    active = role_for(getattr(context, 'workspace', None),
                      getattr(context, 'screen', None), area) == ROLE_TIMELINE
    self.layout.separator()
    self.layout.operator('blendcap.vta_toggle_editor_mode',
                         text='Exit VTA Mode' if active else 'Enter VTA Mode',
                         icon='KEY_HLT' if active else 'KEY_DEHLT', depress=active)
    if active:
        if _state(context)[0] is None:
            _ui().refresh(context)
        self.layout.label(text='VTA MODE — Review Timeline', icon='KEY_HLT')
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='VTA Commands')
        self.layout.prop(context.space_data, 'show_region_ui',
                         text='VTA Sidebar', toggle=True)


def _draw_preview_header(self, context):
    area = getattr(context, 'area', None)
    space = getattr(context, 'space_data', None)
    if (area is None or area.type != 'SEQUENCE_EDITOR' or
            getattr(space, 'view_type', None) not in {'PREVIEW', 'SEQUENCER_PREVIEW'}):
        return
    active = role_for(context.workspace, context.screen, area) == ROLE_PREVIEW
    self.layout.separator()
    self.layout.operator('blendcap.vta_toggle_editor_mode',
                         text='Exit VTA Preview' if active else 'Enter VTA Preview',
                         icon='KEY_HLT' if active else 'KEY_DEHLT', depress=active)
    if active:
        self.layout.label(text='VTA MODE — Preview')


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


def _text_size(context):
    return getattr(getattr(context, 'scene', None),
                   'blendcap_vta_timeline_text_size', 13)


def _timeline_text_size_changed(_self, _context):
    vta_invalidation.request_redraw()


def _draw_channels():
    try:
        import blf, gpu
        from gpu_extras.batch import batch_for_shader
        context = bpy.context
        if not _scope(context) or context.region.type != 'CHANNELS':
            return
        view, _selected, _active, _offset, first, current = _state(context)
        region = context.region
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        _fill(shader, batch_for_shader, 0, 0, region.width, region.height,
              (.115, .115, .115, 1))
        if view is None:
            blf.position(0, 10, region.height - 19, 0)
            blf.size(0, _text_size(context))
            blf.color(0, .9, .7, .5, 1)
            blf.draw(0, 'Review unavailable')
            return
        names = {item.character_id: item.display_name for item in view.characters}
        blf.position(0, 10, region.height - 19, 0)
        blf.size(0, _text_size(context))
        blf.color(0, .75, .75, .75, 1)
        blf.draw(0, 'Characters')
        for character_id, y in character_lanes(view, region.height, first):
            active = character_id == current
            _fill(shader, batch_for_shader, 0, y - 12, region.width, y + 12,
                  (.17, .24, .30, 1) if active else (.16, .16, .16, 1))
            if active:
                _fill(shader, batch_for_shader, 0, y - 12, 4, y + 12,
                      (.95, .9, .45, 1))
            blf.position(0, 10, y - 5, 0)
            blf.size(0, _text_size(context))
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
        region = context.region
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        _fill(shader, batch_for_shader, 0, 0, region.width, region.height,
              (.105, .105, .105, 1))
        if view is None:
            return
        lanes = dict(character_lanes(view, region.height, first))
        # The Dope Sheet supplies View2D navigation, not visible native keys.
        # Draw a modest Blender-like ruler/grid above the ProjectState lanes.
        left = region.view2d.region_to_view(0, 0)[0]
        right = region.view2d.region_to_view(region.width, 0)[0]
        span = max(1.0, right - left)
        step = grid_step(span, region.width)
        first_grid = int(left // step) * step
        for tick in range(min(200, int((right - first_grid) / step) + 2)):
            frame = first_grid + tick * step
            x = region.view2d.view_to_region(frame, 0, clip=False)[0]
            _line(shader, batch_for_shader, ((x, 0), (x, region.height - 24)),
                  (.25, .25, .25, 1))
            import blf
            blf.position(0, x + 3, region.height - 17, 0)
            blf.size(0, _text_size(context))
            blf.color(0, .72, .72, .72, 1)
            blf.draw(0, str(frame))
        for character_id, y in lanes.items():
            _fill(shader, batch_for_shader, 0, y - 12, region.width, y + 12,
                  (.17, .24, .30, 1) if character_id == current
                  else (.15, .15, .15, 1))
        playhead = region.view2d.view_to_region(context.scene.frame_current, 0,
                                               clip=False)[0]
        if 0 <= playhead < region.width:
            _line(shader, batch_for_shader,
                  ((playhead, 0), (playhead, region.height)), (.38, .63, .95, 1))
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
            path = Path(__file__).resolve().parent.parent / 'vta_workspace_mode.blend'
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
        for screen in workspace.screens:
            initialize_workspace_preset(workspace, screen)
            for area in screen.areas:
                if role_for(workspace, screen, area) == ROLE_TIMELINE:
                    area.spaces.active.show_region_ui = True
        vta_invalidation.request_redraw()
        self.report({'INFO'}, 'Video to Animation workspace opened. Layout is provisional.')
        return {'FINISHED'}


class BLENDCAP_MT_vta_timeline_context(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_context'
    bl_label = 'VTA Review Key'

    def draw(self, _context):
        ui = _ui()
        row = self.layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        ui._draw_management_actions(
            self.layout, actions={'clear-review-key', 'clear-selected-review-keys'},
            availability=ui._action_availability(),
        )
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='More VTA Commands')


class BLENDCAP_MT_vta_timeline_commands(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_commands'
    bl_label = 'VTA Commands'

    def draw(self, context):
        ui = _ui()
        layout = self.layout
        layout.operator('blendcap.vta_review_refresh', text='Refresh Review')
        if not ui.refresh(context):
            layout.label(text='VTA review unavailable', icon='INFO')
            return
        ui._draw_management_actions(layout)
        row = layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        row = layout.row()
        row.enabled = ui._action_availability()['update-tracking']
        row.operator('blendcap.vta_update_tracking', text='Update Tracking')


class BLENDCAP_PT_vta_timeline(bpy.types.Panel):
    bl_idname = 'BLENDCAP_PT_vta_timeline'
    bl_label = 'VTA Review Timeline'
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'VTA'

    @classmethod
    def poll(cls, context):
        area = getattr(context, 'area', None)
        return (area is not None and
                role_for(getattr(context, 'workspace', None),
                         getattr(context, 'screen', None), area) == ROLE_TIMELINE)

    def draw(self, context):
        ui = _ui()
        ui.refresh(context)
        availability = ui._action_availability()
        layout = self.layout
        layout.label(text='VTA MODE — Review Timeline', icon='KEY_HLT')
        layout.label(text='Display')
        layout.prop(context.scene, 'blendcap_vta_timeline_text_size',
                    text='Timeline Text Size')
        layout.label(text=f'Selected keys: {len(ui._review_key_selection.keyframe_ids)}')
        if ui._selection.character_id and ui._timeline:
            character = next((item for item in ui._timeline.characters
                              if item.character_id == ui._selection.character_id), None)
            if character is not None:
                layout.label(text=f'Current Character: {character.display_name}')
        layout.separator()
        layout.label(text='Review Keys')
        row = layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        ui._draw_management_actions(layout, actions={
            'clear-review-key', 'clear-selected-review-keys',
            'clear-all-review-keys-on-frame',
        }, availability=availability)
        layout.separator()
        layout.label(text='Character')
        ui._draw_management_actions(layout, actions={
            'clear-assignment', 'rename-character', 'delete-character',
        }, availability=availability)
        layout.separator()
        row = layout.row()
        row.enabled = availability['update-tracking']
        row.operator('blendcap.vta_update_tracking', text='Update Tracking')
        layout.menu('BLENDCAP_MT_vta_timeline_commands', text='More VTA Commands')


class BLENDCAP_OT_vta_goto_review_key(bpy.types.Operator):
    bl_idname = 'blendcap.vta_goto_review_key'
    bl_label = 'Go to Review Key'
    bl_description = 'Explicitly move the current frame to the selected review key'

    @classmethod
    def poll(cls, context):
        ui = _ui()
        return (role_for(getattr(context, 'workspace', None),
                         getattr(context, 'screen', None),
                         getattr(context, 'area', None)) == ROLE_TIMELINE
                and ui._review_key_selection.active_keyframe_id is not None)

    def execute(self, context):
        if not _ui().go_to_selected_timeline_key(context):
            self.report({'WARNING'}, 'Select a VTA review key first.')
            return {'CANCELLED'}
        return {'FINISHED'}


class BLENDCAP_OT_vta_toggle_editor_mode(bpy.types.Operator):
    bl_idname = 'blendcap.vta_toggle_editor_mode'
    bl_label = 'Enter / Exit VTA Mode'
    bl_description = 'Toggle VTA presentation only in this editor area'

    @classmethod
    def poll(cls, context):
        area = getattr(context, 'area', None)
        if area is None:
            return False
        return ((area.type == 'DOPESHEET_EDITOR' and area.ui_type == 'DOPESHEET')
                or (area.type == 'SEQUENCE_EDITOR' and
                    getattr(getattr(context, 'space_data', None), 'view_type', None)
                    in {'PREVIEW', 'SEQUENCER_PREVIEW'}))

    def execute(self, context):
        area = context.area
        role = ROLE_TIMELINE if area.type == 'DOPESHEET_EDITOR' else ROLE_PREVIEW
        active = role_for(context.workspace, context.screen, area) == role
        try:
            set_role(context.workspace, context.screen, area, None if active else role)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not active and role == ROLE_TIMELINE:
            area.spaces.active.show_region_ui = True
            _ui().refresh(context, force=True)
        vta_invalidation.request_redraw()
        return {'FINISHED'}


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
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered
    if _window_handler is None:
        _window_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_window, (), 'WINDOW', 'POST_PIXEL')
    if _channel_handler is None:
        _channel_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_channels, (), 'CHANNELS', 'POST_PIXEL')
    bpy.types.Scene.blendcap_vta_timeline_text_size = bpy.props.IntProperty(
        name='Timeline Text Size', description='VTA canvas text size only',
        default=13, min=10, max=18, update=_timeline_text_size_changed)
    if not _header_registered:
        bpy.types.DOPESHEET_HT_header.append(_draw_header)
        _header_registered = True
    if not _preview_header_registered:
        bpy.types.SEQUENCER_HT_header.append(_draw_preview_header)
        _preview_header_registered = True
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
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered
    for keymap, items in _keymaps:
        for item in items:
            keymap.keymap_items.remove(item)
    _keymaps.clear()
    if hasattr(bpy.types.Scene, 'blendcap_vta_timeline_text_size'):
        del bpy.types.Scene.blendcap_vta_timeline_text_size
    if _header_registered:
        bpy.types.DOPESHEET_HT_header.remove(_draw_header)
        _header_registered = False
    if _preview_header_registered:
        bpy.types.SEQUENCER_HT_header.remove(_draw_preview_header)
        _preview_header_registered = False
    if _window_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_window_handler, 'WINDOW')
        _window_handler = None
    if _channel_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_channel_handler, 'CHANNELS')
        _channel_handler = None
