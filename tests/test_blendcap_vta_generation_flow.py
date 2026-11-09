"""Generation recovery is explicit, source scoped, and atomically published."""
import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class GenerationFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        import sys
        cls.ui = sys.modules[BlenderOutputTests.addon.__name__+'.blendcap.vta_review_ui']
        cls.output = BlenderOutputTests.output
        cls.generation = __import__(BlenderOutputTests.addon.__name__+'.blendcap.vta_generation', fromlist=['migration_range'])
        if not getattr(cls.generation.import_generation,'_fixture_adapter',False):
            original=cls.generation.import_generation
            def import_generation(paths,location,backend,**kw):
                if kw.get('migration') and kw.get('validated') is None:
                    from tests.native_output_fixture import validate
                    values=[]
                    for path in paths:
                        report=cls.output._read_report(path)
                        fingerprint=backend._core.reconstruction_fingerprint(report.get('reconstruction'),report['plan'].get('source_video_sha256'))
                        values.append(dict(report=dict(reconstruction=dict(fingerprint=fingerprint))))
                    if values[0]['report']['reconstruction']['fingerprint']!=values[1]['report']['reconstruction']['fingerprint']:
                        raise ValueError('Migration and requested reconstruction fingerprints differ')
                    # Native per-output fixture will do the full old input
                    # validation. The transaction only needs these stamps.
                    kw['validated']=values
                return original(paths,location,backend,**kw)
            import_generation._fixture_adapter=True
            cls.generation.import_generation=import_generation


    def fixture(self):
        from tests.test_blendcap_vta_source_calibration import BlenderCalibrationTests
        test = BlenderCalibrationTests(methodName='runTest')
        test.setUp()
        self.addCleanup(test.doCleanups)
        return test

    def test_single_ui_command_below_range_controls(self):
        text = inspect.getsource(self.ui.VIEW3D_PT_blendcap_vta_animation.draw)
        self.assertEqual(text.count("text='Generate Animation'"), 1)
        self.assertLess(text.index('blendcap_vta_animation_start'), text.index("text='Generate Animation'"))
        self.assertLess(text.index('blendcap_vta_animation_end'), text.index("text='Generate Animation'"))
        self.assertLess(text.index("text='End = Current Frame'"), text.index("text='Generate Animation'"))
        self.assertNotIn('Generate / Update', inspect.getsource(self.ui))

    def test_selected_range_is_distinct_and_full_source_uses_same_command(self):
        options = self.generation.plan_options
        self.assertEqual(options((0,162),(0,162)), {})
        self.assertEqual(options((39,59),(0,162)), {'frame_range':(39,59)})
        self.assertEqual(options((0,89),(0,162)), {'frame_range':(0,89)})
        with self.assertRaisesRegex(ValueError, 'within source'):
            options((0,163),(0,162))

    def test_source_extent_is_not_scene_play_or_preview_range(self):
        from blendcap_vta_review import ReviewLocation
        cache = SimpleNamespace(source=SimpleNamespace(start_frame=0,end_frame=162))
        location = ReviewLocation(Path('project'), Path('video.mp4'), 21,22)
        self.assertEqual(self.generation.source_bounds(cache, location), (1,163))

    def test_legacy_detected_read_only_and_scope_excludes_user_keys(self):
        t=self.fixture(); f=t.fixture
        rig=f.rigs[0]
        rig.location.x=.5
        rig.keyframe_insert('location',frame=1000)
        before=f.curves(rig)
        path,_=t.calibrated_report(True)
        report=json.loads(path.read_text())
        required=self.generation.migration_range(bpy.context.scene,report['plan'],report['reconstruction']['fingerprint'],(0,162),1)
        self.assertEqual(required,(0,5))
        self.assertEqual(f.curves(rig),before)
        self.assertEqual(rig['vta_reconstruction_fingerprint'],self.generation.LEGACY)

    def test_compatible_has_no_migration(self):
        t=self.fixture(); f=t.fixture
        path,_=t.calibrated_report(False)
        self.output.import_reviewed_output(path,f.location,f.backend)
        report=json.loads(path.read_text())
        self.assertIsNone(self.generation.migration_range(bpy.context.scene,report['plan'],report['reconstruction']['fingerprint'],(0,5),1))


    def test_first_bounded_40_60_then_compatible_bounded_preserves_outside(self):
        t=self.fixture(); f=t.fixture
        for obj in (*f.rigs,bpy.context.scene.camera):
            bpy.data.objects.remove(obj,do_unlink=True)
        f.backend._load_context=lambda _: (None,SimpleNamespace(revision_id='a',source=SimpleNamespace(start_frame=0,end_frame=162)),'a')
        path,fp=t.calibrated_report(True,source_frames=range(39,60))
        rigs,_=self.output.import_reviewed_output(path,f.location,f.backend)
        self.assertEqual({o['vta_reconstruction_fingerprint'] for o in rigs},{fp})
        for o in rigs:
            pose_frames={int(p.co.x) for fc in self.output._iter_action_fcurves(o.animation_data.action) if fc.data_path.startswith('pose.bones[') for p in fc.keyframe_points}
            self.assertEqual(pose_frames,set(range(40,61)))
        old=[f.curves(o) for o in rigs]
        path,fp2=t.calibrated_report(True,source_frames=range(44,48))
        self.output.import_reviewed_output(path,f.location,f.backend)
        self.assertEqual(fp,fp2)
        for o,before in zip(rigs,old):
            after=f.curves(o)
            for channel,values in before.items():
                if channel[0].startswith('pose.bones['):
                    self.assertEqual(tuple(p for p in values[-1] if not 45<=p[0][0]<=48),tuple(p for p in after[channel][-1] if not 45<=p[0][0]<=48))

    def test_migration_failure_before_commit_preserves_objects_and_curves(self):
        t=self.fixture(); f=t.fixture
        paths=self.reports(t)
        before=[f.curves(o) for o in f.rigs]
        with mock.patch.object(self.output,'stage_source_camera',side_effect=ValueError('injected migration failure')):
            with self.assertRaisesRegex(ValueError,'migration failure'):
                self.generation.import_generation(paths,f.location,f.backend,migration=True)
        self.assertEqual([f.curves(o) for o in f.rigs],before)


    def reports(self,t):
        first,_=t.calibrated_report(False)
        second,_=t.calibrated_report(True)
        # Imported migration is bounded, but must cover all old output.
        data=json.loads(first.read_text()); data['plan']['generation_scope']='range'
        first.write_text(json.dumps(data))
        def plan(project, cache, **options):
            span=options.get('frame_range')
            return json.loads((first if list(span or (0,5))==[0,5] else second).read_text())['plan']
        t.fixture.backend._core.build_skeletal_plan=plan
        t.fixture.backend._core.resolve_pose_schedule=lambda samples,frames,**kw:[{'frame':f,'state':'reconstructed','pose_source_frame':f} for f in frames]
        return first,second

    def test_migration_then_request_preserves_user_edits_and_shared_fingerprint(self):
        t=self.fixture(); f=t.fixture
        rig=f.rigs[0]
        for frame in (-20,1,3,500):
            rig.location.x=frame*.1
            rig.keyframe_insert('location',frame=frame)
        before=f.curves(rig,True)
        paths=self.reports(t)
        root=rig.parent; camera=bpy.context.scene.camera
        self.generation.import_generation(paths,f.location,f.backend,migration=True)
        self.assertEqual(f.curves(rig,True),before)
        self.assertIs(rig.parent,root)
        self.assertIs(bpy.context.scene.camera,camera)
        fp=json.loads(paths[0].read_text())['reconstruction']['fingerprint']
        self.assertEqual({o['vta_reconstruction_fingerprint'] for o in f.rigs},{fp})
        self.assertEqual(camera.data['vta_reconstruction_fingerprint'],fp)
        self.assertTrue(camera.data.background_images)

    def test_requested_failure_rolls_back_migration_actions_metadata_camera_and_root(self):
        t=self.fixture(); f=t.fixture
        paths=self.reports(t)
        before=[(o.animation_data.action, f.curves(o), dict(o.items()),o.parent,o.matrix_world.copy()) for o in f.rigs]
        camera=bpy.context.scene.camera; camera_data=camera.data
        objects=set(bpy.data.objects); actions=set(bpy.data.actions)
        importer=self.output.import_reviewed_output
        calls=[]
        def import_one(path,*args,**kw):
            calls.append(path)
            if len(calls)==2:
                raise ValueError('injected requested-output failure')
            return importer(path,*args,**kw)
        with mock.patch.object(self.output,'import_reviewed_output',side_effect=import_one):
            with self.assertRaisesRegex(ValueError,'requested-output failure'):
                self.generation.import_generation(paths,f.location,f.backend,migration=True)
        self.assertEqual(calls,list(paths))
        self.assertEqual(set(bpy.data.objects),objects)
        self.assertEqual(set(bpy.data.actions),actions)
        self.assertIs(camera.data,camera_data)
        for o,(action,curves,meta,parent,matrix) in zip(f.rigs,before):
            self.assertIs(o.animation_data.action,action)
            self.assertEqual(f.curves(o),curves)
            self.assertEqual(dict(o.items()),meta)
            self.assertIs(o.parent,parent)
            self.assertEqual(o.matrix_world,matrix)

    def test_partial_migration_cannot_authorize_mixing(self):
        t=self.fixture(); f=t.fixture
        path,_=t.calibrated_report(True)
        before=[f.curves(o) for o in f.rigs]
        with self.assertRaisesRegex(ValueError,'does not cover all'):
            self.output.import_reviewed_output(path,f.location,f.backend,migration_approved=True)
        self.assertEqual([f.curves(o) for o in f.rigs],before)

    def test_different_migration_requested_metadata_rejected_before_mutation(self):
        t=self.fixture(); f=t.fixture
        paths=self.reports(t)
        second=json.loads(paths[1].read_text()); second.pop('reconstruction'); paths[1].write_text(json.dumps(second))
        before=[f.curves(o) for o in f.rigs]
        with self.assertRaisesRegex(ValueError,'fingerprints differ'):
            self.generation.import_generation(paths,f.location,f.backend,migration=True)
        self.assertEqual([f.curves(o) for o in f.rigs],before)

    def test_primary_failure_is_plain_and_diagnostics_retained(self):
        with mock.patch.object(self.ui,'_reconstruction_log',Path('generation.log')):
            self.ui._generation_failure('incompatible reconstruction fingerprint')
        self.assertIn('kept unchanged',self.ui._status)
        self.assertNotIn('fingerprint',self.ui._status)
        self.assertNotIn('stale',self.ui._status)
        self.assertIn('fingerprint',self.ui._generation_details)
        self.assertIn('generation.log',self.ui._generation_details)
        calibration_error=self.generation.failure_message('FIRST calibration requires an identifiable offline MoGe checkpoint')
        self.assertIn('installed body and FOV models',calibration_error)
        self.assertNotIn('accept the update',calibration_error)
        self.assertIn('both sides', self.generation.failure_message('Cannot interpolate repair gap: valid trustworthy pose required on both sides'))




    def test_preview_sync_uses_existing_row_state_without_time_scene_data_or_navigation_changes(self):
        from blendcap_vta_timeline import TimelineView,TimelineCharacter
        from blendcap_vta_review import ReviewLocation,SelectionState
        # Use the addon's own class, not a second namespace import.
        timeline=TimelineView('s',(TimelineCharacter('a','A'),TimelineCharacter('b','B')),())
        area=SimpleNamespace(type='DOPESHEET_EDITOR'); preview=SimpleNamespace(type='SEQUENCE_EDITOR')
        screen=SimpleNamespace(areas=[area,preview]); workspace=object()
        context=SimpleNamespace(area=preview,screen=screen,workspace=workspace,scene=bpy.context.scene)
        selected=SelectionState(); selected.character_id='b'
        location=ReviewLocation(Path('project'),Path('video.mp4'),0,1)
        frame=bpy.context.scene.frame_current; scene=bpy.context.scene
        def role(ws,sc,a):return self.ui.ROLE_TIMELINE if a is area else self.ui.ROLE_PREVIEW
        with mock.patch.object(self.ui,'role_for',side_effect=role), mock.patch.object(self.ui,'_presentation_current',return_value=True), mock.patch.object(self.ui,'_timeline',timeline), mock.patch.object(self.ui,'_location',location), mock.patch.object(self.ui,'_selection',selected), mock.patch.object(self.ui,'_character_selections',{}), mock.patch.object(self.ui,'_character_scroll',{}), mock.patch.object(self.ui,'_sync_properties') as properties:
            target=SimpleNamespace(area=area,screen=screen,workspace=workspace)
            before=self.ui._lane_scroll(target)
            for _ in range(10):self.assertTrue(self.ui.sync_character_to_timeline(context))
            rows=self.ui.timeline_character_selection(target)
            self.assertEqual(rows.character_ids,('b',)); self.assertEqual(rows.active_id,'b')
            self.assertEqual(self.ui._lane_scroll(target),before)
            properties.assert_not_called()
            selected.character_id='absent'
            self.assertFalse(self.ui.sync_character_to_timeline(context))
            self.assertEqual(rows.active_id,'b')
        self.assertIs(bpy.context.scene,scene)
        self.assertEqual(bpy.context.scene.frame_current,frame)
