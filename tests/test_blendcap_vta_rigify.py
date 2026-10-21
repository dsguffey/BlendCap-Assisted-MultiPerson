"""Real stock generated Rigify tests, not a renamed synthetic FK hierarchy.

Rigify generation is fixture construction only. Production imports no Rigify API.
"""
import importlib
import json
import math
import unittest
from types import SimpleNamespace
from unittest import mock
try:
    import bpy
    from mathutils import Matrix,Vector
except ImportError:bpy=None


@unittest.skipUnless(bpy is not None,'Blender required')
class RigifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import addon_utils
        from tests.test_blendcap_vta_retarget import RetargetTests
        cls.helpers=RetargetTests;RetargetTests.setUpClass()
        cls.addon=RetargetTests.addon;cls.rt=RetargetTests.rt
        cls.adapter=importlib.import_module(cls.rt.__package__+'.vta_rigify_fk')
        cls.old_scene=bpy.context.scene;cls.objects_before=set(bpy.data.objects);cls.data_before=set(bpy.data.armatures)
        cls.actions_before=set(bpy.data.actions);cls.texts_before=set(bpy.data.texts)
        cls.rigify_was_enabled=addon_utils.check('rigify')[1]
        if not cls.rigify_was_enabled:addon_utils.enable('rigify',default_set=True,persistent=False)
        cls.template_scene=bpy.data.scenes.new('Rigify real test template');bpy.context.window.scene=cls.template_scene
        bpy.ops.object.armature_human_metarig_add();bpy.ops.pose.rigify_generate();cls.template=bpy.context.object

    @classmethod
    def tearDownClass(cls):
        import addon_utils
        bpy.context.window.scene=cls.old_scene
        for o in set(bpy.data.objects)-cls.objects_before:bpy.data.objects.remove(o,do_unlink=True)
        bpy.data.scenes.remove(cls.template_scene)
        for d in set(bpy.data.armatures)-cls.data_before:
            if d.users==0:bpy.data.armatures.remove(d)
        for _ in range(3):
            for a in set(bpy.data.actions)-cls.actions_before:
                if a.users==0:bpy.data.actions.remove(a)
        for t in set(bpy.data.texts)-cls.texts_before:
            if t.users==0:bpy.data.texts.remove(t)
        if not cls.rigify_was_enabled:addon_utils.disable('rigify',default_set=True)
        cls.addon.unregister()

    def setUp(self):
        self.scene=bpy.data.scenes.new('Rigify transaction test');bpy.context.window.scene=self.scene
        self.target=self.template.copy();self.target.data=self.template.data.copy();self.scene.collection.objects.link(self.target)
        self.adapter.RigifyFk.isolate_data_drivers(None,self.target,self.template)
        self.helper=self.helpers();self.helper.rt=self.rt;self.helper.scene=self.scene
        self.actions_before=set(bpy.data.actions)
        bpy.context.view_layer.update()

    def tearDown(self):
        for obj in list(self.scene.objects):
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if isinstance(data,bpy.types.Armature) and data.users==0:bpy.data.armatures.remove(data)
        bpy.context.window.scene=self.template_scene;bpy.data.scenes.remove(self.scene)
        for _ in range(3):
            for action in set(bpy.data.actions)-self.actions_before:
                if action.users==0:bpy.data.actions.remove(action)

    def run_job(self,motion=None,**kwargs):
        job=self.rt.RetargetJob(self.scene,self.target,motion or self.helper.motion(),**kwargs)
        for _ in job.steps():pass
        return job

    def properties(self):
        return {b.name:{key:b[key] for key in props} for name,props in self.adapter.PROPERTY_SPEC.items()
                for b in [self.target.pose.bones[name]]}

    def rest(self):return [(b.name,tuple(map(tuple,b.matrix_local))) for b in self.target.data.bones]

    def test_real_detection_mapping_and_normal_control_layer(self):
        match=self.rt.target_profile(self.target)
        self.assertEqual(match.profile.id,'RIGIFY');self.assertEqual(len(self.rt.validate_target(self.target,self.scene)),22)
        self.assertEqual(match.mapping['hips'],'spine_fk');self.assertEqual(match.mapping['left_forearm'],'forearm_fk.L')
        self.assertEqual(self.target.pose.bones['shoulder.L'].rotation_mode,self.template.pose.bones['shoulder.L'].rotation_mode)
        self.assertNotEqual(self.target.pose.bones['shoulder.L'].rotation_mode,'QUATERNION')
        from tests.test_blendcap_vta_retarget import RecordingLayout
        settings=self.scene.blendcap_vta_retarget;settings.target=self.target;settings.show_mapping=True
        layout=RecordingLayout()
        with mock.patch.object(self.rt,'mapping_preview',return_value=tuple(match.mapping.items())):
            self.assertTrue(self.rt.draw_rig_choices(layout,bpy.context))
        labels=[row[1] for row in layout.calls if row[0]=='label']
        self.assertIn('Resolved: Rigify',labels);self.assertIn('Target control',labels)
        self.assertFalse(any('Mixamo' in label for label in labels))

    def test_no_action_to_editable_fk_action_and_generated_graph_preserved(self):
        before=self.adapter.graph_signature(self.target);rest=self.rest();props=self.properties()
        shapes={b.name:(b.custom_shape,b.custom_shape_transform) for b in self.target.pose.bones}
        meta=dict(self.target.items());data_meta=dict(self.target.data.items())
        object_count=len(self.scene.objects);job=self.run_job()
        self.assertEqual(self.adapter.graph_signature(self.target),before);self.assertEqual(self.rest(),rest)
        self.assertEqual(dict(self.target.items()),meta);self.assertEqual(dict(self.target.data.items()),data_meta)
        self.assertEqual({b.name:(b.custom_shape,b.custom_shape_transform) for b in self.target.pose.bones},shapes)
        self.assertEqual(len(self.scene.objects),object_count)
        paths={f.data_path for f in self.rt._iter_action_fcurves(job.published)}
        self.assertTrue(any('forearm_fk.L' in p for p in paths));self.assertFalse(any(token in p for p in paths for token in ('DEF-','ORG-','MCH-')))
        for name in props:
            if 'IK_FK' in props[name]:self.assertEqual(self.target.pose.bones[name]['IK_FK'],1.)
        self.assertEqual(len(self.target.animation_data.drivers),114);self.assertEqual(len(self.target.data.animation_data.drivers),84)
        self.assertFalse(any(c.name.startswith('VTA') for b in self.target.pose.bones for c in b.constraints))

    def test_existing_and_outside_keys_unchanged_with_explicit_fk_receipt(self):
        bone=self.target.pose.bones['forearm_fk.L'];bone.rotation_quaternion=(1,0,0,0)
        bone.keyframe_insert('rotation_quaternion',frame=1000)
        self.target.pose.bones['upper_arm_parent.L']['IK_FK']=0.
        self.target.pose.bones['upper_arm_parent.L'].keyframe_insert('["IK_FK"]',frame=1000)
        old=self.target.animation_data.action;fingerprint=self.rt.action_fingerprint(self.target)
        job=self.run_job();self.assertIs(job.published['vta_previous_action'],old)
        self.assertEqual(self.rt.action_fingerprint(SimpleNamespace(animation_data=SimpleNamespace(action=old))),fingerprint)
        self.assertTrue(any(k.co.x==1000 for f in self.rt._iter_action_fcurves(old) for k in f.keyframe_points))
        reference=json.loads(job.published['vta_rigify_reference'])
        self.assertEqual(reference['upper_arm_parent.L']['IK_FK'],0.)
        self.assertEqual(self.target.pose.bones['upper_arm_parent.L']['IK_FK'],1.)

    def test_cancel_at_each_stage_restores_properties_pose_graph_and_frame(self):
        for phase in ('Preparing','Baking','Aligning Rigify pelvis','Validating'):
            props=self.properties();state=self.rt._rig_state(self.target);graph=self.adapter.graph_signature(self.target)
            frame=self.scene.frame_current;job=self.rt.RetargetJob(self.scene,self.target,self.helper.motion());stream=job.steps()
            for current,_ in stream:
                if current==phase:break
            stream.close()
            self.assertEqual(self.properties(),props);self.assertEqual(self.rt._rig_state(self.target),state)
            self.assertEqual(self.scene.frame_current,frame);self.assertEqual(self.adapter.graph_signature(self.target),graph)
            self.assertIsNone(self.target.animation_data.action);self.assertFalse(any(o.name.startswith('.VTA') for o in self.scene.objects))

    def test_mid_write_failure_and_publish_failure_restore_original_state(self):
        props=self.properties();graph=self.adapter.graph_signature(self.target);fast=importlib.import_module(self.rt.__package__+'.retarget.bake_fk_fast')
        original=fast._bulk_write_keys
        def fail(*args):
            for value in original(*args):yield value;raise RuntimeError('injected Rigify write')
        with mock.patch.object(fast,'_bulk_write_keys',fail),self.assertRaisesRegex(RuntimeError,'injected Rigify write'):self.run_job()
        self.assertEqual(self.properties(),props);self.assertEqual(self.adapter.graph_signature(self.target),graph)
        def stale():raise ValueError('stale source')
        with self.assertRaisesRegex(ValueError,'stale source'):self.run_job(before_publish=stale)
        self.assertEqual(self.properties(),props);self.assertIsNone(self.target.animation_data.action)

    def test_staging_preserves_and_isolates_all_drivers_and_data(self):
        job=self.rt.RetargetJob(self.scene,self.target,self.helper.motion());stream=job.steps();next(stream)
        stage=next(o for o in self.scene.objects if o.name.startswith('.VTA retarget staging'))
        self.assertIsNot(stage.data,self.target.data)
        self.assertEqual(len(stage.animation_data.drivers),114);self.assertEqual(len(stage.data.animation_data.drivers),84)
        for owner in (stage,stage.data):
            for f in owner.animation_data.drivers:
                for v in f.driver.variables:
                    self.assertTrue(all(t.id not in (self.target,self.target.data) for t in v.targets))
        stream.close()

    def test_reference_rest_alignment_nonunit_scale_and_repeat_bake(self):
        self.target.location=(4,-2,1);self.target.rotation_euler=(0,0,.15);self.target.scale=(.7,)*3
        bpy.context.view_layer.update();world=self.target.matrix_world.copy();rest=self.rest()
        first=self.run_job();self.scene.frame_set(3);bpy.context.view_layer.update()
        aligned=self.target.matrix_world.copy();second=self.run_job()
        self.helper.assert_matrix_close(self.target.matrix_world,aligned);self.assertEqual(self.rest(),rest)
        self.assertIs(second.published['vta_previous_action'],first.published)
        self.scene.frame_set(0);bpy.context.view_layer.update();self.helper.assert_matrix_close(self.target.matrix_world,world)
        for name in second.adapter.observations.values():
            bone=self.target.pose.bones[name];self.helper.assert_matrix_close(bone.matrix,bone.bone.matrix_local,2e-4)
        for frame in (1,2,3):
            self.scene.frame_set(frame);bpy.context.view_layer.update()
            got=self.target.matrix_world@self.target.pose.bones['DEF-spine'].matrix
            self.assertLess((got.translation-Vector((frame*.1,0,1-frame*.02))).length,2e-5)

    def test_customized_missing_and_ambiguous_rigs_refused(self):
        bone=self.target.pose.bones['forearm_fk.L'];constraint=bone.constraints.new('COPY_ROTATION');constraint.target=self.target
        with self.assertRaisesRegex(ValueError,'Customized|unsupported|control state'):self.rt.validate_target(self.target,self.scene)
        bone.constraints.remove(constraint)
        self.target.data.bones['forearm_fk.L'].name='owner altered required bone'
        with self.assertRaisesRegex(ValueError,'Missing required'):self.rt.validate_target(self.target,self.scene)

    def test_external_driver_and_metarig_refused_without_loading_generator(self):
        extra=bpy.data.objects.new('external',None);self.scene.collection.objects.link(extra)
        f=self.target.animation_data.drivers[0];f.driver.variables[0].targets[0].id=extra
        with self.assertRaisesRegex(ValueError,'another object|External'):self.rt.validate_target(self.target,self.scene)
        f.driver.variables[0].targets[0].id=self.target
        del self.target.data['rig_id']
        with self.assertRaisesRegex(ValueError,'already-generated'):self.rt.validate_target(self.target,self.scene)

    def test_unrelated_properties_unchanged_and_normal_fk_keys_edit_deform(self):
        self.target.pose.bones['root']['owner_note']=.42
        self.run_job();self.assertEqual(self.target.pose.bones['root']['owner_note'],.42)
        self.scene.frame_set(2);bpy.context.view_layer.update()
        before=(self.target.matrix_world@self.target.pose.bones['DEF-hand.L'].matrix).translation.copy()
        bone=self.target.pose.bones['forearm_fk.L'];bone.rotation_quaternion.rotate(Matrix.Rotation(.25,4,'Z').to_quaternion())
        bone.keyframe_insert('rotation_quaternion',frame=2);self.scene.frame_set(2);bpy.context.view_layer.update()
        after=(self.target.matrix_world@self.target.pose.bones['DEF-hand.L'].matrix).translation
        self.assertGreater((after-before).length,.01)

    def test_namespaced_standard_rig_keeps_left_right_and_self_references(self):
        for bone in self.target.data.bones:bone.name='Actor:'+bone.name
        match=self.rt.target_profile(self.target);self.assertEqual(match.prefix,'Actor:')
        self.assertEqual(match.mapping['left_hand'],'Actor:hand_fk.L')
        self.assertEqual(match.mapping['right_hand'],'Actor:hand_fk.R')
        before=self.adapter.graph_signature(self.target);job=self.run_job()
        self.assertEqual(before,self.adapter.graph_signature(self.target))
        self.assertEqual(job.published['vta_target_profile'],'RIGIFY')

    def test_graph_mutation_during_bake_refused_and_partial_publication_rolls_back(self):
        job=self.rt.RetargetJob(self.scene,self.target,self.helper.motion());stream=job.steps();next(stream)
        driver=self.target.animation_data.drivers[0];expression=driver.driver.expression
        driver.driver.expression='('+expression+')'
        with self.assertRaisesRegex(ValueError,'graph changed'):
            for _ in stream:pass
        self.assertIsNone(self.target.animation_data.action);driver.driver.expression=expression
        self.target.pose.bones['forearm_fk.L'].keyframe_insert('rotation_quaternion',frame=1000)
        old=self.target.animation_data.action;fingerprint=self.rt.action_fingerprint(self.target)
        props=self.properties();original=self.adapter.RigifyFk.prepare
        def fail(adapter,stage):
            original(adapter,stage)
            if stage is self.target:raise RuntimeError('injected publication failure')
        with mock.patch.object(self.adapter.RigifyFk,'prepare',fail),self.assertRaisesRegex(RuntimeError,'publication failure'):
            self.run_job()
        self.assertEqual(self.properties(),props);self.assertIs(self.target.animation_data.action,old)
        self.assertEqual(self.rt.action_fingerprint(self.target),fingerprint)
        self.assertFalse(any(o.name.startswith('.VTA') for o in self.scene.objects))

    def test_final_motion_includes_object_mode_source_correction(self):
        source=self.helper.rig(prefix='');source.location=(2,-3,.4);source.rotation_euler=(.12,0,.3);source.scale=(1.4,)*3
        for bone in source.pose.bones:
            bone.keyframe_insert('rotation_quaternion',frame=1);bone.keyframe_insert('rotation_quaternion',frame=3)
        bindings=[dict(id=s,bone=n,optional=s.endswith('_toe'),endpoint=self.rt.PROFILE.get(self.rt.ENDPOINTS.get(s))) for s,n in self.rt.PROFILE.items()]
        capture=self.rt.capture_motion(source,bindings,1,3,preserve=(self.target,))
        while True:
            try:next(capture)
            except StopIteration as result:rest,samples=result.value;break
        motion=self.helper.motion();motion['joints']=[dict(id=s,parent=self.rt.PARENTS[s],rest=data['matrix'],direction=data['direction']) for s,data in rest.items()]
        motion['samples']=samples
        # Numeric final-motion envelope is the same contract used after private
        # normalization; no inference or source/project writes are involved.
        self.run_job(motion)
        for sample in samples:
            self.scene.frame_set(sample['frame']);bpy.context.view_layer.update()
            actual=self.target.matrix_world@self.target.pose.bones['DEF-spine'].matrix
            self.assertLess((actual.translation-Vector(sample['poses']['hips'][i][3] for i in range(3))).length,2e-5)
