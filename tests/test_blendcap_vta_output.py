"""Reviewed skeletal boundaries, suppression presentation, and real evaluated Hold."""
from dataclasses import replace
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest import mock
import tempfile
import unittest

from blendcap_vta_review import CandidateRow, SelectionState, present_review
from blendcap_vta_review import ReviewLocation, validate_output_mapping, VtaReviewUiError
from blendcap_vta_reconstruct_worker import bbox_for_backend, export_command, infer_reviewed_pose
from tests.test_blendcap_vta_review import Review, Candidate

try:
    import bpy
except ImportError:
    bpy = None


class OutputBoundaryTests(unittest.TestCase):
    def test_stop_path_cannot_target_another_project_or_existing_report(self):
        from blendcap_vta_reconstruct_worker import run
        for stop in ('other/generation-job.stop', 'project/reconstruction/output.json'):
            with self.subTest(stop=stop), self.assertRaisesRegex(RuntimeError, 'stop request'):
                run(SimpleNamespace(core_root='private', blendcap_root='bundle',
                    companion_root='project', video='video.mp4', stop_file=stop))
    def test_hold_seed_reuses_only_immutable_current_evidence_and_never_outside_inference(self):
        import json
        import blendcap_vta_reconstruct_worker as worker
        sample = {'frame': 2, 'mode': 'infer', 'reason': 'user-anchor', 'evidence': {'observation_id': 'exact'}}
        character = {'character_id': 'c', 'samples': [{'frame': 5, 'mode': 'hold'}], 'hold_context': [sample]}
        fields = dict(project_id='p', source_id='s', source_video_sha256='hash', fps=24, width=100, height=50)
        plan = {**fields, 'source_range': [5, 6]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root/'reconstruction/run-old'
            run.mkdir(parents=True)
            pose = run/'pose.npz'
            pose.write_bytes(b'immutable pose artifact')
            bvh = run/'pose.bvh'
            bvh.write_bytes(b'immutable final BVH channels')
            report = {'schema': 'vta-reviewed-skeletal-output', 'version': 1,
                'plan': {**fields, 'characters': [{'character_id': 'c', 'samples': [sample]}]},
                'characters': [{'character_id': 'c', 'source_frames': [2], 'npz': pose.name,
                    'npz_sha256': worker.digest(pose), 'schedule': [
                        {'frame': 2, 'state': 'reconstructed', 'pose_source_frame': 2}],
                    'bvh': bvh.name, 'bvh_sha256': worker.digest(bvh)}]}
            worker.write_new_json(run/'output.json', report)
            seed = worker.find_hold_seed(plan, character, root)
            self.assertEqual(seed['frame'], 2)
            self.assertEqual(worker.validate_hold_seed(plan, character, seed, root), pose)
            altered = {**character, 'hold_context': [{**sample, 'evidence': {'observation_id': 'other'}}]}
            self.assertIsNone(worker.find_hold_seed(plan, altered, root))
            with self.assertRaisesRegex(RuntimeError, 'current reviewed evidence'):
                worker.validate_hold_seed(plan, altered, seed, root)
            self.assertIsNone(worker.find_hold_seed(plan, {**character, 'samples': [
                {'frame': 5, 'mode': 'infer'}, {'frame': 6, 'mode': 'hold'}]}, root))
            pose.write_bytes(b'tampered')
            with self.assertRaisesRegex(RuntimeError, 'pose hash changed'):
                worker.validate_hold_seed(plan, character, seed, root)

    def test_async_output_accepts_navigation_but_rejects_new_project_source_and_offset(self):
        location = ReviewLocation(Path('a_vta'), Path('video.mp4'), 0, 10)
        validate_output_mapping(location, replace(location, source_frame=5, blender_frame=15))
        for altered in (replace(location, companion_root=Path('b_vta')),
                        replace(location, source_path=Path('other.mp4')),
                        replace(location, blender_frame=11)):
            with self.assertRaisesRegex(VtaReviewUiError, 'mapping changed'):
                validate_output_mapping(location, altered)
    def test_preview_hides_suppression_preserves_positions_and_blocks_assignment(self):
        raw = Review(candidates=(SimpleNamespace(**Candidate('r', 'a').__dict__, suppressed=True), Candidate('r', 'b')))
        default = present_review(raw)
        shown = present_review(raw, show_suppressed=True)
        self.assertEqual([(r.observation_id, r.position) for r in default.candidates], [('b', 2)])
        self.assertEqual([(r.observation_id, r.suppressed) for r in shown.candidates], [('a', True), ('b', False)])
        selection = SelectionState()
        selection.select_observation(shown, 'a')
        selection.select_character(shown, shown.characters[0].character_id)
        self.assertFalse(selection.can_assign(shown))
        self.assertFalse(shown.candidates[0].assignable)
        selection.accept_review(default)
        self.assertEqual(selection.observation_id, '')

    def test_lower_left_boundary_conversion_and_no_export_cleanup(self):
        self.assertEqual(bbox_for_backend((10, 20, 80, 200), 300), (10, 100, 80, 280))
        command = export_command('python', Path('bundle'), Path('pose.npz'), Path('out.bvh'))
        self.assertIn('--no-foot-lock', command)
        self.assertIn('--root-motion', command)
        self.assertNotIn('--clean-tracking', command)
        for flag in ('--smooth', '--smooth-root', '--smooth-vertical'):
            self.assertEqual(command[command.index(flag)+1], '0')

    def test_worker_uses_exact_reviewed_box_and_never_selects_another_person(self):
        import numpy as np
        evidence = {'box_xyxy': [10,20,80,200]}
        pose = {'bbox': np.asarray([10,100,80,280]), 'pred_global_rots': np.tile(np.eye(3), (2,1,1)),
                'pred_joint_coords': np.zeros((2,3)), 'pred_cam_t': np.ones(3), 'focal_length': 700.0}
        estimator = SimpleNamespace(process_one_image=mock.Mock(return_value=[pose]))
        torch = SimpleNamespace(inference_mode=nullcontext)
        rgb = np.zeros((300,100,3), dtype=np.uint8)
        result = infer_reviewed_pose(estimator, np, torch, rgb, evidence, 300)
        call = estimator.process_one_image.call_args
        self.assertIs(call.args[0], rgb)
        self.assertEqual(call.kwargs['inference_type'], 'full')
        np.testing.assert_array_equal(call.kwargs['bboxes'], [[10,100,80,280]])
        np.testing.assert_array_equal(result['pred_cam_t'], [1,1,1])
        estimator.process_one_image.return_value = [pose, pose]
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            infer_reviewed_pose(estimator, np, torch, rgb, evidence, 300)
        estimator.process_one_image.return_value = [{**pose, 'bbox': [0,0,1,1]}]
        with self.assertRaisesRegex(RuntimeError, 'different candidate'):
            infer_reviewed_pose(estimator, np, torch, rgb, evidence, 300)
        estimator.process_one_image.return_value = [{**pose, 'pred_cam_t': np.full(3, np.nan)}]
        self.assertIsNone(infer_reviewed_pose(estimator, np, torch, rgb, evidence, 300))

    def test_immutable_run_publication_rejects_existing_manifest(self):
        from blendcap_vta_reconstruct_worker import write_new_json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'output.json'
            write_new_json(path, {'first': True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_new_json(path, {'first': False})
            self.assertEqual(original, path.read_bytes())

    def test_atomic_publication_race_preserves_unexpected_destination(self):
        import blendcap_vta_reconstruct_worker as worker
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'output.json'
            link = os.link
            def race(temporary, destination):
                Path(destination).write_bytes(b'concurrent unrelated output')
                return link(temporary, destination)
            with mock.patch.object(worker.os, 'link', side_effect=race):
                with self.assertRaises(FileExistsError):
                    worker.write_new_json(path, {'result': True})
            self.assertEqual(path.read_bytes(), b'concurrent unrelated output')
            self.assertEqual(list(Path(directory).iterdir()), [path])


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class BlenderOutputTests(unittest.TestCase):
    def test_cooperative_cancel_preserves_review_and_stabilizes_status(self):
        import sys
        ui = sys.modules[self.addon.__name__ + '.blendcap.vta_review_ui']
        view = object()
        process = SimpleNamespace(poll=lambda: None)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(ui, '_reconstruction_stop', Path(directory)/'generation-unique.stop'), \
             mock.patch.object(ui, '_reconstruction_process', process), \
             mock.patch.object(ui, '_view', view):
            operator = SimpleNamespace(action='CANCEL', report=mock.Mock())
            self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(operator, bpy.context), {'FINISHED'})
            self.assertTrue(ui._reconstruction_stop.is_file())
            ui._reconstruction_process = SimpleNamespace(poll=lambda: 1, returncode=1)
            ui._reconstruction_timer()
            self.assertIn('cancelled', ui._generation_status)
            self.assertFalse(ui._status_is_error)
            self.assertIs(ui._view, view)
    def test_hold_seed_copies_original_final_channels_not_span_renormalized_values(self):
        import json
        seed_rig = self.rig('final-seed', [0, 2])
        target = self.rig('renormalized-segment', [2])
        # Keep the helper's disposable import separate from fixture cleanup.
        target['vta_character_id'] = 'c'
        target.pose.bones['Root'].location.x = 500
        target.pose.bones['Root'].keyframe_insert('location', frame=1)
        schedule = [{'frame': f, 'state': 'held', 'pose_source_frame': 2} for f in (5, 6)]
        self.output.retime_reviewed_action(target, [2], schedule, 1)
        seed_values = {(fc.data_path, fc.array_index): fc.evaluate(2)
                       for fc in self.output._iter_action_fcurves(seed_rig.animation_data.action)}
        def imported(*args, **kwargs):
            seed_copy = seed_rig.copy()
            seed_copy.data = seed_rig.data.copy()
            seed_copy.animation_data.action = seed_rig.animation_data.action.copy()
            bpy.context.collection.objects.link(seed_copy)
            bpy.context.view_layer.objects.active = seed_copy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'source.bvh').write_bytes(b'hash-validated source')
            (root/'output.json').write_text(json.dumps({'characters': [{'character_id': 'c',
                'bvh': 'source.bvh', 'bvh_sha256': self.output._hash(root/'source.bvh'), 'source_frames': [0, 2]}]}))
            with mock.patch.object(self.output, '_import_bvh', side_effect=imported):
                self.output._copy_final_hold_seed(target, {'output': 'output.json', 'frame': 2}, schedule, root, 1)
        for fc in self.output._iter_action_fcurves(target.animation_data.action):
            if fc.data_path not in ('hide_render', 'hide_viewport'):
                self.assertEqual(fc.evaluate(6), seed_values[fc.data_path, fc.array_index])
                self.assertEqual(fc.evaluate(7), seed_values[fc.data_path, fc.array_index])

    def test_generate_range_launches_source_bounds_and_keeps_hold_controls_distinct(self):
        import sys
        ui = sys.modules[self.addon.__name__ + '.blendcap.vta_review_ui']
        wm = SimpleNamespace(blendcap_vta_animation_start=11, blendcap_vta_animation_end=15,
            blendcap_vta_motion_start=67, blendcap_vta_motion_end=90)
        context = SimpleNamespace(window_manager=wm, scene=bpy.context.scene)
        operator = SimpleNamespace(action='RANGE', report=mock.Mock())
        plan = mock.Mock(return_value={})
        backend = SimpleNamespace(_load_context=lambda _: ('project', 'cache', 'revision'),
                                  _core=SimpleNamespace(build_skeletal_plan=plan))
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(ui, '_location_from_context', return_value=ReviewLocation(
                 Path(directory), Path(directory)/'video.mp4', 0, 1)), \
             mock.patch.object(ui, '_get_backend', return_value=backend), \
             mock.patch.object(ui, '_edit_available', return_value=True), \
             mock.patch.object(ui, '_python_exe', return_value='managed-python.exe'), \
             mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(ui.subprocess, 'Popen', return_value=SimpleNamespace(poll=lambda: None)) as launch, \
             mock.patch.object(ui, '_reconstruction_process', None), \
             mock.patch.object(ui, '_backend_root', Path('private')), \
             mock.patch.object(bpy.app.timers, 'register'):
            self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(operator, context), {'FINISHED'})
            plan.assert_called_once_with('project', 'cache', frame_range=(10, 14))
            command = launch.call_args.args[0]
            self.assertEqual(command[command.index('--start-frame')+1], '10')
            self.assertEqual(command[command.index('--end-frame')+1], '14')
            self.assertIn('11–15', ui._generation_status)
            self.assertEqual((wm.blendcap_vta_motion_start, wm.blendcap_vta_motion_end), (67, 90))
            launch.reset_mock()
            wm.blendcap_vta_animation_start = 20
            self.assertEqual(ui.BLENDCAP_OT_vta_generate_animation.execute(operator, context), {'CANCELLED'})
            launch.assert_not_called()
            self.assertIn('must not exceed', operator.report.call_args.args[1])

    def test_partial_actions_compose_overlap_hold_independence_and_reload(self):
        obj = self.rig('range-white', list(range(12)))
        other = self.rig('range-yellow', list(range(12)))
        schedule = [{'frame': f, 'state': 'reconstructed', 'pose_source_frame': f} for f in range(12)]
        for target in (obj, other):
            self.output.retime_reviewed_action(target, list(range(12)), schedule, 1)
        def values(target):
            result = {}
            for frame in range(0, 15):
                bpy.context.scene.frame_set(frame)
                result[frame] = self.matrix(target), target.hide_render
            return result
        independent = values(other)
        def keys(action):
            return {(fc.data_path, fc.array_index): tuple((tuple(p.co), p.interpolation)
                    for p in fc.keyframe_points) for fc in self.output._iter_action_fcurves(action)}
        original_action = obj.animation_data.action
        for start, end in ((4, 6), (8, 10), (5, 5)):
            before, before_keys = values(obj), keys(obj.animation_data.action)
            segment = self.rig('range-segment', [start-1, end-1] if start != end else [start-1])
            segment.pose.bones['Root'].location.x = 50 + start
            segment.pose.bones['Root'].keyframe_insert('location', frame=1)
            frames = [start-1, end-1] if start != end else [start-1]
            held = [{'frame': f, 'state': 'held' if f > start-1 and f < end-1 else 'reconstructed',
                     'pose_source_frame': start-1 if f < end-1 else end-1} for f in range(start-1, end)]
            self.output.retime_reviewed_action(segment, frames, held, 1)
            new = self.output.merge_reviewed_range(obj, segment.animation_data.action, start, end)
            # Staging itself cannot touch the previous Action.
            self.assertEqual(keys(obj.animation_data.action), before_keys)
            obj.animation_data.action = new
            obj.animation_data.action_slot = new.slots[0]
            after = values(obj)
            for frame in before:
                if not start <= frame <= end:
                    self.assertEqual(before[frame], after[frame], frame)
            after_keys = keys(new)
            for channel, points in before_keys.items():
                for point in points:
                    if not start <= point[0][0] <= end:
                        self.assertIn(point, after_keys[channel])
            self.assertNotEqual(before[start][0], after[start][0])
            if end-start > 1:
                self.assertEqual(after[start][0], after[start+1][0])
            self.assertEqual(values(other), independent)
        self.assertIn(original_action.name, bpy.data.actions)
        expected = values(obj)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'ranges.blend')
            bpy.data.libraries.write(path, {obj})
            with bpy.data.libraries.load(path) as (source, target):
                target.objects = source.objects
            restored = target.objects[0]
            bpy.context.collection.objects.link(restored)
            try:
                self.assertEqual(values(restored), expected)
            finally:
                bpy.data.objects.remove(restored, do_unlink=True)

    def test_range_rejects_interpolation_nla_and_channel_changes_without_mutation(self):
        obj = self.rig('unsafe-range', [1, 2])
        action = obj.animation_data.action
        with self.assertRaisesRegex(ValueError, 'edited/interpolated'):
            self.output.merge_reviewed_range(obj, action, 1, 2)
        self.assertIs(obj.animation_data.action, action)
        obj.animation_data.nla_tracks.new()
        with self.assertRaisesRegex(ValueError, 'NLA'):
            self.output.merge_reviewed_range(obj, action, 1, 2)

    def test_draw_refresh_scrub_defers_id_writes_completion_and_failure_preserve_review(self):
        import sys
        ui = sys.modules[self.addon.__name__ + '.blendcap.vta_review_ui']
        view = present_review(Review(), source_dimensions=(100, 100))
        process = SimpleNamespace(poll=mock.Mock(return_value=None), returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            location = ReviewLocation(Path(directory), Path(directory)/'video.mp4', 0, 1)
            log = Path(directory)/'generation.log'
            log.write_text('VTA_OUTPUT=' + str(Path(directory)/'output.json'))
            backend = SimpleNamespace(query=mock.Mock(return_value=view), query_timeline=mock.Mock(return_value=object()))
            writes = []
            in_draw = [True]
            def guarded_write(*args):
                if in_draw[0]:
                    raise RuntimeError('Writing to ID classes in this context is not allowed')
                writes.append('safe-main-thread')
            with mock.patch.object(ui, '_get_backend', return_value=backend), \
                 mock.patch.object(ui, '_location_from_context', return_value=location), \
                 mock.patch.object(ui, '_owning_vse_scene', return_value=bpy.context.scene), \
                 mock.patch.object(ui._review_key_selection, 'reconcile'), \
                 mock.patch.object(ui, '_sync_properties', side_effect=guarded_write), \
                 mock.patch.object(ui, 'ensure_vta_preview_context', side_effect=guarded_write), \
                 mock.patch.object(self.output, 'mark_stale_outputs', side_effect=guarded_write), \
                 mock.patch.object(ui, '_reconstruction_process', process), \
                 mock.patch.object(ui, '_reconstruction_location', location), \
                 mock.patch.object(ui, '_reconstruction_scene', bpy.context.scene), \
                 mock.patch.object(ui, '_reconstruction_log', log), \
                 mock.patch.object(self.output, 'import_reviewed_output', return_value=((), {'characters': []})) as commit:
                try:
                    for frame in (1, 4, 9, 2):
                        bpy.context.scene.frame_set(frame)
                        self.assertTrue(ui.refresh(bpy.context, force=True))
                        self.assertIs(ui._view, view)
                        self.assertEqual(ui._reconstruction_timer(), .5)
                    self.assertFalse(writes)
                    in_draw[0] = False
                    ui._presentation_timer()
                    self.assertEqual(len(writes), 3)
                    process.poll.return_value = 0
                    self.assertIsNone(ui._reconstruction_timer())
                    commit.assert_called_once()
                    self.assertIs(ui._view, view)
                    ui._reconstruction_process = SimpleNamespace(poll=lambda: 1, returncode=1)
                    ui._reconstruction_timer()
                    self.assertIs(ui._view, view)
                    self.assertTrue(ui._status_is_error)
                finally:
                    if bpy.app.timers.is_registered(ui._presentation_timer):
                        bpy.app.timers.unregister(ui._presentation_timer)
                    ui._pending_presentation = None
                    ui._view = ui._timeline = ui._location = None

    @classmethod
    def setUpClass(cls):
        name = 'blendcap_output_test_addon'
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / '__init__.py',
            submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
        import sys
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        cls.addon = module
        cls.output = __import__(name + '.blendcap.vta_skeletal_output', fromlist=['retime_reviewed_action'])
        cls.overlay = __import__(name + '.blendcap.vta_overlay', fromlist=['OverlapCycle'])

    def test_overlap_cycles_and_resets_on_frame_pointer_and_transform(self):
        c = self.overlay.OverlayRegionCandidate
        values = (c('small', '', (2, 2, 10, 10), None, False), c('big', '', (0, 0, 20, 20), None, False))
        cycle = self.overlay.OverlapCycle()
        self.assertEqual([cycle.select(values, (5, 5), 'frame1') for _ in range(3)], ['small', 'big', 'small'])
        self.assertEqual(cycle.select(values, (5, 5), 'frame2'), 'small')
        self.assertEqual(cycle.select(values, (19, 19), 'frame2'), 'big')
        self.assertEqual(cycle.select(values, (5, 5), 'frame2'), 'small')
        scaled = tuple(replace(v, region_box=tuple(x+1 for x in v.region_box)) for v in values)
        self.assertEqual(cycle.select(scaled, (5, 5), 'frame2'), 'small')

    def rig(self, name, frames):
        armature = bpy.data.armatures.new(name)
        obj = bpy.data.objects.new(name, armature)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        root = armature.edit_bones.new('Root')
        root.head, root.tail = (0, 0, 0), (0, 0, 1)
        child = armature.edit_bones.new('Hips')
        child.head, child.tail, child.parent = (0, 0, 1), (0, 0, 2), root
        bpy.ops.object.mode_set(mode='OBJECT')
        for index in range(len(frames)):
            obj.pose.bones['Root'].location.x = index * 2
            obj.pose.bones['Root'].keyframe_insert('location', frame=index+1)
            obj.pose.bones['Hips'].rotation_mode = 'XYZ'
            obj.pose.bones['Hips'].rotation_euler.y = index * .3
            obj.pose.bones['Hips'].keyframe_insert('rotation_euler', frame=index+1)
        self.addCleanup(lambda: bpy.data.objects.remove(obj, do_unlink=True))
        return obj

    def matrix(self, obj):
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return tuple(tuple(v for row in (evaluated.matrix_world @ bone.matrix) for v in row)
                     for bone in evaluated.pose.bones)

    def test_evaluated_hold_pose_root_independence_frame_alignment_and_reload(self):
        obj = self.rig('white', [0, 1, 5])
        other = self.rig('yellow', [0, 1, 2, 3, 4, 5])
        schedule = [{'frame': f, 'state': 'held' if f in (2,3,4) else 'reconstructed',
                     'pose_source_frame': 1 if f in (2,3,4) else f} for f in range(6)]
        self.output.retime_reviewed_action(obj, [0,1,5], schedule, 10)
        other_schedule = [{'frame': f, 'state': 'reconstructed', 'pose_source_frame': f} for f in range(6)]
        self.output.retime_reviewed_action(other, list(range(6)), other_schedule, 10)
        bpy.context.scene.frame_set(11)
        held, previous_other = self.matrix(obj), self.matrix(other)
        for frame in (12,13,14):
            bpy.context.scene.frame_set(frame)
            self.assertEqual(self.matrix(obj), held)
            self.assertNotEqual(self.matrix(other), previous_other)
            previous_other = self.matrix(other)
        bpy.context.scene.frame_set(15)
        self.assertNotEqual(self.matrix(obj), held)
        # Persist actual armature animation in a disposable library; read it back.
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'rig.blend')
            bpy.data.libraries.write(path, {obj})
            with bpy.data.libraries.load(path) as (source, target):
                target.objects = source.objects
            restored = target.objects[0]
            bpy.context.collection.objects.link(restored)
            try:
                bpy.context.scene.frame_set(12)
                self.assertEqual(self.matrix(restored), held)
            finally:
                bpy.data.objects.remove(restored, do_unlink=True)

    def test_unavailable_pre_pose_is_hidden_not_rest_animation(self):
        obj = self.rig('no-prior', [2])
        schedule = [{'frame': f, 'state': 'unavailable' if f < 2 else 'reconstructed',
                     'pose_source_frame': None if f < 2 else 2} for f in range(3)]
        self.output.retime_reviewed_action(obj, [2], schedule, 1)
        bpy.context.scene.frame_set(1)
        self.assertTrue(obj.hide_render)
        bpy.context.scene.frame_set(3)
        self.assertFalse(obj.hide_render)

    def test_import_update_replaces_only_owned_current_scene_and_preserves_selection(self):
        import hashlib
        import json
        bvh = '''HIERARCHY
ROOT Root
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Hips
 {
  OFFSET 0 1 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  End Site
  {
   OFFSET 0 1 0
  }
 }
}
MOTION
Frames: 2
Frame Time: 0.1
0 0 0 0 0 0 0 0 0
1 0 0 0 0 0 0 10 0
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root/'reconstruction/run-test'
            run.mkdir(parents=True)
            (root/'analysis').mkdir()
            (root/'project_state.json').write_text('{}')
            (root/'analysis/a.json').write_text('{}')
            pose_path = run/'pose.bvh'
            pose_path.write_text(bvh)
            schedule = [{'frame': f, 'state':'reconstructed', 'pose_source_frame':f} for f in (0,1)]
            plan = {'project_id':'p', 'source_id':'s', 'characters':[{'character_id':'c','display_name':'white','samples':[]}]}
            report = {'schema':'vta-reviewed-skeletal-output', 'version':1, 'plan':plan, 'characters':[
                {'character_id':'c','display_name':'white','source_frames':[0,1],'schedule':schedule,
                 'bvh':'pose.bvh','bvh_sha256':hashlib.sha256(pose_path.read_bytes()).hexdigest()}]}
            output = run/'output.json'
            output.write_text(json.dumps(report))
            backend = SimpleNamespace(_load_context=lambda location:(None,SimpleNamespace(revision_id='a'),'a'),
                _core=SimpleNamespace(build_skeletal_plan=lambda *_args:plan, resolve_pose_schedule=lambda *_args:schedule))
            location = ReviewLocation(root, root/'video.mp4', 0, 20)
            # Ordinary user object with a colliding intended display name survives.
            user = bpy.data.objects.new('VTA white [c]', None)
            bpy.context.collection.objects.link(user)
            other_scene = bpy.data.scenes.new('other-output-scene')
            try:
                first, _ = self.output.import_reviewed_output(output, location, backend)
                old_name = first[0].name
                first[0].select_set(True)
                bpy.context.view_layer.objects.active = first[0]
                outside = bpy.data.objects.new('other-scene-owned', bpy.data.armatures.new('other-owned'))
                outside['vta_project_id'], outside['vta_source_id'], outside['vta_character_id'] = 'p','s','c'
                other_scene.collection.objects.link(outside)
                second, _ = self.output.import_reviewed_output(output, location, backend)
                self.assertEqual(len(second), 1)
                self.assertIs(bpy.context.view_layer.objects.active, second[0])
                self.assertTrue(second[0].select_get())
                self.assertIn(user.name, bpy.data.objects)
                self.assertIn(outside.name, other_scene.objects)
                self.assertEqual(second[0]['vta_character_id'], 'c')
                self.assertFalse(second[0]['vta_output_stale'])
                (root/'project_state.json').write_text('{"changed":true}')
                self.assertTrue(self.output.mark_stale_outputs(root))
                self.assertTrue(second[0]['vta_output_stale'])
                # Malformed/stale publication preserves the existing real rig.
                report['plan'] = {**plan, 'source_id':'changed'}
                output.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'stale'):
                    self.output.import_reviewed_output(output, location, backend)
                self.assertIn(second[0].name, bpy.data.objects)
                report['plan'] = plan
                output.write_text(json.dumps(report))
                before = (set(bpy.data.objects), set(bpy.data.actions), set(bpy.data.armatures), set(bpy.data.cameras))
                real_import = self.output._import_bvh
                def fail_after_creation(*args, **kwargs):
                    real_import(*args, **kwargs)
                    # Camera data owns the staged lens Action until removed.
                    camera = bpy.data.cameras.new('injected staged camera')
                    camera.keyframe_insert('lens', frame=1)
                    raise RuntimeError('Injected staging failure after real BVH import')
                with mock.patch.object(self.output, '_import_bvh', side_effect=fail_after_creation):
                    with self.assertRaisesRegex(RuntimeError, 'staging failure'):
                        self.output.import_reviewed_output(output, location, backend)
                self.assertEqual(before, (set(bpy.data.objects), set(bpy.data.actions), set(bpy.data.armatures), set(bpy.data.cameras)))
                self.assertIn(second[0].name, bpy.data.objects)
                # No-op/failed importer must never retime the active prior rig.
                old_action = second[0].animation_data.action
                bpy.context.view_layer.objects.active = second[0]
                with mock.patch.object(self.output, '_import_bvh'):
                    with self.assertRaisesRegex(ValueError, 'did not create'):
                        self.output.import_reviewed_output(output, location, backend)
                self.assertIs(second[0].animation_data.action, old_action)
                # Inject a commit-time metadata failure after action assignment.
                partial_plan = {**plan, 'source_range': [0, 1]}
                partial_backend = SimpleNamespace(_load_context=backend._load_context,
                    _core=SimpleNamespace(build_skeletal_plan=lambda *_args, **_kwargs: partial_plan,
                                          resolve_pose_schedule=lambda *_args: schedule))
                report.update(version=2, plan=partial_plan)
                output.write_text(json.dumps(report))
                second[0]['vta_output_history'] = 'invalid history JSON'
                before = (set(bpy.data.objects), set(bpy.data.actions), set(bpy.data.armatures))
                with self.assertRaises(ValueError):
                    self.output.import_reviewed_output(output, location, partial_backend)
                self.assertIs(second[0].animation_data.action, old_action)
                self.assertEqual(second[0]['vta_output_history'], 'invalid history JSON')
                self.assertEqual(before, (set(bpy.data.objects), set(bpy.data.actions), set(bpy.data.armatures)))
            finally:
                for obj in tuple(bpy.data.objects):
                    if obj.get('vta_project_id') == 'p' or obj == user:
                        bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.scenes.remove(other_scene)
