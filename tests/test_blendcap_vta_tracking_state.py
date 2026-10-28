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


class ReviewUndoBridgeTests(unittest.TestCase):
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
        return self.bridge._entries[str(self.root.resolve())]

    def rename(self,name):
        observed=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))
        return self.bridge.rename(self.bpy.context,self.root,observed,name)

    def desired(self,snapshot):
        entry=self.bridge._entries[str(self.root.resolve())];token=self.bridge._text(entry);token.clear();token.write(snapshot)
        self.bridge.restore()

    def test_tokens_contain_no_project_document_and_restore_undo_redo(self):
        entry=self.legacy('Changed');after=self.path.read_bytes();token=self.bridge._text(entry)
        identity=token.as_string();self.assertEqual(len(identity),64);self.assertNotIn('Changed',identity)
        self.desired(entry['base']['snapshot_id']);self.assertEqual(self.path.read_bytes(),self.before)
        self.desired(identity);self.assertEqual(self.path.read_bytes(),after)
        self.assertNotIn('states',entry) # Host no longer owns document snapshots.

    def test_failed_edit_and_noop_do_not_create_native_token(self):
        with self.assertRaisesRegex(ValueError,'invalid authoritative'):
            with self.bridge.transaction(self.bpy.context,self.location):raise ValueError('invalid authoritative')
        with self.bridge.transaction(self.bpy.context,self.location):pass
        entry=self.bridge._entries[str(self.root.resolve())]
        self.assertIsNone(self.bridge._text(entry));self.assertEqual(self.path.read_bytes(),self.before);self.assertFalse(self.bridge._pending)

    def test_external_write_fails_closed_on_restore(self):
        entry=self.legacy('Changed');raw=json.loads(self.path.read_text());raw['characters'][0]['display_name']='External'
        self.path.write_text(json.dumps(raw));external=self.path.read_bytes()
        self.desired(entry['base']['snapshot_id']);self.assertEqual(self.path.read_bytes(),external)
        self.assertTrue(self.bridge._pending)
        with self.assertRaises(RuntimeError):self.bridge.require_ready()

    def test_fresh_edit_after_external_write_starts_a_new_undo_epoch(self):
        self.rename('B');old=self.bridge._entries[str(self.root.resolve())]
        raw=json.loads(self.path.read_text());raw['characters'][0]['display_name']='External'
        self.path.write_text(json.dumps(raw));external=self.path.read_bytes()
        self.rename('D');current=self.bridge._entries[str(self.root.resolve())]
        self.assertNotEqual(old['epoch'],current['epoch'])
        self.bpy.data.texts.remove(self.bridge._text(current)) # Native Undo past new epoch.
        self.assertIsNotNone(self.bridge._text(old))
        self.bridge.restore();self.assertEqual(self.path.read_bytes(),external)
        self.bridge.restore();self.assertEqual(self.path.read_bytes(),external)
        self.assertFalse(self.bridge._pending)

    def test_old_snapshot_not_replayed_after_session_clear(self):
        self.legacy('Changed');after=self.path.read_bytes();self.bridge.clear()
        with mock.patch.object(self.client,'call') as call:self.bridge.restore()
        call.assert_not_called();self.assertEqual(self.path.read_bytes(),after)

    def test_missing_postcommit_capture_retains_pending_then_explicit_retry(self):
        actual=self.client.call;count=0
        def call(operation,*a,**kw):
            nonlocal count
            if operation=='review-checkpoint':
                count+=1
                if count==2:raise OSError('capture unavailable')
            return actual(operation,*a,**kw)
        with mock.patch.object(self.client,'call',side_effect=call):self.legacy('Saved')
        self.assertTrue(self.bridge._pending);self.assertTrue((self.root/self.bridge.PENDING_FILE).is_file())
        self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'Saved')
        self.bridge.retry(self.bpy.context);self.assertFalse(self.bridge._pending)

    def test_unknown_snapshot_never_overwrites_project(self):
        entry=self.legacy('Changed');after=self.path.read_bytes();self.desired('f'*64)
        self.assertEqual(self.path.read_bytes(),after);self.assertTrue(self.bridge._pending)

    def test_interleaved_rename_legacy_edit_rename_undo_redo(self):
        first=self.rename('B');entry=self.legacy('C');middle=entry['current']['snapshot_id'];last=self.rename('D')
        for desired,name in [(middle,'C'),(first['after']['snapshot_id'],'B'),(first['before']['snapshot_id'],'Alice'),
                             (first['after']['snapshot_id'],'B'),(middle,'C'),(last['after']['snapshot_id'],'D')]:
            self.desired(desired);self.assertFalse(self.bridge._pending)
            self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],name)

    def test_public_adoption_failure_recovers_applied_receipt(self):
        with mock.patch.object(self.bridge,'_adopt',side_effect=RuntimeError('adoption failed')),self.assertRaises(RuntimeError):self.rename('B')
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

    def test_latest_native_undo_intent_preserved_while_engine_unavailable(self):
        first=self.rename('B');last=self.rename('C')
        with mock.patch.object(self.client,'call',side_effect=OSError('unavailable')):
            self.desired(first['after']['snapshot_id']);self.desired(first['before']['snapshot_id'])
        self.assertTrue(self.bridge._pending);self.bridge.retry(self.bpy.context)
        self.assertEqual(json.loads(self.path.read_text())['characters'][0]['display_name'],'Alice')
        self.assertFalse(self.bridge._pending)
