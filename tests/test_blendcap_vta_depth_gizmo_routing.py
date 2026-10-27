"""Native tool-entry visibility and fallback routing missed by math-only proofs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy
from mathutils import Quaternion,Vector


class DepthRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_depth_tool import DepthToolTests
        DepthToolTests.setUpClass();cls.base=DepthToolTests
        for name in ('tool','keys','ui','session','workspace_ui'):setattr(cls,name,getattr(DepthToolTests,name))

    def setUp(self):
        self.base.setUp(self)
        self.tool._reset_activation()
        self.addCleanup(self.tool._reset_activation)
        self.area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        self.region=next(r for r in self.area.regions if r.type=='WINDOW')
        self.flags=(self.area.spaces.active.show_gizmo,self.area.spaces.active.show_gizmo_tool)
        self.addCleanup(self.restore_flags)

    rig=lambda self,*a:self.base.rig(self,*a)
    cleanup=lambda self:self.base.cleanup(self)
    def restore_flags(self):self.area.spaces.active.show_gizmo,self.area.spaces.active.show_gizmo_tool=self.flags

    def register_tool(self):
        self.tool.register()
        def unregister():
            with bpy.context.temp_override(area=self.area,region=self.region):
                bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
            self.tool.unregister()
        self.addCleanup(unregister)

    def test_real_tool_activation_repairs_saved_hidden_viewport_once(self):
        self.register_tool()
        with bpy.context.temp_override(area=self.area,region=self.region), \
                mock.patch.object(self.tool,'tool_visible',return_value=True):
            self.area.spaces.active.show_gizmo=False;self.area.spaces.active.show_gizmo_tool=False
            self.assertEqual(bpy.ops.wm.tool_set_by_id(name=self.tool.TOOL_ID),{'FINISHED'})
            ref=bpy.context.workspace.tools.from_space_view3d_mode('OBJECT',create=False)
            self.assertEqual(ref.widget,'BLENDCAP_GGT_vta_depth')
            self.assertIsNotNone(ref.gizmo_group_properties(ref.widget))
            self.tool.observe_tool_activation(bpy.context)
            self.assertTrue(self.area.spaces.active.show_gizmo and self.area.spaces.active.show_gizmo_tool)
            # An unchanged tick must not override a subsequent explicit hide.
            self.area.spaces.active.show_gizmo=False
            self.tool.observe_tool_activation(bpy.context)
            self.assertFalse(self.area.spaces.active.show_gizmo)
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box');self.tool.observe_tool_activation(bpy.context)
            bpy.ops.wm.tool_set_by_id(name=self.tool.TOOL_ID);self.tool.observe_tool_activation(bpy.context)
            self.assertTrue(self.area.spaces.active.show_gizmo)

    def test_visibility_setup_never_loads_project_or_changes_main_scene(self):
        self.register_tool()
        with bpy.context.temp_override(area=self.area,region=self.region), \
                mock.patch.object(self.tool,'tool_visible',return_value=True), \
                mock.patch.object(self.ui,'_get_backend',side_effect=AssertionError('No project reads')):
            bpy.ops.wm.tool_set_by_id(name=self.tool.TOOL_ID)
            before=bpy.context.window.scene
            self.tool.observe_tool_activation(bpy.context)
            self.assertEqual(bpy.context.window.scene,before)

    def test_mode_mismatch_does_not_enable_unrelated_viewport(self):
        self.register_tool()
        self.area.spaces.active.show_gizmo=False
        with bpy.context.temp_override(area=self.area,region=self.region),mock.patch.object(self.tool,'tool_visible',return_value=False):
            self.tool.observe_tool_activation(bpy.context)
        self.assertFalse(self.area.spaces.active.show_gizmo)

    def test_native_left_select_fallback_exists_and_tool_masks_drag(self):
        path=Path(bpy.utils.system_resource('SCRIPTS'))/'presets/keyconfig/keymap_data/blender_default.py'
        spec=importlib.util.spec_from_file_location('depth_native_keymap',path)
        native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
        fallbacks=[(name,op,event) for name,ctx,data in native.generate_keymaps(native.Params(select_mouse='LEFT',use_fallback_tool=True))
            if name in {'3D View','Object Mode'} for op,event,props in data['items']
            if op=='transform.translate' and event.get('type')=='LEFTMOUSE' and event.get('value')=='CLICK_DRAG']
        self.assertTrue(fallbacks)
        drag=[op for op,event,props in self.tool.BLENDCAP_WST_vta_depth.bl_keymap
              if event['type']=='LEFTMOUSE' and event['value']=='CLICK_DRAG']
        self.assertEqual(drag,['blendcap.vta_depth_block_drag'])
        self.assertFalse(any(op.startswith('transform.') for op,event,props in self.tool.BLENDCAP_WST_vta_depth.bl_keymap))

    def test_native_block_operator_is_inert_even_with_auto_key_enabled(self):
        self.register_tool()
        self.a.select_set(True);bpy.context.view_layer.objects.active=self.a
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        before=self.a.matrix_world.copy()
        with bpy.context.temp_override(area=self.area,region=self.region),mock.patch.object(self.tool,'tool_visible',return_value=True):
            bpy.ops.wm.tool_set_by_id(name=self.tool.TOOL_ID)
            self.assertEqual(bpy.ops.blendcap.vta_depth_block_drag('INVOKE_DEFAULT'),{'FINISHED'})
            self.assertEqual(bpy.ops.blendcap.vta_depth_block_drag('EXEC_DEFAULT'),{'FINISHED'})
        self.assertEqual(self.a.matrix_world,before)
        self.assertIsNone(self.a.animation_data)

    def test_block_only_polls_for_this_active_tool_in_vta(self):
        self.register_tool()
        with bpy.context.temp_override(area=self.area,region=self.region),mock.patch.object(self.tool,'tool_visible',return_value=True):
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
            self.assertFalse(self.tool.BLENDCAP_OT_vta_depth_block_drag.poll(bpy.context))
            bpy.ops.wm.tool_set_by_id(name=self.tool.TOOL_ID)
            self.assertTrue(self.tool.BLENDCAP_OT_vta_depth_block_drag.poll(bpy.context))
            with mock.patch.object(self.tool,'tool_visible',return_value=False):
                self.assertFalse(self.tool.BLENDCAP_OT_vta_depth_block_drag.poll(bpy.context))

    def test_native_vector_icon_loads_and_group_scales_with_viewport(self):
        path=Path(self.tool.BLENDCAP_WST_vta_depth.bl_icon+'.dat')
        self.assertTrue(path.is_file())
        icon=bpy.app.icons.new_triangles_from_file(str(path))
        self.assertGreater(icon,0);bpy.app.icons.release(icon)
        self.assertIn('SCALE',self.tool.BLENDCAP_GGT_vta_depth.bl_options)
        builder=Path(__file__).resolve().parents[1]/'tools/build_vta_depth_icon.py'
        spec=importlib.util.spec_from_file_location('depth_icon_builder',builder)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(path.read_bytes(),module.icon_bytes())

    def test_modal_world_delta_collinear_in_head_on_side_and_oblique_views(self):
        camera_rotation=self.camera.matrix_world.to_quaternion()
        views=(camera_rotation,camera_rotation@Quaternion((0,1,0),1.570796),Quaternion((1,.3,.6),1.1))
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=False
        for view_rotation in views:
            self.a.location=(0,0,0);bpy.context.view_layer.update()
            op=self.base.modal_operator(self)
            projected=view_rotation.inverted()@self.tool.depth_axis(self.camera)
            axis=Vector((projected.x,projected.y))*100
            op._screen_axis=axis if axis.length>=8 else Vector((0,100))
            origin=self.a.matrix_world.translation.copy();rotation=self.a.rotation_euler.copy();scale=self.a.scale.copy()
            with mock.patch.object(self.tool,'target',return_value=(self.a,self.camera)):
                self.tool.BLENDCAP_OT_vta_depth_drag.modal(op,bpy.context,NS(type='MOUSEMOVE',mouse_x=60,mouse_y=45,shift=False))
                self.tool.BLENDCAP_OT_vta_depth_drag.modal(op,bpy.context,NS(type='LEFTMOUSE',value='RELEASE'))
            bpy.context.view_layer.update()
            delta=self.a.matrix_world.translation-origin;depth=self.tool.depth_axis(self.camera)
            self.assertLess((delta-depth*delta.dot(depth)).length,1e-6)
            self.assertEqual(self.a.rotation_euler,rotation);self.assertEqual(self.a.scale,scale)
