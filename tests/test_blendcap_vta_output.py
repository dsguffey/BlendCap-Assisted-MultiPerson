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
                'pred_joint_coords': np.zeros((2,3)), 'pred_cam_t': np.ones(3)}
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
            finally:
                for obj in tuple(bpy.data.objects):
                    if obj.get('vta_project_id') == 'p' or obj == user:
                        bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.scenes.remove(other_scene)
