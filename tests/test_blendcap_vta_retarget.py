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


class RecordingLayout:
    def __init__(self, calls=None):
        self.calls=[] if calls is None else calls
        self.enabled=True
    def row(self, **kwargs):return RecordingLayout(self.calls)
    column=row
    box=row
    def prop(self, owner, name, **kwargs):self.calls.append(('prop',name,kwargs))
    def label(self, **kwargs):self.calls.append(('label',kwargs.get('text','')))
    def separator(self):pass
    def operator(self, name, **kwargs):
        self.calls.append(('operator',name,kwargs));return SimpleNamespace()


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

    def test_rotated_centimetre_target_preserves_rest_data_and_frame_zero_object(self):
        target=self.rig(rotation=math.pi/2,scale=.01);signature=self.signature(target)
        job=self.run_job(target)
        self.scene.frame_set(0);bpy.context.view_layer.update()
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
            self.assertEqual(len(fc.keyframe_points),4)
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
        from pathlib import Path
        from blendcap_vta_review import ReviewLocation
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
        self.scene.frame_set(0)
        location=ReviewLocation(Path('project_vta'),Path('clip.mp4'),-1,0)
        with mock.patch.object(ui,'loaded_context_matches',return_value=True),mock.patch.object(ui,'require_loaded_context'),mock.patch.object(ui,'_analysis_locked',return_value=False),mock.patch.object(ui,'cached_scene_source',return_value=('project',dict(source_id='source',start_frame=0,end_frame=162))),mock.patch.object(ui,'_get_backend',return_value=backend),mock.patch.object(ui,'_location_from_context',return_value=location):
            result=bpy.ops.blendcap.vta_retarget('EXEC_DEFAULT',source_name=source.name,target_name=target.name,start=1,end=3)
        self.assertEqual(result,{'FINISHED'});self.assertEqual(backend.export_retarget_motion.call_count,1)
        exported_location=backend.export_retarget_motion.call_args.args[0]
        self.assertEqual((exported_location.blender_frame,exported_location.source_frame),(1,0))
        self.assertEqual(self.scene.frame_current,0)
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
        for factory in (self.rig,self.epic_rig):
            target=factory(rotation=math.pi/2,scale=.01);job=self.run_job(target,motion)
            self.scene.frame_set(3);bpy.context.view_layer.update()
            got=target.matrix_world@target.pose.bones[job.mapping['hips']].matrix
            expected=Matrix(samples[-1]['poses']['hips']).translation
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

    def assert_matrix_close(self,a,b,tolerance=2e-5):
        self.assertLess(max(abs(v-w) for r,s in zip(a,b) for v,w in zip(r,s)),tolerance)

    def test_different_import_sizes_and_positions_match_source_pelvis_and_leg_size(self):
        motion=self.motion()
        for factor,position in ((.65,(4,-2,1)),(1.6,(-3,5,.2))):
            target=self.rig(rotation=math.pi/2,scale=.01)
            target.scale*=factor;target.location=position;bpy.context.view_layer.update()
            imported=target.matrix_world.copy();rest=self.signature(target)[1]
            job=self.run_job(target,motion)
            for frame in (1,2,3):
                self.scene.frame_set(frame);bpy.context.view_layer.update()
                poses={s:target.matrix_world@target.pose.bones[n].matrix for s,n in job.mapping.items()}
                self.assertLess((poses['hips'].translation-Matrix(motion['samples'][frame-1]['poses']['hips']).translation).length,2e-5)
                wanted=self.rt._leg_length({s:Matrix(m) for s,m in motion['samples'][frame-1]['poses'].items()})
                self.assertAlmostEqual(self.rt._leg_length(poses),wanted,places=5)
            self.scene.frame_set(0);bpy.context.view_layer.update()
            self.assert_matrix_close(target.matrix_world,imported)
            self.assertEqual(self.signature(target)[1],rest)
            for bone in target.pose.bones:self.assert_matrix_close(bone.matrix,bone.bone.matrix_local)

    def test_repeat_retarget_and_cancel_do_not_compound_alignment_or_lose_reference(self):
        target=self.rig();target.location=(3,4,0);target.scale*=1.7;bpy.context.view_layer.update()
        imported=target.matrix_world.copy();first=self.run_job(target)
        self.scene.frame_set(3);bpy.context.view_layer.update();aligned=target.matrix_world.copy()
        second=self.run_job(target);self.assertIs(second.published['vta_previous_action'],first.published)
        self.assert_matrix_close(target.matrix_world,aligned)
        job=self.rt.RetargetJob(self.scene,target,self.motion());stream=job.steps()
        for phase,_ in stream:
            if phase=='Validating':break
        stream.close();self.assertIs(target.animation_data.action,second.published)
        self.assert_matrix_close(target.matrix_world,aligned)
        self.scene.frame_set(0);bpy.context.view_layer.update();self.assert_matrix_close(target.matrix_world,imported)

    def test_edited_generated_object_animation_is_refused(self):
        target=self.rig();job=self.run_job(target)
        curve=next(f for f in self.rt._iter_action_fcurves(job.published) if f.data_path=='location')
        curve.keyframe_points[-1].co.y+=.2
        with self.assertRaisesRegex(ValueError,'Object Mode'):self.rt.validate_target(target,self.scene)

    def test_unmapped_euler_bone_is_neutral_at_zero_without_changing_rest_or_mode(self):
        target=self.rig();bpy.context.view_layer.objects.active=target;bpy.ops.object.mode_set(mode='EDIT')
        bone=target.data.edit_bones.new('extra finger');bone.head=(0,0,0);bone.tail=(0,.1,0)
        bone.parent=target.data.edit_bones['mixamorig:LeftHand'];bpy.ops.object.mode_set(mode='OBJECT')
        bone=target.pose.bones['extra finger'];bone.rotation_mode='XYZ';bone.rotation_euler=(.3,.2,.1)
        bone.keyframe_insert('rotation_euler',frame=1);old=target.animation_data.action
        self.run_job(target);self.scene.frame_set(0);bpy.context.view_layer.update()
        self.assertEqual(bone.rotation_mode,'XYZ');self.assert_matrix_close(bone.matrix,bone.bone.matrix_local)
        self.assertIs(target.animation_data.action['vta_previous_action'],old)

    def test_reserved_zero_rejected_and_bounded_start_not_shifted(self):
        target=self.rig();motion=self.motion();motion['frame_range']=[0,2]
        for frame,sample in enumerate(motion['samples']):sample['frame']=frame
        with self.assertRaisesRegex(ValueError,'Frame 0'):self.run_job(target,motion)
        motion=self.motion();motion['frame_range']=[22,24]
        for frame,sample in enumerate(motion['samples'],22):sample['frame']=frame
        job=self.run_job(target,motion)
        for curve in self.rt._iter_action_fcurves(job.published):self.assertEqual([int(k.co.x) for k in curve.keyframe_points],[0,22,23,24])

    def test_parented_mesh_follows_alignment_and_raw_mesh_is_unchanged(self):
        target=self.rig();target.scale*=1.5;target.location=(4,0,0);bpy.context.view_layer.update()
        mesh=bpy.data.meshes.new('attached mesh');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
        child=bpy.data.objects.new('attached mesh',mesh);self.scene.collection.objects.link(child);child.parent=target
        child.location=(.3,.2,.1);bpy.context.view_layer.update();original=child.matrix_world.copy()
        vertices=[tuple(v.co) for v in mesh.vertices];parent_inverse=child.matrix_parent_inverse.copy()
        job=self.run_job(target);self.scene.frame_set(2);bpy.context.view_layer.update()
        self.assert_matrix_close(child.matrix_world,job.frame_world[2]@child.matrix_local)
        self.assertEqual([tuple(v.co) for v in mesh.vertices],vertices);self.assertEqual(child.matrix_parent_inverse,parent_inverse)
        self.scene.frame_set(0);bpy.context.view_layer.update();self.assert_matrix_close(child.matrix_world,original)

    def test_uniform_source_scale_changes_survive_without_scaling_pelvis_displacement_twice(self):
        target=self.rig();motion=self.motion()
        sample=motion['samples'][-1];pivot=Vector((.3,0,0))
        for s,value in sample['poses'].items():
            m=Matrix(value);m.translation=pivot+(m.translation-pivot)*1.2;sample['poses'][s]=[list(r) for r in m]
        job=self.run_job(target,motion);self.scene.frame_set(3);bpy.context.view_layer.update()
        poses={s:target.matrix_world@target.pose.bones[n].matrix for s,n in job.mapping.items()}
        self.assertLess((poses['hips'].translation-Matrix(sample['poses']['hips']).translation).length,1e-5)
        self.assertAlmostEqual(self.rt._leg_length(poses),self.rt._leg_length({s:Matrix(m) for s,m in sample['poses'].items()}),places=5)

    def test_generated_object_track_modifier_or_mute_cannot_bypass_safety(self):
        for modification in ('modifier','mute'):
            target=self.rig();job=self.run_job(target)
            curve=next(f for f in self.rt._iter_action_fcurves(job.published) if f.data_path=='scale')
            if modification=='modifier':curve.modifiers.new('NOISE')
            else:curve.mute=True
            with self.assertRaisesRegex(ValueError,'Object Mode'):self.rt.validate_target(target,self.scene)

    def test_delta_transforms_restore_at_zero_and_clear_for_aligned_animation(self):
        target=self.rig();target.delta_location=(.3,-.5,.2);target.delta_rotation_euler=(.1,.2,.3);target.delta_scale=(1.3,)*3
        bpy.context.view_layer.update();original=self.rt._transform_state(target);world=target.matrix_world.copy()
        job=self.run_job(target);self.scene.frame_set(0);bpy.context.view_layer.update()
        self.assertEqual(self.rt._transform_state(target),original);self.assert_matrix_close(target.matrix_world,world)
        self.scene.frame_set(2);bpy.context.view_layer.update();self.assert_matrix_close(target.matrix_world,job.frame_world[2])

    def ui_fixture(self, *, toes=True):
        names={s:'Source'+s for s in self.rt.PROFILE}
        source=self.rig(prefix='',names=names,toes=toes);target=self.rig(toes=toes)
        for owner in (self.scene,source):owner['vta_project_id']='p';owner['vta_source_id']='s'
        source['vta_character_id']='c'
        settings=self.scene.blendcap_vta_retarget;settings.source=source;settings.target=target
        bindings=tuple(dict(id=s,bone=n,optional=s.endswith('_toe')) for s,n in names.items())
        backend=SimpleNamespace(_core=SimpleNamespace(retarget_source_bindings=lambda:bindings),
            _load_project_source=mock.Mock(side_effect=AssertionError('Draw must not load project')))
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        return source,target,settings,backend,ui

    def test_native_object_pointer_pickers_filter_meshes_and_other_scenes(self):
        source,target,settings,backend,ui=self.ui_fixture()
        mesh=bpy.data.objects.new('clothing',bpy.data.meshes.new('clothing'));self.scene.collection.objects.link(mesh)
        self.assertTrue(self.rt.source_picker_poll(settings,source));self.assertTrue(self.rt.target_picker_poll(settings,target))
        self.assertFalse(self.rt.target_picker_poll(settings,source));self.assertFalse(self.rt.source_picker_poll(settings,target))
        for poll in (self.rt.source_picker_poll,self.rt.target_picker_poll):self.assertFalse(poll(settings,mesh))
        self.scene.collection.objects.unlink(target)
        self.assertFalse(self.rt.target_picker_poll(settings,target))
        self.scene.collection.objects.link(target)
        for field in ('source','target'):
            prop=self.rt.VtaRetargetSettings.bl_rna.properties[field]
            self.assertEqual(prop.type,'POINTER');self.assertEqual(prop.fixed_type.identifier,'Object')

    def test_picker_pointer_survives_target_rename(self):
        source,target,settings,backend,ui=self.ui_fixture();target.name='Owner renamed armature'
        self.assertIs(settings.target,target)
        self.assertTrue(self.rt.target_picker_poll(settings,target))

    def test_read_only_mapping_uses_real_names_and_optional_toes(self):
        source,target,settings,backend,ui=self.ui_fixture(toes=False)
        with mock.patch.object(ui,'_get_backend',return_value=backend):rows=self.rt.mapping_preview(bpy.context,settings)
        self.assertEqual(len(rows),20)
        self.assertIn(('Sourceleft_forearm','mixamorig:LeftForeArm'),rows)
        self.assertFalse(any(name.endswith('ToeBase') for _,name in rows))
        backend._load_project_source.assert_not_called()

    def test_repeated_mapping_draws_do_not_reload_private_state_or_offer_remapping(self):
        source,target,settings,backend,ui=self.ui_fixture();settings.show_mapping=True
        before=self.signature(target);layout=RecordingLayout()
        with mock.patch.object(ui,'_get_backend',return_value=backend):
            for _ in range(10):self.assertTrue(self.rt.draw_rig_choices(layout,bpy.context))
        self.assertEqual(self.signature(target),before);backend._load_project_source.assert_not_called()
        self.assertEqual({c[1] for c in layout.calls if c[0]=='prop'},{'source','target','show_mapping','target_profile'})
        self.assertFalse(any(c[0]=='operator' for c in layout.calls))
        self.assertIn(('label','Sourceleft_forearm'),layout.calls)

    def test_programmatic_mesh_pointer_cannot_bypass_preflight(self):
        source,target,settings,backend,ui=self.ui_fixture()
        mesh=bpy.data.objects.new('wrong mesh',bpy.data.meshes.new('wrong mesh'));self.scene.collection.objects.link(mesh)
        settings.target=mesh
        with self.assertRaisesRegex(ValueError,'armature'):self.rt.mapping_preview(bpy.context,settings)

    def test_scene_picker_panel_passes_exact_names_to_existing_transaction(self):
        source,target,settings,backend,ui=self.ui_fixture()
        settings.target.name='Current target'
        names=[]
        def run(op,context):
            names.append((op.source_name,op.target_name))
            yield from ()
        with mock.patch.object(self.rt.BLENDCAP_OT_vta_retarget,'_run',run),mock.patch.object(ui,'loaded_context_matches',return_value=True),mock.patch.object(ui,'_analysis_locked',return_value=False):
            self.assertEqual(bpy.ops.blendcap.vta_retarget('EXEC_DEFAULT',use_scene_selection=True),{'FINISHED'})
        self.assertEqual(names,[(source.name,'Current target')])

    def test_retarget_is_in_vta_3d_sidebar_not_preview_menu(self):
        import inspect
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        panel=self.rt.VIEW3D_PT_vta_retarget
        self.assertEqual((panel.bl_space_type,panel.bl_category),('VIEW_3D','VTA'))
        self.assertNotIn('blendcap.vta_retarget',inspect.getsource(ui.BLENDCAP_MT_vta_commands.draw))
        self.assertIn("'source'",inspect.getsource(self.rt.draw_rig_choices))

    def test_legacy_ui_hidden_while_engine_presets_and_operators_remain(self):
        panels=importlib.import_module(self.addon.__name__+'.blendcap.panels')
        names=('_draw_setup_section','_draw_source_section','_draw_capture_section','_draw_bvh_settings_section','_draw_bvh_library_section')
        with mock.patch.object(panels,'addon_prefs',return_value=None),mock.patch.object(panels,'_draw_retargeting_section') as legacy,mock.patch.object(panels,'_draw_donate_section') as donate:
            with mock.patch.multiple(panels,**{name:mock.Mock() for name in names}):
                panels.draw_blendcap_ui(SimpleNamespace(layout=RecordingLayout()),bpy.context)
        legacy.assert_not_called();donate.assert_not_called()
        self.assertEqual(panels.VIEW3D_PT_blendcap_ui.bl_category,'VTA')
        self.assertTrue(callable(panels._draw_retarget_mapper_table))
        self.assertTrue(hasattr(self.scene,'blendcap_retarget_pairs'))
        for name in ('apply_retarget','convert_fk_to_ik','retarget_reload_preset'):
            self.assertTrue(getattr(bpy.ops.blendcap,name).get_rna_type())

    def test_credits_are_last_with_notices_source_and_support(self):
        panels=importlib.import_module(self.addon.__name__+'.blendcap.panels');layout=RecordingLayout()
        with mock.patch.object(panels,'addon_prefs',return_value=None),mock.patch.object(panels,'SHOW_DONATE_BUTTON',True):
            panels.VIEW3D_PT_vta_credits.draw(SimpleNamespace(layout=layout),bpy.context)
        self.assertGreater(panels.VIEW3D_PT_vta_credits.bl_order,self.rt.VIEW3D_PT_vta_retarget.bl_order)
        self.assertEqual(panels.VIEW3D_PT_vta_credits.bl_category,'VTA')
        self.assertIn(('label','© 2026 Arcomade — BlendCap'),layout.calls)
        self.assertTrue(any('GPL-3.0' in str(c) for c in layout.calls))
        self.assertTrue(any(c[:2]==('operator','blendcap.donate') for c in layout.calls))
        self.assertEqual(sum(c[:2]==('operator','wm.path_open') for c in layout.calls),2)

    def test_credits_donation_honors_existing_hide_preference(self):
        panels=importlib.import_module(self.addon.__name__+'.blendcap.panels');layout=RecordingLayout()
        with mock.patch.object(panels,'addon_prefs',return_value=SimpleNamespace(hide_donate_buttons=True)),mock.patch.object(panels,'SHOW_DONATE_BUTTON',True):
            panels.VIEW3D_PT_vta_credits.draw(SimpleNamespace(layout=layout),bpy.context)
        self.assertFalse(any(c[:2]==('operator','blendcap.donate') for c in layout.calls))
        self.assertIn(('label','© 2026 Arcomade — BlendCap'),layout.calls)

    def epic_rig(self,*,prefix='',toes=True,rotation=0.,scale=1.,roll=0.):
        profiles=importlib.import_module(self.addon.__name__+'.blendcap.vta_target_profiles')
        obj=self.rig(prefix=prefix,names=profiles.EPIC_UE4.bones,toes=toes,rotation=rotation,scale=scale)
        bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
        for bone in obj.data.edit_bones:bone.roll=roll
        root=obj.data.edit_bones.new(prefix+'root');root.head=(0,0,0);root.tail=(0,.1/scale,0)
        obj.data.edit_bones[prefix+'pelvis'].parent=root
        bpy.ops.object.mode_set(mode='OBJECT');obj.pose.bones[prefix+'root'].rotation_mode='QUATERNION'
        return obj

    def test_epic_detection_required_optional_and_namespace(self):
        for prefix,toes in (('',True),('hero:',False),('char|',True)):
            obj=self.epic_rig(prefix=prefix,toes=toes)
            match=self.rt.target_profile(obj)
            self.assertEqual(match.profile.id,'EPIC_UE4');self.assertEqual(match.prefix,prefix)
            self.assertEqual(len(self.rt.validate_target(obj,self.scene)),22 if toes else 20)
            self.assertEqual(match.mapping['hips'],prefix+'pelvis')

    def test_epic_rotated_nonunit_rolled_target_root_once_and_reference_rest(self):
        obj=self.epic_rig(prefix='hero:',rotation=math.pi/2,scale=.01,roll=.41)
        obj.location=(3,-5,2);obj.scale*=1.4;bpy.context.view_layer.update();original=self.signature(obj)
        job=self.run_job(obj);self.assertEqual(job.published['vta_target_profile'],'EPIC_UE4')
        for frame in (1,2,3):
            self.scene.frame_set(frame);bpy.context.view_layer.update()
            pelvis=obj.matrix_world@obj.pose.bones['hero:pelvis'].matrix
            self.assertLess((pelvis.translation-Vector((frame*.1,0,1-frame*.02))).length,2e-5)
            self.assert_matrix_close(obj.pose.bones['hero:root'].matrix_basis,Matrix.Identity(4))
        self.scene.frame_set(0);bpy.context.view_layer.update();self.assertEqual(self.signature(obj),original)

    def test_epic_existing_action_and_outside_keys_preserved(self):
        obj=self.epic_rig();bone=obj.pose.bones['pelvis'];bone.rotation_quaternion=(1,0,0,0)
        bone.keyframe_insert('rotation_quaternion',frame=1000);old=obj.animation_data.action
        fingerprint=self.rt.action_fingerprint(obj);job=self.run_job(obj)
        self.assertIs(job.published['vta_previous_action'],old)
        self.assertEqual(self.rt.action_fingerprint(SimpleNamespace(animation_data=SimpleNamespace(action=old))),fingerprint)
        self.assertTrue(any(k.co.x==1000 for f in self.rt._iter_action_fcurves(old) for k in f.keyframe_points))

    def test_epic_cancellation_and_injected_partial_write_are_transactional(self):
        for fail in (False,True):
            obj=self.epic_rig();obj.pose.bones['pelvis'].keyframe_insert('location',frame=1000)
            old=obj.animation_data.action;before=self.rt._rig_state(obj);fingerprint=self.rt.action_fingerprint(obj)
            job=self.rt.RetargetJob(self.scene,obj,self.motion());stream=job.steps()
            if fail:
                original=self.fast._bulk_write_keys
                def partial(*args):
                    for value in original(*args):yield value;raise RuntimeError('injected Epic failure')
                with mock.patch.object(self.fast,'_bulk_write_keys',partial),self.assertRaisesRegex(RuntimeError,'injected Epic'):
                    for _ in stream:pass
            else:
                for phase,_ in stream:
                    if phase=='Baking':break
                stream.close()
            self.assertEqual(self.rt._rig_state(obj),before);self.assertIs(obj.animation_data.action,old)
            self.assertEqual(self.rt.action_fingerprint(obj),fingerprint)
            self.assertFalse(any(o.name.startswith('.VTA') for o in self.scene.objects))

    def test_epic_invalid_hierarchy_missing_bones_and_ue5_are_explicit(self):
        obj=self.epic_rig();obj.data.bones['lowerarm_l'].name='missing'
        with self.assertRaisesRegex(ValueError,'lowerarm_l'):self.rt.validate_target(obj,self.scene)
        obj.data.bones['missing'].name='lowerarm_l'
        bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
        obj.data.edit_bones['pelvis'].parent=None;bpy.ops.object.mode_set(mode='OBJECT')
        with self.assertRaisesRegex(ValueError,'hierarchy'):self.rt.validate_target(obj,self.scene)
        bpy.ops.object.mode_set(mode='EDIT');obj.data.edit_bones['pelvis'].parent=obj.data.edit_bones['root']
        extra=obj.data.edit_bones.new('spine_05');extra.head=(0,0,0);extra.tail=(0,.1,0);bpy.ops.object.mode_set(mode='OBJECT')
        with self.assertRaisesRegex(ValueError,'UE5'):self.rt.validate_target(obj,self.scene)

    def test_control_rig_signatures_fail_instead_of_falling_back_to_fk_export_names(self):
        profiles=importlib.import_module(self.addon.__name__+'.blendcap.vta_target_profiles')
        for control in (profiles.ARP,profiles.CLOUDRIG):
            names=set(profiles.MIXAMO.bones.values())|set(control.signatures)
            with self.assertRaisesRegex(ValueError,'adapter'):profiles.resolve_target(names)
            with self.assertRaisesRegex(ValueError,'adapter'):profiles.resolve_target(names,'MIXAMO')
        names=set(profiles.MIXAMO.bones.values())|set(profiles.RIGIFY.signatures)
        for profile in ('AUTO','MIXAMO','RIGIFY'):
            with self.assertRaisesRegex(ValueError,'Ambiguous/customized'):profiles.resolve_target(names,profile)

    def test_ambiguous_profiles_require_explicit_selection_and_namespaces_never_guess(self):
        profiles=importlib.import_module(self.addon.__name__+'.blendcap.vta_target_profiles')
        names=set(profiles.MIXAMO.bones.values())|set(profiles.EPIC_UE4.bones.values())|{'root'}
        with self.assertRaisesRegex(ValueError,'ambiguous'):profiles.resolve_target(names)
        self.assertEqual(profiles.resolve_target(names,'EPIC_UE4').profile.id,'EPIC_UE4')
        duplicate={prefix+n for prefix in ('A:','B:') for n in profiles.EPIC_UE4.bones.values()}|{'A:root','B:root'}
        with self.assertRaisesRegex(ValueError,'ambiguous'):profiles.resolve_target(duplicate,'EPIC_UE4')
