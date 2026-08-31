"""Focused unit tests for the standalone anchor-review renderer."""

import unittest

import render_preview_anchor_review as review
import track_preview_target as tracker


def box(x, y=0, width=20, height=40):
    return [x, y, x + width, y + height]


def validated_documents(frames, candidates, anchors, ranges=None, digest="digest"):
    sidecar_raw = {
        "version": 2,
        "fps": 30.0,
        "sample_indices": frames,
        "boxes": [items[0] if items else None for items in candidates],
        "all_boxes": candidates,
    }
    anchor_raw = {
        "schema": "blendcap-preview-target-anchors",
        "version": 1,
        "track_id": "red-shorts-step2",
        "coordinate_space": "source-pixels-xyxy",
        "source": {
            "sidecar_sha256": digest,
            "fps": 30.0,
            "sampling_interval": frames[1] - frames[0],
        },
        "anchors": anchors,
        "forced_occlusion_ranges": ranges or [],
    }
    sidecar = tracker.validate_sidecar(sidecar_raw, digest=digest)
    anchor_document = tracker.validate_anchors(anchor_raw, sidecar)
    return sidecar, anchor_document


def anchor(anchor_id, frame, snapshot):
    return {
        "anchor_id": anchor_id,
        "frame": frame,
        "clicked_point_xy": [
            (snapshot[0] + snapshot[2]) / 2.0,
            (snapshot[1] + snapshot[3]) / 2.0,
        ],
        "box_snapshot_xyxy": snapshot,
    }


class PreviewAnchorReviewTests(unittest.TestCase):
    def test_anchor_specs_use_sampled_neighbors_and_snapshot(self):
        frames = [0, 2, 4, 6]
        candidates = [
            [box(0), box(100)],
            [box(2), box(102)],
            [box(4), box(104)],
            [box(6), box(106)],
        ]
        sidecar, anchors = validated_documents(
            frames,
            candidates,
            [anchor("red-shorts-step2-004", 4, box(104))],
        )

        specs = review.build_anchor_review_specs(sidecar, anchors)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["display_label"], "red-shorts")
        self.assertEqual(
            [panel["frame"] for panel in specs[0]["panels"]],
            [2, 4, 6],
        )
        anchor_panel = specs[0]["panels"][1]
        self.assertEqual(anchor_panel["role"], "anchor")
        self.assertEqual(anchor_panel["anchor_snapshot_xyxy"], box(104))
        self.assertEqual(anchor_panel["anchor_temporary_candidate_index"], 1)

    def test_missing_neighbor_is_explicit_at_sample_boundary(self):
        frames = [0, 3, 6]
        candidates = [[box(frame)] for frame in frames]
        sidecar, anchors = validated_documents(
            frames,
            candidates,
            [anchor("first", 0, box(0)), anchor("last", 6, box(6))],
        )

        specs = review.build_anchor_review_specs(sidecar, anchors)

        self.assertTrue(specs[0]["panels"][0]["missing"])
        self.assertIsNone(specs[0]["panels"][0]["frame"])
        self.assertTrue(specs[1]["panels"][2]["missing"])
        self.assertIsNone(specs[1]["panels"][2]["frame"])

    def test_forced_midpoint_is_a_sampled_frame_by_position(self):
        frames = [0, 5, 10, 15, 20, 25, 30]
        candidates = [[box(frame)] for frame in frames]
        sidecar, anchors = validated_documents(
            frames,
            candidates,
            [anchor("left", 0, box(0)), anchor("right", 30, box(30))],
            [{"start_frame": 5, "end_frame": 20, "note": "hidden"}],
        )

        specs = review.build_forced_review_specs(sidecar, anchors)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["note"], "hidden")
        self.assertEqual(
            [panel["frame"] for panel in specs[0]["panels"]],
            [5, 10, 20],
        )

    def test_required_frames_deduplicate_repeated_range_panels(self):
        frames = [0, 1, 2]
        candidates = [[box(frame)] for frame in frames]
        sidecar, anchors = validated_documents(
            frames,
            candidates,
            [anchor("only", 0, box(0))],
            [{"start_frame": 2, "end_frame": 2}],
        )
        anchor_specs = review.build_anchor_review_specs(sidecar, anchors)
        forced_specs = review.build_forced_review_specs(sidecar, anchors)

        self.assertEqual(
            review.required_frames(anchor_specs, forced_specs), [0, 1, 2]
        )
        self.assertEqual(
            [panel["frame"] for panel in forced_specs[0]["panels"]],
            [2, 2, 2],
        )

    def test_candidate_reordering_changes_only_temporary_position(self):
        frames = [0, 1, 2]
        target = box(100)
        first_sidecar, first_anchors = validated_documents(
            frames,
            [[box(0), target], [box(1), target], [box(2), target]],
            [anchor("target", 1, target)],
        )
        second_sidecar, second_anchors = validated_documents(
            frames,
            [[target, box(0)], [target, box(1)], [target, box(2)]],
            [anchor("target", 1, target)],
        )

        first = review.build_anchor_review_specs(
            first_sidecar, first_anchors
        )[0]["panels"][1]
        second = review.build_anchor_review_specs(
            second_sidecar, second_anchors
        )[0]["panels"][1]

        self.assertEqual(first["anchor_snapshot_xyxy"], target)
        self.assertEqual(second["anchor_snapshot_xyxy"], target)
        self.assertEqual(first["anchor_temporary_candidate_index"], 1)
        self.assertEqual(second["anchor_temporary_candidate_index"], 0)

    def test_safe_names_cannot_create_nested_output_paths(self):
        self.assertEqual(review.safe_filename("../red shorts/a"), "red_shorts_a")
        self.assertEqual(
            review.safe_filename("red-shorts-step1-033"),
            "red-shorts-step1-033",
        )

    def test_missing_snapshot_is_rejected_for_visual_review(self):
        frames = [0, 1]
        candidates = [[box(0)], [box(1)]]
        sidecar_raw = {
            "version": 2,
            "fps": 30.0,
            "sample_indices": frames,
            "boxes": [box(0), box(1)],
            "all_boxes": candidates,
        }
        anchor_raw = {
            "schema": "blendcap-preview-target-anchors",
            "version": 1,
            "track_id": "target",
            "coordinate_space": "source-pixels-xyxy",
            "anchors": [
                {
                    "anchor_id": "point-only",
                    "frame": 0,
                    "clicked_point_xy": [10.0, 20.0],
                }
            ],
            "forced_occlusion_ranges": [],
        }
        sidecar = tracker.validate_sidecar(sidecar_raw, digest="digest")
        anchors = tracker.validate_anchors(anchor_raw, sidecar)

        with self.assertRaises(review.ReviewError):
            review.require_anchor_snapshots(anchors)

    def test_source_metadata_is_required_for_review(self):
        with self.assertRaisesRegex(review.ReviewError, "source metadata"):
            review.require_anchor_source_metadata({})

        with self.assertRaisesRegex(review.ReviewError, "sidecar_sha256"):
            review.require_anchor_source_metadata(
                {"source": {"fps": 30.0, "sampling_interval": 1}}
            )


if __name__ == "__main__":
    unittest.main()
