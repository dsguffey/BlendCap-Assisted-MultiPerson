"""Blender-independent VSE overlay projection tests."""
import unittest

from blendcap.vta_overlay import (
    bind_vse_preview_scene,
    build_overlay_candidates,
    find_scene_owning_source,
    source_box_to_region,
)
from blendcap_vta_review import CandidateRow, ReviewView


class View2D:
    def view_to_region(self, x, y, clip=False):
        return (x * 2 + 10, y * 3 + 20)


def view(state="analyzed-with-candidates"):
    return ReviewView("rev", "src", 3, 100, 100, state, state, (
        CandidateRow("rev", "b", (10, 20, 30, 40), "Candidate 1", None, None),
        CandidateRow("rev", "a", (50, 60, 70, 80), "Candidate 2", "char", "Ada"),
    ) if state == "analyzed-with-candidates" else (), ())


class OverlayTests(unittest.TestCase):
    def test_mapping_uses_single_lower_left_view_transform(self):
        self.assertEqual(
            source_box_to_region(View2D(), (0, 0, 100, 50), 100, 50),
            (-90.0, -55.0, 110.0, 95.0),
        )

    def test_rows_preserve_durable_selection_and_authoritative_label(self):
        rows = build_overlay_candidates(view(), "a")
        self.assertEqual(rows[1].label, "Candidate 2 — Ada")
        self.assertTrue(rows[1].selected)
        self.assertFalse(rows[0].selected)

    def test_empty_states_clear_overlay(self):
        self.assertEqual(build_overlay_candidates(view("not-analyzed"), "a"), ())
        self.assertEqual(build_overlay_candidates(view("analyzed-empty"), "a"), ())

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
