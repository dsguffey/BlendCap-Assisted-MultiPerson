"""Presentation and session Undo contracts for deferred tracking reconciliation."""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from blendcap_vta_timeline import TimelineKey, present_timeline
from blendcap_vta_workspace import authoritative_key_style


class TrackingKeyPresentationTests(unittest.TestCase):
    def test_pending_has_color_noncolor_marker_and_explicit_tooltip(self):
        key=TimelineKey('k','grey',62,'visible','obs',tracking_state='pending')
        color,underline=authoritative_key_style(key,(0,1,0,1))
        self.assertEqual(color,(.15,.92,.95,1));self.assertTrue(underline)
        self.assertIn('not yet included',key.tracking_label)
        used=TimelineKey('k','grey',62,'visible','obs',tracking_state='incorporated')
        self.assertEqual(authoritative_key_style(used,(0,1,0,1)),((0,1,0,1),False))
        self.assertIn('included in current tracking',used.tracking_label)

    def test_projection_keeps_key_state_character_source_and_frame_identity(self):
        view=present_timeline(NS(source_id='clinch',characters=(),keys=(NS(
            keyframe_id='k',character_id='grey',frame=61,state=NS(value='visible'),
            observation_id='obs',tracking_state='pending'),)))
        self.assertEqual(view.source_id,'clinch')
        self.assertEqual((view.keys[0].character_id,view.keys[0].source_frame,view.keys[0].tracking_state),
                         ('grey',61,'pending'))


class ReviewRecoveryBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_scene_binding import SceneBindingTests
        SceneBindingTests.setUpClass();cls.ui=SceneBindingTests.ui
        cls.bridge=importlib.import_module(cls.ui.__package__+'.vta_review_undo')

    def setUp(self):
        import bpy
        from tests.test_blendcap_vta_mutations import project,PY,ENTRY,EngineClient,EngineEndpoint
        self.bpy=bpy;self.bridge.clear()
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name);project(self.root)
        self.path=self.root/'project_state.json';self.before=self.path.read_bytes();self.location=NS(companion_root=self.root)
        self.client=EngineClient(EngineEndpoint(PY,ENTRY))
        self.real_refresh=self.bridge._refresh
        for patch in (mock.patch.object(self.bridge,'_client',return_value=self.client),mock.patch.object(self.bridge,'_refresh'),
                      mock.patch.object(self.ui,'set_operation_feedback')):
            patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for text in list(self.bpy.data.texts):
            if text.get('vta_review_undo') or text.get('vta_reconciliation_pending'):self.bpy.data.texts.remove(text)
        self.bridge.clear()

    def legacy(self,name):
        with self.bridge.transaction(self.bpy.context,self.location) as guard:
            self.assertEqual(guard['expected_document_sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())
            raw=json.loads(self.path.read_text());raw['characters'][0]['display_name']=name;self.path.write_text(json.dumps(raw))
        return self.path.read_bytes()

    def rename(self,name):
        observed=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))
        return self.bridge.rename(self.bpy.context,self.root,observed,name)

    def test_domain_edits_have_no_native_tokens_and_undo_never_calls_engine(self):
        self.rename('B');self.legacy('C');self.rename('D');after=self.path.read_bytes()
        with mock.patch.object(self.client,'call',side_effect=AssertionError('Native Undo called engine')):
            for _ in range(6):self.bridge.native_changed()
        self.assertEqual(self.path.read_bytes(),after)
        self.assertFalse(any(t.get('vta_review_undo') for t in self.bpy.data.texts))
        self.assertFalse((self.root/'.vta-engine/snapshots').exists())

    def test_failed_edit_and_noop_do_not_create_native_token(self):
        with self.assertRaisesRegex(ValueError,'invalid authoritative'):
            with self.bridge.transaction(self.bpy.context,self.location):raise ValueError('invalid authoritative')
        with self.bridge.transaction(self.bpy.context,self.location):pass
        self.assertEqual(self.path.read_bytes(),self.before);self.assertFalse(self.bridge._pending)

    def test_native_presentation_failure_does_not_block_domain_or_touch_keys(self):
        self.rename('B');after=self.path.read_bytes()
        with mock.patch.object(self.bridge,'_refresh',side_effect=RuntimeError('Missing native binding')):
            self.bridge.native_changed()
        self.assertIn('Missing native binding',self.bridge._presentation_warning)
        self.assertFalse(self.bridge._pending);self.bridge.require_ready()
        self.assertEqual(self.path.read_bytes(),after)

    def test_missing_postcommit_capture_retains_pending_then_explicit_retry(self):
        actual=self.client.call;count=0
        def call(operation,*a,**kw):
            nonlocal count
            if operation=='project-state':
                count+=1
                if count==2:raise OSError('capture unavailable')
            return actual(operation,*a,**kw)
        with mock.patch.object(self.client,'call',side_effect=call):self.legacy('Saved')
        self.assertTrue(self.bridge._pending);self.assertTrue((self.root/self.bridge.PENDING_FILE).is_file())
        self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'Saved')
        self.bridge.retry(self.bpy.context);self.assertFalse(self.bridge._pending)

    def test_public_adoption_failure_recovers_applied_receipt(self):
        with mock.patch.object(self.bridge,'_refresh',side_effect=RuntimeError('adoption failed')),self.assertRaises(RuntimeError):self.rename('B')
        self.assertTrue(self.bridge._pending);self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'B')
        self.bridge.retry(self.bpy.context);self.assertFalse(self.bridge._pending)

    def test_oversized_local_intent_does_not_leave_false_pending_state(self):
        with self.assertRaises(ValueError):self.rename('\u540d'*2700)
        self.assertFalse(self.bridge._pending)
        self.assertFalse(any(t.get('vta_reconciliation_pending') for t in self.bpy.data.texts))
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_production_rename_operator_routes_observe_and_mutate_through_engine(self):
        ui=self.ui
        current=NS(characters=(NS(character_id='a',display_name='Alice',occupied_observation_id=None),),candidates=())
        operator=NS(action='rename-character',new_name='',report=mock.Mock())
        context=NS(window_manager=NS(invoke_props_dialog=mock.Mock(return_value={'RUNNING_MODAL'})))
        with mock.patch.object(ui,'_view',current),mock.patch.object(ui,'_location',self.location), \
                mock.patch.object(ui,'_selection',NS(character_id='a')),mock.patch.object(ui,'_use_timeline_active'), \
                mock.patch.object(ui,'refresh',return_value=True),mock.patch.object(ui,'_engine_client',return_value=self.client), \
                mock.patch.object(ui,'_get_backend',side_effect=AssertionError('Legacy rename path used')), \
                mock.patch.object(ui,'_edit_available',return_value=True):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage.invoke(operator,context,None),{'RUNNING_MODAL'})
            self.assertEqual(self.path.read_bytes(),self.before) # Cancel/invoke is read-only.
            operator.new_name='Through engine'
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage.execute(operator,context),{'FINISHED'})
        self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'Through engine')
        self.assertFalse(self.bridge._pending)

    def test_pending_survives_native_undo_and_no_replay_on_retry(self):
        observed=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))
        with mock.patch.object(self.client,'call',side_effect=OSError('unavailable')),self.assertRaises(OSError):
            self.bridge.rename(self.bpy.context,self.root,observed,'B')
        pending=json.loads((self.root/self.bridge.PENDING_FILE).read_text())
        self.bridge.native_changed()
        self.assertEqual(json.loads((self.root/self.bridge.PENDING_FILE).read_text()),pending)
        self.bridge.retry(self.bpy.context);self.assertFalse(self.bridge._pending)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_applied_but_superseded_recovery_is_not_silently_adopted(self):
        with mock.patch.object(self.bridge,'_refresh',side_effect=RuntimeError('refresh failed')),self.assertRaises(RuntimeError):self.rename('B')
        raw=json.loads(self.path.read_text());raw['characters'][0]['display_name']='External';self.path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(RuntimeError,'outside pending'):self.bridge.retry(self.bpy.context)
        self.assertTrue(self.bridge._pending)
        self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'External')

    def test_legacy_unresolved_undo_is_preserved_and_refused_not_replayed(self):
        before=self.client.call('project-state',dict(companion_root=str(self.root)))
        legacy=self.bridge._intent(str(self.root),before,'capture','project-state',dict(companion_root=str(self.root)))
        legacy['desired_snapshot']='f'*64
        with self.assertRaisesRegex(RuntimeError,'prior M2 build'):self.bridge._read_intent(legacy)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_native_membership_change_is_warned_without_repairing_scene(self):
        scene=self.bpy.context.scene
        original={k:scene.get(k) for k in ('vta_project_id','vta_scene_characters')}
        try:
            scene['vta_project_id']='test';scene['vta_scene_characters']='before'
            self.bridge.native_before();scene['vta_scene_characters']='after'
            self.bridge.native_changed()
            self.assertTrue(self.bridge._presentation_warning)
            self.assertEqual(scene['vta_scene_characters'],'after')
            self.assertEqual(self.path.read_bytes(),self.before)
        finally:
            for key,value in original.items():
                if value is None:del scene[key]
                else:scene[key]=value

    def test_failed_refresh_return_is_not_mistaken_for_success(self):
        # Call the real helper, which must notice refresh's handled-error False.
        with mock.patch.object(self.ui,'refresh',return_value=False),self.assertRaises(RuntimeError):
            self.real_refresh()
