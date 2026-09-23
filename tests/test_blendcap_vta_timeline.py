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

    def test_tool_and_native_menu_share_management_operator(self):
        ui = self.ui
        self.assertEqual(ui.BLENDCAP_WST_vta_review_key_select.bl_context_mode, 'SEQUENCER')
        self.assertEqual(ui.BLENDCAP_WST_vta_review_key_select_combined.bl_context_mode,
                         'SEQUENCER_PREVIEW')
        self.assertEqual(len(ui.BLENDCAP_WST_vta_review_key_select.bl_keymap), 2)
        self.assertEqual({item[0] for item in ui.BLENDCAP_WST_vta_review_key_select.bl_keymap},
                         {"blendcap.vta_review_key_select"})
        self.assertIn(ui.BLENDCAP_OT_vta_review_manage, ui.classes)
        self.assertNotIn("BLENDCAP_MT_vta_review", vars(ui))
        self.assertTrue(callable(ui._draw_vta_select))
        self.assertTrue(callable(ui._draw_vta_context))

    def test_click_selection_navigates_by_key_id(self):
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
        scene.frame_set.assert_called_once_with(105)

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
