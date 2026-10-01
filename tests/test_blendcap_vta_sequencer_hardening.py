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
        self.assertIn("BLENDCAP_MT_vta_commands", names)

    def test_context_hooks_keep_legacy_fallback_without_obsolete_tool(self):
        source = (ROOT / "blendcap" / "vta_review_ui.py").read_text(encoding="utf-8")
        self.assertIn("_set_menu_hook(bpy.types.SEQUENCER_MT_context_menu, _draw_vta_context)", source)
        self.assertIn("_set_menu_hook(bpy.types.SEQUENCER_MT_editor_menus, _draw_vta_header)", source)
        self.assertNotIn("_draw_select_review_keys", source)
        self.assertNotIn("BLENDCAP_WST_vta_review_key_select", source)
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
    @classmethod
    def setUpClass(cls):
        name = "_vta_sequencer_registry_test"
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[name] = addon
        spec.loader.exec_module(addon)
        cls.addon = addon
        cls.ui = sys.modules[name + ".blendcap.vta_review_ui"]

    def test_shared_action_availability_and_menu_operator_paths(self):
        ui = self.ui
        character = SimpleNamespace(character_id="red", occupied_observation_id="box")
        view = SimpleNamespace(characters=(character,), source_frame=11)
        timeline = SimpleNamespace(keys=(SimpleNamespace(source_frame=11),))
        context = SimpleNamespace(space_data=SimpleNamespace(view_type='SEQUENCER'))

        class Layout:
            def __init__(self, calls=None):
                self.calls = [] if calls is None else calls
                self.enabled = True

            def row(self):
                return Layout(self.calls)

            def operator(self, operator, *, text):
                self.calls.append((operator, text, self.enabled))
                return SimpleNamespace()

            def separator(self):
                pass

            def label(self, **_kwargs):
                pass

        with mock.patch.object(ui, "_view", view), mock.patch.object(ui, "_timeline", timeline), \
             mock.patch.object(ui, "_location", object()), \
             mock.patch.object(ui, "_selection", SimpleNamespace(character_id="red")), \
             mock.patch.object(ui, "_review_key_selection", SimpleNamespace(keyframe_ids=("key",))), \
             mock.patch.object(ui, "refresh", return_value=True):
            enabled = ui._action_availability()
            self.assertTrue(all(enabled.values()))
            panel = Layout()
            ui._draw_management_actions(panel)
            menu = Layout()
            ui.BLENDCAP_MT_vta_commands.draw(SimpleNamespace(layout=menu), context)
            self.assertEqual(panel.calls, [call for call in menu.calls
                                           if call[0] == "blendcap.vta_review_manage"])
            self.assertIn(("blendcap.vta_update_tracking", "Update Tracking", True), menu.calls)
            self.assertEqual({call[0] for call in menu.calls}, {
            "blendcap.vta_review_manage", "blendcap.vta_update_tracking",
            "blendcap.vta_analyze"
            })
            with mock.patch.object(ui, "_review_key_selection", SimpleNamespace(keyframe_ids=())):
                disabled = ui._action_availability()
                self.assertFalse(disabled["clear-review-key"])
                self.assertFalse(disabled["clear-selected-review-keys"])
            with mock.patch.object(ui, "_timeline", SimpleNamespace(keys=())):
                self.assertFalse(ui._action_availability()["clear-all-review-keys-on-frame"])
            with mock.patch.object(ui, "_view", SimpleNamespace(
                    characters=(SimpleNamespace(character_id="red", occupied_observation_id=None),),
                    source_frame=11)):
                self.assertFalse(ui._action_availability()["clear-assignment"])
            with mock.patch.object(ui, "_selection", SimpleNamespace(character_id="")):
                missing = ui._action_availability()
                self.assertFalse(missing["rename-character"])
                self.assertFalse(missing["delete-character"])
                self.assertFalse(missing["update-tracking"])

    def test_header_and_context_use_native_menu_in_vta_mode(self):
        ui = self.ui
        context = SimpleNamespace(space_data=SimpleNamespace(view_type='SEQUENCER'))
        header = mock.Mock()
        with mock.patch.object(ui, 'role_for', return_value='preview'):
            ui._draw_vta_header(SimpleNamespace(layout=header), context)
        header.menu.assert_called_once_with("BLENDCAP_MT_vta_commands", text="VTA Commands")
        right_click = mock.Mock()
        with mock.patch.object(ui, 'role_for', return_value='preview'):
            ui._draw_vta_context(SimpleNamespace(layout=right_click), context)
        right_click.menu.assert_called_once_with("BLENDCAP_MT_vta_commands", text="VTA Commands")

    def test_operator_poll_and_native_hooks_survive_reregister_without_duplicates(self):
        addon = self.addon
        ui = self.ui

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
                self.assertEqual(bpy.types.Menu.bl_rna_get_subclass_py("BLENDCAP_MT_vta_commands"),
                                 ui.BLENDCAP_MT_vta_commands)
                with mock.patch.object(ui, "_view", object()), mock.patch.object(ui, "_location", object()):
                    self.assertTrue(bpy.ops.blendcap.vta_review_manage.poll())
                self.assertEqual(len(hooks(bpy.types.SEQUENCER_MT_context_menu, "_draw_vta_context")), 1)
                self.assertEqual(len(hooks(bpy.types.SEQUENCER_MT_editor_menus, "_draw_vta_header")), 1)
                self.assertFalse(hasattr(ui, '_registered_timeline_tools'))
                self.assertIsNotNone(ui._review_context_keymap)
                self.assertEqual(sum(
                    item.idname == "blendcap.vta_review_key_select"
                    and item.type == 'RIGHTMOUSE'
                    for item in ui._review_context_keymap[0].keymap_items
                ), 1)
                self.assertIsNotNone(ui.vta_timeline_overlay._handler)
                panels = (
                    ui.VIEW3D_PT_blendcap_vta_review,
                    ui.VIEW3D_PT_blendcap_vta_analyze,
                    ui.VIEW3D_PT_blendcap_vta_characters,
                    ui.VIEW3D_PT_blendcap_vta_current_frame,
                    ui.VIEW3D_PT_blendcap_vta_tracking,
                    ui.VIEW3D_PT_blendcap_vta_display,
                    ui.VIEW3D_PT_blendcap_vta_advanced,
                )
                with mock.patch.object(ui, "_view", None), \
                     mock.patch.object(ui, "_location", None), \
                     mock.patch.object(ui, "refresh", return_value=False), \
                     mock.patch.object(ui, "addon_prefs", return_value=None):
                    for panel in panels:
                        self.assertIsNotNone(bpy.types.Panel.bl_rna_get_subclass_py(
                            panel.bl_idname))
                        panel.draw(SimpleNamespace(layout=mock.Mock()), bpy.context)
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_editor_menus, "_draw_vta_menu"))
                addon.unregister()
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_context_menu, "_draw_vta_context"))
                self.assertFalse(hooks(bpy.types.SEQUENCER_MT_editor_menus, "_draw_vta_header"))
                self.assertIsNone(ui._review_context_keymap)
                self.assertIsNone(ui.vta_timeline_overlay._handler)
        finally:
            if getattr(ui.vta_timeline_overlay, "_handler", None) is not None:
                addon.unregister()


if __name__ == "__main__":
    unittest.main()
