"""Snap changes viewport presentation, never calibrated camera or rig motion."""
import importlib
from types import SimpleNamespace
import unittest
from unittest import mock
try:
    import bpy
except ImportError:
    bpy=None

@unittest.skipUnless(bpy is not None,'Blender runtime required')
class CameraViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass();cls.fixture_class=ObjectPlacementTests
        cls.workspace=importlib.import_module(ObjectPlacementTests.output.__package__+'.vta_workspace')
    def setUp(self):
        self.fixture=self.fixture_class();self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        self.space=self.area.spaces.active
        self.before=(self.space.use_local_camera,self.space.camera,self.space.region_3d.view_perspective,
            tuple(self.space.region_3d.view_camera_offset),self.space.region_3d.view_camera_zoom,
            self.space.overlay.show_overlays,self.space.overlay.show_bones,self.space.show_object_viewport_armature)
        self.addCleanup(self.restore_view)
        self.context=SimpleNamespace(scene=bpy.context.scene,area=self.area,workspace=bpy.context.workspace,screen=bpy.context.screen)
    def restore_view(self):
        s=self.space;p=self.before
        camera=p[1]
        try:
            if camera is not None:
                camera.name
        except ReferenceError:
            camera=None
        s.use_local_camera,s.camera,s.region_3d.view_perspective=p[0],camera,p[2]
        s.region_3d.view_camera_offset,s.region_3d.view_camera_zoom=p[3:5]
        s.overlay.show_overlays,s.overlay.show_bones,s.show_object_viewport_armature=p[5:]
    def test_rotated_view_restored_without_camera_rig_or_curve_changes(self):
        self.space.region_3d.view_perspective='PERSP'
        self.space.region_3d.view_camera_offset=(.2,.3)
        self.space.region_3d.view_camera_zoom=40
        self.space.overlay.show_overlays=False
        camera=bpy.context.scene.camera
        before=[o.matrix_world.copy() for o in [camera,*self.fixture.rigs]]
        keys=[self.fixture.curves(o) for o in self.fixture.rigs]
        self.assertEqual(self.workspace.snap_source_camera_view(self.context),1)
        self.assertEqual(self.space.region_3d.view_perspective,'CAMERA')
        self.assertIs(self.space.camera,camera)
        self.assertTrue(self.space.use_local_camera)
        self.assertEqual(tuple(self.space.region_3d.view_camera_offset),(0,0))
        self.assertEqual(self.space.region_3d.view_camera_zoom,0)
        self.assertTrue(self.space.overlay.show_overlays)
        self.assertEqual([o.matrix_world for o in [camera,*self.fixture.rigs]],before)
        self.assertEqual([self.fixture.curves(o) for o in self.fixture.rigs],keys)
    def test_finds_owned_camera_without_changing_other_active_camera(self):
        owned=bpy.context.scene.camera
        user=bpy.data.objects.new('user camera',bpy.data.cameras.new('user camera'))
        bpy.context.scene.collection.objects.link(user);bpy.context.scene.camera=user
        self.workspace.snap_source_camera_view(self.context)
        self.assertIs(self.space.camera,owned)
        self.assertIs(bpy.context.scene.camera,user)
    def test_deleted_camera_actionable_error(self):
        bpy.data.objects.remove(bpy.context.scene.camera,do_unlink=True)
        op=SimpleNamespace(report=mock.Mock())
        self.assertEqual(self.workspace.BLENDCAP_OT_vta_snap_source_camera.execute(op,self.context),{'CANCELLED'})
        self.assertIn('Source camera unavailable',op.report.call_args.args[1])
    def test_ambiguous_camera_rejected(self):
        copy=bpy.context.scene.camera.copy();bpy.context.scene.collection.objects.link(copy)
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            self.workspace.snap_source_camera_view(self.context)
    def test_vta_menu_targets_workspace_viewport(self):
        self.context.area=SimpleNamespace(type='DOPESHEET_EDITOR')
        self.context.workspace={self.workspace.WORKSPACE_TAG:1}
        self.space.region_3d.view_perspective='PERSP'
        self.assertEqual(self.workspace.snap_source_camera_view(self.context),1)
        self.assertEqual(self.space.region_3d.view_perspective,'CAMERA')
    def test_other_workspace_menu_does_not_change_view(self):
        self.context.area=SimpleNamespace(type='DOPESHEET_EDITOR')
        self.context.workspace={}
        self.space.region_3d.view_perspective='PERSP'
        with self.assertRaisesRegex(ValueError,'3D viewport or the VTA workspace'):
            self.workspace.snap_source_camera_view(self.context)
        self.assertEqual(self.space.region_3d.view_perspective,'PERSP')
