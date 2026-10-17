"""Bounded cleanup/log and selected-generation QA regressions."""
import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy


class RollFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        cls.ui=importlib.import_module(BlenderOutputTests.output.__package__+'.vta_review_ui')
        cls.cleanup=importlib.import_module(BlenderOutputTests.output.__package__+'.vta_camera_cleanup')

    def test_logs_base_uses_existing_companion_without_error_or_source(self):
        ui=self.ui
        with tempfile.TemporaryDirectory(prefix='VTA logs spaces ') as folder:
            blend=Path(folder)/'My project.blend';root=ui.companion_root_for_blend(blend)
            root.mkdir();(root/'project_state.json').write_text('{}')
            with mock.patch.object(ui.bpy,'data',NS(filepath=str(blend))),mock.patch.object(ui.subprocess,'Popen') as launch:
                self.assertEqual(ui.BLENDCAP_OT_vta_open_logs.execute(NS(report=mock.Mock()),NS()),{'FINISHED'})
                launch.assert_called_once_with(['explorer.exe',str(root.resolve())])
                self.assertEqual(sorted(p.name for p in root.iterdir()),['project_state.json'])

    def test_logs_missing_project_never_launches_default_folder(self):
        ui=self.ui
        with mock.patch.object(ui.bpy,'data',NS(filepath='')),mock.patch.object(ui.subprocess,'Popen') as launch:
            operator=NS(report=mock.Mock())
            self.assertEqual(ui.BLENDCAP_OT_vta_open_logs.execute(operator,NS()),{'CANCELLED'})
            launch.assert_not_called();operator.report.assert_called_once()

    def test_cleanup_failure_prominently_reports_failure_not_progress(self):
        c=self.cleanup
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'worker.log';log.write_text('Rotation 1/112\nRotation 111/112\nRotation cleanup failed: missing reference evidence\n')
            job=dict(process=NS(poll=lambda:1,returncode=1),cancelled=False,log=log,scene=None)
            with mock.patch.object(c,'_job',job),mock.patch.object(c,'_finish_feedback') as feedback:
                self.assertIsNone(c.poll_job())
                self.assertIn('missing reference evidence',feedback.call_args.args[1])
                self.assertNotIn('Rotation 1/112',feedback.call_args.args[1])

    def test_root_position_rotation_uses_camera_origin_not_pelvis_or_world_origin(self):
        from tests.test_blendcap_vta_camera_cleanup import CameraCleanupTests
        from mathutils import Matrix,Vector
        t=CameraCleanupTests();t.setUpClass();t.setUp();self.addCleanup(t.doCleanups)
        solve,truth=t.solve((0,0,.14),moving=False)
        camera_origin=Vector((8,-11,3));user=Matrix.Translation((.4,.7,-.2))
        t.obj.matrix_world=user @ Matrix.Translation(camera_origin)
        before=t.nonroot(t.obj);camera=t.scene.camera.matrix_world.copy()
        t.cleanup.apply_solve(t.scene,t.obj,solve,t.core)
        for frame,pose in truth.items():
            t.scene.frame_set(frame);t.obj.hide_viewport=False;t.obj.hide_set(False);bpy.context.view_layer.update()
            expected=user @ Matrix.Translation(camera_origin) @ pose
            actual=t.obj.matrix_world @ t.root.matrix
            self.assertLess(max(abs(a-b) for ar,br in zip(actual,expected) for a,b in zip(ar,br)),2e-5)
        self.assertEqual(t.nonroot(t.obj),before);self.assertEqual(t.scene.camera.matrix_world,camera)

    def test_zero_selection_blocks_before_plan_or_worker_and_has_clear_guidance(self):
        ui=self.ui
        with tempfile.TemporaryDirectory() as folder:
            wm=NS(blendcap_vta_animation_start=1,blendcap_vta_animation_end=5)
            context=NS(scene=NS(),window_manager=wm)
            backend=NS(_load_context=lambda _: (None,NS(source=NS(start_frame=0,end_frame=10)),'r'))
            with mock.patch.object(ui,'_source_actions_available',return_value=True), \
                    mock.patch.object(ui,'tracking_character_ids',return_value=()), \
                    mock.patch.object(ui,'_reconstruction_process',None),mock.patch.object(ui,'_edit_available',return_value=True), \
                    mock.patch.object(ui,'_location_from_context',return_value=NS(companion_root=Path(folder),blender_frame=1,source_frame=0)), \
                    mock.patch.object(ui,'_get_backend',return_value=backend),mock.patch.object(ui.subprocess,'Popen') as launch:
                self.assertFalse(ui.BLENDCAP_OT_vta_generate_animation.poll(context))
                self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(NS(action='RANGE'),context),{'CANCELLED'})
                launch.assert_not_called();self.assertIn('Select at least one Character.',ui._generation_details)

    def test_calibration_update_never_expands_selected_character_scope(self):
        from importlib import import_module
        generation=import_module(self.ui.__package__+'.vta_generation')
        a=bpy.data.objects.new('selected-generation-owner',bpy.data.armatures.new('selected-generation-data'))
        b=bpy.data.objects.new('unselected-generation-owner',bpy.data.armatures.new('unselected-generation-data'))
        for o,cid in ((a,'a'),(b,'b')):
            bpy.context.scene.collection.objects.link(o)
            o['vta_project_id']='scope';o['vta_source_id']='source';o['vta_character_id']=cid
            o['vta_reconstruction_fingerprint']='old'
        self.addCleanup(lambda: [(bpy.data.objects.remove(o,do_unlink=True),bpy.data.armatures.remove(d)) for o,d in ((a,a.data),(b,b.data))])
        plan=dict(project_id='scope',source_id='source',character_scope=['a'])
        with self.assertRaisesRegex(ValueError,'Select all existing Characters'):
            generation.migration_range(bpy.context.scene,plan,'new',(0,10),1)
        self.assertIsNone(generation.migration_range(bpy.context.scene,plan,'old',(0,10),1))

    def test_generation_scope_dropdown_removed_and_multi_ids_are_not_collapsed(self):
        import inspect
        ui=self.ui;text=inspect.getsource(ui.BLENDCAP_OT_vta_generate_animation.execute)
        self.assertIn("options['character_ids']=ids",text)
        self.assertNotIn('blendcap_vta_generation_scope',inspect.getsource(ui))

    def test_foreign_scene_character_is_rejected_before_plan_or_worker(self):
        ui=self.ui;session=importlib.import_module(ui.__package__+'.vta_scene_session')
        scope=importlib.import_module(ui.__package__+'.vta_scene_characters')
        with tempfile.TemporaryDirectory() as folder:
            context=NS(scene=bpy.context.scene,window_manager=NS(blendcap_vta_animation_start=1,blendcap_vta_animation_end=5))
            build=mock.Mock();backend=NS(_load_context=lambda _: (None,NS(source=NS(start_frame=0,end_frame=10)),'r'),_core=NS(build_skeletal_plan=build))
            with mock.patch.object(ui,'tracking_character_ids',return_value=('other-scene-character',)), \
                    mock.patch.object(ui,'_reconstruction_process',None),mock.patch.object(ui,'_edit_available',return_value=True), \
                    mock.patch.object(ui,'_location_from_context',return_value=NS(companion_root=Path(folder),blender_frame=1,source_frame=0)), \
                    mock.patch.object(ui,'_get_backend',return_value=backend),mock.patch.object(ui.subprocess,'Popen') as launch, \
                    mock.patch.object(session,'owned',return_value=True),mock.patch.object(scope,'allowed',return_value={'this-scene-character'}):
                self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(NS(action='RANGE'),context),{'CANCELLED'})
                build.assert_not_called();launch.assert_not_called()
