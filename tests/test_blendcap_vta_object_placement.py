"""Object Mode placement is user-owned; generated bones/visibility are not."""
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class ObjectPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        cls.output = BlenderOutputTests.output

    def setUp(self):
        import numpy as np
        from blendcap_vta_review import ReviewLocation
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before = {kind: set(getattr(bpy.data, kind)) for kind in
                       ('objects', 'armatures', 'actions', 'cameras', 'images')}
        self.camera_before = bpy.context.scene.camera
        self.frame_before = bpy.context.scene.frame_current
        self.render_before = (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y)
        self.addCleanup(self.cleanup)
        self.pid = 'object-placement-' + uuid4().hex
        self.location = ReviewLocation(self.root, self.root/'source.png', 0, 1)
        image = bpy.data.images.new('placement source', width=1000, height=600)
        image.filepath_raw = str(self.location.source_path)
        image.file_format = 'PNG'
        image.save()
        (self.root/'analysis').mkdir()
        (self.root/'analysis/a.json').write_text('{}')
        (self.root/'project_state.json').write_text('{}')
        self.plan = None
        self.schedule = None
        def sample(frame, point, focal, width, height):
            return {'frame': int(frame), 'pelvis_world_m': [float(point[0]), float(point[2]), -float(point[1])],
                    'focal_px': float(focal), 'principal_point_px': [width/2, height/2]}
        def reference(characters, width, height):
            first = min(s['frame'] for s in characters[0]['samples'])
            selected = sorted([{'character_id': c['character_id'],
                                'samples': [next(s for s in c['samples'] if s['frame'] == first)]}
                               for c in characters], key=lambda c:c['character_id'])
            return {'translation_m': [0, -5, 1.5], 'reference_samples': selected}
        self.sample = sample
        self.backend = SimpleNamespace(_load_context=lambda _: (None, SimpleNamespace(
            revision_id='a', source=SimpleNamespace(start_frame=0, end_frame=5)), 'a'),
            _core=SimpleNamespace(build_skeletal_plan=lambda *args, **kw:self.plan,
                resolve_pose_schedule=lambda *args, **kw:self.schedule,
                reconstruction_fingerprint=lambda *args:'legacy-per-frame-intrinsics',
                require_compatible_reconstruction=lambda *args, **kw:None,
                source_camera_sample=sample, scene_reference_from_samples=reference))
        self.report = self.make_report(False)
        self.rigs, _ = self.output.import_reviewed_output(self.report, self.location, self.backend)
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()

    def cleanup(self):
        bpy.context.scene.camera = self.camera_before
        for kind in self.before:
            for item in set(getattr(bpy.data, kind)) - self.before[kind]:
                if kind == 'objects':
                    bpy.data.objects.remove(item, do_unlink=True)
                elif item.users == 0:
                    getattr(bpy.data, kind).remove(item)
        bpy.context.scene.frame_set(self.frame_before)
        bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y = self.render_before

    def make_report(self, bounded, displacement=0, unavailable=False, source_frames=None):
        import numpy as np
        frames = list(source_frames) if source_frames is not None else [2, 3] if bounded else list(range(6))
        self.schedule = [{'frame':f, 'state':'unavailable' if unavailable else 'reconstructed',
                          'pose_source_frame':None if unavailable else f} for f in frames]
        self.plan = {'version':4, 'generation_scope':'range' if bounded else 'whole-source', 'project_id':self.pid, 'source_id':'s',
            'source_video_sha256':self.output._hash(self.location.source_path),
            'width':1000, 'height':600, 'source_range':[frames[0], frames[-1]],
            'characters':[{'character_id':c, 'display_name':c, 'samples':[]} for c in ('left','right')]}
        run = self.root/'reconstruction'/uuid4().hex
        run.mkdir(parents=True)
        records = []
        for index, cid in enumerate(('left','right')):
            samples = [self.sample(f, [index*2-1+displacement+f*.1, 0, 5], 500, 1000, 600) for f in frames]
            joint = np.zeros((len(frames), 2, 3))
            camera = [[s['pelvis_world_m'][0], 0, 5] for s in samples]
            np.savez(run/(cid+'.npz'), frame_indices=frames, joint_coords=joint,
                     cam_t=camera, focal_length=[500]*len(frames))
            motion = '\n'.join(f'{f*.1} 0 0 0 0 0 0 {f*5} 0' for f in frames)
            (run/(cid+'.bvh')).write_text('''HIERARCHY
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
Frames: ''' + str(len(frames)) + '\nFrame Time: 0.1\n' + motion + '\n')
            empty = unavailable and index == 0
            records.append({'character_id':cid, 'display_name':cid, 'source_frames':[] if empty else frames,
                'schedule':self.schedule if not unavailable or empty else [
                    {'frame':f,'state':'reconstructed','pose_source_frame':f} for f in frames],
                'bvh':None if empty else cid+'.bvh', 'bvh_sha256':self.output._hash(run/(cid+'.bvh')),
                'npz':cid+'.npz', 'npz_sha256':self.output._hash(run/(cid+'.npz')),
                'camera_registration':None if empty else {'version':1,
                    'convention':'camera-centred-metres; world=(cv-x,cv-z,-cv-y); centred-square-pixels', 'samples':samples}})
        if unavailable:
            # The importer asks schedule separately per Character.
            self.backend._core.resolve_pose_schedule = lambda samples, successful, **kw: [
                {'frame':f,'state':'reconstructed' if successful else 'unavailable',
                 'pose_source_frame':f if successful else None} for f in frames]
        report = run/'output.json'
        report.write_text(json.dumps({'schema':'vta-reviewed-skeletal-output', 'version':4,
                                     'plan':self.plan, 'characters':records}))
        return report

    def curves(self, rig, user_only=False):
        return {(fc.data_path, fc.array_index): (fc.extrapolation, fc.mute, fc.lock,
            tuple((tuple(p.co), p.interpolation, p.easing, p.handle_left_type, p.handle_right_type,
                   tuple(p.handle_left), tuple(p.handle_right)) for p in fc.keyframe_points))
            for fc in self.output._iter_action_fcurves(rig.animation_data.action)
            if not user_only or self.output._user_transform_curve(fc)}

    def placement(self, rig):
        return (tuple(rig.location), rig.rotation_mode, tuple(rig.rotation_euler),
                tuple(rig.rotation_quaternion), tuple(rig.rotation_axis_angle), tuple(rig.scale))

    def regenerate(self, bounded, displacement=1):
        report = self.make_report(bounded, displacement)
        return self.output.import_reviewed_output(report, self.location, self.backend)[0]

    def assert_user_preserved(self, rig, before, placement, pointer):
        self.assertEqual(rig.as_pointer(), pointer)
        self.assertEqual(self.curves(rig, True), before)
        self.assertEqual(self.placement(rig), placement)

    def test_static_translation_bounded_and_full(self):
        self.check_static('location', (.25, -.4, .3))

    def test_static_rotation_bounded_and_full(self):
        self.check_static('rotation_euler', (.1, -.2, math.tau+.3))

    def test_static_scale_bounded_and_full(self):
        self.check_static('scale', (1.1, .9, 1.2))

    def check_static(self, channel, value):
        rig = self.rigs[0]
        rig.rotation_mode = 'ZYX'
        setattr(rig, channel, value)
        expected = self.placement(rig)
        pointer = rig.as_pointer()
        for bounded in (True, False, True, False):
            self.assertIs(self.regenerate(bounded)[0], rig)
            self.assert_user_preserved(rig, {}, expected, pointer)

    def check_keys(self, channel, mode='XYZ'):
        rig = self.rigs[0]
        rig.rotation_mode = mode
        initial = tuple(getattr(rig, channel))
        for frame in (-4, 1, 3, 4, 6, 20):
            values = list(initial)
            if frame != 1:
                values[-1] += .15*frame
            setattr(rig, channel, values)
            rig.keyframe_insert(channel, frame=frame)
        for fc in self.output._iter_action_fcurves(rig.animation_data.action):
            if self.output._user_transform_curve(fc):
                fc.extrapolation = 'LINEAR'
                for p in fc.keyframe_points:
                    p.interpolation = 'BEZIER'
                    p.handle_left_type = p.handle_right_type = 'FREE'
                    p.handle_left = (p.co.x-.3, p.co.y-.17)
                    p.handle_right = (p.co.x+.4, p.co.y+.21)
        bpy.context.scene.frame_set(1)  # previous silent-loss case: canonical here
        bpy.context.view_layer.update()
        placement, keys, pointer = self.placement(rig), self.curves(rig, True), rig.as_pointer()
        generated_before = {k:v for k,v in self.curves(rig).items() if k not in keys}
        for bounded in (False, True, False, True):
            regenerated = self.regenerate(bounded)
            self.assertIs(regenerated[0], rig)
            self.assert_user_preserved(rig, keys, placement, pointer)
        self.assertNotEqual({k:v for k,v in self.curves(rig).items() if k not in keys}, generated_before)

    def test_keyed_translation_exact_bounded_full_and_repeat(self):
        self.check_keys('location')

    def test_keyed_euler_rotation_and_mode(self):
        self.check_keys('rotation_euler', 'ZYX')

    def test_keyed_quaternion_rotation_and_mode(self):
        self.check_keys('rotation_quaternion', 'QUATERNION')

    def test_keyed_axis_angle_rotation_and_mode(self):
        self.check_keys('rotation_axis_angle', 'AXIS_ANGLE')

    def test_keyed_scale_exact_bounded_full_and_repeat(self):
        self.check_keys('scale')

    def test_default_auto_handles_and_linear_keys_preserved(self):
        rig = self.rigs[0]
        for frame in (-2, 1, 3, 4, 9):
            rig.location.x = frame*.1
            rig.keyframe_insert('location', frame=frame)
        for fc in self.output._iter_action_fcurves(rig.animation_data.action):
            if self.output._user_transform_curve(fc):
                fc.keyframe_points[2].interpolation = 'LINEAR'
        bpy.context.scene.frame_set(1)
        keys, placement, pointer = self.curves(rig, True), self.placement(rig), rig.as_pointer()
        for bounded in (True, False, True):
            self.regenerate(bounded)
            self.assert_user_preserved(rig, keys, placement, pointer)

    def test_save_reload_then_bounded_full_preserves_object_keys(self):
        self.check_keys('location')
        rig = self.rigs[0]
        scene_root = rig.parent
        keys, placement = self.curves(rig, True), self.placement(rig)
        path = self.root/'placement.blend'
        bpy.data.libraries.write(str(path), {rig})
        with bpy.data.libraries.load(str(path)) as (_, target):
            target.objects = [rig.name]
        restored = target.objects[0]
        # This is an object-only library append, not a whole-scene reload. Its
        # parent dependency is also appended; explicitly reconnect the tested
        # object to this scene's existing owned identity container rather than
        # expecting production to merge duplicate ownership implicitly.
        appended_root = restored.parent
        restored.parent = scene_root
        if appended_root is not None and appended_root != scene_root:
            bpy.data.objects.remove(appended_root, do_unlink=True)
        bpy.data.objects.remove(rig, do_unlink=True)
        bpy.context.collection.objects.link(restored)
        self.rigs = (restored, self.rigs[1])
        bpy.context.scene.frame_set(1)
        pointer = restored.as_pointer()
        for bounded in (True, False):
            self.assertIs(self.regenerate(bounded)[0], restored)
            self.assert_user_preserved(restored, keys, placement, pointer)

    def test_bounded_generated_outside_and_other_character_placement_preserved(self):
        rig, other = self.rigs
        other.location.z += .7
        other_before = self.placement(other)
        before = self.curves(rig)
        self.regenerate(True, 2)
        after = self.curves(rig)
        for channel, (_, _, _, points) in before.items():
            for point in points:
                if not 3 <= point[0][0] <= 4:
                    # Generated CONSTANT handles can be recalculated; their
                    # outside values/interpolation must remain unchanged.
                    self.assertIn(point[:2], [p[:2] for p in after[channel][3]])
        self.assertNotEqual(before, after)
        self.assertEqual(self.placement(other), other_before)

    def test_uncorrected_camera_projection_and_internal_root_update(self):
        from bpy_extras.object_utils import world_to_camera_view
        for bounded in (True, False):
            rigs = self.regenerate(bounded, .5)
            for frame in (3, 4):
                bpy.context.scene.frame_set(frame)
                bpy.context.view_layer.update()
                for index, rig in enumerate(rigs):
                    point = rig.matrix_world @ rig.pose.bones['Hips'].head
                    uv = world_to_camera_view(bpy.context.scene, bpy.context.scene.camera, point)
                    self.assertAlmostEqual(uv.x*1000, 500+500*(index*2-1+.5+(frame-1)*.1)/5, places=3)
                    self.assertAlmostEqual(uv.y, .5, places=5)

    def test_translation_additive_without_altering_registered_bone_motion(self):
        from mathutils import Vector
        rig = self.rigs[0]
        rig.location += Vector((.25, -.4, .3))
        self.regenerate(False, 1)
        bpy.context.scene.frame_set(3)
        bpy.context.view_layer.update()
        world = rig.matrix_world @ rig.pose.bones['Hips'].head
        expected = Vector((.2, 5, 0)) + Vector((0, -5, 1.5)) + Vector((.25, -.4, .3))
        self.assertLess((world-expected).length, 1e-5)

    def test_unavailable_bounded_span_does_not_trim_user_keys(self):
        rig = self.rigs[0]
        for frame in (-1, 3, 10):
            rig.location.x = frame*.1
            rig.keyframe_insert('location', frame=frame)
        keys = self.curves(rig, True)
        report = self.make_report(True, unavailable=True)
        self.output.import_reviewed_output(report, self.location, self.backend)
        self.assertEqual(self.curves(rig, True), keys)
        bpy.context.scene.frame_set(3)
        self.assertTrue(rig.hide_render)

    def check_reject(self, edit, error):
        rig = self.rigs[0]
        edit(rig)
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)
        bpy.context.view_layer.update()
        before = self.curves(rig), self.placement(rig), rig.animation_data.action
        objects = set(bpy.data.objects)
        for bounded in (True, False):
            with self.assertRaisesRegex(ValueError, error):
                self.regenerate(bounded)
            self.assertEqual((self.curves(rig), self.placement(rig), rig.animation_data.action), before)
            self.assertEqual(set(bpy.data.objects), objects)

    def test_manual_skeletal_interpolation_rejected_both_paths(self):
        def edit(rig):
            fc = next(fc for fc in self.output._iter_action_fcurves(rig.animation_data.action) if fc.data_path.startswith('pose.'))
            fc.keyframe_points[0].interpolation = 'BEZIER'
        self.check_reject(edit, 'edited/interpolated')

    def test_commit_failure_rolls_back_without_decomposing_user_transforms(self):
        rig = self.rigs[0]
        rig.rotation_mode = 'ZYX'
        rig.rotation_euler.z = math.tau+.4
        rig.scale = (1.1, .9, 1.2)
        rig.keyframe_insert('location', frame=1)
        rig.location.x += .3
        rig.keyframe_insert('location', frame=4)
        bpy.context.scene.frame_set(1)
        # Fail after the first object's Action has been committed.
        self.rigs[1]['vta_output_history'] = 'invalid JSON'
        before = [(self.curves(o), self.placement(o), o.animation_data.action) for o in self.rigs]
        objects, actions = set(bpy.data.objects), set(bpy.data.actions)
        for bounded in (True, False):
            with self.assertRaises(ValueError):
                self.regenerate(bounded)
            self.assertEqual([(self.curves(o), self.placement(o), o.animation_data.action) for o in self.rigs], before)
            self.assertEqual(set(bpy.data.objects), objects)
            self.assertEqual(set(bpy.data.actions), actions)

    def test_multiple_action_slots_rejected_both_paths(self):
        self.check_reject(lambda rig:rig.animation_data.action.slots.new('OBJECT', 'extra'), 'edited/interpolated')

    def test_object_constraints_rejected_both_paths(self):
        self.check_reject(lambda rig:rig.constraints.new('COPY_LOCATION'), 'constraints')

    def test_bone_constraints_rejected_both_paths(self):
        self.check_reject(lambda rig:rig.pose.bones['Root'].constraints.new('COPY_LOCATION'), 'constraints')

    def test_parenting_rejected_both_paths(self):
        self.check_reject(lambda rig:setattr(rig, 'parent', self.rigs[1]), 'parents')

    def test_object_drivers_rejected_both_paths(self):
        self.check_reject(lambda rig:rig.driver_add('location', 0), 'drivers')

    def test_nla_rejected_both_paths(self):
        self.check_reject(lambda rig:rig.animation_data.nla_tracks.new(), 'NLA')

    def test_object_curve_modifiers_rejected_both_paths(self):
        def edit(rig):
            rig.keyframe_insert('location', frame=1)
            next(fc for fc in self.output._iter_action_fcurves(rig.animation_data.action) if fc.data_path == 'location').modifiers.new('NOISE')
        self.check_reject(edit, 'edited/interpolated')

    def test_custom_object_channels_not_allowlisted(self):
        def edit(rig):
            rig['custom'] = 1.
            rig.keyframe_insert('["custom"]', frame=1)
            next(fc for fc in self.output._iter_action_fcurves(rig.animation_data.action) if fc.data_path == '["custom"]').keyframe_points[0].interpolation = 'CONSTANT'
        self.check_reject(edit, 'channels differ')

    def test_delta_transforms_not_allowlisted(self):
        def edit(rig):
            rig.keyframe_insert('delta_location', frame=1)
            for fc in self.output._iter_action_fcurves(rig.animation_data.action):
                if fc.data_path == 'delta_location':
                    fc.keyframe_points[0].interpolation = 'CONSTANT'
        self.check_reject(edit, 'channels differ')

    def test_incompatible_rest_skeleton_rejected_both_paths(self):
        def edit(rig):
            bpy.context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode='EDIT')
            rig.data.edit_bones['Hips'].head.x += .25
            bpy.ops.object.mode_set(mode='OBJECT')
        self.check_reject(edit, 'skeleton differs')

    def test_missing_fixed_registration_base_rejected_without_reset(self):
        del bpy.context.scene.camera.data['vta_scene_reference']
        self.check_reject(lambda rig:None, 'fixed registration base')

    def test_freeze_interpolate_bounded_preserve_object_keys_and_unrelated_character(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'Video-to-Animation'))
        from vta_core import resolve_pose_schedule, quaternion_slerp
        rig = self.rigs[0]
        for frame, value in [(1, .3), (6, .7)]:
            rig.location.x = value
            rig.rotation_euler.z = value
            rig.scale = (1+value, 1, 1)
            for channel in ('location', 'rotation_euler', 'scale'):
                rig.keyframe_insert(channel, frame=frame)
        before = self.curves(rig, True)
        pointer = rig.as_pointer()
        for mode in ('freeze', 'interpolate'):
            path = self.make_report(False)
            report = json.loads(path.read_text())
            self.plan['generation_scope'] = 'range'
            self.plan['source_range'] = [2, 3]
            report['plan'] = self.plan
            record = next(r for r in report['characters'] if r['character_id'] == rig['vta_character_id'])
            s = [dict(frame=f, mode='infer', repair_mode=mode if f in (2,3) else None, reason='raw')
                 for f in range(6)]
            fixed = resolve_pose_schedule(s, list(range(6)))[2:4]
            record['schedule'] = fixed
            for other in report['characters']:
                if other is not record:
                    other['schedule'] = other['schedule'][2:4]
            self.backend._core.quaternion_slerp = quaternion_slerp
            self.backend._core.resolve_pose_schedule = lambda _s, _frames, **kw: (
                fixed if _s is self.plan['characters'][0]['samples'] else report['characters'][1]['schedule'])
            # Character order is deterministic in this fixture.
            path.write_text(json.dumps(report))
            self.output.import_reviewed_output(path, self.location, self.backend)
            self.assertEqual(rig.as_pointer(), pointer)
            self.assertEqual(self.curves(rig, True), before)
