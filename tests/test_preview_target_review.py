"""Standard-library tests for the offline target review-and-apply workflow."""

import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import apply_preview_target_review as apply_review
import build_preview_target_review as build_review
import track_preview_target as tracker


def box(x, y=10, width=80, height=120):
    return [x, y, x + width, y + height]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class ReviewFixture:
    def __init__(self, root, interval=1, ambiguous=False, ranges=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.frames = [0, interval, 2 * interval, 3 * interval, 4 * interval]
        if ambiguous:
            candidates = [
                [box(0), box(350)],
                [box(3), box(347)],
                [box(8), box(78)],
                [box(12), box(72)],
                [box(16), box(64)],
            ]
            anchors = [
                {"anchor_id": "left", "frame": self.frames[0], "box_snapshot_xyxy": box(0)},
                {"anchor_id": "right", "frame": self.frames[-1], "box_snapshot_xyxy": box(64)},
            ]
        else:
            candidates = [[box(i * 4), box(350)] for i in range(5)]
            anchors = [
                {"anchor_id": "left", "frame": self.frames[0], "box_snapshot_xyxy": box(0)},
                {"anchor_id": "right", "frame": self.frames[-1], "box_snapshot_xyxy": box(16)},
            ]
        self.sidecar_raw = {
            "version": 2,
            "fps": 25.0,
            "sample_indices": self.frames,
            "boxes": [items[0] for items in candidates],
            "all_boxes": candidates,
        }
        self.sidecar_path = self.root / "sidecar.json"
        write_json(self.sidecar_path, self.sidecar_raw)
        digest = build_review.sha256_file(self.sidecar_path)
        self.anchors_raw = {
            "schema": "blendcap-preview-target-anchors",
            "version": 1,
            "track_id": "durable-target",
            "coordinate_space": "source-pixels-xyxy",
            "source": {"sidecar_sha256": digest, "fps": 25.0, "sampling_interval": interval},
            "anchors": anchors,
            "forced_occlusion_ranges": ranges or [],
        }
        self.anchors_path = self.root / "anchors.json"
        write_json(self.anchors_path, self.anchors_raw)
        sidecar = tracker.validate_sidecar(self.sidecar_raw, digest)
        self.track_raw = tracker.build_track(sidecar, self.anchors_raw)
        self.track_path = self.root / "track.json"
        write_json(self.track_path, self.track_raw)
        self.video_path = self.root / "source.mp4"
        self.video_path.write_bytes(b"synthetic video identity")

    def context(self, target="red"):
        return build_review.validate_inputs(
            self.sidecar_path,
            {target: self.anchors_path},
            {target: self.track_path},
        )

    def decisions(self, values, target="red"):
        context = self.context(target)
        source = context["target_provenance"]
        return {
            "schema": build_review.DECISION_SCHEMA,
            "version": 1,
            "review_id": "review-test",
            "created_at_utc": "2026-01-01T00:00:00Z",
            "source": {
                "sidecar_sha256": context["sidecar"]["sha256"],
                "video_sha256": build_review.sha256_file(self.video_path),
                "fusion_sha256": None,
                "fps": 25.0,
                "sampling_interval": context["sidecar"]["sampling_interval"],
                "targets": source,
            },
            "target_ids": [target],
            "frame_range": [self.frames[0], self.frames[-1]],
            "decisions": values,
            "statement": {
                "appearance_is_non_authoritative": True,
                "temporary_candidate_indexes_are_per_frame_only": True,
            },
        }


class FakeCapture:
    def __init__(self, values, opened=True):
        self.values = values
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def get(self, property_id):
        return self.values[property_id]

    def release(self):
        self.released = True


class FakeCV2:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 7

    def __init__(self, capture):
        self.capture = capture

    def VideoCapture(self, _path):
        return self.capture


class PreviewTargetReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = ReviewFixture(self.temporary.name, ambiguous=True)

    def tearDown(self):
        self.temporary.cleanup()

    def _base(self):
        return apply_review.validate_base(
            self.fixture.sidecar_path,
            self.fixture.anchors_path,
            self.fixture.track_path,
        )

    def _validated(self, values):
        raw = self.fixture.decisions(values)
        path = Path(self.temporary.name) / "decisions.json"
        write_json(path, raw)
        return raw, path, apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_review_model_queues_ambiguous_frames_and_starts_blank(self):
        context = self.fixture.context()
        model = build_review.build_review_model(
            context, build_review.sha256_file(self.fixture.video_path)
        )
        self.assertTrue(model["queue"])
        self.assertTrue(all(
            next(x for x in model["frames"] if x["frame"] == item["frame"])["targets"]["red"]["geometry"]["state"] == "ambiguous"
            for item in model["queue"]
        ))
        document = build_review._html_document(model)
        self.assertIn("let decisions={}", document)
        self.assertIn("Appearance is non-authoritative", document)
        self.assertNotIn("selected=", document)
        self.assertIn("Complete every review item before export", document)
        self.assertIn("replaces_forced_range", document)

    def test_embedded_json_cannot_terminate_the_script_element(self):
        model = build_review.build_review_model(
            self.fixture.context(), build_review.sha256_file(self.fixture.video_path)
        )
        hostile = "</script><script>alert('x')</script> — snowman ☃"
        model["frames"][0]["targets"]["red"]["geometry"]["reason"] = hostile
        document = build_review._html_document(model)
        self.assertNotIn(hostile, document)
        self.assertIn("<\\/script><script>", document)
        self.assertIn("\\u2603", document)

    def test_frame_range_requires_sampled_inclusive_boundaries(self):
        sidecar = self.fixture.context()["sidecar"]
        self.assertEqual(build_review.parse_frame_range("1:3", sidecar), (1, 3))
        with self.assertRaisesRegex(build_review.ReviewError, "reversed"):
            build_review.parse_frame_range("7:3", sidecar)
        with self.assertRaisesRegex(build_review.ReviewError, "start"):
            build_review.parse_frame_range("9:9", sidecar)

    def test_strict_track_rebuild_rejects_stale_track(self):
        raw = copy.deepcopy(self.fixture.track_raw)
        raw["frames"][1]["state"] = "tracked"
        write_json(self.fixture.track_path, raw)
        with self.assertRaisesRegex(build_review.ReviewError, "exactly match"):
            self.fixture.context()

    def test_select_candidate_becomes_exact_box_and_center_without_temp_identity(self):
        frame = self.fixture.frames[2]
        raw, _, decisions = self._validated([
            {"target_id": "red", "frame": frame, "action": "select-existing-candidate", "temporary_candidate_index": 1, "note": "human selection"}
        ])
        revised, track, changes = apply_review.apply_decisions(
            self._base(), decisions, hashlib.sha256(json.dumps(raw).encode()).hexdigest()
        )
        added = next(item for item in revised["anchors"] if item["frame"] == frame)
        expected = self.fixture.sidecar_raw["all_boxes"][2][1]
        self.assertEqual(added["box_snapshot_xyxy"], expected)
        self.assertEqual(added["clicked_point_xy"], [(expected[0] + expected[2]) / 2, (expected[1] + expected[3]) / 2])
        self.assertNotIn("temporary_candidate_index", added)
        self.assertNotIn("temporary_candidate_index", changes[0])
        self.assertEqual(next(item for item in track["frames"] if item["frame"] == frame)["state"], "anchor")

    def test_reviewed_selection_handoff_uses_verified_video_dimensions_and_explicit_identity(self):
        frame = self.fixture.frames[2]
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": frame, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        base = self._base()
        revised, _, _ = apply_review.apply_decisions(base, decisions, "a" * 64)
        capture = FakeCapture({3: 640, 4: 480, 5: 25.0, 7: 100})
        received = {}

        def private_consumer(payload, *, character_id):
            received["payload"] = copy.deepcopy(payload)
            received["character_id"] = character_id
            return "private-keyframe"

        with mock.patch.dict(sys.modules, {"cv2": FakeCV2(capture)}):
            payload, result = apply_review.handoff_reviewed_selection_to_vta(
                base,
                revised,
                decisions[0],
                video_path=self.fixture.video_path,
                expected_video_sha256=build_review.sha256_file(self.fixture.video_path),
                source_id="source-1",
                analysis_revision_id="analysis-review-1",
                provider="blendcap-yolo-preview",
                model="yolo-person",
                configuration_id="review-config",
                character_id="character-user-selected",
                private_consumer=private_consumer,
            )

        self.assertEqual(result, "private-keyframe")
        self.assertEqual(received["character_id"], "character-user-selected")
        self.assertEqual(payload, received["payload"])
        self.assertEqual(payload["source"]["width"], 640)
        self.assertEqual(payload["source"]["height"], 480)
        self.assertEqual(payload["observation"]["box_xyxy"], [8.0, 350.0, 88.0, 470.0])
        self.assertNotIn("character_id", payload)
        self.assertNotIn("temporary_candidate_index", json.dumps(payload))
        self.assertTrue(capture.released)

    def test_reviewed_selection_handoff_rejects_missing_dimensions_before_private_consumer(self):
        frame = self.fixture.frames[2]
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": frame, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        base = self._base()
        revised, _, _ = apply_review.apply_decisions(base, decisions, "b" * 64)
        capture = FakeCapture({3: 0, 4: 480, 5: 25.0, 7: 100})
        private_consumer = mock.Mock()

        with mock.patch.dict(sys.modules, {"cv2": FakeCV2(capture)}):
            with self.assertRaisesRegex(apply_review.ApplyError, "positive width and height"):
                apply_review.handoff_reviewed_selection_to_vta(
                    base,
                    revised,
                    decisions[0],
                    video_path=self.fixture.video_path,
                    expected_video_sha256=build_review.sha256_file(self.fixture.video_path),
                    source_id="source-1",
                    analysis_revision_id="analysis-review-1",
                    provider="blendcap-yolo-preview",
                    model="yolo-person",
                    configuration_id="review-config",
                    character_id="character-user-selected",
                    private_consumer=private_consumer,
                )

        private_consumer.assert_not_called()
        self.assertTrue(capture.released)

    def test_candidate_reordering_resolves_physical_selected_box(self):
        frame = self.fixture.frames[2]
        first = self.fixture.sidecar_raw["all_boxes"][2]
        selected = copy.deepcopy(first[1])
        reordered = copy.deepcopy(self.fixture.sidecar_raw)
        reordered["all_boxes"][2] = list(reversed(reordered["all_boxes"][2]))
        reordered["boxes"][2] = reordered["all_boxes"][2][0]
        write_json(self.fixture.sidecar_path, reordered)
        digest = build_review.sha256_file(self.fixture.sidecar_path)
        anchors = copy.deepcopy(self.fixture.anchors_raw)
        anchors["source"]["sidecar_sha256"] = digest
        write_json(self.fixture.anchors_path, anchors)
        sidecar = tracker.validate_sidecar(reordered, digest)
        write_json(self.fixture.track_path, tracker.build_track(sidecar, anchors))
        raw = self.fixture.decisions([
            {"target_id": "red", "frame": frame, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        validated = apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)
        revised, _, _ = apply_review.apply_decisions(self._base(), validated, "d" * 64)
        added = next(item for item in revised["anchors"] if item["frame"] == frame)
        self.assertEqual(added["box_snapshot_xyxy"], selected)

    def test_invalid_candidate_positions_are_rejected(self):
        frame = self.fixture.frames[1]
        for value in (True, -1, 99, None):
            with self.subTest(value=value):
                raw = self.fixture.decisions([
                    {"target_id": "red", "frame": frame, "action": "select-existing-candidate", "temporary_candidate_index": value}
                ])
                with self.assertRaises(apply_review.ApplyError):
                    apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_stale_decision_hashes_are_rejected(self):
        raw = self.fixture.decisions([
            {"target_id": "red", "frame": 1, "action": "uncertain"}
        ])
        raw["source"]["targets"]["red"]["track_sha256"] = "0" * 64
        with self.assertRaisesRegex(apply_review.ApplyError, "track hash"):
            apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_range_actions_require_semantics_and_reject_overlap(self):
        for action in ("merged-shared-detection", "no-valid-candidate"):
            item = {"target_id": "red", "frame": 1, "action": action, "start_frame": 1, "end_frame": 2, "note": ""}
            if action == "merged-shared-detection":
                item["temporary_candidate_index"] = 0
            raw = self.fixture.decisions([item])
            with self.assertRaisesRegex(apply_review.ApplyError, "note"):
                apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": 1, "action": "not-visible", "start_frame": 1, "end_frame": 3},
            {"target_id": "red", "frame": 2, "action": "no-valid-candidate", "start_frame": 2, "end_frame": 2, "note": "visible without a box"},
        ])
        with self.assertRaisesRegex(apply_review.ApplyError, "overlap"):
            apply_review.apply_decisions(self._base(), decisions, "a" * 64)

    def test_range_cannot_extend_outside_reviewed_scope(self):
        raw = self.fixture.decisions([
            {"target_id": "red", "frame": 2, "action": "not-visible", "start_frame": 1, "end_frame": 3}
        ])
        raw["frame_range"] = [2, 3]
        with self.assertRaisesRegex(apply_review.ApplyError, "outside the reviewed"):
            apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_replacement_can_extend_a_forced_range(self):
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = ReviewFixture(
            self.temporary.name,
            ranges=[{"start_frame": 1, "end_frame": 1, "note": "old"}],
        )
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": 2, "action": "not-visible", "start_frame": 1, "end_frame": 2, "note": "extended", "replaces_forced_range": {"start_frame": 1, "end_frame": 1}}
        ])
        revised, track, _ = apply_review.apply_decisions(self._base(), decisions, "b" * 64)
        self.assertEqual(revised["forced_occlusion_ranges"], [{"start_frame": 1, "end_frame": 2, "note": "extended"}])
        states = {item["frame"]: item["state"] for item in track["frames"]}
        self.assertEqual(states[1], "occluded")
        self.assertEqual(states[2], "occluded")

    def test_anchor_inside_resulting_forced_range_is_rejected(self):
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": 2, "action": "select-existing-candidate", "temporary_candidate_index": 0},
            {"target_id": "red", "frame": 1, "action": "not-visible", "start_frame": 1, "end_frame": 3},
        ])
        with self.assertRaisesRegex(apply_review.ApplyError, "inside a forced"):
            apply_review.apply_decisions(self._base(), decisions, "c" * 64)

    def test_uncertain_makes_no_track_change(self):
        _, _, decisions = self._validated([
            {"target_id": "red", "frame": 1, "action": "uncertain", "note": "cannot tell"}
        ])
        revised, track, _ = apply_review.apply_decisions(self._base(), decisions, "e" * 64)
        self.assertEqual(revised, self.fixture.anchors_raw)
        self.assertEqual(track, self.fixture.track_raw)

    def test_revision_tracks_changed_intervals_and_outside_equivalence(self):
        raw, path, decisions = self._validated([
            {"target_id": "red", "frame": 2, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        base = self._base()
        revised, track, changes = apply_review.apply_decisions(base, decisions, build_review.sha256_file(path))
        revision, _, _ = apply_review.build_revision(
            base, revised, track, changes, path, raw,
            Path("new-anchors.json"), Path("new-track.json")
        )
        self.assertTrue(revision["verification"]["outside_affected_intervals_unchanged"])
        self.assertTrue(revision["affected_intervals"])
        self.assertFalse(revision["verification"]["temporary_candidate_indexes_persisted_as_identity"])

    def test_interval_eleven_changes_use_sampled_adjacency(self):
        other = ReviewFixture(Path(self.temporary.name) / "step11", interval=11, ambiguous=True)
        raw = other.decisions([
            {"target_id": "red", "frame": 22, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        decisions = apply_review.validate_decisions(
            raw,
            apply_review.validate_base(other.sidecar_path, other.anchors_path, other.track_path),
            "red",
            other.video_path,
        )
        base = apply_review.validate_base(other.sidecar_path, other.anchors_path, other.track_path)
        _, track, _ = apply_review.apply_decisions(base, decisions, "f" * 64)
        intervals = apply_review._changed_intervals(base["track"], track, 11)
        self.assertTrue(all((item["end_frame"] - item["start_frame"]) % 11 == 0 for item in intervals))

    def test_fusion_suggestion_is_display_only_and_never_preselected(self):
        context = self.fixture.context()
        fusion = {
            "schema": "blendcap-preview-geometry-appearance-fusion-diagnostic",
            "version": 1,
            "source": {"sidecar_sha256": context["sidecar"]["sha256"], "fps": 25.0, "sampling_interval": 1},
            "policy": {"appearance_is_authoritative": False},
            "frames": [],
        }
        for record in context["tracks"]["red"]["frames"]:
            proposal = None
            if record["state"] == "ambiguous":
                proposal = {"status": "candidate-proposed-unthresholded", "temporary_candidate_index": 0, "threshold_status": "not-configured", "appearance_is_authoritative": False, "box_xyxy": self.fixture.sidecar_raw["all_boxes"][record["frame"]][0]}
            fusion["frames"].append({"frame": record["frame"], "targets": [{"target_id": "red", "source_geometry": {key: copy.deepcopy(value) for key, value in record.items() if key != "frame"}, "state": record["state"], "diagnostic_appearance_proposal": proposal}]})
        build_review._validate_fusion(fusion, context["sidecar"], context["tracks"])
        context["fusion"] = fusion
        model = build_review.build_review_model(context, "1" * 64)
        self.assertTrue(any(item["targets"]["red"]["appearance_suggestion"] for item in model["frames"]))
        self.assertNotIn("selected=", build_review._html_document(model))

    def test_fusion_provenance_is_bound_to_exact_anchor_and_track_hashes(self):
        context = self.fixture.context()
        provenance = context["target_provenance"]["red"]
        fusion = {
            "schema": "blendcap-preview-geometry-appearance-fusion-diagnostic",
            "version": 1,
            "source": {"sidecar_sha256": context["sidecar"]["sha256"], "fps": 25.0, "sampling_interval": 1},
            "provenance": {"targets": [{"target_id": "red", "track_id": provenance["track_id"], "anchors_sha256": provenance["anchors_sha256"], "geometry_track_sha256": "0" * 64}]},
            "policy": {"appearance_is_authoritative": False},
            "frames": [],
        }
        with self.assertRaisesRegex(build_review.ReviewError, "track hash"):
            build_review._validate_fusion(
                fusion, context["sidecar"], context["tracks"], context["target_provenance"]
            )

    def test_directional_chain_stop_is_available_for_review(self):
        model = build_review.build_review_model(
            self.fixture.context(), build_review.sha256_file(self.fixture.video_path)
        )
        chains = [item["targets"]["red"]["directional_chain"] for item in model["frames"]]
        self.assertTrue(all(chain is not None for chain in chains))
        self.assertTrue(any(chain["forward_stop"] or chain["backward_stop"] for chain in chains))

    def test_decisions_with_fusion_hash_require_the_exact_fusion_input(self):
        raw = self.fixture.decisions([
            {"target_id": "red", "frame": 1, "action": "uncertain"}
        ])
        fusion = Path(self.temporary.name) / "fusion.json"
        base = self._base()
        write_json(fusion, {
            "schema": "blendcap-preview-geometry-appearance-fusion-diagnostic",
            "version": 1,
            "source": {"sidecar_sha256": base["sidecar_sha256"], "fps": 25.0, "sampling_interval": 1},
            "policy": {"appearance_is_authoritative": False},
            "provenance": {"targets": [{"target_id": "red", "track_id": base["track"]["track_id"], "anchors_sha256": base["anchors_sha256"], "geometry_track_sha256": base["track_sha256"]}]},
        })
        raw["source"]["fusion_sha256"] = build_review.sha256_file(fusion)
        with self.assertRaisesRegex(apply_review.ApplyError, "--fusion"):
            apply_review.validate_decisions(raw, base, "red", self.fixture.video_path)
        apply_review.validate_decisions(raw, base, "red", self.fixture.video_path, fusion)

    def test_duplicate_decision_for_target_frame_is_rejected(self):
        item = {"target_id": "red", "frame": 1, "action": "uncertain"}
        raw = self.fixture.decisions([item, copy.deepcopy(item)])
        with self.assertRaisesRegex(apply_review.ApplyError, "Duplicate"):
            apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_shared_candidate_cannot_become_two_ordinary_identities(self):
        raw = self.fixture.decisions([
            {"target_id": "red", "frame": 1, "action": "select-existing-candidate", "temporary_candidate_index": 0},
            {"target_id": "black", "frame": 1, "action": "select-existing-candidate", "temporary_candidate_index": 0},
        ])
        raw["target_ids"].append("black")
        raw["source"]["targets"]["black"] = {
            "anchors_sha256": "0" * 64,
            "track_sha256": "1" * 64,
            "track_id": "black-track",
        }
        with self.assertRaisesRegex(apply_review.ApplyError, "same ordinary candidate"):
            apply_review.validate_decisions(raw, self._base(), "red", self.fixture.video_path)

    def test_parent_revision_is_hash_bound_without_mutating_it(self):
        raw, decisions_path, decisions = self._validated([
            {"target_id": "red", "frame": 2, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        base = self._base()
        revised, track, changes = apply_review.apply_decisions(base, decisions, "1" * 64)
        parent_path = Path(self.temporary.name) / "parent.json"
        parent = {
            "schema": apply_review.REVISION_SCHEMA,
            "version": 1,
            "revision_id": "prior",
            "outputs": {
                "anchors_sha256": base["anchors_sha256"],
                "track_sha256": base["track_sha256"],
            },
        }
        write_json(parent_path, parent)
        before = parent_path.read_bytes()
        revision, _, _ = apply_review.build_revision(
            base, revised, track, changes, decisions_path, raw,
            Path("a.json"), Path("t.json"), parent_path
        )
        self.assertEqual(revision["parent_revision"]["sha256"], build_review.sha256_file(parent_path))
        self.assertEqual(parent_path.read_bytes(), before)

    def test_unexpected_track_change_outside_decision_scope_is_rejected(self):
        raw, path, decisions = self._validated([
            {"target_id": "red", "frame": 1, "action": "uncertain"}
        ])
        base = self._base()
        revised, track, changes = apply_review.apply_decisions(base, decisions, "2" * 64)
        track["frames"][-1]["reason"] = "tampered"
        with self.assertRaisesRegex(apply_review.ApplyError, "outside"):
            apply_review.build_revision(
                base, revised, track, changes, path, raw,
                Path("a.json"), Path("t.json")
            )

    def test_successful_publication_writes_three_exact_documents(self):
        parent = Path.cwd() / "checkpoints" / "dev-preview"
        token = f"test-review-publish-{os.getpid()}"
        destinations = [parent / f"{token}-{suffix}.json" for suffix in ("a", "t", "r")]
        for path in destinations:
            self.addCleanup(lambda value=path: value.unlink(missing_ok=True))
        values = [b"anchors\n", b"track\n", b"revision\n"]
        apply_review.publish_outputs(list(zip(destinations, values)), [])
        self.assertEqual([path.read_bytes() for path in destinations], values)
        self.assertFalse(any(parent.glob(f".{token}-*.tmp")))

    def test_cli_does_not_accept_labels_or_thresholds(self):
        required = [
            "--video", "v", "--sidecar", "s", "--anchors", "a", "--track", "t",
            "--decisions", "d", "--target", "red", "--output-anchors", "oa",
            "--output-track", "ot", "--output-revision", "or",
        ]
        for option in ("--labels", "--threshold"):
            with self.subTest(option=option):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        apply_review.parse_args(required + [option, "x"])

    def test_marker_placement_has_no_overlap(self):
        positions = build_review._marker_positions([box(10), box(10), box(11), box(12)], 800, 600)
        self.assertEqual(len(positions), len(set(positions)))
        self.assertTrue(all(
            ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 >= 25
            for i, (x1, y1) in enumerate(positions)
            for x2, y2 in positions[i + 1:]
        ))

    def test_output_paths_are_restricted_and_no_clobber(self):
        workspace = Path(self.temporary.name) / "workspace"
        allowed = workspace / "checkpoints" / "dev-preview"
        allowed.mkdir(parents=True)
        good = allowed / "revision.json"
        self.assertEqual(apply_review.validate_outputs([good], [], workspace)[0], good.resolve())
        with self.assertRaisesRegex(apply_review.ApplyError, "beneath"):
            apply_review.validate_outputs([workspace / "elsewhere.json"], [], workspace)
        good.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(apply_review.ApplyError, "already exists"):
            apply_review.validate_outputs([good], [], workspace)
        self.assertEqual(good.read_text(encoding="utf-8"), "preserve")

    def test_atomic_publication_race_preserves_unexpected_destination(self):
        repository = Path.cwd()
        parent = repository / "checkpoints" / "dev-preview"
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / f"test-review-race-{os.getpid()}.json"
        self.addCleanup(lambda: destination.unlink(missing_ok=True))
        original_validate = apply_review.validate_outputs
        calls = 0

        def raced(outputs, inputs, workspace=None):
            nonlocal calls
            result = original_validate(outputs, inputs, workspace)
            calls += 1
            if calls == 2:
                destination.write_text("concurrent", encoding="utf-8")
            return result

        with mock.patch.object(apply_review, "validate_outputs", side_effect=raced):
            with self.assertRaises(apply_review.ApplyError):
                apply_review.publish_outputs([(destination, b"new\n")], [])
        self.assertEqual(destination.read_text(encoding="utf-8"), "concurrent")

    def test_packet_publication_race_preserves_existing_directory(self):
        parent = Path.cwd() / "checkpoints" / "dev-preview"
        destination = parent / f"test-review-packet-race-{os.getpid()}"
        self.addCleanup(lambda: __import__("shutil").rmtree(destination, ignore_errors=True))
        model = build_review.build_review_model(
            self.fixture.context(), build_review.sha256_file(self.fixture.video_path)
        )
        real_rename = os.rename

        def raced(source, target):
            Path(target).mkdir()
            (Path(target) / "preserve.txt").write_text("concurrent", encoding="utf-8")
            return real_rename(source, target)

        with mock.patch.object(build_review, "render_review_images", return_value={}):
            with mock.patch.object(build_review.os, "rename", side_effect=raced):
                with self.assertRaises(build_review.ReviewError):
                    build_review.publish_packet(
                        destination, model, self.fixture.video_path,
                        self.fixture.context()["sidecar"],
                    )
        self.assertEqual((destination / "preserve.txt").read_text(encoding="utf-8"), "concurrent")

    def test_input_structures_are_not_mutated(self):
        raw, _, decisions = self._validated([
            {"target_id": "red", "frame": 2, "action": "select-existing-candidate", "temporary_candidate_index": 0}
        ])
        base = self._base()
        before_base = copy.deepcopy(base)
        before_raw = copy.deepcopy(raw)
        revised, track, changes = apply_review.apply_decisions(base, decisions, "f" * 64)
        revised["anchors"][0]["box_snapshot_xyxy"][0] = -999
        track["frames"][0]["box_xyxy"][0] = -999
        changes[0]["resolved_box_xyxy"][0] = -999
        self.assertEqual(base, before_base)
        self.assertEqual(raw, before_raw)

    def test_outputs_exclude_paths_embeddings_crops_and_production_decisions(self):
        model = build_review.build_review_model(
            self.fixture.context(), build_review.sha256_file(self.fixture.video_path)
        )
        text = json.dumps(model).lower()
        for forbidden in (str(self.fixture.video_path).lower(), "embedding", "crop_image", "production_identity"):
            self.assertNotIn(forbidden, text)
        raw, path, decisions = self._validated([
            {"target_id": "red", "frame": 1, "action": "uncertain"}
        ])
        base = self._base()
        revised, track, changes = apply_review.apply_decisions(base, decisions, "3" * 64)
        revision, _, _ = apply_review.build_revision(
            base, revised, track, changes, path, raw,
            Path("a.json"), Path("t.json")
        )
        revision_text = json.dumps(revision).lower()
        self.assertNotIn(str(self.fixture.video_path).lower(), revision_text)
        self.assertNotIn("embedding", revision_text)


if __name__ == "__main__":
    unittest.main()
