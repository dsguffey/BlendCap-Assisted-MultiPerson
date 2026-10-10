"""Source-camera registration, not visual object-position adjustment."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class CameraRegistrationTests(unittest.TestCase):
    def test_scene_rebase_projects_origin_to_bottom_centre_and_survives_reload(self):
        from mathutils import Vector
        from bpy_extras.object_utils import world_to_camera_view
        ref = {'reference': {'translation_m': [0, -5, 1.5], 'version': 1}}
        plan = {'project_id': 'scene-reference-test', 'source_id': 's', 'width': 1000,
                'height': 600, 'source_range': [0, 1]}
        scene = bpy.context.scene
        before_render = scene.render.resolution_x, scene.render.resolution_y
        with tempfile.TemporaryDirectory() as directory:
            image = bpy.data.images.new('reference-source', width=10, height=10)
            image.filepath_raw = str(Path(directory)/'source.png')
            image.file_format = 'PNG'
            image.save()
            location = SimpleNamespace(source_path=Path(image.filepath_raw), blender_frame=1, source_frame=0)
            _, camera = self.output.stage_source_camera(plan, [{'camera_registration': {
                'samples': [{'frame': 0, 'focal_px': 1000}]}}], location, False, None, ref)
            rigs = [self.helper.rig('reference-left', [0]), self.helper.rig('reference-right', [0])]
            try:
                scene.render.resolution_x, scene.render.resolution_y = 1000, 600
                scene.frame_set(1)
                uv = world_to_camera_view(scene, camera, Vector((0, 0, 0)))
                self.assertAlmostEqual(uv.x, .5, places=6)
                self.assertAlmostEqual(uv.y, 0, places=6)
                points = [Vector((-1, 4, .5)), Vector((1, 6, .5))]
                for rig, point in zip(rigs, points):
                    self.output.register_camera_root(rig, [{'pelvis_world_m': list(point)}])
                    rig.matrix_world = self.output.scene_translation(ref)
                bpy.context.view_layer.update()
                worlds = [o.matrix_world @ o.pose.bones['Hips'].head for o in rigs]
                self.assertLess(((worlds[1]-worlds[0])-(points[1]-points[0])).length, 1e-5)
                for point, world in zip(points, worlds):
                    uv = world_to_camera_view(scene, camera, world)
                    self.assertAlmostEqual(uv.x*1000, 500+1000*point.x/point.y, places=3)
                    self.assertAlmostEqual((1-uv.y)*600, 300-1000*point.z/point.y, places=3)
                saved = Path(directory)/'reference.blend'
                bpy.data.libraries.write(str(saved), {camera, *rigs})
                with bpy.data.libraries.load(str(saved)) as (_, target):
                    target.objects = [camera.name]
                restored = target.objects[0]
                try:
                    bpy.context.collection.objects.link(restored)
                    bpy.context.view_layer.update()
                    self.assertEqual(restored.matrix_world, camera.matrix_world)
                    self.assertEqual(json.loads(restored.data['vta_scene_reference']), ref)
                finally:
                    bpy.data.objects.remove(restored, do_unlink=True)
            finally:
                data = camera.data
                bpy.data.objects.remove(camera, do_unlink=True)
                bpy.data.cameras.remove(data)
                bpy.data.images.remove(image)
                scene.render.resolution_x, scene.render.resolution_y = before_render

    def test_frame_zero_is_hidden_without_a_legacy_pose_guard(self):
        rig = self.helper.rig('source-limited', [0, 1, 2, 3])
        schedule = [{'frame': f, 'state': 'reconstructed', 'pose_source_frame': f} for f in range(4)]
        self.output.retime_reviewed_action(rig, list(range(4)), schedule, 1)
        segment = self.helper.rig('source-limited-update', [0])
        self.output.retime_reviewed_action(segment, [0], schedule[:1], 1)
        old = rig.animation_data.action
        outside = {(fc.data_path, fc.array_index): fc.evaluate(3)
                   for fc in self.output._iter_action_fcurves(old)}
        new = self.output.merge_reviewed_range(rig, segment.animation_data.action, 1, 1, source_bounds=(1, 4))
        self.output.limit_source_visibility(new, 1, 4)
        for fc in self.output._iter_action_fcurves(new):
            self.assertEqual(fc.evaluate(3), outside[fc.data_path, fc.array_index])
            if fc.data_path in ('hide_viewport', 'hide_render'):
                self.assertEqual(fc.evaluate(0), 1)
                self.assertEqual(fc.evaluate(1), 0)
                self.assertEqual(fc.evaluate(5), 1)
            else:
                self.assertTrue(all(1 <= p.co.x <= 4 for p in fc.keyframe_points))
        self.assertIs(rig.animation_data.action, old)

    def test_fixed_reference_reused_despite_new_depth_and_detects_provenance_change(self):
        sample = {'frame': 0, 'pelvis_world_m': [0, 5, 0], 'focal_px': 1000}
        reference = {'reference_samples': [{'character_id': 'a', 'samples': [sample]}],
                     'translation_m': [0, -5, 1.5]}
        core = SimpleNamespace(scene_reference_from_samples=mock.Mock(return_value=reference))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'output.json'
            path.write_text(json.dumps({'plan': {'source_video_sha256': 'video'},
                'characters': [{'character_id': 'a', 'camera_registration': {'samples': [sample]}}]}))
            saved = {'reference': reference, 'source_video_sha256': 'video',
                     'output_file': str(path), 'output_sha256': self.output._hash(path)}
            camera = SimpleNamespace(data={'vta_scene_reference': json.dumps(saved)})
            plan = {'width': 1000, 'height': 600, 'source_video_sha256': 'video'}
            self.assertEqual(self.output.resolve_scene_reference(plan, [], camera, core, 'new-output'), saved)
            core.scene_reference_from_samples.assert_called_once_with(reference['reference_samples'], 1000, 600)
            altered = json.loads(camera.data['vta_scene_reference'])
            altered['reference']['translation_m'][1] = -8
            camera.data['vta_scene_reference'] = json.dumps(altered)
            with self.assertRaisesRegex(ValueError, 'incompatible or was edited'):
                self.output.resolve_scene_reference(plan, [], camera, core, 'new-output')
            camera.data['vta_scene_reference'] = json.dumps(saved)
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'reconstruction changed'):
                self.output.resolve_scene_reference(plan, [], camera, core, 'new-output')

    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        cls.output = BlenderOutputTests.output
        cls.helper = BlenderOutputTests()

    def tearDown(self):
        self.helper.tearDown()

    def test_raw_pelvis_alignment_keeps_rotation_and_characters_distinct(self):
        from mathutils import Vector
        left, right = self.helper.rig('left', [0, 1]), self.helper.rig('right', [0, 1])
        # This rig helper uses a nontrivial Root rest basis; correction must
        # be transformed into Root-local translation, not assigned world XYZ.
        original_rotation = {index: next(fc for fc in self.output._iter_action_fcurves(left.animation_data.action)
            if fc.data_path == 'pose.bones["Hips"].rotation_euler' and fc.array_index == 1).evaluate(index)
            for index in (1, 2)}
        for obj, x in ((left, -.5), (right, .5)):
            samples = [{'frame': f, 'pelvis_world_m': [x, 5, 1]} for f in (0, 1)]
            self.output.register_camera_root(obj, samples)
            for index in (1, 2):
                bpy.context.scene.frame_set(index)
                bpy.context.view_layer.update()
                self.assertLess((obj.pose.bones['Hips'].head - Vector([x, 5, 1])).length, 1e-5)
                if obj is left:
                    self.assertEqual(obj.pose.bones['Hips'].rotation_euler.y, original_rotation[index])
        left.location.x = 2
        with self.assertRaisesRegex(ValueError, 'unedited object'):
            self.output.register_camera_root(left, samples)

    def test_camera_matches_pixel_projection_and_does_not_modify_user_camera(self):
        from mathutils import Vector
        from bpy_extras.object_utils import world_to_camera_view
        import math
        plan = {'project_id': 'test-camera', 'source_id': 's', 'width': 1000,
                'height': 600, 'source_range': [0, 1]}
        records = [{'camera_registration': {'samples': [{'frame': 0, 'focal_px': 1000}]}}]
        scene = bpy.context.scene
        user = scene.camera
        old_render = scene.render.resolution_x, scene.render.resolution_y
        with tempfile.TemporaryDirectory() as directory:
            image = bpy.data.images.new('source-test', width=10, height=10)
            image.filepath_raw = str(Path(directory)/'image.png')
            image.file_format = 'PNG'
            image.save()
            location = SimpleNamespace(source_path=Path(image.filepath_raw), blender_frame=1, source_frame=0)
            before, camera = self.output.stage_source_camera(plan, records, location, False, None)
            try:
                self.assertIsNone(before)
                self.assertIs(scene.camera, user)
                scene.render.resolution_x, scene.render.resolution_y = 1000, 600
                scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
                scene.frame_set(1)
                for point, pixels in (([-1,4,-.5], [250,425]), ([1,4,.5], [750,175])):
                    uv = world_to_camera_view(scene, camera, Vector(point))
                    self.assertAlmostEqual(uv.x*1000, pixels[0], places=3)
                    self.assertAlmostEqual((1-uv.y)*600, pixels[1], places=3)
                self.assertEqual(camera.data.background_images[0].image_user.frame_start, 1)
                camera['vta_source_id'] = 'other'  # no ambiguous ownership in second stage
                with self.assertRaisesRegex(ValueError, 'disagree'):
                    self.output.stage_source_camera(plan, records + [{'camera_registration': {
                        'samples': [{'frame':0, 'focal_px':1100}]}}], location, False, None)
            finally:
                data = camera.data
                bpy.data.objects.remove(camera, do_unlink=True)
                bpy.data.cameras.remove(data)
                scene.render.resolution_x, scene.render.resolution_y = old_render
                bpy.data.images.remove(image)

    def test_missing_or_invalid_focal_metadata_fails_without_guessing(self):
        import numpy as np
        from contextlib import nullcontext
        from blendcap_vta_reconstruct_worker import infer_reviewed_pose
        pose = {'bbox': [0,0,10,10], 'pred_global_rots': np.tile(np.eye(3),(2,1,1)),
                'pred_joint_coords': np.zeros((2,3)), 'pred_cam_t': np.ones(3)}
        estimator = SimpleNamespace(process_one_image=lambda *a, **kw: [pose])
        for focal in (None, 0, float('nan'), [1,2]):
            pose['focal_length'] = focal
            with self.subTest(focal=focal), self.assertRaisesRegex(RuntimeError, 'focal_length'):
                infer_reviewed_pose(estimator, np, SimpleNamespace(inference_mode=nullcontext),
                    np.zeros((10,10,3)), {'box_xyxy':[0,0,10,10]}, 10)

    def test_camera_range_staging_preserves_outside_lens_and_rejects_edits(self):
        plan = {'project_id':'test-range-camera', 'source_id':'s', 'width':1000,
                'height':600, 'source_range':[0,9]}
        with tempfile.TemporaryDirectory() as directory:
            image = bpy.data.images.new('range-source', width=10, height=10)
            image.filepath_raw = str(Path(directory)/'source.png')
            image.file_format = 'PNG'
            image.save()
            location = SimpleNamespace(source_path=Path(image.filepath_raw), blender_frame=1, source_frame=0)
            _, prior = self.output.stage_source_camera(plan, [{'camera_registration': {
                'samples':[{'frame':0,'focal_px':500}]}}], location, False, None)
            data_before = prior.data
            _, fresh = self.output.stage_source_camera({**plan,'source_range':[4,5]}, [
                {'camera_registration': {'samples':[{'frame':4,'focal_px':1000}]}}], location, True, [5,6])
            try:
                old = next(self.output._iter_action_fcurves(prior.data.animation_data.action))
                new = next(self.output._iter_action_fcurves(fresh.data.animation_data.action))
                for frame in range(1,11):
                    # Frame 4 has new reconstruction calibration; frame 5
                    # has none, so an existing source lens must not be guessed.
                    self.assertEqual(new.evaluate(frame), 36 if frame == 5 else old.evaluate(frame))
                self.assertIs(prior.data, data_before)  # staging is not a commit
                fresh['vta_source_id'] = 'staged-other'
                prior.data.shift_x = .1
                with self.assertRaisesRegex(ValueError, 'was edited'):
                    self.output.stage_source_camera(plan, [], location, False, None)
            finally:
                for camera in (prior, fresh):
                    data = camera.data
                    bpy.data.objects.remove(camera, do_unlink=True)
                    bpy.data.cameras.remove(data)
                bpy.data.images.remove(image)

    def test_registration_is_bound_to_npz_and_cannot_be_tampered(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'pose.npz'
            np.savez(path, frame_indices=[0], joint_coords=np.zeros((1,2,3)), cam_t=[[0,0,5]], focal_length=[500])
            sample = {'frame':0,'pelvis_world_m':[0,5,0],'focal_px':500.,'principal_point_px':[500.,300.]}
            core = SimpleNamespace(source_camera_sample=lambda *a: sample)
            record = {'source_frames':[0],'npz':'pose.npz','npz_sha256':self.output._hash(path),
                'camera_registration': {'version':1,
                    'convention':'camera-centred-metres; world=(cv-x,cv-z,-cv-y); centred-square-pixels', 'samples':[sample]}}
            plan = {'width':1000,'height':600}
            self.assertEqual(self.output.validate_camera_registration(record,plan,root,core),[sample])
            altered = json.loads(json.dumps(record))
            altered['camera_registration']['samples'][0]['focal_px'] = 501
            with self.assertRaisesRegex(ValueError, 'raw reconstruction'):
                self.output.validate_camera_registration(altered,plan,root,core)
            path.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'NPZ hash changed'):
                self.output.validate_camera_registration(record,plan,root,core)
