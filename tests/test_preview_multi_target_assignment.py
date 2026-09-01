"""Focused tests for multi-target appearance assignment diagnostics."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import diagnose_preview_multi_target_assignment as diagnostic


SIDECAR_HASH = "1" * 64
WEIGHTS_HASH = "2" * 64
DEFAULT_ANCHOR_HASH = "3" * 64
ANCHOR_A_HASH = "a" * 64
ANCHOR_B_HASH = "b" * 64
ANCHOR_C_HASH = "c" * 64


def make_report(
    score_vectors,
    *,
    boxes=None,
    sidecar_hash=SIDECAR_HASH,
    anchor_hash=DEFAULT_ANCHOR_HASH,
    reference_frames=(0, 2),
    frame=1,
    stored_label=None,
    fps=30.0,
    sampling_interval=1,
):
    boxes = boxes or [[0.0, 0.0, 10.0, 20.0], [20.0, 0.0, 30.0, 20.0]]
    methods = (
        f"similarity_to_anchor_{reference_frames[0]}",
        f"similarity_to_anchor_{reference_frames[1]}",
        "endpoint_mean_score",
        "full_gallery_score",
    )
    candidates = []
    for temporary_index, box in enumerate(boxes):
        candidates.append(
            {
                "temporary_candidate_index": temporary_index,
                "box_xyxy": list(box),
                "scores": {
                    method: score_vectors.get(method, score_vectors["default"])[
                        temporary_index
                    ]
                    for method in methods
                },
            }
        )
    rankings = {}
    for method in methods:
        values = score_vectors.get(method, score_vectors["default"])
        non_null = [
            (value, index)
            for index, value in enumerate(values)
            if value is not None
        ]
        non_null.sort(key=lambda item: (-item[0], item[1]))
        rankings[method] = {
            "temporary_candidate_indexes": [item[1] for item in non_null],
            "top_temporary_candidate_index": (
                non_null[0][1] if non_null else None
            ),
        }
    frame_record = {
        "frame": frame,
        "timestamp_seconds": frame / 30.0,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "rankings": rankings,
    }
    if stored_label is not None:
        frame_record["human_confirmed_label"] = {
            "temporary_candidate_index": stored_label,
            "ranks": {},
        }
    view = {"frames": [frame_record]}
    return {
        "schema": diagnostic.APPEARANCE_SCHEMA,
        "version": diagnostic.APPEARANCE_VERSION,
        "source": {
            "sidecar_sha256": sidecar_hash,
            "anchors_sha256": anchor_hash,
            "target_track_sha256": None,
            "weights_sha256": WEIGHTS_HASH,
            "fps": fps,
            "sampling_interval": sampling_interval,
        },
        "model": {
            "architecture": "osnet_ain_x1_0",
            "descriptor_dimensions": 512,
            "preprocessing": {
                "color": "RGB",
                "size_height_width": [256, 128],
                "normalization_mean": [0.485, 0.456, 0.406],
                "normalization_std": [0.229, 0.224, 0.225],
            },
            "similarity": "cosine of L2-normalized descriptors",
        },
        "experiment": {
            "reference_anchor_frames": list(reference_frames),
            "evaluation_frames": [frame],
            "human_confirmed_labels": [],
        },
        "views": {
            "whole_box": json.loads(json.dumps(view)),
            "inner_box": json.loads(json.dumps(view)),
        },
    }


def build(reports, labels=None, frame_range=None):
    return diagnostic.build_diagnostic(
        reports,
        {
            target_id: format(index + 10, "064x")
            for index, target_id in enumerate(sorted(reports))
        },
        frame_range=frame_range,
        labels=labels,
    )


def combine_report_frames(*reports):
    combined = json.loads(json.dumps(reports[0]))
    for view_name in combined["views"]:
        combined["views"][view_name]["frames"] = [
            json.loads(json.dumps(report["views"][view_name]["frames"][0]))
            for report in reports
        ]
    combined["experiment"]["evaluation_frames"] = [
        report["views"]["whole_box"]["frames"][0]["frame"]
        for report in reports
    ]
    return combined


def frame_result(output, method="endpoint_mean_score", view="whole_box"):
    return output["views"][view]["methods"][method]["frames"][0]


def assigned_indexes(result):
    return {
        item["target_id"]: item["temporary_candidate_index"]
        for item in result["best_assignment"]
    }


def recursive_keys(value):
    keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(recursive_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(recursive_keys(child))
    return keys


class AssignmentTests(unittest.TestCase):
    def test_correct_two_target_assignment(self):
        output = build(
            {
                "red": make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH),
                "black": make_report(
                    {"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH
                ),
            }
        )
        result = frame_result(output)
        self.assertEqual(assigned_indexes(result), {"black": 1, "red": 0})
        self.assertTrue(result["evaluable"])

    def test_repairs_independent_same_candidate_collision(self):
        reports = {
            "yellow": make_report(
                {"default": [0.9, 0.8]}, anchor_hash=ANCHOR_A_HASH
            ),
            "white": make_report(
                {"default": [0.95, 0.1]}, anchor_hash=ANCHOR_B_HASH
            ),
        }
        labels = {"yellow": {1: 1}, "white": {1: 0}}
        result = frame_result(build(reports, labels))
        self.assertTrue(result["independent_collisions"])
        self.assertEqual(assigned_indexes(result), {"white": 0, "yellow": 1})
        self.assertTrue(
            result["ground_truth_evaluation"]["correct_collision_repair"]
        )

    def test_wrong_joint_assignment_is_honestly_reported(self):
        reports = {
            "a": make_report({"default": [0.9, 0.8]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.95, 0.1]}, anchor_hash=ANCHOR_B_HASH),
        }
        labels = {"a": {1: 0}, "b": {1: 1}}
        result = frame_result(build(reports, labels))
        evaluation = result["ground_truth_evaluation"]
        self.assertFalse(evaluation["simultaneous_all_target_correct"])
        self.assertTrue(evaluation["incorrect_forced_assignment"])

    def test_candidate_order_invariance(self):
        boxes = [[0.0, 0.0, 10.0, 20.0], [20.0, 0.0, 30.0, 20.0]]
        original = {
            "a": make_report({"default": [0.9, 0.2]}, boxes=boxes, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.8]}, boxes=boxes, anchor_hash=ANCHOR_B_HASH),
        }
        reordered_boxes = [boxes[1], boxes[0]]
        reordered = {
            "a": make_report(
                {"default": [0.2, 0.9]}, boxes=reordered_boxes, anchor_hash=ANCHOR_A_HASH
            ),
            "b": make_report(
                {"default": [0.8, 0.1]}, boxes=reordered_boxes, anchor_hash=ANCHOR_B_HASH
            ),
        }
        first = assigned_indexes(frame_result(build(original)))
        second = assigned_indexes(frame_result(build(reordered)))
        first_boxes = {target: boxes[index] for target, index in first.items()}
        second_boxes = {
            target: reordered_boxes[index] for target, index in second.items()
        }
        self.assertEqual(first_boxes, second_boxes)

    def test_target_order_invariance(self):
        a = make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH)
        b = make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH)
        first = frame_result(build({"a": a, "b": b}))
        second = frame_result(build({"b": b, "a": a}))
        self.assertEqual(first, second)

    def test_three_target_assignment(self):
        boxes = [
            [0.0, 0.0, 10.0, 20.0],
            [20.0, 0.0, 30.0, 20.0],
            [40.0, 0.0, 50.0, 20.0],
        ]
        reports = {
            "a": make_report({"default": [0.9, 0.2, 0.1]}, boxes=boxes, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.2, 0.9, 0.1]}, boxes=boxes, anchor_hash=ANCHOR_B_HASH),
            "c": make_report({"default": [0.2, 0.1, 0.9]}, boxes=boxes, anchor_hash=ANCHOR_C_HASH),
        }
        self.assertEqual(
            assigned_indexes(frame_result(build(reports))),
            {"a": 0, "b": 1, "c": 2},
        )

    def test_insufficient_candidates_is_unevaluable(self):
        reports = {
            target: make_report(
                {"default": [0.9, 0.2]},
                anchor_hash={"a": ANCHOR_A_HASH, "b": ANCHOR_B_HASH, "c": ANCHOR_C_HASH}[target],
            )
            for target in ("a", "b", "c")
        }
        result = frame_result(build(reports))
        self.assertFalse(result["evaluable"])
        self.assertEqual(result["unevaluable_reason"], "insufficient-candidates")

    def test_null_scores_are_unevaluable(self):
        reports = {
            "a": make_report({"default": [0.9, None]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH),
        }
        result = frame_result(build(reports))
        self.assertFalse(result["evaluable"])
        self.assertEqual(result["unevaluable_reason"], "null-scores")

    def test_exact_assignment_ties_are_preserved(self):
        reports = {
            "a": make_report({"default": [1.0, 1.0]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [1.0, 1.0]}, anchor_hash=ANCHOR_B_HASH),
        }
        result = frame_result(build(reports))
        self.assertTrue(result["is_exact_assignment_tie"])
        self.assertEqual(len(result["tied_best_assignments"]), 2)
        self.assertEqual(result["best_minus_runner_up_margin"], 0.0)

    def test_runner_up_and_margin_calculation(self):
        reports = {
            "a": make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH),
        }
        result = frame_result(build(reports))
        self.assertEqual(result["best_score"], 0.9 + 0.8)
        self.assertEqual(result["runner_up_score"], 0.2 + 0.1)
        self.assertEqual(
            result["best_minus_runner_up_margin"],
            (0.9 + 0.8) - (0.2 + 0.1),
        )

    def test_labels_do_not_change_assignment_scores_or_margins(self):
        reports = {
            "a": make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH),
        }
        without_labels = frame_result(build(reports))
        with_labels = frame_result(build(reports, {"a": {1: 1}, "b": {1: 0}}))
        fields = (
            "score_matrix",
            "independent_top_choices",
            "independent_collisions",
            "best_assignment",
            "best_score",
            "runner_up_assignment",
            "runner_up_score",
            "best_minus_runner_up_margin",
            "is_exact_assignment_tie",
            "tied_best_assignments",
        )
        for field in fields:
            self.assertEqual(without_labels[field], with_labels[field], field)

    def test_exact_tie_ground_truth_is_ambiguous_and_uncredited(self):
        reports = {
            "a": make_report({"default": [1.0, 1.0]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [1.0, 1.0]}, anchor_hash=ANCHOR_B_HASH),
        }
        output = build(reports, {"a": {1: 0}, "b": {1: 1}})
        evaluation = frame_result(output)["ground_truth_evaluation"]
        self.assertTrue(evaluation["ambiguous_exact_tie"])
        self.assertEqual(
            evaluation["tied_best_assignments_ground_truth_match"], "some"
        )
        self.assertTrue(all(item["correct"] is None for item in evaluation["per_target"]))
        summary = output["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]
        self.assertEqual(summary["ground_truth"]["exact_tie_ambiguous_frames"], 1)
        self.assertEqual(summary["ground_truth"]["frames_with_complete_labels"], 0)
        self.assertEqual(summary["ground_truth"]["per_target"]["a"]["evaluated_frames"], 0)
        self.assertEqual(summary["ground_truth"]["per_target"]["b"]["evaluated_frames"], 0)

    def test_tied_candidate_reordering_does_not_change_evaluation_summary(self):
        boxes = [[0.0, 0.0, 10.0, 20.0], [20.0, 0.0, 30.0, 20.0]]
        original = {
            "a": make_report({"default": [1.0, 1.0]}, boxes=boxes, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [1.0, 1.0]}, boxes=boxes, anchor_hash=ANCHOR_B_HASH),
        }
        reordered = {
            "a": make_report({"default": [1.0, 1.0]}, boxes=list(reversed(boxes)), anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [1.0, 1.0]}, boxes=list(reversed(boxes)), anchor_hash=ANCHOR_B_HASH),
        }
        first = build(original, {"a": {1: 0}, "b": {1: 1}})
        second = build(reordered, {"a": {1: 1}, "b": {1: 0}})
        first_summary = first["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]
        second_summary = second["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]
        self.assertEqual(first_summary, second_summary)

    def test_partial_labels_use_per_target_denominators(self):
        reports = {
            "a": make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH),
        }
        output = build(reports, {"a": {1: 0}})
        evaluation = frame_result(output)["ground_truth_evaluation"]
        per_target = {item["target_id"]: item for item in evaluation["per_target"]}
        self.assertTrue(per_target["a"]["correct"])
        self.assertIsNone(per_target["b"]["correct"])
        self.assertEqual(evaluation["missing_target_ids"], ["b"])
        self.assertIsNone(evaluation["simultaneous_all_target_correct"])
        self.assertIsNone(evaluation["incorrect_forced_assignment"])
        summary = output["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]["ground_truth"]
        self.assertEqual(summary["per_target"]["a"], {"correct_frames": 1, "evaluated_frames": 1})
        self.assertEqual(summary["per_target"]["b"], {"correct_frames": 0, "evaluated_frames": 0})
        self.assertEqual(summary["frames_with_complete_labels"], 0)

    def test_already_wrong_target_is_not_an_incorrect_forced_assignment(self):
        boxes = [
            [0.0, 0.0, 10.0, 20.0],
            [20.0, 0.0, 30.0, 20.0],
            [40.0, 0.0, 50.0, 20.0],
        ]
        reports = {
            "a": make_report({"default": [0.9, 0.8, 0.1]}, boxes=boxes, anchor_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.95, 0.7, 0.2]}, boxes=boxes, anchor_hash=ANCHOR_B_HASH),
        }
        result = frame_result(build(reports, {"a": {1: 2}, "b": {1: 0}}))
        evaluation = result["ground_truth_evaluation"]
        self.assertTrue(result["independent_collisions"])
        self.assertFalse(evaluation["simultaneous_all_target_correct"])
        self.assertEqual(evaluation["incorrect_forced_target_ids"], [])
        self.assertFalse(evaluation["incorrect_forced_assignment"])

    def test_self_reference_null_only_makes_that_method_unevaluable(self):
        vectors_a = {
            "default": [0.9, 0.2],
            "similarity_to_anchor_0": [None, None],
        }
        vectors_b = {
            "default": [0.1, 0.8],
            "similarity_to_anchor_0": [None, None],
        }
        output = build(
            {
                "a": make_report(vectors_a, anchor_hash=ANCHOR_A_HASH),
                "b": make_report(vectors_b, anchor_hash=ANCHOR_B_HASH),
            }
        )
        anchor_result = frame_result(output, method="similarity_to_anchor_0")
        endpoint_result = frame_result(output, method="endpoint_mean_score")
        self.assertFalse(anchor_result["evaluable"])
        self.assertEqual(anchor_result["unevaluable_reason"], "null-scores")
        self.assertTrue(endpoint_result["evaluable"])

    def test_assignment_enumeration_limit_reports_calculated_count(self):
        boxes = [
            [float(index * 20), 0.0, float(index * 20 + 10), 20.0]
            for index in range(10)
        ]
        reports = {
            f"target-{index}": make_report(
                {"default": [1.0 - candidate / 100.0 for candidate in range(10)]},
                boxes=boxes,
                anchor_hash=format(index + 20, "064x"),
            )
            for index in range(8)
        }
        result = frame_result(build(reports))
        self.assertFalse(result["evaluable"])
        self.assertEqual(result["unevaluable_reason"], "assignment-enumeration-limit")
        self.assertEqual(result["calculated_permutation_count"], 1_814_400)

    def test_multi_frame_margin_summary_excludes_unevaluable_frame(self):
        a = combine_report_frames(
            make_report({"default": [0.9, 0.1]}, frame=1, anchor_hash=ANCHOR_A_HASH),
            make_report({"default": [0.7, 0.2]}, frame=2, anchor_hash=ANCHOR_A_HASH),
            make_report({"default": [0.6, None]}, frame=3, anchor_hash=ANCHOR_A_HASH),
        )
        b = combine_report_frames(
            make_report({"default": [0.1, 0.8]}, frame=1, anchor_hash=ANCHOR_B_HASH),
            make_report({"default": [0.1, 0.6]}, frame=2, anchor_hash=ANCHOR_B_HASH),
            make_report({"default": [0.1, 0.5]}, frame=3, anchor_hash=ANCHOR_B_HASH),
        )
        summary = build({"a": a, "b": b})["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]
        self.assertEqual(summary["total_frames"], 3)
        self.assertEqual(summary["evaluable_frames"], 2)
        self.assertEqual(summary["unevaluable_frames"], 1)
        self.assertAlmostEqual(summary["minimum_assignment_margin"], 1.0)
        self.assertAlmostEqual(summary["median_assignment_margin"], 1.25)


class ValidationTests(unittest.TestCase):
    def test_at_least_two_unique_targets_are_required(self):
        with self.assertRaisesRegex(diagnostic.AssignmentError, "At least two"):
            diagnostic.parse_target_specs(["one=report.json"])
        with self.assertRaisesRegex(diagnostic.AssignmentError, "Duplicate"):
            diagnostic.parse_target_specs(["one=a.json", "one=b.json"])

    def test_mismatched_sidecars_are_rejected(self):
        reports = {
            "a": make_report({"default": [0.9, 0.1]}, sidecar_hash=ANCHOR_A_HASH),
            "b": make_report({"default": [0.1, 0.9]}, sidecar_hash=ANCHOR_B_HASH),
        }
        with self.assertRaisesRegex(diagnostic.AssignmentError, "sidecar"):
            build(reports)

    def test_mismatched_candidate_boxes_are_rejected(self):
        reports = {
            "a": make_report({"default": [0.9, 0.1]}),
            "b": make_report(
                {"default": [0.1, 0.9]},
                boxes=[[0.0, 0.0, 9.0, 20.0], [20.0, 0.0, 30.0, 20.0]],
            ),
        }
        with self.assertRaisesRegex(diagnostic.AssignmentError, "candidate boxes"):
            build(reports)

    def test_mismatched_reference_frames_are_rejected(self):
        reports = {
            "a": make_report({"default": [0.9, 0.1]}, reference_frames=(0, 2)),
            "b": make_report({"default": [0.1, 0.9]}, reference_frames=(0, 3)),
        }
        with self.assertRaisesRegex(diagnostic.AssignmentError, "reference frames"):
            build(reports)

    def test_unequal_gallery_composition_is_exploratory_and_warned(self):
        output = build(
            {
                "a": make_report({"default": [0.9, 0.1]}, anchor_hash=ANCHOR_A_HASH),
                "b": make_report({"default": [0.1, 0.9]}, anchor_hash=ANCHOR_B_HASH),
            }
        )
        self.assertNotIn(
            diagnostic.FULL_GALLERY_METHOD, output["evidence_methods"]["primary"]
        )
        exploratory = output["evidence_methods"]["exploratory"][0]
        self.assertFalse(exploratory["comparable_gallery_composition"])
        self.assertIn("non-comparable", exploratory["warning"])
        self.assertTrue(
            any("non-comparable" in warning for warning in output["warnings"])
        )

    def test_mismatched_frame_coverage_reports_exact_missing_frames(self):
        a = combine_report_frames(
            make_report({"default": [0.9, 0.1]}, frame=1),
            make_report({"default": [0.9, 0.1]}, frame=2),
        )
        b = make_report({"default": [0.1, 0.9]}, frame=1)
        with self.assertRaisesRegex(
            diagnostic.AssignmentError, r"missing frames by target: b=\[2\]"
        ):
            build({"a": a, "b": b})

        a_with_outside_frame = combine_report_frames(
            make_report({"default": [0.9, 0.1]}, frame=1),
            make_report({"default": [0.9, 0.1]}, frame=2),
            make_report({"default": [0.9, 0.1]}, frame=9),
        )
        b_with_outside_frame = combine_report_frames(
            make_report({"default": [0.1, 0.9]}, frame=1),
            make_report({"default": [0.1, 0.9]}, frame=9),
        )
        with self.assertRaisesRegex(
            diagnostic.AssignmentError, r"missing frames by target: b=\[2\]"
        ):
            build(
                {"a": a_with_outside_frame, "b": b_with_outside_frame},
                frame_range=(1, 2),
            )

    def test_missing_architecture_and_preprocessing_are_rejected_clearly(self):
        for field in ("architecture", "preprocessing"):
            with self.subTest(field=field):
                a = make_report({"default": [0.9, 0.1]})
                b = make_report({"default": [0.1, 0.9]})
                del a["model"][field]
                with self.assertRaisesRegex(
                    diagnostic.AssignmentError, f"model\\.{field}"
                ):
                    build({"a": a, "b": b})

    def test_invalid_fps_interval_and_sha256_are_rejected(self):
        cases = []
        invalid_fps = make_report({"default": [0.9, 0.1]}, fps=0.0)
        cases.append((invalid_fps, r"source\.fps must be positive"))
        invalid_interval = make_report(
            {"default": [0.9, 0.1]}, sampling_interval=0
        )
        cases.append(
            (invalid_interval, r"source\.sampling_interval must be positive")
        )
        invalid_hash = make_report({"default": [0.9, 0.1]})
        invalid_hash["source"]["weights_sha256"] = "not-a-sha256"
        cases.append((invalid_hash, r"source\.weights_sha256"))
        for invalid_report, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(diagnostic.AssignmentError, message):
                    build(
                        {
                            "a": invalid_report,
                            "b": make_report({"default": [0.1, 0.9]}),
                        }
                    )

    def test_missing_rankings_are_rejected_clearly(self):
        a = make_report({"default": [0.9, 0.1]})
        del a["views"]["whole_box"]["frames"][0]["rankings"]
        with self.assertRaisesRegex(diagnostic.AssignmentError, r"\.rankings"):
            build({"a": a, "b": make_report({"default": [0.1, 0.9]})})

    def test_ground_truth_evaluation_and_missing_labels_are_reported(self):
        reports = {
            "a": make_report({"default": [0.9, 0.2]}, anchor_hash=ANCHOR_A_HASH, stored_label=0),
            "b": make_report({"default": [0.1, 0.8]}, anchor_hash=ANCHOR_B_HASH),
        }
        output = build(reports, {"a": {1: 0}, "b": {1: 1}})
        summary = output["views"]["whole_box"]["methods"][
            "endpoint_mean_score"
        ]["evaluation_summary"]
        self.assertEqual(summary["ground_truth"]["simultaneous_all_target_correct_frames"], 1)
        self.assertIn(
            {"target_id": "b", "frame": 1},
            output["ground_truth"]["appearance_report_labels_missing"],
        )

    def test_safe_output_path_and_input_collision_handling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "first.json"
            second = root / "second.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            targets = {"a": first, "b": second}
            with mock.patch.object(diagnostic, "OUTPUT_ROOT", root):
                diagnostic.validate_paths(targets, root / "output.json")
                with self.assertRaisesRegex(diagnostic.AssignmentError, "overwrite"):
                    diagnostic.validate_paths(targets, first)
                with self.assertRaisesRegex(diagnostic.AssignmentError, "beneath"):
                    diagnostic.validate_paths(targets, root.parent / "outside.json")

    def test_output_has_no_embeddings_crops_tracker_states_or_persistent_identity(self):
        output = build(
            {
                "a": make_report({"default": [0.9, 0.1]}, anchor_hash=ANCHOR_A_HASH),
                "b": make_report({"default": [0.1, 0.9]}, anchor_hash=ANCHOR_B_HASH),
            }
        )
        forbidden = {
            "embedding",
            "embeddings",
            "crop",
            "crop_image",
            "track_state",
            "tracker_state",
            "persistent_identity",
            "source_video",
        }
        self.assertTrue(forbidden.isdisjoint(recursive_keys(output)))
        self.assertEqual(output["schema"], diagnostic.SCHEMA)
        self.assertEqual(output["version"], 1)


if __name__ == "__main__":
    unittest.main()
