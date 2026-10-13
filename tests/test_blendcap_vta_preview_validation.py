"""Synthetic regressions for production preview-evidence validation."""

import copy
import unittest

import blendcap_vta_preview_validation as validation


def sidecar():
    return {
        'version': 2, 'fps': 25.0, 'sample_indices': [0, 2, 4],
        'boxes': [[0, 0, 100, 100], None, [0, 0, 100, 100]],
        'all_boxes': [[[0, 0, 100, 100], [200, 0, 300, 100]] for _ in range(3)],
    }


def anchors():
    return {
        'schema': 'blendcap-preview-target-anchors', 'version': 1,
        'track_id': 'external-reference', 'coordinate_space': 'source-pixels-xyxy',
        'source': {'sidecar_sha256': 'a' * 64, 'fps': 25.0, 'sampling_interval': 2},
        'anchors': [{'anchor_id': 'explicit-anchor', 'frame': 2,
                     'box_snapshot_xyxy': [0, 0, 100, 100]}],
        'forced_occlusion_ranges': [],
    }


def resolve(raw=None, sampled=None):
    return validation.validate_anchors(
        anchors() if raw is None else raw,
        validation.validate_sidecar(sidecar(), digest='a' * 64) if sampled is None else sampled,
    )


class PreviewValidationTests(unittest.TestCase):
    def test_sidecar_preserves_geometry_frame_mapping_and_digest(self):
        result = validation.validate_sidecar(sidecar(), digest='a' * 64)
        self.assertEqual(result['fps'], 25.0)
        self.assertEqual(result['sampling_interval'], 2)
        self.assertEqual(result['frame_to_position'], {0: 0, 2: 1, 4: 2})
        self.assertEqual(result['all_boxes'][0][0], (0.0, 0.0, 100.0, 100.0))
        self.assertEqual(result['sha256'], 'a' * 64)

    def test_inputs_are_not_mutated(self):
        sampled, raw = sidecar(), anchors()
        before = copy.deepcopy((sampled, raw))
        resolve(raw, validation.validate_sidecar(sampled, digest='a' * 64))
        self.assertEqual((sampled, raw), before)

    def test_sidecar_schema_version_rejects(self):
        raw = sidecar(); raw['version'] = 1
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'expected 2'):
            validation.validate_sidecar(raw)

    def test_invalid_fps_rejects(self):
        for value in (0, -1, float('nan'), float('inf'), 'invalid'):
            with self.subTest(value=value), self.assertRaises(validation.PreviewEvidenceError):
                raw = sidecar(); raw['fps'] = value
                validation.validate_sidecar(raw)

    def test_sidecar_array_shapes_reject(self):
        for key in ('sample_indices', 'boxes', 'all_boxes'):
            with self.subTest(key=key), self.assertRaises(validation.PreviewEvidenceError):
                raw = sidecar(); raw[key] = None
                validation.validate_sidecar(raw)
        for key in ('boxes', 'all_boxes'):
            with self.subTest(key=key), self.assertRaisesRegex(validation.PreviewEvidenceError, 'lengths'):
                raw = sidecar(); raw[key].pop()
                validation.validate_sidecar(raw)
        raw = sidecar(); raw['sample_indices'] = [0]
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'at least two'):
            validation.validate_sidecar(raw)

    def test_sample_indices_reject_invalid_or_unordered_frames(self):
        for frames in ([0, True, 4], [0, 2.0, 4], [-2, 0, 2], [0, 0, 4], [4, 2, 0]):
            with self.subTest(frames=frames), self.assertRaises(validation.PreviewEvidenceError):
                raw = sidecar(); raw['sample_indices'] = frames
                validation.validate_sidecar(raw)

    def test_nonconstant_sampling_rejects(self):
        raw = sidecar(); raw['sample_indices'] = [0, 2, 5]
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'constant positive'):
            validation.validate_sidecar(raw)

    def test_invalid_box_geometry_rejects(self):
        for box in (None, [0, 0, 0, 100], [0, 100, 100, 0], [0, 0, float('nan'), 100],
                    [0, 0, 100], (0, 0, 100, 100)):
            with self.subTest(box=box), self.assertRaises(validation.PreviewEvidenceError):
                raw = sidecar(); raw['all_boxes'][0] = [box]
                validation.validate_sidecar(raw)
        raw = sidecar(); raw['all_boxes'][0] = None
        with self.assertRaises(validation.PreviewEvidenceError):
            validation.validate_sidecar(raw)

    def test_null_selected_box_and_empty_candidate_frame_are_valid(self):
        raw = sidecar(); raw['all_boxes'][1] = []
        result = validation.validate_sidecar(raw)
        self.assertEqual(result['all_boxes'][1], [])

    def test_anchor_schema_coordinate_and_reference_validation(self):
        for key, value in (('schema', 'foreign'), ('version', 2), ('track_id', ''),
                           ('coordinate_space', 'normalized')):
            with self.subTest(key=key), self.assertRaises(validation.PreviewEvidenceError):
                raw = anchors(); raw[key] = value
                resolve(raw)

    def test_snapshot_resolves_same_box_after_candidate_reordering(self):
        first = resolve()['anchors'][0]
        raw = sidecar()
        raw['all_boxes'] = [list(reversed(items)) for items in raw['all_boxes']]
        second = resolve(sampled=validation.validate_sidecar(raw, digest='a' * 64))['anchors'][0]
        self.assertEqual(first['box_xyxy'], second['box_xyxy'])
        self.assertEqual(first['_box'], second['_box'])
        self.assertEqual(first['temporary_candidate_index'], 0)
        self.assertEqual(second['temporary_candidate_index'], 1)

    def test_clicked_point_requires_one_candidate(self):
        raw = anchors(); item = raw['anchors'][0]
        del item['box_snapshot_xyxy']; item['clicked_point_xy'] = [50, 50]
        self.assertEqual(resolve(raw)['anchors'][0]['box_xyxy'], [0, 0, 100, 100])
        sampled = sidecar(); sampled['all_boxes'][1].append([25, 25, 75, 75])
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'resolves to 2'):
            resolve(raw, validation.validate_sidecar(sampled, digest='a' * 64))

    def test_point_and_snapshot_must_agree(self):
        raw = anchors(); raw['anchors'][0]['clicked_point_xy'] = [250, 50]
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'resolves to 0'):
            resolve(raw)

    def test_snapshot_resolution_keeps_existing_tolerances(self):
        self.assertEqual((validation.ANCHOR_MINIMUM_IOU,
                          validation.ANCHOR_MAXIMUM_CENTER_DISPLACEMENT,
                          validation.ANCHOR_MAXIMUM_AREA_FOLD_CHANGE), (0.85, 0.10, 1.20))
        raw = anchors(); raw['anchors'][0]['box_snapshot_xyxy'] = [2, 0, 102, 100]
        self.assertEqual(resolve(raw)['anchors'][0]['box_xyxy'], [0, 0, 100, 100])
        raw['anchors'][0]['box_snapshot_xyxy'] = [20, 0, 120, 100]
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'resolves to 0'):
            resolve(raw)

    def test_source_metadata_mismatch_rejects(self):
        for key, value in (('sidecar_sha256', 'b' * 64), ('fps', 30),
                           ('sampling_interval', 1), ('sampling_interval', True)):
            with self.subTest(key=key), self.assertRaises(validation.PreviewEvidenceError):
                raw = anchors(); raw['source'][key] = value
                resolve(raw)
        raw = anchors(); raw['source']['fps'] = 25.0005
        resolve(raw)
        raw['source']['sidecar_sha256'] = ('a' * 64).upper()
        resolve(raw)

    def test_invalid_anchor_fields_reject(self):
        for key, value in (('anchor_id', ''), ('frame', True), ('frame', 1),
                           ('note', 123), ('clicked_point_xy', [1]),
                           ('box_snapshot_xyxy', None)):
            with self.subTest(key=key), self.assertRaises(validation.PreviewEvidenceError):
                raw = anchors(); raw['anchors'][0][key] = value
                resolve(raw)

    def test_duplicate_ids_and_frames_reject(self):
        raw = anchors(); raw['anchors'].append(copy.deepcopy(raw['anchors'][0]))
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'anchor_id'):
            resolve(raw)
        raw['anchors'][1]['anchor_id'] = 'other'
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'one anchor'):
            resolve(raw)

    def test_ranges_sort_and_adjacent_nonoverlap_is_valid(self):
        raw = anchors(); raw['anchors'][0]['frame'] = 4
        raw['forced_occlusion_ranges'] = [{'start_frame': 2, 'end_frame': 2},
                                           {'start_frame': 0, 'end_frame': 0}]
        result = resolve(raw)
        self.assertEqual(result['forced_frames'], {0, 2})
        self.assertEqual(result['forced_occlusion_ranges'][0]['start_frame'], 0)

    def test_invalid_ranges_reject(self):
        for ranges in ([{'start_frame': 2, 'end_frame': 0}],
                       [{'start_frame': 1, 'end_frame': 2}],
                       [{'start_frame': True, 'end_frame': 2}],
                       [{'start_frame': 0, 'end_frame': 2, 'note': 1}],
                       [{'start_frame': 0, 'end_frame': 2}, {'start_frame': 2, 'end_frame': 4}]):
            with self.subTest(ranges=ranges), self.assertRaises(validation.PreviewEvidenceError):
                raw = anchors(); raw['forced_occlusion_ranges'] = ranges
                resolve(raw)

    def test_anchor_inside_forced_range_rejects(self):
        raw = anchors(); raw['forced_occlusion_ranges'] = [{'start_frame': 0, 'end_frame': 2}]
        with self.assertRaisesRegex(validation.PreviewEvidenceError, 'inside a forced'):
            resolve(raw)

    def test_module_exposes_no_tracker_or_file_publication(self):
        for name in ('PARAMETERS', 'build_track', 'run_direction', '_match_candidate',
                     '_candidate_metrics', 'parse_args', '_load_json', '_sha256', 'main'):
            self.assertFalse(hasattr(validation, name), name)


if __name__ == '__main__':
    unittest.main()
