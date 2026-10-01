"""Focused tests for the public, Blender-independent VTA review shell."""

from dataclasses import dataclass
from enum import Enum
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

import blendcap_vta_review as review_ui

try:
    import bpy  # noqa: F401 - Blender-specific operator tests below
except ImportError:
    bpy = None


class State(Enum):
    NOT_ANALYZED = "not-analyzed"
    ANALYZED_EMPTY = "analyzed-empty"
    CANDIDATES = "analyzed-with-candidates"


@dataclass(frozen=True)
class Candidate:
    analysis_revision_id: str
    observation_id: str
    frame: int = 12
    box_xyxy: tuple[float, float, float, float] = (10.0, 20.0, 30.0, 40.0)
    assigned_character_id: str | None = None


@dataclass(frozen=True)
class Character:
    character_id: str
    display_name: str


@dataclass(frozen=True)
class Assignment:
    character_id: str
    observation_id: str


@dataclass(frozen=True)
class Decision:
    character_id: str
    keyframe_id: str
    state: object
    observation_id: str | None = None


@dataclass(frozen=True)
class Review:
    analysis_revision_id: str = "revision-1"
    source_id: str = "source-1"
    frame: int = 12
    state: State = State.CANDIDATES
    candidates: tuple = (
        Candidate("revision-1", "observation-b"),
        Candidate("revision-1", "observation-a"),
    )
    characters: tuple = (
        Character("character-red", "Red"),
        Character("character-black", "Black"),
    )
    character_assignments: tuple = ()
    character_decisions: tuple = ()


class ReviewPresentationTests(unittest.TestCase):
    def test_hard_state_projection_is_distinct_from_unreviewed_and_candidate_occupancy(self):
        occluded = Review(
            character_decisions=(Decision("character-red", "hard-1", SimpleNamespace(value="occluded")),),
        )
        view = review_ui.present_review(occluded)
        red = next(item for item in view.characters if item.character_id == "character-red")
        black = next(item for item in view.characters if item.character_id == "character-black")
        self.assertEqual(red.decision_state, "occluded")
        self.assertIsNone(red.occupied_observation_id)
        self.assertIsNone(black.decision_state)
        self.assertTrue(all(item.assigned_character_id is None for item in view.candidates))
        selection = review_ui.SelectionState()
        self.assertTrue(selection.select_character(view, "character-red"))
        self.assertIsNone(selection.hard_state_intent(view, "occluded"))
        intent = selection.hard_state_intent(view, "not-visible")
        self.assertTrue(intent.requires_confirmation)
        self.assertIn("Occluded", " ".join(review_ui.hard_state_confirmation_lines(intent)))

    def test_reacquisition_from_hard_state_requires_confirmed_assign(self):
        view = review_ui.present_review(Review(
            character_decisions=(Decision("character-red", "hard-1", SimpleNamespace(value="no-valid-candidate")),),
        ))
        selection = review_ui.SelectionState()
        self.assertTrue(selection.select_character(view, "character-red"))
        self.assertTrue(selection.select_observation(view, "observation-a"))
        intent = selection.assignment_intent(view)
        self.assertTrue(intent.requires_confirmation)
        self.assertIn("No Valid Candidate", " ".join(review_ui.confirmation_lines(intent)))

    def test_hard_state_operator_is_drawn_and_registered(self):
        root = Path(__file__).resolve().parents[1]
        ui = ast.parse((root / "blendcap" / "vta_review_ui.py").read_text(encoding="utf-8"))
        registration = ast.parse((root / "blendcap" / "registration.py").read_text(encoding="utf-8"))
        panel = next(item for item in ui.body if isinstance(item, ast.ClassDef) and item.name == "VIEW3D_PT_blendcap_vta_current_frame")
        draw = next(item for item in panel.body if isinstance(item, ast.FunctionDef) and item.name == "draw")
        calls = [item for item in ast.walk(draw) if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "operator"]
        self.assertIn("blendcap.vta_review_hard_state", [item.args[0].value for item in calls if item.args and isinstance(item.args[0], ast.Constant)])
        imports = [item for item in registration.body if isinstance(item, ast.ImportFrom) and item.module == "vta_review_ui"]
        self.assertIn("BLENDCAP_OT_vta_review_hard_state", {alias.name for item in imports for alias in item.names})
        self.assertIn("BLENDCAP_OT_vta_review_hard_state", {item.id for item in ast.walk(registration) if isinstance(item, ast.Name)})

    def test_new_character_selection_does_not_select_or_assign_candidate(self):
        view = review_ui.present_review(Review(characters=(Character("new-id", "Wrestler A"),)))
        selection = review_ui.SelectionState()

        self.assertTrue(selection.select_character(view, "new-id"))
        self.assertEqual(selection.character_id, "new-id")
        self.assertEqual(selection.observation_id, "")
        self.assertTrue(all(candidate.assigned_character_id is None for candidate in view.candidates))
        self.assertFalse(selection.can_assign(view))

    def test_assigned_candidate_selects_its_stable_character_unassigned_preserves_context(self):
        view = review_ui.present_review(Review(
            candidates=(
                Candidate("revision-1", "assigned", assigned_character_id="character-red"),
                Candidate("revision-1", "unassigned"),
            ),
            character_assignments=(Assignment("character-red", "assigned"),),
        ))
        selection = review_ui.SelectionState()
        self.assertTrue(selection.select_character(view, "character-black"))
        self.assertTrue(selection.select_observation(view, "assigned"))
        self.assertEqual(selection.character_id, "character-red")
        self.assertTrue(selection.select_observation(view, "unassigned"))
        self.assertEqual(selection.character_id, "character-red")
        self.assertFalse(selection.select_observation(view, "nonexistent"))
        self.assertEqual(selection.character_id, "character-red")

    def test_blender_create_action_is_drawn_and_registered(self):
        repo_root = Path(__file__).resolve().parents[1]
        ui_tree = ast.parse((repo_root / "blendcap" / "vta_review_ui.py").read_text(encoding="utf-8"))
        registration_tree = ast.parse(
            (repo_root / "blendcap" / "registration.py").read_text(encoding="utf-8")
        )

        create_class = next(
            node for node in ui_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "BLENDCAP_OT_vta_review_create_character"
        )
        self.assertIn(
            "blendcap.vta_review_create_character",
            {
                node.value.value
                for node in create_class.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "bl_idname"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
            },
        )

        panel = next(
            node for node in ui_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "VIEW3D_PT_blendcap_vta_characters"
        )
        draw = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        drawn_operator_ids = {
            call.args[0].value
            for call in ast.walk(draw)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "operator"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        }
        self.assertIn("blendcap.vta_review_create_character", drawn_operator_ids)

        review_import = next(
            node for node in registration_tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "vta_review_ui"
        )
        imported_names = {alias.name for alias in review_import.names}
        self.assertIn("BLENDCAP_OT_vta_review_create_character", imported_names)

        classes_assignment = next(
            node for node in registration_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "classes" for target in node.targets)
        )
        registered_names = {
            node.id for node in ast.walk(classes_assignment.value) if isinstance(node, ast.Name)
        }
        self.assertIn("BLENDCAP_OT_vta_review_create_character", registered_names)

    def test_character_selector_is_alphabetical_by_display_name(self):
        review = Review(characters=(Character("z-id", "Zebra"), Character("a-id", "Amy")))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            source_hash = review_ui.sha256_file(source)

            class Project:
                def to_dict(self):
                    return {
                        "sources": [{
                            "source_id": "source-1", "video_sha256": source_hash,
                            "start_frame": 0, "end_frame": 20,
                        }],
                        "active_analysis_revision_by_source": {"source-1": "revision-1"},
                    }

            class Core:
                load_project_state = staticmethod(lambda _root: Project())
                load_analysis_cache = staticmethod(lambda _root, _revision: object())
                build_frame_review = staticmethod(lambda *_args, **_kwargs: review)

            backend = review_ui.PrivateCoreBackend(importer=lambda _name: Core())
            queried = backend.query(review_ui.ReviewLocation(
                Path(directory) / "scene_vta", source, 12, 13
            ))

        self.assertEqual([row.display_name for row in queried.characters], ["Amy", "Zebra"])

    def test_character_name_collision_matches_private_trim_casefold_rule(self):
        view = review_ui.present_review(Review())

        self.assertTrue(review_ui.character_name_exists(view, " RED "))
        self.assertFalse(review_ui.character_name_exists(view, "Red Wrestler"))
        self.assertFalse(review_ui.character_name_exists(view, "  "))

    def test_review_states_translate_explicitly(self):
        expected = {
            State.NOT_ANALYZED: "Not Analyzed",
            State.ANALYZED_EMPTY: "Analyzed Empty",
            State.CANDIDATES: "Candidates Available",
        }
        for state, label in expected.items():
            with self.subTest(state=state):
                view = review_ui.present_review(Review(state=state, candidates=()))
                self.assertEqual(view.state_label, label)

    def test_durable_observation_identity_survives_presentation_reordering(self):
        first = review_ui.present_review(Review())
        second = review_ui.present_review(
            Review(candidates=tuple(reversed(Review().candidates)))
        )
        self.assertEqual(
            {item.observation_id for item in first.candidates},
            {item.observation_id for item in second.candidates},
        )
        self.assertNotEqual(first.candidates[0].label, second.candidates[0].label)

    def test_assignment_is_visible_after_refresh(self):
        view = review_ui.present_review(
            Review(
                candidates=(Candidate("revision-1", "observation-a", assigned_character_id="character-red"),),
                character_assignments=(Assignment("character-red", "observation-a"),),
            )
        )
        self.assertEqual(view.candidates[0].assigned_character_name, "Red")
        self.assertFalse(view.candidates[0].assignable)
        red = next(item for item in view.characters if item.character_id == "character-red")
        self.assertFalse(red.available)

    def test_stale_candidate_clears_on_frame_or_revision_change(self):
        selection = review_ui.SelectionState()
        first = review_ui.present_review(Review())
        selection.accept_review(first)
        selection.observation_id = "observation-a"
        selection.character_id = "character-red"
        selection.accept_review(review_ui.present_review(Review(frame=13)))
        self.assertEqual(selection.observation_id, "")
        self.assertEqual(selection.character_id, "character-red")
        selection.observation_id = "observation-a"
        selection.accept_review(
            review_ui.present_review(Review(analysis_revision_id="revision-2"))
        )
        self.assertEqual(selection.observation_id, "")

    def test_same_assignment_is_no_op_but_occupied_choices_can_reassign(self):
        view = review_ui.present_review(
            Review(
                candidates=(
                    Candidate("revision-1", "observation-a", assigned_character_id="character-red"),
                    Candidate("revision-1", "observation-b"),
                ),
                character_assignments=(Assignment("character-red", "observation-a"),),
            )
        )
        selection = review_ui.SelectionState()
        selection.accept_review(view)
        self.assertTrue(selection.select_observation(view, "observation-a"))
        self.assertEqual(selection.observation_id, "observation-a")
        selection.character_id = "character-red"
        self.assertFalse(selection.can_assign(view))
        selection.character_id = "character-black"
        self.assertTrue(selection.can_assign(view))
        self.assertTrue(selection.assignment_intent(view).requires_confirmation)
        selection.observation_id = "observation-b"
        selection.character_id = "character-red"
        self.assertTrue(selection.can_assign(view))
        self.assertTrue(selection.assignment_intent(view).requires_confirmation)

    def test_confirmation_describes_both_conflicts_without_internal_ids(self):
        view = review_ui.present_review(
            Review(
                candidates=(
                    Candidate("revision-1", "observation-a", assigned_character_id="character-red"),
                    Candidate("revision-1", "observation-b", assigned_character_id="character-black"),
                ),
                character_assignments=(
                    Assignment("character-red", "observation-a"),
                    Assignment("character-black", "observation-b"),
                ),
            )
        )
        selection = review_ui.SelectionState()
        self.assertTrue(selection.select_observation(view, "observation-a"))
        self.assertTrue(selection.select_character(view, "character-black"))
        intent = selection.assignment_intent(view)
        self.assertTrue(intent.requires_confirmation)
        lines = review_ui.confirmation_lines(intent)
        self.assertIn("Red", " ".join(lines))
        self.assertIn("Black", " ".join(lines))
        self.assertIn("Candidate 1", " ".join(lines))
        self.assertIn("Candidate 2", " ".join(lines))
        self.assertIn("unassigned", " ".join(lines))
        self.assertNotIn("observation-a", " ".join(lines))
        self.assertNotIn("character-black", " ".join(lines))

    def test_valid_selection_uses_durable_ids(self):
        view = review_ui.present_review(Review())
        selection = review_ui.SelectionState()
        selection.accept_review(view)
        selection.observation_id = "observation-b"
        selection.character_id = "character-black"
        self.assertTrue(selection.can_assign(view))
        self.assertEqual(view.candidates[0].analysis_revision_id, "revision-1")

    def test_backend_builds_request_from_durable_selection_and_reloads(self):
        calls = []

        class Project:
            def to_dict(self):
                return {
                    "sources": [{
                        "source_id": "source-1",
                        "video_sha256": source_hash,
                        "start_frame": 0,
                        "end_frame": 20,
                    }],
                    "active_analysis_revision_by_source": {
                        "source-1": "revision-1",
                    },
                }

        class Core:
            @staticmethod
            def load_project_state(_root):
                calls.append("load-project")
                return Project()

            @staticmethod
            def load_analysis_cache(_root, revision_id):
                calls.append(("load-cache", revision_id))
                return object()

            @staticmethod
            def build_frame_review(_project, _cache, **values):
                calls.append(("build-review", values))
                return Review()

            @staticmethod
            def build_review_decision_request(_review, **values):
                calls.append(("build-request", values))
                return values

            @staticmethod
            def apply_review_decision_and_persist(_root, _cache, request):
                calls.append(("apply", request))
                return object()

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            source_hash = review_ui.sha256_file(source)
            backend = review_ui.PrivateCoreBackend(importer=lambda _name: Core())
            location = review_ui.ReviewLocation(
                Path(directory) / "scene_vta", source, 12, 13
            )
            _receipt, refreshed = backend.apply(
                location,
                observation_id="observation-b",
                character_id="character-black",
            )

        request_call = next(item for item in calls if isinstance(item, tuple) and item[0] == "build-request")
        self.assertEqual(request_call[1], {
            "analysis_revision_id": "revision-1",
            "observation_id": "observation-b",
            "character_id": "character-black",
        })
        self.assertEqual(refreshed.source_frame, 12)
        self.assertEqual(calls.count("load-project"), 2)

    def test_backend_prepares_conflict_without_mutation_then_applies_and_reloads(self):
        calls = []
        prepared = type("Request", (), {
            "observation_id": "observation-a",
            "character_id": "character-black",
        })()

        class Project:
            def to_dict(self):
                return {
                    "sources": [{
                        "source_id": "source-1", "video_sha256": source_hash,
                        "start_frame": 0, "end_frame": 20,
                    }],
                    "active_analysis_revision_by_source": {"source-1": "revision-1"},
                }

        class Core:
            @staticmethod
            def load_project_state(_root):
                calls.append("load-project")
                return Project()

            @staticmethod
            def load_analysis_cache(_root, _revision_id):
                return object()

            @staticmethod
            def build_frame_review(_project, _cache, **_values):
                return Review(
                    candidates=(Candidate("revision-1", "observation-a", assigned_character_id="character-red"),),
                    character_assignments=(Assignment("character-red", "observation-a"),),
                )

            @staticmethod
            def build_confirmed_reassignment_request(_review, **values):
                calls.append(("prepare", values))
                return prepared

            @staticmethod
            def apply_confirmed_reassignment_and_persist(_root, _cache, request):
                calls.append(("apply", request))
                return object()

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            source_hash = review_ui.sha256_file(source)
            backend = review_ui.PrivateCoreBackend(importer=lambda _name: Core())
            location = review_ui.ReviewLocation(Path(directory) / "scene_vta", source, 12, 13)
            request, current = backend.prepare_reassignment(
                location,
                observation_id="observation-a",
                character_id="character-black",
            )
            self.assertEqual(request, prepared)
            self.assertEqual(current.candidates[0].assigned_character_name, "Red")
            self.assertFalse(any(item[0] == "apply" for item in calls if isinstance(item, tuple)))
            _receipt, refreshed = backend.apply_confirmed_reassignment(location, request)

        self.assertIn(("apply", prepared), calls)
        self.assertEqual(refreshed.source_frame, 12)
        self.assertGreaterEqual(calls.count("load-project"), 3)

    def test_backend_creates_through_private_persistence_then_reloads(self):
        calls = []
        created = Character("character-new", "Wrestler A")

        class Project:
            def to_dict(self):
                return {
                    "sources": [{
                        "source_id": "source-1",
                        "video_sha256": source_hash,
                        "start_frame": 0,
                        "end_frame": 20,
                    }],
                    "active_analysis_revision_by_source": {"source-1": "revision-1"},
                }

        class Core:
            @staticmethod
            def load_project_state(_root):
                calls.append("load-project")
                return Project()

            @staticmethod
            def load_analysis_cache(_root, revision_id):
                calls.append(("load-cache", revision_id))
                return object()

            @staticmethod
            def create_character_and_persist(root, display_name):
                calls.append(("create-character", root, display_name))
                return created

            @staticmethod
            def build_frame_review(_project, _cache, **values):
                calls.append(("build-review", values))
                return Review(characters=(created,))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            source_hash = review_ui.sha256_file(source)
            root = Path(directory) / "scene_vta"
            location = review_ui.ReviewLocation(root, source, 12, 13)
            backend = review_ui.PrivateCoreBackend(importer=lambda _name: Core())
            character, refreshed = backend.create_character(
                location, display_name="Wrestler A"
            )

        self.assertEqual(character, created)
        self.assertEqual([row.character_id for row in refreshed.characters], ["character-new"])
        self.assertEqual(refreshed.candidates[0].assigned_character_id, None)
        self.assertIn(("create-character", root, "Wrestler A"), calls)
        self.assertEqual(calls.count("load-project"), 2)

    def test_private_error_codes_map_to_user_messages(self):
        class Code(Enum):
            VALUE = "candidate-occupied"

        class Error(Exception):
            code = Code.VALUE

        self.assertEqual(
            review_ui.user_message(Error("internal")),
            "That candidate is already assigned.",
        )

    def test_vse_mapping_is_explicit_and_bounded(self):
        self.assertEqual(
            review_ui.map_vse_frame_to_source(
                blender_frame=110,
                visible_start=100,
                visible_end=120,
                source_offset=5,
            ),
            15,
        )
        with self.assertRaisesRegex(review_ui.VtaReviewUiError, "outside"):
            review_ui.map_vse_frame_to_source(
                blender_frame=99,
                visible_start=100,
                visible_end=120,
                source_offset=5,
            )

    def test_visible_analysis_range_is_inclusive_and_uses_blender_frames(self):
        location = review_ui.ReviewLocation(Path('C:/project'), Path('C:/video.mp4'), 0, 1)
        self.assertEqual(review_ui.map_visible_analysis_range(
            location, 0, 162, 4, 10), (3, 9))
        self.assertEqual(review_ui.visible_frame(location, 3), 4)
        self.assertEqual(review_ui.map_visible_analysis_range(
            location, 0, 162, 1, 163), (0, 162))
        for start, end, message in ((0, 10, "within"), (4, 164, "within"),
                                    (10, 4, "End Frame"), (True, 10, "whole")):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(
                review_ui.VtaReviewUiError, message):
                review_ui.map_visible_analysis_range(location, 0, 162, start, end)
        shifted = review_ui.ReviewLocation(Path('C:/project'), Path('C:/video.mp4'), 5, 105)
        self.assertEqual(review_ui.map_visible_analysis_range(
            shifted, 0, 162, 104, 110), (4, 10))

    def test_source_coverage_does_not_require_an_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            source.write_bytes(b'video')
            digest = review_ui.sha256_file(source)
            project = SimpleNamespace(to_dict=lambda: {'sources': [{
                'source_id': 'source', 'video_sha256': digest,
                'start_frame': 0, 'end_frame': 162,
            }]})
            core = SimpleNamespace(load_project_state=lambda _root: project)
            backend = review_ui.PrivateCoreBackend(core=core)
            location = review_ui.ReviewLocation(Path(directory), source, 0, 1)
            self.assertEqual(backend.source_coverage(location), (0, 162))

    def test_companion_root_is_exactly_beside_saved_blend(self):
        self.assertEqual(
            review_ui.companion_root_for_blend(Path("C:/work/scene.blend")),
            Path("C:/work/scene_vta"),
        )
        with self.assertRaisesRegex(review_ui.VtaReviewUiError, "Save"):
            review_ui.companion_root_for_blend("")


@unittest.skipUnless(bpy is not None, "requires Blender Python")
class BlenderAssignOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package_name = "blendcap_vta_review_operator_test"
        spec = importlib.util.spec_from_file_location(
            package_name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = addon
        spec.loader.exec_module(addon)
        from blendcap_vta_review_operator_test.blendcap import vta_review_ui
        cls.panel_ui = vta_review_ui
        cls.real_sync_properties = staticmethod(vta_review_ui._sync_properties)

    def setUp(self):
        self.selection = review_ui.SelectionState()
        self.location = review_ui.ReviewLocation(
            Path("review_vta"), Path("source.mp4"), 12, 13
        )
        for name, value in (
            ("_selection", self.selection),
            ("_location", self.location),
            ("_view", None),
            ("_status", ""),
            ("_status_is_error", False),
        ):
            patcher = mock.patch.object(self.panel_ui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("_rebuild_enum_items", "_sync_properties"):
            patcher = mock.patch.object(self.panel_ui, name, lambda *_args: None)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.panel_ui, "refresh", lambda *_args, **_kwargs: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dialogs = []
        self.context = SimpleNamespace(
            window_manager=SimpleNamespace(
                invoke_props_dialog=lambda operator, **options: self.dialogs.append(options) or {'RUNNING_MODAL'}
            )
        )

    def _operator(self):
        panel_ui = self.panel_ui

        class OperatorDouble:
            def __init__(self):
                self.messages = []

            def report(self, level, message):
                self.messages.append((level, message))

            def execute(self, context):
                return panel_ui.BLENDCAP_OT_vta_review_assign.execute(self, context)

        return OperatorDouble()

    def test_preview_assigned_click_updates_current_character(self):
        current = review_ui.present_review(Review(
            candidates=(Candidate("revision-1", "assigned", assigned_character_id="character-red"),),
            character_assignments=(Assignment("character-red", "assigned"),),
        ), source_dimensions=(100, 100))
        self.panel_ui._view = current
        wm = SimpleNamespace(blendcap_vta_candidate="", blendcap_vta_character="character-black")
        context = SimpleNamespace(
            area=SimpleNamespace(type='SEQUENCE_EDITOR'),
            region=SimpleNamespace(type='PREVIEW', view2d=object()),
            space_data=SimpleNamespace(view_type='PREVIEW'),
            scene=SimpleNamespace(blendcap_show_vta_candidate_overlay=True),
            window_manager=wm,
        )

        def sync(ctx):
            ctx.window_manager.blendcap_vta_candidate = self.selection.observation_id
            ctx.window_manager.blendcap_vta_character = self.selection.character_id

        with mock.patch.object(self.panel_ui.vta_overlay, "build_overlay_region_candidates", return_value=()), \
             mock.patch.object(self.panel_ui.vta_overlay, "hit_test_overlay_candidates", return_value="assigned"), \
             mock.patch.object(self.panel_ui, "_sync_properties", side_effect=sync):
            self.assertTrue(self.panel_ui.select_overlay_candidate_at(context, (10, 10)))
        self.assertEqual(self.selection.character_id, "character-red")
        self.assertEqual(wm.blendcap_vta_character, "character-red")

    def test_sidebar_character_change_updates_shared_timeline_context(self):
        timeline = SimpleNamespace(characters=(
            SimpleNamespace(character_id="character-red"),
            SimpleNamespace(character_id="character-black"),
        ))
        wm = SimpleNamespace(blendcap_vta_character="character-black")
        with mock.patch.object(self.panel_ui, "_timeline", timeline), \
             mock.patch.object(self.panel_ui.vta_invalidation, "request_redraw") as redraw:
            self.panel_ui._character_changed(wm, None)
            self.assertEqual(self.panel_ui._lane_scroll(), 1)
        self.assertEqual(self.selection.character_id, "character-black")
        redraw.assert_called_once()

    def test_preview_unassigned_click_keeps_existing_character(self):
        current = review_ui.present_review(Review(), source_dimensions=(100, 100))
        self.panel_ui._view = current
        self.selection.character_id = "character-black"
        wm = SimpleNamespace(blendcap_vta_candidate="", blendcap_vta_character="character-black")
        context = SimpleNamespace(
            area=SimpleNamespace(type='SEQUENCE_EDITOR'),
            region=SimpleNamespace(type='PREVIEW', view2d=object()),
            space_data=SimpleNamespace(view_type='PREVIEW'),
            scene=SimpleNamespace(blendcap_show_vta_candidate_overlay=True),
            window_manager=wm,
        )

        def sync(ctx):
            ctx.window_manager.blendcap_vta_candidate = self.selection.observation_id
            ctx.window_manager.blendcap_vta_character = self.selection.character_id

        with mock.patch.object(self.panel_ui.vta_overlay, "build_overlay_region_candidates", return_value=()), \
             mock.patch.object(self.panel_ui.vta_overlay, "hit_test_overlay_candidates", return_value="observation-a"), \
             mock.patch.object(self.panel_ui, "_sync_properties", side_effect=sync):
            self.assertTrue(self.panel_ui.select_overlay_candidate_at(context, (10, 10)))
        self.assertEqual(self.selection.observation_id, "observation-a")
        self.assertEqual(wm.blendcap_vta_character, "character-black")

    def test_preview_click_selects_but_drag_is_consumed_without_selection(self):
        operator = object()
        with mock.patch.object(self.panel_ui, "select_overlay_candidate_at", return_value=True) as select:
            self.assertEqual(self.panel_ui.BLENDCAP_OT_vta_candidate_select.invoke(
                operator, None, SimpleNamespace(value='CLICK_DRAG')
            ), {'FINISHED'})
            select.assert_not_called()
            self.assertEqual(self.panel_ui.BLENDCAP_OT_vta_candidate_select.invoke(
                operator, None, SimpleNamespace(value='CLICK', mouse_region_x=10, mouse_region_y=12)
            ), {'FINISHED'})
            select.assert_called_once_with(None, (10, 12))
        for tool in (self.panel_ui.BLENDCAP_WST_vta_candidate_select_preview,
                     self.panel_ui.BLENDCAP_WST_vta_candidate_select_combined):
            self.assertEqual({entry[1]["value"] for entry in tool.bl_keymap},
                             {'CLICK', 'CLICK_DRAG'})

    def test_programmatic_refresh_does_not_override_reassignment_character(self):
        current = review_ui.present_review(Review(
            candidates=(Candidate("revision-1", "assigned", assigned_character_id="character-red"),),
            character_assignments=(Assignment("character-red", "assigned"),),
        ))
        self.panel_ui._view = current
        self.selection.accept_review(current)
        self.selection.observation_id = "assigned"
        self.selection.character_id = "character-black"  # deliberate reassignment intent
        panel = self.panel_ui

        class CallbackWindowManager:
            def __init__(self):
                self._candidate = ""
                self._character = ""

            @property
            def blendcap_vta_candidate(self): return self._candidate

            @blendcap_vta_candidate.setter
            def blendcap_vta_candidate(self, value):
                self._candidate = value
                panel._candidate_changed(self, None)

            @property
            def blendcap_vta_character(self): return self._character

            @blendcap_vta_character.setter
            def blendcap_vta_character(self, value):
                self._character = value
                panel._character_changed(self, None)

        wm = CallbackWindowManager()
        with mock.patch.object(panel.vta_overlay, "request_redraw"):
            type(self).real_sync_properties(SimpleNamespace(window_manager=wm))
        self.assertEqual(wm.blendcap_vta_candidate, "assigned")
        self.assertEqual(wm.blendcap_vta_character, "character-black")
        self.assertEqual(self.selection.character_id, "character-black")

    def _hard_operator(self, kind="occluded"):
        panel_ui = self.panel_ui

        class OperatorDouble:
            decision_kind = kind

            def __init__(self):
                self.messages = []

            def report(self, level, message):
                self.messages.append((level, message))

            def execute(self, context):
                return panel_ui.BLENDCAP_OT_vta_review_hard_state.execute(self, context)

        return OperatorDouble()

    def test_hard_state_conflict_cancel_confirm_and_direct_bypass(self):
        occupied = review_ui.present_review(Review(
            candidates=(Candidate("revision-1", "observation-a", assigned_character_id="character-red"),),
            character_assignments=(Assignment("character-red", "observation-a"),),
            character_decisions=(Decision("character-red", "old-key", SimpleNamespace(value="visible"), "observation-a"),),
        ))
        changed = review_ui.present_review(Review(
            character_decisions=(Decision("character-red", "hard-key", SimpleNamespace(value="occluded")),),
        ))
        self.panel_ui._view = occupied
        self.selection.accept_review(occupied)
        self.selection.character_id = "character-red"
        calls = []
        request = SimpleNamespace(
            character_id="character-red", state=SimpleNamespace(value="occluded"),
            requires_confirmation=True,
        )

        class Backend:
            def prepare_hard_state(_self, *_args, **_kwargs):
                calls.append("prepare")
                return request, occupied

            def apply_hard_state(_self, *_args, **kwargs):
                calls.append(("apply", kwargs["confirmed"]))
                return object(), changed

        with mock.patch.object(self.panel_ui, "_get_backend", return_value=Backend()):
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_hard_state.execute(self._hard_operator(), self.context),
                {'CANCELLED'},
            )
            self.assertEqual(calls, ["prepare"])
            cancelled = self._hard_operator()
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_hard_state.invoke(cancelled, self.context, None),
                {'RUNNING_MODAL'},
            )
            self.assertEqual(self.dialogs[-1]["confirm_text"], "Replace Decision")
            self.assertEqual(calls, ["prepare", "prepare"])
            # Blender Cancel never calls execute: no authoritative apply occurs.
            confirmed = self._hard_operator()
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_hard_state.invoke(confirmed, self.context, None),
                {'RUNNING_MODAL'},
            )
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_hard_state.execute(confirmed, self.context),
                {'FINISHED'},
            )
        self.assertEqual(calls, ["prepare", "prepare", "prepare", ("apply", True)])
        self.assertEqual(self.panel_ui._view, changed)

    def test_vacant_hard_state_applies_without_dialog(self):
        available = review_ui.present_review(Review())
        changed = review_ui.present_review(Review(
            character_decisions=(Decision("character-red", "hard-key", SimpleNamespace(value="no-valid-candidate")),),
        ))
        self.panel_ui._view = available
        self.selection.accept_review(available)
        self.selection.character_id = "character-red"
        calls = []
        request = SimpleNamespace(
            character_id="character-red", state=SimpleNamespace(value="no-valid-candidate"),
            requires_confirmation=False,
        )

        class Backend:
            def prepare_hard_state(_self, *_args, **_kwargs):
                calls.append("prepare")
                return request, available

            def apply_hard_state(_self, *_args, **kwargs):
                calls.append(("apply", kwargs["confirmed"]))
                return object(), changed

        with mock.patch.object(self.panel_ui, "_get_backend", return_value=Backend()):
            result = self.panel_ui.BLENDCAP_OT_vta_review_hard_state.invoke(
                self._hard_operator("no-valid-candidate"), self.context, None
            )
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(calls, ["prepare", "prepare", ("apply", False)])
        self.assertEqual(self.dialogs, [])

    def test_conflict_dialog_cancel_and_confirm_use_one_authoritative_call(self):
        occupied = review_ui.present_review(
            Review(
                candidates=(
                    Candidate("revision-1", "observation-a", assigned_character_id="character-red"),
                    Candidate("revision-1", "observation-b", assigned_character_id="character-black"),
                ),
                character_assignments=(
                    Assignment("character-red", "observation-a"),
                    Assignment("character-black", "observation-b"),
                ),
            )
        )
        corrected = review_ui.present_review(
            Review(
                candidates=(
                    Candidate("revision-1", "observation-a", assigned_character_id="character-black"),
                    Candidate("revision-1", "observation-b"),
                ),
                character_assignments=(Assignment("character-black", "observation-a"),),
            )
        )
        self.panel_ui._view = occupied
        self.selection.accept_review(occupied)
        self.selection.observation_id = "observation-a"
        self.selection.character_id = "character-black"
        calls = []
        request = SimpleNamespace(observation_id="observation-a", character_id="character-black")

        class Backend:
            def prepare_reassignment(_self, *_args, **_kwargs):
                calls.append("prepare")
                return request, occupied

            def apply_confirmed_reassignment(_self, *_args):
                calls.append("apply")
                return object(), corrected

            def apply(_self, *_args, **_kwargs):
                self.fail("conflicting Assign took the immediate path")

        with mock.patch.object(self.panel_ui, "_get_backend", return_value=Backend()):
            cancelled = self._operator()
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_assign.invoke(cancelled, self.context, None),
                {'RUNNING_MODAL'},
            )
            self.assertEqual(calls, ["prepare"])
            self.assertEqual(self.dialogs[0]["confirm_text"], "Reassign")
            self.assertIn("Candidate 2 will become unassigned", " ".join(cancelled._confirmation_lines))
            # Blender Cancel does not call execute; the prepared request is discarded.
            confirmed = self._operator()
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_assign.invoke(confirmed, self.context, None),
                {'RUNNING_MODAL'},
            )
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_assign.execute(confirmed, self.context),
                {'FINISHED'},
            )

        self.assertEqual(calls, ["prepare", "prepare", "apply"])
        self.assertEqual(self.panel_ui._view, corrected)
        self.assertFalse(self.selection.can_assign(corrected))

    def test_unoccupied_assign_runs_without_dialog_and_direct_conflict_cannot_bypass_it(self):
        available = review_ui.present_review(Review())
        occupied = review_ui.present_review(
            Review(
                candidates=(Candidate("revision-1", "observation-a", assigned_character_id="character-red"),),
                character_assignments=(Assignment("character-red", "observation-a"),),
            )
        )
        self.panel_ui._view = available
        self.selection.accept_review(available)
        self.selection.observation_id = "observation-a"
        self.selection.character_id = "character-black"
        calls = []

        class Backend:
            def apply(_self, *_args, **_kwargs):
                calls.append("ordinary")
                return object(), available

        with mock.patch.object(self.panel_ui, "_get_backend", return_value=Backend()):
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_assign.invoke(self._operator(), self.context, None),
                {'FINISHED'},
            )
            self.assertEqual(calls, ["ordinary"])
            self.assertEqual(self.dialogs, [])
            self.panel_ui._view = occupied
            self.selection.accept_review(occupied)
            self.selection.observation_id = "observation-a"
            self.selection.character_id = "character-black"
            self.assertEqual(
                self.panel_ui.BLENDCAP_OT_vta_review_assign.execute(self._operator(), self.context),
                {'CANCELLED'},
            )
            self.assertEqual(calls, ["ordinary"])

if __name__ == "__main__":
    unittest.main()
