"""Blender-independent VSE overlay projection tests."""
import unittest

from blendcap.vta_overlay import (
    OverlayRegionCandidate,
    bind_vse_preview_scene,
    build_overlay_candidates,
    find_scene_owning_source,
    hit_test_overlay_candidates,
    hit_test_review_candidate,
    is_vse_preview_region,
    layout_overlay_labels,
    source_box_to_region,
)
from blendcap_vta_review import CandidateRow, ReviewView, SelectionState


class View2D:
    def view_to_region(self, x, y, clip=False):
        return (x * 2 + 10, y * 3 + 20)


def view(state="analyzed-with-candidates"):
    return ReviewView("rev", "src", 3, 100, 100, state, state, (
        CandidateRow("rev", "b", (10, 20, 30, 40), "Candidate 1", None, None),
        CandidateRow("rev", "a", (50, 60, 70, 80), "Candidate 2", "char", "Ada"),
    ) if state == "analyzed-with-candidates" else (), ())


class OverlayTests(unittest.TestCase):
    @staticmethod
    def region(observation_id, box, assigned=None):
        return OverlayRegionCandidate(
            observation_id, observation_id, box, assigned, False
        )

    def test_mapping_uses_single_lower_left_view_transform(self):
        self.assertEqual(
            source_box_to_region(View2D(), (0, 0, 100, 50), 100, 50),
            (-90.0, -55.0, 110.0, 95.0),
        )

    def test_rows_preserve_durable_selection_and_authoritative_label(self):
        rows = build_overlay_candidates(view(), "a")
        self.assertEqual(rows[0].label, "Candidate 1")
        self.assertEqual(rows[1].label, "Ada")
        self.assertEqual(rows[1].observation_id, "a")
        self.assertTrue(rows[1].selected)
        self.assertFalse(rows[0].selected)

    def test_empty_states_clear_overlay(self):
        self.assertEqual(build_overlay_candidates(view("not-analyzed"), "a"), ())
        self.assertEqual(build_overlay_candidates(view("analyzed-empty"), "a"), ())

    def test_single_candidate_hit_and_empty_click(self):
        candidates = (self.region("durable-a", (10, 20, 30, 40)),)
        self.assertEqual(
            hit_test_overlay_candidates(candidates, (20, 30)), "durable-a"
        )
        self.assertIsNone(hit_test_overlay_candidates(candidates, (9, 30)))

    def test_only_vse_preview_region_accepts_tool_context(self):
        self.assertTrue(
            is_vse_preview_region('SEQUENCE_EDITOR', 'PREVIEW', 'PREVIEW')
        )
        self.assertTrue(
            is_vse_preview_region(
                'SEQUENCE_EDITOR', 'PREVIEW', 'SEQUENCER_PREVIEW'
            )
        )
        self.assertFalse(
            is_vse_preview_region('SEQUENCE_EDITOR', 'WINDOW', 'PREVIEW')
        )
        self.assertFalse(
            is_vse_preview_region('SEQUENCE_EDITOR', 'PREVIEW', 'SEQUENCER')
        )
        self.assertFalse(
            is_vse_preview_region('VIEW_3D', 'PREVIEW', 'PREVIEW')
        )

    def test_global_coordinates_do_not_match_region_local_box(self):
        candidates = (self.region("durable-a", (10, 20, 30, 40)),)
        self.assertIsNone(hit_test_overlay_candidates(candidates, (1010, 1020)))

    def test_smallest_containing_box_wins(self):
        candidates = (
            self.region("large", (0, 0, 100, 100)),
            self.region("small", (25, 25, 50, 50)),
        )
        self.assertEqual(hit_test_overlay_candidates(candidates, (30, 30)), "small")

    def test_equal_area_tie_and_reorder_use_observation_id(self):
        first = self.region("observation-b", (0, 0, 20, 20))
        second = self.region("observation-a", (5, 5, 25, 25))
        self.assertEqual(
            hit_test_overlay_candidates((first, second), (10, 10)),
            "observation-a",
        )
        self.assertEqual(
            hit_test_overlay_candidates((second, first), (10, 10)),
            "observation-a",
        )

    def test_overlay_disabled_has_no_hit(self):
        candidates = (self.region("durable-a", (0, 0, 20, 20)),)
        self.assertIsNone(
            hit_test_overlay_candidates(candidates, (10, 10), enabled=False)
        )

    def test_replacement_with_empty_review_has_no_stale_hit_region(self):
        old = view()
        replacement = view("analyzed-empty")
        self.assertIsNotNone(
            hit_test_review_candidate(old, "", View2D(), (-50, -40))
        )
        self.assertIsNone(
            hit_test_review_candidate(replacement, "", View2D(), (-50, -40))
        )

    def test_overlay_selection_uses_panel_selection_store(self):
        current = view()
        selection = SelectionState()
        selection.accept_review(current)
        self.assertTrue(selection.select_observation(current, "a"))
        rows = build_overlay_candidates(current, selection.observation_id)
        self.assertEqual([row.observation_id for row in rows if row.selected], ["a"])

    def test_assigned_candidate_remains_clickable(self):
        assigned = self.region("assigned", (0, 0, 20, 20), "Ada")
        self.assertEqual(
            hit_test_overlay_candidates((assigned,), (10, 10)), "assigned"
        )

    def test_label_plates_avoid_overlap_and_remain_near_boxes(self):
        candidates = (
            OverlayRegionCandidate("a", "Candidate 1", (20, 20, 90, 80), None, False),
            OverlayRegionCandidate("b", "Candidate 2", (25, 25, 95, 82), None, False),
        )
        placements = layout_overlay_labels(candidates, lambda _label: (86, 15), 300, 180)
        first, second = (placement.rect for placement in placements)
        self.assertEqual([item.observation_id for item in placements], ["a", "b"])
        self.assertTrue(first[2] <= second[0] or second[2] <= first[0] or
                        first[3] <= second[1] or second[3] <= first[1])
        self.assertTrue(all(0 <= rect[0] < rect[2] <= 300 and
                            0 <= rect[1] < rect[3] <= 180 for rect in (first, second)))
        self.assertEqual(layout_overlay_labels(candidates, lambda _label: (86, 15), 300, 180),
                         placements)

    def test_preview_binds_scene_owning_authoritative_strip_without_hardcoded_name(self):
        class Strip:
            def __init__(self, source): self.source = source
        class Scene:
            def __init__(self, name, source):
                self.name = name
                self.strips = (Strip(source),)
        unrelated = Scene("Scene", "other.mp4")
        owner = Scene("Wrestling Review", "authoritative.mp4")
        self.assertIs(
            find_scene_owning_source(
                (unrelated, owner), "authoritative.mp4",
                lambda scene: scene.strips, lambda strip: strip.source,
                preferred_scene=unrelated,
            ),
            owner,
        )

    def test_existing_correct_scene_binding_is_preserved(self):
        class Workspace:
            sequencer_scene = None
        class Space:
            view_type = 'PREVIEW'
            display_channel = 0
        class Spaces:
            active = Space()
        class Area:
            type = 'SEQUENCE_EDITOR'
            spaces = Spaces()
            redrawn = 0
            def tag_redraw(self): self.redrawn += 1
        owner = object()
        workspace = Workspace()
        workspace.sequencer_scene = owner
        area = Area()
        self.assertEqual(bind_vse_preview_scene(workspace, (area,), owner), 0)
        self.assertIs(workspace.sequencer_scene, owner)
        self.assertEqual(area.redrawn, 1)

    def test_wrong_or_missing_scene_binding_is_corrected_without_creating_content(self):
        class Space:
            view_type = 'PREVIEW'
            display_channel = 3
        class Spaces:
            active = Space()
        class Area:
            type = 'SEQUENCE_EDITOR'
            spaces = Spaces()
            redrawn = 0
            def tag_redraw(self): self.redrawn += 1
        class Workspace:
            sequencer_scene = None
        owner = object()
        workspace = Workspace()
        area = Area()
        self.assertEqual(bind_vse_preview_scene(workspace, [area], owner), 2)
        self.assertIs(workspace.sequencer_scene, owner)
        self.assertEqual(area.spaces.active.display_channel, 0)
        self.assertEqual(bind_vse_preview_scene(workspace, [area], owner), 0)
        self.assertEqual(area.redrawn, 2)

    def test_missing_preview_area_defers_safely_without_creating_content(self):
        class Workspace:
            sequencer_scene = None
        class Area:
            type = 'VIEW_3D'
            redrawn = 0
            def tag_redraw(self): self.redrawn += 1
        owner = object()
        workspace = Workspace()
        area = Area()
        self.assertEqual(bind_vse_preview_scene(workspace, (area,), owner), 1)
        self.assertIs(workspace.sequencer_scene, owner)
        self.assertEqual(area.redrawn, 0)

    def test_ambiguous_source_owner_is_rejected_without_creating_content(self):
        class Scene:
            def __init__(self, name): self.name = name
        class Strip:
            source = "same.mp4"
        first, second = Scene("First"), Scene("Second")
        with self.assertRaisesRegex(ValueError, "More than one"):
            find_scene_owning_source(
                (first, second), "same.mp4", lambda _scene: (Strip(),),
                lambda strip: strip.source,
            )
