"""Core polish: real Blender transforms/imports and bounded presentation routing."""
import copy
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest import mock

import bpy
from mathutils import Matrix, Vector
import blendcap_vta_generation_ipc as ipc
from blendcap_vta_review import ReviewLocation
from blendcap_vta_timeline import TimelineMotionInterval


class CorePolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        name = BlenderOutputTests.addon.__name__
        cls.ui = importlib.import_module(name + '.blendcap.vta_review_ui')
        cls.workspace = importlib.import_module(name + '.blendcap.vta_workspace')
        cls.depth = importlib.import_module(name + '.blendcap.vta_camera_depth')
        cls.output = BlenderOutputTests.output

    def invalid_plan(self):
        return {'project_id':'p','source_id':'s','characters':[{'character_id':'yellow','display_name':'Yellow',
            'samples':[dict(frame=78,mode='infer',repair_mode='interpolate',continuity=0)],
            'repair_context':[dict(frame=77,mode='infer',repair_mode=None,continuity=0)]}]}

    def test_host_invalid_repair_fails_before_runtime_checks_or_launch_names_range(self):
        import vta_core
        ui = self.ui
        project = NS(repair_intervals=lambda *_: (NS(mode='interpolate',start_frame=78,end_frame=89),))
        cache = NS(source=NS(start_frame=0,end_frame=162))
        backend = NS(_load_context=lambda _: (project,cache,'r'), _core=NS(
            build_skeletal_plan=lambda *_a,**_kw:self.invalid_plan(), validate_skeletal_repairs=vta_core.validate_skeletal_repairs))
        context = NS(window_manager=NS(blendcap_vta_animation_start=79,blendcap_vta_animation_end=90),scene=NS())
        operator = NS(action='RANGE',report=mock.Mock())
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(ui,'_location_from_context',return_value=ReviewLocation(Path(folder),Path('video.mp4'),0,1)), \
                mock.patch.object(ui,'_get_backend',return_value=backend), mock.patch.object(ui,'_edit_available',return_value=True), \
                mock.patch.object(ui,'_reconstruction_process',None), mock.patch.object(ui,'_python_exe') as python, \
                mock.patch.object(ui.subprocess,'Popen') as launch:
            self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(operator,context),{'CANCELLED'})
            launch.assert_not_called(); python.assert_not_called()
            self.assertIn('Yellow: Interpolate repair 79–90',ui._generation_details)
            self.assertIn('outside the repair range',ui._generation_details)
            self.assertNotIn('Yellow',ui._status)
            operator.report.assert_not_called()

    def test_worker_invalid_repair_fails_before_estimator_and_writes_no_run(self):
        import vta_core
        import blendcap_vta_reconstruct_worker as worker
        import vta_core.source_preflight as preflight
        project = NS(to_dict=lambda:dict(active_analysis_revision_by_source={'s':'r'}),
            repair_intervals=lambda *_:(NS(mode='interpolate',start_frame=78,end_frame=89),))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = NS(core_root=str(Path(vta_core.__file__).parent.parent),blendcap_root=str(root),
                companion_root=str(root),video=str(root/'video.mp4'),start_frame=78,end_frame=89)
            estimator = mock.Mock()
            with mock.patch.object(vta_core,'load_project_state',return_value=project), \
                    mock.patch.object(vta_core,'load_analysis_cache'), mock.patch.object(vta_core,'build_skeletal_plan',return_value=self.invalid_plan()), \
                    mock.patch('blendcap_vta_analyze_worker._source_for_video',return_value=NS(source_id='s')), \
                    mock.patch.object(preflight,'preflight_source'), mock.patch.dict('sys.modules',{'save_mhr_data':NS(load_estimator_fast=estimator)}):
                with self.assertRaisesRegex(vta_core.StateModelError,'Yellow: Interpolate repair 79–90'):
                    worker.run(args)
            estimator.assert_not_called()
            self.assertFalse((root/'reconstruction').exists())
            self.assertFalse((root/'analysis/vta-analysis.lock').exists())

    def test_selected_launch_passes_exact_ids_and_all_default_stays_available(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as folder:
            location=ReviewLocation(Path(folder),Path(folder)/'video.mp4',0,1)
            wm=NS(blendcap_vta_animation_start=20,blendcap_vta_animation_end=21,blendcap_vta_generation_scope='SELECTED')
            context=NS(window_manager=wm,scene=NS(objects=[],blendcap_props=NS(fov_mode='FIRST')))
            build=mock.Mock(return_value=dict(project_id='p',source_id='s'))
            backend=NS(_load_context=lambda _:(None,NS(source=NS(start_frame=0,end_frame=162)),'r'),
                _core=NS(build_skeletal_plan=build,validate_skeletal_repairs=mock.Mock()))
            with mock.patch.object(ui,'_location_from_context',return_value=location), mock.patch.object(ui,'_get_backend',return_value=backend), \
                    mock.patch.object(ui,'tracking_character_ids',return_value=('white',)), mock.patch.object(ui,'require_loaded_context'), \
                    mock.patch.object(ui,'_edit_available',return_value=True), mock.patch.object(ui,'_python_exe',return_value='python'), \
                    mock.patch.object(Path,'is_file',return_value=True), mock.patch.object(ui,'_reconstruction_process',None), \
                    mock.patch.object(ui,'_backend_root',Path('private')), mock.patch.object(bpy.app.timers,'register'), \
                    mock.patch.object(ui.subprocess,'Popen',return_value=NS(poll=lambda:None)) as launch:
                self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(NS(action='RANGE',report=mock.Mock()),context),{'FINISHED'})
                build.assert_called_once_with(None,mock.ANY,frame_range=(19,20),character_ids=('white',))
                command=launch.call_args.args[0]
                self.assertEqual(command[command.index('--character-id')+1],'white')
                self.assertNotIn('--decision-file',command)
                # Pretend the already-asserted fake request was never launched.
                ui._reconstruction_process=None; ui._generation_job=None
                wm.blendcap_vta_generation_scope='ALL'; build.reset_mock()
                self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(NS(action='RANGE',report=mock.Mock()),context),{'FINISHED'})
                self.assertNotIn('character_ids',build.call_args.kwargs)
                self.assertNotIn('--character-id',launch.call_args.args[0])
                ui._reconstruction_process=None; ui._generation_job=None

    def test_selected_import_preserves_unrelated_action_curves_visibility_and_transform(self):
        from tests.test_blendcap_vta_source_calibration import BlenderCalibrationTests
        t=BlenderCalibrationTests(); t.setUp(); self.addCleanup(t.doCleanups); f=t.fixture
        path,_=t.calibrated_report(False); self.output.import_reviewed_output(path,f.location,f.backend)
        other=f.rigs[1]; other.location=(.7,.2,.1)
        other.keyframe_insert('location',frame=10); other.show_in_front=False
        before=(other.animation_data.action, f.curves(other), other.matrix_basis.copy(),other.show_in_front,dict(other.items()))
        path,_=t.calibrated_report(True)
        report=json.loads(path.read_text()); report['plan']['characters']=report['plan']['characters'][:1]
        report['plan']['character_scope']=['left']; report['characters']=report['characters'][:1]
        f.plan=report['plan']; path.write_text(json.dumps(report))
        rebuilt=mock.Mock(return_value=f.plan); f.backend._core.build_skeletal_plan=rebuilt
        rigs,_=self.output.import_reviewed_output(path,f.location,f.backend)
        self.assertEqual([r['vta_character_id'] for r in rigs],['left'])
        self.assertEqual(rebuilt.call_args.kwargs['character_ids'],['left'])
        self.assertEqual(before,(other.animation_data.action,f.curves(other),other.matrix_basis.copy(),other.show_in_front,dict(other.items())))

    def repair_edit(self, action):
        ui=self.ui; interval=TimelineMotionInterval('yellow',78,89,'interpolate')
        location=ReviewLocation(Path('project'),Path('video.mp4'),0,1)
        wm=NS(blendcap_vta_motion_start=80,blendcap_vta_motion_end=89)
        context=NS(window_manager=wm)
        timeline=NS(repair_ranges=[])
        mode='freeze' if action=='FREEZE' else 'interpolate'
        expected=TimelineMotionInterval('yellow',79 if action=='RESIZE' else 78,88 if action=='RESIZE' else 89,mode)
        backend=NS(set_motion_trust_for_characters=mock.Mock(side_effect=lambda *_a,**_kw:timeline.repair_ranges.append(expected)))
        with mock.patch.object(ui,'_edit_available',return_value=True), mock.patch.object(ui,'_is_timeline_context',return_value=True), \
                mock.patch.object(ui,'_location_from_context',return_value=location),mock.patch.object(ui,'_location',location), \
                mock.patch.object(ui,'timeline_character_selection',return_value=NS(character_ids=('yellow','white'))), \
                mock.patch.object(ui,'selected_repair_range',return_value=interval), mock.patch.object(ui,'refresh',return_value=True), \
                mock.patch.object(ui,'_timeline',timeline),mock.patch.object(ui,'_get_backend',return_value=backend), \
                mock.patch.object(ui,'select_repair_range') as select:
            self.assertEqual(ui.BLENDCAP_OT_vta_motion_trust.execute(NS(action=action,report=mock.Mock()),context),{'FINISHED'})
            call=backend.set_motion_trust_for_characters.call_args
            self.assertEqual(call.args[1],('yellow',))
            self.assertEqual(call.kwargs['action'],'resize' if action=='RESIZE' else 'change-mode')
            self.assertEqual(call.kwargs['mode'],mode)
            self.assertEqual(call.args[2:4],(80,89) if action=='RESIZE' else (79,90))
            if action=='RESIZE':self.assertEqual(call.kwargs['previous_range'],(78,89))
            select.assert_called_once_with(context,expected)

    def test_resize_routes_exact_selected_range_and_reselects_new_endpoints(self):self.repair_edit('RESIZE')
    def test_buttons_convert_selected_range_without_creating_or_affecting_other_rows(self):
        self.repair_edit('FREEZE'); self.repair_edit('INTERPOLATE')

    def test_selected_repair_text_no_dropdown_and_active_type_disabled(self):
        ui=self.ui; context=NS(window_manager=NS())
        interval=TimelineMotionInterval('yellow',78,89,'interpolate')
        labels=[]; props=[]; buttons=[]
        class Layout:
            enabled=True
            def row(self,**_):return Layout()
            def column(self):return Layout()
            def label(self,**kwargs):labels.append(kwargs['text'])
            def prop(self,owner,key,**kwargs):props.append(key)
            def operator(self,name,**kwargs):
                result=NS();buttons.append((kwargs['text'],self,result));return result
        with mock.patch.object(ui,'selected_repair_range',return_value=interval),mock.patch.object(ui,'_location',NS(blender_frame=1,source_frame=0)), \
                mock.patch.object(ui,'_analysis_locked',return_value=False),mock.patch.object(ui,'_is_timeline_context',return_value=True), \
                mock.patch.object(ui,'timeline_character_selection',return_value=NS(character_ids=('yellow',))),mock.patch.object(ui,'_view',None), \
                mock.patch.object(ui,'_timeline',None), mock.patch.object(ui,'current_character_id',return_value='yellow'):
            ui.draw_motion_trust(Layout(),context)
        self.assertIn('Selected repair: Interpolate, frames 79–90',labels)
        self.assertNotIn('blendcap_vta_repair_mode',props)
        self.assertIn('blendcap_vta_motion_start',props);self.assertIn('blendcap_vta_motion_end',props)
        states={label:layout.enabled for label,layout,_ in buttons}
        self.assertFalse(states['Interpolate']); self.assertTrue(states['Freeze']);self.assertTrue(states['Apply Start / End'])

    def test_blank_guidance_scoped_no_objects_or_disk_writes(self):
        workspace={self.workspace.WORKSPACE_TAG:1}; scene=NS(objects=[])
        context=NS(area=NS(type='VIEW_3D'),workspace=workspace,scene=scene)
        with mock.patch.object(self.ui,'loaded_context_matches',return_value=True), \
                mock.patch.object(self.ui,'cached_scene_source',return_value=('p',{'source_id':'s'})):
            self.assertIn('No animation generated yet.',self.workspace.blank_animation_guidance(context))
            self.assertEqual(scene.objects,[])
            scene.objects=[NS(type='ARMATURE',get=lambda key:{'vta_project_id':'p','vta_source_id':'s'}.get(key))]
            self.assertEqual(self.workspace.blank_animation_guidance(context),())
            scene.objects=[];context.workspace={}
            self.assertEqual(self.workspace.blank_animation_guidance(context),())

    def test_first_snap_only_first_success_and_same_context(self):
        workspace={self.workspace.WORKSPACE_TAG:1};scene=NS(frame_current=22)
        context=NS(scene=scene,workspace=workspace,screen=NS(areas=[]))
        with mock.patch.object(self.workspace,'configure_source_camera_views',return_value=1) as snap:
            self.assertEqual(self.workspace.snap_first_output(context,scene,True,workspace),1)
            self.assertEqual(self.workspace.snap_first_output(context,scene,False,workspace),0)
            self.assertEqual(self.workspace.snap_first_output(context,NS(),True,workspace),0)
            self.assertEqual(self.workspace.snap_first_output(context,scene,True,{}),0)
            snap.assert_called_once_with(scene,(workspace,))
        self.assertEqual(scene.frame_current,22)
        self.assertIn('snap_first_output',inspect.getsource(self.ui._reconstruction_timer))

    def test_depth_math_arbitrary_camera_and_parent_only_translation(self):
        depth=self.depth
        camera=NS(matrix_world=Matrix.Rotation(.78,4,'X') @ Matrix.Rotation(1.12,4,'Z'))
        for parent in (None,NS(matrix_world=Matrix.Translation((9,2,3)))):
            obj=NS(parent=parent,matrix_parent_inverse=Matrix.Identity(4))
            local=depth.local_depth_axis(obj,camera)
            world=local if parent is None else parent.matrix_world.to_3x3() @ local
            self.assertLess((world-depth.depth_axis(camera)).length,1e-6)
            # Viewport rotation changes mouse projection, never the constrained world axis.
            for screen_axis in ((25,40),(-70,10),(0,-50)):
                amount=depth.mouse_depth_delta(Vector(screen_axis)*.3,screen_axis)
                self.assertAlmostEqual(amount,.3,places=6)
                self.assertLess((world*amount).cross(depth.depth_axis(camera)).length,1e-6)

    def test_depth_perpendicular_fine_and_degenerate(self):
        self.assertAlmostEqual(self.depth.mouse_depth_delta((-40,25),(25,40)),0)
        normal=self.depth.mouse_depth_delta((25,40),(25,40))
        self.assertAlmostEqual(self.depth.mouse_depth_delta((25,40),(25,40),fine=True),normal*.1)
        for axis in ((0,0),(float('nan'),1)):
            with self.assertRaisesRegex(ValueError,'Orbit'):
                self.depth.mouse_depth_delta((10,10),axis)

    def test_depth_modal_cancel_restores_object_and_confirm_changes_no_curves_or_camera(self):
        from tests.test_blendcap_vta_source_calibration import BlenderCalibrationTests
        t=BlenderCalibrationTests(); t.setUp(); self.addCleanup(t.doCleanups); f=t.fixture
        rig=f.rigs[0]; camera=bpy.context.scene.camera
        origin=rig.location.copy(); rot=rig.rotation_euler.copy(); scale=rig.scale.copy()
        curves=f.curves(rig);cam_matrix=camera.matrix_world.copy()
        context=NS(scene=bpy.context.scene,area=NS(header_text_set=mock.Mock(),tag_redraw=mock.Mock()))
        operator=NS(_obj=rig,_origin=origin,_scene=context.scene,_axis=self.depth.local_depth_axis(rig,camera),
            _screen_axis=Vector((20,30)),_mouse=Vector((0,0)),_distance=0.,cancel=lambda ctx:self.depth.BLENDCAP_OT_vta_camera_depth.cancel(operator,ctx))
        operator._end_display=lambda ctx:self.depth.BLENDCAP_OT_vta_camera_depth._end_display(operator,ctx)
        with mock.patch.object(self.ui,'loaded_context_matches',return_value=True),mock.patch.object(self.ui,'_analysis_locked',return_value=False):
            self.assertEqual(self.depth.BLENDCAP_OT_vta_camera_depth.modal(operator,context,NS(type='MOUSEMOVE',mouse_x=20,mouse_y=30,shift=True)),{'RUNNING_MODAL'})
            self.assertAlmostEqual((rig.location-origin).length,.1,places=6)
            self.assertEqual(self.depth.BLENDCAP_OT_vta_camera_depth.modal(operator,context,NS(type='ESC')) ,{'CANCELLED'})
            self.assertEqual(rig.location,origin)
            self.depth.BLENDCAP_OT_vta_camera_depth.modal(operator,context,NS(type='MOUSEMOVE',mouse_x=40,mouse_y=60,shift=False))
            self.assertEqual(self.depth.BLENDCAP_OT_vta_camera_depth.modal(operator,context,NS(type='RET',value='PRESS')),{'FINISHED'})
        self.assertEqual((rig.rotation_euler,rig.scale),(rot,scale))
        self.assertEqual(f.curves(rig),curves);self.assertEqual(camera.matrix_world,cam_matrix)
        self.assertIn('UNDO',self.depth.BLENDCAP_OT_vta_camera_depth.bl_options)

    def test_windows_early_path_budget_compact_run_and_deep_ipc_independent(self):
        ipc.preflight_generation_paths(Path('short'),windows=True)
        with self.assertRaisesRegex(ValueError,'shorter folder'):
            ipc.preflight_generation_paths(Path('x'*230),windows=True)
        with tempfile.TemporaryDirectory() as folder:
            path=ipc.new_output_directory(folder)
            self.assertEqual(len(path.name),18)
            self.assertEqual(path.parent.name,'reconstruction')
        self.assertIn('Ignore this candidate during automatic continuation',self.ui.BLENDCAP_OT_vta_suppress_candidate.bl_description)
        generation=importlib.import_module(self.ui.__package__+'.vta_generation')
        self.assertIn('shorter folder',generation.failure_message('Project path is too long. Save in a shorter folder.'))
        self.assertIn('Select Character rows',generation.failure_message('Select Character rows or choose All Reviewed.'))

    def test_timeline_tooltip_hits_review_repairs_rows_and_derived_not_generated_poses(self):
        w=self.workspace; context=NS(region=NS(type='WINDOW',height=200,width=800,view2d=NS(
            view_to_region=lambda x,y,clip=False:(x*10,y))))
        view=NS(derived=())
        with mock.patch.object(w,'_state',return_value=(view,(),None,1,0,None)), mock.patch.object(w,'_metrics',return_value=NS(ui_scale=1)), \
                mock.patch.object(w,'hit_review_key',return_value='hard'),mock.patch.object(w,'hit_motion_interval'):
            self.assertIn('Hard review key',w.timeline_tooltip(context,(30,100)))
        for mode in ('freeze','interpolate'):
            with mock.patch.object(w,'_state',return_value=(view,(),None,1,0,None)),mock.patch.object(w,'_metrics'), \
                    mock.patch.object(w,'hit_review_key',return_value=None),mock.patch.object(w,'hit_motion_interval',return_value=NS(mode=mode)):
                text=w.timeline_tooltip(context,(30,100))
                self.assertIn(mode.title()+' repair',text)
                if mode=='interpolate':self.assertIn('outside both endpoints',text)
        context.region.type='CHANNELS'
        with mock.patch.object(w,'_state',return_value=(view,(),None,1,0,None)),mock.patch.object(w,'_metrics'),mock.patch.object(w,'hit_character',return_value='white'):
            self.assertIn('Selected rows',w.timeline_tooltip(context,(30,100)))
        context.region.type='WINDOW';view.derived=(NS(character_id='white',source_frame=2),)
        with mock.patch.object(w,'_state',return_value=(view,(),None,1,0,None)), mock.patch.object(w,'_metrics',return_value=NS(ui_scale=1)), \
                mock.patch.object(w,'hit_review_key',return_value=None),mock.patch.object(w,'hit_motion_interval',return_value=None), \
                mock.patch.object(w,'character_lanes',return_value=(('white',100),)):
            self.assertIn('not a human review key or generated pose',w.timeline_tooltip(context,(30,100)))
