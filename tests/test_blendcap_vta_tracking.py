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
    def test_preview_names_and_palette_preserve_provenance_and_injectivity(self):
        from blendcap_vta_visuals import provenance_color, ASSIGNMENT_COLOR, TRACKING_COLOR
        people = tuple(SimpleNamespace(character_id=cid, display_name=cid.title()) for cid in ('red', 'black'))
        base = dict(analysis_revision_id='analysis', source_id='s', frame=2,
                    state=SimpleNamespace(value='analyzed-with-candidates'), characters=people,
                    candidates=tuple(SimpleNamespace(analysis_revision_id='analysis', observation_id=oid,
                        box_xyxy=(i*20, 1, i*20+10, 20)) for i, oid in enumerate(('human', 'derived', 'neutral'))),
                    character_assignments=(SimpleNamespace(character_id='red', observation_id='human'),),
                    character_decisions=(), derived_tracking=(SimpleNamespace(character_id='black',
                        state=SimpleNamespace(value='visible'), observation_id='derived'),))
        overlay = vta_overlay.build_overlay_candidates(review.present_review(SimpleNamespace(**base), (100, 100)), '')
        self.assertEqual(tuple((r.label, r.provenance) for r in overlay),
                         (('Red', 'human'), ('Black', 'derived'), ('Candidate 3', 'neutral')))
        self.assertEqual(provenance_color('human', label=True), ASSIGNMENT_COLOR)
        self.assertEqual(provenance_color('derived', label=True)[:3], TRACKING_COLOR[:3])
        self.assertNotEqual(provenance_color('human'), provenance_color('derived'))
        for assignments, derived in (
                ((SimpleNamespace(character_id='black', observation_id='human'),), base['derived_tracking']),
                ((), (SimpleNamespace(character_id='black', state=SimpleNamespace(value='visible'), observation_id='human'),)),
                ((), (SimpleNamespace(character_id='red', state=SimpleNamespace(value='visible'), observation_id='derived'),))):
            invalid = dict(base)
            invalid['character_assignments'] += assignments
            invalid['derived_tracking'] += derived
            with self.assertRaisesRegex(review.VtaReviewUiError, 'injective'):
                review.present_review(SimpleNamespace(**invalid))

    def test_bulk_backend_validates_set_and_uses_registered_cache_and_digest(self):
        core = SimpleNamespace(validate_analysis_cache_registration=mock.Mock(),
                               update_tracking_many_and_persist=mock.Mock(return_value=('receipt',)))
        backend = review.PrivateCoreBackend(core)
        backend._load_context = mock.Mock(return_value=(SimpleNamespace(
            characters=(SimpleNamespace(character_id='red'), SimpleNamespace(character_id='black')),
            to_json=lambda: 'canonical'), SimpleNamespace(source=SimpleNamespace(source_id='s')), 'analysis'))
        backend.query = mock.Mock(return_value='review')
        backend.query_timeline = mock.Mock(return_value='timeline')
        location = review.ReviewLocation(Path('root'), Path('video'), 8, 18)
        self.assertEqual(backend.update_tracking_for_characters(location, ('red', 'black')), (('receipt',), 'review', 'timeline'))
        core.update_tracking_many_and_persist.assert_called_once_with(Path('root'), ('black', 'red'), 's', 8,
            expected_project_sha256=__import__('hashlib').sha256(b'canonical').hexdigest())
        core.validate_analysis_cache_registration.assert_called_once()
        for ids in ((), ('red', 'red'), ('red', 'missing'), ([],)):
            with self.assertRaises(review.VtaReviewUiError):
                backend.update_tracking_for_characters(location, ids)
        self.assertEqual(core.update_tracking_many_and_persist.call_count, 1)

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
        self.assertEqual(overlay[0].label, "Red")
        self.assertEqual(overlay[0].provenance, 'derived')
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
