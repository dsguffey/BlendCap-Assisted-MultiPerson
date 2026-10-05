"""Synthetic research contracts; no dependency on frozen real files or GPU."""
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from tools.vta_tracking_signal_research import (
    appearance_choice, crop_bounds, direction, gated_score, predict_box,
    run_predictions, score_predictions, top_id, write_new,
)


def anchor(frame=0):
    return NS(frame=frame, candidate_evidence=NS(box_xyxy=(0, 0, 10, 20), observation_id='anchor'))


class SignalTests(unittest.TestCase):
    def test_velocity_uses_two_adjacent_frames_without_mutating_history(self):
        history = [(0, (0, 0, 10, 20)), (1, (2, 3, 12, 23))]
        before = copy.deepcopy(history)
        self.assertEqual(predict_box(history), (4, 6, 14, 26))
        self.assertEqual(history, before)
        self.assertEqual(predict_box(history[:1]), history[0][1])
        with self.assertRaisesRegex(ValueError, 'bridge'):
            predict_box([(0, history[0][1]), (2, history[1][1])])

    def test_log_scale_prediction(self):
        self.assertEqual(predict_box([(0, (0, 0, 10, 10)), (1, (-5, -5, 15, 15))]), (-15, -15, 25, 25))

    def test_exact_ties_never_select_and_order_is_irrelevant(self):
        self.assertIsNone(top_id({'a': .5, 'b': .5}))
        self.assertEqual(top_id({'b': .4, 'a': .5}), top_id({'a': .5, 'b': .4}))
        self.assertIsNone(top_id({}))
        self.assertIsNone(top_id({'a': float('nan')}))

    def test_lower_left_crop_transform_and_inner_view(self):
        self.assertEqual(crop_bounds((10, 20, 110, 220), 200, 300, 'whole'), (10, 80, 110, 280))
        self.assertEqual(crop_bounds((10, 20, 110, 220), 200, 300, 'inner'), (30, 100, 90, 260))

    def test_direction_stops_at_missing_frame_no_reacquisition(self):
        item = NS(box_xyxy=(1, 0, 11, 20), observation_id='one')
        cache = NS(frame_result=lambda f: NS(candidates=(item,)) if f in (1, 3) else None)
        bt = NS(_match=lambda *args: (item, .9))
        found, trace = direction(bt, cache, (1, 2, 3), anchor(), [], 'geometry', {})
        self.assertEqual(list(found), [1])
        self.assertEqual(trace[-1]['frame'], 2)
        self.assertEqual(trace[-1]['reason'], 'missing-or-empty-candidates')
        with self.assertRaisesRegex(ValueError, 'adjacent'):
            direction(bt, cache, (2,), anchor(), [], 'geometry', {})

    def test_exception_does_not_relax_center_or_iou(self):
        bt = NS(MIN_IOU=.08, MAX_CENTER_DISPLACEMENT=.42, MAX_AREA_FOLD=1.9,
                MIN_MATCH_SCORE=.42, _metrics=lambda *_: (.5, .1, 2.2))
        self.assertIsNone(gated_score(bt, (), ()))
        self.assertIsNotNone(gated_score(bt, (), (), area_exception=True))
        bt._metrics = lambda *_: (.5, .5, 2.2)
        self.assertIsNone(gated_score(bt, (), (), area_exception=True))
        bt._metrics = lambda *_: (.01, .1, 2.2)
        self.assertIsNone(gated_score(bt, (), (), area_exception=True))

    def test_labels_do_not_enter_prediction_api(self):
        import inspect
        from tools.vta_tracking_signal_research import run_predictions, extract_features
        for function in (direction, run_predictions, extract_features):
            self.assertNotIn('labels', inspect.signature(function).parameters)

    def test_scoring_excludes_human_anchors_and_preserves_inputs(self):
        labels = {'frames': [{'source_frame': 0, 'blender_frame': 1,
                            'candidates': [{'position': 1, 'observation_id': 'a'}, {'position': 2, 'observation_id': 'b'}],
                            'labels': {'wrestler-a': {'kind': 'candidate', 'candidate_position': 1},
                                       'wrestler-b': {'kind': 'candidate', 'candidate_position': 2}}}]}
        prediction = {'characters': {ch: {'scopes': [{'start_frame': 0, 'end_frame': 0}],
                       'records': {0: {'observation_id': 'a'}}} for ch in ('wrestler-a', 'wrestler-b')}}
        before = copy.deepcopy((labels, prediction))
        state = NS(identity_keyframes=[NS(character_id='wrestler-a', frame=0)])
        scored = score_predictions(prediction, labels, state)
        self.assertEqual(scored['summary']['wrestler-a'], {'excluded-human-anchor': 1})
        self.assertEqual(scored['summary']['wrestler-b'], {'semantic-swap': 1})
        self.assertEqual((labels, prediction), before)

    def test_crop_reference_disagreement_and_missing_features_abstain(self):
        candidates = (NS(observation_id='a'), NS(observation_id='b'))
        references = [anchor()]
        features = {'current_frame': 1, 'color': {(0, 'anchor', view): 1 for view in ('whole', 'inner')}}
        for view in ('whole', 'inner'):
            features['color'].update({(1, 'a', view): 2, (1, 'b', view): 1})
        with mock.patch('tools.vta_tracking_signal_research.cosine', side_effect=lambda a, b: a * b):
            self.assertEqual(appearance_choice(candidates, references, features, 'color'), 'a')
            self.assertEqual(appearance_choice(candidates[::-1], references, features, 'color'), 'a')
            features['color'][(1, 'b', 'inner')] = 3
            self.assertIsNone(appearance_choice(candidates, references, features, 'color'))
            del features['color'][(1, 'b', 'inner')]
            self.assertIsNone(appearance_choice(candidates, references, features, 'color'))
            self.assertIsNone(appearance_choice(candidates, [], features, 'color'))

    def test_conflicting_derived_claims_both_abstain_without_mutation(self):
        keys = [NS(character_id=ch, source_id='source', frame=0, keyframe_id=ch,
                   candidate_evidence=NS(box_xyxy=(0, 0, 10, 20), observation_id=ch))
                for ch in ('wrestler-a', 'wrestler-b')]
        scope = lambda ch, *_: NS(start_frame=1, end_frame=2,
                left_boundary=NS(keyframe_id=ch), right_boundary=NS(keyframe_id=None),
                to_dict=lambda: {'start_frame': 1, 'end_frame': 2})
        state = NS(identity_keyframes=keys, compute_tracking_scope=scope)
        candidate = NS(box_xyxy=(1, 0, 11, 20), observation_id='shared')
        cache = NS(source=NS(source_id='source'), frame_result=lambda _: NS(candidates=(candidate,)))
        bt = NS(_match=lambda *_: (candidate, .9))
        before = repr(keys)
        results = run_predictions(bt, state, cache, {ch: [0] for ch in ('wrestler-a', 'wrestler-b')}, 'geometry', {})
        self.assertEqual(results['withheld_collision_frames'], [1, 2])
        self.assertTrue(all(row['observation_id'] is None for v in results['characters'].values() for row in v['records'].values()))
        self.assertEqual(repr(keys), before)

    def test_output_rejects_outside_paths_and_never_clobbers(self):
        with self.assertRaisesRegex(ValueError, 'under'):
            write_new(Path('outside-signal-report.json'), {})
        path = Path(__file__).resolve().parents[1] / 'checkpoints/dev-preview/test-signal-output.json'
        with mock.patch.object(Path, 'exists', return_value=True):
            with self.assertRaisesRegex(ValueError, 'exists'):
                write_new(path, {})

    def test_no_candidate_anchor_cannot_bridge_hard_boundary(self):
        key = NS(character_id='wrestler-a', source_id='source', frame=1,
                 keyframe_id='hard', candidate_evidence=None)
        scope = NS(start_frame=2, end_frame=3, left_boundary=NS(keyframe_id='hard'),
                   right_boundary=NS(keyframe_id=None), to_dict=lambda: {'start_frame': 2, 'end_frame': 3})
        state = NS(identity_keyframes=[key], compute_tracking_scope=lambda *_: scope)
        cache = NS(source=NS(source_id='source'), frame_result=lambda _: NS(candidates=()))
        results = run_predictions(NS(), state, cache,
                                  {ch: [1] for ch in ('wrestler-a', 'wrestler-b')}, 'geometry', {})
        self.assertTrue(all(not value['records'] for value in results['characters'].values()))
        self.assertEqual(key.candidate_evidence, None)

    def test_bidirectional_disagreement_abstains_inside_scope(self):
        keys = [NS(character_id='wrestler-a', source_id='source', frame=f, keyframe_id=str(f),
                   candidate_evidence=NS(box_xyxy=(x, 0, x + 10, 20), observation_id=str(f)))
                for f, x in ((0, 0), (3, 30))]
        scope = NS(start_frame=1, end_frame=2, left_boundary=NS(keyframe_id='0'),
                   right_boundary=NS(keyframe_id='3'), to_dict=lambda: {'start_frame': 1, 'end_frame': 2})
        candidates = (NS(observation_id='left', box_xyxy=(0, 0, 10, 20)),
                      NS(observation_id='right', box_xyxy=(30, 0, 40, 20)))
        state = NS(identity_keyframes=keys, compute_tracking_scope=lambda *_: scope)
        cache = NS(source=NS(source_id='source'), frame_result=lambda _: NS(candidates=candidates))
        bt = NS(_match=lambda box, _: (candidates[0 if box[0] == 0 else 1], .8))
        result = run_predictions(bt, state, cache, {ch: [0] for ch in ('wrestler-a', 'wrestler-b')}, 'geometry', {})
        self.assertEqual(set(result['characters']['wrestler-a']['records']), {1, 2})
        self.assertTrue(all(r['reason'] == 'directional-disagreement' for r in result['characters']['wrestler-a']['records'].values()))

    def test_accepted_set_and_missing_record_denominators(self):
        rows = []
        for f in (0, 1):
            rows.append({'source_frame': f, 'blender_frame': f + 1,
                'candidates': [{'position': 1, 'observation_id': 'a'}, {'position': 2, 'observation_id': 'b'}],
                'labels': {ch: {'kind': 'accepted-set', 'candidate_position': None, 'candidate_positions': [1, 2]}
                           for ch in ('wrestler-a', 'wrestler-b')}})
        prediction = {'characters': {ch: {'scopes': [{'start_frame': 0, 'end_frame': 0}],
            'records': {0: {'observation_id': 'b'}}} for ch in ('wrestler-a', 'wrestler-b')}}
        result = score_predictions(prediction, {'frames': rows}, NS(identity_keyframes=[]))
        for ch in ('wrestler-a', 'wrestler-b'):
            self.assertEqual(result['metrics'][ch]['scored_cases'], 2)
            self.assertEqual(result['metrics'][ch]['correct_resolved'], 1)
            self.assertEqual(result['metrics'][ch]['outside_scope'], 1)

    def test_area_exception_requires_appearance_consensus(self):
        item = NS(observation_id='one', box_xyxy=(0, 0, 20, 20))
        bt = NS(MIN_IOU=.08, MAX_CENTER_DISPLACEMENT=.42, MAX_AREA_FOLD=1.9,
                MIN_MATCH_SCORE=.42, _metrics=lambda *_: (.5, .1, 2.0))
        cache = NS(frame_result=lambda _: NS(candidates=(item,)))
        with mock.patch('tools.vta_tracking_signal_research.appearance_choice', return_value=None):
            found, _ = direction(bt, cache, (1,), anchor(), [anchor()], 'color-exception', {'color': {}})
            self.assertFalse(found)
        with mock.patch('tools.vta_tracking_signal_research.appearance_choice', return_value='one'):
            found, _ = direction(bt, cache, (1,), anchor(), [anchor()], 'color-exception', {'color': {}})
            self.assertEqual(found[1][0].observation_id, 'one')


if __name__ == '__main__':
    unittest.main()
