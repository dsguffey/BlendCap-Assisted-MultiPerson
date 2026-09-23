"""Focused Blender-independent regression checks for Sequencer integration."""

import ast
import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType
import unittest
from unittest import mock

from blendcap_vta_review import tracking_update_error, tracking_update_success


ROOT = Path(__file__).resolve().parents[1]
_package = ModuleType("_vta_sequencer_test_package")
_package.__path__ = [str(ROOT)]
sys.modules[_package.__name__] = _package
_sub = ModuleType(_package.__name__ + ".blendcap")
_sub.__path__ = [str(ROOT / "blendcap")]
sys.modules[_sub.__name__] = _sub
visible_lanes = importlib.import_module(_sub.__name__ + ".vta_timeline_overlay").visible_lanes
try:
    import bpy
except ImportError:
    bpy = None


class SequencerHardeningTests(unittest.TestCase):
    def test_live_registration_list_contains_manage_and_key_selection(self):
        tree = ast.parse((ROOT / "blendcap" / "registration.py").read_text(encoding="utf-8"))
        registered = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "classes"
                                  for target in node.targets))
        names = {node.id for node in ast.walk(registered) if isinstance(node, ast.Name)}
        self.assertIn("BLENDCAP_OT_vta_review_manage", names)
        self.assertIn("BLENDCAP_OT_vta_review_key_select", names)

    def test_select_and_context_hooks_use_existing_blender_menus(self):
        source = (ROOT / "blendcap" / "vta_review_ui.py").read_text(encoding="utf-8")
        self.assertIn("_set_menu_hook(bpy.types.SEQUENCER_MT_select, _draw_vta_select)", source)
        self.assertIn("_set_menu_hook(bpy.types.SEQUENCER_MT_context_menu, _draw_vta_context)", source)
        self.assertNotIn("SEQUENCER_MT_editor_menus.append", source)
        self.assertNotIn("BLENDCAP_MT_vta_review", source)
        self.assertNotIn('text="First Lane"', source)

    def test_auto_focused_lanes_keep_other_characters_when_space_allows(self):
        view = SimpleNamespace(characters=tuple(
            SimpleNamespace(character_id=str(i)) for i in range(3)))
        self.assertEqual(len(visible_lanes(view, 200, scroll=2)), 3)

    def test_strip_occupancy_suppresses_draw_and_hit_lanes(self):
        view = SimpleNamespace(characters=(SimpleNamespace(character_id="red"),))
        fake_view2d = SimpleNamespace(region_to_view=lambda x, y: (x, 1.5))
        self.assertEqual(visible_lanes(view, 200, view2d=fake_view2d,
                                       occupied_channels=(1,)), ())
        self.assertEqual(len(visible_lanes(view, 200, view2d=fake_view2d,
                                           occupied_channels=(3,))), 1)

    def test_expected_tracking_status_is_concise_and_persistent_text(self):
        result = SimpleNamespace(matched=1, unresolved=1, scope=SimpleNamespace(
            left_boundary=SimpleNamespace(frame=1),
            right_boundary=SimpleNamespace(frame=60)))
        text = tracking_update_success(result)
        self.assertIn("2 analyzed frames", text)
        self.assertIn("1 matched, 1 unresolved", text)
        self.assertIn("1–60", text)
        self.assertIn("No analyzed candidate data", tracking_update_error(
            RuntimeError("no analyzed candidate data exists in this bounded range")))
        self.assertIn("No candidate identity anchor", tracking_update_error(
            RuntimeError("local region has no usable candidate-assignment anchor")))


@unittest.skipUnless(bpy is not None, "requires Blender background runtime")
class BlenderSequencerRegistryTests(unittest.TestCase):
    def test_operator_poll_and_native_hooks_survive_reregister_without_duplicates(self):
        name = "_vta_sequencer_registry_test"
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[name] = addon
        spec.loader.exec_module(addon)
        ui = sys.modules[name + ".blendcap.vta_review_ui"]

        def hooks(menu, callback_name):
            return [item for item in getattr(menu.draw, "_draw_funcs", ())
                    if getattr(item, "__name__", None) == callback_name]

        try:
            for _ in range(2):
                addon.register()
                self.assertEqual(bpy.ops.blendcap.vta_review_manage.get_rna_type().identifier,
                                 "BLENDCAP_OT_vta_review_manage")
                self.assertEqual(bpy.ops.blendcap.vta_review_key_select.get_rna_type().identifier,
                                 "BLENDCAP_OT_vta_review_key_select")
                with mock.patch.object(ui, "_view", object()), mock.patch.object(ui, "_location", object()):
                    self.assertTrue(bpy.ops.blendcap.vta_review_manage.poll())
                self.assertEqual(len(hooks(bpy.types.SEQUENCER_MT_select, "_draw_vta_select")), 1)
                self.assertEqual(len(hooks(bpy.types.SEQUENCER_MT_context_menu, "_draw_vta_context")), 1)
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_editor_menus, "_draw_vta_menu"))
                addon.unregister()
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_select, "_draw_vta_select"))
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_context_menu, "_draw_vta_context"))
        finally:
            if getattr(ui.vta_timeline_overlay, "_handler", None) is not None:
                addon.unregister()


if __name__ == "__main__":
    unittest.main()
