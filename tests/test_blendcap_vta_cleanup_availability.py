"""Read-only cleanup button gates and pre-confirmation validation."""
import importlib,json,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy


class CleanupAvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests
        CameraCleanupTests.setUpClass();cls.base=CameraCleanupTests
        cls.cleanup=CameraCleanupTests.cleanup
        cls.ui=importlib.import_module(cls.cleanup.__package__+'.vta_review_ui')

    def setUp(self):
        self.t=self.base();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.a,self.b=self.t.obj,self.t.other
        self.scene=bpy.context.scene;self.context=bpy.context
        self.info=(self.t.fixture.pid,{'source_id':'s'},None)
        self.camera=self.scene.camera
        for obj in (self.a,self.b):obj['vta_reconstruction_fingerprint']='eligible-fixed'
        self.camera.data['vta_reconstruction_fingerprint']='eligible-fixed'
        modes=mock.patch.object(self.cleanup,'observed_output_mode',return_value='FIRST');modes.start();self.addCleanup(modes.stop)
        for target,name,value in ((self.ui,'_source_actions_available',True),
                (self.ui,'require_loaded_context',None),(self.ui,'cached_scene_source',self.info)):
            patch=mock.patch.object(target,name,return_value=value);patch.start();self.addCleanup(patch.stop)

    def selection(self,ids):
        patch=mock.patch.object(self.ui,'tracking_character_ids',return_value=ids);patch.start();self.addCleanup(patch.stop)

    def test_zero_selection_never_confirms_prepares_or_starts(self):
        self.selection(())
        c=self.cleanup;operator=NS(action='REMOVE',report=mock.Mock())
        context=NS(scene=self.scene,window_manager=NS(invoke_props_dialog=mock.Mock()))
        before=tuple(self.t.fixture.root.rglob('*'))
        with mock.patch.object(c,'prepare_selection') as prepare,mock.patch.object(c.subprocess,'Popen') as worker, \
                mock.patch.object(c,'_finish_feedback'):
            self.assertFalse(c.BLENDCAP_OT_vta_camera_cleanup.poll(context))
            self.assertEqual(c.BLENDCAP_OT_vta_camera_cleanup.invoke(operator,context,None),{'CANCELLED'})
            self.assertEqual(c.BLENDCAP_OT_vta_camera_cleanup.execute(operator,context),{'CANCELLED'})
            context.window_manager.invoke_props_dialog.assert_not_called();prepare.assert_not_called();worker.assert_not_called()
        self.assertEqual(tuple(self.t.fixture.root.rglob('*')),before);self.assertIsNone(c._job)

    def test_one_and_multiple_valid_selected_enable(self):
        for objects in ((self.a,),(self.a,self.b)):
            with mock.patch.object(self.ui,'tracking_character_ids',return_value=tuple(o['vta_character_id'] for o in objects)):
                self.assertTrue(self.cleanup.availability(self.context)[0])
                self.assertTrue(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.poll(self.context))

    def test_stale_finalized_or_missing_rigs_disable_and_restore_is_independent(self):
        self.selection((self.a['vta_character_id'],))
        c=self.cleanup
        self.a['vta_output_stale']=True;self.assertFalse(c.availability(self.context)[0]);self.a['vta_output_stale']=False
        with mock.patch.object(c,'observed_output_mode',return_value='EVERY'):
            self.assertFalse(c.availability(self.context)[0])
        self.a[c.FINAL]=json.dumps(dict(version=1,original_root_sha256=c.action_signature(self.a.animation_data.action,root_only=True)))
        self.a[c.BACKUP]=self.a.animation_data.action
        self.assertFalse(c.availability(self.context)[0]);self.assertTrue(c.availability(self.context,'RESTORE')[0])
        self.a.pop(c.BACKUP);self.assertFalse(c.availability(self.context,'RESTORE')[0])
        with mock.patch.object(self.ui,'tracking_character_ids',return_value=('not-generated',)):
            self.assertFalse(c.availability(self.context)[0])

    def test_one_valid_among_ineligible_or_not_generated_is_enabled(self):
        self.a['vta_output_stale']=True
        self.selection((self.a['vta_character_id'],self.b['vta_character_id'],'not-generated'))
        self.assertTrue(self.cleanup.availability(self.context)[0])

    def test_full_preflight_refuses_before_worker(self):
        self.selection((self.a['vta_character_id'],))
        c=self.cleanup;operator=NS(action='REMOVE',report=mock.Mock())
        context=NS(scene=self.scene,window_manager=NS(invoke_props_dialog=mock.Mock()))
        operator.execute=lambda ctx:c.BLENDCAP_OT_vta_camera_cleanup.execute(operator,ctx)
        with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(c,'prepare_selection',side_effect=ValueError('Review changed')), \
                mock.patch.object(c.subprocess,'Popen') as worker,mock.patch.object(c,'_finish_feedback'):
            self.assertEqual(c.BLENDCAP_OT_vta_camera_cleanup.invoke(operator,context,None),{'CANCELLED'})
            context.window_manager.invoke_props_dialog.assert_not_called();worker.assert_not_called()

    def test_polls_use_no_project_load_or_worker_or_solve_preparation(self):
        self.selection((self.a['vta_character_id'],))
        with mock.patch.object(self.ui,'_get_backend') as backend,mock.patch.object(self.cleanup,'prepare_selection') as prepare:
            for _ in range(20):self.assertTrue(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.poll(self.context))
            backend.assert_not_called();prepare.assert_not_called()

    def test_draw_disables_remove_instead_of_waiting_for_a_click(self):
        calls=[]
        class Layout:
            enabled=True
            def column(self):return Layout()
            def row(self):return Layout()
            def label(self,**_):pass
            def operator(self,name,**kwargs):
                calls.append((name,kwargs.get('text'),self.enabled));return NS()
        with mock.patch.object(self.ui,'draw_operation_feedback'),mock.patch.object(self.ui,'tracking_character_ids',return_value=()):
            self.cleanup.draw_cleanup(Layout(),self.context)
        self.assertIn(('blendcap.vta_camera_cleanup','Remove Camera Movement',False),calls)

    def test_valid_remove_invokes_execute_without_confirmation(self):
        self.selection((self.a['vta_character_id'],self.b['vta_character_id']))
        c=self.cleanup;dialog=mock.Mock(return_value={'RUNNING_MODAL'})
        context=NS(scene=self.scene,window_manager=NS(invoke_props_dialog=dialog));operator=NS(action='REMOVE',report=mock.Mock(),execute=mock.Mock(return_value={'FINISHED'}))
        with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(c,'prepare_selection') as prepare:
            self.assertEqual(c.BLENDCAP_OT_vta_camera_cleanup.invoke(operator,context,None),{'FINISHED'})
            prepare.assert_not_called();dialog.assert_not_called();operator.execute.assert_called_once_with(context)

    def test_observer_metadata_is_cached_and_invalidates_without_poll_io(self):
        c=self.cleanup
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'output.json';obj={'vta_output_file':str(path)}
            path.write_text(json.dumps(dict(reconstruction=dict(mode='FIRST'))))
            with mock.patch.object(c.json,'loads',wraps=json.loads) as parse:
                c.observe_outputs([obj]);c.observe_outputs([obj]);self.assertEqual(parse.call_count,1)
                self.assertEqual(c._output_modes[str(path)][1],'FIRST')
                path.write_text(json.dumps(dict(reconstruction=dict(mode='EVERY'),revision=1)))
                c.observe_outputs([obj]);self.assertEqual(parse.call_count,2)
                self.assertEqual(c._output_modes[str(path)][1],'EVERY')

    def test_restore_without_confirmation_keeps_finalized_animation(self):
        self.selection((self.a['vta_character_id'],))
        c=self.cleanup;solve,_=self.t.solve();c.apply_solve(self.scene,self.a,solve,self.t.core)
        before=self.a.animation_data.action
        with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(c,'_finish_feedback'):
            result=c.BLENDCAP_OT_vta_camera_cleanup.execute(NS(action='RESTORE',report=mock.Mock()),self.context)
        self.assertEqual(result,{'CANCELLED'});self.assertIs(self.a.animation_data.action,before);self.assertTrue(self.a.get(c.FINAL))
