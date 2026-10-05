"""Per-editor VTA modes and ProjectState-backed Dope Sheet presentation.

The bundled workspace is an optional preset. No Action, F-curve, Blender
keyframe, or tracking authority is created here.
"""

from __future__ import annotations

from pathlib import Path
import math

import bpy

from ..blendcap_vta_workspace import (
    WORKSPACE_NAME, WORKSPACE_TAG, TimelineMetrics, character_lanes, grid_step,
    hit_character, hit_review_key, is_review_region, review_area, review_keys_in_box,
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
_box_gesture = None


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
    # Extend the inline editor menus so the toggle follows Blender's Key menu.
    # Blender can also render this group as a collapsed menu in narrow headers.
    area = getattr(context, 'area', None)
    if area is None or area.type != 'DOPESHEET_EDITOR' or area.ui_type != 'DOPESHEET':
        return
    # Header draw contexts use the HEADER region, while _scope intentionally
    # accepts only the canvas regions. The editor role is the mode authority.
    active = role_for(getattr(context, 'workspace', None),
                      getattr(context, 'screen', None), area) == ROLE_TIMELINE
    self.layout.separator()
    self.layout.operator('blendcap.vta_toggle_editor_mode',
                         text='VTA Mode', icon='KEY_HLT', depress=active)
    if active:
        if _state(context)[0] is None:
            _ui().refresh(context)
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='VTA Commands')


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


def _metrics(context):
    system = getattr(getattr(context, 'preferences', None), 'system', None)
    # ui_scale already includes OS DPI and Blender's display scale. A background
    # Blender process may report zero because it has no initialized display.
    scale = getattr(system, 'ui_scale', 1.0)
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    return TimelineMetrics(ui_scale=scale, text_size=_text_size(context))


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
        metrics = _metrics(context)
        scale = metrics.ui_scale
        half_row = metrics.row_height / 2 - scale
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        _fill(shader, batch_for_shader, 0, 0, region.width, region.height,
              (.115, .115, .115, 1))
        if view is None:
            blf.position(0, 10 * scale, region.height - metrics.font_size - 6 * scale, 0)
            blf.size(0, metrics.font_size)
            blf.color(0, .9, .7, .5, 1)
            blf.draw(0, 'Review unavailable')
            return
        names = {item.character_id: item.display_name for item in view.characters}
        blf.position(0, 10 * scale, region.height - metrics.font_size - 6 * scale, 0)
        blf.size(0, metrics.font_size)
        blf.color(0, .75, .75, .75, 1)
        blf.draw(0, 'Characters')
        for character_id, y in character_lanes(view, region.height, first, metrics=metrics):
            active = character_id == current
            _fill(shader, batch_for_shader, 0, y - half_row, region.width, y + half_row,
                  (.17, .24, .30, 1) if active else (.16, .16, .16, 1))
            if active:
                _fill(shader, batch_for_shader, 0, y - half_row, 4 * scale, y + half_row,
                      (.95, .9, .45, 1))
            blf.position(0, 10 * scale, y - metrics.font_size * .35, 0)
            blf.size(0, metrics.font_size)
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
        metrics = _metrics(context)
        scale = metrics.ui_scale
        half_row = metrics.row_height / 2 - scale
        key_radius = metrics.key_radius
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        _fill(shader, batch_for_shader, 0, 0, region.width, region.height,
              (.105, .105, .105, 1))
        if view is None:
            return
        lanes = dict(character_lanes(view, region.height, first, metrics=metrics))
        # The Dope Sheet supplies View2D navigation, not visible native keys.
        # Draw a modest Blender-like ruler/grid above the ProjectState lanes.
        left = region.view2d.region_to_view(0, 0)[0]
        right = region.view2d.region_to_view(region.width, 0)[0]
        span = max(1.0, right - left)
        step = grid_step(span, region.width,
                         minimum_pixels=max(48, metrics.text_size * 3) * scale)
        first_grid = int(left // step) * step
        for tick in range(min(200, int((right - first_grid) / step) + 2)):
            frame = first_grid + tick * step
            x = region.view2d.view_to_region(frame, 0, clip=False)[0]
            _line(shader, batch_for_shader, ((x, 0), (x, region.height - 24 * scale)),
                  (.25, .25, .25, 1))
            import blf
            blf.position(0, x + 3 * scale, region.height - metrics.font_size - 4 * scale, 0)
            blf.size(0, metrics.font_size)
            blf.color(0, .72, .72, .72, 1)
            blf.draw(0, str(frame))
        for character_id, y in lanes.items():
            _fill(shader, batch_for_shader, 0, y - half_row, region.width, y + half_row,
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
                _line(shader, batch_for_shader, ((x, y - 3 * scale), (x, y + 3 * scale)),
                      (.55, .68, .72, .55) if mark.state == 'visible'
                      else (.48, .5, .52, .4))
        for key in view.keys:
            y = lanes.get(key.character_id)
            if y is None:
                continue
            x = frame_x(key.source_frame)
            if not 8 * scale <= x <= region.width - 8 * scale:
                continue
            color = {
                'visible': (.45, .95, .48, 1),
                'partially-visible': (.45, .95, .48, 1),
                'occluded': (1, .75, .25, 1),
                'no-valid-candidate': (.3, .8, 1, 1),
                'not-visible': (1, .48, .48, 1),
            }[key.state]
            if key.keyframe_id in selected:
                radius = 10 * scale
                _line(shader, batch_for_shader,
                      ((x - radius, y - radius), (x + radius, y - radius),
                       (x + radius, y + radius), (x - radius, y + radius),
                       (x - radius, y - radius)),
                      (1, 1, 1, 1) if key.keyframe_id == active
                      else (.7, .73, .77, .9))
            if key.is_assignment:
                batch = batch_for_shader(shader, 'TRIS', {"pos": (
                    (x, y + key_radius), (x + key_radius, y), (x, y - key_radius),
                    (x, y + key_radius), (x, y - key_radius), (x - key_radius, y),
                )})
                shader.bind(); shader.uniform_float('color', color); batch.draw(shader)
            else:
                radius = 6 * scale
                if key.state == 'occluded':
                    _line(shader, batch_for_shader, ((x - radius, y), (x, y + radius),
                          (x + radius, y), (x, y - radius), (x - radius, y)), color)
                elif key.state == 'no-valid-candidate':
                    _line(shader, batch_for_shader,
                          ((x - radius, y - radius), (x + radius, y - radius),
                           (x + radius, y + radius), (x - radius, y + radius),
                           (x - radius, y - radius)), color)
                else:
                    _line(shader, batch_for_shader, ((x - radius, y - radius),
                          (x + radius, y + radius)), color)
                    _line(shader, batch_for_shader, ((x - radius, y + radius),
                          (x + radius, y - radius)), color)
        gesture = _box_gesture
        if (gesture is not None and gesture['dragged'] and
                gesture['area'] == context.area and gesture['region'] == region):
            x0, y0 = gesture['start']
            x1, y1 = gesture['end']
            _line(shader, batch_for_shader,
                  ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)),
                  (.7, .85, 1, 1))
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
    bl_description = 'Click a key or drag empty canvas to select a group; Shift/Ctrl adds keys'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _scope(context)

    def invoke(self, context, event):
        global _box_gesture
        ui = _ui()
        view, _selected, _active, offset, first, _current = _state(context)
        if view is None:
            return {'PASS_THROUGH'}
        point = (event.mouse_region_x, event.mouse_region_y)
        metrics = _metrics(context)
        if context.region.type == 'CHANNELS':
            if event.type == 'LEFTMOUSE':
                character_id = hit_character(view, context.region.height, point[1], first,
                                              metrics=metrics)
                if character_id and ui.select_timeline_character(context, character_id):
                    return {'FINISHED'}
            return {'PASS_THROUGH'}
        key_id = hit_review_key(view, context.region.view2d,
                                context.region.height, point, offset, first, metrics=metrics)
        if key_id is None:
            # Leave Blender's frame ruler and non-left-click tools native.
            if (event.type == 'LEFTMOUSE' and
                    point[1] < context.region.height - 24 * metrics.ui_scale and
                    _box_gesture is None):
                self._gesture_view = view
                self._gesture_metrics = metrics
                self._gesture_offset = offset
                self._gesture_first = first
                self._gesture_extend = bool(event.ctrl or getattr(event, 'shift', False))
                self._gesture = dict(area=context.area, region=context.region,
                                     start=point, end=point, dragged=False)
                _box_gesture = self._gesture
                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        if ui.select_timeline_key(context, key_id,
                                  extend=bool(event.ctrl or getattr(event, 'shift', False)),
                                  activate=event.type == 'RIGHTMOUSE'):
            if event.type == 'RIGHTMOUSE':
                bpy.ops.wm.call_menu('INVOKE_DEFAULT', name=BLENDCAP_MT_vta_timeline_context.bl_idname)
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def _finish_box(self, result):
        global _box_gesture
        if _box_gesture is self._gesture:
            _box_gesture = None
        vta_invalidation.request_redraw()
        return {result}

    def cancel(self, _context):
        if hasattr(self, '_gesture'):
            self._finish_box('CANCELLED')

    def modal(self, context, event):
        gesture = self._gesture
        if (gesture is not _box_gesture or not _scope(context) or
                context.area != gesture['area'] or context.region != gesture['region'] or
                _state(context)[0] is not self._gesture_view or
                _metrics(context) != self._gesture_metrics):
            return self._finish_box('CANCELLED')
        if event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'}:
            return self._finish_box('CANCELLED')
        region = gesture['region']
        scale = self._gesture_metrics.ui_scale
        if event.type in {'MOUSEMOVE', 'LEFTMOUSE'}:
            x = getattr(event, 'mouse_x', region.x + event.mouse_region_x) - region.x
            y = getattr(event, 'mouse_y', region.y + event.mouse_region_y) - region.y
            gesture['end'] = (max(0, min(region.width, x)),
                              max(0, min(region.height - 24 * scale, y)))
            start = gesture['start']
            gesture['dragged'] |= math.hypot(x - start[0], y - start[1]) > 4 * scale
            vta_invalidation.request_redraw()
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if gesture['dragged']:
                ids = review_keys_in_box(
                    self._gesture_view, region.view2d, region.width, region.height,
                    gesture['start'], gesture['end'], self._gesture_offset,
                    self._gesture_first, metrics=self._gesture_metrics)
                _ui().select_timeline_keys(context, ids, extend=self._gesture_extend)
            return self._finish_box('FINISHED')
        return {'RUNNING_MODAL'}


def register():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered
    if _window_handler is None:
        _window_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_window, (), 'WINDOW', 'POST_PIXEL')
    if _channel_handler is None:
        _channel_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_channels, (), 'CHANNELS', 'POST_PIXEL')
    bpy.types.Scene.blendcap_vta_timeline_text_size = bpy.props.IntProperty(
        name='Timeline Text Size', description='VTA text size before Blender UI scaling; rows adapt to fit',
        default=13, min=10, max=32, update=_timeline_text_size_changed)
    if not _header_registered:
        bpy.types.DOPESHEET_MT_editor_menus.append(_draw_header)
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
                                       ("LEFTMOUSE", {"shift": True}),
                                       ("RIGHTMOUSE", {}))]
            _keymaps.append((keymap, items))


def unregister():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered, _box_gesture
    _box_gesture = None
    for keymap, items in _keymaps:
        for item in items:
            keymap.keymap_items.remove(item)
    _keymaps.clear()
    if hasattr(bpy.types.Scene, 'blendcap_vta_timeline_text_size'):
        del bpy.types.Scene.blendcap_vta_timeline_text_size
    if _header_registered:
        bpy.types.DOPESHEET_MT_editor_menus.remove(_draw_header)
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
