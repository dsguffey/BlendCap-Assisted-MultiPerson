"""Per-editor VTA modes and ProjectState-backed Dope Sheet presentation.

The bundled workspace is an optional preset. No Action, F-curve, Blender
keyframe, or tracking authority is created here.
"""

from __future__ import annotations

from pathlib import Path
import math
from dataclasses import replace
from types import SimpleNamespace

import bpy

from ..blendcap_vta_workspace import (
    WORKSPACE_NAME, WORKSPACE_TAG, TimelineMetrics, character_lanes, grid_step,
    hit_character, hit_review_key, is_review_region, review_area, review_keys_in_box,
    motion_interval_rects, hit_motion_interval, characters_in_box, derived_mark_color, repair_range_colors,
    authoritative_key_style, channel_rows, channel_items, NATIVE_BOTTOM,
)
from ..blendcap_vta_visuals import ASSIGNMENT_COLOR
from ..blendcap_vta_tracking_hints import marker_positions, hit_marker
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
_collapsed_depth = {}
_depth_focus = set()  # Editor/domain focus only; actual key selection lives in FCurves.
_row_scroll = {}  # Scene/source/editor local: stable first row plus fallback index.
_row_pan = {}  # Fractional trackpad motion, in region pixels.


def _expansion_key(context):
    scene = getattr(context, 'scene', None)
    workspace = getattr(context, 'workspace', None)
    area = getattr(context, 'area', None)
    if not isinstance(scene, bpy.types.Scene) or workspace is None or area is None:
        return None
    return (getattr(scene, 'session_uid', scene.as_pointer()),
            scene.get('vta_project_id'), scene.get('vta_source_id'),
            workspace.as_pointer(), area.as_pointer())


def expanded_characters(context, view):
    """Presentation only: stable IDs, Scene/source/editor local; no animation writes."""
    key = _expansion_key(context)
    if key is None:
        return ()
    collapsed = _collapsed_depth.get(key, ())
    return tuple(c.character_id for c in getattr(view, 'characters', ())
                 if c.character_id not in collapsed)


@bpy.app.handlers.persistent
def _clear_channel_presentation(*_args):
    _collapsed_depth.clear()
    _depth_focus.clear()
    _row_scroll.clear()
    _row_pan.clear()
    from .vta_timeline_markers import clear
    clear()


def _rows(context, view, first, metrics):
    return channel_rows(view, context.region.height, first, metrics=metrics,
                        expanded=expanded_characters(context, view))


def _row_at(context, view, first, metrics, y):
    return next((row for row in _rows(context, view, first, metrics)
                 if abs(row.y-y) < metrics.row_height/2), None)


def _depth_interaction(context, view, first, metrics, point, event):
    """Select native child keys/scrub or toggle disclosure; no review authority edits."""
    row = _row_at(context, view, first, metrics, point[1])
    if row is None:
        return None
    if (context.region.type == 'CHANNELS' and row.kind == 'character'
            and event.type == 'LEFTMOUSE' and 6*metrics.ui_scale <= point[0] <= 26*metrics.ui_scale):
        key = _expansion_key(context)
        if key is not None:
            collapsed = _collapsed_depth.setdefault(key, set())
            if row.character_id in collapsed:
                collapsed.remove(row.character_id)
            else:
                collapsed.add(row.character_id)
            vta_invalidation.request_redraw()
            return {'FINISHED'}
    if row.kind != 'depth':
        return None
    if context.region.type == 'WINDOW' and event.type == 'LEFTMOUSE':
        from .vta_depth_keys import scene_rigs, select_frame, clear_selection
        rigs = scene_rigs(context.scene, [c.character_id for c in view.characters])
        positions = depth_key_markers(context, {row.character_id:row.y}, metrics.ui_scale)
        hit = min(positions, key=lambda value:abs(value[2]-point[0]), default=None)
        frame = (hit[1] if hit and abs(hit[2]-point[0]) <= metrics.hit_radius else
                 round(context.region.view2d.region_to_view(point[0], point[1])[0]))
        if hit and abs(hit[2]-point[0]) <= metrics.hit_radius:
            select_frame(rigs, row.character_id, frame,
                         extend=bool(event.ctrl or getattr(event, 'shift', False)))
        elif not (event.ctrl or getattr(event, 'shift', False)):
            clear_selection(rigs)
        _depth_focus.add(_expansion_key(context))
        context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        vta_invalidation.request_redraw()
    # Never select hidden pose channels or alter review-key/identity selection.
    return {'FINISHED'}


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
        raise ValueError('Use Snap to Source Camera in a 3D viewport or the MoCap workspace.')
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
                         text='Exit MoCap Mode' if active else 'Enter MoCap Mode', icon='KEY_HLT', depress=active)
    if active:
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='MoCap Commands')


def _draw_preview_header(self, context):
    area = getattr(context, 'area', None)
    space = getattr(context, 'space_data', None)
    if (area is None or area.type != 'SEQUENCE_EDITOR' or
            getattr(space, 'view_type', None) not in {'PREVIEW', 'SEQUENCER_PREVIEW'}):
        return
    active = role_for(context.workspace, context.screen, area) == ROLE_PREVIEW
    if active and not _ui().loaded_context_matches(context):
        self.layout.label(text='MoCap mismatch — Load MoCap Scene to restore', icon='INFO')


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
    metrics = TimelineMetrics(ui_scale=scale, text_size=_text_size(context))
    anchor = _row_scroll.get(_expansion_key(context))
    if anchor is not None:
        view = _ui()._timeline
        rows = channel_items(view, expanded_characters(context, view))
        index = next((i for i, row in enumerate(rows) if row == anchor[:2]), anchor[2])
        metrics = replace(metrics, scroll_rows=index)
    return metrics


def _native_bottom(context, event, metrics=None):
    """The native scrollbar/event band stays below the custom marker bridge."""
    return (context.region.type == 'WINDOW' and
            event.mouse_region_y < NATIVE_BOTTOM*(metrics or _metrics(context)).ui_scale)


def reveal_character(context, character_id):
    """Reveal the completed management edit without changing either selection.

    Refreshing the model is insufficient in short editors: expanded Depth rows
    and the existing stable viewport anchor can keep the edited parent outside
    the drawn slice. Only move a matching active Scene's MoCap viewport if needed.
    """
    ui=_ui();view=ui._timeline
    if view is None:return False
    from .vta_scene_session import main_scene
    owner=main_scene(context);changed=False
    for window in getattr(context.window_manager,'windows',()):
        if window.scene!=owner:continue
        for area in window.screen.areas:
            if role_for(window.workspace,window.screen,area)!=ROLE_TIMELINE:continue
            region=next((r for r in area.regions if r.type=='WINDOW'),None)
            if region is None:continue
            target=SimpleNamespace(scene=owner,window=window,workspace=window.workspace,
                screen=window.screen,area=area,region=region,preferences=context.preferences)
            metrics=_metrics(target)
            rows=_rows(target,view,ui._lane_scroll(target),metrics)
            if any(r.character_id==character_id and r.kind=='character' for r in rows):continue
            items=channel_items(view,expanded_characters(target,view))
            index=next((i for i,row in enumerate(items) if row==(character_id,'character')),None)
            if index is None or not rows:continue
            start=min(index,max(0,len(items)-len(rows)))
            _row_scroll[_expansion_key(target)]=(*items[start],start)
            changed=True
    if changed:vta_invalidation.request_redraw()
    return changed


class BLENDCAP_OT_vta_timeline_scroll(bpy.types.Operator):
    bl_idname = 'blendcap.vta_timeline_scroll'
    bl_label = 'Scroll MoCap Rows'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _scope(context)

    def invoke(self, context, event):
        metrics = _metrics(context)
        if (context.region.type=='WINDOW' and event.mouse_region_y<metrics.bottom_padding) or _box_gesture is not None:
            return {'PASS_THROUGH'}
        view, _, _, _, first, _ = _state(context)
        rows = channel_items(view, expanded_characters(context, view))
        shown = _rows(context, view, first, metrics)
        if not rows or not shown:
            return {'PASS_THROUGH'}
        start = rows.index((shown[0].character_id, shown[0].kind))
        key = _expansion_key(context)
        if event.type == 'TRACKPADPAN':
            dx = event.mouse_x - event.mouse_prev_x
            dy = event.mouse_y - event.mouse_prev_y
            if abs(dx) >= abs(dy):
                return {'PASS_THROUGH'}  # Keep horizontal navigation native.
            distance = _row_pan.get(key, 0) + dy
            delta = int(distance / metrics.row_height)
            _row_pan[key] = distance - delta * metrics.row_height
            if not delta:
                return {'FINISHED'}
        else:
            _row_pan.pop(key, None)
            delta = 1 if event.type == 'WHEELDOWNMOUSE' else -1
        start = min(max(0, start + delta), max(0, len(rows)-len(shown)))
        _row_scroll[key] = (*rows[start], start)
        vta_invalidation.request_redraw()
        return {'FINISHED'}


def _timeline_text_size_changed(_self, _context):
    vta_invalidation.request_redraw()


def tracking_hint_markers(context, view, first, metrics):
    if context.region.type != 'WINDOW':
        return ()
    hints = _ui().tracking_hints(context)
    if not hints:
        return ()
    return marker_positions(hints,
        character_lanes(view, context.region.height, first, metrics=metrics,
                        expanded=expanded_characters(context, view)),
        lambda frame: context.region.view2d.view_to_region(frame, 0, clip=False)[0],
        context.region.width, metrics.hit_radius)


def _draw_tracking_hint_markers(context, view, first, metrics, shader, batch):
    import blf
    # An outlined triangle/question mark, never a filled human-key diamond.
    for _hint,x,y in tracking_hint_markers(context, view, first, metrics):
        r=8*metrics.ui_scale
        _line(shader,batch,((x,y+r),(x+r,y-r),(x-r,y-r),(x,y+r)),(.98,.77,.28,1))
        blf.position(0,x-3*metrics.ui_scale,y-5*metrics.ui_scale,0)
        blf.size(0,11*metrics.ui_scale);blf.color(0,.98,.77,.28,1);blf.draw(0,'?')


def depth_key_markers(context, lanes, scale):
    """Live native keys, in Blender time (not source time). No metadata cache."""
    from .vta_depth_keys import scene_keyed_frames
    return tuple((cid, frame, context.region.view2d.view_to_region(frame, 0, clip=False)[0],
                  lanes[cid])
                 for cid, frames in scene_keyed_frames(context.scene, lanes).items()
                 for frame in frames)


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
        expanded = expanded_characters(context, view)
        for channel in _rows(context, view, first, metrics):
            character_id, y = channel.character_id, channel.y
            if channel.kind == 'depth':
                _fill(shader, batch_for_shader, 0, y-half_row, region.width, y+half_row, (.12,.12,.12,1))
                _line(shader, batch_for_shader, ((20*scale,y+half_row),(20*scale,y),(32*scale,y)),(.4,.4,.4,1))
                blf.position(0, 40*scale, y-metrics.font_size*.35, 0)
                blf.size(0,metrics.font_size);blf.color(0,.8,.8,.8,1);blf.draw(0,'Depth')
                continue
            background, text, border = _row_style(context, character_id, current)
            _fill(shader, batch_for_shader, 0, y - half_row, region.width, y + half_row,
                  background)
            if border is not None:
                _fill(shader, batch_for_shader, 0, y - half_row, 4 * scale, y + half_row,
                      border)
            # Blender-style disclosure, independent of row/Character selection.
            points = (((12*scale,y+3*scale),(20*scale,y+3*scale),(16*scale,y-3*scale))
                      if character_id in expanded else
                      ((13*scale,y+4*scale),(19*scale,y),(13*scale,y-4*scale)))
            shader.bind();shader.uniform_float('color',text)
            batch_for_shader(shader,'TRIS',{'pos':points}).draw(shader)
            blf.position(0, 30 * scale, y - metrics.font_size * .35, 0)
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
        # Cover the complete native canvas, including keys and native markers.
        # Blender draws its scrollbar overlay afterwards; our marker bridge is
        # above its native marker event band, not under the scrollbar.
        _fill(shader, batch_for_shader, 0, 0, region.width, region.height,
              (.105, .105, .105, 1))
        if view is None:
            from .vta_timeline_markers import draw
            draw(context,scale,shader,batch_for_shader,_fill,_line)
            return
        expanded = expanded_characters(context, view)
        rows = _rows(context, view, first, metrics)
        lanes = {r.character_id:r.y for r in rows if r.kind == 'character'}
        depth_lanes = {r.character_id:r.y for r in rows if r.kind == 'depth'}
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
        for y in depth_lanes.values():
            _fill(shader,batch_for_shader,0,y-half_row,region.width,y+half_row,(.12,.12,.12,1))
        playhead = region.view2d.view_to_region(context.scene.frame_current, 0,
                                               clip=False)[0]
        frame_x = lambda frame: region.view2d.view_to_region(
            frame + offset, 0, clip=False)[0]
        # Exact range inspection is transient; identity keys retain hit priority.
        selected_repair = _ui().selected_repair_range(context)
        for interval, x0, x1, y0, y1 in motion_interval_rects(
                view, region.view2d, region.width, region.height, offset, first,
                metrics=metrics, include_interval=True, expanded=expanded):
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
        _draw_tracking_hint_markers(context, view, first, metrics, shader, batch_for_shader)
        # Native Blender-frame Location keys, aggregated only in their own child.
        from .vta_depth_keys import selected_scene_frames
        selected_depth = selected_scene_frames(context.scene, depth_lanes)
        for _cid, _frame, x, y in depth_key_markers(context, depth_lanes, scale):
            if key_radius <= x <= region.width-key_radius:
                color = (.95,.75,.3,1) if _frame in selected_depth.get(_cid, ()) else (.55,.57,.6,1)
                shader.bind();shader.uniform_float('color',color)
                batch_for_shader(shader,'TRIS',{'pos':(
                    (x,y+key_radius),(x+key_radius,y),(x,y-key_radius),
                    (x,y+key_radius),(x,y-key_radius),(x-key_radius,y))}).draw(shader)
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
            color, pending = authoritative_key_style(key, color)
            if pending:
                _line(shader, batch_for_shader, ((x-4*scale,y-10*scale),(x+4*scale,y-10*scale)),color)
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
        from .vta_timeline_markers import draw
        draw(context,scale,shader,batch_for_shader,_fill,_line)
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
        raise ValueError('Multiple MoCap workspaces are tagged; choose one manually.')
    if matches:
        return matches[0]
    if WORKSPACE_NAME in bpy.data.workspaces:
        raise ValueError('A workspace conflicts with the bundled workspace name; it was not replaced.')
    return create_vta_workspace(context)


def create_vta_workspace(context):
    """Append an independent preset; creation never chooses a source or Scene."""
    path = Path(__file__).resolve().parent.parent / 'vta_workspace_mode.blend'
    with bpy.data.libraries.load(str(path), link=False) as (source, dest):
        if WORKSPACE_NAME not in source.workspaces:
            raise ValueError('Bundled file has no MoCap workspace.')
        dest.workspaces = [WORKSPACE_NAME]
    workspace = dest.workspaces[0]
    if workspace is None or workspace.get(WORKSPACE_TAG) != 1:
        raise ValueError('Bundled workspace is missing its MoCap scope tag.')
    from ..blendcap_vta_product import PRODUCT_NAME
    workspace.name = PRODUCT_NAME
    return workspace


def _open_workspace_sidebar(workspace):
    """Best effort through public RNA only; sidebar failure never breaks Create."""
    for screen in getattr(workspace, 'screens', ()):
        for area in screen.areas:
            if area.type != 'VIEW_3D': continue
            try:
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == 'UI': region.active_panel_category = 'MoCap'
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass


class BLENDCAP_OT_open_vta_workspace(bpy.types.Operator):
    bl_idname = 'blendcap.open_vta_workspace'
    bl_label = 'Create MoCap Workspace'
    bl_description = 'Create another MoCap workspace and switch to its new tab; existing workspaces are preserved'

    def execute(self, context):
        try:
            workspace = create_vta_workspace(context)
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        context.window.workspace = workspace
        try: _open_workspace_sidebar(workspace)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError): pass
        # Open the layout only. Enter/Load/New are the explicit Scene actions.
        vta_invalidation.request_redraw()
        self.report({'INFO'}, 'StudioFree MoCap workspace created.')
        return {'FINISHED'}


class BLENDCAP_MT_vta_timeline_context(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_context'
    bl_label = 'MoCap Review Key'

    def draw(self, context):
        ui = _ui()
        row = self.layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        ui._draw_management_actions(
            self.layout, actions={'clear-review-key', 'clear-selected-review-keys'},
            availability=ui._action_availability(context),
        )
        self.layout.menu('BLENDCAP_MT_vta_timeline_commands', text='More MoCap Commands')


class BLENDCAP_MT_vta_timeline_commands(bpy.types.Menu):
    bl_idname = 'BLENDCAP_MT_vta_timeline_commands'
    bl_label = 'MoCap Commands'

    def draw(self, context):
        ui = _ui()
        layout = self.layout
        layout.operator('blendcap.vta_reconcile', text='Retry Reconciliation')
        repeat = layout.row(); repeat.enabled = ui.BLENDCAP_OT_vta_repeat_assignment.poll(context)
        repeat.operator('blendcap.vta_repeat_assignment', text='Repeat Last Assignment Mark')
        ui.draw_scene_workflow(layout, context)
        layout.operator('blendcap.vta_review_refresh', text='Refresh Review')
        layout.operator('blendcap.vta_snap_source_camera', text='Snap to Source Camera', icon='CAMERA_DATA')
        ui.draw_scene_timing(layout, context, menu=True)
        if not ui.refresh(context):
            layout.label(text='MoCap review unavailable', icon='INFO')
            return
        ui._draw_management_actions(layout, availability=ui._action_availability(context))
        row = layout.row()
        row.enabled = ui._review_key_selection.active_keyframe_id is not None
        row.operator('blendcap.vta_goto_review_key', text='Go to Review Key')
        row = layout.row()
        row.enabled = ui._action_availability(context)['update-tracking']
        row.operator('blendcap.vta_update_tracking', text='Update Tracking')
        layout.operator('blendcap.vta_restore_protected',text='Restore Last Protected Edit')
        ui.draw_operation_feedback(layout, context, 'tracking')
        layout.separator()
        ui.draw_cleanup_commands(layout, context, menu=True)


class BLENDCAP_PT_vta_timeline(bpy.types.Panel):
    bl_idname = 'BLENDCAP_PT_vta_timeline'
    bl_label = 'MoCap Review Timeline'
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'MoCap'

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
        layout.label(text='Expand Character → Depth: native Location keys')
        layout.label(text='Edit native keys: Shift F6 (Graph Editor)')
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
        layout.operator('blendcap.vta_restore_protected',text='Restore Last Protected Edit')
        layout.menu('BLENDCAP_MT_vta_timeline_commands', text='More MoCap Commands')

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
            self.report({'WARNING'}, 'Select a MoCap review key first.')
            return {'CANCELLED'}
        return {'FINISHED'}


class BLENDCAP_OT_vta_toggle_editor_mode(bpy.types.Operator):
    bl_idname = 'blendcap.vta_toggle_editor_mode'
    bl_label = 'Enter / Exit MoCap Mode'
    bl_description = 'Enter the remembered MoCap Scene or exit review without changing data'

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
    expanded = expanded_characters(context, view)
    channel = _row_at(context, view, first, metrics, point[1])
    if channel and channel.kind == 'depth':
        if context.region.type == 'CHANNELS':
            return 'Depth — This Character’s native Object Location keys, aggregated across X/Y/Z. No identity edits.'
        for _cid, frame, x, y in depth_key_markers(context, {channel.character_id:channel.y}, metrics.ui_scale):
            if abs(x-point[0]) <= metrics.hit_radius:
                return (f'Depth key at frame {frame:g} — Click to select/scrub; Shift/Ctrl toggles selection. '
                        'X/Delete removes this Character’s complete Location key. Shift F6: Graph Editor.')
        return 'Depth — Click to scrub; click diamonds to select, Shift/Ctrl to add, X/Delete to remove selected Location keys.'
    if context.region.type == 'CHANNELS':
        cid = hit_character(view, context.region.height, point[1], first, metrics=metrics, expanded=expanded)
        if cid and 6*metrics.ui_scale <= point[0] <= 26*metrics.ui_scale:
            return 'Expand/collapse this Character’s Depth channel — Presentation only; animation is unchanged.'
        return 'Character row — Selected rows target tracking, repairs and selected generation.' if cid else ''
    key_id = hit_review_key(view, context.region.view2d, context.region.height, point, offset, first, metrics=metrics, expanded=expanded)
    if key_id:
        key = next((k for k in getattr(view, 'keys', ()) if k.keyframe_id == key_id), None)
        label = getattr(key, 'tracking_label', 'Authoritative — not yet included in Update Tracking')
        return 'Hard review key — Explicit Character assignment or review decision. ' + label
    markers = tracking_hint_markers(context,view,first,metrics)
    hint = hit_marker(markers,point,metrics.hit_radius) if markers else None
    if hint:
        return (f'Identity ambiguous at frame {hint.frame} — Non-authoritative hint. '
                'Click: review / Right-click: dismiss. Assign only valid separate evidence; otherwise No Valid Candidate.')
    interval = hit_motion_interval(view, context.region.view2d, context.region.width,
        context.region.height, point, offset, first, metrics=metrics, expanded=expanded)
    if interval:
        return ('Freeze repair — Hold the preceding trustworthy unrepaired pose.' if interval.mode == 'freeze'
                else 'Interpolate repair — Blend raw anchors outside both endpoints; repaired frames never anchor.')
    lanes = dict(character_lanes(view, context.region.height, first, metrics=metrics, expanded=expanded))
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
    bottom = _metrics(context).bottom_padding if context.region.type == 'WINDOW' else 0
    blf.position(0, 10, bottom + 8, 0)
    blf.color(0, 1, .95, .8, 1)
    blf.draw(0, text)


class BLENDCAP_OT_vta_timeline_hover(bpy.types.Operator):
    bl_idname = 'blendcap.vta_timeline_hover'
    bl_label = 'MoCap Timeline Tooltip'
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
    bl_label = 'Select MoCap Timeline Item'
    bl_description = 'Select Character rows or review keys; drag within their own region to box-select'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _scope(context)

    def invoke(self, context, event):
        global _box_gesture
        metrics = _metrics(context)
        from . import vta_timeline_markers as markers
        if markers.contains(context,event,metrics.ui_scale):
            markers.focus(context)
            _depth_focus.discard(_expansion_key(context))
            if event.type=='LEFTMOUSE':
                return bpy.ops.blendcap.vta_marker_drag('INVOKE_DEFAULT')
            return {'FINISHED'}
        if _native_bottom(context,event,metrics):
            # Keep the scrollbar native; empty painted pixels above it must
            # never select an underlying pose key when no Scene markers exist.
            return {'PASS_THROUGH'} if event.mouse_region_y<16*metrics.ui_scale else {'FINISHED'}
        markers.focus(context,False)
        ui = _ui()
        view, _selected, _active, offset, first, _current = _state(context)
        if view is None:
            return {'PASS_THROUGH'}
        point = (event.mouse_region_x, event.mouse_region_y)
        if _box_gesture is None and event.type in {'LEFTMOUSE','RIGHTMOUSE'}:
            try:
                depth_result = _depth_interaction(context, view, first, metrics, point, event)
            except ValueError as exc:
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            if depth_result is not None:
                return depth_result
            if event.type == 'LEFTMOUSE':
                if _expansion_key(context) in _depth_focus:
                    from .vta_depth_keys import scene_rigs, clear_selection
                    clear_selection(scene_rigs(context.scene, [c.character_id for c in view.characters]))
                _depth_focus.discard(_expansion_key(context))
        if context.region.type == 'CHANNELS':
            if (event.type != 'LEFTMOUSE' or _box_gesture is not None or
                    point[1] >= context.region.height - 24 * metrics.ui_scale):
                return {'PASS_THROUGH'}
        expanded = expanded_characters(context, view)
        key_id = (None if context.region.type == 'CHANNELS' else hit_review_key(view, context.region.view2d,
                                context.region.height, point, offset, first, metrics=metrics, expanded=expanded)
                  )
        if key_id is None:
            markers = tracking_hint_markers(context,view,first,metrics)
            hint = hit_marker(markers,point,metrics.hit_radius) if markers else None
            if hint is not None and event.type in {'LEFTMOUSE','RIGHTMOUSE'} and _box_gesture is None:
                return bpy.ops.blendcap.vta_goto_tracking_hint(character_id=hint.character_id,
                    frame=hint.frame,dismiss=event.type=='RIGHTMOUSE')
            if context.region.type == 'WINDOW' and event.type == 'LEFTMOUSE' and _box_gesture is None:
                interval = hit_motion_interval(view, context.region.view2d,
                                               context.region.width, context.region.height,
                                               point, offset, first, metrics=metrics, expanded=expanded)
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
                self._gesture_expanded = expanded
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
                _metrics(context) != self._gesture_metrics or
                expanded_characters(context, self._gesture_view) != self._gesture_expanded):
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
                        metrics=self._gesture_metrics, expanded=self._gesture_expanded)
                    _ui().select_timeline_characters(context, ids, extend=self._gesture_extend)
                else:
                    ids = review_keys_in_box(
                        self._gesture_view, region.view2d, region.width, region.height,
                        gesture['start'], gesture['end'], self._gesture_offset,
                        self._gesture_first, metrics=self._gesture_metrics, expanded=self._gesture_expanded)
                    _ui().select_timeline_keys(context, ids, extend=self._gesture_extend)
            elif self._gesture_characters:
                cid = hit_character(self._gesture_view, region.height, gesture['start'][1],
                                    self._gesture_first, metrics=self._gesture_metrics, expanded=self._gesture_expanded)
                if cid is not None:
                    _ui().select_timeline_character(context, cid, extend=self._gesture_extend)
                else:
                    _ui().select_timeline_characters(context, (), extend=self._gesture_extend)
            elif not self._gesture_extend:
                _ui().select_timeline_keys(context, ())
            return self._finish_box('FINISHED')
        return {'RUNNING_MODAL'}


def _delegated_timeline_result(result):
    # The nested native/dialog operator owns its own handler. Only BOX starts
    # this dispatcher's modal lifecycle; do not leave a second operator alive.
    return {'FINISHED'} if 'RUNNING_MODAL' in result else result


class BLENDCAP_OT_vta_timeline_keys(bpy.types.Operator):
    bl_idname='blendcap.vta_timeline_keys'
    bl_label='MoCap Timeline Editing'
    bl_options={'INTERNAL'}
    __annotations__={'action':bpy.props.EnumProperty(items=[(a,a,'') for a in ('BOX','ALL','NONE','DELETE','ADD','RENAME','MOVE')])}

    @classmethod
    def poll(cls,context):return _scope(context)

    def invoke(self,context,event):
        if _native_bottom(context, event):return {'CANCELLED'}
        from . import vta_timeline_markers as markers
        marker_focus=context.region.type=='WINDOW' and (
            markers.focused(context) or markers.contains(context,event,_metrics(context).ui_scale))
        if self.action=='ADD' or (marker_focus and self.action in {'RENAME','MOVE','DELETE'}):
            return _delegated_timeline_result(markers.command(context,self.action))
        if self.action in {'RENAME','MOVE'}:return {'PASS_THROUGH'}
        if marker_focus:
            if self.action in {'ALL','NONE'}:
                for marker in markers.scene(context).timeline_markers:marker.select=self.action=='ALL'
                vta_invalidation.request_redraw()
                return {'FINISHED'}
            return {'CANCELLED'}
        if self.action=='DELETE':
            if _expansion_key(context) in _depth_focus:return bpy.ops.blendcap.vta_depth_delete()
            if _ui()._review_key_selection.keyframe_ids:
                return _delegated_timeline_result(bpy.ops.blendcap.vta_review_manage('INVOKE_DEFAULT',action='clear-selected-review-keys'))
            return {'CANCELLED'}  # Never delete hidden native pose keys.
        view=_state(context)[0]
        if view is None:return {'CANCELLED'}
        if self.action in ('ALL','NONE'):
            _depth_focus.discard(_expansion_key(context))
            if context.region.type=='CHANNELS':
                _ui().select_timeline_characters(context,[c.character_id for c in view.characters] if self.action=='ALL' else ())
            else:
                _ui().select_timeline_keys(context,[k.keyframe_id for k in view.keys] if self.action=='ALL' else ())
            return {'FINISHED'}
        self._waiting=True;self._selector=None
        context.window.cursor_modal_set('CROSSHAIR');context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self,context):
        if self._selector is not None:self._selector.cancel(context)
        context.window.cursor_modal_restore()

    def modal(self,context,event):
        if self._selector is None:
            if event.type in ('ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'):
                self.cancel(context);return {'CANCELLED'}
            if event.type!='LEFTMOUSE' or event.value!='PRESS':return {'RUNNING_MODAL'}
            # Reuse the existing bounded marquee implementation, without adding
            # another modal handler or another selection model.
            from types import SimpleNamespace,MethodType
            cls=BLENDCAP_OT_vta_timeline_select
            proxy=SimpleNamespace()
            proxy._finish_box=MethodType(cls._finish_box,proxy)
            proxy.cancel=MethodType(cls.cancel,proxy)
            # B always starts a rectangle, even over an existing key/Depth row.
            view,_,_,offset,first,_=_state(context);metrics=_metrics(context)
            point=(event.mouse_region_x,event.mouse_region_y)
            proxy._gesture_view=view;proxy._gesture_metrics=metrics;proxy._gesture_offset=offset
            proxy._gesture_first=first;proxy._gesture_expanded=expanded_characters(context,view)
            proxy._gesture_extend=bool(event.shift);proxy._gesture_characters=context.region.type=='CHANNELS'
            proxy._gesture=dict(area=context.area,region=context.region,start=point,end=point,dragged=False)
            global _box_gesture
            _box_gesture=proxy._gesture;self._selector=proxy
            return {'RUNNING_MODAL'}
        result=BLENDCAP_OT_vta_timeline_select.modal(self._selector,context,event)
        if result=={'FINISHED'}:_depth_focus.discard(_expansion_key(context))
        if result!={'RUNNING_MODAL'}:context.window.cursor_modal_restore()
        return result


class BLENDCAP_OT_vta_depth_delete(bpy.types.Operator):
    bl_idname = 'blendcap.vta_depth_delete'
    bl_label = 'Delete Selected Depth Keys'
    bl_description = 'Delete selected Characters’ native Location keys at selected Depth diamonds; all XYZ components'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _scope(context) and context.region.type == 'WINDOW'

    def execute(self, context):
        view = _state(context)[0]
        if view is None or _expansion_key(context) not in _depth_focus:
            self.report({'INFO'}, 'Select Depth diamonds before deleting Depth keys.')
            return {'CANCELLED'}
        from .vta_depth_keys import delete_selected
        try:
            count = delete_selected(context.scene, [c.character_id for c in view.characters])
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        if not count:
            return {'CANCELLED'}
        frame = context.scene.frame_current_final
        context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        vta_invalidation.request_redraw()
        return {'FINISHED'}


def register():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered, _camera_header_registered, _blank_handler
    if _clear_channel_presentation not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_clear_channel_presentation)
    if _blank_handler is None:
        _blank_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_blank_animation, (), 'WINDOW', 'POST_PIXEL')
    if _window_handler is None:
        _window_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_window, (), 'WINDOW', 'POST_PIXEL')
    if _channel_handler is None:
        _channel_handler = bpy.types.SpaceDopeSheetEditor.draw_handler_add(
            _draw_channels, (), 'CHANNELS', 'POST_PIXEL')
    bpy.types.Scene.blendcap_vta_timeline_text_size = bpy.props.IntProperty(
        name='Timeline Text Size', description='MoCap text size before Blender UI scaling; rows adapt to fit',
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
            for button in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'):
                items.append(keymap.keymap_items.new(BLENDCAP_OT_vta_timeline_scroll.bl_idname, button, 'ANY'))
            for button,action,mods in (('B','BOX',{}),('A','ALL',{}),('A','NONE',{'alt':True})):
                item=keymap.keymap_items.new(BLENDCAP_OT_vta_timeline_keys.bl_idname,button,'PRESS',**mods)
                item.properties.action=action;items.append(item)
            if name == 'Dopesheet':
                for button in ('X', 'DEL'):
                    item=keymap.keymap_items.new(BLENDCAP_OT_vta_timeline_keys.bl_idname, button, 'PRESS')
                    item.properties.action='DELETE';items.append(item)
                for button,action in (('M','ADD'),('F2','RENAME'),('G','MOVE')):
                    item=keymap.keymap_items.new(BLENDCAP_OT_vta_timeline_keys.bl_idname,button,'PRESS')
                    item.properties.action=action;items.append(item)


def unregister():
    global _window_handler, _channel_handler, _header_registered, _preview_header_registered, _box_gesture, _camera_header_registered, _blank_handler, _hover
    _clear_channel_presentation()
    if _clear_channel_presentation in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_clear_channel_presentation)
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
