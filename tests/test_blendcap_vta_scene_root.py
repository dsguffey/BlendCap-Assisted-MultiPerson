"""Production identity SceneRoot ownership and transaction regression gates."""
import unittest
from unittest import mock

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class SceneRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass()
        cls.fixture_class = ObjectPlacementTests
        cls.output = ObjectPlacementTests.output
        import importlib
        cls.roots = importlib.import_module(cls.output.__package__ + '.vta_scene_root')
        cls.workspace = importlib.import_module(cls.output.__package__ + '.vta_workspace')

    def setUp(self):
        self.fixture = self.fixture_class()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.rigs = self.fixture.rigs
        self.root = self.rigs[0].parent
        self.camera = bpy.context.scene.camera

    def snapshot(self):
        return [(obj, obj.parent, obj.parent_type, obj.parent_bone, obj.matrix_parent_inverse.copy(),
                 self.fixture.placement(obj), obj.animation_data.action if obj.animation_data else None,
                 self.fixture.curves(obj) if obj.type == 'ARMATURE' else None) for obj in [*self.rigs, self.camera]]

    def data_sets(self):
        return [set(getattr(bpy.data, name)) for name in ('objects', 'actions', 'armatures', 'cameras', 'images')]

    def legacy(self):
        for obj in [*self.rigs, self.camera]:
            obj.parent = None  # identity container; local channels remain unchanged
        bpy.data.objects.remove(self.root, do_unlink=True)
        self.root = None

    def test_creation_metadata_and_parent_configuration(self):
        from mathutils import Matrix
        root = self.roots.find_scene_root(bpy.context.scene, self.fixture.pid, 's')
        self.assertIs(root, self.root)
        self.assertEqual(root.type, 'EMPTY')
        self.assertEqual(root[self.roots.ROOT_TAG], 1)
        self.assertIsNone(root.animation_data)
        self.assertIsNone(root.parent)
        self.assertFalse(root.constraints)
        self.assertEqual(root.matrix_world, Matrix.Identity(4))
        self.assertEqual(root.matrix_basis, Matrix.Identity(4))
        for obj in [*self.rigs, self.camera]:
            self.assertIs(obj.parent, root)
            self.assertEqual(obj.parent_type, 'OBJECT')
            self.assertEqual(obj.matrix_parent_inverse, Matrix.Identity(4))
            self.assertTrue(self.roots.supported_parent(obj))

    def test_renamed_root_reused_not_identified_by_name(self):
        self.root.name = 'User-friendly renamed container'
        self.fixture.regenerate(True)
        self.assertIs(self.rigs[0].parent, self.root)
        self.assertIs(self.roots.find_scene_root(bpy.context.scene, self.fixture.pid, 's'), self.root)

    def test_legacy_adoption_preserves_user_curves_and_local_channels(self):
        rig = self.rigs[0]
        for frame, value in ((1, .3), (6, .7)):
            rig.location.x = value
            rig.rotation_euler.z = value
            rig.scale = (1 + value, 1, 1)
            for channel in ('location', 'rotation_euler', 'scale'):
                rig.keyframe_insert(channel, frame=frame)
        bpy.context.scene.frame_set(1)
        keys, placement = self.fixture.curves(rig, True), self.fixture.placement(rig)
        self.legacy()
        for bounded in (True, False):
            self.fixture.regenerate(bounded)
            self.assertEqual(self.fixture.curves(rig, True), keys)
            self.assertEqual(self.fixture.placement(rig), placement)
            self.assertTrue(self.roots.supported_parent(rig))

    def test_name_only_impostor_untouched(self):
        self.legacy()
        impostor = bpy.data.objects.new('VTA_SceneRoot', None)
        bpy.context.scene.collection.objects.link(impostor)
        impostor.location.x = 8
        self.fixture.regenerate(True)
        root = self.rigs[0].parent
        self.assertIsNot(root, impostor)
        self.assertNotIn(self.roots.ROOT_TAG, impostor)
        self.assertEqual(impostor.location.x, 8)

    def assert_rejected(self, edit):
        edit()
        bpy.context.view_layer.update()
        before, data = self.snapshot(), self.data_sets()
        for bounded in (True, False):
            with self.assertRaisesRegex(ValueError, 'SceneRoot|parents|parent'):
                self.fixture.regenerate(bounded)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.data_sets(), data)

    def test_moved_root_rejected(self):
        self.assert_rejected(lambda: setattr(self.root, 'location', (1, 0, 0)))

    def test_rotated_scaled_and_delta_root_rejected(self):
        for channel, value in (('rotation_euler', (.1, 0, 0)), ('scale', (2, 1, 1)),
                               ('delta_location', (0, .1, 0))):
            with self.subTest(channel=channel):
                setattr(self.root, channel, value)
                self.assertFalse(self.roots.supported_parent(self.rigs[0]))
                setattr(self.root, channel, (1, 1, 1) if channel == 'scale' else (0, 0, 0))
        self.assert_rejected(lambda: setattr(self.root, 'delta_scale', (1, 2, 1)))

    def test_animated_root_rejected(self):
        self.assert_rejected(lambda: self.root.keyframe_insert('location', frame=1))

    def test_driven_root_rejected(self):
        self.assert_rejected(lambda: self.root.driver_add('location', 0))

    def test_constrained_root_rejected(self):
        self.assert_rejected(lambda: self.root.constraints.new('COPY_LOCATION'))

    def test_parented_root_rejected(self):
        ancestor = bpy.data.objects.new('unsupported ancestor', None)
        bpy.context.scene.collection.objects.link(ancestor)
        self.assert_rejected(lambda: setattr(self.root, 'parent', ancestor))

    def test_wrong_project_source_and_tag_rejected(self):
        for key in ('vta_project_id', 'vta_source_id', self.roots.ROOT_TAG):
            with self.subTest(key=key):
                original = self.root[key]
                self.root[key] = 'wrong'
                self.assert_rejected(lambda: None)
                self.root[key] = original
                bpy.context.view_layer.update()

    def test_nonidentity_inverse_and_arbitrary_parent_rejected(self):
        from mathutils import Matrix
        self.assert_rejected(lambda: setattr(self.rigs[0], 'matrix_parent_inverse', Matrix.Translation((1, 0, 0))))
        self.rigs[0].matrix_parent_inverse = Matrix.Identity(4)
        self.assert_rejected(lambda: setattr(self.rigs[0], 'parent', self.rigs[1]))

    def test_duplicate_owned_roots_rejected(self):
        duplicate = self.root.copy()
        bpy.context.scene.collection.objects.link(duplicate)
        self.assert_rejected(lambda: None)

    def test_malformed_unparented_owned_root_not_silently_replaced(self):
        for obj in [*self.rigs, self.camera]:
            obj.parent = None
        self.assert_rejected(lambda: setattr(self.root, 'location', (1, 0, 0)))

    def test_registration_temporary_rigs_still_unparented_identity(self):
        from mathutils import Matrix
        original = self.output.register_camera_root
        def checked(obj, samples):
            self.assertIsNone(obj.parent)
            self.assertEqual(obj.matrix_world, Matrix.Identity(4))
            return original(obj, samples)
        with mock.patch.object(self.output, 'register_camera_root', checked):
            self.fixture.regenerate(True)

    def test_failure_during_adoption_restores_legacy_hierarchy_and_datablocks(self):
        self.legacy()
        self.check_failure('parent')

    def test_failure_after_adoption_restores_existing_root_and_datablocks(self):
        self.check_failure('cleanup')

    def test_presentation_failure_rolls_back_new_root_and_hierarchy(self):
        self.legacy()
        self.check_failure('presentation')

    def check_failure(self, kind):
        before, data, camera_data = self.snapshot(), self.data_sets(), self.camera.data
        if kind == 'parent':
            function, target = 'parent_to_scene_root', self.output
            original = getattr(target, function)
            calls = []
            def fail(*args):
                calls.append(args[0]); original(*args)
                if len(calls) == 2:
                    raise RuntimeError('injected adoption failure')
        else:
            function = '_remove_staged' if kind == 'cleanup' else 'configure_source_camera_views'
            target = self.output if kind == 'cleanup' else self.workspace
            def fail(*args, **kw):
                self.assertTrue(all(self.roots.supported_parent(o) and o.parent for o in [*self.rigs, self.camera]))
                raise RuntimeError('injected adoption failure')
        with mock.patch.object(target, function, fail), self.assertRaisesRegex(RuntimeError, 'injected adoption'):
            self.fixture.regenerate(True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.data_sets(), data)
        self.assertIs(self.camera.data, camera_data)

    def test_camera_deletion_retains_motion_and_rejects_regeneration(self):
        from mathutils import Matrix
        before = [o.matrix_world.copy() for o in self.rigs]
        keys = [self.fixture.curves(o) for o in self.rigs]
        bpy.data.objects.remove(self.camera, do_unlink=True)
        self.assertEqual([o.matrix_world for o in self.rigs], before)
        self.assertEqual([self.fixture.curves(o) for o in self.rigs], keys)
        with self.assertRaisesRegex(ValueError, 'fixed registration base'):
            self.fixture.regenerate(True)
        self.assertEqual([o.matrix_world for o in self.rigs], before)
        self.assertEqual([self.fixture.curves(o) for o in self.rigs], keys)
