"""Pure and Blender-background contracts for the VTA review timeline."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock
import blendcap_vta_review as review_shell

from blendcap_vta_timeline import (
    ReviewKeySelection, TimelineCharacter, TimelineKey, TimelineView,
    hit_test_keys, key_at, lane_layout, present_timeline,
)

try:
    import bpy
except ImportError:
    bpy = None


def view():
    return TimelineView("source", (
        TimelineCharacter("amy", "Amy"),
        TimelineCharacter("empty", "Empty"),
        TimelineCharacter("zed", "Zed"),
    ), (
        TimelineKey("key-a", "amy", 5, "visible", "observation"),
        TimelineKey("key-b", "zed", 9, "occluded", None),
        TimelineKey("key-c", "amy", 11, "no-valid-candidate", None),
        TimelineKey("key-d", "zed", 12, "not-visible", None),
    ))


class TimelineProofTests(unittest.TestCase):
    def test_character_projection_sorts_names_without_changing_stable_ids(self):
        private = SimpleNamespace(source_id='source', characters=(
            SimpleNamespace(character_id='z', display_name='Yellow'),
            SimpleNamespace(character_id='b', display_name='alice'),
            SimpleNamespace(character_id='a', display_name='Alice'),
            SimpleNamespace(character_id='w', display_name='White'),
        ), keys=())
        projected = present_timeline(private)
        self.assertEqual([item.character_id for item in projected.characters],
                         ['a', 'b', 'w', 'z'])
        self.assertEqual(private.characters[0].character_id, 'z')

    def test_thin_backend_uses_private_projection_and_command(self):
        private_timeline = SimpleNamespace(
            source_id="source",
            characters=(SimpleNamespace(character_id="amy", display_name="Amy"),),
            keys=(SimpleNamespace(keyframe_id="durable", character_id="amy", frame=5,
                                  state=SimpleNamespace(value="occluded"),
                                  observation_id=None),),
        )
        project = object()
        cache = SimpleNamespace(source=SimpleNamespace(source_id="source"))
        core = SimpleNamespace(
            validate_analysis_cache_registration=mock.Mock(),
            build_review_timeline=mock.Mock(return_value=private_timeline),
            build_review_management_request=mock.Mock(return_value="request"),
        )
        backend = review_shell.PrivateCoreBackend(core=core)
        location = review_shell.ReviewLocation(Path("companion"), Path("video.mp4"), 5, 105)
        with mock.patch.object(backend, "_load_context", return_value=(project, cache, "revision")):
            timeline = backend.query_timeline(location)
            request = backend.prepare_management(location, "clear-review-key",
                                                 keyframe_ids=("durable",))
        self.assertEqual(timeline.keys[0].keyframe_id, "durable")
        self.assertEqual(timeline.keys[0].label, "Occluded")
        self.assertEqual(request, "request")
        core.build_review_management_request.assert_called_once_with(
            project, "clear-review-key", keyframe_ids=("durable",)
        )

    def test_current_assignment_from_prior_revision_remains_clearable(self):
        review = SimpleNamespace(
            analysis_revision_id="new-revision", source_id="source", frame=5,
            state=SimpleNamespace(value="analyzed-with-candidates"),
            candidates=(SimpleNamespace(analysis_revision_id="new-revision",
                                        observation_id="new-observation", box_xyxy=(1, 2, 3, 4)),),
            characters=(SimpleNamespace(character_id="amy", display_name="Amy"),),
            character_assignments=(),
            character_decisions=(SimpleNamespace(character_id="amy", keyframe_id="durable",
                                                 state=SimpleNamespace(value="visible"),
                                                 observation_id="older-observation"),),
        )
        projected = review_shell.present_review(review)
        self.assertEqual(projected.characters[0].occupied_observation_id, "older-observation")
        selection = review_shell.SelectionState()
        selection.character_id = "amy"
        selection.observation_id = "new-observation"
        intent = selection.assignment_intent(projected)
        self.assertTrue(intent.requires_confirmation)

    def test_projection_has_all_state_labels_and_no_derived_keys(self):
        private = SimpleNamespace(
            source_id="source",
            characters=(SimpleNamespace(character_id="amy", display_name="Amy"),),
            keys=(SimpleNamespace(keyframe_id="durable", character_id="amy",
                                  frame=5, state=SimpleNamespace(value="visible"),
                                  observation_id="observation"),),
        )
        projected = present_timeline(private)
        self.assertEqual(projected.keys[0].label, "Candidate Assignment")
        self.assertEqual([key.label for key in view().keys], [
            "Candidate Assignment", "Occluded", "No Valid Candidate", "Not Visible"
        ])
        self.assertEqual(projected.keys[0].keyframe_id, "durable")

    def test_stable_selection_multi_select_and_reconcile(self):
        selection = ReviewKeySelection()
        self.assertTrue(selection.click(view(), "key-a"))
        self.assertTrue(selection.click(view(), "key-b", extend=True))
        self.assertEqual(selection.keyframe_ids, ("key-a", "key-b"))
        self.assertTrue(selection.click(view(), "key-a", extend=True))
        self.assertEqual(selection.keyframe_ids, ("key-b",))
        self.assertFalse(selection.click(view(), "absent"))
        selection.reconcile(TimelineView("source", view().characters, view().keys[:1]))
        self.assertEqual(selection.keyframe_ids, ())

    def test_right_click_active_key_preserves_selected_group_by_durable_id(self):
        selection = ReviewKeySelection()
        selection.click(view(), "key-a")
        selection.click(view(), "key-b", extend=True)
        self.assertTrue(selection.activate(view(), "key-a"))
        self.assertEqual(selection.keyframe_ids, ("key-a", "key-b"))
        self.assertEqual(selection.active_keyframe_id, "key-a")
        self.assertTrue(selection.activate(view(), "key-c"))
        self.assertEqual(selection.keyframe_ids, ("key-c",))
        self.assertFalse(selection.activate(view(), "missing"))
        selection.reconcile(TimelineView("source", view().characters, ()))
        self.assertIsNone(selection.active_keyframe_id)

    def test_ctrl_toggle_retargets_active_key_without_index_identity(self):
        selection = ReviewKeySelection()
        selection.click(view(), "key-a")
        selection.click(view(), "key-b", extend=True)
        self.assertEqual(selection.active_keyframe_id, "key-b")
        selection.click(view(), "key-b", extend=True)
        self.assertEqual(selection.active_keyframe_id, "key-a")
        selection.click(view(), "key-a", extend=True)
        self.assertEqual(selection.keyframe_ids, ())
        self.assertIsNone(selection.active_keyframe_id)

    def test_lane_layout_keeps_empty_row_and_hit_test_uses_frame_transform(self):
        rows = lane_layout(view(), top=100, row_height=24)
        self.assertEqual([name for name, _y in rows], ["amy", "empty", "zed"])
        self.assertEqual(hit_test_keys(
            view(), rows, frame_to_x=lambda frame: frame * 10,
            point=(50, rows[0][1]),
        ), "key-a")
        self.assertIsNone(hit_test_keys(
            view(), rows, frame_to_x=lambda frame: frame * 10,
            point=(50, rows[1][1]),
        ))
        self.assertEqual(key_at(view(), "key-d").state, "not-visible")


@unittest.skipUnless(bpy is not None, "requires Blender background runtime")
class BlenderTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = "blendcap_timeline_test"
        spec = importlib.util.spec_from_file_location(
            package, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.ui = sys.modules[package + ".blendcap.vta_review_ui"]
        cls.overlay = sys.modules[package + ".blendcap.vta_timeline_overlay"]

    def test_direct_selection_and_native_menu_share_management_operator(self):
        ui = self.ui
        self.assertFalse(hasattr(ui, 'BLENDCAP_WST_vta_review_key_select'))
        self.assertIsNotNone(ui.BLENDCAP_OT_vta_review_key_select)
        self.assertIn(ui.BLENDCAP_OT_vta_review_manage, ui.classes)
        self.assertNotIn("BLENDCAP_MT_vta_review", vars(ui))
        self.assertTrue(callable(ui._draw_vta_context))

    def test_context_keymap_is_tool_independent_and_removed(self):
        ui = self.ui
        items = SimpleNamespace(new=mock.Mock(return_value=object()), remove=mock.Mock())
        keymap = SimpleNamespace(keymap_items=items)
        keyconfig = SimpleNamespace(keymaps=SimpleNamespace(new=mock.Mock(return_value=keymap)))
        wm = SimpleNamespace(keyconfigs=SimpleNamespace(addon=keyconfig))
        with mock.patch.object(ui, "bpy", SimpleNamespace(context=SimpleNamespace(window_manager=wm))), \
             mock.patch.object(ui, "_review_context_keymap", None):
            ui._register_review_context_keymap()
            ui._register_review_context_keymap()
            keyconfig.keymaps.new.assert_called_once_with(
                name='Sequencer', space_type='SEQUENCE_EDITOR', region_type='WINDOW'
            )
            self.assertEqual(items.new.call_args_list, [
                mock.call('blendcap.vta_review_key_select', 'LEFTMOUSE', 'PRESS'),
                mock.call('blendcap.vta_review_key_select', 'LEFTMOUSE', 'PRESS', ctrl=True),
                mock.call('blendcap.vta_review_key_select', 'RIGHTMOUSE', 'PRESS'),
            ])
            ui._unregister_review_context_keymap()
            self.assertIsNone(ui._review_context_keymap)
            self.assertEqual(items.remove.call_count, 3)

    def test_generic_character_names_skip_existing_and_keep_custom_names(self):
        ui = self.ui
        rows = SimpleNamespace(characters=(
            SimpleNamespace(display_name="Character 1"),
            SimpleNamespace(display_name="Ada"),
            SimpleNamespace(display_name="character 3"),
        ))
        self.assertEqual(ui.next_generic_character_name(rows), "Character 2")
        self.assertEqual([item.display_name for item in rows.characters],
                         ["Character 1", "Ada", "character 3"])

    def test_click_selection_uses_key_id_without_moving_time(self):
        ui = self.ui
        selection = ReviewKeySelection()
        scene = SimpleNamespace(frame_set=mock.Mock())
        context = SimpleNamespace(region=SimpleNamespace(view2d=object(), height=300),
                                  window_manager=SimpleNamespace(blendcap_vta_lane_scroll=0))
        location = SimpleNamespace(source_frame=5, blender_frame=105)
        with mock.patch.object(ui, "_timeline", view()), \
             mock.patch.object(ui, "_location", location), \
             mock.patch.object(ui, "_review_key_selection", selection), \
             mock.patch.object(ui, "_selection", SimpleNamespace(character_id="")), \
             mock.patch.object(ui.vta_timeline_overlay, "hit_key", return_value="key-a"), \
             mock.patch.object(ui, "_owning_vse_scene", return_value=scene), \
             mock.patch.object(ui, "refresh", return_value=True), \
             mock.patch.object(ui, "_sync_properties"):
            self.assertTrue(ui.select_review_key_at(context, (50, 250)))
            self.assertEqual(ui._selection.character_id, "amy")
            self.assertEqual(selection.keyframe_ids, ("key-a",))
        scene.frame_set.assert_not_called()

    def test_right_click_key_overrides_stale_character_for_management(self):
        ui = self.ui
        selection = ReviewKeySelection()
        scene = SimpleNamespace(frame_set=mock.Mock())
        context = SimpleNamespace(region=SimpleNamespace(view2d=object(), height=300))
        location = SimpleNamespace(source_frame=5, blender_frame=105)
        current = SimpleNamespace(source_id="source", source_frame=5)
        chosen = SimpleNamespace(character_id="zed")
        with mock.patch.object(ui, "_timeline", view()), \
             mock.patch.object(ui, "_location", location), \
             mock.patch.object(ui, "_view", current), \
             mock.patch.object(ui, "_review_key_selection", selection), \
             mock.patch.object(ui, "_selection", chosen), \
             mock.patch.object(ui.vta_timeline_overlay, "hit_key", return_value="key-a"), \
             mock.patch.object(ui, "_owning_vse_scene", return_value=scene), \
             mock.patch.object(ui, "refresh", return_value=True), \
             mock.patch.object(ui, "_sync_properties"):
            self.assertTrue(ui.select_review_key_at(context, (50, 250), activate=True))
            self.assertEqual(chosen.character_id, "amy")
            self.assertEqual(selection.active_keyframe_id, "key-a")
            self.assertEqual(selection.keyframe_ids, ("key-a",))
            operator = SimpleNamespace(action="clear-review-key")
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage._arguments(operator),
                             {"keyframe_ids": ("key-a",)})
            operator.action = "clear-assignment"
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage._arguments(operator),
                             {"character_id": "amy", "source_id": "source", "frame": 5})
            operator.action = "delete-character"
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage._arguments(operator),
                             {"character_id": "amy"})
        scene.frame_set.assert_not_called()

    def test_right_click_from_blank_selection_activates_key_before_native_menu(self):
        ui = self.ui
        selection = ReviewKeySelection()
        scene = SimpleNamespace(frame_set=mock.Mock())
        context = SimpleNamespace(region=SimpleNamespace(view2d=object(), height=300),
                                  area=SimpleNamespace(tag_redraw=mock.Mock()))
        event = SimpleNamespace(type='RIGHTMOUSE', mouse_region_x=50,
                                mouse_region_y=250, ctrl=False)
        chosen = SimpleNamespace(character_id="zed")
        observed = []
        with mock.patch.object(ui, "_timeline", view()), \
             mock.patch.object(ui, "_location", SimpleNamespace(source_frame=5, blender_frame=105)), \
             mock.patch.object(ui, "_review_key_selection", selection), \
             mock.patch.object(ui, "_selection", chosen), \
             mock.patch.object(ui.vta_timeline_overlay, "hit_key", return_value="key-a"), \
             mock.patch.object(ui, "_owning_vse_scene", return_value=scene), \
             mock.patch.object(ui, "refresh", return_value=True), \
             mock.patch.object(ui, "_sync_properties"), \
             mock.patch.object(ui, "_open_native_sequencer_context_menu",
                               side_effect=lambda: observed.append(
                                   (selection.active_keyframe_id, chosen.character_id,
                                    scene.frame_set.call_count))):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_key_select.invoke(
                None, context, event), {'FINISHED'})
        self.assertEqual(observed, [("key-a", "amy", 0)])

    def test_lane_headroom_preserves_glyph_bounds_and_strip_filter(self):
        overlay = self.overlay
        self.assertGreaterEqual(overlay.TOP_PADDING, overlay.ROW_HEIGHT + 12)
        lanes = overlay.visible_lanes(view(), 180)
        self.assertLessEqual(lanes[0][1] + 11, 180 - overlay.ROW_HEIGHT)
        class View2D:
            @staticmethod
            def region_to_view(_x, y):
                return 0, y / 24
        filtered = overlay.visible_lanes(view(), 180, view2d=View2D(),
                                         occupied_channels=(round(lanes[0][1] / 24),))
        self.assertNotIn(lanes[0], filtered)

    def test_active_key_is_distinct_from_multiselected_and_unselected(self):
        style = self.overlay.selection_outline_style
        self.assertEqual(style("a", ("a", "b"), "a"), "active")
        self.assertEqual(style("b", ("a", "b"), "a"), "selected")
        self.assertIsNone(style("c", ("a", "b"), "a"))

    def test_right_click_key_uses_native_context_menu_and_empty_passes_through(self):
        ui = self.ui
        event = SimpleNamespace(type='RIGHTMOUSE', mouse_region_x=50,
                                mouse_region_y=250, ctrl=False)
        context = SimpleNamespace(area=SimpleNamespace(tag_redraw=mock.Mock()))
        with mock.patch.object(ui, "select_review_key_at", return_value=True) as select, \
             mock.patch.object(ui, "_open_native_sequencer_context_menu") as menu:
            self.assertEqual(ui.BLENDCAP_OT_vta_review_key_select.invoke(None, context, event),
                             {'FINISHED'})
            select.assert_called_once_with(context, (50, 250), activate=True)
            menu.assert_called_once_with()
        with mock.patch.object(ui, "select_review_key_at", return_value=False), \
             mock.patch.object(ui, "_open_native_sequencer_context_menu") as menu:
            self.assertEqual(ui.BLENDCAP_OT_vta_review_key_select.invoke(None, context, event),
                             {'PASS_THROUGH'})
            menu.assert_not_called()
        with mock.patch.object(ui, "select_review_key_at", return_value=True), \
             mock.patch.object(ui, "_open_native_sequencer_context_menu",
                               side_effect=RuntimeError("menu unavailable")):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_key_select.invoke(None, context, event),
                             {'PASS_THROUGH'})

    def test_empty_left_click_clears_only_transient_key_selection(self):
        ui = self.ui
        selection = ReviewKeySelection()
        selection.click(view(), "key-a")
        event = SimpleNamespace(type='LEFTMOUSE', mouse_region_x=20,
                                mouse_region_y=20, ctrl=False)
        context = SimpleNamespace(area=SimpleNamespace(tag_redraw=mock.Mock()))
        with mock.patch.object(ui, "_review_key_selection", selection), \
             mock.patch.object(ui, "select_review_key_at", return_value=False):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_key_select.invoke(None, context, event),
                             {'PASS_THROUGH'})
        self.assertEqual(selection.keyframe_ids, ())
        context.area.tag_redraw.assert_called_once_with()

    def test_cancel_and_direct_execute_cannot_mutate(self):
        ui = self.ui
        operator = SimpleNamespace(
            action="delete-character", report=mock.Mock(),
        )
        backend = SimpleNamespace(apply_management=mock.Mock())
        with mock.patch.object(ui, "_get_backend", return_value=backend):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage.execute(operator, object()),
                             {'CANCELLED'})
        backend.apply_management.assert_not_called()

    def test_overlay_is_timeline_only_and_does_not_create_channels(self):
        self.assertTrue(self.overlay.is_timeline_region(
            SimpleNamespace(type='SEQUENCE_EDITOR'), SimpleNamespace(type='WINDOW'),
            SimpleNamespace(view_type='SEQUENCER'),
        ))
        self.assertFalse(self.overlay.is_timeline_region(
            SimpleNamespace(type='SEQUENCE_EDITOR'), SimpleNamespace(type='PREVIEW'),
            SimpleNamespace(view_type='SEQUENCER_PREVIEW'),
        ))
        self.assertEqual(len(self.overlay.visible_lanes(view(), 150)), 3)

    def test_every_management_action_uses_same_confirmed_private_command(self):
        ui = self.ui
        location = review_shell.ReviewLocation(Path("companion"), Path("video.mp4"), 5, 105)
        current = review_shell.ReviewView(
            "revision", "source", 5, 640, 480, "analyzed-with-candidates", "Candidates",
            (review_shell.CandidateRow("revision", "observation", (1, 2, 3, 4),
                                       "Candidate 1", "amy", "Amy", 1),),
            (review_shell.CharacterRow("amy", "Amy", "observation", "visible"),),
        )
        selected = ReviewKeySelection()
        selected.click(view(), "key-a")
        choices = (
            "clear-assignment", "clear-review-key", "clear-selected-review-keys",
            "clear-all-review-keys-on-frame", "rename-character", "delete-character",
        )
        for action in choices:
            with self.subTest(action=action):
                request = SimpleNamespace(
                    action=action, project_sha256="hash", character_id="amy",
                    keyframe_ids=("key-a",), affected_count=1,
                )
                backend = SimpleNamespace(prepare_management=mock.Mock(return_value=request),
                                          apply_management=mock.Mock(return_value=(None, None, None)))
                selection = review_shell.SelectionState()
                selection.character_id = "amy"
                wm = SimpleNamespace(invoke_props_dialog=mock.Mock(return_value={'RUNNING_MODAL'}))
                context = SimpleNamespace(window_manager=wm)

                class OperatorDouble:
                    def __init__(self):
                        self.action = action
                        self.new_name = ""
                        self.messages = []

                    def report(self, level, message):
                        self.messages.append((level, message))

                    def execute(self, ctx):
                        return ui.BLENDCAP_OT_vta_review_manage.execute(self, ctx)

                    def _arguments(self):
                        return ui.BLENDCAP_OT_vta_review_manage._arguments(self)

                operator = OperatorDouble()
                with mock.patch.object(ui, "_view", current), \
                     mock.patch.object(ui, "_timeline", view()), \
                     mock.patch.object(ui, "_location", location), \
                     mock.patch.object(ui, "_selection", selection), \
                     mock.patch.object(ui, "_review_key_selection", selected), \
                     mock.patch.object(ui, "refresh", return_value=True), \
                     mock.patch.object(ui, "_get_backend", return_value=backend):
                    self.assertEqual(
                        ui.BLENDCAP_OT_vta_review_manage.invoke(operator, context, None),
                        {'RUNNING_MODAL'},
                    )
                    backend.apply_management.assert_not_called()  # Cancel path
                    self.assertEqual(operator.execute(context), {'FINISHED'})
                    backend.apply_management.assert_called_once()
                    self.assertTrue(backend.apply_management.call_args.kwargs["confirmed"])

    def test_stale_character_selection_blocks_confirmed_delete(self):
        ui = self.ui
        location = review_shell.ReviewLocation(Path("companion"), Path("video.mp4"), 5, 105)
        selected = review_shell.SelectionState()
        selected.character_id = "other"
        request = SimpleNamespace(action="delete-character", character_id="amy",
                                  project_sha256="hash", keyframe_ids=("key-a",))
        backend = SimpleNamespace(apply_management=mock.Mock())
        operator = SimpleNamespace(
            action="delete-character", _pending_request=request,
            _pending_location=location, report=mock.Mock(),
        )
        with mock.patch.object(ui, "_location", location), \
             mock.patch.object(ui, "_selection", selected), \
             mock.patch.object(ui, "refresh", return_value=True), \
             mock.patch.object(ui, "_get_backend", return_value=backend):
            self.assertEqual(ui.BLENDCAP_OT_vta_review_manage.execute(operator, object()),
                             {'CANCELLED'})
        backend.apply_management.assert_not_called()

    def test_shared_management_renderer_exposes_six_operator_actions(self):
        ui = self.ui
        class Layout:
            def __init__(self):
                self.actions = []
                self.operator_context = None

            def row(self):
                return self

            def operator(self, name, text):
                self.actions.append((name, text))
                return SimpleNamespace(action=None)

        current = review_shell.ReviewView(
            "revision", "source", 5, 640, 480, "analyzed-with-candidates", "Candidates",
            (), (review_shell.CharacterRow("amy", "Amy", "observation", "visible"),),
        )
        selected = ReviewKeySelection()
        selected.click(view(), "key-a")
        character = review_shell.SelectionState()
        character.character_id = "amy"
        layout = Layout()
        with mock.patch.object(ui, "_view", current), \
             mock.patch.object(ui, "_timeline", view()), \
             mock.patch.object(ui, "_selection", character), \
             mock.patch.object(ui, "_review_key_selection", selected):
            ui._draw_management_actions(layout)
        self.assertEqual(len(layout.actions), 6)
        self.assertEqual({item[0] for item in layout.actions}, {"blendcap.vta_review_manage"})
        self.assertEqual(layout.operator_context, 'INVOKE_DEFAULT')


if __name__ == "__main__":
    unittest.main()
