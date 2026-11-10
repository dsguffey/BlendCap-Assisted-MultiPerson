"""Native sparse editing, whole-pose choices and Blender marker isolation."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace as NS,MethodType
import unittest
from unittest import mock

try:import bpy
except ImportError:bpy=None


@unittest.skipUnless(bpy is not None,'Blender required')
class AnimationEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests as C
        C.setUpClass.__func__(cls)
        cls.edit=importlib.import_module(cls.output.__package__+'.vta_animation_editing')
        cls.ui=importlib.import_module(cls.output.__package__+'.vta_review_ui')
        cls.workspace=importlib.import_module(cls.output.__package__+'.vta_workspace')

    def setUp(self):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests as C
        C.setUp(self)
        self.original_markers=[(m.name,m.frame,m.select,m.camera) for m in self.scene.timeline_markers]
        self.marker=self.scene.timeline_markers.new('Owner marker',frame=3)
        self.addCleanup(self.restore_markers)

    def restore_markers(self):
        for m in tuple(self.scene.timeline_markers):self.scene.timeline_markers.remove(m)
        for name,frame,selected,camera in self.original_markers:
            m=self.scene.timeline_markers.new(name,frame=frame);m.select=selected;m.camera=camera

    def restore_scene(self):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests as C
        C.restore_scene(self)

    def solve(self):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests as C
        return C.solve(self)

    def policy(self,mode='MISSING',ids=None):
        value=self.edit.inspect(self.scene,self.fixture.pid,'s',ids or ['left'],(1,6))
        return dict(value,project_id=self.fixture.pid,source_id='s',mode=mode)

    def remove_pose_frames(self,obj,frames):
        for fc in self.edit.pose_curves(obj):
            for i in range(len(fc.keyframe_points)-1,-1,-1):
                if fc.keyframe_points[i].co.x in frames:fc.keyframe_points.remove(fc.keyframe_points[i],fast=True)
            fc.update()

    def point_rows(self,action):
        return {(fc.data_path,fc.array_index):[(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),
            p.interpolation,p.handle_left_type,p.handle_right_type) for p in fc.keyframe_points]
            for fc in self.output._iter_action_fcurves(action)}

    def test_each_character_and_partial_bone_pose_are_independent(self):
        self.remove_pose_frames(self.obj,{2,3})
        self.remove_pose_frames(self.other,{4})
        # Reinsert just one bone channel at 3; that protects the entire pose.
        self.obj.pose.bones['Hips'].keyframe_insert('rotation_euler',index=0,frame=3)
        p=self.policy(ids=['left','right'])
        self.assertEqual(p['frames']['left'],[1,3,4,5,6])
        self.assertEqual(p['frames']['right'],[1,2,3,5,6])
        self.assertEqual(self.edit.selected_frames(p,'left'),{2})
        self.assertEqual(self.edit.selected_frames(p,'right'),{4})

    def test_entirely_unkeyed_and_fully_keyed_ignore_depth_and_other_objects(self):
        self.assertEqual(self.policy()['states']['left'],'FULL')
        self.remove_pose_frames(self.obj,set(range(1,7)))
        self.obj.keyframe_insert('location',frame=3)
        self.other.keyframe_insert('rotation_euler',frame=4)
        self.assertEqual(self.policy()['states']['left'],'EMPTY')
        self.assertTrue(self.edit.mixed(self.policy(ids=['left','right'])))

    def merge_case(self,mode):
        incoming=self.other.animation_data.action.copy()
        self.addCleanup(lambda: bpy.data.actions.remove(incoming) if incoming.users==0 else None)
        # Incoming new reconstruction is detectably different.
        for fc in self.output._iter_action_fcurves(incoming):
            if fc.data_path.startswith('pose.bones['):
                for p in fc.keyframe_points:p.co.y+=.5
        self.remove_pose_frames(self.obj,{3})
        fc=self.edit.pose_curves(self.obj)[0]
        for p in fc.keyframe_points:
            p.interpolation='BEZIER';p.handle_left_type='FREE';p.handle_right_type='FREE'
            if p.co.x==2:p.co.y+=.37
        self.obj.keyframe_insert('location',frame=3)
        old=self.obj.animation_data.action
        before=self.point_rows(old);policy=self.policy(mode)
        result=self.edit.merge(self.obj,incoming,policy,'left')
        self.addCleanup(lambda: bpy.data.actions.remove(result) if result.users==0 else None)
        after=self.point_rows(result)
        allowed=self.edit.selected_frames(policy,'left')
        for key,points in before.items():
            for p in points:
                if p[0][0] not in allowed or not key[0].startswith('pose.bones['):
                    self.assertIn(p,after[key])
        pose_frames={p[0][0] for key,points in after.items() if key[0].startswith('pose.bones[') for p in points}
        self.assertEqual(pose_frames,set(range(1,7)) if mode!='EXISTING' else {1,2,4,5,6})
        self.assertEqual(self.point_rows(old),before)
        self.assertEqual(self.scene.timeline_markers['Owner marker'].frame,3)

    def test_missing_preserves_manual_poses(self):self.merge_case('MISSING')
    def test_existing_preserves_gaps(self):self.merge_case('EXISTING')
    def test_all_replaces_scope(self):self.merge_case('ALL')

    def test_subframe_and_unsafe_nla_driver_modifier_rejected(self):
        for unsafe in ('NLA','DRIVER','MODIFIER'):
            if unsafe=='NLA':item=self.obj.animation_data.nla_tracks.new()
            elif unsafe=='DRIVER':item=self.obj.driver_add('location',0)
            else:item=self.edit.pose_curves(self.obj)[0].modifiers.new('NOISE')
            with self.assertRaises(ValueError):self.edit.merge(self.obj,self.other.animation_data.action,self.policy(),'left')
            if unsafe=='NLA':self.obj.animation_data.nla_tracks.remove(item)
            elif unsafe=='DRIVER':self.obj.driver_remove('location',0)
            else:self.edit.pose_curves(self.obj)[0].modifiers.remove(item)
        self.obj.pose.bones['Hips'].keyframe_insert('rotation_euler',frame=2.5)
        with self.assertRaisesRegex(ValueError,'subframes'):self.policy()

    def test_pose_snapshot_rejects_later_edit_but_allows_depth(self):
        value=self.policy();self.obj.keyframe_insert('location',frame=3)
        self.edit.require_unchanged(self.scene,value)
        self.obj.pose.bones['Hips'].rotation_euler.x+=.2
        self.obj.pose.bones['Hips'].keyframe_insert('rotation_euler',frame=3)
        with self.assertRaisesRegex(ValueError,'poses changed'):self.edit.require_unchanged(self.scene,value)

    def test_sparse_cleanup_linear_bezier_manual_pose_and_restore_exactness(self):
        for interpolation in ('LINEAR','BEZIER'):
            solve,_=self.solve()
            self.remove_pose_frames(self.obj,{3,4})
            self.obj.pose.bones['Hips'].rotation_euler.x=.7
            self.obj.pose.bones['Hips'].keyframe_insert('rotation_euler',frame=2)
            for fc in self.edit.pose_curves(self.obj):
                for p in fc.keyframe_points:
                    p.interpolation=interpolation;p.handle_left_type='FREE';p.handle_right_type='FREE'
            original=self.cleanup.action_signature(self.obj.animation_data.action)
            before=self.point_rows(self.obj.animation_data.action)
            self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
            after=self.point_rows(self.obj.animation_data.action)
            for key,points in before.items():
                if not key[0].startswith(self.cleanup.ROOT):self.assertEqual(points,after[key])
                else:
                    self.assertEqual([p[0][0] for p in points],[p[0][0] for p in after[key]])
                    self.assertEqual([p[3:] for p in points],[p[3:] for p in after[key]])
            self.cleanup.restore_source(self.obj)
            self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action),original)
            self.assertEqual(self.scene.timeline_markers['Owner marker'].frame,3)

    def test_misaligned_root_keys_refuse_without_inserting(self):
        solve,_=self.solve();fc=self.cleanup.root_curves(self.obj.animation_data.action)[0]
        fc.keyframe_points.remove(next(p for p in fc.keyframe_points if p.co.x==3))
        before=self.point_rows(self.obj.animation_data.action)
        with self.assertRaisesRegex(ValueError,'different frame sets'):self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertEqual(before,self.point_rows(self.obj.animation_data.action))

    def test_dialog_defaults_cancel_and_does_not_start_a_job(self):
        cls=self.ui.BLENDCAP_OT_vta_generate_animation
        op=NS(_editing_snapshot=self.policy(),editing_mode='CANCEL')
        with mock.patch.object(importlib.import_module(self.ui.__package__+'.vta_jobs_ui'),'start_generation') as start:
            self.assertEqual(cls.execute(op,bpy.context),{'CANCELLED'});start.assert_not_called()
        import sys
        prop=cls.__annotations__['editing_mode']
        if isinstance(prop,str):prop=eval(prop,vars(sys.modules[cls.__module__]),vars(cls))
        self.assertEqual(prop.keywords['default'],'CANCEL')

    def test_painted_native_band_does_not_edit_underlying_keys(self):
        ctx=NS(region=NS(type='WINDOW'))
        with mock.patch.object(self.ui,'select_timeline_keys') as review:
            for action in ('BOX','ALL','NONE','DELETE'):
                result=self.workspace.BLENDCAP_OT_vta_timeline_keys.invoke(
                    NS(action=action),ctx,NS(mouse_region_y=20))
                self.assertEqual(result,{'CANCELLED'})
            review.assert_not_called()

    def test_marker_save_reopen_preserves_native_data(self):
        path=self.fixture.root/'marker-save.blend'
        bpy.data.libraries.write(str(path),{self.scene})
        with bpy.data.libraries.load(str(path)) as (a,b):b.scenes=[self.scene.name]
        loaded=b.scenes[0]
        try:self.assertEqual(loaded.timeline_markers['Owner marker'].frame,3)
        finally:bpy.data.scenes.remove(loaded)

    def test_native_generation_application_modes_markers_and_other_character(self):
        from tests.test_blendcap_vta_source_calibration import BlenderCalibrationTests
        generation=importlib.import_module(self.output.__package__+'.vta_generation')
        for mode in ('MISSING','EXISTING','ALL'):
            t=BlenderCalibrationTests(methodName='runTest');t.setUp()
            try:
                f=t.fixture
                def report():
                    path,_=t.calibrated_report(False)
                    data=json.loads(path.read_text())
                    data['plan']['character_scope']=['left','right']
                    path.write_text(json.dumps(data));f.plan=data['plan']
                    return path
                initial=report();rigs,_=self.output.import_reviewed_output(initial,f.location,f.backend)
                obj=next(o for o in rigs if o['vta_character_id']=='left')
                other=next(o for o in rigs if o['vta_character_id']=='right')
                self.remove_pose_frames(obj,{3})
                bone=obj.pose.bones['Hips'];bone.rotation_euler.x=.77;bone.keyframe_insert('rotation_euler',frame=2)
                for fc in self.edit.pose_curves(obj):
                    for p in fc.keyframe_points:p.interpolation='LINEAR'
                obj.keyframe_insert('location',frame=3)
                before=self.point_rows(obj.animation_data.action)
                other_before=self.point_rows(other.animation_data.action)
                # Incoming validated report is selection-scoped to left only.
                path=report();data=json.loads(path.read_text())
                data['plan']['characters']=[c for c in data['plan']['characters'] if c['character_id']=='left']
                data['plan']['character_scope']=['left']
                data['characters']=[c for c in data['characters'] if c['character_id']=='left']
                f.plan=data['plan'];path.write_text(json.dumps(data))
                policy=dict(self.edit.inspect(self.scene,f.pid,'s',['left'],(1,6)),
                    project_id=f.pid,source_id='s',mode=mode)
                generation.import_generation([path],f.location,f.backend,native_edit=policy)
                after=self.point_rows(obj.animation_data.action)
                allowed=self.edit.selected_frames(policy,'left')
                for key,points in before.items():
                    for p in points:
                        if p[0][0] not in allowed or not key[0].startswith('pose.bones['):self.assertIn(p,after[key])
                frames={p[0][0] for key,points in after.items() if key[0].startswith('pose.bones[') for p in points}
                self.assertEqual(frames,set(range(1,7)) if mode!='EXISTING' else {1,2,4,5,6})
                self.assertEqual(other_before,self.point_rows(other.animation_data.action))
                self.assertEqual(self.scene.timeline_markers['Owner marker'].frame,3)
            finally:t.doCleanups()

    def test_shortcuts_marker_delete_and_review_delete_are_isolated(self):
        cls=self.workspace.BLENDCAP_OT_vta_timeline_keys
        ctx=NS(region=NS(type='WINDOW'),scene=self.scene,area=NS(as_pointer=lambda:15))
        event=NS(mouse_region_y=100)
        review=mock.Mock(return_value={'FINISHED'})
        ops=NS(ops=NS(blendcap=NS(vta_review_manage=review)))
        with mock.patch.object(self.workspace,'_expansion_key',return_value='review-only'),\
             mock.patch.object(self.ui,'_review_key_selection',NS(keyframe_ids=('key',))),mock.patch.object(self.workspace,'bpy',ops):
            self.assertEqual(cls.invoke(NS(action='DELETE'),ctx,event),{'FINISHED'})
            review.assert_called_once_with('INVOKE_DEFAULT',action='clear-selected-review-keys')
            review.reset_mock()
            self.assertEqual(cls.invoke(NS(action='DELETE'),ctx,NS(mouse_region_y=20)),{'CANCELLED'})
            review.assert_not_called()

    def test_b_marquee_reuses_review_selection_and_cancel_keeps_selection(self):
        cls=self.workspace.BLENDCAP_OT_vta_timeline_keys
        from tests.test_blendcap_vta_workspace import data
        view=data();region=NS(type='WINDOW',height=240,width=400,x=0,y=0,
            view2d=NS(view_to_region=lambda f,y,clip=False:(f*2,0)))
        ctx=NS(region=region,area=object(),window=NS(cursor_modal_set=mock.Mock(),cursor_modal_restore=mock.Mock()),
               window_manager=NS(modal_handler_add=mock.Mock()))
        for cancel in (True,False):
            op=NS(action='BOX');op.cancel=MethodType(cls.cancel,op)
            event=lambda typ,value='PRESS',x=50,y=120:NS(type=typ,value=value,mouse_region_x=x,mouse_region_y=y,mouse_x=x,mouse_y=y,shift=True)
            with mock.patch.object(self.workspace,'_state',return_value=(view,(),None,0,0,None)),\
                 mock.patch.object(self.workspace,'_scope',return_value=True),mock.patch.object(self.ui,'select_timeline_keys') as selection:
                self.assertEqual(cls.invoke(op,ctx,event('B')),{'RUNNING_MODAL'})
                cls.modal(op,ctx,event('LEFTMOUSE'))
                if cancel:
                    self.assertEqual(cls.modal(op,ctx,event('ESC')),{'CANCELLED'});selection.assert_not_called()
                else:
                    cls.modal(op,ctx,event('MOUSEMOVE',x=120,y=180))
                    self.assertEqual(cls.modal(op,ctx,event('LEFTMOUSE','RELEASE',x=120,y=180)),{'FINISHED'})
                    selection.assert_called_once_with(ctx,('durable-black','durable-red'),extend=True)

    def test_delegated_dialog_is_terminal_without_another_modal_handler(self):
        cls=self.workspace.BLENDCAP_OT_vta_timeline_keys
        ctx=NS(region=NS(type='WINDOW'),window_manager=NS(modal_handler_add=mock.Mock()))
        with mock.patch.object(self.workspace,'_expansion_key',return_value='review-only'),\
             mock.patch.object(self.ui,'_review_key_selection',NS(keyframe_ids=('key',))),\
             mock.patch.object(self.workspace,'bpy',NS(ops=NS(blendcap=NS(
                 vta_review_manage=mock.Mock(return_value={'RUNNING_MODAL'}))))):
            self.assertEqual(cls.invoke(NS(action='DELETE'),ctx,NS(mouse_region_y=100)),{'FINISHED'})
        ctx.window_manager.modal_handler_add.assert_not_called()
