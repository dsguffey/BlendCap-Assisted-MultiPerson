"""Scoring-only semantic evaluation contract tests (no private-core dependency)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.vta_semantic_evaluation import (
    prepare, resolve_anchor_character_ids, score, sha256, validate_labels, write_new,
)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class SemanticEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_path = self.root / "project_state.json"
        self.cache_path = self.root / "analysis" / "rev.json"
        save(self.project_path, {
            "sources": [{"source_id": "source"}],
            "characters": [{"character_id": "red"}, {"character_id": "black"}],
            "active_analysis_revision_by_source": {"source": "rev"},
        })
        save(self.cache_path, {
            "source": {"source_id": "source"}, "revision_id": "rev",
            "frames": [
                {"frame": frame, "state": "analyzed", "timestamp_seconds": frame / 24,
                 "candidates": [{"observation_id": f"r{frame}", "box_xyxy": [1, 2, 3, 4]},
                                {"observation_id": f"b{frame}", "box_xyxy": [5, 6, 7, 8]}]}
                for frame in (3, 4, 5)
            ],
        })
        self.labels_path = self.root / "labels.json"
        self.baseline_path = self.root / "baseline.json"

    def _labels(self) -> dict:
        labels = prepare(self.root, ("red", "black"), (4, 5, 6), 1)
        for row in labels["frames"]:
            row["labels"] = {
                "red": {"kind": "candidate", "candidate_position": 1},
                "black": {"kind": "candidate", "candidate_position": 2},
            }
        return labels

    def _baseline(self) -> dict:
        return {
            "schema": "blendcap-vta-geometry-baseline-diagnostic", "version": 1,
            "project_sha256": sha256(self.project_path),
            "analysis_cache_sha256": sha256(self.cache_path), "source_id": "source",
            "characters": {
                "red": {"scope": {"start_frame": 3, "end_frame": 5},
                        "records": [{"frame": 3, "state": "visible", "observation_id": "r3"},
                                    {"frame": 4, "state": "unresolved", "observation_id": None},
                                    {"frame": 5, "state": "visible", "observation_id": "b5"}]},
                "black": {"scope": {"start_frame": 3, "end_frame": 5},
                          "records": [{"frame": 3, "state": "visible", "observation_id": "b3"},
                                      {"frame": 4, "state": "visible", "observation_id": "b4"},
                                      {"frame": 5, "state": "unresolved", "observation_id": None}]},
            },
        }

    def test_blank_template_is_source_bound_and_non_authoritative(self) -> None:
        template = prepare(self.root, ("red", "black"), (4, 5, 6), 1)
        self.assertEqual([row["source_frame"] for row in template["frames"]], [3, 4, 5])
        self.assertEqual(template["frames"][0]["labels"]["red"]["kind"], None)
        self.assertEqual(template["frames"][0]["candidates"][0]["position"], 1)
        self.assertFalse(template["ground_truth_used_as_tracker_input"])
        self.assertEqual(template["purpose"], "scoring-only-not-authoritative")

    def test_labels_cannot_be_stale_or_malformed(self) -> None:
        template = prepare(self.root, ("red", "black"), (4, 5, 6), 1)
        labels = self._labels()
        validate_labels(labels, template)
        for mutation in (
            lambda item: item.update(project_sha256="0" * 64),
            lambda item: item["frames"][0]["candidates"][0].update(box_xyxy=[0, 0, 0, 0]),
            lambda item: item["frames"][0]["labels"]["red"].update(candidate_position=3),
            lambda item: item["frames"][0]["labels"]["red"].update(kind="skip"),
        ):
            invalid = copy.deepcopy(labels)
            mutation(invalid)
            with self.assertRaises(ValueError):
                validate_labels(invalid, template)

    def test_scoring_classifications_and_project_immutability(self) -> None:
        labels = self._labels()
        save(self.labels_path, labels)
        save(self.baseline_path, self._baseline())
        before = (sha256(self.project_path), sha256(self.cache_path), sha256(self.baseline_path))
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual([row["characters"]["red"]["outcome"] for row in report["frames"]],
                         ["correct", "unresolved-with-candidate", "semantic-swap"])
        self.assertEqual(report["summary"]["black"]["correct"], 2)
        self.assertEqual(report["summary"]["black"]["unresolved-with-candidate"], 1)
        self.assertEqual(report["summary"]["red"]["boundary-violation"], 0)
        self.assertEqual(report["coverage_vs_correctness"]["red"], {
            "candidate_label_cases": 3,
            "covered_candidate_cases": 3,
            "correct_candidate_cases": 1,
        })
        self.assertEqual(before, (sha256(self.project_path), sha256(self.cache_path),
                                  sha256(self.baseline_path)))
        self.assertFalse(report["ground_truth_used_as_tracker_input"])
        self.assertEqual(report, score(self.root, self.baseline_path, self.labels_path))

    def test_conservative_result_and_reappearance(self) -> None:
        labels = self._labels()
        labels["frames"][0]["labels"]["red"] = {"kind": "occluded", "candidate_position": None}
        labels["frames"][1]["labels"]["black"] = {"kind": "no-valid-candidate", "candidate_position": None}
        save(self.labels_path, labels)
        save(self.baseline_path, self._baseline())
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual(report["summary"]["red"]["false-match"], 1)
        self.assertEqual(report["summary"]["black"]["false-match"], 1)
        # Reappearance is defined only over the sampled labels, not every video frame.
        self.assertEqual(report["summary"]["black"]["reacquisition-success"], 0)

    def test_scored_reappearance_is_only_after_non_candidate_truth(self) -> None:
        labels = self._labels()
        labels["frames"][0]["labels"]["red"] = {"kind": "occluded", "candidate_position": None}
        baseline = self._baseline()
        baseline["characters"]["red"]["records"][0]["observation_id"] = None
        baseline["characters"]["red"]["records"][1]["observation_id"] = "r4"
        save(self.labels_path, labels)
        save(self.baseline_path, baseline)
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual(report["summary"]["red"]["correct-conservative-unresolved"], 1)
        self.assertEqual(report["summary"]["red"]["reacquisition-success"], 1)

    def test_output_never_overwrites(self) -> None:
        output = self.root / "result.json"
        write_new(output, {"marker": 1})
        with self.assertRaises(ValueError):
            write_new(output, {"marker": 2})
        self.assertEqual(read_json_for_test(output)["marker"], 1)

    def test_version_two_anchor_exclusion_accepted_set_and_no_valid_are_scoring_only(self):
        project = read_json_for_test(self.project_path)
        project["identity_keyframes"] = [
            {"character_id": "red", "frame": 3,
             "candidate_evidence": {"observation_id": "r3"}},
            {"character_id": "black", "frame": 3,
             "candidate_evidence": {"observation_id": "b3"}},
        ]
        save(self.project_path, project)
        cache = read_json_for_test(self.cache_path)
        self.assertEqual(resolve_anchor_character_ids(
            project, cache, 3, {"Red": 1, "Black": 2}
        ), {"Red": "red", "Black": "black"})
        labels = prepare(self.root, ("red", "black"), (4, 5, 6), 1,
                         version=2, excluded_blender_frames=(4,))
        labels["semantic_identity_map"] = {"Red": "red", "Black": "black"}
        for row in labels["frames"]:
            row["labels"]["red"].update(kind="candidate", candidate_position=1)
            row["labels"]["black"].update(kind="candidate", candidate_position=2)
        labels["frames"][1]["labels"]["red"] = {
            "kind": "accepted-set", "candidate_position": None, "candidate_positions": [1, 2]
        }
        labels["frames"][1]["note"] = "Two plausible boxes"
        labels["frames"][2]["labels"]["red"] = {
            "kind": "no-valid-candidate", "candidate_position": None,
            "candidate_positions": None,
        }
        labels["frames"][2]["labels"]["black"] = {
            "kind": "no-valid-candidate", "candidate_position": None,
            "candidate_positions": None,
        }
        save(self.labels_path, labels)
        save(self.baseline_path, self._baseline())
        before = [sha256(path) for path in (self.project_path, self.cache_path, self.baseline_path)]
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual(report["frames"][0]["characters"]["red"]["outcome"],
                         "excluded-authoritative-anchor")
        self.assertEqual(report["frames"][1]["characters"]["red"]["outcome"],
                         "unresolved-with-candidate")
        self.assertEqual(report["frames"][2]["characters"]["red"]["outcome"],
                         "false-match")
        self.assertEqual(report["frames"][2]["characters"]["black"]["outcome"],
                         "correct-conservative-unresolved")
        self.assertEqual(report["joint_summary"]["independently_scored_frames"], 2)
        self.assertEqual([sha256(path) for path in
                          (self.project_path, self.cache_path, self.baseline_path)], before)

    def test_version_two_rejects_malformed_accepted_set(self):
        labels = prepare(self.root, ("red", "black"), (4, 5, 6), 1,
                         version=2, excluded_blender_frames=(4,))
        for row in labels["frames"]:
            for character, position in (("red", 1), ("black", 2)):
                row["labels"][character].update(kind="candidate", candidate_position=position)
        labels["frames"][1]["labels"]["red"] = {
            "kind": "accepted-set", "candidate_position": None, "candidate_positions": [1, 1]
        }
        with self.assertRaisesRegex(ValueError, "Accepted-set"):
            validate_labels(labels, prepare(self.root, ("red", "black"), (4, 5, 6), 1,
                                            version=2, excluded_blender_frames=(4,)))

    def test_accepted_set_credits_either_box_but_not_an_unrelated_one(self):
        labels = prepare(self.root, ("red", "black"), (4, 5, 6), 1, version=2)
        for row in labels["frames"]:
            for character, position in (("red", 1), ("black", 2)):
                row["labels"][character].update(kind="candidate", candidate_position=position)
        labels["frames"][1]["labels"]["red"] = {
            "kind": "accepted-set", "candidate_position": None, "candidate_positions": [1, 2]
        }
        baseline = self._baseline()
        baseline["characters"]["red"]["records"][1]["observation_id"] = "b4"
        save(self.labels_path, labels)
        save(self.baseline_path, baseline)
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual(report["frames"][1]["characters"]["red"]["outcome"],
                         "correct-accepted-set")
        self.assertEqual(report["coverage_vs_correctness"]["red"]["correct_candidate_cases"], 2)

    def test_frame_17_inclusion_and_three_no_valid_frames_are_not_tracker_inputs(self):
        cache = read_json_for_test(self.cache_path)
        cache["frames"] = [
            {"frame": frame, "state": "analyzed", "timestamp_seconds": frame / 24,
             "candidates": [{"observation_id": f"o{frame}-{position}",
                             "box_xyxy": [position, 1, position + 1, 2]}
                            for position in range(1, 9)]}
            for frame in (0, 16, 66, 72, 80, 140)
        ]
        save(self.cache_path, cache)
        labels = prepare(self.root, ("red", "black"), (1, 17, 67, 73, 81, 141), 1,
                         version=2)
        for row in labels["frames"]:
            for character, position in (("red", 1), ("black", 2)):
                row["labels"][character].update(kind="candidate", candidate_position=position)
        for row in labels["frames"]:
            if row["blender_frame"] in (67, 73, 81):
                for character in ("red", "black"):
                    row["labels"][character] = {
                        "kind": "no-valid-candidate", "candidate_position": None,
                        "candidate_positions": None,
                    }
            if row["blender_frame"] == 141:
                row["labels"]["red"] = {
                    "kind": "accepted-set", "candidate_position": None,
                    "candidate_positions": [2, 5],
                }
        save(self.labels_path, labels)
        baseline = self._baseline()
        for character in ("red", "black"):
            baseline["characters"][character]["scope"] = {"start_frame": 0, "end_frame": 140}
            baseline["characters"][character]["records"] = [
                {"frame": frame, "state": "unresolved", "observation_id": None}
                for frame in (0, 16, 66, 72, 80, 140)
            ]
        save(self.baseline_path, baseline)
        before = self.project_path.read_bytes()
        report = score(self.root, self.baseline_path, self.labels_path)
        self.assertEqual([row["blender_frame"] for row in report["frames"]],
                         [1, 17, 67, 73, 81, 141])
        for frame in (67, 73, 81):
            row = next(row for row in report["frames"] if row["blender_frame"] == frame)
            self.assertEqual([row["characters"][character]["outcome"]
                              for character in ("red", "black")],
                             ["correct-conservative-unresolved"] * 2)
        self.assertEqual(self.project_path.read_bytes(), before)


def read_json_for_test(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
