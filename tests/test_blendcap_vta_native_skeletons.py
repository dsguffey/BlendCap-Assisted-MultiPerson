"""UE5/Godot structural proofs, not real Manny or generated-control certification."""
import copy
import importlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:bpy=None


def skeleton(helper,family,*,prefix='',toes=True,fingers=True,rotation=.0,scale=1.,roll=.31):
    """Conventional skeleton with native spine/neck, metacarpals and twists."""
    rt=helper.rt;profiles=importlib.import_module(rt.__package__+'.vta_target_profiles')
    profile=next(p for p in profiles.PROFILES if p.id==family)
    data=bpy.data.armatures.new(family+' fixture data')
    obj=bpy.data.objects.new(family+' structural fixture',data);helper.scene.collection.objects.link(obj)
    obj.matrix_world=Matrix.Translation((2,-3,.4))@Matrix.Rotation(rotation,4,'X')@Matrix.Scale(scale,4)
    bpy.context.view_layer.update()
    for o in bpy.context.selected_objects:o.select_set(False)
    obj.select_set(True);bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
    points=helper.points();body={s:n for s,n in profile.bones.items() if toes or not s.endswith('_toe')}
    # Independent native convention fixture, NOT the production hierarchy table.
    if family=='EPIC_UE5':
        parents={'root':None,'pelvis':'root','spine_01':'pelvis','spine_02':'spine_01',
                 'spine_03':'spine_02','spine_04':'spine_03','spine_05':'spine_04',
                 'neck_01':'spine_05','neck_02':'neck_01','head':'neck_02'}
        for side in ('l','r'):
            for chain,parent in ((('clavicle','upperarm','lowerarm','hand'),'spine_05'),
                                 (('thigh','calf','foot','ball'),'pelvis')):
                for name in chain:
                    if name=='ball' and not toes:continue
                    parents[name+'_'+side]=parent;parent=name+'_'+side
    else:
        parents={'Root':None,'Hips':'Root','Spine':'Hips','Chest':'Spine',
                 'UpperChest':'Chest','Neck':'UpperChest','Head':'Neck'}
        for side in ('Left','Right'):
            for chain,parent in ((('Shoulder','UpperArm','LowerArm','Hand'),'UpperChest'),
                                 (('UpperLeg','LowerLeg','Foot','Toes'),'Hips')):
                for name in chain:
                    if name=='Toes' and not toes:continue
                    parents[side+name]=parent;parent=side+name
    coords={n:points[s] for s,n in body.items()};coords[profile.root]=Vector((0,0,0))
    if family=='EPIC_UE5':
        coords.update(spine_02=points['spine_lower'].lerp(points['spine_middle'],.5),
                      spine_04=points['spine_middle'].lerp(points['chest'],.5),
                      neck_02=points['neck'].lerp(points['head'],.5))
        for side in ('l','r'):
            for part,parent in (('upperarm_twist_01','upperarm'),('upperarm_twist_02','upperarm'),
                                ('lowerarm_twist_01','lowerarm'),('thigh_twist_01','thigh'),('calf_twist_01','calf')):
                name=part+'_'+side;parents[name]=parent+'_'+side;coords[name]=coords[parent+'_'+side]+Vector((0,0,.01))
    pending=dict(parents)
    while pending:
        for name,parent in list(pending.items()):
            if parent and prefix+parent not in data.edit_bones:continue
            bone=data.edit_bones.new(prefix+name);bone.head=coords[name]/scale
            bone.tail=bone.head+Vector((0,.08/scale,0));bone.roll=roll
            if parent:bone.parent=data.edit_bones[prefix+parent]
            del pending[name]
    bpy.ops.object.mode_set(mode='OBJECT')
    for b in obj.pose.bones:b.rotation_mode='QUATERNION'
    if fingers:
        from tests.test_blendcap_vta_fingers import add_fingers
        add_fingers(rt,obj,family,prefix)
        if family=='EPIC_UE5':
            bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
            for side in ('l','r'):
                for digit in ('index','middle','ring','pinky'):
                    first=data.edit_bones[prefix+digit+'_01_'+side]
                    carpal=data.edit_bones.new(prefix+digit+'_metacarpal_'+side)
                    carpal.head=first.head-Vector((.02/scale,0,0));carpal.tail=first.head
                    carpal.parent=data.edit_bones[prefix+'hand_'+side];first.parent=carpal
            bpy.ops.object.mode_set(mode='OBJECT')
            for b in obj.pose.bones:b.rotation_mode='QUATERNION'
    return obj


def skin(scene,rig):
    """Rigid-weighted triangles exercise all actual deform matrices."""
    verts=[];faces=[];names=list(rig.data.bones.keys())
    for name in names:
        point=rig.data.bones[name].head_local
        verts.extend(tuple(point+off) for off in (Vector((0,0,0)),Vector((.002,0,0)),Vector((0,.002,0))))
        faces.append(tuple(range(len(verts)-3,len(verts))))
    data=bpy.data.meshes.new('native skeleton proof skin');data.from_pydata(verts,[],faces)
    mesh=bpy.data.objects.new('native skeleton proof skin',data);scene.collection.objects.link(mesh)
    mesh.parent=rig;mesh.matrix_basis=Matrix.Identity(4)
    for i,name in enumerate(names):mesh.vertex_groups.new(name=name).add(list(range(i*3,i*3+3)),1.,'REPLACE')
    mesh.modifiers.new('Native armature deformation','ARMATURE').object=rig
    return mesh


def state(rt,scene,obj,frames=(0,1,3,5)):
    old=scene.frame_current;sub=scene.frame_subframe;raw=rt._rig_state(obj)
    result=dict(action=obj.animation_data.action.name if obj.animation_data and obj.animation_data.action else None,
        fingerprint=rt.action_fingerprint(obj),rest=[(b.name,b.parent.name if b.parent else None,tuple(map(tuple,b.matrix_local))) for b in obj.data.bones],
        props=repr([(b.name,dict(b.items())) for b in obj.pose.bones]),samples={})
    try:
        for frame in frames:
            scene.frame_set(frame);bpy.context.view_layer.update()
            result['samples'][frame]={n:tuple(map(tuple,obj.matrix_world@b.matrix)) for n,b in obj.pose.bones.items()}
    finally:
        scene.frame_set(old,subframe=sub);rt._restore_rig(raw);bpy.context.view_layer.update()
    return result


@unittest.skipUnless(bpy is not None,'Blender required')
class NativeSkeletonTests(unittest.TestCase):
    families=('EPIC_UE5','GODOT_HUMANOID')
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_retarget import RetargetTests
        cls.helpers=RetargetTests;RetargetTests.setUpClass();cls.rt=RetargetTests.rt
        cls.profiles=importlib.import_module(cls.rt.__package__+'.vta_target_profiles')
        cls.fingers=importlib.import_module(cls.rt.__package__+'.vta_fingers')
    @classmethod
    def tearDownClass(cls):cls.helpers.tearDownClass()
    def setUp(self):
        self.helper=self.helpers();self.helper.setUp();self.scene=self.helper.scene
    def tearDown(self):self.helper.tearDown()
    def bake(self,obj,motion=None,**kwargs):return self.helper.run_job(obj,motion,**kwargs)
    def motion(self,frames=5):
        from tests.test_blendcap_vta_fingers import finger_motion
        return finger_motion(self.helper,frames,corrected=True)[1]

    def test_exact_detection_namespaces_and_22_body_30_fingers(self):
        for family in self.families:
            for prefix in ('','personA:','personB_'):
                with self.subTest(family=family,prefix=prefix):
                    obj=skeleton(self.helper,family,prefix=prefix);match=self.rt.target_profile(obj)
                    self.assertEqual((match.profile.id,match.prefix),(family,prefix))
                    self.assertEqual(len(self.rt.validate_target(obj,self.scene)),22)
                    self.assertEqual(len(self.fingers.resolve(obj,match)),30)
                    with self.assertRaises(ValueError):self.rt.validate_target(obj,self.scene,'EPIC_UE4' if family=='EPIC_UE5' else 'MIXAMO')

    def test_optional_toes_and_partial_fingers_fail_soft(self):
        for family in self.families:
            with self.subTest(family=family):
                obj=skeleton(self.helper,family,toes=False);name=self.fingers.MAPS[family]['left_index_2']
                obj.data.bones[name].name='unsupported finger name';job=self.bake(obj,self.motion())
                self.assertEqual(len(job.fingers),28);self.assertEqual(len(job.mapping),48)
                self.assertNotIn('left_index_3',job.fingers)

    def test_extra_native_parents_are_strict_not_skipped_by_name(self):
        for family in self.families:
            obj=skeleton(self.helper,family);name='spine_04' if family=='EPIC_UE5' else 'UpperChest'
            bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
            obj.data.edit_bones[name].parent=obj.data.edit_bones['root' if family=='EPIC_UE5' else 'Root']
            bpy.ops.object.mode_set(mode='OBJECT')
            with self.assertRaisesRegex(ValueError,'hierarchy'):self.rt.validate_target(obj,self.scene)

    def test_ue5_missing_intermediate_body_bone_is_refused(self):
        obj=skeleton(self.helper,'EPIC_UE5');obj.data.bones['spine_02'].name='wrong'
        with self.assertRaisesRegex(ValueError,'hierarchy'):self.rt.validate_target(obj,self.scene)

    def test_ue5_unsafe_or_missing_metacarpal_omits_only_dependent_finger(self):
        obj=skeleton(self.helper,'EPIC_UE5');match=self.rt.target_profile(obj)
        obj.data.bones['index_metacarpal_l'].name='wrong'
        self.assertEqual(len(self.fingers.resolve(obj,match)),27)
        self.assertIn('left_thumb_1',self.fingers.resolve(obj,match))
        obj.pose.bones['middle_metacarpal_r'].scale=(1,2,1)
        self.assertEqual(len(self.fingers.resolve(obj,match)),24)

    def test_calibrated_bake_no_prior_action_root_once_reference_and_repeat(self):
        motion=self.motion()
        for family in self.families:
            with self.subTest(family=family):
                obj=skeleton(self.helper,family,rotation=math.pi/2,scale=.01)
                rest=self.helper.signature(obj);reference=obj.matrix_world.copy();first=self.bake(obj,motion)
                self.assertEqual(len(first.fingers),30);self.assertEqual(first.published['vta_target_profile'],family)
                self.assertNotIn('vta_previous_action',first.published)
                self.scene.frame_set(0);bpy.context.view_layer.update();self.helper.assert_matrix_close(obj.matrix_world,reference)
                for bone in obj.pose.bones:self.helper.assert_matrix_close(bone.matrix,bone.bone.matrix_local)
                expected={}
                for frame in range(1,6):
                    self.scene.frame_set(frame);bpy.context.view_layer.update()
                    got=obj.matrix_world@obj.pose.bones[first.mapping['hips']].matrix
                    self.assertLess((got.translation-Matrix(motion['samples'][frame-1]['poses']['hips']).translation).length,2e-5)
                    root=obj.pose.bones[self.rt.target_profile(obj).profile.root]
                    self.helper.assert_matrix_close(root.matrix_basis,Matrix.Identity(4));expected[frame]=obj.matrix_world.copy()
                second=self.bake(obj,motion);self.assertIs(second.published['vta_previous_action'],first.published)
                for frame,world in expected.items():
                    self.scene.frame_set(frame);bpy.context.view_layer.update();self.helper.assert_matrix_close(obj.matrix_world,world)
                self.assertEqual([(b.name,tuple(map(tuple,b.matrix_local))) for b in obj.data.bones],rest[1])

    def test_body_matrices_identical_with_and_without_optional_fingers(self):
        motion=self.motion();body=copy.deepcopy(motion);body['version']=1
        body['joints']=[j for j in body['joints'] if j['id'] not in self.fingers.PARENTS]
        for sample in body['samples']:sample['poses']={s:m for s,m in sample['poses'].items() if s not in self.fingers.PARENTS}
        for family in self.families:
            obj=skeleton(self.helper,family);job=self.bake(obj,motion);expected={}
            for frame in range(1,6):
                self.scene.frame_set(frame);bpy.context.view_layer.update()
                expected[frame]={s:(obj.matrix_world@obj.pose.bones[n].matrix).copy() for s,n in job.mapping.items() if s not in self.fingers.PARENTS}
            job=self.bake(obj,body)
            for frame,rows in expected.items():
                self.scene.frame_set(frame);bpy.context.view_layer.update()
                for s,m in rows.items():self.helper.assert_matrix_close(obj.matrix_world@obj.pose.bones[job.mapping[s]].matrix,m,2e-5)

    def test_previous_outside_range_action_preserved_and_editable_new_curves(self):
        for family in self.families:
            obj=skeleton(self.helper,family);match=self.rt.target_profile(obj)
            obj.pose.bones[match.mapping['hips']].keyframe_insert('rotation_quaternion',frame=1000)
            old=obj.animation_data.action;before=self.rt.action_fingerprint(obj);flag=old.use_fake_user
            job=self.bake(obj,self.motion());self.assertIs(job.published['vta_previous_action'],old)
            self.assertEqual(self.rt.action_fingerprint(SimpleNamespace(animation_data=SimpleNamespace(action=old))),before)
            self.assertEqual(old.use_fake_user,flag)
            self.assertTrue(any(k.co.x==1000 for c in self.rt._iter_action_fcurves(old) for k in c.keyframe_points))
            curve=next(iter(self.rt._iter_action_fcurves(job.published)));self.assertFalse(curve.lock)
            value=curve.keyframe_points[1].co.y;curve.keyframe_points[1].co.y=value+.01
            self.assertNotEqual(curve.keyframe_points[1].co.y,value)

    def test_cancel_mid_bake_failure_and_publication_failure_rollback(self):
        motion=self.motion()
        for family in self.families:
            for fail in ('cancel','write','publish'):
                with self.subTest(family=family,fail=fail):
                    obj=skeleton(self.helper,family);hips=obj.pose.bones[self.rt.target_profile(obj).mapping['hips']]
                    hips.keyframe_insert('rotation_quaternion',frame=1000);hips.location=(.2,.3,.4)
                    old=obj.animation_data.action;before=self.rt.action_fingerprint(obj);raw=self.rt._rig_state(obj)
                    count=len(self.scene.objects);actions=set(bpy.data.actions)
                    if fail=='cancel':
                        job=self.rt.RetargetJob(self.scene,obj,motion);stream=job.steps()
                        for phase,_ in stream:
                            if phase=='Baking':break
                        stream.close()
                    elif fail=='write':
                        fast=self.helper.fast;original=fast._bulk_write_keys
                        def broken(*args):
                            for value in original(*args):
                                yield value
                                raise RuntimeError('injected native partial-write failure')
                        with mock.patch.object(fast,'_bulk_write_keys',broken),self.assertRaisesRegex(RuntimeError,'injected'):
                            self.bake(obj,motion)
                    else:
                        def broken_publish():raise RuntimeError('injected native publication failure')
                        with self.assertRaisesRegex(RuntimeError,'publication'):self.bake(obj,motion,before_publish=broken_publish)
                    self.assertIs(obj.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(obj),before)
                    self.assertEqual(self.rt._rig_state(obj),raw);self.assertEqual(len(self.scene.objects),count)
                    self.assertEqual(set(bpy.data.actions),actions)

    def test_constraints_drivers_nla_and_inheritance_still_refused(self):
        for family in self.families:
            obj=skeleton(self.helper,family);hips=obj.pose.bones[self.rt.target_profile(obj).mapping['hips']]
            c=hips.constraints.new('COPY_ROTATION')
            with self.assertRaisesRegex(ValueError,'constraints'):self.rt.validate_target(obj,self.scene)
            hips.constraints.remove(c);obj.animation_data_create().nla_tracks.new()
            with self.assertRaisesRegex(ValueError,'NLA'):self.rt.validate_target(obj,self.scene)
            obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0]);hips.bone.use_inherit_rotation=False
            with self.assertRaisesRegex(ValueError,'inheritance'):self.rt.validate_target(obj,self.scene)

    def test_multi_character_isolation_not_a_two_rig_special_case(self):
        motion=self.motion();objects=[skeleton(self.helper,f,prefix='identical:') for f in (*self.families,*self.families)]
        for index,obj in enumerate(objects):
            motion_i=copy.deepcopy(motion)
            for sample in motion_i['samples']:sample['poses']['hips'][0][3]+=index*2
            self.bake(obj,motion_i)
        for obj in objects:
            others={o:state(self.rt,self.scene,o) for o in objects if o is not obj};self.bake(obj,motion)
            for other,before in others.items():self.assertEqual(state(self.rt,self.scene,other),before)

    def test_native_extra_bones_neutral_finite_skinned_geometry_and_source_unchanged(self):
        motion=self.motion();before=copy.deepcopy(motion)
        for family in self.families:
            obj=skeleton(self.helper,family);mesh=skin(self.scene,obj);job=self.bake(obj,motion)
            for frame in (0,1,3,5):
                self.scene.frame_set(frame);bpy.context.view_layer.update()
                ev=mesh.evaluated_get(bpy.context.evaluated_depsgraph_get());data=ev.to_mesh()
                self.assertTrue(all(math.isfinite(v) for p in data.vertices for v in ev.matrix_world@p.co))
                for index,bone in enumerate(obj.pose.bones):
                    got=ev.matrix_world@data.vertices[index*3].co;expected=(obj.matrix_world@bone.matrix).translation
                    self.assertLess((got-expected).length,2e-5,bone.name)
                ev.to_mesh_clear()
                for bone in obj.pose.bones:
                    if bone.name not in job.mapping.values():self.helper.assert_matrix_close(bone.matrix_basis,Matrix.Identity(4))
            self.assertFalse(any(o.name.startswith('.VTA ') for o in self.scene.objects))
        self.assertEqual(motion,before)

    def test_control_family_markers_cannot_fall_back_to_skeleton_profiles(self):
        for family in self.families:
            for marker in ('cloudrig','is_generated_cloudrig'):
                obj=skeleton(self.helper,family);obj.data[marker]=True
                for forced in ('AUTO',family):
                    with self.assertRaisesRegex(ValueError,'CloudRig control adapter'):
                        self.rt.validate_target(obj,self.scene,forced)

    def test_save_fresh_reopen_and_repeat_both_profiles(self):
        motion=self.motion();expected={}
        for family in self.families:
            obj=skeleton(self.helper,family,prefix='saved:');skin(self.scene,obj);self.bake(obj,motion)
            expected[obj.name]=self.rt.action_fingerprint(obj)
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='vta-native-roundtrip-') as folder:
            path=Path(folder)/'proof.blend';bpy.data.libraries.write(str(path),{self.scene},fake_user=True)
            motion_path=Path(folder)/'motion.json';motion_path.write_text(json.dumps(motion))
            script=f'''import sys,json,math
sys.dont_write_bytecode=True
sys.path.insert(0,{str(root)!r})
import bpy
from pathlib import Path
from tests.test_blendcap_vta_retarget import RetargetTests
RetargetTests.setUpClass();rt=RetargetTests.rt
motion=json.loads(Path({str(motion_path)!r}).read_text())
expected=json.loads({json.dumps(expected)!r})
assert len(expected)==2
for name,fingerprint in list(expected.items()):
 obj=bpy.data.objects[name]
 assert rt.action_fingerprint(obj)==fingerprint
 old=obj.animation_data.action
 job=rt.RetargetJob(bpy.context.scene,obj,motion)
 for _ in job.steps():pass
 assert job.published['vta_previous_action']==old and len(job.fingers)==30
 for other,fp in expected.items():
  if other!=name:assert rt.action_fingerprint(bpy.data.objects[other])==fp
 expected[name]=rt.action_fingerprint(obj)
 for frame in (0,1,3,5):
  bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
  for bone in obj.pose.bones:assert all(math.isfinite(v) for row in bone.matrix for v in row)
RetargetTests.tearDownClass()
print('NATIVE_FRESH_REOPEN_REPEAT_PASS')
'''
            result=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec',str(path),
                '--python-exit-code','1','--python-expr',script],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('NATIVE_FRESH_REOPEN_REPEAT_PASS',result.stdout)
