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
        SceneBindingTests.setUpClass()
        cls.ui=SceneBindingTests.ui
        cls.bridge=importlib.import_module(cls.ui.__package__+'.vta_review_undo')

    def setUp(self):
        import bpy
        self.bpy=bpy;self.bridge.clear()
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.path=self.root/'project_state.json'
        self.location=NS(companion_root=self.root)
        self.before=json.dumps({'project_id':'test','keys':[]}).encode()
        self.after=json.dumps({'project_id':'test','keys':['pending']}).encode()
        self.path.write_bytes(self.before)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for text in list(self.bpy.data.texts):
            if text.get('vta_review_undo')==1:self.bpy.data.texts.remove(text)
        self.bridge.clear()

    def edit(self):
        with self.bridge.transaction(self.bpy.context,self.location) as guard:
            self.assertEqual(guard['expected_document_sha256'],hashlib.sha256(self.before).hexdigest())
            self.path.write_bytes(self.after)
        return self.bridge._entries[str(self.root.resolve())]

    def test_tokens_contain_no_project_document_and_restore_undo_redo(self):
        entry=self.edit();token=self.bridge._text(entry);after_token=token.as_string()
        self.assertNotIn('pending',after_token);self.assertEqual(len(after_token),32)
        def restore(root,expected,snapshot):
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),expected)
            self.path.write_text(snapshot,encoding='utf-8')
        backend=NS(restore_review_snapshot=mock.Mock(side_effect=restore))
        with mock.patch.object(self.ui,'_get_backend',return_value=backend),\
                mock.patch.object(self.ui,'refresh'),mock.patch.object(self.ui.vta_invalidation,'request_redraw'):
            token.clear();token.write('base');self.bridge.restore()
            self.assertEqual(self.path.read_bytes(),self.before)
            token.clear();token.write(after_token);self.bridge.restore()
            self.assertEqual(self.path.read_bytes(),self.after)
            self.assertEqual(backend.restore_review_snapshot.call_count,2)

    def test_failed_edit_and_noop_do_not_create_native_token(self):
        with self.assertRaisesRegex(ValueError,'invalid authoritative'):
            with self.bridge.transaction(self.bpy.context,self.location):
                raise ValueError('invalid authoritative conflict')
        with self.bridge.transaction(self.bpy.context,self.location):pass
        entry=self.bridge._entries[str(self.root.resolve())]
        self.assertIsNone(self.bridge._text(entry));self.assertEqual(self.path.read_bytes(),self.before)

    def test_external_write_fails_closed_on_restore(self):
        entry=self.edit();token=self.bridge._text(entry);token.clear();token.write('base')
        external=b'{"project_id":"test","keys":["external"]}';self.path.write_bytes(external)
        backend=NS(restore_review_snapshot=mock.Mock(side_effect=ValueError('Project changed')))
        with mock.patch.object(self.ui,'_get_backend',return_value=backend),\
                mock.patch.object(self.ui,'set_operation_feedback') as feedback:
            self.bridge.restore()
        self.assertEqual(self.path.read_bytes(),external);feedback.assert_called_once()

    def test_old_snapshot_not_replayed_after_session_clear(self):
        entry=self.edit();self.bridge.clear()
        with mock.patch.object(self.ui,'_get_backend') as backend:self.bridge.restore()
        backend.assert_not_called();self.assertEqual(self.path.read_bytes(),self.after)

    def test_missing_postcommit_capture_does_not_report_edit_failure(self):
        real_read=Path.read_bytes
        def read(path):
            if path==self.path and path.read_text().find('pending')>=0:raise OSError('unavailable')
            return real_read(path)
        with mock.patch.object(Path,'read_bytes',read):self.edit_without_guard_assertion()
        self.assertEqual(self.path.read_bytes(),self.after)
        self.assertFalse(self.bridge._entries);self.assertIn('saved',self.bridge._error)

    def edit_without_guard_assertion(self):
        with self.bridge.transaction(self.bpy.context,self.location):self.path.write_bytes(self.after)

    def test_capture_memory_bounded_and_evicted_token_never_overwrites(self):
        with mock.patch.object(self.bridge,'MAX_SNAPSHOTS',3):
            self.edit()
            entry=self.bridge._entries[str(self.root.resolve())];old=entry['current']
            for i in range(6):
                with self.bridge.transaction(self.bpy.context,self.location):
                    self.path.write_text(json.dumps({'project_id':'test','keys':[i]}))
            self.assertEqual(len(entry['states']),3);self.assertNotIn(old,entry['states'])
            token=self.bridge._text(entry);token.clear();token.write(old)
            with mock.patch.object(self.ui,'_get_backend') as backend,\
                    mock.patch.object(self.ui,'set_operation_feedback'):
                self.bridge.restore()
            backend.assert_not_called()
