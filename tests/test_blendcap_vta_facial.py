"""Real Blender facial ownership/transactions; no model dependency in unit tests."""
import ast
import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from vta_face_worker import associate,MODELS,ImportGuard

try:import bpy
except ImportError:bpy=None


class FaceProviderTests(unittest.TestCase):
    def test_missing_and_corrupt_models_fail_structurally_offline(self):
        script=Path(__file__).resolve().parents[1]/'vta_face_worker.py'
        python=Path(sys.prefix)/'bin'/'python.exe' if bpy else Path(sys.executable)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=root/'request.json';request.write_text('{}')
            for corrupt in (False,True):
                if corrupt:
                    for name in MODELS:(root/name).write_bytes(b'wrong model')
                output=root/'result.json'
                process=subprocess.run([str(python),'-I','-B',str(script),'--request',str(request),
                    '--output',str(output),'--video',str(root/'absent.mp4'),'--models',str(root)],
                    capture_output=True,text=True,timeout=15)
                self.assertEqual(process.returncode,1,process.stderr)
                result=json.loads(output.read_text())
                self.assertEqual(result['status'],'failed')
                self.assertIn('hash mismatch' if corrupt else 'Missing local face model',result['details'])

    def test_ambiguous_not_selected_by_score(self):
        ctx=dict(head_roi=[0,0,100,100],hard_boundary=False,other_body_rois=[])
        self.assertEqual(associate([dict(box=[10,10,30,30],score=.99),dict(box=[40,10,60,30],score=.51)],ctx)[1],'ambiguous')

    def test_foreign_context_rejected(self):
        ctx=dict(head_roi=[0,0,100,100],hard_boundary=False,other_body_rois=[[0,0,100,100]])
        self.assertEqual(associate([dict(box=[10,10,30,30],score=.99)],ctx)[1],'ambiguous')

    def test_unknown_context_unavailable(self):
        self.assertEqual(associate([],dict(head_roi=None,hard_boundary=False,other_body_rois=[]))[1],'unavailable')

    def test_private_and_host_imports_guarded(self):
        for name in ['bpy','blendcap','vta_core','pipeline','save_face_data']:
            with self.assertRaises(ImportError):ImportGuard().find_spec(name)
        path=Path(__file__).resolve().parents[1]/'vta_face_worker.py'
        names=[n.module for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(set(names)&{'bpy','blendcap','vta_core'})
        self.assertEqual(len(MODELS),2)


@unittest.skipUnless(bpy is not None,'Blender required')
class FacialApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        cls.face=importlib.import_module(BlenderOutputTests.output.__package__+'.vta_facial')
        from vta_core import facial
        cls.core=facial

    def setUp(self):
        self.scene=bpy.data.scenes.new('facial test');self.scene['vta_scene_owner_id']='scene-face-test'
        self.scene_before=bpy.context.window.scene;bpy.context.window.scene=self.scene
        mesh=bpy.data.meshes.new('facial target');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
        self.obj=bpy.data.objects.new('facial target',mesh);self.scene.collection.objects.link(self.obj)
        for name in ['Basis','eyeBlinkLeft','jawOpen','user_control','tongueOut']:
            key=self.obj.shape_key_add(name=name)
            if name!='Basis':key.value=0.
        self.identity=dict(scene_id='scene-face-test',project_id='p',source_id='s',character_id='red')
        self.binding=self.face.bind(self.scene,self.obj,self.identity,self.core.CHANNELS)
        self.actions=set(bpy.data.actions)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        bpy.context.window.scene=self.scene_before
        for obj in list(self.scene.objects):
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if data and data.users==0:bpy.data.meshes.remove(data)
        bpy.data.scenes.remove(self.scene)
        for a in set(bpy.data.actions)-self.actions:
            if a.users==0:bpy.data.actions.remove(a)

    def samples(self,a=0,b=3,value=.6):
        return [dict(source_frame=f,weights={c:value for c in self.core.CHANNELS}) for f in range(a,b+1)]

    def apply(self,a=0,b=3,value=.6):
        stage=self.face.stage(self.obj,self.binding,self.samples(a,b,value),1)
        self.face.publish([stage],lambda:None)
        return stage

    def value(self,frame,name='eyeBlinkLeft'):
        self.scene.frame_set(frame);bpy.context.view_layer.update()
        return self.obj.data.shape_keys.key_blocks[name].value

    def test_mapping_missing_and_unsupported(self):
        self.assertEqual(set(self.binding['mapping']),{'eyeBlinkLeft','jawOpen'})
        self.assertNotIn('tongueOut',self.binding['mapping'])

    def test_suffix_aliases_cover_all_canonical_channels_without_renaming(self):
        keys=self.obj.data.shape_keys.key_blocks
        keys['eyeBlinkLeft'].name='eyeBlink_L'
        for channel in self.core.CHANNELS:
            target=channel[:-4]+'_L' if channel.endswith('Left') else channel[:-5]+'_R' if channel.endswith('Right') else channel
            if target not in keys:self.obj.shape_key_add(name=target).value=0.
        names=list(keys.keys());mapping=self.face.channel_map(self.obj,self.core.CHANNELS)
        self.assertEqual(set(mapping),set(self.core.CHANNELS))
        self.assertEqual(mapping['eyeBlinkLeft'],'eyeBlink_L')
        self.assertEqual(mapping['mouthSmileRight'],'mouthSmile_R')
        self.assertEqual(mapping['browDownLeft'],'browDown_L')
        self.assertEqual(mapping['jawOpen'],'jawOpen')
        self.assertEqual(list(keys.keys()),names)
        self.assertNotIn('tongueOut',mapping)

    def test_canonical_and_alias_collision_refused(self):
        self.obj.shape_key_add(name='eyeBlink_L').value=0.
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.face.channel_map(self.obj,self.core.CHANNELS)

    def test_alias_application_uses_actual_target_path(self):
        keys=self.obj.data.shape_keys.key_blocks;keys['eyeBlinkLeft'].name='eyeBlink_L'
        self.binding=self.face.bind(self.scene,self.obj,self.identity,self.core.CHANNELS)
        self.apply()
        self.assertAlmostEqual(self.value(2,'eyeBlink_L'),.6,places=5)
        self.assertNotIn('eyeBlinkLeft',keys)

    def test_animated_alias_target_refused_unchanged(self):
        keys=self.obj.data.shape_keys;keys.key_blocks['eyeBlinkLeft'].name='eyeBlink_L'
        block=keys.key_blocks['eyeBlink_L'];block.keyframe_insert('value',frame=30)
        action=keys.animation_data.action;before=self.face.signature(action)
        with self.assertRaisesRegex(ValueError,'User animation'):self.face.bind(self.scene,self.obj,self.identity,self.core.CHANNELS)
        self.assertEqual(self.face.signature(action),before)
        self.assertNotIn(self.face.OUTPUT,self.obj)

    def test_unmatched_static_values_untouched(self):
        keys=self.obj.data.shape_keys.key_blocks
        keys['tongueOut'].value=.37;keys['user_control'].value=.63
        self.apply()
        for frame in (0,2,4,10):
            self.value(frame)
            self.assertAlmostEqual(keys['tongueOut'].value,.37,places=5)
            self.assertAlmostEqual(keys['user_control'].value,.63,places=5)

    def test_non_neutral_static_matched_control_outside_range(self):
        self.obj.data.shape_keys.key_blocks['eyeBlinkLeft'].value=.4
        with self.assertRaisesRegex(ValueError,'neutral'):self.apply()
        self.assertAlmostEqual(self.value(0),.4,places=5)
        self.assertAlmostEqual(self.value(10),.4,places=5)
        self.assertIsNone(self.obj.data.shape_keys.animation_data)
        self.assertNotIn(self.face.OUTPUT,self.obj)

    def test_unrelated_action_slot_curves_preserved_and_range_isolated(self):
        user=self.obj.data.shape_keys.key_blocks['user_control']
        user.value=.2;user.keyframe_insert('value',frame=1)
        user.value=.8;user.keyframe_insert('value',frame=20)
        ad=self.obj.data.shape_keys.animation_data;action,slot=ad.action,ad.action_slot
        before=self.face.signature(action);fps=(self.scene.render.fps,self.scene.render.fps_base)
        self.apply()
        self.assertIs(ad.action,action);self.assertEqual(ad.action_slot,slot)
        self.assertEqual(before,self.face.signature(action));self.assertEqual(fps,(self.scene.render.fps,self.scene.render.fps_base))
        self.assertAlmostEqual(self.value(2),.6,places=5)
        self.assertAlmostEqual(self.value(10),0.,places=5)

    def test_repeat_preserves_outside_owned_range(self):
        self.apply(0,6,.6);self.apply(2,3,.1)
        self.assertAlmostEqual(self.value(1),.6,places=5)
        self.assertAlmostEqual(self.value(3),.1,places=5)
        self.assertAlmostEqual(self.value(7),.6,places=5)
        self.assertEqual(len(self.obj.data.shape_keys.animation_data.nla_tracks),1)

    def test_derived_filter_provenance_persisted_without_relabelling_raw(self):
        samples=self.samples(0,2)
        for row in samples:row.update(origin='derived_filtered',raw_state='observed',input_origin='observed',filter_policy='vta-face-temporal-1')
        staged=self.face.stage(self.obj,self.binding,samples,1);self.face.publish([staged],lambda:None)
        _,action,_=self.face.owned_track(self.obj)
        before=json.loads(action['vta_face_derivation'])
        self.assertEqual(before['2']['origin'],'derived_filtered')
        self.assertEqual(before['2']['raw_state'],'observed')
        self.apply(5,6)
        _,action,_=self.face.owned_track(self.obj)
        self.assertEqual(json.loads(action['vta_face_derivation'])['2'],before['2'])

    def test_disjoint_ranges_do_not_fill_outside(self):
        self.apply(0,1,.7);self.apply(5,6,.3)
        self.assertAlmostEqual(self.value(4),0.,places=5)
        self.assertAlmostEqual(self.value(6),.3,places=5)

    def test_store_failure_rolls_back_animation_and_metadata(self):
        self.apply();before=self.obj[self.face.OUTPUT];track,action,_=self.face.owned_track(self.obj)
        stage=self.face.stage(self.obj,self.binding,self.samples(value=.2),1)
        with self.assertRaises(OSError):self.face.publish([stage],lambda:(_ for _ in ()).throw(OSError('injected')))
        self.assertEqual(self.obj[self.face.OUTPUT],before);self.assertFalse(track.mute)
        self.assertEqual(self.face.owned_track(self.obj)[1],action)
        self.assertAlmostEqual(self.value(2),.6,places=5)

    def test_staging_cancel_does_not_change_animation(self):
        self.apply();before=self.obj[self.face.OUTPUT]
        stage=self.face.stage(self.obj,self.binding,self.samples(value=.1),1)
        self.face.rollback([stage]);self.assertEqual(self.obj[self.face.OUTPUT],before)
        self.assertAlmostEqual(self.value(2),.6,places=5)

    def test_cancel_with_removed_job_directory_releases_request(self):
        process=mock.Mock();process.poll.return_value=None
        lock=mock.MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            self.face._job=dict(cancelled=False,stop=Path(directory)/'removed'/'stop',process=process,lock=lock)
            try:self.face.cancel_job()
            finally:self.face._job=None
        process.terminate.assert_called_once();lock.__exit__.assert_called_once()
        self.assertFalse(self.face.busy())

    def test_shared_mesh_or_foreign_scene_binding_rejected(self):
        other=bpy.data.objects.new('shared',self.obj.data);self.scene.collection.objects.link(other)
        with self.assertRaises(ValueError):self.face.validate_mesh(self.scene,self.obj,self.binding)
        bpy.data.objects.remove(other,do_unlink=True)
        self.binding['scene_id']='other'
        with self.assertRaises(ValueError):self.face.validate_mesh(self.scene,self.obj,self.binding)

    def test_user_matching_curve_refused_not_rewritten(self):
        block=self.obj.data.shape_keys.key_blocks['eyeBlinkLeft'];block.keyframe_insert('value',frame=30)
        action=self.obj.data.shape_keys.animation_data.action;before=self.face.signature(action)
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.face.signature(action),before)

    def test_edited_owned_action_protected(self):
        self.apply();_,action,_=self.face.owned_track(self.obj)
        curve=next(self.face._iter_action_fcurves(action));curve.keyframe_points[0].co.y=.2
        with self.assertRaises(ValueError):self.apply()

    def test_duplicated_binding_refused(self):
        duplicate=self.obj.copy();duplicate.data=self.obj.data.copy();self.scene.collection.objects.link(duplicate)
        with self.assertRaises(ValueError):self.face.targets(self.scene,[self.identity],self.core.CHANNELS)

    def test_binding_action_slots_and_output_persist_through_library_reload(self):
        self.apply()
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'facial.blend')
            bpy.data.libraries.write(path,{self.obj})
            with bpy.data.libraries.load(path,link=False) as (source,destination):destination.objects=list(source.objects)
            loaded=destination.objects[0];self.scene.collection.objects.link(loaded)
            self.assertEqual(loaded[self.face.BINDING],self.obj[self.face.BINDING])
            self.assertEqual(loaded[self.face.OUTPUT],self.obj[self.face.OUTPUT])
            _,action,_=self.face.owned_track(loaded)
            self.assertEqual(len(action.slots),1)

    def test_matching_drivers_and_solo_layers_are_refused(self):
        keys=self.obj.data.shape_keys
        driver=keys.key_blocks['jawOpen'].driver_add('value')
        with self.assertRaises(ValueError):self.apply()
        keys.key_blocks['jawOpen'].driver_remove('value')
        self.apply();track,_,_=self.face.owned_track(self.obj);track.is_solo=True
        with self.assertRaises(ValueError):self.apply()

    def test_multiple_target_publication_failure_rolls_back_every_target(self):
        self.apply();prior=self.obj[self.face.OUTPUT]
        mesh=self.obj.data.copy();other=bpy.data.objects.new('blue face',mesh);self.scene.collection.objects.link(other)
        # Copy the geometry/keys, not another Character's animation/binding.
        mesh.shape_keys.animation_data_clear()
        binding=self.face.bind(self.scene,other,{**self.identity,'character_id':'blue'},self.core.CHANNELS)
        stages=[self.face.stage(self.obj,self.binding,self.samples(value=.1),1),
                self.face.stage(other,binding,self.samples(value=.2),1)]
        with self.assertRaises(OSError):self.face.publish(stages,lambda:(_ for _ in ()).throw(OSError('disk failure')))
        self.assertEqual(self.obj[self.face.OUTPUT],prior)
        self.assertNotIn(self.face.OUTPUT,other)
        self.assertAlmostEqual(self.value(2),.6,places=5)


if __name__=='__main__':unittest.main()
