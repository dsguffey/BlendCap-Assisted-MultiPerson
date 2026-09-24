"""Public-shell proof that Update Tracking stays separate from review authority."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import blendcap_vta_review as review
import blendcap_vta_timeline as timeline
from blendcap import vta_overlay


ROOT = Path(__file__).resolve().parents[1]


class TrackingPresentationTests(unittest.TestCase):
    def test_derived_character_is_not_authoritative_assignment(self):
        candidate = SimpleNamespace(analysis_revision_id="analysis", observation_id="obs",
                                    box_xyxy=(1, 2, 10, 20))
        person = SimpleNamespace(character_id="red", display_name="Red")
        base = dict(analysis_revision_id="analysis", source_id="source", frame=3,
                    state=SimpleNamespace(value="analyzed-with-candidates"),
                    candidates=(candidate,), characters=(person,),
                    character_assignments=(), character_decisions=(),
                    derived_tracking=(SimpleNamespace(character_id="red",
                        state=SimpleNamespace(value="visible"), observation_id="obs"),))
        view = review.present_review(SimpleNamespace(**base), (100, 100))
        self.assertIsNone(view.candidates[0].assigned_character_id)
        self.assertEqual(view.characters[0].derived_observation_id, "obs")
        overlay = vta_overlay.build_overlay_candidates(view, "")
        self.assertEqual(overlay[0].label, "Candidate 1")
        self.assertFalse(review.SelectionState().can_assign(view))
        base["character_decisions"] = (SimpleNamespace(
            character_id="red", state=SimpleNamespace(value="visible"),
            observation_id="obs"),)
        authoritative = review.present_review(SimpleNamespace(**base), (100, 100))
        self.assertEqual(authoritative.characters[0].decision_state, "visible")

    def test_derived_timeline_has_secondary_non_key_records(self):
        private = SimpleNamespace(source_id="source",
            characters=(SimpleNamespace(character_id="red", display_name="Red"),),
            keys=(SimpleNamespace(keyframe_id="key", character_id="red", frame=1,
                                  state=SimpleNamespace(value="visible"), observation_id="obs"),),
            derived=(SimpleNamespace(character_id="red", frame=2,
                                     state=SimpleNamespace(value="unresolved")),))
        view = timeline.present_timeline(private)
        self.assertEqual(len(view.keys), 1)
        self.assertEqual(len(view.derived), 1)
        self.assertIsNone(timeline.hit_test_keys(
            view, (("red", 10),), frame_to_x=lambda frame: frame * 10,
            point=(20, 10), radius=2,
        ))

    def test_backend_wires_selected_character_only_to_explicit_command(self):
        backend = review.PrivateCoreBackend(core=SimpleNamespace(
            validate_analysis_cache_registration=mock.Mock(),
            update_tracking_and_persist=mock.Mock(return_value="receipt"),
        ))
        project = SimpleNamespace(characters=(SimpleNamespace(character_id="red"),))
        cache = SimpleNamespace(source=SimpleNamespace(source_id="source"))
        backend._load_context = mock.Mock(return_value=(project, cache, "analysis"))
        backend.query = mock.Mock(return_value="review")
        backend.query_timeline = mock.Mock(return_value="timeline")
        location = review.ReviewLocation(Path("root"), Path("video"), 8, 18)
        self.assertEqual(backend.update_tracking(location, "red"),
                         ("receipt", "review", "timeline"))
        backend._core.update_tracking_and_persist.assert_called_once_with(
            location.companion_root, "red", "source", 8,
        )
        with self.assertRaises(review.VtaReviewUiError):
            backend.update_tracking(location, "other")

    def test_operator_is_registered_and_only_explicitly_executed(self):
        tree = ast.parse((ROOT / "blendcap" / "vta_review_ui.py").read_text(encoding="utf-8"))
        classes = {item.name for item in tree.body if isinstance(item, ast.ClassDef)}
        self.assertIn("BLENDCAP_OT_vta_update_tracking", classes)
        registration = ast.parse((ROOT / "blendcap" / "registration.py").read_text(encoding="utf-8"))
        class_tuple = next(node.value for node in registration.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "classes"
                                   for target in node.targets))
        self.assertIn("BLENDCAP_OT_vta_update_tracking", {
            node.id for node in ast.walk(class_tuple) if isinstance(node, ast.Name)
        })


if __name__ == "__main__":
    unittest.main()
