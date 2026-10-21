"""Optional evaluated BlendCap finger passthrough, no trackers/inference."""
import copy
import importlib
import math
import unittest
from unittest import mock
try:
    import bpy
    from mathutils import Matrix,Vector
except ImportError:bpy=None


def core(rt):return importlib.import_module(rt.__package__+'.vta_review_ui')._get_backend(bpy.context)._core


def add_fingers(rt,rig,family='MIXAMO',prefix='',omit=()):
    conventions=importlib.import_module(rt.__package__+'.vta_fingers')
    body=rt.target_profile(rig).mapping if family!='SOURCE' else {b['id']:b['bone'] for b in core(rt).retarget_source_bindings()}
    if family!='SOURCE' and not prefix:prefix=rt.target_profile(rig).prefix
    names=conventions.MAPS['MIXAMO' if family=='SOURCE' else family]
    for obj in bpy.context.selected_objects:obj.select_set(False)
    rig.select_set(True);bpy.context.view_layer.objects.active=rig;bpy.ops.object.mode_set(mode='EDIT')
    for semantic,name in names.items():
        if semantic in omit:continue
        side,finger,segment=semantic.split('_');segment=int(segment);sign=1 if side=='left' else -1
        hand=rig.data.edit_bones[body[side+'_hand']];parent=conventions.PARENTS[semantic]
        parent_name=body[parent] if parent in body else prefix+names[parent]
        if parent_name not in rig.data.edit_bones:continue
        root=hand.head.copy();spread=(('thumb','index','middle','ring','pinky').index(finger)-2)*.015
        bone=rig.data.edit_bones.new(prefix+name)
        bone.head=root+Vector((sign*(.02+(segment-1)*.035),spread,0))
        bone.tail=bone.head+Vector((sign*.035,.012 if finger=='thumb' else 0,0))
        bone.parent=rig.data.edit_bones[parent_name]
    bpy.ops.object.mode_set(mode='OBJECT')
    for b in rig.pose.bones:b.rotation_mode='QUATERNION'


def finger_motion(helper,frames=5,corrected=False):
    vta_core=core(helper.rt)
    names={b['id']:b['bone'] for b in vta_core.retarget_source_bindings()}
    source=helper.rig(prefix='',names=names);source.name='Faithful BlendCap source'
    source['vta_character_id']='finger-character';add_fingers(helper.rt,source,'SOURCE')
    if corrected:source.location=(2,-3,.5);source.rotation_euler=(.1,0,.3);source.scale=(1.4,)*3
    for f in range(1,frames+1):
        for b in source.pose.bones:
            finger='Hand' in b.name and b.name[-1:] in ('1','2','3')
            angle=.12*math.sin(f*.4)*int(b.name[-1])*(1 if b.name.startswith('Left') else -1) if finger else .03*f
            b.rotation_quaternion=Matrix.Rotation(angle,4,'X' if finger else 'Z').to_quaternion()
            b.keyframe_insert('rotation_quaternion',frame=f)
    stream=helper.rt.capture_motion(source,vta_core.retarget_source_bindings(include_fingers=True),1,frames)
    while True:
        try:next(stream)
        except StopIteration as done:rest,samples=done.value;break
    return source,vta_core.normalize_retarget_motion(character_id='finger-character',source_id='finger-source',fps=24,rest=rest,samples=samples)


@unittest.skipUnless(bpy is not None,'Blender required')
class FingerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_retarget import RetargetTests
        cls.helpers=RetargetTests;RetargetTests.setUpClass();cls.rt=RetargetTests.rt
        cls.fingers=importlib.import_module(cls.rt.__package__+'.vta_fingers')
    @classmethod
    def tearDownClass(cls):cls.helpers.tearDownClass()
    def setUp(self):
        self.helper=self.helpers();self.helper.setUp();self.scene=self.helper.scene
    def tearDown(self):self.helper.tearDown()
    def bake(self,target,motion):return self.helper.run_job(target,motion)

    def test_evaluated_source_has_30_semantic_joints_and_exact_left_right_thumb_chains(self):
        source,motion=finger_motion(self.helper)
        self.assertEqual(motion['version'],2);self.assertEqual(len(motion['joints']),52)
        joints={j['id']:j for j in motion['joints']}
        for side in ('left','right'):
            self.assertEqual(joints[side+'_thumb_1']['parent'],side+'_hand')
            self.assertEqual(joints[side+'_thumb_3']['parent'],side+'_thumb_2')
        self.assertNotEqual(motion['samples'][1]['poses']['left_index_2'],motion['samples'][1]['poses']['right_index_2'])

    def test_mixamo_all_fingers_and_body_unchanged_with_optional_stream(self):
        source,motion=finger_motion(self.helper);target=self.helper.rig();add_fingers(self.rt,target)
        first=self.bake(target,motion);self.assertEqual(len(first.fingers),30)
        expected={}
        for f in range(1,6):
            self.scene.frame_set(f);bpy.context.view_layer.update()
            expected[f]={s:(target.matrix_world@target.pose.bones[n].matrix).copy() for s,n in first.mapping.items() if s not in self.fingers.PARENTS}
        body=copy.deepcopy(motion);body['version']=1;body['joints']=[j for j in body['joints'] if j['id'] not in self.fingers.PARENTS]
        for sample in body['samples']:sample['poses']={s:m for s,m in sample['poses'].items() if s not in self.fingers.PARENTS}
        second=self.bake(target,body);self.assertFalse(second.fingers)
        for f,rows in expected.items():
            self.scene.frame_set(f);bpy.context.view_layer.update()
            for s,m in rows.items():self.helper.assert_matrix_close(target.matrix_world@target.pose.bones[second.mapping[s]].matrix,m,2e-5)

    def test_partial_target_chain_and_no_finger_source_fail_soft(self):
        _,motion=finger_motion(self.helper);target=self.helper.rig()
        add_fingers(self.rt,target,omit=('left_index_2',))
        job=self.bake(target,motion);self.assertEqual(len(job.fingers),28)
        self.assertNotIn('left_index_3',job.fingers)
        self.assertFalse(self.bake(target,self.helper.motion()).fingers)

    def test_sparse_finger_sample_is_not_keyed_or_invented(self):
        _,motion=finger_motion(self.helper);target=self.helper.rig();add_fingers(self.rt,target)
        for segment in (2,3):del motion['samples'][2]['poses']['left_index_'+str(segment)]
        before=copy.deepcopy(motion);job=self.bake(target,motion);self.assertEqual(motion,before)
        path=target.pose.bones[job.fingers['left_index_2']].path_from_id('rotation_quaternion')
        for curve in self.rt._iter_action_fcurves(job.published):
            if curve.data_path==path:self.assertEqual([k.co.x for k in curve.keyframe_points],[0.,1.,2.,4.,5.])

    def test_epic_optional_mapping_and_frame_zero_repeat(self):
        _,motion=finger_motion(self.helper);target=self.helper.epic_rig(prefix='UE4:')
        add_fingers(self.rt,target,'EPIC_UE4','UE4:');first=self.bake(target,motion)
        self.assertEqual(len(first.fingers),30);self.assertEqual(first.fingers['right_thumb_1'],'UE4:thumb_01_r')
        self.scene.frame_set(0);bpy.context.view_layer.update();world=target.matrix_world.copy()
        second=self.bake(target,motion);self.scene.frame_set(0);bpy.context.view_layer.update()
        self.helper.assert_matrix_close(target.matrix_world,world)
        self.assertIs(second.published['vta_previous_action'],first.published)

    def test_finger_cancel_failure_and_existing_action_are_transactional(self):
        _,motion=finger_motion(self.helper);target=self.helper.rig();add_fingers(self.rt,target)
        old,fingerprint=self.helper.old_action(target)
        job=self.rt.RetargetJob(self.scene,target,motion);stream=job.steps();next(stream);stream.close()
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)
        with mock.patch.object(self.rt,'_matrix_bake_pairs_iter_fast',side_effect=RuntimeError('finger failure')):
            with self.assertRaisesRegex(RuntimeError,'finger failure'):self.bake(target,motion)
        self.assertIs(target.animation_data.action,old);self.assertEqual(self.rt.action_fingerprint(target),fingerprint)

    def test_all_catalog_families_have_30_unique_optional_names(self):
        for family,mapping in self.fingers.MAPS.items():
            self.assertEqual(len(mapping),30,family);self.assertEqual(len(set(mapping.values())),30,family)
        self.assertEqual(self.fingers.MAPS['GODOT_HUMANOID']['left_thumb_1'],'LeftThumbMetacarpal')
        self.assertEqual(self.fingers.MAPS['GODOT_HUMANOID']['right_pinky_3'],'RightLittleDistal')

    def test_source_object_mode_corrections_and_unrelated_target_are_preserved(self):
        source,motion=finger_motion(self.helper,corrected=True);target=self.helper.rig();add_fingers(self.rt,target)
        other=self.helper.rig();add_fingers(self.rt,other);self.bake(other,motion)
        action=other.animation_data.action;fingerprint=self.rt.action_fingerprint(other)
        self.scene.frame_set(3);bpy.context.view_layer.update();before=other.matrix_world.copy()
        job=self.bake(target,motion)
        self.assertEqual(len(job.fingers),30);self.assertIs(other.animation_data.action,action)
        self.assertEqual(self.rt.action_fingerprint(other),fingerprint);self.helper.assert_matrix_close(other.matrix_world,before)
        self.scene.frame_set(2);bpy.context.view_layer.update()
        expected=(source.matrix_world@source.pose.bones['LeftHandThumb2'].matrix).translation
        self.assertLess((expected-Matrix(motion['samples'][1]['poses']['left_thumb_2']).translation).length,1e-5)

    def test_unsafe_optional_chain_is_omitted_without_failing_body(self):
        _,motion=finger_motion(self.helper);target=self.helper.rig();add_fingers(self.rt,target)
        target.pose.bones['mixamorig:LeftHandThumb1'].rotation_mode='AXIS_ANGLE'
        job=self.bake(target,motion);self.assertEqual(len(job.fingers),27)
        self.assertNotIn('left_thumb_3',job.fingers)

    def test_local_legacy_profile_finger_names_match_the_catalog(self):
        import json
        from pathlib import Path
        rows={b['bone']:b['id'] for b in core(self.rt).retarget_source_bindings(include_fingers=True) if b['id'] in self.fingers.PARENTS}
        for family,preset in (('MIXAMO','mixamo'),('RIGIFY','rigify_new'),('ARP','arp'),('CLOUDRIG','cloudrig')):
            data=json.loads((Path(__file__).resolve().parents[1]/'retarget_maps/blendcap_standard'/('blendcap_to_'+preset+'.json')).read_text())
            actual={rows[p['source']]:p['target'] for p in data['pairs'] if p['source'] in rows}
            self.assertEqual(actual,self.fingers.MAPS[family])
