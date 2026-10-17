"""Manual object translation only; no reconstruction, key or pose ownership."""
import math
import bpy
from mathutils import Vector


def depth_axis(camera):
    return (camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))).normalized()


def mouse_depth_delta(motion, screen_axis, *, fine=False):
    axis = Vector(screen_axis)
    if axis.length_squared < 1e-8 or any(not math.isfinite(v) for v in axis):
        raise ValueError('Camera depth is nearly head-on. Orbit the viewport slightly and retry.')
    return Vector(motion).dot(axis) / axis.length_squared * (.1 if fine else 1)


def local_depth_axis(obj, camera):
    axis = depth_axis(camera)
    if obj.parent:
        axis = (obj.parent.matrix_world @ obj.matrix_parent_inverse).to_3x3().inverted() @ axis
    return axis


def depth_target(context):
    from . import vta_review_ui as ui
    from .vta_scene_root import supported_parent
    ui.require_loaded_context(context)
    info = ui.cached_scene_source(context)
    obj = context.active_object
    if (not info or obj is None or obj.type != 'ARMATURE' or not obj.select_get()
            or context.scene.objects.get(obj.name) is not obj or obj.get('vta_project_id') != info[0]
            or obj.get('vta_source_id') != info[1]['source_id']):
        raise ValueError('Select one generated VTA Character rig in this Scene.')
    selected = [o for o in context.selected_objects if o.type == 'ARMATURE'
                and o.get('vta_project_id') == info[0] and o.get('vta_source_id') == info[1]['source_id']]
    if selected != [obj] or obj.constraints or not supported_parent(obj) or any(obj.lock_location):
        raise ValueError('Use one unlocked Character with a supported parent and no constraints.')
    if obj.animation_data and (obj.animation_data.drivers or obj.animation_data.nla_tracks):
        raise ValueError('Camera depth does not support driven or NLA-controlled Characters.')
    cameras = [o for o in context.scene.objects if o.type == 'CAMERA'
               and o.get('vta_project_id') == info[0] and o.get('vta_source_id') == info[1]['source_id']]
    if len(cameras) != 1:
        raise ValueError('Retain the unique VTA Source Camera to move along its depth axis.')
    return obj, cameras[0]


class BLENDCAP_OT_vta_camera_depth(bpy.types.Operator):
    bl_idname = 'blendcap.vta_camera_depth'
    bl_label = 'Move Along Camera Depth'
    bl_description = 'Move one Character along source-camera depth; Shift: fine, Enter/click: confirm, Esc: cancel. Orbit slightly if head-on'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    distance: bpy.props.FloatProperty(name='Camera Depth', subtype='DISTANCE', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        from . import vta_review_ui as ui
        return bool(getattr(context, 'area', None) and context.area.type == 'VIEW_3D'
                    and ui.loaded_context_matches(context) and not ui._analysis_locked())

    def invoke(self, context, event):
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        try:
            self._obj, self._camera = depth_target(context)
            self._scene = context.scene
            self._origin = self._obj.location.copy()
            self._axis = local_depth_axis(self._obj, self._camera)
            self._region = next(r for r in context.area.regions if r.type == 'WINDOW')
            rv = context.space_data.region_3d
            # Sample near the rig's visible pelvis, not its organizational origin.
            hips = self._obj.pose.bones.get('Hips')
            point = self._obj.matrix_world @ hips.head if hips else self._obj.matrix_world.translation
            a = location_3d_to_region_2d(self._region, rv, point)
            b = location_3d_to_region_2d(self._region, rv, point + depth_axis(self._camera) * .01)
            if a is None or b is None:
                raise ValueError('Character is behind this view. Frame it and retry.')
            self._screen_axis = (b - a) / .01  # pixels per world metre
            mouse_depth_delta((0, 0), self._screen_axis)
            # Presentation change belongs only to the invoking viewport. Leave
            # it closed on confirm/cancel; this is not editor-history restore.
            context.space_data.show_region_ui = False
            self._area = context.area
            self._guide = (a, self._screen_axis.normalized())
            self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_axis, (), 'WINDOW', 'POST_PIXEL')
            self._mouse = Vector((event.mouse_x, event.mouse_y))
            self._distance = 0.
            context.window_manager.modal_handler_add(self)
            context.area.header_text_set('Camera depth: drag along projected axis | Shift fine | Enter/click confirm | Esc cancel')
            return {'RUNNING_MODAL'}
        except (ValueError, RuntimeError, StopIteration) as exc:
            self._end_display(context)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}

    def execute(self, context):
        """Native redo/EXEC uses the same target/math, with Blender undo ownership."""
        try:
            obj, camera = depth_target(context)
            obj.location += local_depth_axis(obj, camera) * self.distance
            obj.update_tag()
        except (ValueError, RuntimeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}

    def _draw_axis(self):
        if bpy.context.area != self._area or bpy.context.region != self._region:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        origin, direction = self._guide
        a, b = origin - direction * 60, origin + direction * 60
        wing = Vector((-direction.y, direction.x)) * 7
        points = (tuple(a),tuple(b),tuple(b-direction*12+wing),tuple(b),tuple(b-direction*12-wing))
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind(); shader.uniform_float('color',(.3,.8,1,1))
        batch_for_shader(shader,'LINE_STRIP',{'pos':points}).draw(shader)

    def _end_display(self, context):
        if getattr(self, '_handle', None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def cancel(self, context):
        try:
            if getattr(self, '_obj', None) and self._obj.name in bpy.data.objects:
                self._obj.location = self._origin
                self._obj.update_tag()
        except ReferenceError:
            pass  # A loaded file may have removed the original object.
        finally:
            self._end_display(context)

    def modal(self, context, event):
        from . import vta_review_ui as ui
        if (event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'} or context.scene != self._scene
                or not ui.loaded_context_matches(context) or ui._analysis_locked()):
            self.cancel(context)
            return {'CANCELLED'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
            self.distance = self._distance
            self._end_display(context)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_x, event.mouse_y))
            self._distance += mouse_depth_delta(mouse - self._mouse, self._screen_axis, fine=event.shift)
            self._mouse = mouse
            self._obj.location = self._origin + self._axis * self._distance
            self._obj.update_tag()
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}
