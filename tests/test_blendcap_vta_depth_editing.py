"""Native selection/deletion, arbitrary-size depth groups and keying transactions."""
import importlib,json,subprocess,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy
from mathutils import Vector

class DepthEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_depth_tool import DepthToolTests
        cls.base=DepthToolTests;cls.base.setUpClass()
        for n in ('tool','keys','ui','workspace_ui'):setattr(cls,n,getattr(cls.base,n))
    def setUp(self):
        self.base.setUp(self);self.scene=bpy.context.scene
        self.tags={k:self.scene.get(k) for k in ('vta_project_id','vta_source_id')}
        self.scene['vta_project_id']='depth project';self.scene['vta_source_id']='depth source'
        self.camera['vta_project_id']='depth project';self.camera['vta_source_id']='depth source'
        self.addCleanup(self.restore_tags)
    rig=lambda self,*a:self.base.rig(self,*a)
    cleanup=lambda self:self.base.cleanup(self)
    def restore_tags(self):
        for k,v in self.tags.items():
            if v is None:self.scene.pop(k,None)
            else:self.scene[k]=v
    def rigs(self):return self.keys.scene_rigs(self.scene,['a','b'])
    def seed(self):
        for o in (self.a,self.b):
            for f in (5,12):o.keyframe_insert('location',frame=f)
    def group_context(self,objects=None):
        objs=objects or (self.a,self.b)
        for o in bpy.context.selected_objects:o.select_set(False)
        for o in objs:o.select_set(True)
        return mock.patch.multiple(self.ui,_selection_rigs=mock.Mock(return_value=objs),
            loaded_context_matches=mock.Mock(return_value=True),_analysis_locked=mock.Mock(return_value=False),
            cached_scene_source=mock.Mock(return_value=('depth project',{'source_id':'depth source'})))
    def test_plain_click_selects_one_native_xyz_aggregate(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5)
        self.assertEqual(self.keys.selected_frames(self.a),(5.,));self.assertEqual(self.keys.selected_frames(self.b),())
        self.assertTrue(all(c.keyframe_points[0].select_control_point for c in self.keys.location_curves(self.a)))
    def test_additive_multi_character_frame_selection_and_toggle(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5);self.keys.select_frame(self.rigs(),'b',12,extend=True)
        self.assertEqual(self.keys.selected_scene_frames(self.scene,['a','b']),{'a':(5.,),'b':(12.,)})
        self.keys.select_frame(self.rigs(),'a',5,extend=True);self.assertEqual(self.keys.selected_frames(self.a),())
    def test_delete_selected_complete_xyz_but_not_other_character_or_pose(self):
        self.seed();self.a.keyframe_insert('rotation_euler',frame=5)
        self.keys.select_frame(self.rigs(),'a',5)
        self.assertEqual(self.keys.delete_selected(self.scene,['a','b']),1)
        self.assertEqual(self.keys.keyed_frames(self.a),(12.,));self.assertEqual(self.keys.keyed_frames(self.b),(5.,12.))
        curves=self.a.animation_data.action.layers[0].strips[0].channelbag(self.a.animation_data.action_slot).fcurves
        self.assertTrue(any(c.data_path=='rotation_euler' and len(c.keyframe_points)==1 for c in curves))
    def test_subframe_xyz_union_deletes_unselected_axes_at_same_frame(self):
        self.a.keyframe_insert('location',frame=8.5);curves=self.keys.location_curves(self.a)
        for c in curves:
            for k in c.keyframe_points:k.select_control_point=False
        curves[0].keyframe_points[0].select_control_point=True
        self.assertEqual(self.keys.delete_selected(self.scene,['a']),1);self.assertEqual(self.keys.keyed_frames(self.a),())
    def test_delete_leaves_native_interpolation_from_remaining_keys(self):
        for frame,value in [(1,0),(5,10),(9,0)]:
            self.a.location=(value,value,value);self.a.keyframe_insert('location',frame=frame)
        self.scene.frame_set(5);self.assertGreater(self.a.location.length,10)
        self.keys.select_frame(self.rigs(),'a',5);self.keys.delete_selected(self.scene,['a']);self.scene.frame_set(5)
        self.assertLess(self.a.location.length,1e-7);self.assertEqual(self.keys.keyed_frames(self.a),(1.,9.))
    def test_external_key_move_delete_action_swap_selection_reads_live(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5)
        for c in self.keys.location_curves(self.a):c.keyframe_points[0].co.x=7.25;c.update()
        self.assertEqual(self.keys.selected_frames(self.a),(7.25,))
        self.keys.delete_selected(self.scene,['a']);self.assertEqual(self.keys.selected_frames(self.a),())
    def test_empty_depth_click_clears_selection_without_deleting_keys(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5)
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        ctx=NS(scene=self.scene,workspace=bpy.context.workspace,area=area,region=NS(type='WINDOW',view2d=NS(view_to_region=lambda f,y,**kw:(f*10,y),region_to_view=lambda x,y:(x/10,y))))
        view=NS(characters=(NS(character_id='a'),NS(character_id='b')))
        row=NS(kind='depth',character_id='a',y=100)
        with mock.patch.object(self.workspace_ui,'_row_at',return_value=row):self.workspace_ui._depth_interaction(ctx,view,0,self.workspace_ui.TimelineMetrics(),(88,100),NS(type='LEFTMOUSE',ctrl=False,shift=False))
        self.assertEqual(self.keys.selected_scene_frames(self.scene,['a','b']),{});self.assertEqual(self.keys.keyed_frames(self.a),(5.,12.))
    def test_shared_slot_refused_before_any_delete(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5)
        self.b.animation_data.action=self.a.animation_data.action;self.b.animation_data.action_slot=self.a.animation_data.action_slot
        with self.assertRaisesRegex(ValueError,'shared'):self.keys.delete_selected(self.scene,['a','b'])
        self.assertEqual(self.keys.keyed_frames(self.a),(5.,12.))
    def test_separate_action_slots_do_not_cross_select_or_delete(self):
        self.a.keyframe_insert('location',frame=5);action=self.a.animation_data.action
        self.b.animation_data_create();self.b.animation_data.action=action;self.b.animation_data.action_slot=action.slots.new('OBJECT',self.b.name)
        self.b.keyframe_insert('location',frame=9)
        self.keys.select_frame(self.rigs(),'a',5);self.keys.delete_selected(self.scene,['a','b'])
        self.assertEqual(self.keys.keyed_frames(self.a),());self.assertEqual(self.keys.keyed_frames(self.b),(9.,))
    def test_group_resolution_filters_locked_character_and_unrelated_objects(self):
        c=self.rig('third','c');c['vta_source_id']='other';self.b.lock_location[0]=True
        with self.group_context((self.a,self.b)):
            selected,cam=self.tool.targets(bpy.context)
        self.assertEqual(selected,(self.a,));self.assertEqual(cam,self.camera)
        self.assertEqual(c.location,Vector())
    def test_no_eligible_character_refused(self):
        self.a.lock_location[0]=True;self.b.lock_location[0]=True
        with self.group_context(),self.assertRaises(ValueError):self.tool.targets(bpy.context)
    def test_group_target_does_not_depend_on_active_unrelated_object(self):
        bpy.context.view_layer.objects.active=self.camera
        with self.group_context():selected,_cam=self.tool.targets(bpy.context)
        self.assertEqual(set(selected),{self.a,self.b})
    def test_group_pivot_is_stable_visible_handle_centroid(self):
        self.a.location=(1,2,3);self.b.location=(4,5,6);bpy.context.view_layer.update()
        expected=(self.tool.handle_point(self.a)+self.tool.handle_point(self.b))/2
        self.assertLess((self.tool.group_point((self.a,self.b))-expected).length,1e-6)
        self.assertEqual(self.tool.group_point((self.a,self.b)),self.tool.group_point((self.b,self.a)))
    def test_arbitrary_five_character_equal_axis_delta(self):
        objs=[self.a,self.b]+[self.rig('extra '+str(i),'c'+str(i)) for i in range(3)]
        for i,o in enumerate(objs):o.location=(i*.2,i*.3,i*.1)
        bpy.context.view_layer.update();positions=[o.matrix_world.translation.copy() for o in objs]
        for o in objs:self.tool.apply_distance(o,self.camera,.3)
        bpy.context.view_layer.update()
        for o,p in zip(objs,positions):self.assertLess((o.matrix_world.translation-p-self.tool.depth_axis(self.camera)*.3).length,1e-6)
        for i in range(1,len(objs)):
            self.assertLess(((objs[i].matrix_world.translation-objs[0].matrix_world.translation)-(positions[i]-positions[0])).length,1e-6)
    def test_autokey_group_on_all_off_none(self):
        self.scene.frame_set(21);self.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        self.scene.tool_settings.use_keyframe_insert_auto=False;self.tool.finish_group(bpy.context,(self.a,self.b),(True,True))
        self.assertIsNone(self.a.animation_data);self.assertIsNone(self.b.animation_data)
        self.scene.tool_settings.use_keyframe_insert_auto=True;self.tool.finish_group(bpy.context,(self.a,self.b),(True,True))
        for o in (self.a,self.b):self.assertEqual(self.keys.keyed_frames(o),(21.,))
    def test_autokey_failure_rolls_back_all_location_keys_and_existing_action_identity(self):
        self.seed();self.scene.frame_set(20);self.scene.tool_settings.use_keyframe_insert_auto=True
        old=self.a.animation_data.action;state=[self.keys.location_snapshot(o) for o in (self.a,self.b)];original=self.tool.finish_key
        def fail(context,obj,changed):
            if obj==self.b:raise ValueError('Injected second target key failure')
            return original(context,obj,changed)
        with mock.patch.object(self.tool,'finish_key',side_effect=fail),self.assertRaises(ValueError):self.tool.finish_group(bpy.context,(self.a,self.b),(True,True))
        self.assertEqual(self.a.animation_data.action,old)
        for o,s in zip((self.a,self.b),state):self.assertEqual(self.keys.location_snapshot(o),s)
    def test_autokey_failure_rolls_back_new_action(self):
        self.scene.tool_settings.use_keyframe_insert_auto=True;original=self.tool.finish_key
        def fail(ctx,o,c):
            if o==self.b:raise ValueError('second')
            return original(ctx,o,c)
        with mock.patch.object(self.tool,'finish_key',side_effect=fail),self.assertRaises(ValueError):self.tool.finish_group(bpy.context,(self.a,self.b),(True,True))
        self.assertIsNone(self.a.animation_data);self.assertIsNone(self.b.animation_data)
    def test_group_cancel_restores_each_object_without_keys(self):
        rows=[(self.a,self.a.location.copy(),self.tool.local_depth_axis(self.a,self.camera)),(self.b,self.b.location.copy(),self.tool.local_depth_axis(self.b,self.camera))]
        op=NS(_objects=(self.a,self.b),_origins=tuple(r[1] for r in rows),_axes=tuple(r[2] for r in rows),_end=lambda c:None)
        for o,p,a in rows:o.location=p+a*.3
        self.tool.BLENDCAP_OT_vta_depth_drag.cancel(op,bpy.context)
        for o,p,a in rows:self.assertEqual(o.location,p);self.assertIsNone(o.animation_data)
    def test_autokey_failure_removes_new_location_curves_from_existing_action(self):
        self.a.keyframe_insert('rotation_euler',frame=4);action=self.a.animation_data.action
        self.scene.tool_settings.use_keyframe_insert_auto=True;original=self.tool.finish_key
        def fail(ctx,o,c):
            if o==self.b:raise ValueError('second')
            return original(ctx,o,c)
        with mock.patch.object(self.tool,'finish_key',side_effect=fail),self.assertRaises(ValueError):self.tool.finish_group(bpy.context,(self.a,self.b),(True,True))
        self.assertEqual(self.a.animation_data.action,action);self.assertEqual(self.keys.location_curves(self.a),())
        self.assertTrue(all(c.data_path=='rotation_euler' for c in action.layers[0].strips[0].channelbag(self.a.animation_data.action_slot).fcurves))
    def test_delete_poll_is_vta_window_only(self):
        ctx=NS(region=NS(type='WINDOW'))
        with mock.patch.object(self.workspace_ui,'_scope',return_value=False):self.assertFalse(self.workspace_ui.BLENDCAP_OT_vta_depth_delete.poll(ctx))
        with mock.patch.object(self.workspace_ui,'_scope',return_value=True):
            self.assertTrue(self.workspace_ui.BLENDCAP_OT_vta_depth_delete.poll(ctx));ctx.region.type='CHANNELS';self.assertFalse(self.workspace_ui.BLENDCAP_OT_vta_depth_delete.poll(ctx))
    def test_modal_group_release_keys_all_and_preserves_relative_separation(self):
        cls=self.tool.BLENDCAP_OT_vta_depth_drag
        self.scene.tool_settings.use_keyframe_insert_auto=True;self.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        self.a.location=(1,2,3);self.b.location=(3,5,7)
        origins=tuple(o.location.copy() for o in (self.a,self.b));axes=tuple(self.tool.local_depth_axis(o,self.camera) for o in (self.a,self.b))
        op=NS(_objects=(self.a,self.b),_origins=origins,_axes=axes,_camera=self.camera,_scene=self.scene,_frame=self.scene.frame_current_final,
            _mouse=Vector((0,0)),_start_mouse=Vector((0,0)),_screen_axis=Vector((100,0)),_distance=0.,_drag_started=True,_end=lambda c:None,distance=0.)
        op.cancel=lambda c:cls.cancel(op,c)
        with mock.patch.object(self.tool,'targets',return_value=((self.a,self.b),self.camera)):
            self.assertEqual(cls.modal(op,bpy.context,NS(type='MOUSEMOVE',mouse_x=30,mouse_y=10,shift=False)),{'RUNNING_MODAL'})
            for o,p,axis in zip((self.a,self.b),origins,axes):self.assertLess((o.location-p-axis*.3).length,1e-6)
            self.assertEqual(cls.modal(op,bpy.context,NS(type='LEFTMOUSE',value='RELEASE')),{'FINISHED'})
        self.assertLess(((self.b.location-self.a.location)-(origins[1]-origins[0])).length,1e-6)
        for o in (self.a,self.b):self.assertEqual(self.keys.keyed_frames(o),(float(self.scene.frame_current),))
    def test_selected_diamond_draw_state_is_native_not_parallel_metadata(self):
        self.seed();self.keys.select_frame(self.rigs(),'a',5)
        for c in self.keys.location_curves(self.a):
            for k in c.keyframe_points:k.select_control_point=False
        self.assertEqual(self.keys.selected_scene_frames(self.scene,['a','b']),{})
    def test_advanced_camera_preserves_modes_without_exposing_manual_unsupported_path(self):
        from inspect import getsource
        text=getsource(self.ui.VIEW3D_PT_blendcap_vta_generation_camera.draw)
        self.assertIn("'FIRST'",text);self.assertIn("'EVERY'",text);self.assertNotIn("'MANUAL'",text)
        self.assertIn('DEFAULT_CLOSED',self.ui.VIEW3D_PT_blendcap_vta_generation_camera.bl_options)
        self.assertNotIn('fov_mode',getsource(self.ui.VIEW3D_PT_blendcap_vta_animation.draw))
    def test_native_group_and_delete_undo_redo(self):
        with tempfile.TemporaryDirectory() as folder:
            run=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--python-exit-code','1','--python',str(Path(__file__).with_name('vta_depth_edit_native_undo.py')),'--',folder],capture_output=True,text=True,timeout=60)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertTrue((Path(folder)/'verified.json').exists())
