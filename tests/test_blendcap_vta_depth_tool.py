"""Real native location Actions plus constrained depth interaction contracts."""
import importlib
import math
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy
from mathutils import Matrix, Vector


class DepthToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        name = BlenderOutputTests.addon.__name__ + '.blendcap.'
        cls.tool = importlib.import_module(name + 'vta_depth_tool')
        cls.keys = importlib.import_module(name + 'vta_depth_keys')
        cls.ui = importlib.import_module(name + 'vta_review_ui')
        cls.session = importlib.import_module(name + 'vta_scene_session')
        cls.workspace_ui = importlib.import_module(name + 'vta_workspace')

    def setUp(self):
        self.objects = set(bpy.data.objects)
        self.actions = set(bpy.data.actions)
        self.autokey = bpy.context.scene.tool_settings.use_keyframe_insert_auto
        self.autokey_mode = bpy.context.scene.tool_settings.auto_keying_mode
        self.frame = bpy.context.scene.frame_current
        self.a = self.rig('depth A', 'a')
        self.b = self.rig('depth B', 'b')
        self.camera = bpy.data.objects.new('depth source camera', bpy.data.cameras.new('depth camera'))
        bpy.context.collection.objects.link(self.camera)
        self.camera.rotation_euler = (.2, -.4, .7)
        bpy.context.view_layer.update()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for obj in set(bpy.data.objects) - self.objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for action in set(bpy.data.actions) - self.actions:
            bpy.data.actions.remove(action)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = self.autokey
        bpy.context.scene.tool_settings.auto_keying_mode = self.autokey_mode
        bpy.context.scene.frame_set(self.frame)

    def rig(self, name, cid):
        obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
        bpy.context.collection.objects.link(obj)
        obj['vta_character_id'] = cid
        obj['vta_project_id'] = 'depth project'
        obj['vta_source_id'] = 'depth source'
        return obj

    def test_camera_axis_equivalence_parent_scale_no_rotation_or_scale_change(self):
        parent = bpy.data.objects.new('depth parent', None)
        bpy.context.collection.objects.link(parent)
        parent.rotation_euler = (.4, .2, -.3)
        parent.scale = (2, 3, 4)
        self.a.parent = parent
        self.a.location = (2, 3, 4)
        self.a.matrix_parent_inverse = Matrix.Translation((1, -2, 3))
        bpy.context.view_layer.update()
        before = self.a.matrix_world.translation.copy()
        rotation, scale, other = self.a.rotation_euler.copy(), self.a.scale.copy(), self.b.matrix_world.copy()
        self.tool.apply_distance(self.a, self.camera, 1.25)
        bpy.context.view_layer.update()
        delta = self.a.matrix_world.translation - before
        self.assertLess((delta - self.tool.depth_axis(self.camera)*1.25).length, 2e-6)
        self.assertEqual(self.a.rotation_euler, rotation)
        self.assertEqual(self.a.scale, scale)
        self.assertEqual(self.b.matrix_world, other)

    def test_nonfinite_distance_rejected(self):
        before = self.a.location.copy()
        with self.assertRaises(ValueError): self.tool.apply_distance(self.a, self.camera, math.nan)
        self.assertEqual(self.a.location, before)

    def test_auto_off_and_noop_create_no_animation(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        self.tool.apply_distance(self.a, self.camera, .4)
        self.assertFalse(self.tool.finish_key(bpy.context, self.a, True))
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        self.assertFalse(self.tool.finish_key(bpy.context, self.a, False))
        self.assertIsNone(self.a.animation_data)

    def test_pre_layered_action_read_fallback(self):
        curve=NS(data_path='location',array_index=0,keyframe_points=[NS(co=Vector((4,1)))])
        obj=NS(animation_data=NS(action=NS(fcurves=(curve,NS(data_path='rotation_euler',array_index=0)))))
        self.assertEqual(self.keys.keyed_frames(obj),(4.,))

    def test_auto_native_location_key_update_no_duplicate_no_pose_or_scale(self):
        bpy.context.scene.frame_set(12)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        bpy.context.scene.tool_settings.auto_keying_mode = 'ADD_REPLACE_KEYS'
        for distance in (.1, .2, -.3):
            self.tool.apply_distance(self.a, self.camera, distance)
            self.assertTrue(self.tool.finish_key(bpy.context, self.a, True))
        self.assertEqual(self.keys.keyed_frames(self.a), (12.,))
        curves = self.keys.location_curves(self.a)
        self.assertEqual(len(curves), 3)
        self.assertEqual({c.data_path for c in curves}, {'location'})
        self.assertTrue(all(len(c.keyframe_points) == 1 for c in curves))
        self.assertIsNone(self.b.animation_data)

    def test_replace_keys_autokey_does_not_create_new_frame(self):
        self.a.keyframe_insert('location', frame=5)
        bpy.context.scene.frame_set(6)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        bpy.context.scene.tool_settings.auto_keying_mode = 'REPLACE_KEYS'
        self.assertFalse(self.tool.finish_key(bpy.context, self.a, True))
        self.assertEqual(self.keys.keyed_frames(self.a), (5.,))
        bpy.context.scene.frame_set(5)
        self.assertTrue(self.tool.finish_key(bpy.context, self.a, True))

    def test_manual_location_insert_move_delete_subframe_marker_reads_live_data(self):
        self.a.keyframe_insert('location', frame=7)
        self.a.keyframe_insert('rotation_euler', frame=30)
        curves = self.keys.location_curves(self.a)
        self.assertEqual(self.keys.keyed_frames(self.a), (7.,))
        for curve in curves:
            curve.keyframe_points[0].co.x = 8.5
            curve.update()
        self.assertEqual(self.keys.keyed_frames(self.a), (8.5,))
        for curve in curves: curve.keyframe_points.remove(curve.keyframe_points[0])
        self.assertEqual(self.keys.keyed_frames(self.a), ())

    def test_native_builtin_location_keying_set_targets_object_not_pose(self):
        # The unmodified Blender Location keying set used by I/Keying menu.
        import keyingsets_builtins
        paths = []
        keyingsets_builtins.BUILTIN_KSI_Location.generate(None, bpy.context,
            NS(paths=NS(add=lambda owner, path, **kw: paths.append((owner, path)))), self.a)
        self.assertEqual(paths, [(self.a, 'location')])

    def test_native_location_insert_operator_uses_selected_object(self):
        for obj in bpy.context.selected_objects: obj.select_set(False)
        bpy.context.view_layer.objects.active = self.a
        self.a.select_set(True)
        bpy.context.scene.frame_set(14)
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(area=area,region=region):
            self.assertEqual(bpy.ops.anim.keyframe_insert(type='Location'), {'FINISHED'})
        self.assertEqual(self.keys.keyed_frames(self.a), (14.,))
        self.assertIsNone(self.b.animation_data)

    def test_marker_uses_native_frame_and_supplied_depth_child_center(self):
        self.a.keyframe_insert('location', frame=11.5)
        context=NS(scene=bpy.context.scene,region=NS(view2d=NS(view_to_region=lambda frame,y,**kw:(frame*10, y))))
        with mock.patch.object(self.keys,'scene_keyed_frames',return_value={'a':self.keys.keyed_frames(self.a)}):
            self.assertEqual(self.workspace_ui.depth_key_markers(context,{'a':100},2),
                             (('a',11.5,115.,100),))

    def test_shared_animation_slot_refused_before_key_mutation(self):
        self.a.keyframe_insert('location',frame=3)
        self.b.animation_data_create()
        self.b.animation_data.action=self.a.animation_data.action
        self.b.animation_data.action_slot=self.a.animation_data.action_slot
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        bpy.context.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        with self.assertRaisesRegex(ValueError,'shared'):self.tool.finish_key(bpy.context,self.a,True)
        self.assertEqual(self.keys.keyed_frames(self.a),(3.,))

    def test_scene_key_markers_are_local_and_reject_duplicate_rigs(self):
        scene = bpy.data.scenes.new('depth other Scene')
        try:
            scene['vta_project_id'], scene['vta_source_id'] = 'depth project', 'depth source'
            scene.collection.objects.link(self.a)
            self.a.keyframe_insert('location', frame=3)
            self.b.keyframe_insert('location', frame=9)
            self.assertEqual(self.keys.scene_keyed_frames(scene, ('a','b')), {'a':(3.,)})
            duplicate = self.rig('depth duplicate', 'a')
            scene.collection.objects.link(duplicate)
            self.assertEqual(self.keys.scene_keyed_frames(scene, ('a',)), {})
        finally: bpy.data.scenes.remove(scene)

    def test_layered_action_slots_do_not_cross_objects(self):
        self.a.keyframe_insert('location', frame=3)
        action = self.a.animation_data.action
        self.b.animation_data_create()
        self.b.animation_data.action = action
        slot = action.slots.new('OBJECT', self.b.name)
        self.b.animation_data.action_slot = slot
        self.b.keyframe_insert('location', frame=99)
        self.assertEqual(self.keys.keyed_frames(self.a), (3.,))
        self.assertEqual(self.keys.keyed_frames(self.b), (99.,))

    def test_authoritative_mode_visibility_not_workspace_name(self):
        workspace = NS(blendcap_vta_mode_enabled=True, blendcap_vta_loaded_scene=None)
        owned = {'vta_scene_owner_version':1,'vta_scene_owner_id':'test'}
        workspace.blendcap_vta_loaded_scene = owned
        workspace.screens = ()
        context = NS(mode='OBJECT',workspace=workspace,window=NS(scene=owned))
        self.assertTrue(self.tool.tool_visible(context))
        workspace.blendcap_vta_mode_enabled=False
        self.assertFalse(self.tool.tool_visible(context))
        workspace.blendcap_vta_mode_enabled=True
        context.window.scene={}
        self.assertFalse(self.tool.tool_visible(context))
        context.window.scene=owned
        self.assertTrue(self.tool.tool_visible(context))
        context.mode='POSE'
        self.assertFalse(self.tool.tool_visible(context))

    def test_target_follows_existing_selection_and_rejects_deselected_or_unowned(self):
        context = NS(mode='OBJECT')
        with mock.patch.object(self.tool,'tool_visible',return_value=True), \
                mock.patch.object(self.ui,'_analysis_locked',return_value=False), \
                mock.patch.object(self.ui,'_selection_rigs',return_value=(self.a,self.b)), \
                mock.patch.object(self.tool,'depth_target',return_value=(self.a,self.camera)) as resolve:
            self.assertEqual(self.tool.target(context), (self.a,self.camera))
            resolve.return_value = (self.b,self.camera)
            self.assertEqual(self.tool.target(context), (self.b,self.camera))
            resolve.side_effect = ValueError('deselected')
            with self.assertRaises(ValueError): self.tool.target(context)
            resolve.side_effect = None
            resolve.return_value=(self.camera,self.camera)
            with self.assertRaises(ValueError): self.tool.target(context)

    def modal_operator(self):
        # Exercise registered operator methods without a GUI event injector.
        cls = self.tool.BLENDCAP_OT_vta_depth_drag
        op = NS(_obj=self.a,_camera=self.camera,_scene=bpy.context.scene,
            _frame=bpy.context.scene.frame_current_final,_origin=self.a.location.copy(),
            _axis=self.tool.local_depth_axis(self.a,self.camera),_mouse=Vector((0,0)),
            _start_mouse=Vector((0,0)),_drag_started=False,
            _screen_axis=Vector((100,0)),_distance=0.,distance=0.,report=mock.Mock())
        op._end = lambda context: None
        op.cancel = lambda context: cls.cancel(op,context)
        return op

    def test_modal_xyz_ignored_drag_axis_only_release_autokey(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        bpy.context.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        op = self.modal_operator()
        cls = self.tool.BLENDCAP_OT_vta_depth_drag
        with mock.patch.object(self.tool,'target',return_value=(self.a,self.camera)):
            for key in ('X','Y','Z','G','RET'):
                self.assertEqual(cls.modal(op,bpy.context,NS(type=key,value='PRESS')), {'RUNNING_MODAL'})
                self.assertEqual(self.a.location,op._origin)
            cls.modal(op,bpy.context,NS(type='MOUSEMOVE',mouse_x=50,mouse_y=88,shift=False))
            self.assertLess((self.a.location - op._origin - op._axis*.5).length,1e-7)
            self.assertIsNone(self.a.animation_data)
            self.assertEqual(cls.modal(op,bpy.context,NS(type='LEFTMOUSE',value='RELEASE')), {'FINISHED'})
            self.assertEqual(self.keys.keyed_frames(self.a),(float(bpy.context.scene.frame_current),))

    def test_cancel_and_frame_change_restore_transform_without_keys(self):
        cls = self.tool.BLENDCAP_OT_vta_depth_drag
        for event in ('ESC','RIGHTMOUSE','WINDOW_DEACTIVATE','FRAME_CHANGE','TARGET_CHANGE'):
            op = self.modal_operator()
            self.a.location = op._origin + op._axis*.3
            if event == 'FRAME_CHANGE': op._frame -= 1
            selected = (self.b,self.camera) if event=='TARGET_CHANGE' else (self.a,self.camera)
            with mock.patch.object(self.tool,'target',return_value=selected):
                self.assertEqual(cls.modal(op,bpy.context,NS(type=event)), {'CANCELLED'})
            self.assertEqual(self.a.location,op._origin)
            self.assertIsNone(self.a.animation_data)

    def test_click_without_drag_is_noop_with_autokey_enabled(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        op=self.modal_operator()
        with mock.patch.object(self.tool,'target',return_value=(self.a,self.camera)):
            cls=self.tool.BLENDCAP_OT_vta_depth_drag
            cls.modal(op,bpy.context,NS(type='MOUSEMOVE',mouse_x=1,mouse_y=0,shift=False))
            self.assertEqual(self.a.location,op._origin)
            self.assertEqual(cls.modal(op,bpy.context,NS(type='LEFTMOUSE',value='RELEASE')),{'FINISHED'})
        self.assertIsNone(self.a.animation_data)

    def test_gizmo_frame_follows_character_and_hides_when_target_disappears(self):
        group=NS(arrow=NS(),ring=NS())
        method=self.tool.BLENDCAP_GGT_vta_depth.draw_prepare
        with mock.patch.object(self.tool,'target',return_value=(self.a,self.camera)) as resolve:
            self.a.location=(1,2,3);bpy.context.view_layer.update()
            method(group,bpy.context)
            self.assertLess((group.arrow.matrix_basis.translation-self.a.matrix_world.translation).length,1e-6)
            resolve.return_value=(self.b,self.camera)
            self.b.location=(-3,4,5);bpy.context.view_layer.update()
            method(group,bpy.context)
            self.assertLess((group.ring.matrix_basis.translation-self.b.matrix_world.translation).length,1e-6)
            z=group.arrow.matrix_basis.to_3x3()@Vector((0,0,1))
            self.assertLess((z-self.tool.depth_axis(self.camera)).length,1e-6)
            resolve.side_effect=ValueError('No selected VTA Character')
            method(group,bpy.context)
            self.assertTrue(group.arrow.hide and group.ring.hide)

    def test_shared_scene_object_refused(self):
        other=bpy.data.scenes.new('linked depth Scene')
        try:
            other.collection.objects.link(self.a)
            with mock.patch.object(self.tool,'tool_visible',return_value=True), \
                    mock.patch.object(self.ui,'_analysis_locked',return_value=False), \
                    mock.patch.object(self.ui,'_selection_rigs',return_value=(self.a,)), \
                    mock.patch.object(self.tool,'depth_target',return_value=(self.a,self.camera)):
                with self.assertRaisesRegex(ValueError,'one Scene'):self.tool.target(bpy.context)
        finally:bpy.data.scenes.remove(other)

    def test_screen_projection_and_head_on_fallback(self):
        import bpy_extras.view3d_utils as utils
        context = NS(region=NS(),space_data=NS(region_3d=NS()))
        for projected, expected in [([Vector((0,0)),Vector((1,0))],(100,0)),
                                     ([Vector((0,0)),Vector((0,0))],(0,100))]:
            with mock.patch.object(utils,'location_3d_to_region_2d',side_effect=projected), \
                    mock.patch.object(utils,'region_2d_to_location_3d',return_value=Vector((.01,0,0))):
                self.assertLess((self.tool.screen_axis(context,Vector((0,0,0)),self.camera)-Vector(expected)).length,1e-5)

    def test_dynamic_toolbar_register_unregister_lifecycle(self):
        from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active as toolbar
        before = list(toolbar._tools['OBJECT'])
        self.tool.register()
        try:
            self.assertIn(self.tool.depth_tools,toolbar._tools['OBJECT'])
            self.assertEqual([v.idname for v in self.tool.depth_tools(None)],[self.tool.TOOL_ID])
            with mock.patch.object(self.tool,'tool_visible',return_value=False):
                self.assertEqual(list(self.tool.depth_tools(bpy.context)),[])
            with mock.patch.object(self.tool,'tool_visible',return_value=True):
                self.assertEqual([v.idname for v in self.tool.depth_tools(bpy.context)],[self.tool.TOOL_ID])
            area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
            region=next(r for r in area.regions if r.type=='WINDOW')
            with bpy.context.temp_override(area=area,region=region):
                from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
                for enabled in (False,True,False,True):
                    with mock.patch.object(self.ui,'loaded_context_matches',return_value=enabled):
                        ids=[v.idname for v in ToolSelectPanelHelper._tools_flatten(toolbar.tools_from_context(bpy.context)) if v]
                        self.assertEqual(self.tool.TOOL_ID in ids,enabled)
            self.assertIsNotNone(bpy.types.GizmoGroup.bl_rna_get_subclass_py('BLENDCAP_GGT_vta_depth'))
        finally: self.tool.unregister()
        self.assertEqual(toolbar._tools['OBJECT'],before)

    def test_location_keying_preserves_existing_pose_and_rotation_curves(self):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        fixture=BlenderOutputTests()
        self.addCleanup(fixture.doCleanups)
        obj=fixture.rig('depth existing generated pose',[0,1,2])
        obj.keyframe_insert('rotation_euler',frame=20)
        action=obj.animation_data.action
        slot=obj.animation_data.action_slot
        bag=action.layers[0].strips[0].channelbag(slot)
        def non_location():
            return {(c.data_path,c.array_index):tuple((tuple(k.co),k.interpolation) for k in c.keyframe_points)
                    for c in bag.fcurves if c.data_path!='location'}
        before=non_location()
        self.assertTrue(any('pose.bones' in path for path,index in before))
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        bpy.context.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        self.tool.apply_distance(obj,self.camera,.75)
        self.tool.finish_key(bpy.context,obj,True)
        self.assertEqual(non_location(),before)
        self.assertEqual(obj.animation_data.action,action)

    def test_native_undo_redo_in_fresh_background_blender(self):
        # Native undo invalidates RNA pointers; isolate from shared fixtures.
        with tempfile.TemporaryDirectory() as folder:
            command=[bpy.app.binary_path,'--background','--factory-startup','--python-exit-code','1',
                     '--python',str(Path(__file__).with_name('vta_depth_native_undo.py')),'--',folder]
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue((Path(folder)/'verified.txt').is_file())


if __name__ == '__main__': unittest.main()
