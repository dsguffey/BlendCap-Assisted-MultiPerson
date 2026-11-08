"""VTA-only, source-depth-only viewport interaction on native Object Location."""
import math
from pathlib import Path
import bpy
from mathutils import Vector
from .vta_camera_depth import depth_axis, depth_target, local_depth_axis, mouse_depth_delta

TOOL_ID = 'blendcap.vta_depth'
_tool_definition = None
_activation_tokens = {}


def observe_tool_activation(context):
    """One-shot viewport visibility setup on entry, not continuous enforcement.

    A tool can be activated while a saved VTA viewport has all gizmos disabled.
    Never write this from drawing, never change Scene/bindings, and respect a
    subsequent explicit user hide until the next tool/session entry.
    """
    workspace = context.workspace
    ref = workspace.tools.from_space_view3d_mode('OBJECT', create=False)
    active = bool(ref and ref.idname == TOOL_ID and tool_visible(context))
    scene = context.window.scene
    token = (workspace.as_pointer(), getattr(scene, 'session_uid', scene.as_pointer()), active)
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        key = (context.window.as_pointer(), area.as_pointer())
        if _activation_tokens.get(key) != token and active:
            area.spaces.active.show_gizmo = True
            area.spaces.active.show_gizmo_tool = True
            area.tag_redraw()
        _activation_tokens[key] = token


@bpy.app.handlers.persistent
def _reset_activation(*_args):
    _activation_tokens.clear()


def tool_visible(context):
    from . import vta_review_ui as ui
    return bool(context and getattr(context, 'mode', '') == 'OBJECT'
                and ui.loaded_context_matches(context))


def target(context):
    from . import vta_review_ui as ui
    if not tool_visible(context) or ui._analysis_locked():
        raise ValueError('Load this MoCap Scene in Object Mode before editing depth.')
    obj, camera = depth_target(context)
    # Use the existing review/object synchronization's eligibility, not a new
    # remembered target. Deselecting/switching rigs immediately changes this.
    if obj not in ui._selection_rigs(context):
        raise ValueError('Select a generated Character belonging to this MoCap Scene.')
    if not obj.is_editable:
        raise ValueError('Depth editing requires an editable local Character.')
    if len(obj.users_scene) != 1:
        raise ValueError('Make this Character local to one Scene before editing depth.')
    return obj, camera


def targets(context):
    """Scene-local native selection, any number of eligible Characters."""
    from . import vta_review_ui as ui
    from .vta_scene_root import supported_parent
    selected = tuple(o for o in ui._selection_rigs(context) if o.select_get())
    if len(selected) <= 1:
        obj, camera = target(context)  # Preserve the accepted single-Character path.
        return (obj,), camera
    if not tool_visible(context) or ui._analysis_locked():
        raise ValueError('Load this MoCap Scene in Object Mode before editing depth.')
    info = ui.cached_scene_source(context)
    if not info: raise ValueError('Load this MoCap Scene before editing depth.')
    cameras = [o for o in context.scene.objects if o.type == 'CAMERA'
               and o.get('vta_project_id') == info[0] and o.get('vta_source_id') == info[1]['source_id']]
    if len(cameras) != 1: raise ValueError('Retain the unique MoCap Source Camera to edit depth.')
    from .vta_depth_keys import validate_edit
    eligible = []
    for obj in selected:
        if (not obj.is_editable or len(obj.users_scene) != 1 or obj.constraints
                or not supported_parent(obj) or any(obj.lock_location)
                or sum(o.get('vta_character_id') == obj.get('vta_character_id') for o in selected) != 1):
            continue
        if obj.animation_data and (obj.animation_data.drivers or obj.animation_data.nla_tracks): continue
        if context.scene.tool_settings.use_keyframe_insert_auto:
            try: validate_edit(obj)
            except ValueError: continue
        eligible.append(obj)
    if not eligible: raise ValueError('Select an editable, unlocked Character without constraints or NLA.')
    return tuple(sorted(eligible, key=lambda o:o.name)), cameras[0]


def group_point(objects):
    # Generated Object origins sit at the source camera; averaging those would
    # put a multi-selection handle behind/inside the camera. Use the existing
    # visible pelvis handle points instead, independent of active-object order.
    return sum((handle_point(o) for o in objects), Vector()) / len(objects)


def finish_group(context, objects, changed):
    if not context.scene.tool_settings.use_keyframe_insert_auto: return
    from .vta_depth_keys import validate_edit, location_snapshot, restore_location_snapshot
    moving = [o for o, did_move in zip(objects, changed) if did_move]
    for o in moving: validate_edit(o)
    snapshots = [(o, location_snapshot(o)) for o in moving]
    try:
        for o in moving: finish_key(context, o, True)
    except (ValueError, RuntimeError):
        for o, state in snapshots: restore_location_snapshot(o, state)
        raise


def drag_members(operator):
    return (tuple(zip(operator._objects, operator._origins, operator._axes))
            if hasattr(operator, '_objects') else ((operator._obj, operator._origin, operator._axis),))


def handle_point(obj):
    hips = obj.pose.bones.get('Hips')
    return obj.matrix_world @ hips.head if hips else obj.matrix_world.translation.copy()


def screen_axis(context, point, camera):
    from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
    region, rv = context.region, context.space_data.region_3d
    a = location_3d_to_region_2d(region, rv, point)
    b = location_3d_to_region_2d(region, rv, point + depth_axis(camera) * .01)
    if a is None or b is None:
        raise ValueError('Frame the selected Character in this viewport and retry.')
    projected = (b - a) / .01
    if projected.length >= 8:
        return projected
    # In source-camera view depth projects to a point. A vertical drag on the
    # centre ring still changes ONLY depth, using the local view's pixel scale.
    side = region_2d_to_location_3d(region, rv, a + Vector((0, 1)), point)
    metres_per_pixel = (side - point).length
    if metres_per_pixel < 1e-10:
        raise ValueError('Viewport depth scale is unavailable.')
    return Vector((0, 1 / metres_per_pixel))


def apply_distance(obj, camera, distance):
    if not math.isfinite(distance):
        raise ValueError('Depth distance must be finite.')
    obj.location += local_depth_axis(obj, camera) * distance
    obj.update_tag()


def finish_key(context, obj, changed):
    """One native location key on release, never on tool selection/cancel."""
    if not changed or not context.scene.tool_settings.use_keyframe_insert_auto:
        return False
    frame = context.scene.frame_current_final
    settings = context.scene.tool_settings
    if settings.auto_keying_mode == 'REPLACE_KEYS':
        from .vta_depth_keys import keyed_frames
        if not any(abs(frame - value) < 1e-5 for value in keyed_frames(obj)):
            return False
    animation = obj.animation_data
    if animation and animation.action:
        if not animation.action.is_editable:
            raise ValueError('Auto Key requires an editable Character Action.')
        if any(other is not obj and other.animation_data
               and other.animation_data.action == animation.action
               and getattr(other.animation_data, 'action_slot', None) == getattr(animation, 'action_slot', None)
               for other in bpy.data.objects):
            raise ValueError('Character Action slot is shared; make its animation single-user before Auto Key.')
    options = {'INSERTKEY_REPLACE'} if settings.auto_keying_mode == 'REPLACE_KEYS' else set()
    if not obj.keyframe_insert(data_path='location', frame=frame, group='Object Transforms',
                               keytype=settings.keyframe_type, options=options):
        raise ValueError('Blender could not key this Character Object Location.')
    return True


def redraw():
    from .vta_invalidation import request_redraw
    request_redraw()


class BLENDCAP_OT_vta_depth_drag(bpy.types.Operator):
    bl_idname = 'blendcap.vta_depth_drag'
    bl_label = 'MoCap Depth'
    bl_description = 'Drag along source-camera depth only; Shift fine; release confirms; Esc cancels. Head-on: drag vertically'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    distance: bpy.props.FloatProperty(name='Camera Depth', subtype='DISTANCE', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        try:
            targets(context)
            return bool(context.area and context.area.type == 'VIEW_3D')
        except (ValueError, RuntimeError, ReferenceError):
            return False

    def invoke(self, context, event):
        try:
            self._objects, self._camera = targets(context)
            self._obj = self._objects[0]
            self._scene = context.scene
            self._frame = context.scene.frame_current_final
            self._origin = self._obj.location.copy()
            self._axis = local_depth_axis(self._obj, self._camera)
            self._origins = tuple(o.location.copy() for o in self._objects)
            self._axes = tuple(local_depth_axis(o, self._camera) for o in self._objects)
            self._screen_axis = screen_axis(context, group_point(self._objects), self._camera)
            self._mouse = Vector((event.mouse_x, event.mouse_y))
            self._start_mouse = self._mouse.copy()
            self._drag_started = False
            self._distance = 0.
            context.window_manager.modal_handler_add(self)
            context.area.header_text_set('MoCap Depth only | Shift fine | Release confirm | Esc cancel | Head-on: vertical drag')
            return {'RUNNING_MODAL'}
        except (ValueError, RuntimeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}

    def execute(self, context):
        objects, origins = (), ()
        try:
            objects, camera = targets(context)
            origins = tuple(o.location.copy() for o in objects)
            for obj in objects: apply_distance(obj, camera, self.distance)
            finish_group(context, objects, [(o.location-p).length > 1e-8 for o,p in zip(objects,origins)])
        except (ValueError, RuntimeError) as exc:
            for obj, origin in zip(objects, origins): obj.location = origin;obj.update_tag()
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        redraw()
        return {'FINISHED'}

    def _end(self, context):
        if context.area:
            context.area.header_text_set(None)
        redraw()

    def cancel(self, context):
        for obj, origin, _axis in drag_members(self):
            try:
                obj.location = origin;obj.update_tag()
            except ReferenceError:
                pass
        self._end(context)

    def modal(self, context, event):
        try:
            current = targets(context)
            objects = tuple(row[0] for row in drag_members(self))
            valid = (context.scene == self._scene and current == (objects, self._camera)
                     and context.scene.frame_current_final == self._frame)
        except (ValueError, RuntimeError, ReferenceError):
            valid = False
        if not valid or event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'}:
            self.cancel(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            try:
                rows = drag_members(self)
                finish_group(context, tuple(r[0] for r in rows), [(o.location-p).length > 1e-8 for o,p,_a in rows])
            except (ValueError, RuntimeError) as exc:
                self.cancel(context)
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            self.distance = self._distance
            self._end(context)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_x, event.mouse_y))
            if not self._drag_started:
                threshold = context.preferences.inputs.drag_threshold_mouse
                if (mouse - self._start_mouse).length < threshold:
                    return {'RUNNING_MODAL'}
                self._drag_started = True
            self._distance += mouse_depth_delta(mouse - self._mouse, self._screen_axis, fine=event.shift)
            self._mouse = mouse
            for obj, origin, axis in drag_members(self):
                obj.location = origin + axis * self._distance;obj.update_tag()
            redraw()
        # Consume X/Y/Z, G, numeric transforms, etc. Never hand off to generic
        # translation: its keyboard reconstraining can escape the depth axis.
        return {'RUNNING_MODAL'}


class BLENDCAP_OT_vta_depth_block_drag(bpy.types.Operator):
    bl_idname = 'blendcap.vta_depth_block_drag'
    bl_label = 'MoCap Depth: drag the handle only'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if not tool_visible(context) or not context.area or context.area.type != 'VIEW_3D':
            return False
        ref = context.workspace.tools.from_space_view3d_mode('OBJECT', create=False)
        return bool(ref and ref.idname == TOOL_ID)

    def invoke(self, context, event):
        # Consume the native Object Mode select-tweak/translate fallback. Only
        # gizmo.target_set_operator may begin a depth drag; this never moves/keys.
        return self.execute(context)

    def execute(self, context):
        return {'FINISHED'}


class BLENDCAP_GGT_vta_depth(bpy.types.GizmoGroup):
    bl_idname = 'BLENDCAP_GGT_vta_depth'
    bl_label = 'MoCap Depth'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'SCALE'}

    @classmethod
    def poll(cls, context):
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        return bool(tool and tool.idname == TOOL_ID and BLENDCAP_OT_vta_depth_drag.poll(context))

    def setup(self, context):
        self.arrow = self.gizmos.new('GIZMO_GT_arrow_3d')
        self.arrow.draw_style = 'BOX'
        self.ring = self.gizmos.new('GIZMO_GT_dial_3d')
        self.ring.use_draw_value = False
        for gizmo in (self.arrow, self.ring):
            gizmo.color = (.2, .8, 1.)
            gizmo.alpha = .8
            gizmo.color_highlight = (.65, .95, 1.)
            gizmo.alpha_highlight = 1.
            gizmo.use_draw_modal = True
            gizmo.target_set_operator('blendcap.vta_depth_drag')
        self.arrow.scale_basis = .8
        self.ring.scale_basis = .18

    def draw_prepare(self, context):
        try:
            objects, camera = targets(context)
            rotation = depth_axis(camera).to_track_quat('Z', 'Y').to_matrix().to_4x4()
            rotation.translation = group_point(objects)
            for gizmo in (self.arrow, self.ring):
                gizmo.hide = False
                gizmo.matrix_basis = rotation
        except (ValueError, RuntimeError, ReferenceError):
            for gizmo in (self.arrow, self.ring):
                gizmo.hide = True


class BLENDCAP_WST_vta_depth(bpy.types.WorkSpaceTool):
    bl_idname = TOOL_ID
    bl_label = 'MoCap Depth'
    bl_description = 'Move selected eligible Characters together along source-camera depth. Auto Key keys each Object Location. Head-on: drag centre ring vertically'
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_icon = str(Path(__file__).parent / 'icons' / 'vta_depth')
    bl_widget = 'BLENDCAP_GGT_vta_depth'
    bl_keymap = (
        ('blendcap.vta_depth_block_drag', {'type':'LEFTMOUSE', 'value':'CLICK_DRAG', 'any':True}, None),
        ('view3d.select', {'type':'LEFTMOUSE', 'value':'PRESS'},
         {'properties':[('deselect_all', True)]}),
        ('view3d.select', {'type':'LEFTMOUSE', 'value':'PRESS', 'shift':True},
         {'properties':[('toggle', True)]}),
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        layout.label(text='MoCap Depth — source-camera axis only')
        if not context.space_data.show_gizmo or not context.space_data.show_gizmo_tool:
            layout.prop(context.space_data, 'show_gizmo', text='Show Gizmos')
            layout.prop(context.space_data, 'show_gizmo_tool', text='Tool Gizmo')


def depth_tools(context):
    # Blender's toolbar supports dynamic ToolDef generators. WorkSpaceTool.poll
    # is NOT evaluated by register_tool; a static registration clutters all modes.
    # None is Blender's internal keymap-enumeration sentinel, not a UI context.
    if context is None or tool_visible(context):
        yield _tool_definition


def register():
    global _tool_definition
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active
    for cls in (BLENDCAP_OT_vta_depth_drag, BLENDCAP_OT_vta_depth_block_drag, BLENDCAP_GGT_vta_depth):
        bpy.utils.register_class(cls)
    _reset_activation()
    if _reset_activation not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reset_activation)
    bpy.utils.register_tool(BLENDCAP_WST_vta_depth, after={'builtin.move'})
    _tool_definition = BLENDCAP_WST_vta_depth._bl_tool
    tools = VIEW3D_PT_tools_active._tools['OBJECT']
    tools[tools.index(_tool_definition)] = depth_tools


def unregister():
    global _tool_definition
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active
    tools = VIEW3D_PT_tools_active._tools['OBJECT']
    if depth_tools in tools:
        tools[tools.index(depth_tools)] = _tool_definition
        bpy.utils.unregister_tool(BLENDCAP_WST_vta_depth)
    _tool_definition = None
    _reset_activation()
    if _reset_activation in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reset_activation)
    for cls in (BLENDCAP_GGT_vta_depth, BLENDCAP_OT_vta_depth_block_drag, BLENDCAP_OT_vta_depth_drag):
        bpy.utils.unregister_class(cls)
