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
    motion_interval_rects, hit_motion_interval, characters_in_box, derived_mark_color, repair_range_colors,
)
from ..blendcap_vta_visuals import ASSIGNMENT_COLOR
from ..blendcap_vta_area_mode import (
    ROLE_PREVIEW, ROLE_TIMELINE, initialize_workspace_preset, role_for, set_role,
    configured_role_for,
)
from . import vta_invalidation


_window_handler = None
_channel_handler = None
_header_registered = False
_preview_header_registered = False
_camera_header_registered = False
_keymaps = []
_box_gesture = None
_blank_handler = None
_hover = None


def _ui():
    from . import vta_review_ui
    return vta_review_ui


def source_camera_view_state():
    """Snapshot only fields the generated-camera presentation changes."""
    state = []
    for workspace in bpy.data.workspaces:
        if workspace.get(WORKSPACE_TAG) != 1:
            continue
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    space = area.spaces.active
                    for owner, fields in ((space, ('use_local_camera', 'camera', 'show_object_viewport_armature')),
                            (space.overlay, ('show_overlays', 'show_bones')),
                            (space.region_3d, ('view_perspective', 'view_camera_offset', 'view_camera_zoom'))):
                        state.extend((owner, field, tuple(getattr(owner, field)) if field == 'view_camera_offset'
                                      else getattr(owner, field)) for field in fields)
    return state


def configure_source_camera_views(scene, workspaces=None):
    """Only the opt-in VTA workspace's 3D views present the owned source camera.

    The video Preview remains a video/review surface, not a 3D compositor.
    Keep the user's sidebar visibility and all ordinary workspaces unchanged.
    """
    camera = scene.camera
    if camera is None or not camera.get('vta_source_id') or not camera.get('vta_project_id'):
        return 0
    count = 0
    for workspace in bpy.data.workspaces if workspaces is None else workspaces:
        if workspace.get(WORKSPACE_TAG) != 1:
            continue
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                space = area.spaces.active
                space.use_local_camera = True
                space.camera = camera
                # The preset hides overlays for its old empty 3D placeholder.
                # Blender draws both armatures AND camera backgrounds through
                # the overlay engine; camera selection alone cannot show them.
                space.overlay.show_overlays = True
                space.overlay.show_bones = True
                space.show_object_viewport_armature = True
                space.region_3d.view_perspective = 'CAMERA'
                space.region_3d.view_camera_offset = (0, 0)
                space.region_3d.view_camera_zoom = 0
                count += 1
    return count


def snap_source_camera_view(context):
    """Return the requested viewport to the owned camera; never align/move it."""
    cameras = [o for o in context.scene.objects if o.type == 'CAMERA'
               and o.get('vta_project_id') and o.get('vta_source_id')]
    active = context.scene.camera
    if active in cameras:
        cameras = [o for o in cameras if o.get('vta_project_id') == active.get('vta_project_id')
                   and o.get('vta_source_id') == active.get('vta_source_id')]
    if len(cameras) != 1:
        raise ValueError('Source camera unavailable or ambiguous. Generate animation with its source camera retained.')
    area = getattr(context, 'area', None)
    if area is not None and area.type == 'VIEW_3D':
        areas = [area]
    elif getattr(context, 'workspace', None) and context.workspace.get(WORKSPACE_TAG) == 1:
        areas = [a for a in context.screen.areas if a.type == 'VIEW_3D']
    else:
        areas = []
    if not areas:
        raise ValueError('Use Snap to Source Camera in a 3D viewport or the VTA workspace.')
    if any(a.spaces.active.local_view is not None for a in areas):
        raise ValueError('Exit Local View (/) before snapping to the source camera.')
    for target in areas:
        space = target.spaces.active
        space.use_local_camera = True
        space.camera = cameras[0]
        space.region_3d.view_perspective = 'CAMERA'
        space.region_3d.view_camera_offset = (0, 0)
        space.region_3d.view_camera_zoom = 0
        space.overlay.show_overlays = True
        space.overlay.show_bones = True
        space.show_object_viewport_armature = True
        target.tag_redraw()
    return len(areas)




class BLENDCAP_OT_vta_snap_source_camera(bpy.types.Operator):
    bl_idname = 'blendcap.vta_snap_source_camera'
    bl_label = 'Snap to Source Camera'
    bl_description = 'Restore the source-video camera view after orbiting; does not move the camera or Characters'

    @classmethod
    def poll(cls, context):
        if not _ui().loaded_context_matches(context):
            return False
        scene = getattr(context, 'scene', None)
        return bool(scene and any(o.type == 'CAMERA' and o.get('vta_project_id')
                                  and o.get('vta_source_id') for o in scene.objects))

    def execute(self, context):
        try:
            _ui().require_loaded_context(context)
            snap_source_camera_view(_ui()._context_for_main_scene(context))
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def _draw_camera_header(self, context):
    if getattr(context, 'workspace', None) and context.workspace.get(WORKSPACE_TAG) == 1:
        self.layout.operator('blendcap.vta_snap_source_camera', text='Camera View', icon='CAMERA_DATA')


def blank_animation_guidance(context):
    """Cheap presentation only: never create objects or load project state."""
    if (getattr(context, 'area', None) is None or context.area.type != 'VIEW_3D'
            or not getattr(context, 'workspace', None) or context.workspace.get(WORKSPACE_TAG) != 1
            or not _ui().loaded_context_matches(context)):
        return ()
    info = _ui().cached_scene_source(context)
    if not info:
        return ()
    project_id, source_id = info[0], info[1]['source_id']
    if any(o.type == 'ARMATURE' and o.get('vta_project_id') == project_id
           and o.get('vta_source_id') == source_id for o in context.scene.objects):
        return ()
    return ('No animation generated yet.',
            'Set Animation Start / End and click Generate Animation Range.')


def _draw_blank_animation():
    lines = blank_animation_guidance(bpy.context)
    if not lines:
        return
    import blf
    region = bpy.context.region
    if region is None or region.type != 'WINDOW':
        return
    scale = _metrics(bpy.context).ui_scale
    space = bpy.context.space_data
    left = max((r.width for r in bpy.context.area.regions if r.type == 'TOOLS'), default=0) if space.show_region_toolbar else 0
    right = max((r.width for r in bpy.context.area.regions if r.type == 'UI'), default=0) if space.show_region_ui else 0
    draw_blank_guidance(region.width, region.height, lines, scale=scale, left=left, right=right)


def draw_blank_guidance(width, height, lines, *, scale=1, left=0, right=0, draw=None):
    """Real font metrics, wrapped and centred away from headers/overlay panels.

    `draw` allows the same layout/BLF font to be raster-verified headlessly.
    """
    import blf
    scale = max(.5, min(scale, 4))
    x0, x1 = left + 24 * scale, width - right - 24 * scale
    available = x1 - x0
    if available < 80 * scale or height < 100 * scale:
        return []  # Sidebar repeats guidance if the usable viewport is too small.
    rows = []
    for index, message in enumerate(lines):
        size = (18 if index == 0 else 15) * scale
        blf.size(0, size)
        words = message.split(); line = ''
        for word in words:
            candidate = (line + ' ' + word).strip()
            if line and blf.dimensions(0, candidate)[0] > available:
                rows.append((line, size)); line = word
            else:
                line = candidate
        if line:
            rows.append((line, size))
    spacing = 26 * scale
    y = height * .5 + (len(rows) - 1) * spacing * .5
    placements = []
    for text, size in rows:
        blf.size(0, size)
        measured, _ = blf.dimensions(0, text)
        x = x0 + (available - measured) * .5
        blf.position(0, x, y, 0)
        blf.color(0, 1, 1, 1, 1)
        (draw or blf.draw)(0, text)
        placements.append((text, x, y, measured, size))
        y -= spacing
    return placements


def snap_first_output(context, scene, first_output, workspace):
    if not first_output or getattr(context, 'scene', None) != scene or getattr(context, 'workspace', None) != workspace:
        return 0
    if workspace is None or workspace.get(WORKSPACE_TAG) != 1:
        return 0
    if any(a.type == 'VIEW_3D' and a.spaces.active.local_view is not None for a in context.screen.areas):
        return 0  # Never hijack isolated/user-changed context.
    return configure_source_camera_views(scene, (workspace,))


def _scope(context):
    return is_review_region(
        getattr(context, "workspace", None), getattr(context, "screen", None),
        getattr(context, "area", None), getattr(context, "region", None),
    )


def _state(context):
    ui = _ui()
    view, selected, active, offset, first = ui.timeline_state(context)
    return view, selected, active, offset, first, ui.current_character_id(context)


def _row_style(context, character_id, active):
    """Use the native list-item theme, with a stronger active highlight."""
    selection = _ui().timeline_character_selection(context)
    selected = bool(selection and character_id in selection.character_ids)
    theme = context.preferences.themes[0]
    item = theme.user_interface.wcol_list_item
    # List-item inner is transparent in Blender's default theme. Our custom
    # opaque canvas cannot discard its alpha (transparent white becomes white).
    background = tuple(theme.dopesheet_editor.space.back)
    composite = lambda color: tuple(color[i] * color[3] + background[i] * (1 - color[3])
                                    for i in range(3)) + (1,)
    normal = composite(item.inner)
    highlight = composite(item.inner_sel)
    if character_id == active and selected:
        return highlight, tuple(item.text_sel) + (1,), tuple(item.outline_sel)
    if selected:
        muted = tuple(normal[i] * .35 + highlight[i] * .65 for i in range(3)) + (1,)
        return muted, tuple(item.text_sel) + (1,), None
    return normal, tuple(item.text) + (1,), None


def _draw_gesture(context, shader, batch):
    gesture = _box_gesture
    if (gesture is not None and gesture['dragged'] and
            gesture['area'] == context.area and gesture['region'] == context.region):
        x0, y0 = gesture['start']
        x1, y1 = gesture['end']
        _line(shader, batch, ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)),
              (.7, .85, 1, 1))


def _draw_header(self, context):
    # Extend the inline editor menus so the toggle follows Blender's Key menu.
    # Blender can also render this group as a collapsed menu in narrow headers.
    area = getattr(context, 'area', None)
    if area is None or area.type != 'DOPESHEET_EDITOR' or area.ui_type != 'DOPESHEET':
        return
    # Header draw contexts use the HEADER region, while _scope intentionally
    # accepts only the canvas regions. The editor role is the mode authority.
    from .vta_scene_session import enabled
    active = enabled(getattr(context, 'workspace', None))
    self.layout.separator()
    self.layout.operator('blendcap.vta_toggle_editor_mode',
                         text='Exit VTA Mode' if active else 'Enter VTA Mode', icon='KEY_HLT', depress=active)
    self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='VTA Commands')


def _draw_preview_header(self, context):
    area = getattr(context, 'area', None)
    space = getattr(context, 'space_data', None)
    if (area is None or area.type != 'SEQUENCE_EDITOR' or
            getattr(space, 'view_type', None) not in {'PREVIEW', 'SEQUENCER_PREVIEW'}):
        return
    active = role_for(context.workspace, context.screen, area) == ROLE_PREVIEW
    if active and not _ui().loaded_context_matches(context):
        self.layout.label(text='VTA mismatch — Load VTA Scene to restore', icon='INFO')


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
            background, text, border = _row_style(context, character_id, current)
            _fill(shader, batch_for_shader, 0, y - half_row, region.width, y + half_row,
                  background)
            if border is not None:
                _fill(shader, batch_for_shader, 0, y - half_row, 4 * scale, y + half_row,
                      border)
            blf.position(0, 10 * scale, y - metrics.font_size * .35, 0)
            blf.size(0, metrics.font_size)
            blf.color(0, *text)
            blf.draw(0, names[character_id][:36])
        _draw_gesture(context, shader, batch_for_shader)
        _draw_hover(context)
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
            background, _text, _border = _row_style(context, character_id, current)
            _fill(shader, batch_for_shader, 0, y - half_row, region.width, y + half_row,
                  background)
        playhead = region.view2d.view_to_region(context.scene.frame_current, 0,
                                               clip=False)[0]
        frame_x = lambda frame: region.view2d.view_to_region(
            frame + offset, 0, clip=False)[0]
        # Exact range inspection is transient; identity keys retain hit priority.
        selected_repair = _ui().selected_repair_range(context)
        for interval, x0, x1, y0, y1 in motion_interval_rects(
                view, region.view2d, region.width, region.height, offset, first,
                metrics=metrics, include_interval=True):
            fill_color, line_color = repair_range_colors(interval.mode)
            _fill(shader, batch_for_shader, x0, y0, x1, y1, fill_color)
            _line(shader, batch_for_shader, ((x0, y0), (x0, y1), (x1, y1), (x1, y0)),
                  line_color)
            # Bounded slash strokes distinguish rejected ranges without color.
            spacing = max(12 * scale, (x1 - x0) / 160)
            for index in range(min(161, int((x1 - x0) / spacing) + 1)):
                x = x0 + index * spacing
                _line(shader, batch_for_shader,
                      ((x, y0), (min(x1, x + 7 * scale), y1)), line_color)
            if x1 - x0 > metrics.font_size * 13:
                import blf
                blf.position(0, x0 + 5 * scale, y0 + 3 * scale, 0)
                blf.size(0, metrics.font_size)
                blf.color(0, *line_color)
                blf.draw(0, interval.mode.upper())
            if interval == selected_repair:
                inset = min(2 * scale, (x1 - x0) / 4, (y1 - y0) / 4)
                highlight = (*context.preferences.themes[0].user_interface.wcol_list_item.text_sel, 1)
                _line(shader, batch_for_shader,
                      ((x0 + inset, y0 + inset), (x0 + inset, y1 - inset),
                       (x1 - inset, y1 - inset), (x1 - inset, y0 + inset),
                       (x0 + inset, y0 + inset)), highlight)
        if 0 <= playhead < region.width:
            _line(shader, batch_for_shader,
                  ((playhead, 0), (playhead, region.height)), (.38, .63, .95, 1))
        for mark in view.derived:
            y = lanes.get(mark.character_id)
            if y is None:
                continue
            x = frame_x(mark.source_frame)
            if 0 <= x < region.width:
                _line(shader, batch_for_shader, ((x, y - 3 * scale), (x, y + 3 * scale)),
                      derived_mark_color(mark.state))
        for key in view.keys:
            y = lanes.get(key.character_id)
            if y is None:
                continue
            x = frame_x(key.source_frame)
            if not 8 * scale <= x <= region.width - 8 * scale:
                continue
            color = {
                'visible': ASSIGNMENT_COLOR,
                'partially-visible': ASSIGNMENT_COLOR,
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
        _draw_gesture(context, shader, batch_for_shader)
        _draw_hover(context)
    except Exception:
        return


def get_vta_workspace(context):
    """Find/open the dedicated preset without choosing or activating a Scene."""
    if context.workspace.get(WORKSPACE_TAG) == 1:
        return context.workspace
    matches = [w for w in bpy.data.workspaces if w.get(WORKSPACE_TAG) == 1]
    if len(matches) > 1:
        raise ValueError('Multiple VTA workspaces are tagged; choose one manually.')
    if matches:
        return matches[0]
    if WORKSPACE_NAME in bpy.data.workspaces:
        raise ValueError('A workspace named Video to Animation already exists; it was not replaced.')
    path = Path(__file__).resolve().parent.parent / 'vta_workspace_mode.blend'
    with bpy.data.libraries.load(str(path), link=False) as (source, dest):
        if WORKSPACE_NAME not in source.workspaces:
            raise ValueError('Bundled file has no VTA workspace.')
        dest.workspaces = [WORKSPACE_NAME]
    workspace = dest.workspaces[0]
    if workspace is None or workspace.get(WORKSPACE_TAG) != 1:
        raise ValueError('Bundled workspace is missing its VTA scope tag.')
    return workspace


class BLENDCAP_OT_open_vta_workspace(bpy.types.Operator):
    bl_idname = 'blendcap.open_vta_workspace'
    bl_label = 'Open VTA Workspace'
    bl_description = 'Open the provisional Video to Animation review workspace'

    def execute(self, context):
        try:
            workspace = get_vta_workspace(context)
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        context.window.workspace = workspace
        # Open the layout only. Enter/Load/New are the explicit Scene actions.
        vta_invalidation.request_redraw()
        self.report({'INFO'}, 'Video to Animation workspace opened. Layout is provisional.')
        return {'FINISHED'}


class BLENDCAP_MT_vta_timeline_context(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_context'
    bl_label = 'VTA Review Key'

    def draw(self, context):
        ui = _ui()
        row = self.layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        ui._draw_management_actions(
            self.layout, actions={'clear-review-key', 'clear-selected-review-keys'},
            availability=ui._action_availability(context),
        )
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='More VTA Commands')


class BLENDCAP_MT_vta_timeline_commands(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_commands'
    bl_label = 'VTA Commands'

    def draw(self, context):
        ui = _ui()
        layout = self.layout
        ui.draw_scene_workflow(layout, context)
        layout.operator('blendcap.vta_review_refresh', text='Refresh Review')
        layout.operator('blendcap.vta_snap_source_camera', text='Snap to Source Camera', icon='CAMERA_DATA')
        ui.draw_scene_timing(layout, context, menu=True)
        if not ui.refresh(context):
            layout.label(text='VTA review unavailable', icon='INFO')
            return
        ui._draw_management_actions(layout, availability=ui._action_availability(context))
        row = layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        row = layout.row()
        row.enabled = ui._action_availability(context)['update-tracking']
        row.operator('blendcap.vta_update_tracking', text='Update Tracking')
        ui.draw_operation_feedback(layout, context, 'tracking')
        layout.separator()
        ui.draw_cleanup_commands(layout, context, menu=True)


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
        availability = ui._action_availability(context)
        layout = self.layout
        layout.label(text='Display')
        layout.prop(context.scene, 'blendcap_vta_timeline_text_size',
                    text='Timeline Text Size')
        layout.label(text=f'Selected keys: {len(ui._review_key_selection.keyframe_ids)}')
        selection = ui.timeline_character_selection(context)
        layout.label(text=f'Selected Characters: {len(selection.character_ids) if selection else 0}')
        if ui.current_character_id(context) and ui._timeline:
            character = next((item for item in ui._timeline.characters
                              if item.character_id == ui.current_character_id(context)), None)
            if character is not None:
                layout.label(text=f'Active Character: {character.display_name}')
        layout.label(text='Tracking ticks: bright = matched')
        layout.label(text='Grey = unresolved; not confidence')
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

        layout.separator()
        ui.draw_motion_trust(layout, context)


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
    bl_description = 'Enter the remembered VTA Scene or exit review without changing data'

    @classmethod
    def poll(cls, context):
        area = getattr(context, 'area', None)
        if area is None:
            return False
        return area.type == 'DOPESHEET_EDITOR' and area.ui_type == 'DOPESHEET'

    def execute(self, context):
        try:
            from .vta_scene_session import enabled, enter, exit_mode
            if enabled(context.workspace):
                exit_mode(context)
            else:
                enter(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        vta_invalidation.request_redraw()
        return {'FINISHED'}


def timeline_tooltip(context, point):
    view, _keys, _active, offset, first, _current = _state(context)
    if view is None:
        return ''
    metrics = _metrics(context)
    if context.region.type == 'CHANNELS':
        cid = hit_character(view, context.region.height, point[1], first, metrics=metrics)
        return 'Character row — Selected rows target tracking, repairs and selected generation.' if cid else ''
    if hit_review_key(view, context.region.view2d, context.region.height, point, offset, first, metrics=metrics):
        return 'Hard review key — Explicit Character assignment or review decision at this frame.'
    interval = hit_motion_interval(view, context.region.view2d, context.region.width,
        context.region.height, point, offset, first, metrics=metrics)
    if interval:
        return ('Freeze repair — Hold the preceding trustworthy unrepaired pose.' if interval.mode == 'freeze'
                else 'Interpolate repair — Blend raw anchors outside both endpoints; repaired frames never anchor.')
    lanes = dict(character_lanes(view, context.region.height, first, metrics=metrics))
    for mark in view.derived:
        x = context.region.view2d.view_to_region(mark.source_frame + offset, 0, clip=False)[0]
        if mark.character_id in lanes and abs(x - point[0]) <= 4 * metrics.ui_scale and abs(lanes[mark.character_id] - point[1]) <= 5 * metrics.ui_scale:
            return 'Derived tracking mark — Automatic continuation, not a human review key or generated pose.'
    return ''


def _draw_hover(context):
    if _hover is None or _hover[:2] != (context.area.as_pointer(), context.region.as_pointer()):
        return
    import blf
    text = _hover[2]
    blf.size(0, 12)
    blf.position(0, 10, 8, 0)
    blf.color(0, 1, .95, .8, 1)
    blf.draw(0, text)


class BLENDCAP_OT_vta_timeline_hover(bpy.types.Operator):
    bl_idname = 'blendcap.vta_timeline_hover'
    bl_label = 'VTA Timeline Tooltip'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _scope(context)

    def invoke(self, context, event):
        global _hover
        if _box_gesture is not None:
            return {'PASS_THROUGH'}
        text = timeline_tooltip(context, (event.mouse_region_x, event.mouse_region_y))
        value = (context.area.as_pointer(), context.region.as_pointer(), text) if text else None
        if value != _hover:
            _hover = value
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


class BLENDCAP_OT_vta_timeline_select(bpy.types.Operator):
    bl_idname = 'blendcap.vta_timeline_select'
    bl_label = 'Select VTA Timeline Item'
    bl_description = 'Select Character rows or review keys; drag within their own region to box-select'
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
            if (event.type != 'LEFTMOUSE' or _box_gesture is not None or
                    point[1] >= context.region.height - 24 * metrics.ui_scale):
                return {'PASS_THROUGH'}
        key_id = (None if context.region.type == 'CHANNELS' else hit_review_key(view, context.region.view2d,
                                context.region.height, point, offset, first, metrics=metrics)
                  )
        if key_id is None:
            if context.region.type == 'WINDOW' and event.type == 'LEFTMOUSE' and _box_gesture is None:
                interval = hit_motion_interval(view, context.region.view2d,
                                               context.region.width, context.region.height,
                                               point, offset, first, metrics=metrics)
                if interval is not None and ui.select_repair_range(context, interval):
                    return {'FINISHED'}
            # Leave Blender's frame ruler and non-left-click tools native.
            if (event.type == 'LEFTMOUSE' and
                    point[1] < context.region.height - 24 * metrics.ui_scale and
                    _box_gesture is None):
                self._gesture_view = view
                self._gesture_metrics = metrics
                self._gesture_offset = offset
                self._gesture_first = first
                self._gesture_extend = bool(event.ctrl or getattr(event, 'shift', False))
                self._gesture_characters = context.region.type == 'CHANNELS'
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
                if self._gesture_characters:
                    ids = characters_in_box(self._gesture_view, region.height,
                        gesture['start'], gesture['end'], self._gesture_first,
                        metrics=self._gesture_metrics)
                    _ui().select_timeline_characters(context, ids, extend=self._gesture_extend)
                else:
                    ids = review_keys_in_box(
                        self._gesture_view, region.view2d, region.width, region.height,
                        gesture['start'], gesture['end'], self._gesture_offset,
                        self._gesture_first, metrics=self._gesture_metrics)
                    _ui().select_timeline_keys(context, ids, extend=self._gesture_extend)
            elif self._gesture_characters:
                cid = hit_character(self._gesture_view, region.height, gesture['start'][1],
                                    self._gesture_first, metrics=self._gesture_metrics)
                if cid is not None:
                    _ui().select_timeline_character(context, cid, extend=self._gesture_extend)
                else:
                    _ui().select_timeline_characters(context, (), extend=self._gesture_extend)
            return self._finish_box('FINISHED')
        return {'RUNNING_MODAL'}


def register():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered, _camera_header_registered, _blank_handler
    if _blank_handler is None:
        _blank_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_blank_animation, (), 'WINDOW', 'POST_PIXEL')
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
    if not _camera_header_registered:
        bpy.types.VIEW3D_HT_header.append(_draw_camera_header)
        _camera_header_registered = True
    config = getattr(bpy.context.window_manager.keyconfigs, 'addon', None)
    if config is not None and not _keymaps:
        # CHANNELS dispatches Animation Channels (EMPTY/WINDOW), not a
        # Dopesheet/CHANNELS map. Operator poll still limits it to VTA rows.
        for name, space in (('Dopesheet', 'DOPESHEET_EDITOR'), ('Animation Channels', 'EMPTY')):
            keymap = config.keymaps.new(name=name, space_type=space, region_type='WINDOW')
            items = [keymap.keymap_items.new(
                BLENDCAP_OT_vta_timeline_select.bl_idname, button, 'PRESS', **modifiers,
            ) for button, modifiers in (("LEFTMOUSE", {}),
                                       ("LEFTMOUSE", {"ctrl": True}),
                                       ("LEFTMOUSE", {"shift": True}),
                                       ("RIGHTMOUSE", {}))]
            _keymaps.append((keymap, items))
            items.append(keymap.keymap_items.new(BLENDCAP_OT_vta_timeline_hover.bl_idname, 'MOUSEMOVE', 'ANY'))


def unregister():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered, _box_gesture, _camera_header_registered, _blank_handler, _hover
    _hover = None
    if _blank_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_blank_handler, 'WINDOW')
        _blank_handler = None
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
    if _camera_header_registered:
        bpy.types.VIEW3D_HT_header.remove(_draw_camera_header)
        _camera_header_registered = False
    if _window_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_window_handler, 'WINDOW')
        _window_handler = None
    if _channel_handler is not None:
        bpy.types.SpaceDopeSheetEditor.draw_handler_remove(_channel_handler, 'CHANNELS')
        _channel_handler = None
