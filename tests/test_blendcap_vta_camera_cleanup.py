"""Real Blender root ownership, reversal, publication and persistence checks."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

try:
    import bpy
    from mathutils import Matrix, Euler
except ImportError:
    bpy = None

PUBLIC = Path(__file__).resolve().parents[1]


@unittest.skipUnless(bpy is not None,'Blender runtime required')
class CameraCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass()
        cls.fixture_class = ObjectPlacementTests
        cls.output = ObjectPlacementTests.output
        cls.cleanup = importlib.import_module(cls.output.__package__+'.vta_camera_cleanup')
        sys.path.insert(0,str(PUBLIC.parent/'Video-to-Animation'))
        from vta_core import camera_rotation
        cls.core = camera_rotation

    def setUp(self):
        self.fixture = self.fixture_class()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.obj,self.other = self.fixture.rigs
        self.scene = bpy.context.scene
        self.metadata = {k:self.scene[k] for k in (self.cleanup.TRACKS,self.cleanup.SCENE_ID) if k in self.scene}
        self.scene[self.cleanup.SCENE_ID] = 'cleanup-unit-scene'
        self.addCleanup(self.restore_scene)
        self.root = self.obj.pose.bones['Root']

    def restore_scene(self):
        self.cleanup.cancel_job()
        backup = self.obj.get(self.cleanup.BACKUP)
        if backup: backup.use_fake_user = False
        for key in (self.cleanup.TRACKS,self.cleanup.SCENE_ID):
            self.scene.pop(key,None)
        for key,value in self.metadata.items(): self.scene[key] = value

    def solve(self,axes=(.02,.03,.01),*,moving=True):
        base = Matrix(((1,0,0),(0,0,1),(0,-1,0)))
        rotations,truth = [],{}
        # Build camera-perturbed generated motion in the actual imported bone
        # rest basis; limb animation remains the fixture's original animation.
        for frame in range(1,7):
            self.scene.frame_set(frame)
            self.obj.hide_viewport = False;self.obj.hide_set(False)
            bpy.context.view_layer.update()
            r = Euler(tuple(a*(frame-1) for a in axes),'XYZ').to_matrix()
            world = self.root.matrix.copy()
            if moving: world.translation.x += .1*(frame-1)
            truth[frame] = world.copy()
            self.root.matrix = (base @ r @ base.inverted()).to_4x4() @ world
            self.root.keyframe_insert('location',frame=frame)
            field = 'rotation_quaternion' if self.root.rotation_mode=='QUATERNION' else 'rotation_euler'
            self.root.keyframe_insert(field,frame=frame)
            rotations.append([list(row) for row in r])
        for fc in self.cleanup.root_curves(self.obj.animation_data.action):
            for p in fc.keyframe_points: p.interpolation='CONSTANT'
        request = dict(algorithm=self.core.ALGORITHM,scene_id='cleanup-unit-scene',
            project_id=self.fixture.pid,source_id='s',video_sha256='a'*64,cache_sha256='b'*64,
            calibration='fixed',width=1000,height=600,focal_px=500.,range=[0,5],offset=1,fps=24.,
            source_camera_matrix=[list(row) for row in self.scene.camera.matrix_world])
        solve = dict(schema='vta-camera-rotation',version=3,request=request,
            request_sha256=self.core.digest(request),translation='not-estimated',
            convention='cv-right-down-forward; world-to-camera; first-frame-identity',
            rotations=rotations,diagnostics=[],skipped=[],components=[dict(frame=i,
                accepted=dict(pan=True,tilt=True,roll=True),reasons={},method='joint-rotation') for i in range(6)])
        solve['sha256'] = self.core.digest(solve)
        return solve,truth

    def nonroot(self,obj):
        return [(fc.data_path,fc.array_index,[(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),
                    p.interpolation,p.handle_left_type,p.handle_right_type) for p in fc.keyframe_points])
                for fc in self.output._iter_action_fcurves(obj.animation_data.action)
                if not fc.data_path.startswith(self.cleanup.ROOT)]

    def check_axes(self,axes):
        solve,truth = self.solve(axes)
        other = self.cleanup.action_signature(self.other.animation_data.action)
        limbs = self.nonroot(self.obj)
        source = self.cleanup.action_signature(self.obj.animation_data.action,root_only=True)
        camera = self.scene.camera.matrix_world.copy()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertEqual(self.nonroot(self.obj),limbs)
        self.assertEqual(self.cleanup.action_signature(self.other.animation_data.action),other)
        self.assertEqual(self.scene.camera.matrix_world,camera)
        self.assertTrue(self.obj.get(self.cleanup.FINAL))
        for frame,expected in truth.items():
            self.scene.frame_set(frame);self.obj.hide_viewport=False
            bpy.context.view_layer.update()
            actual = self.obj.pose.bones['Root'].matrix
            self.assertLess(max(abs(a-b) for row_a,row_b in zip(actual,expected)
                                for a,b in zip(row_a,row_b)),2e-5)
        self.cleanup.restore_source(self.obj)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action,root_only=True),source)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))
        self.assertIn(solve['request_sha256'],json.loads(self.scene[self.cleanup.TRACKS]))

    def test_pan_bake(self): self.check_axes((0,.04,0))
    def test_tilt_bake(self): self.check_axes((.04,0,0))
    def test_roll_bake(self): self.check_axes((0,0,.04))
    def test_combined_moving_character_limbs_and_other_unchanged(self): self.check_axes((.02,.03,.01))

    def test_object_curves_and_later_object_edits_survive_restore(self):
        solve,_ = self.solve()
        self.obj.location.x += .4
        self.obj.keyframe_insert('location',frame=2)
        self.obj.location.x += .2
        self.obj.keyframe_insert('location',frame=5)
        self.obj.rotation_euler=(.1,.2,.3);self.obj.keyframe_insert('rotation_euler',frame=3)
        self.obj.scale=(1.1,.9,1.2);self.obj.keyframe_insert('scale',frame=3)
        self.obj.delta_location=(.1,.2,.3)
        delta=self.obj.delta_location.copy()
        before = self.nonroot(self.obj)
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertEqual(self.nonroot(self.obj),before)
        self.obj.location.x += 2
        self.obj.keyframe_insert('location',frame=4)
        later = self.nonroot(self.obj)
        self.cleanup.restore_source(self.obj)
        self.assertEqual(self.nonroot(self.obj),later)
        self.assertEqual(self.obj.delta_location,delta)

    def test_generated_root_scale_channels_are_not_rewritten(self):
        solve,_=self.solve()
        self.root.scale=(.9,1.1,1.)
        self.root.keyframe_insert('scale',frame=3)
        curves=[fc for fc in self.output._iter_action_fcurves(self.obj.animation_data.action) if fc.data_path==self.cleanup.ROOT+'scale']
        for fc in curves:
            for p in fc.keyframe_points:p.interpolation='CONSTANT'
        before=[(fc.array_index,[(tuple(p.co),p.interpolation) for p in fc.keyframe_points]) for fc in curves]
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        after=[(fc.array_index,[(tuple(p.co),p.interpolation) for p in fc.keyframe_points])
            for fc in self.output._iter_action_fcurves(self.obj.animation_data.action) if fc.data_path==self.cleanup.ROOT+'scale']
        self.assertEqual(after,before)

    def test_hidden_rig_does_not_require_evaluated_sampling(self):
        solve,_ = self.solve()
        self.obj.hide_set(True)
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertTrue(self.obj.hide_get())

    def skip(self,solve,indices):
        for index in indices:
            solve['rotations'][index]=None
            solve['components'][index].update(accepted=dict(pan=False,tilt=False,roll=False),
                reasons={name:'Unsupported test background' for name in ('pan','tilt','roll')},method='unresolved')
        solve['skipped']=[dict(frame=i,reason='Unsupported test background') for i in indices]
        solve['sha256']=self.core.digest({k:v for k,v in solve.items() if k!='sha256'})

    def test_partial_cleanup_preserves_skipped_values_and_restores_exactly(self):
        solve,truth=self.solve()
        # A sparse source key at skipped frame 3 must not inherit the correction
        # from frame 2. Record original evaluations before any staged inserts.
        curves=self.cleanup.root_curves(self.obj.animation_data.action)
        for fc in curves:
            for point in list(fc.keyframe_points):
                if point.co.x==3:fc.keyframe_points.remove(point)
        before={(fc.data_path,fc.array_index):[fc.evaluate(f) for f in (3,4)] for fc in curves}
        source=self.cleanup.action_signature(self.obj.animation_data.action,root_only=True)
        other=self.cleanup.action_signature(self.other.animation_data.action)
        limbs=self.nonroot(self.obj)
        self.skip(solve,[2,3])
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        for fc in self.cleanup.root_curves(self.obj.animation_data.action):
            self.assertEqual([fc.evaluate(f) for f in (3,4)],before[fc.data_path,fc.array_index])
        for frame in (2,5,6):
            self.scene.frame_set(frame);self.obj.hide_viewport=False;bpy.context.view_layer.update()
            actual=self.root.matrix
            self.assertLess(max(abs(a-b) for ar,br in zip(actual,truth[frame]) for a,b in zip(ar,br)),2e-5)
        self.assertEqual(self.nonroot(self.obj),limbs)
        self.assertEqual(self.cleanup.action_signature(self.other.animation_data.action),other)
        with mock.patch.object(self.cleanup,'_finish_feedback') as feedback:
            self.cleanup.completion_feedback(self.scene,self.obj,solve)
            self.assertIn('skipped 2',feedback.call_args.args[0])
            self.assertIn('may jump',feedback.call_args.args[1])
            self.assertNotIn('error',feedback.call_args.kwargs)
        self.cleanup.restore_source(self.obj)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action,root_only=True),source)

    def test_all_rejected_frames_leave_action_and_finalization_unchanged(self):
        solve,_=self.solve();self.skip(solve,[1,2,3,4,5])
        original=self.obj.animation_data.action
        signature=self.cleanup.action_signature(original)
        actions=set(bpy.data.actions)
        self.assertIsNone(self.cleanup.apply_solve(self.scene,self.obj,solve,self.core))
        self.assertIs(self.obj.animation_data.action,original)
        self.assertEqual(self.cleanup.action_signature(original),signature)
        self.assertEqual(set(bpy.data.actions),actions)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))
        self.assertFalse(self.obj.get(self.cleanup.BACKUP))
        with mock.patch.object(self.cleanup,'_finish_feedback') as feedback:
            self.cleanup.completion_feedback(self.scene,self.obj,solve)
            self.assertIn('animation unchanged',feedback.call_args.args[0])

    def test_roll_only_bake_leaves_pan_tilt_motion_scale_and_other_character(self):
        from mathutils import Vector
        import math
        solve,_=self.solve((.02,.03,.04))
        base=Matrix(((1,0,0),(0,0,1),(0,-1,0)))
        expected={};scales={}
        for index in range(1,6):
            frame=index+1;self.scene.frame_set(frame)
            self.obj.hide_viewport=False;bpy.context.view_layer.update()
            source=self.root.matrix.copy();scales[frame]=source.to_scale()
            full=Matrix(solve['rotations'][index])
            angle=math.atan2(full[1][0],full[0][0])
            r=Matrix.Rotation(angle,3,'Z')
            solve['rotations'][index]=[list(row) for row in r]
            solve['components'][index].update(accepted=dict(pan=False,tilt=False,roll=True),
                reasons=dict(pan='Unsupported test zoom',tilt='Unsupported test zoom'),method='independent-roll')
            expected[frame]=(base @ r.transposed() @ base.inverted()).to_4x4() @ source
        solve['sha256']=self.core.digest({k:v for k,v in solve.items() if k!='sha256'})
        signature=self.cleanup.action_signature(self.obj.animation_data.action,root_only=True)
        other=self.cleanup.action_signature(self.other.animation_data.action)
        limbs=self.nonroot(self.obj);camera=self.scene.camera.matrix_world.copy()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        for frame,matrix in expected.items():
            self.scene.frame_set(frame);self.obj.hide_viewport=False;bpy.context.view_layer.update()
            actual=self.root.matrix
            self.assertLess(max(abs(a-b) for ar,br in zip(actual,matrix) for a,b in zip(ar,br)),2e-5)
            self.assertLess((actual.to_scale()-scales[frame]).length,1e-5)
        metadata=json.loads(self.obj[self.cleanup.FINAL])
        self.assertEqual(len(metadata['component_review']),5)
        self.assertTrue(all(row['uncorrected']==['pan','tilt'] for row in metadata['component_review']))
        self.assertEqual(self.nonroot(self.obj),limbs)
        self.assertEqual(self.cleanup.action_signature(self.other.animation_data.action),other)
        self.assertEqual(self.scene.camera.matrix_world,camera)
        with mock.patch.object(self.cleanup,'_finish_feedback') as feedback:
            self.cleanup.completion_feedback(self.scene,self.obj,solve)
            self.assertIn('5 roll-only',feedback.call_args.args[0])
            self.assertIn('Pan/tilt were not corrected',feedback.call_args.args[1])
        self.cleanup.restore_source(self.obj)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action,root_only=True),signature)

    def test_partial_cached_and_worker_results_use_same_warning_path(self):
        solve,_=self.solve();self.skip(solve,[2])
        # Operator cache-hit branch and asynchronous publication share the same
        # outcome function, rather than labelling a partial solve a full success.
        ui=importlib.import_module(self.output.__package__+'.vta_review_ui')
        context=NS(scene=self.scene)
        self.scene[self.cleanup.TRACKS]=json.dumps({solve['request_sha256']:solve})
        prepared=((self.obj,),None,None,self.core,solve['request'],'revision',[])
        with mock.patch.object(ui,'_analysis_locked',return_value=False), \
                mock.patch.object(self.cleanup,'availability',return_value=(True,'')), \
                mock.patch.object(self.cleanup,'targets',return_value=((self.obj,),None)), \
                mock.patch.object(self.cleanup,'prepare_selection',return_value=prepared), \
                mock.patch.object(self.cleanup,'completion_feedback') as feedback:
            operator=NS(action='REMOVE',_confirmed_objects=(self.obj,),report=mock.Mock())
            self.assertEqual(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.execute(operator,context),{'FINISHED'})
            feedback.assert_called_once_with(self.scene,self.obj,solve,None)
        self.cleanup.restore_source(self.obj)
        directory=self.fixture.root/'rotation-partial';directory.mkdir()
        (directory/'solve.json').write_text(json.dumps(solve))
        log=directory/'worker.log';log.write_text('skipped one')
        self.cleanup._job=dict(process=NS(poll=lambda:0,returncode=0),cancelled=False,
            objects=(self.obj,),selected=(self.obj,),scene=self.scene,
            snapshots=((self.obj,self.obj.animation_data.action,self.cleanup.action_signature(self.obj.animation_data.action)),),
            log=log,directory=directory,request=solve['request'])
        with mock.patch.object(self.cleanup,'prepare_selection',return_value=prepared), \
                mock.patch.object(self.cleanup,'targets',return_value=((self.obj,),None)), \
                mock.patch.object(self.cleanup,'completion_feedback') as feedback:
            self.cleanup.poll_job()
            feedback.assert_called_once_with(self.scene,self.obj,solve,log)
        self.assertTrue(self.obj.get(self.cleanup.FINAL))

    def test_no_translation_compensation_identity_track(self):
        solve,_ = self.solve((0,0,0))
        before = self.cleanup.action_signature(self.obj.animation_data.action,root_only=True)
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        after = self.cleanup.action_signature(self.obj.animation_data.action,root_only=True)
        # Curve sampling may update handles; evaluated source trajectory remains.
        backup = self.obj[self.cleanup.BACKUP]
        for a,b in zip(self.cleanup.root_curves(self.obj.animation_data.action),self.cleanup.root_curves(backup)):
            for frame in range(1,7): self.assertAlmostEqual(a.evaluate(frame),b.evaluate(frame),places=5)

    def test_finalized_guard_and_double_application(self):
        solve,_ = self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        with self.assertRaisesRegex(ValueError,'Restore Source Alignment'):
            self.cleanup.require_source_aligned(self.scene,[self.obj['vta_character_id']])
        self.cleanup.require_source_aligned(self.scene,[self.other['vta_character_id']])
        with self.assertRaisesRegex(ValueError,'already finalized'):
            self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.cleanup.restore_source(self.obj)
        self.cleanup.require_source_aligned(self.scene,[self.obj['vta_character_id']])

    def test_remove_runs_directly_restore_still_requires_confirmation(self):
        ui = importlib.import_module(self.output.__package__+'.vta_review_ui')
        original = self.obj.animation_data.action
        dialog = mock.Mock(return_value={'RUNNING_MODAL'})
        operator = NS(action='REMOVE',report=mock.Mock(),execute=mock.Mock(return_value={'FINISHED'}))
        context = NS(window_manager=NS(invoke_props_dialog=dialog),scene=self.scene)
        with mock.patch.object(self.cleanup,'targets',return_value=((self.obj,),None)), \
                mock.patch.object(self.cleanup,'availability',return_value=(True,'')), \
                mock.patch.object(self.cleanup,'prepare_selection'),mock.patch.object(ui,'_analysis_locked',return_value=False):
            self.assertEqual(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.invoke(operator,context,None),{'FINISHED'})
            dialog.assert_not_called();operator.execute.assert_called_once_with(context)
            operator.action='RESTORE'
            self.assertEqual(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.invoke(operator,context,None),{'RUNNING_MODAL'})
        self.assertIs(self.obj.animation_data.action,original)
        dialog.assert_called_once()
        self.assertIn('Restore Source Alignment',dialog.call_args.kwargs['confirm_text'])

    def test_finalized_tracking_and_repair_operators_block_before_backend_mutation(self):
        solve,_ = self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        ui = importlib.import_module(self.output.__package__+'.vta_review_ui')
        context = NS(scene=self.scene,window_manager=NS(blendcap_vta_motion_start=1,blendcap_vta_motion_end=6))
        ids = (self.obj['vta_character_id'],)
        backend = mock.Mock()
        with mock.patch.object(ui,'_edit_available',return_value=True),mock.patch.object(ui,'_is_timeline_context',return_value=True), \
                mock.patch.object(ui,'_location_from_context',return_value=self.fixture.location), \
                mock.patch.object(ui,'tracking_character_ids',return_value=ids), \
                mock.patch.object(ui,'timeline_character_selection',return_value=NS(character_ids=ids)), \
                mock.patch.object(ui,'refresh',return_value=True),mock.patch.object(ui,'_location',self.fixture.location), \
                mock.patch.object(ui,'selected_repair_range',return_value=None), \
                mock.patch.object(ui,'_get_backend',return_value=backend):
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(NS(report=mock.Mock()),context),{'CANCELLED'})
            self.assertEqual(ui.BLENDCAP_OT_vta_motion_trust.execute(NS(action='FREEZE',report=mock.Mock()),context),{'CANCELLED'})
        backend.update_tracking_for_characters.assert_not_called()
        backend.set_motion_trust_for_characters.assert_not_called()

    def test_scene_guard_ignores_unrelated_source_owner(self):
        solve,_ = self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.cleanup.require_source_aligned(self.scene,[self.obj['vta_character_id']],self.fixture.pid,'different-source')

    def test_cancelled_poller_never_publishes_completed_result(self):
        original = self.obj.animation_data.action
        self.cleanup._job = dict(process=NS(poll=lambda:0),cancelled=True)
        self.assertIsNone(self.cleanup.poll_job())
        self.assertIs(self.obj.animation_data.action,original)
        self.assertFalse(self.cleanup.busy())

    def test_stale_worker_result_is_not_published(self):
        solve,_ = self.solve()
        original = self.obj.animation_data.action
        log = self.fixture.root/'worker.log';log.write_text('finished')
        self.cleanup._job = dict(process=NS(poll=lambda:0,returncode=0),cancelled=False,
            objects=(self.obj,),selected=(self.obj,),scene=self.scene,
            snapshots=((self.obj,original,'stale'),),log=log)
        self.assertIsNone(self.cleanup.poll_job())
        self.assertIs(self.obj.animation_data.action,original)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))

    def test_batch_same_solve_independent_metadata_and_restore(self):
        solve,_=self.solve();self.skip(solve,[2])
        before={obj:self.cleanup.action_signature(obj.animation_data.action) for obj in (self.obj,self.other)}
        with mock.patch.object(self.cleanup,'_finish_feedback'):
            self.assertTrue(self.cleanup.apply_selection(self.scene,(self.obj,self.other),solve,self.core))
        for obj in before:
            meta=json.loads(obj[self.cleanup.FINAL])
            self.assertEqual(meta['character_id'],obj['vta_character_id'])
            self.assertEqual(meta['solve_sha256'],solve['sha256'])
            self.assertIn(3,meta['skipped_frames'])
        other=self.cleanup.action_signature(self.other.animation_data.action)
        self.cleanup.restore_source(self.obj)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action),before[self.obj])
        self.assertEqual(self.cleanup.action_signature(self.other.animation_data.action),other)
        self.assertTrue(self.other.get(self.cleanup.FINAL))
        self.cleanup.restore_source(self.other)
        self.assertEqual(self.cleanup.action_signature(self.other.animation_data.action),before[self.other])

    def test_batch_problem_character_does_not_block_other(self):
        solve,_=self.solve();before=self.cleanup.action_signature(self.obj.animation_data.action)
        apply=self.cleanup.apply_solve
        def partial(scene,obj,*args):
            if obj==self.obj:raise ValueError('unsupported rig')
            return apply(scene,obj,*args)
        with mock.patch.object(self.cleanup,'apply_solve',side_effect=partial),mock.patch.object(self.cleanup,'_finish_feedback') as feedback:
            self.assertTrue(self.cleanup.apply_selection(self.scene,(self.obj,self.other),solve,self.core))
        self.assertIn(self.obj.name,feedback.call_args.args[1])
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action),before)
        self.assertFalse(self.obj.get(self.cleanup.FINAL));self.assertTrue(self.other.get(self.cleanup.FINAL))
        self.cleanup.restore_source(self.other)

    def test_batch_preflight_union_range_single_scene_and_problem_isolation(self):
        solve,_=self.solve();request=solve['request']
        def prepared(ctx,obj):
            return obj,None,None,self.core,{**request,'range':[0,5 if obj==self.obj else 7]},'revision'
        with mock.patch.object(self.cleanup,'prepare',side_effect=prepared):
            objects,_,_,_,req,_,issues=self.cleanup.prepare_selection(None,(self.obj,self.other))
        self.assertEqual(objects,(self.obj,self.other));self.assertEqual(req['range'],[0,7]);self.assertFalse(issues)
        def failing(ctx,obj):
            if obj==self.obj:raise ValueError('stale')
            return prepared(ctx,obj)
        with mock.patch.object(self.cleanup,'prepare',side_effect=failing):
            objects,_,_,_,_,_,issues=self.cleanup.prepare_selection(None,(self.obj,self.other))
        self.assertEqual(objects,(self.other,));self.assertEqual(issues,[(self.obj.name,'stale')])

    def test_batch_operator_uses_all_confirmed_targets_and_cached_solve(self):
        solve,_=self.solve();objects=(self.obj,self.other)
        self.scene[self.cleanup.TRACKS]=json.dumps({solve['request_sha256']:solve})
        ui=importlib.import_module(self.output.__package__+'.vta_review_ui')
        context=NS(scene=self.scene)
        prepared=(objects,None,None,self.core,solve['request'],'r',[])
        with mock.patch.object(ui,'_analysis_locked',return_value=False), \
                mock.patch.object(self.cleanup,'availability',return_value=(True,'')), \
                mock.patch.object(self.cleanup,'targets',return_value=(objects,None)), \
                mock.patch.object(self.cleanup,'prepare_selection',return_value=prepared), \
                mock.patch.object(self.cleanup,'_finish_feedback'):
            operator=NS(action='REMOVE',_confirmed_objects=objects,report=mock.Mock())
            self.assertEqual(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.execute(operator,context),{'FINISHED'})
            for obj in objects:self.assertTrue(obj.get(self.cleanup.FINAL))
            operator.action='RESTORE'
            self.assertEqual(self.cleanup.BLENDCAP_OT_vta_camera_cleanup.execute(operator,context),{'FINISHED'})
            for obj in objects:self.assertFalse(obj.get(self.cleanup.FINAL))

    def test_restore_confirmation_explains_root_replacement_and_preserved_edits(self):
        layout=mock.Mock()
        self.cleanup.BLENDCAP_OT_vta_camera_cleanup.draw(NS(action='RESTORE',layout=layout),None)
        text=' '.join(call.kwargs['text'] for call in layout.label.call_args_list)
        self.assertIn('Root edits will be replaced',text)
        self.assertIn('Object Mode edits and limb animation are retained',text)

    def test_scene_switch_does_not_receive_another_scenes_feedback_or_animation(self):
        original=self.obj.animation_data.action
        other=bpy.data.scenes.new('empty unrelated cleanup Scene')
        log=self.fixture.root/'switch.log';log.write_text('finished')
        ui=importlib.import_module(self.output.__package__+'.vta_review_ui')
        try:
            bpy.context.window.scene=other
            self.cleanup._job=dict(process=NS(poll=lambda:0,returncode=0),cancelled=False,
                obj=self.obj,scene=self.scene,log=log)
            with mock.patch.object(ui,'set_operation_feedback') as feedback:
                self.cleanup.poll_job()
                self.assertIs(feedback.call_args.kwargs['context'].window.scene,self.scene)
            self.assertIs(bpy.context.window.scene,other)
            self.assertIs(self.obj.animation_data.action,original)
        finally:
            bpy.context.window.scene=self.scene
            bpy.data.scenes.remove(other)

    def test_generation_import_blocks_before_mutation(self):
        solve,_ = self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        before = self.cleanup.action_signature(self.obj.animation_data.action)
        with self.assertRaisesRegex(ValueError,'Restore Source Alignment'):
            self.fixture.regenerate(True)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action),before)

    def test_source_wide_migration_cannot_silently_include_finalized_other_character(self):
        solve,_=self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        generation=importlib.import_module(self.output.__package__+'.vta_generation')
        plan=dict(project_id=self.fixture.pid,source_id='s',source_range=[0,5])
        with self.assertRaisesRegex(ValueError,'Restore Source Alignment'):
            generation.migration_range(self.scene,plan,'changed-calibration',(0,5),1)

    def test_scene_identity_and_shared_rig_refused(self):
        solve,_ = self.solve()
        other_scene = bpy.data.scenes.new('unrelated cleanup Scene')
        try:
            with self.assertRaises(ValueError): self.cleanup.apply_solve(other_scene,self.obj,solve,self.core)
            other_scene.collection.objects.link(self.obj)
            with self.assertRaisesRegex(ValueError,'shared'):
                self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        finally: bpy.data.scenes.remove(other_scene)

    def test_camera_deletion_keeps_finished_animation_solve_and_restore(self):
        solve,_ = self.solve()
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        before=self.cleanup.action_signature(self.obj.animation_data.action)
        bpy.data.objects.remove(self.scene.camera,do_unlink=True)
        self.assertEqual(self.cleanup.action_signature(self.obj.animation_data.action),before)
        stored=json.loads(self.scene[self.cleanup.TRACKS])[solve['request_sha256']]
        self.assertEqual(stored['request']['source_camera_matrix'],solve['request']['source_camera_matrix'])
        self.cleanup.restore_source(self.obj)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))

    def test_corrupt_solve_and_backup_fail_without_change(self):
        solve,_ = self.solve()
        original = self.obj.animation_data.action
        solve['rotations'][1][0][0] = 99
        with self.assertRaises(ValueError): self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertIs(self.obj.animation_data.action,original)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))

    def test_failed_publish_restores_action_and_metadata(self):
        solve,_ = self.solve()
        original = self.obj.animation_data.action
        # Invalid existing Scene solve map is detected after the staged bake.
        self.scene[self.cleanup.TRACKS] = 'invalid'
        before = set(bpy.data.actions)
        with self.assertRaises(ValueError): self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        self.assertIs(self.obj.animation_data.action,original)
        self.assertEqual(set(bpy.data.actions),before)
        self.assertFalse(self.obj.get(self.cleanup.FINAL))
        self.assertEqual(self.scene[self.cleanup.TRACKS],'invalid')

    def test_save_reload_fresh_process_preserves_solve_and_recovery(self):
        solve,_ = self.solve()
        self.skip(solve,[2])
        self.cleanup.apply_solve(self.scene,self.obj,solve,self.core)
        destination = self.fixture.root/'cleanup proof.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        command = [bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec',
            '--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--fresh',str(destination),self.obj.name]
        result = subprocess.run(command,capture_output=True,text=True,timeout=40,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('FRESH_ROTATION_RESTORE_PASS',result.stdout)


if __name__ == '__main__':
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(PUBLIC))
    args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if args and args[0]=='--fresh':
        CameraCleanupTests.setUpClass()
        bpy.ops.wm.open_mainfile(filepath=args[1])
        cleanup = CameraCleanupTests.cleanup
        obj = bpy.data.objects[args[2]]
        tracks = bpy.context.scene[cleanup.TRACKS]
        original = json.loads(obj[cleanup.FINAL])['original_root_sha256']
        cleanup.restore_source(obj)
        assert cleanup.action_signature(obj.animation_data.action,root_only=True)==original
        assert bpy.context.scene[cleanup.TRACKS]==tracks and not obj.get(cleanup.FINAL)
        print('FRESH_ROTATION_RESTORE_PASS',flush=True)
    else:
        unittest.main(argv=[sys.argv[0]],verbosity=2)
