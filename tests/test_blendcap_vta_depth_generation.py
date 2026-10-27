"""Derived Fixed policy, bounded continuity, user-key authority and Automatic parity."""
import copy
import importlib
import json
import math
from pathlib import Path
import unittest
from unittest import mock

from blendcap_vta_depth_policy import fixed_point, make_policy, previous_policy, require_compatible


class PolicyTests(unittest.TestCase):
    def test_actual_oblique_axis_keeps_perpendicular_motion_and_input(self):
        point=[2.,4.,6.];before=point.copy();n=(1.,2.,3.);origin=(.2,.3,.4)
        result=fixed_point(point,origin,n,5.)
        unit=[x/math.sqrt(14) for x in n];delta=[a-b for a,b in zip(result,point)]
        self.assertAlmostEqual(sum((p-c)*a for p,c,a in zip(result,origin,unit)),5.)
        for d,a in zip(delta,unit):self.assertAlmostEqual(d,sum(x*y for x,y in zip(delta,unit))*a)
        self.assertEqual(point,before)
    def test_saved_reference_survives_later_and_earlier_ranges(self):
        saved=make_policy('FIXED','source','a',frame=30,depth=5.)
        for frame,depth in [(100,10.),(0,2.)]:
            self.assertEqual(make_policy('FIXED','source','a',previous=saved,frame=frame,depth=depth),saved)
    def test_unmarked_output_is_auto_and_policy_switch_requires_full(self):
        fixed=make_policy('FIXED','source','a',frame=0,depth=5)
        for mode,previous in [('FIXED',None),('AUTOMATIC',fixed)]:
            with self.assertRaisesRegex(ValueError,'whole source'):require_compatible(mode,previous,bounded=True)
            require_compatible(mode,previous,bounded=False)
        require_compatible('FIXED',fixed,bounded=True);require_compatible('AUTOMATIC',None,bounded=True)
    def test_identity_checksum_corruption_and_invalid_axis_fail_closed(self):
        record=make_policy('FIXED','source','a',frame=0,depth=5.)
        for source,cid in [('other','a'),('source','b')]:
            with self.assertRaises(ValueError):previous_policy(json.dumps(record),source,cid)
        record['reference_depth']=10
        with self.assertRaises(ValueError):previous_policy(json.dumps(record),'source','a')
        for axis in [(0,0,0),(0,float('nan'),1)]:
            with self.assertRaises(ValueError):fixed_point((1,2,3),(0,0,0),axis,5)
    def test_automatic_has_no_reference_and_same_reference_is_character_specific(self):
        auto=make_policy('AUTOMATIC','source','a')
        self.assertIsNone(auto['reference_depth']);self.assertIsNone(auto['reference_frame'])
        self.assertNotEqual(make_policy('FIXED','source','a',frame=0,depth=5),make_policy('FIXED','source','b',frame=0,depth=7))


class BlenderPolicyTests(unittest.TestCase):
    def setUp(self):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass();self.f=ObjectPlacementTests(methodName='runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.adapter=importlib.import_module(self.f.output.__package__+'.vta_depth_generation')
    def varying(self,bounded=False):
        import numpy as np
        f=self.f;path=f.make_report(bounded);report=json.loads(path.read_text())
        for i,c in enumerate(report['characters']):
            npz=path.parent/c['npz']
            with np.load(npz) as raw:data={k:raw[k].copy() for k in raw.files}
            depths=([5.,5.6,4.8,6.,5.1,4.] if i==0 else [7.,6.9,7.4,6.5,7.1,8.])
            for j,frame in enumerate(c['source_frames']):
                data['cam_t'][j,2]=depths[frame];c['camera_registration']['samples'][j]['pelvis_world_m'][1]=depths[frame]
            np.savez(npz,**data);c['npz_sha256']=f.output._hash(npz)
        path.write_text(json.dumps(report));return path
    def sample(self):
        import bpy
        values={}
        for rig in self.f.rigs:
            rows=[]
            for frame in range(1,7):
                bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
                ev=rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
                rows.append((tuple(ev.matrix_world@ev.pose.bones['Hips'].head),
                             tuple(tuple(b.matrix_basis.to_quaternion()) for b in ev.pose.bones)))
            values[rig['vta_character_id']]=rows
        return values
    def test_fixed_removes_only_depth_after_registered_pose_and_leaves_raw_files(self):
        f=self.f;path=self.varying();before={p:p.read_bytes() for p in path.parent.iterdir()}
        f.output.import_reviewed_output(path,f.location,f.backend,depth_mode='AUTOMATIC');auto=self.sample()
        f.output.import_reviewed_output(path,f.location,f.backend,depth_mode='FIXED');fixed=self.sample()
        for cid in auto:
            for a,b in zip(auto[cid],fixed[cid]):
                self.assertAlmostEqual(a[0][0],b[0][0],places=5);self.assertAlmostEqual(a[0][2],b[0][2],places=5)
                self.assertEqual(a[1],b[1]);self.assertAlmostEqual(b[0][1],fixed[cid][0][0][1],places=5)
        self.assertEqual(before,{p:p.read_bytes() for p in path.parent.iterdir()})
    def test_bounded_fixed_reuses_reference_and_keeps_outside_and_user_location_keys(self):
        import bpy
        f=self.f;f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='FIXED')
        for rig in f.rigs:
            for frame,offset in [(1,.2),(4,.7),(6,.3)]:
                rig.location.y=-5+offset;rig.keyframe_insert('location',frame=frame)
        keys=[f.curves(r) for r in f.rigs];refs=[r['vta_depth_policy'] for r in f.rigs];poses=self.sample()
        f.output.import_reviewed_output(self.varying(True),f.location,f.backend,depth_mode='FIXED')
        for rig,old,ref in zip(f.rigs,keys,refs):
            self.assertEqual(rig['vta_depth_policy'],ref)
            for channel,values in old.items():
                if channel[0]=='location':self.assertEqual(f.curves(rig)[channel],values)
        now=self.sample()
        for cid in poses:
            for index in (0,1,4,5):self.assertEqual(now[cid][index],poses[cid][index])
        # A later full regeneration also retains the established reference.
        f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='FIXED')
        self.assertEqual([r['vta_depth_policy'] for r in f.rigs],refs)
    def test_mode_switch_bounded_refuses_without_mutation_full_auto_restores_raw(self):
        f=self.f;path=self.varying();f.output.import_reviewed_output(path,f.location,f.backend,depth_mode='AUTOMATIC');auto=self.sample()
        before=[f.curves(r) for r in f.rigs]
        with self.assertRaisesRegex(ValueError,'whole source'):
            f.output.import_reviewed_output(self.varying(True),f.location,f.backend,depth_mode='FIXED')
        self.assertEqual(before,[f.curves(r) for r in f.rigs])
        f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='FIXED')
        with self.assertRaisesRegex(ValueError,'whole source'):
            f.output.import_reviewed_output(self.varying(True),f.location,f.backend,depth_mode='AUTOMATIC')
        f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='AUTOMATIC')
        self.assertEqual(self.sample(),auto)
    def test_transaction_rolls_back_if_policy_fails_on_second_character(self):
        import bpy
        f=self.f;before=[f.curves(r) for r in f.rigs];objects=set(bpy.data.objects);camera=bpy.context.scene.camera;actions=set(bpy.data.actions)
        apply=self.adapter.apply;count=0
        def fail(*a,**kw):
            nonlocal count
            count+=1
            if count==2:raise ValueError('injected policy failure')
            return apply(*a,**kw)
        with mock.patch.object(self.adapter,'apply',side_effect=fail),self.assertRaisesRegex(ValueError,'injected'):
            f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='FIXED')
        self.assertEqual(before,[f.curves(r) for r in f.rigs]);self.assertEqual(objects,set(bpy.data.objects))
        self.assertEqual(actions,set(bpy.data.actions));self.assertEqual(camera,bpy.context.scene.camera)
    def test_missing_frames_remain_unavailable_and_are_not_keyed_by_policy(self):
        import bpy
        f=self.f;path=self.varying();report=json.loads(path.read_text())
        for c in report['characters']:
            c['schedule'][2]={'frame':2,'state':'unavailable','pose_source_frame':None}
        f.schedule=report['characters'][0]['schedule'];path.write_text(json.dumps(report))
        f.output.import_reviewed_output(path,f.location,f.backend,depth_mode='FIXED')
        for rig in f.rigs:
            bpy.context.scene.frame_set(3);self.assertTrue(rig.hide_render)
            curves=f.curves(rig)
            self.assertFalse(any(p[0][0]==3 for channel,value in curves.items() if channel[0].endswith('.location') for p in value[-1]))

    def test_save_reload_preserves_reference_and_native_keys(self):
        import bpy
        f=self.f;f.output.import_reviewed_output(self.varying(),f.location,f.backend,depth_mode='FIXED')
        for rig in f.rigs:rig.keyframe_insert('location',frame=4)
        before={r['vta_character_id']:(r['vta_depth_policy'],f.curves(r,True)) for r in f.rigs}
        path=f.root/'fixed-derived.blend';bpy.data.libraries.write(str(path),set(f.rigs))
        with bpy.data.libraries.load(str(path)) as (source,target):target.objects=source.objects
        for rig in target.objects:
            if rig.type=='ARMATURE':
                self.assertEqual((rig['vta_depth_policy'],f.curves(rig,True)),before[rig['vta_character_id']])
                self.adapter.preflight([rig],'FIXED',f.plan['source_video_sha256'],bounded=True)

    def test_checkbox_default_confirmation_accept_cancel_and_scene_guard(self):
        from types import SimpleNamespace as NS
        ui=importlib.import_module(self.f.output.__package__+'.vta_review_ui')
        f=self.f;scene=NS(objects=f.rigs,get=lambda k: f.pid if k=='vta_project_id' else 's',blendcap_vta_automatic_depth=True)
        context=NS(scene=scene,window_manager=NS(invoke_props_dialog=mock.Mock(return_value={'RUNNING_MODAL'})))
        operator=NS(automatic=True,report=mock.Mock())
        prompt=mock.Mock()
        with mock.patch.object(ui,'_analysis_locked',return_value=False),mock.patch.object(ui,'bpy',NS(ops=NS(blendcap=NS(vta_confirm_depth_mode=prompt)))):
            ui._depth_mode_changed(scene,context)
            self.assertFalse(scene.blendcap_vta_automatic_depth)
            prompt.assert_called_once_with('INVOKE_DEFAULT',automatic=True)
            self.assertEqual(ui.BLENDCAP_OT_vta_confirm_depth_mode.invoke(operator,context,None),{'RUNNING_MODAL'})
            # Escape/cancel does not execute and leaves False. Accept changes only the option.
            before=[f.curves(r) for r in f.rigs]
            self.assertEqual(ui.BLENDCAP_OT_vta_confirm_depth_mode.execute(operator,context),{'FINISHED'})
            self.assertTrue(scene.blendcap_vta_automatic_depth);self.assertEqual(before,[f.curves(r) for r in f.rigs])
            self.assertEqual(ui.BLENDCAP_OT_vta_confirm_depth_mode.execute(operator,NS(scene=object())),{'CANCELLED'})
        import inspect
        source=inspect.getsource(ui.register_properties)
        part=source.split('bpy.types.Scene.blendcap_vta_automatic_depth =',1)[1].split('bpy.types.',1)[0]
        self.assertIn('default=False',part);self.assertIn('update=_depth_mode_changed',part)

    def test_new_source_checkbox_does_not_prompt_and_busy_change_is_reverted(self):
        from types import SimpleNamespace as NS
        ui=importlib.import_module(self.f.output.__package__+'.vta_review_ui')
        scene=NS(objects=[],get=lambda k:None,blendcap_vta_automatic_depth=True)
        prompt=mock.Mock()
        with mock.patch.object(ui,'bpy',NS(ops=NS(blendcap=NS(vta_confirm_depth_mode=prompt)))):
            ui._depth_mode_changed(scene,NS(scene=scene));prompt.assert_not_called();self.assertTrue(scene.blendcap_vta_automatic_depth)
            scene.objects=self.f.rigs;scene.get=lambda k:self.f.pid if k=='vta_project_id' else 's'
            with mock.patch.object(ui,'_analysis_locked',return_value=True):ui._depth_mode_changed(scene,NS(scene=scene))
            self.assertFalse(scene.blendcap_vta_automatic_depth);prompt.assert_not_called()

    def test_selected_character_policy_does_not_change_other_character(self):
        f=self.f;path=self.varying();report=json.loads(path.read_text())
        untouched=f.rigs[1];before=f.curves(untouched);metadata=dict(untouched.items())
        report['plan']['character_scope']=['left']
        report['plan']['characters']=[c for c in report['plan']['characters'] if c['character_id']=='left']
        report['characters']=[c for c in report['characters'] if c['character_id']=='left']
        f.plan=report['plan'];path.write_text(json.dumps(report))
        f.output.import_reviewed_output(path,f.location,f.backend,depth_mode='FIXED')
        self.assertEqual(f.curves(untouched),before);self.assertEqual(dict(untouched.items()),metadata)
        self.assertEqual(json.loads(f.rigs[0]['vta_depth_policy'])['mode'],'FIXED')

    def test_import_transaction_receives_captured_mode(self):
        f=self.f;generation=importlib.import_module(f.output.__package__+'.vta_generation')
        with mock.patch.object(f.output,'import_reviewed_output',return_value=((),{})) as publish:
            generation.import_generation(['output'],f.location,f.backend,depth_mode='FIXED')
            self.assertEqual(publish.call_args.kwargs['depth_mode'],'FIXED')
        self.assertIn('whole source',generation.failure_message('Changing Depth mode requires generating the whole source for selected Characters.'))
