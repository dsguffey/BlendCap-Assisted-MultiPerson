"""Production FK adapter regressions; synthetic local rigs, no third-party assets."""
import copy
import importlib
import math
import hashlib
from types import SimpleNamespace
import unittest
from unittest import mock
try:
    import bpy
    from mathutils import Matrix,Vector,Quaternion
except ImportError:bpy=None


@unittest.skipUnless(bpy is not None,'Blender required')
class RetargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass();cls.addon=BlenderOutputTests.addon;cls.addon.register()
        cls.rt=importlib.import_module(cls.addon.__name__+'.blendcap.vta_retarget')
        cls.fast=importlib.import_module(cls.addon.__name__+'.blendcap.retarget.bake_fk_fast')

    @classmethod
    def tearDownClass(cls):cls.addon.unregister()

    def setUp(self):
        self.old_scene=bpy.context.scene;self.scene=bpy.data.scenes.new('retarget unit')
        bpy.context.window.scene=self.scene;self.before_actions=set(bpy.data.actions)

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(self.scene.objects):
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if data and data.users==0 and isinstance(data,bpy.types.Armature):bpy.data.armatures.remove(data)
        bpy.context.window.scene=self.old_scene;bpy.data.scenes.remove(self.scene)
        for _ in range(3):
            for action in set(bpy.data.actions)-self.before_actions:
                if action.users==0:bpy.data.actions.remove(action)

    def points(self,apose=False):
        points={'hips':(0,0,1),'spine_lower':(0,0,1.12),'spine_middle':(0,0,1.3),
                'chest':(0,0,1.45),'neck':(0,0,1.58),'head':(0,0,1.72)}
        for side,sign in (('left',1),('right',-1)):
            for s,p in [('clavicle',(.09,0,1.45)),('upper_arm',(.22,0,1.45)),
                    ('forearm',(.55,0,1.25 if apose else 1.45)),('hand',(.83,0,1.1 if apose else 1.45)),
                    ('thigh',(.09,0,.97)),('shin',(.09,0,.5)),('foot',(.09,0,.07)),('toe',(.09,-.12,.02))]:
                points[side+'_'+s]=(sign*p[0],p[1],p[2])
        return {s:Vector(p) for s,p in points.items()}

    def rig(self,*,prefix='mixamorig:',rotation=.0,scale=1.,toes=True,names=None):
        data=bpy.data.armatures.new('test data');obj=bpy.data.objects.new('test rig',data);self.scene.collection.objects.link(obj)
        obj.matrix_world=Matrix.Rotation(rotation,4,'X')@Matrix.Scale(scale,4)
        obj.select_set(True);bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
        for s,bone_name in (names or self.rt.PROFILE).items():
            if not toes and s.endswith('_toe'):continue
            p=self.points()[s];m=obj.matrix_world.inverted()@Matrix.Translation(p)
            b=data.edit_bones.new(prefix+bone_name);b.head=m.translation;b.tail=m.translation+Vector((0,.1/scale,0));b.matrix=m;b.length=.1/scale
            parent=self.rt.PARENTS[s]
            if parent:b.parent=data.edit_bones[prefix+(names or self.rt.PROFILE)[parent]]
        bpy.ops.object.mode_set(mode='OBJECT')
        for b in obj.pose.bones:b.rotation_mode='QUATERNION'
        return obj

    def motion(self,frames=3):
        points=self.points(apose=True);rest=[]
        for s in self.rt.PROFILE:
            endpoint=self.rt.ENDPOINTS.get(s);direction=points[endpoint]-points[s] if endpoint else None
            if s.endswith('_hand'):direction=points[s]-points[self.rt.PARENTS[s]]
            rest.append(dict(id=s,parent=self.rt.PARENTS[s],rest=[list(r) for r in Matrix.Translation(points[s])],direction=list(direction) if direction is not None else None))
        samples=[]
        for f in range(1,frames+1):
            poses={}
            for s in self.rt.PROFILE:
                m=Matrix.Translation(points[s]+Vector((f*.1,0,-f*.02)))@Matrix.Rotation(f*.05,4,'Z')
                poses[s]=[list(r) for r in m]
            samples.append(dict(frame=f,available=True,poses=poses))
        return dict(schema='vta-final-motion',version=1,root_policy='composite-pelvis-once',transform_space='global',coordinates='right-handed-z-up-metres',
                    frame_range=[1,frames],fps=24,joints=rest,samples=samples,provenance=dict(digest='opaque-digest'))

    def run_job(self,target,motion=None,**kwargs):
        job=self.rt.RetargetJob(self.scene,target,motion or self.motion(),**kwargs)
        for _ in job.steps():pass
        return job

    def signature(self,obj):
        return (tuple(map(tuple,obj.matrix_world)),[(b.name,tuple(map(tuple,b.matrix_local))) for b in obj.data.bones])

    def old_action(self,target):
        b=target.pose.bones['mixamorig:Hips'];b.rotation_quaternion=(1,0,0,0);b.keyframe_insert('rotation_quaternion',frame=1000)
        target['owner_curve']=.7;target.keyframe_insert('["owner_curve"]',frame=1000)
        old=target.animation_data.action
        return old,self.rt.action_fingerprint(target)

    def test_rotated_centimetre_target_bakes_without_rest_or_object_mutation(self):
        target=self.rig(rotation=math.pi/2,scale=.01);signature=self.signature(target)
        job=self.run_job(target)
        self.assertEqual(self.signature(target),signature);self.assertIs(target.animation_data.action,job.published)
        self.assertFalse(any(o.name.startswith('.VTA') for o in self.scene.objects))
        self.assertFalse(any(b.constraints for b in target.pose.bones))

    def test_identity_target_and_apose_calibration(self):
        target=self.rig();motion=self.motion();job=self.run_job(target,motion)
        self.assertGreater(job.offsets['left_upper_arm'].angle,.1)

    def test_previous_action_outside_keys_untouched_and_retained(self):
        target=self.rig();old,fingerprint=self.old_action(target);old_fake=old.use_fake_user
        job=self.run_job(target)
        self.assertIsNot(old,job.published);self.assertIs(job.published['vta_previous_action'],old)
        proxy=SimpleNamespace(animation_data=SimpleNamespace(action=old))
        self.assertEqual(self.rt.action_fingerprint(proxy),fingerprint);self.assertEqual(old.use_fake_user,old_fake)
        self.assertTrue(any(p.co.x==1000 for f in self.rt._iter_action_fcurves(old) for p in f.keyframe_points))

    def test_cancel_before_bake_preserves_pose_and_animation(self):
        target=self.rig();old,fingerprint=self.old_action(target);target.pose.bones['mixamorig:Hips'].location=(.2,.3,.4)
        original=target.pose.bones['mixamorig:Hips'].matrix_basis.copy();job=self.rt.RetargetJob(self.scene,target,self.motion())
        stream=job.steps();next(stream);stream.close()
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)
        self.assertEqual(target.pose.bones['mixamorig:Hips'].matrix_basis,original)
        self.assertEqual(len(self.scene.objects),1)

    def test_cancel_during_real_key_write_leaves_original_untouched(self):
        target=self.rig();old,fingerprint=self.old_action(target);job=self.rt.RetargetJob(self.scene,target,self.motion())
        stream=job.steps()
        for phase,frame in stream:
            if phase=='Baking' and frame is None:break
        stream.close();self.assertIs(target.animation_data.action,old)
        self.assertEqual(self.rt.action_fingerprint(target),fingerprint);self.assertEqual(len(self.scene.objects),1)

    def test_cancel_preserves_raw_quaternion_sign_and_unkeyed_channels(self):
        target=self.rig();bone=target.pose.bones['mixamorig:Hips']
        bone.rotation_quaternion=(-1,0,0,0);bone.location=(.21,.32,.43)
        before=self.rt._transform_state(bone);job=self.rt.RetargetJob(self.scene,target,self.motion())
        stream=job.steps()
        for phase,_ in stream:
            if phase=='Validating':break
        stream.close();self.assertEqual(self.rt._transform_state(bone),before)

    def test_mid_write_exception_discards_only_staging(self):
        target=self.rig();old,fingerprint=self.old_action(target);original=self.fast._bulk_write_keys
        def fail(*args):
            for value in original(*args):
                yield value
                raise RuntimeError('injected partial-write failure')
        with mock.patch.object(self.fast,'_bulk_write_keys',fail),self.assertRaisesRegex(RuntimeError,'injected'):
            self.run_job(target)
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)
        self.assertEqual(len(self.scene.objects),1)

    def test_prepublication_failure_keeps_previous_assignment(self):
        target=self.rig();old,fingerprint=self.old_action(target)
        def fail():raise ValueError('stale source')
        with self.assertRaisesRegex(ValueError,'stale source'):self.run_job(target,before_publish=fail)
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)

    def test_missing_required_bone_is_explicit(self):
        target=self.rig();target.data.bones['mixamorig:LeftForeArm'].name='missing'
        with self.assertRaisesRegex(ValueError,'LeftForeArm'):self.rt.validate_target(target,self.scene)

    def test_toes_and_fingers_optional(self):
        target=self.rig(toes=False);job=self.run_job(target)
        self.assertEqual(len(job.mapping),20)

    def test_namespace_variants(self):
        for prefix in ('mixamorig1:', 'mixamorig2:', 'mixamorig', ''):
            target=self.rig(prefix=prefix)
            self.assertEqual(len(self.rt.validate_target(target,self.scene)),22)

    def test_bad_scale_constraints_nla_and_object_animation_refused(self):
        target=self.rig();target.scale.x=2
        with self.assertRaisesRegex(ValueError,'uniform'):self.rt.validate_target(target,self.scene)
        target.scale=(1,1,1);target.constraints.new('COPY_LOCATION')
        with self.assertRaisesRegex(ValueError,'constraints'):self.rt.validate_target(target,self.scene)
        target.constraints.clear();target.animation_data_create();target.animation_data.nla_tracks.new()
        with self.assertRaisesRegex(ValueError,'NLA'):self.rt.validate_target(target,self.scene)
        target.animation_data_clear();target.keyframe_insert('location',frame=1)
        with self.assertRaisesRegex(ValueError,'Object Mode'):self.rt.validate_target(target,self.scene)

    def test_unavailable_frame_is_not_bridged(self):
        target=self.rig();motion=self.motion();motion['samples'][1]=dict(frame=2,available=False,poses=None)
        with self.assertRaisesRegex(ValueError,'unavailable'):self.rt.RetargetJob(self.scene,target,motion)

    def test_root_translation_contributes_once(self):
        target=self.rig();motion=self.motion();job=self.run_job(target,motion)
        self.scene.frame_set(3);bpy.context.view_layer.update()
        p=target.matrix_world@target.pose.bones['mixamorig:Hips'].head
        expected=Vector((.3,0,1-.06))
        self.assertLess((p-expected).length,1e-5)

    def test_new_action_does_not_inherit_old_child_translation(self):
        target=self.rig();bone=target.pose.bones['mixamorig:LeftArm']
        bone.location=(.2,.3,.4);bone.keyframe_insert('location',frame=1)
        old=target.animation_data.action;fingerprint=self.rt.action_fingerprint(target)
        self.run_job(target);self.scene.frame_set(2);bpy.context.view_layer.update()
        self.assertLess(bone.location.length,1e-7);self.assertEqual(tuple(bone.scale),(1,1,1))
        self.assertEqual(self.rt.action_fingerprint(SimpleNamespace(animation_data=SimpleNamespace(action=old))),fingerprint)

    def test_constant_keys_and_quaternion_continuity(self):
        target=self.rig();self.run_job(target)
        for fc in self.rt._iter_action_fcurves(target.animation_data.action):
            self.assertTrue(all(p.interpolation=='CONSTANT' for p in fc.keyframe_points))
            self.assertEqual(len(fc.keyframe_points),3)
        for bone in target.pose.bones:
            path=bone.path_from_id('rotation_quaternion')
            curves=sorted((f for f in self.rt._iter_action_fcurves(target.animation_data.action) if f.data_path==path),key=lambda f:f.array_index)
            qs=[Quaternion([fc.evaluate(frame) for fc in curves]) for frame in (1,2,3)]
            self.assertTrue(all(a.dot(b)>=0 for a,b in zip(qs,qs[1:])))

    def test_old_animated_custom_property_restored_on_success_and_cancel(self):
        for cancel in (False,True):
            target=self.rig();self.old_action(target)
            target['owner_curve']=.1;target.keyframe_insert('["owner_curve"]',frame=1)
            target['owner_curve']=.9;target.keyframe_insert('["owner_curve"]',frame=3)
            target['owner_curve']=.42
            job=self.rt.RetargetJob(self.scene,target,self.motion());stream=job.steps()
            if cancel:
                for phase,_ in stream:
                    if phase=='Validating':break
                stream.close()
            else:
                for _ in stream:pass
            self.assertAlmostEqual(target['owner_curve'],.42,places=6)

    def test_changed_target_action_is_not_overwritten(self):
        target=self.rig();old,_=self.old_action(target)
        job=self.rt.RetargetJob(self.scene,target,self.motion());stream=job.steps();next(stream)
        replacement=bpy.data.actions.new('owner intervened');target.animation_data.action=replacement
        with self.assertRaisesRegex(ValueError,'animation changed'):
            for _ in stream:pass
        self.assertIs(target.animation_data.action,replacement)

    def test_malformed_contract_refused_without_allocating_helpers(self):
        target=self.rig()
        for mutation in ('frame','joint','nonfinite','fps'):
            motion=self.motion()
            if mutation=='frame':motion['samples'][1]['frame']=5
            if mutation=='joint':motion['joints'][2]['parent']='head'
            if mutation=='nonfinite':motion['samples'][0]['poses']['hips'][0][3]=math.nan
            if mutation=='fps':motion['fps']=0
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'contract'):
                self.rt.RetargetJob(self.scene,target,motion)
            self.assertEqual(len(self.scene.objects),1)

    def test_operator_uses_numeric_backend_boundary_and_publishes(self):
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        names={s:'Source'+s for s in self.rt.PROFILE};source=self.rig(prefix='',names=names);target=self.rig()
        source['vta_project_id']='project';source['vta_character_id']='character';source['vta_source_id']='source'
        source.pose.bones[names['hips']].keyframe_insert('location',frame=1)
        source.pose.bones[names['hips']].location=(.1,0,0);source.pose.bones[names['hips']].keyframe_insert('location',frame=3)
        bindings=[dict(id=s,bone=n,parent=self.rt.PARENTS[s],endpoint=None,optional=s.endswith('_toe')) for s,n in names.items()]
        def export(location,**snapshot):
            result=self.motion();result['samples']=snapshot['samples']
            for joint in result['joints']:
                row=snapshot['rest'][joint['id']];joint['rest']=row['matrix'];joint['direction']=row['direction']
            result['provenance']['state_token']=hashlib.sha256(b'unchanged private authority').hexdigest()
            return result
        backend=SimpleNamespace(_core=SimpleNamespace(retarget_source_bindings=lambda:bindings),export_retarget_motion=mock.Mock(side_effect=export),
            _load_project_source=lambda loc:(SimpleNamespace(to_json=lambda:'unchanged private authority'),None))
        with mock.patch.object(ui,'loaded_context_matches',return_value=True),mock.patch.object(ui,'require_loaded_context'),mock.patch.object(ui,'_analysis_locked',return_value=False),mock.patch.object(ui,'cached_scene_source',return_value=('project',dict(source_id='source'))),mock.patch.object(ui,'_get_backend',return_value=backend),mock.patch.object(ui,'_location_from_context',return_value=object()):
            result=bpy.ops.blendcap.vta_retarget('EXEC_DEFAULT',source_name=source.name,target_name=target.name,start=1,end=3)
        self.assertEqual(result,{'FINISHED'});self.assertEqual(backend.export_retarget_motion.call_count,1)
        self.assertTrue(target.animation_data.action.name.startswith('VTA Mixamo FK'))
        self.assertIsNone(self.rt._running)

    def test_partial_channel_writer_is_not_published(self):
        target=self.rig();old,fingerprint=self.old_action(target);original=self.fast._bulk_write_keys
        def drop_channel(obj,keys,primed,frame):
            keys=dict(keys);keys.pop(next(iter(keys)))
            yield from original(obj,keys,primed,frame)
        with mock.patch.object(self.fast,'_bulk_write_keys',drop_channel),self.assertRaisesRegex(ValueError,'Incomplete'):
            self.run_job(target)
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)

    def test_object_corrections_reach_final_baked_target(self):
        names={s:('Source'+s) for s in self.rt.PROFILE};source=self.rig(prefix='',names=names)
        for frame,location,angle,scale in ((1,(0,0,0),0,1),(3,(.7,.2,.1),.4,1.2)):
            source.location=location;source.rotation_euler=(0,0,angle);source.scale=(scale,)*3
            for prop in ('location','rotation_euler','scale'):source.keyframe_insert(prop,frame=frame)
        self.scene.frame_set(1);bpy.context.view_layer.update()
        bindings=[dict(id=s,bone=n,parent=self.rt.PARENTS[s],endpoint=None,optional=s.endswith('_toe')) for s,n in names.items()]
        stream=self.rt.capture_motion(source,bindings,1,3)
        while True:
            try:next(stream)
            except StopIteration as done:rest,samples=done.value;break
        motion=self.motion();motion['samples']=samples
        for joint in motion['joints']:
            matrix=Matrix(rest[joint['id']]['matrix']);joint['rest']=[list(r) for r in Matrix.LocRotScale(matrix.translation,matrix.to_quaternion(),(1,1,1))]
            joint['direction']=None
        for sample in samples:
            for name,m in sample['poses'].items():
                matrix=Matrix(m);sample['poses'][name]=[list(r) for r in Matrix.LocRotScale(matrix.translation,matrix.to_quaternion(),(1,1,1))]
        target=self.rig(rotation=math.pi/2,scale=.01);job=self.run_job(target,motion)
        self.scene.frame_set(3);bpy.context.view_layer.update()
        got=target.matrix_world@target.pose.bones['mixamorig:Hips'].matrix
        expected=job.rest['hips'].translation+job.scale*(Matrix(samples[-1]['poses']['hips']).translation-Matrix(rest['hips']['matrix']).translation)
        self.assertLess((got.translation-expected).length,1e-5)
        want=Matrix(samples[-1]['poses']['hips']).to_quaternion()@job.offsets['hips']
        self.assertLess(got.to_quaternion().rotation_difference(want).angle,1e-3)
        self.assertGreater((got.translation-job.rest['hips'].translation).length,.7)

    def test_capture_includes_object_motion_and_restores_hidden_pose_state(self):
        names={s:('Source'+s) for s in self.rt.PROFILE};source=self.rig(prefix='',names=names)
        source.location=(0,0,0);source.keyframe_insert('location',frame=1)
        source.location=(.7,.2,.1);source.keyframe_insert('location',frame=3)
        source.rotation_euler=(0,0,.4);source.keyframe_insert('rotation_euler',frame=3)
        source.rotation_euler=(0,0,0);source.keyframe_insert('rotation_euler',frame=1)
        source.scale=(1.2,)*3;source.keyframe_insert('scale',frame=3);source.scale=(1,)*3;source.keyframe_insert('scale',frame=1)
        self.scene.frame_set(1);source.hide_set(True);original=source.matrix_basis.copy()
        bindings=[dict(id=s,bone=n,parent=self.rt.PARENTS[s],endpoint=None,optional=s.endswith('_toe')) for s,n in names.items()]
        stream=self.rt.capture_motion(source,bindings,1,3)
        while True:
            try:next(stream)
            except StopIteration as done:rest,samples=done.value;break
        a=Matrix(samples[0]['poses']['hips']);b=Matrix(samples[-1]['poses']['hips'])
        self.assertGreater((a.translation-b.translation).length,.5)
        self.assertGreater(a.to_quaternion().rotation_difference(b.to_quaternion()).angle,.3)
        self.assertTrue(source.hide_get());self.assertEqual(source.matrix_basis,original);self.assertEqual(self.scene.frame_current,1)
