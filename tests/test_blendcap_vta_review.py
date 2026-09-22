"""Focused tests for the public, Blender-independent VTA review shell."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import tempfile
import unittest

import blendcap_vta_review as review_ui


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


class ReviewPresentationTests(unittest.TestCase):
    def test_new_character_selection_does_not_select_or_assign_candidate(self):
        view = review_ui.present_review(Review(characters=(Character("new-id", "Wrestler A"),)))
        selection = review_ui.SelectionState()

        self.assertTrue(selection.select_character(view, "new-id"))
        self.assertEqual(selection.character_id, "new-id")
        self.assertEqual(selection.observation_id, "")
        self.assertTrue(all(candidate.assigned_character_id is None for candidate in view.candidates))
        self.assertFalse(selection.can_assign(view))

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

    def test_occupied_candidate_or_character_cannot_apply(self):
        view = review_ui.present_review(
            Review(
                candidates=(Candidate("revision-1", "observation-a", assigned_character_id="character-red"),),
                character_assignments=(Assignment("character-red", "observation-a"),),
            )
        )
        selection = review_ui.SelectionState()
        selection.accept_review(view)
        self.assertTrue(selection.select_observation(view, "observation-a"))
        self.assertEqual(selection.observation_id, "observation-a")
        selection.character_id = "character-red"
        self.assertFalse(selection.can_assign(view))

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

    def test_companion_root_is_exactly_beside_saved_blend(self):
        self.assertEqual(
            review_ui.companion_root_for_blend(Path("C:/work/scene.blend")),
            Path("C:/work/scene_vta"),
        )
        with self.assertRaisesRegex(review_ui.VtaReviewUiError, "Save"):
            review_ui.companion_root_for_blend("")


if __name__ == "__main__":
    unittest.main()
