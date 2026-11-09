"""Durable M4 client, thin UI routing and native previous-output regressions."""
import hashlib,importlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
from uuid import uuid4
from blendcap_vta_jobs import ControlTask,JobHandle,read_artifact
import vta_protocol as wire
try:import bpy
except ImportError:bpy=None

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.jid=uuid4().hex
        self.folder=self.root/'.vta-engine/jobs'/self.jid;self.folder.mkdir(parents=True)
    def artifact(self,raw):
        (self.folder/'result.json').write_bytes(raw)
        return dict(job_id=self.jid,name='result.json',sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
    def test_duplicate_fields_nonfinite_and_corrupt_manifest_fail_closed(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}'):
            with self.assertRaises(ValueError):read_artifact(self.root,self.artifact(raw))
        manifest=self.artifact(b'{"a":1}')
        with self.assertRaises(ValueError):read_artifact(self.root,{**manifest,'sha256':'0'*64})
        with self.assertRaises(ValueError):read_artifact(self.root,{**manifest,'name':'../project_state.json'})
    def test_validated_artifact_and_control_task_keep_errors_visible(self):
        manifest=self.artifact(b'{"a":1}');self.assertEqual(read_artifact(self.root,manifest),dict(a=1))
        task=ControlTask();task.submit(lambda:7);task.thread.join(5);self.assertEqual(task.take(),7)
        def fail():raise ValueError('isolated failure')
        task.submit(fail);task.thread.join(5)
        with self.assertRaisesRegex(ValueError,'isolated failure'):task.take()
    def test_retarget_controls_reject_commands_paths_and_unknown_fields(self):
        payload=dict(companion_root=str(self.root),project_id='p',expected_revision=dict(number=0,sha256='a'*64),source_id='s',input_id=uuid4().hex,input_sha256='b'*64,input_bytes=100)
        wire.request(uuid4().hex,payload,'retarget-export')
        for change in ({'input_id':'../escape'},{'command':'echo bad'},{'input_bytes':32*1024*1024+1}):
            with self.assertRaises(wire.ProtocolError):wire.request(uuid4().hex,{**payload,**change},'retarget-export')

@unittest.skipUnless(bpy is not None,'Blender required')
class JobsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass();cls.addon=BlenderOutputTests.addon;cls.addon.register()
        cls.ui=importlib.import_module(cls.addon.__name__+'.blendcap.vta_review_ui')
        cls.jobs=importlib.import_module(cls.addon.__name__+'.blendcap.vta_jobs_ui')
        cls.restore=importlib.import_module(cls.addon.__name__+'.blendcap.vta_output_restore')
    @classmethod
    def tearDownClass(cls):cls.addon.unregister()
    def setUp(self):
        self.jobs.clear();temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.scene=bpy.data.scenes.new('M4 job test');self.old_scene=bpy.context.scene;bpy.context.window.scene=self.scene
        for key,value in dict(vta_scene_owner_id=uuid4().hex,vta_project_id='p',vta_source_id='s').items():self.scene[key]=value
        self.scene.blendcap_props.fov_mode='FIRST';self.scene.blendcap_vta_automatic_depth=False
        self.context=NS(scene=self.scene,window_manager=bpy.context.window_manager)
        self.location=NS(companion_root=self.root,source_path=self.root/'source.mp4',blender_frame=11,source_frame=10)
        self.source=dict(source_id='s',start_frame=0,end_frame=162,video_sha256='a'*64)
        self.context.window_manager.blendcap_vta_animation_scope='RANGE'
        self.context.window_manager.blendcap_vta_animation_start=11;self.context.window_manager.blendcap_vta_animation_end=15
        self.client=mock.Mock();self.backend=mock.Mock()
        def prepare(location,ids,bounds,**kw):
            handle=JobHandle(self.root,kw['request_id'],'generation',self.client)
            handle.status=dict(state='prepared')
            return handle,dict(plan=dict(character_scope=list(ids),source_range=list(bounds)))
        self.backend.prepare.side_effect=prepare
        patches=[mock.patch.object(self.jobs,'_root',return_value=self.root),mock.patch.object(self.ui,'require_loaded_context'),
            mock.patch.object(self.ui,'_context_for_main_scene',side_effect=lambda c:c),mock.patch.object(self.ui,'loaded_context_matches',return_value=True),
            mock.patch.object(self.ui,'_location_from_context',return_value=self.location),mock.patch.object(self.ui,'_engine_client',return_value=self.client),
            mock.patch.object(self.jobs,'_backend',return_value=self.backend),mock.patch.object(self.jobs,'_runtime',return_value={}),
            mock.patch.object(self.ui,'cached_scene_source',return_value=('p',self.source)),mock.patch.object(self.ui,'tracking_character_ids',return_value=('white','yellow'))]
        for patch in patches:patch.start();self.addCleanup(patch.stop)
    def tearDown(self):
        active=self.jobs._active
        if active and active['task'].thread:active['task'].thread.join(5)
        self.jobs.clear();bpy.context.window.scene=self.old_scene
        for obj in tuple(self.scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.scenes.remove(self.scene)
        for text in tuple(bpy.data.texts):
            if text.get('vta_output_restore_project')=='p':bpy.data.texts.remove(text)
    def test_generation_prepares_exact_selected_ids_source_range_and_fixed_defaults(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        self.assertEqual(self.backend.prepare.call_args.args,(self.location,('white','yellow'),(10,14)))
        self.assertEqual(self.backend.prepare.call_args.kwargs['depth_mode'],'FIXED');self.assertEqual(self.backend.prepare.call_args.kwargs['fov_mode'],'FIRST')
        self.assertTrue(self.jobs.busy());self.assertTrue(self.jobs._path(self.root,job['pointer']).is_file())
        self.assertEqual(job['phase'],'prepare');self.client.call.assert_not_called() # No implicit provider start until main-thread adoption.
    def test_whole_source_ignores_hidden_range_and_keeps_selected_characters(self):
        self.context.window_manager.blendcap_vta_animation_scope='WHOLE'
        self.context.window_manager.blendcap_vta_animation_start=500
        self.context.window_manager.blendcap_vta_animation_end=-2
        with mock.patch.object(self.ui,'tracking_character_ids',return_value=('yellow',)):
            self.jobs.start_generation(self.context)
        self.jobs._active['task'].thread.join(5)
        self.assertEqual(self.backend.prepare.call_args.args,(self.location,('yellow',),(0,162)))
    def test_invalid_frame_range_does_not_prepare_or_save_job(self):
        for start,end in ((15,11),(0,15),(11,165)):
            with self.subTest(start=start,end=end):
                self.context.window_manager.blendcap_vta_animation_start=start
                self.context.window_manager.blendcap_vta_animation_end=end
                with self.assertRaises(ValueError):self.jobs.start_generation(self.context)
                self.assertIsNone(self.jobs._active)
                self.backend.prepare.assert_not_called()
                self.assertFalse((self.root/'.vta-engine').exists())
    def test_automatic_and_every_are_explicit_job_inputs(self):
        self.scene.blendcap_vta_automatic_depth=True;self.scene.blendcap_props.fov_mode='EVERY'
        self.jobs.start_generation(self.context);self.jobs._active['task'].thread.join(5)
        self.assertEqual(self.backend.prepare.call_args.kwargs['depth_mode'],'AUTOMATIC');self.assertEqual(self.backend.prepare.call_args.kwargs['fov_mode'],'EVERY')
    def test_zero_selection_and_duplicate_scene_identity_never_prepare(self):
        with mock.patch.object(self.ui,'tracking_character_ids',return_value=()):
            with self.assertRaisesRegex(ValueError,'Select at least one'):self.jobs.start_generation(self.context)
        self.backend.prepare.assert_not_called()
        other=bpy.data.scenes.new('duplicate M4 owner');other['vta_scene_owner_id']=self.scene['vta_scene_owner_id']
        try:
            with self.assertRaisesRegex(ValueError,'Duplicate Scene'):self.jobs.start_generation(self.context)
        finally:bpy.data.scenes.remove(other)
    def test_unresolved_pointer_blocks_duplicate_work_and_recover_does_not_start_models(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        self.jobs._active=None
        with self.assertRaisesRegex(ValueError,'Recover or discard'):self.jobs.start_generation(self.context)
        self.jobs._start(self.context,'generation',recover=True);self.jobs._active['task'].thread.join(5)
        self.assertEqual(self.client.call.call_args.args[0],'job-status');self.assertEqual(self.backend.prepare.call_count,1)
    def test_draw_and_poll_never_invoke_engine_or_private_backend(self):
        from tests.test_blendcap_vta_retarget import RecordingLayout
        self.jobs._messages[(self.scene['vta_scene_owner_id'],'generation')]='body reconstruction 2/4'
        for _ in range(30):self.jobs.draw(RecordingLayout(),self.context,'generation');self.jobs.busy()
        self.client.call.assert_not_called();self.backend.prepare.assert_not_called()
    def test_cancel_is_request_scoped_and_not_a_shared_marker(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        self.assertTrue(self.jobs.request_cancel());self.assertTrue(job['cancel'])
        self.assertFalse((self.root/'analysis/vta-analysis-stop').exists())
    def test_cancel_after_acceptance_keeps_result_and_skips_native_application(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        job['cancel']=True
        self.jobs._status(job,dict(project_id='p',source_id='s',state='accepted'))
        self.assertIsNone(self.jobs._active);self.client.call.assert_not_called()
        self.assertIn('Native application cancelled',self.jobs._messages[self.jobs._key(self.scene,'generation')])
    def test_changed_depth_settings_reject_native_application(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        value=dict(depth_mode='AUTOMATIC',report=dict(reconstruction=dict(mode='FIRST'),plan=dict(character_scope=['white'])))
        with self.assertRaisesRegex(ValueError,'Generation settings changed'):self.jobs._apply(job,[],[value])
        self.client.call.assert_not_called()
    def test_wrong_source_status_never_gets_accepted(self):
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5)
        with self.assertRaisesRegex(ValueError,'project/source differs'):self.jobs._status(job,dict(project_id='p',source_id='other',state='ready'))
        self.client.call.assert_not_called()
    def test_known_missing_preparation_can_be_recovered_without_model_replay(self):
        from blendcap_vta_engine import EngineError
        self.jobs.start_generation(self.context);job=self.jobs._active;job['task'].thread.join(5);self.jobs._active=None
        self.client.call.side_effect=EngineError('JOB_NOT_FOUND','No durable preparation record.')
        self.jobs._start(self.context,'generation',recover=True);job=self.jobs._active;job['task'].thread.join(5)
        self.jobs.tick();self.assertIsNone(self.jobs._active)
        self.assertTrue(self.jobs._saved[self.jobs._key(self.scene,'generation')]['settled'])
        self.assertEqual(self.backend.prepare.call_count,1)
    def test_restore_refuses_manual_keys_and_duplicate_scene_owner(self):
        data=bpy.data.armatures.new('M4 restore armature');obj=bpy.data.objects.new('M4 restore object',data);self.scene.collection.objects.link(obj)
        for key,value in dict(vta_project_id='p',vta_source_id='s',vta_character_id='white').items():obj[key]=value
        obj.location=(0,0,0);obj.keyframe_insert('location',frame=1)
        candidate=self.restore.capture(self.scene,'p','s',companion_root=self.root)
        stage=bpy.data.objects.new('M4 fresh Action stage',None);self.scene.collection.objects.link(stage)
        stage.keyframe_insert('location',frame=1)
        bpy.context.view_layer.update()
        obj.animation_data.action=stage.animation_data.action;obj.animation_data.action_slot=stage.animation_data.action_slot
        obj.location=(0,1,0);obj.keyframe_insert('location',frame=1)
        self.assertTrue(self.restore.publish(self.scene,candidate));new=obj.animation_data.action
        curve=next(self.restore._iter_action_fcurves(new));curve.mute=True
        with self.assertRaisesRegex(ValueError,'Manual work was retained'):self.restore.restore(self.scene)
        curve.mute=False
        obj.location=(0,2,0);obj.keyframe_insert('location',frame=2)
        with self.assertRaisesRegex(ValueError,'Manual work was retained'):self.restore.restore(self.scene)
        self.assertIs(obj.animation_data.action,new)
        other=bpy.data.scenes.new('duplicate restore owner');other['vta_scene_owner_id']=self.scene['vta_scene_owner_id']
        try:self.assertIsNone(self.restore.slot(self.scene))
        finally:bpy.data.scenes.remove(other)

if __name__=='__main__':unittest.main()
