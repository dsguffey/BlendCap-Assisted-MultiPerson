"""Native add-on enable restricts context; unrestricted register is not enough."""
import importlib.util
from pathlib import Path
import sys
import unittest
try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class SceneStartupTests(unittest.TestCase):
    def test_real_restricted_register_installs_all_hooks_and_timer(self):
        from _bpy_restrict_state import RestrictBlend
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('scene_startup_test', root/'__init__.py',
                                                     submodule_search_locations=[str(root)])
        addon = importlib.util.module_from_spec(spec); sys.modules[spec.name] = addon
        spec.loader.exec_module(addon)
        ui = sys.modules[spec.name+'.blendcap.vta_review_ui']
        ws = sys.modules[spec.name+'.blendcap.vta_workspace']
        def count(owner, fn):
            return getattr(owner.draw, '_draw_funcs', ()).count(fn)
        for _ in range(2):
            try:
                with RestrictBlend():
                    addon.register()
                self.assertTrue(bpy.app.timers.is_registered(ui._scene_observer_timer))
                self.assertEqual(count(bpy.types.DOPESHEET_MT_editor_menus, ws._draw_header), 1)
                self.assertEqual(count(bpy.types.SEQUENCER_HT_header, ws._draw_preview_header), 1)
                self.assertEqual(count(bpy.types.VIEW3D_HT_header, ws._draw_camera_header), 1)
                self.assertEqual(count(bpy.types.SEQUENCER_MT_editor_menus, ui._draw_vta_header), 1)
                self.assertTrue(ws._header_registered and ws._preview_header_registered and ws._camera_header_registered)
                self.assertIsNone(ui._view)
            finally:
                addon.unregister()
            self.assertFalse(bpy.app.timers.is_registered(ui._scene_observer_timer))
            self.assertEqual(count(bpy.types.DOPESHEET_MT_editor_menus, ws._draw_header), 0)

    def test_restricted_content_reset_never_touches_editor_configuration(self):
        from _bpy_restrict_state import RestrictBlend
        package = sys.modules.get('scene_startup_test')
        if package is None:
            self.test_real_restricted_register_installs_all_hooks_and_timer()
        ui = sys.modules['scene_startup_test.blendcap.vta_review_ui']
        screen, workspace = bpy.context.screen, bpy.context.workspace
        before = (dict(screen.items()), dict(workspace.items()), workspace.sequencer_scene)
        with RestrictBlend():
            ui._reset_scene_presentation()
        self.assertEqual(before, (dict(screen.items()), dict(workspace.items()), workspace.sequencer_scene))
        self.assertIsNone(ui._active_review_scene)
