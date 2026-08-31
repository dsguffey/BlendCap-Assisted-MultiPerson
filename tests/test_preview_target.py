"""Unit tests for the standalone preview target tracker."""

import unittest

import track_preview_target as tracker


def box(x, y=0, width=100, height=100):
    return [x, y, x + width, y + height]


def sidecar_document(frames, candidates):
    return {
        "version": 2,
        "fps": 30.0,
        "start_frame": frames[0],
        "end_frame": frames[-1] + 1,
        "sample_indices": frames,
        "boxes": [items[0] if items else None for items in candidates],
        "all_boxes": candidates,
    }


def anchor(anchor_id, frame, snapshot):
    return {
        "anchor_id": anchor_id,
        "frame": frame,
        "box_snapshot_xyxy": snapshot,
    }


def anchor_document(anchors, ranges=None):
    return {
        "schema": "blendcap-preview-target-anchors",
        "version": 1,
        "track_id": "test-target",
        "coordinate_space": "source-pixels-xyxy",
        "anchors": anchors,
        "forced_occlusion_ranges": ranges or [],
    }


def validated_sidecar(frames, candidates):
    raw = sidecar_document(frames, candidates)
    return tracker.validate_sidecar(raw, digest="test-digest")


def records_by_frame(track):
    return {record["frame"]: record for record in track["frames"]}


class PreviewTargetTests(unittest.TestCase):
    def test_bidirectional_agreement_tracks_only_bracketed_frames(self):
        frames = [0, 1, 2, 3, 4, 5, 6]
        candidates = [[box(frame * 2), box(400)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 1, box(2)), anchor("right", 5, box(10))]
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        self.assertEqual(result[0]["state"], "untracked")
        self.assertEqual(result[1]["state"], "anchor")
        self.assertEqual(result[2]["state"], "tracked")
        self.assertEqual(result[3]["state"], "tracked")
        self.assertEqual(result[4]["state"], "tracked")
        self.assertEqual(result[5]["state"], "anchor")
        self.assertEqual(result[6]["state"], "untracked")
        self.assertEqual(result[0]["reason"], "outside-anchor-coverage")
        self.assertEqual(result[6]["reason"], "outside-anchor-coverage")

    def test_candidate_reordering_does_not_change_identity_result(self):
        frames = [0, 1, 2, 3, 4]
        candidates = [[box(frame * 2), box(400)] for frame in frames]
        first_sidecar = validated_sidecar(frames, candidates)
        second_sidecar = validated_sidecar(
            frames, [list(reversed(items)) for items in candidates]
        )
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 4, box(8))]
        )

        first = tracker.build_track(first_sidecar, anchors)
        second = tracker.build_track(second_sidecar, anchors)
        first_values = [
            (item["frame"], item["state"], item["box_xyxy"])
            for item in first["frames"]
        ]
        second_values = [
            (item["frame"], item["state"], item["box_xyxy"])
            for item in second["frames"]
        ]

        self.assertEqual(first_values, second_values)
        self.assertNotEqual(
            first["resolved_anchors"][0]["temporary_candidate_index"],
            second["resolved_anchors"][0]["temporary_candidate_index"],
        )

    def test_forced_occlusion_precedes_normal_coverage(self):
        frames = list(range(7))
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("only", 3, box(3))],
            [{"start_frame": 0, "end_frame": 1, "note": "offscreen"}],
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        for frame in (0, 1):
            self.assertEqual(result[frame]["state"], "occluded")
            self.assertIsNone(result[frame]["box_xyxy"])
            self.assertEqual(result[frame]["evidence"], "user-forced")
            self.assertEqual(result[frame]["reason"], "forced-occlusion-range")
        self.assertEqual(result[2]["state"], "untracked")
        self.assertEqual(result[3]["state"], "anchor")
        self.assertEqual(result[4]["state"], "untracked")

    def test_forced_range_blocks_anchor_interval(self):
        frames = list(range(7))
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 6, box(6))],
            [{"start_frame": 2, "end_frame": 4}],
        )

        track = tracker.build_track(sidecar, anchors)
        result = records_by_frame(track)

        self.assertEqual(result[1]["state"], "untracked")
        for frame in (2, 3, 4):
            self.assertEqual(result[frame]["state"], "occluded")
            self.assertEqual(result[frame]["evidence"], "user-forced")
        self.assertEqual(result[5]["state"], "untracked")
        self.assertEqual(
            track["interval_diagnostics"][0]["status"],
            "blocked-by-forced-occlusion",
        )

    def test_directional_pass_stops_at_forced_boundary(self):
        frames = [0, 1, 2, 3, 4]
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)

        forward = tracker.run_direction(
            sidecar, [1, 2, 3, 4], tuple(box(0)), blocked_frames={2, 3}
        )
        backward = tracker.run_direction(
            sidecar, [3, 2, 1, 0], tuple(box(4)), blocked_frames={2, 3}
        )

        self.assertEqual(forward[1]["kind"], "hypothesis")
        self.assertEqual(forward[2]["kind"], "blocked")
        self.assertNotIn(3, forward)
        self.assertEqual(backward[3]["kind"], "blocked")
        self.assertNotIn(2, backward)

    def test_anchor_inside_forced_range_is_rejected(self):
        frames = list(range(5))
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("bad", 2, box(2))],
            [{"start_frame": 1, "end_frame": 3}],
        )

        with self.assertRaisesRegex(tracker.TrackError, "inside a forced"):
            tracker.build_track(sidecar, anchors)

    def test_invalid_forced_ranges_are_rejected(self):
        frames = [0, 2, 4, 6]
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        base_anchor = [anchor("only", 6, box(6))]
        invalid_ranges = [
            [{"start_frame": 4, "end_frame": 2}],
            [{"start_frame": 1, "end_frame": 2}],
            [
                {"start_frame": 0, "end_frame": 2},
                {"start_frame": 2, "end_frame": 4},
            ],
            [
                {"start_frame": 0, "end_frame": 4},
                {"start_frame": 2, "end_frame": 2},
            ],
        ]

        for ranges in invalid_ranges:
            with self.subTest(ranges=ranges):
                with self.assertRaises(tracker.TrackError):
                    tracker.build_track(
                        sidecar, anchor_document(base_anchor, ranges)
                    )

    def test_adjacent_non_overlapping_ranges_are_allowed(self):
        frames = [0, 2, 4, 6]
        candidates = [[box(frame)] for frame in frames]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("only", 6, box(6))],
            [
                {"start_frame": 0, "end_frame": 0},
                {"start_frame": 2, "end_frame": 2},
            ],
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        self.assertEqual(result[0]["state"], "occluded")
        self.assertEqual(result[2]["state"], "occluded")
        self.assertEqual(result[4]["state"], "untracked")

    def test_single_direction_only_is_ambiguous(self):
        frames = [0, 1, 2, 3, 4]
        candidates = [
            [box(0)],
            [box(2)],
            [box(82)],
            [box(80)],
            [box(78)],
        ]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 4, box(78))]
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        for frame in (1, 2, 3):
            self.assertEqual(result[frame]["state"], "ambiguous")
            self.assertEqual(result[frame]["reason"], "single-direction-only")
            self.assertIsNone(result[frame]["box_xyxy"])

    def test_directional_disagreement_is_ambiguous(self):
        frames = [0, 1, 2]
        candidates = [[box(0)], [box(5), box(75)], [box(80)]]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 2, box(80))]
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        self.assertEqual(result[1]["state"], "ambiguous")
        self.assertEqual(
            result[1]["reason"], "forward-backward-disagreement"
        )
        self.assertIsNone(result[1]["box_xyxy"])
        self.assertIsNotNone(result[1]["hypotheses"]["forward"])
        self.assertIsNotNone(result[1]["hypotheses"]["backward"])

    def test_neither_direction_is_occluded(self):
        frames = [0, 1, 2]
        candidates = [[box(0)], [box(500)], [box(0)]]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 2, box(0))]
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        self.assertEqual(result[1]["state"], "occluded")
        self.assertEqual(result[1]["reason"], "no-bidirectional-target-match")
        self.assertIsNone(result[1]["box_xyxy"])

    def test_detector_miss_is_occluded(self):
        frames = [0, 1, 2]
        candidates = [[box(0)], [], [box(0)]]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 2, box(0))]
        )

        result = records_by_frame(tracker.build_track(sidecar, anchors))

        self.assertEqual(result[1]["state"], "occluded")
        self.assertEqual(result[1]["reason"], "detector-miss")

    def test_point_and_snapshot_conflict_is_rejected(self):
        frames = [0, 1]
        candidates = [[box(0), box(200)], [box(0), box(200)]]
        sidecar = validated_sidecar(frames, candidates)
        conflicting = {
            "anchor_id": "conflict",
            "frame": 0,
            "clicked_point_xy": [250, 50],
            "box_snapshot_xyxy": box(0),
        }

        with self.assertRaisesRegex(tracker.TrackError, "resolves to 0"):
            tracker.build_track(sidecar, anchor_document([conflicting]))

    def test_constant_intervals_one_and_eleven_are_supported(self):
        for interval in (1, 11):
            with self.subTest(interval=interval):
                frames = [0, interval, interval * 2]
                candidates = [[box(0)], [box(2)], [box(4)]]
                sidecar = validated_sidecar(frames, candidates)
                anchors = anchor_document(
                    [
                        anchor("left", frames[0], box(0)),
                        anchor("right", frames[2], box(4)),
                    ]
                )

                track = tracker.build_track(sidecar, anchors)
                result = records_by_frame(track)

                self.assertEqual(track["source"]["sampling_interval"], interval)
                self.assertEqual(result[frames[1]]["state"], "tracked")

    def test_only_anchors_and_tracked_frames_have_final_boxes(self):
        frames = [0, 1, 2, 3, 4]
        candidates = [[box(0)], [box(2)], [box(500)], [box(2)], [box(0)]]
        sidecar = validated_sidecar(frames, candidates)
        anchors = anchor_document(
            [anchor("left", 0, box(0)), anchor("right", 4, box(0))]
        )

        track = tracker.build_track(sidecar, anchors)

        for record in track["frames"]:
            if record["box_xyxy"] is not None:
                self.assertIn(record["state"], ("anchor", "tracked"))
            if record["state"] in ("ambiguous", "occluded", "untracked"):
                self.assertIsNone(record["box_xyxy"])


if __name__ == "__main__":
    unittest.main()
