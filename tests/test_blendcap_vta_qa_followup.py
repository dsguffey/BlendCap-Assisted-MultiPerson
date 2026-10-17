"""Native collection/font API regressions and operation-local feedback."""
import inspect
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest import mock

import bpy
import blf
import imbuf


class QaFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_core_polish import CorePolishTests
        CorePolishTests.setUpClass()
        cls.ui=CorePolishTests.ui;cls.depth=CorePolishTests.depth;cls.workspace=CorePolishTests.workspace

    def setUp(self):
        self.ui._operation_feedback.clear()
        self.addCleanup(self.ui._operation_feedback.clear)

    def test_native_scene_object_membership_reproduces_and_fixed_target_resolves_identity(self):
        from tests.test_blendcap_vta_source_calibration import BlenderCalibrationTests
        t=BlenderCalibrationTests();t.setUp();self.addCleanup(t.doCleanups);f=t.fixture
        rig=f.rigs[0];scene=bpy.context.scene
        for obj in bpy.context.selected_objects:obj.select_set(False)
        rig.select_set(True);bpy.context.view_layer.objects.active=rig
        with self.assertRaises(TypeError):
            _ = rig in scene.objects  # Actual bpy_prop_collection, not a Python list.
        with mock.patch.object(self.ui,'require_loaded_context'),mock.patch.object(self.ui,'cached_scene_source',return_value=(f.pid,{'source_id':'s'})):
            self.assertEqual(self.depth.depth_target(bpy.context),(rig,scene.camera))
            fake=NS(active_object=rig,scene=NS(objects={}),selected_objects=(rig,))
            with self.assertRaisesRegex(ValueError,'Select one'):
                self.depth.depth_target(fake)
        self.assertIn('execute',vars(self.depth.BLENDCAP_OT_vta_camera_depth))

    def test_native_blf_raster_guidance_is_centered_readable_wrapped_and_clear_of_panels(self):
        lines=('No animation generated yet.','Set Animation Start / End and click Generate Animation Range.')
        for scale,width,height,left,right in ((1,800,600,60,360),(2,1600,1200,120,720)):
            image=imbuf.new((width,height))
            with blf.bind_imbuf(0,image):
                placements=self.workspace.draw_blank_guidance(width,height,lines,scale=scale,left=left,right=right,draw=blf.draw_buffer)
            self.assertGreaterEqual(len(placements),3)
            for text,x,y,measured,size in placements:
                self.assertGreaterEqual(x,left+24*scale-1)
                self.assertLessEqual(x+measured,width-right-24*scale+1)
                self.assertGreater(y,height*.2);self.assertLess(y,height*.8)
                self.assertGreaterEqual(size,15*scale)
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'guidance.png';imbuf.write(image,filepath=str(path))
                raster=bpy.data.images.load(str(path))
                try:
                    pixels=list(raster.pixels)
                    visible=[i//4 for i in range(3,len(pixels),4) if pixels[i]>.1]
                    self.assertGreater(len(visible),100)
                    self.assertGreater(min(i//width for i in visible),height*.2)
                    self.assertLess(max(i//width for i in visible),height*.8)
                finally:bpy.data.images.remove(raster)

    def test_draw_callback_uses_window_region_and_actual_ui_scale_not_header_corner(self):
        context=NS(region=NS(type='WINDOW',width=900,height=650),area=NS(regions=(NS(type='TOOLS',width=60),NS(type='UI',width=250))),
            space_data=NS(show_region_toolbar=True,show_region_ui=True))
        with mock.patch.object(self.workspace.bpy,'context',context),mock.patch.object(self.workspace,'blank_animation_guidance',return_value=('Title','Body')), \
                mock.patch.object(self.workspace,'_metrics',return_value=NS(ui_scale=2)),mock.patch.object(self.workspace,'draw_blank_guidance') as draw:
            self.workspace._draw_blank_animation()
            draw.assert_called_once_with(900,650,('Title','Body'),scale=2,left=60,right=250)
            context.region.type='HEADER';draw.reset_mock();self.workspace._draw_blank_animation();draw.assert_not_called()

    def layout(self,calls):
        class Layout:
            enabled=True
            def label(self,**kw):calls.append(('label',kw['text']))
            def prop(self,owner,field,**kw):calls.append(('prop',field,kw.get('text')))
            def row(self,**kw):return Layout()
            def column(self,**kw):return Layout()
            def operator(self,name,**kw):calls.append(('operator',name,kw['text']));return NS()
        return Layout()

    def test_compact_local_primary_details_and_no_tracking_generation_duplication(self):
        ui=self.ui;context=NS(scene=NS(),window_manager=NS(blendcap_vta_animation_details=False))
        with mock.patch.object(ui,'_reconstruction_log',Path('generation.log')),mock.patch.object(ui,'_generation_job',None):
            ui._generation_failure('Yellow: Interpolate repair 1–19 needs valid unrepaired anchors outside the repair.',context=context)
        calls=[];ui.draw_operation_feedback(self.layout(calls),context,'animation')
        self.assertEqual([c[1] for c in calls if c[0]=='label'],['Couldn’t update the animation.','Your existing animation was kept unchanged.'])
        self.assertNotIn('Yellow',str(calls))
        ui.draw_operation_feedback(self.layout(calls),context,'tracking')
        self.assertEqual(len([c for c in calls if c[0]=='label']),2)
        context.window_manager.blendcap_vta_animation_details=True;calls.clear()
        ui.draw_operation_feedback(self.layout(calls),context,'animation')
        self.assertIn('Yellow: Interpolate repair 1–19',str(calls))
        self.assertIn('Reconstruction did not start',str(calls))
        self.assertIn(('operator','blendcap.vta_generation_details','Go to Log'),calls)
        other=NS(scene=NS(),window_manager=context.window_manager);calls.clear()
        ui.draw_operation_feedback(self.layout(calls),other,'animation');self.assertEqual(calls,[])

    def test_go_to_log_selects_exact_file_not_clipboard(self):
        ui=self.ui
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'validation.log';path.write_text('small diagnostic')
            context=NS(scene=NS(),window_manager=NS(clipboard='unchanged'))
            ui.set_operation_feedback('animation','Failed','Details',context=context,log=path)
            with mock.patch.object(ui.os,'name','nt'),mock.patch.object(ui.subprocess,'Popen') as launch:
                result=ui.BLENDCAP_OT_vta_generation_details.execute(NS(operation='animation',report=mock.Mock()),context)
                self.assertEqual(result,{'FINISHED'})
                launch.assert_called_once_with(['explorer.exe','/select,',str(path.resolve())])
            self.assertEqual(context.window_manager.clipboard,'unchanged')

    def test_missing_or_relative_log_never_launches_documents(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(self.ui.subprocess,'Popen') as launch, \
                mock.patch.object(bpy.ops.wm,'path_open') as open_folder:
            for path in (Path(folder)/'gone.log',Path('relative.log')):
                with self.assertRaisesRegex(ValueError,'available|absolute'):
                    self.ui.open_operation_log(path)
            launch.assert_not_called();open_folder.assert_not_called()

    def test_worker_warning_is_not_reported_as_blocking_exception(self):
        from .test_blendcap_vta_core_polish import CorePolishTests
        import importlib
        generation=importlib.import_module(self.ui.__package__+'.vta_generation')
        result=generation.worker_failure(-1073741819,["Ignored kwargs: {'drop_path': 0.1}"])
        self.assertIn('0xC0000005',result);self.assertIn('not necessarily the cause',result)
        self.assertTrue(result.startswith('Generation worker exited unexpectedly'))
        self.assertEqual(generation.worker_failure(1,['warning','RuntimeError: actual failure']),'RuntimeError: actual failure')

    def test_abrupt_generation_failure_releases_runtime_eligibility_and_keeps_animation(self):
        ui=self.ui
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'worker.log';path.write_text("Ignored kwargs: {'drop_path': 0.1}\n")
            process=NS(poll=lambda:27,returncode=27)
            with mock.patch.object(ui,'_reconstruction_process',process),mock.patch.object(ui,'_generation_job',None), \
                    mock.patch.object(ui,'_reconstruction_log',path),mock.patch.object(ui,'_reconstruction_stop',None), \
                    mock.patch.object(ui,'_reconstruction_scene',None),mock.patch.object(ui,'_reconstruction_location',None), \
                    mock.patch.object(ui,'_analysis_process',None), \
                    mock.patch(ui.__package__+'.vta_skeletal_output.import_reviewed_output') as publish:
                self.assertIsNone(ui._reconstruction_timer())
                self.assertIsNone(ui._reconstruction_process);self.assertIsNone(ui._generation_job)
                publish.assert_not_called()
                self.assertIn('exit code 27',ui._generation_details)
                self.assertIn('unchanged',ui._generation_status)

    @unittest.skipUnless(__import__('os').name=='nt','Legacy empty-lock evaluation is Windows-specific')
    def test_reopened_abandoned_empty_lock_is_not_permanent_ui_busy_state(self):
        ui=self.ui
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'analysis/vta-analysis.lock';path.parent.mkdir();path.write_bytes(b'')
            with mock.patch.object(ui,'_location',NS(companion_root=Path(folder))), \
                    mock.patch.object(ui,'_analysis_process',None),mock.patch.object(ui,'_reconstruction_process',None), \
                    mock.patch(ui.__package__+'.vta_camera_cleanup.busy',return_value=False):
                self.assertFalse(ui._analysis_locked());self.assertTrue(path.exists(),'Poll must be read-only')
                ui._require_analysis_idle();self.assertFalse(path.exists(),'Explicit command recovers it')

    def test_repair_validation_error_local_no_popup_and_preserved(self):
        ui=self.ui;context=NS(scene=NS(),window_manager=NS())
        operator=NS(action='RESIZE',report=mock.Mock())
        with mock.patch.object(ui,'_edit_available',return_value=True),mock.patch.object(ui,'_is_timeline_context',return_value=True), \
                mock.patch.object(ui,'_location_from_context'),mock.patch.object(ui,'timeline_character_selection',return_value=NS(character_ids=('yellow',))), \
                mock.patch.object(ui,'selected_repair_range',return_value=None),mock.patch.object(ui,'_get_backend') as backend:
            self.assertEqual(ui.BLENDCAP_OT_vta_motion_trust.execute(operator,context),{'CANCELLED'})
            operator.report.assert_not_called();backend.assert_not_called()
        item=ui._operation_feedback[ui._feedback_key('repair',context)]
        self.assertEqual(item['primary'],'Couldn’t edit the repair.')
        self.assertIn('Select a repair',item['details']);self.assertIn('unchanged',item['disposition'])

    def test_tracking_no_rows_validation_local_not_report_popup(self):
        ui=self.ui;context=NS(scene=NS(),window_manager=NS());operator=NS(report=mock.Mock())
        with mock.patch.object(ui,'_edit_available',return_value=True),mock.patch.object(ui,'_location_from_context'), \
                mock.patch.object(ui,'tracking_character_ids',return_value=()),mock.patch.object(ui,'_get_backend') as backend:
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator,context),{'CANCELLED'})
            operator.report.assert_not_called();backend.assert_not_called()
        item=ui._operation_feedback[ui._feedback_key('tracking',context)]
        self.assertIn('Select Character rows',item['details'])
        self.assertIn("draw_operation_feedback(layout, context, 'tracking')",inspect.getsource(ui.VIEW3D_PT_blendcap_vta_tracking.draw))
        self.assertNotIn('textwrap.wrap(_status',inspect.getsource(ui.VIEW3D_PT_blendcap_vta_tracking.draw))
