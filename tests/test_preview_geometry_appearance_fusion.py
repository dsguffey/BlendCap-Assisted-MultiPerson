"""Focused tests for diagnostic geometry-plus-appearance fusion."""

import copy
import contextlib
import hashlib
import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import diagnose_preview_geometry_appearance_fusion as fusion
import diagnose_preview_multi_target_assignment as joint_tool
import track_preview_target as tracker


WEIGHTS_HASH = "8" * 64


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def geometry_validation_case(
    anchor_frames=(2, 4), forced_ranges=None, final_frame=8
):
    frames = list(range(final_frame + 1))
    boxes = [[0.0, 0.0, 10.0, 20.0], [20.0, 0.0, 30.0, 20.0]]
    sidecar_raw = {
        "version": 2,
        "fps": 10.0,
        "sample_indices": frames,
        "boxes": [list(boxes[0]) for _ in frames],
        "all_boxes": [[list(box) for box in boxes] for _ in frames],
    }
    sidecar = tracker.validate_sidecar(sidecar_raw, "a" * 64)
    anchors = []
    for index, frame in enumerate(anchor_frames):
        box = boxes[0]
        anchors.append(
            {
                "anchor_id": f"anchor-{index}",
                "frame": frame,
                "clicked_point_xy": [5.0, 10.0],
                "box_snapshot_xyxy": list(box),
            }
        )
    anchor_doc = {
        "schema": "blendcap-preview-target-anchors",
        "version": 1,
        "track_id": "interval-target",
        "coordinate_space": "source-pixels-xyxy",
        "source": {
            "sidecar_sha256": sidecar["sha256"],
            "fps": sidecar["fps"],
            "sampling_interval": sidecar["sampling_interval"],
        },
        "anchors": anchors,
        "forced_occlusion_ranges": list(forced_ranges or []),
    }
    validated = tracker.validate_anchors(anchor_doc, sidecar)
    return sidecar, validated, tracker.build_track(sidecar, anchor_doc)


def set_geometry_record(track, sidecar, frame, state):
    record = next(item for item in track["frames"] if item["frame"] == frame)

    def hypothesis(candidate_index):
        position = sidecar["frame_to_position"][frame]
        return {
            "box_xyxy": list(sidecar["all_boxes"][position][candidate_index]),
            "temporary_candidate_index": candidate_index,
            "score": 0.8,
            "iou": 0.7,
            "center_displacement_diagonals": 0.1,
            "area_fold_change": 1.1,
        }

    if state == "tracked":
        selected = hypothesis(0)
        record.update(
            {
                "state": "tracked",
                "box_xyxy": list(selected["box_xyxy"]),
                "temporary_candidate_index": 0,
                "evidence": "bidirectional",
                "reason": None,
                "hypotheses": {
                    "forward": copy.deepcopy(selected),
                    "backward": copy.deepcopy(selected),
                },
            }
        )
    elif state == "ambiguous":
        record.update(
            {
                "state": "ambiguous",
                "box_xyxy": None,
                "temporary_candidate_index": None,
                "evidence": None,
                "reason": "forward-backward-disagreement",
                "hypotheses": {
                    "forward": hypothesis(0),
                    "backward": hypothesis(1),
                },
            }
        )
    else:
        raise AssertionError(state)


def rename_reference_method(report, old_frame, new_frame):
    old_name = f"similarity_to_anchor_{old_frame}"
    new_name = f"similarity_to_anchor_{new_frame}"
    report["experiment"]["reference_anchor_frames"] = [0, new_frame]
    for view in report["views"].values():
        for frame in view["frames"]:
            frame["rankings"][new_name] = frame["rankings"].pop(old_name)
            for candidate in frame["candidates"]:
                candidate["scores"][new_name] = candidate["scores"].pop(old_name)


def ranking(values):
    scored = [(value, index) for index, value in enumerate(values) if value is not None]
    scored.sort(key=lambda item: (-item[0], item[1]))
    indexes = [index for _, index in scored]
    return {
        "temporary_candidate_indexes": indexes,
        "top_temporary_candidate_index": indexes[0] if indexes else None,
        "top_two_margin": (
            scored[0][0] - scored[1][0] if len(scored) >= 2 else None
        ),
    }


def make_appearance_report(
    sidecar,
    sidecar_hash,
    anchor_hash,
    evaluation_frames,
    references,
    target_index,
):
    methods = (
        f"similarity_to_anchor_{references[0]}",
        f"similarity_to_anchor_{references[1]}",
        "endpoint_mean_score",
        "full_gallery_score",
    )
    views = {}
    for view_name in ("whole_box", "inner_box"):
        frames = []
        for frame in evaluation_frames:
            position = sidecar["sample_indices"].index(frame)
            boxes = sidecar["all_boxes"][position]
            default = (
                [0.9, 0.4, 0.2]
                if target_index == 0
                else [0.3, 0.85, 0.2]
            )
            candidates = []
            candidate_values = []
            for candidate_index, box in enumerate(boxes):
                physical_index = int(round(box[0] / 20.0))
                value = default[physical_index]
                candidate_values.append(value)
                candidates.append(
                    {
                        "temporary_candidate_index": candidate_index,
                        "box_xyxy": list(box),
                        "scores": {
                            method: value for method in methods
                        },
                    }
                )
            frames.append(
                {
                    "frame": frame,
                    "timestamp_seconds": frame / sidecar["fps"],
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "rankings": {
                        method: ranking(candidate_values)
                        for method in methods
                    },
                }
            )
        views[view_name] = {"frames": copy.deepcopy(frames)}
    return {
        "schema": joint_tool.APPEARANCE_SCHEMA,
        "version": joint_tool.APPEARANCE_VERSION,
        "source": {
            "sidecar_sha256": sidecar_hash,
            "anchors_sha256": anchor_hash,
            "target_track_sha256": None,
            "weights_sha256": WEIGHTS_HASH,
            "fps": sidecar["fps"],
            "sampling_interval": sidecar["sample_indices"][1]
            - sidecar["sample_indices"][0],
        },
        "model": {
            "architecture": "osnet_ain_x1_0",
            "preprocessing": {
                "color": "RGB",
                "size_height_width": [256, 128],
                "normalization_mean": [0.485, 0.456, 0.406],
                "normalization_std": [0.229, 0.224, 0.225],
            },
        },
        "experiment": {
            "reference_anchor_frames": list(references),
            "evaluation_frames": list(evaluation_frames),
        },
        "views": views,
    }


class Bundle:
    def __init__(
        self,
        directory,
        interval=1,
        joint_frames=None,
        forced=False,
        forced_single=False,
        insufficient=False,
        reorder_candidates=False,
    ):
        self.root = Path(directory)
        frames = [0, interval, interval * 2, interval * 3]
        boxes = [
            [0.0, 0.0, 10.0, 20.0],
            [20.0, 0.0, 30.0, 20.0],
            [40.0, 0.0, 50.0, 20.0],
        ]
        all_boxes = [[list(box) for box in boxes] for _ in frames]
        if reorder_candidates:
            all_boxes[1] = [list(boxes[2]), list(boxes[1]), list(boxes[0])]
        if insufficient:
            all_boxes[1] = [list(boxes[0])]
        self.sidecar_raw = {
            "version": 2,
            "fps": 10.0,
            "sample_indices": frames,
            "boxes": [list(boxes[0]) for _ in frames],
            "all_boxes": all_boxes,
        }
        self.sidecar_path = self.root / "sidecar.json"
        write_json(self.sidecar_path, self.sidecar_raw)
        sidecar_hash = file_hash(self.sidecar_path)
        self.sidecar = tracker.validate_sidecar(self.sidecar_raw, sidecar_hash)
        self.anchor_paths = {}
        self.track_paths = {}
        self.appearance_paths = {}
        self.anchor_docs = {}
        self.track_docs = {}
        self.appearance_docs = {}
        anchor_hashes = {}
        track_hashes = {}
        for target_index, target_id in enumerate(("a", "b")):
            box = boxes[target_index]
            ranges = (
                [
                    {
                        "start_frame": frames[1],
                        "end_frame": frames[1] if forced_single else frames[2],
                    }
                ]
                if forced or forced_single
                else []
            )
            anchor_doc = {
                "schema": "blendcap-preview-target-anchors",
                "version": 1,
                "track_id": f"target-{target_id}",
                "coordinate_space": "source-pixels-xyxy",
                "source": {
                    "sidecar_sha256": sidecar_hash,
                    "fps": self.sidecar["fps"],
                    "sampling_interval": interval,
                },
                "anchors": [
                    {
                        "anchor_id": f"{target_id}-start",
                        "frame": frames[0],
                        "clicked_point_xy": [
                            (box[0] + box[2]) / 2,
                            (box[1] + box[3]) / 2,
                        ],
                        "box_snapshot_xyxy": list(box),
                    },
                    {
                        "anchor_id": f"{target_id}-end",
                        "frame": frames[-1],
                        "clicked_point_xy": [
                            (box[0] + box[2]) / 2,
                            (box[1] + box[3]) / 2,
                        ],
                        "box_snapshot_xyxy": list(box),
                    },
                ],
                "forced_occlusion_ranges": ranges,
            }
            anchor_path = self.root / f"{target_id}-anchors.json"
            write_json(anchor_path, anchor_doc)
            validated = tracker.validate_anchors(anchor_doc, self.sidecar)
            track_doc = tracker.build_track(self.sidecar, anchor_doc)
            track_path = self.root / f"{target_id}-track.json"
            write_json(track_path, track_doc)
            self.anchor_paths[target_id] = anchor_path
            self.track_paths[target_id] = track_path
            self.anchor_docs[target_id] = anchor_doc
            self.track_docs[target_id] = track_doc
            anchor_hashes[target_id] = file_hash(anchor_path)
            track_hashes[target_id] = file_hash(track_path)
            self.asserted_validated = validated
        evaluation_frames = joint_frames or frames[1:3]
        references = (frames[0], frames[-1])
        report_hashes = {}
        for target_index, target_id in enumerate(("a", "b")):
            report = make_appearance_report(
                self.sidecar_raw,
                sidecar_hash,
                anchor_hashes[target_id],
                evaluation_frames,
                references,
                target_index,
            )
            path = self.root / f"{target_id}-appearance.json"
            write_json(path, report)
            self.appearance_paths[target_id] = path
            self.appearance_docs[target_id] = report
            report_hashes[target_id] = file_hash(path)
        joint = joint_tool.build_diagnostic(
            self.appearance_docs, report_hashes, labels={}
        )
        self.joint_path = self.root / "joint.json"
        write_json(self.joint_path, joint)
        self.joint_doc = joint
        self.output_path = self.root / "output.json"

    def load(self):
        return fusion.load_and_validate(
            self.sidecar_path,
            self.joint_path,
            self.anchor_paths,
            self.track_paths,
            self.appearance_paths,
        )

    def write_joint(self):
        write_json(self.joint_path, self.joint_doc)

    def write_appearance_and_refresh_joint_hash(self, target_id):
        write_json(
            self.appearance_paths[target_id], self.appearance_docs[target_id]
        )
        target = next(
            item
            for item in self.joint_doc["provenance"]["targets"]
            if item["target_id"] == target_id
        )
        target["appearance_report_sha256"] = file_hash(
            self.appearance_paths[target_id]
        )
        self.write_joint()

    def rebuild_joint(self):
        report_hashes = {
            target_id: file_hash(path)
            for target_id, path in self.appearance_paths.items()
        }
        self.joint_doc = joint_tool.build_diagnostic(
            self.appearance_docs, report_hashes, labels={}
        )
        self.write_joint()


def serialized_joint_frame(bundle, view="whole_box", method="endpoint_mean_score", frame=1):
    return next(
        item
        for item in bundle.joint_doc["views"][view]["methods"][method]["frames"]
        if item["frame"] == frame
    )


def make_serialized_tie_bundle(bundle, view="whole_box", method="endpoint_mean_score"):
    for report in bundle.appearance_docs.values():
        frame = next(
            item for item in report["views"][view]["frames"] if item["frame"] == 1
        )
        values = [1.0, 1.0, 0.0]
        for candidate, value in zip(frame["candidates"], values):
            candidate["scores"][method] = value
        frame["rankings"][method] = ranking(values)
    for target_id in bundle.appearance_docs:
        write_json(bundle.appearance_paths[target_id], bundle.appearance_docs[target_id])
    bundle.rebuild_joint()


def target_result(report, frame, target_id):
    frame_record = next(item for item in report["frames"] if item["frame"] == frame)
    return next(item for item in frame_record["targets"] if item["target_id"] == target_id)


def make_ambiguous(context, target_id, frame, forward=0, backward=2):
    record = next(
        item for item in context["tracks"][target_id]["frames"] if item["frame"] == frame
    )
    candidates = context["sidecar"]["all_boxes"][
        context["sidecar"]["frame_to_position"][frame]
    ]
    def hypothesis(index):
        return {
            "box_xyxy": list(candidates[index]),
            "temporary_candidate_index": index,
            "score": 0.8,
            "iou": 0.7,
            "center_displacement_diagonals": 0.1,
            "area_fold_change": 1.1,
        }
    record.update(
        {
            "state": "ambiguous",
            "box_xyxy": None,
            "temporary_candidate_index": None,
            "evidence": "bidirectional",
            "reason": "forward-backward-disagreement",
            "hypotheses": {
                "forward": hypothesis(forward),
                "backward": hypothesis(backward),
            },
        }
    )


def joint_frame(context, view, method, frame):
    return context["normalized_joint"]["views"][view][method][frame]


def recompute_joint_claims(record):
    rows = sorted(record["score_matrix"], key=lambda item: item["target_id"])
    target_ids = [row["target_id"] for row in rows]
    score_rows = [
        [item["score"] for item in row["candidate_scores"]] for row in rows
    ]
    candidate_count = len(score_rows[0])

    def serialized(signature):
        return [
            {
                "target_id": target_id,
                "temporary_candidate_index": signature[index],
                "score": score_rows[index][signature[index]],
            }
            for index, target_id in enumerate(target_ids)
        ]

    independent = []
    selected = {}
    for target_id, scores in zip(target_ids, score_rows):
        maximum = max(scores)
        tied = [index for index, value in enumerate(scores) if value == maximum]
        selected.setdefault(tied[0], []).append(target_id)
        independent.append(
            {
                "target_id": target_id,
                "temporary_candidate_index": tied[0],
                "score": maximum,
                "tied_top_temporary_candidate_indexes": tied,
                "is_tie": len(tied) > 1,
            }
        )
    collisions = [
        {"temporary_candidate_index": candidate, "target_ids": targets}
        for candidate, targets in sorted(selected.items())
        if len(targets) > 1
    ]
    assignments = []
    for signature in itertools.permutations(range(candidate_count), len(target_ids)):
        assignments.append(
            (
                sum(
                    score_rows[index][signature[index]]
                    for index in range(len(target_ids))
                ),
                signature,
            )
        )
    assignments.sort(key=lambda item: (-item[0], item[1]))
    best_score, best = assignments[0]
    runner_score, runner = assignments[1]
    tied = [signature for score, signature in assignments if score == best_score]
    record.update(
        {
            "evaluable": True,
            "unevaluable_reason": None,
            "calculated_permutation_count": len(assignments),
            "enumerated_assignment_count": len(assignments),
            "independent_top_choices": independent,
            "independent_collisions": collisions,
            "collision_constraint_applied": bool(collisions),
            "best_assignment": serialized(best),
            "best_score": best_score,
            "runner_up_assignment": serialized(runner),
            "runner_up_score": runner_score,
            "best_minus_runner_up_margin": best_score - runner_score,
            "is_exact_assignment_tie": len(tied) > 1,
            "tied_best_assignments": [serialized(signature) for signature in tied],
        }
    )


def change_assignment(context, view, method, frame, mapping):
    record = joint_frame(context, view, method, frame)
    for row in record["score_matrix"]:
        selected = mapping[row["target_id"]]
        for candidate in row["candidate_scores"]:
            candidate["score"] = (
                1.0
                if candidate["temporary_candidate_index"] == selected
                else 0.1 - candidate["temporary_candidate_index"] * 0.01
            )
    recompute_joint_claims(record)


def make_exact_assignment_tie(record):
    rows = {row["target_id"]: row for row in record["score_matrix"]}
    for target_id in sorted(rows):
        values = [1.0, 1.0, 0.0]
        for item, value in zip(rows[target_id]["candidate_scores"], values):
            item["score"] = value
    recompute_joint_claims(record)


class FusionPrecedenceTests(unittest.TestCase):
    def test_anchor_overrides_conflicting_appearance(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory, joint_frames=[0])
            context = bundle.load()
            change_assignment(context, "whole_box", "endpoint_mean_score", 0, {"a": 2, "b": 1})
            report = fusion.build_fusion(context)
            result = target_result(report, 0, "a")
            self.assertEqual(result["state"], "anchor")
            self.assertEqual(result["accepted_temporary_candidate_index"], 0)
            self.assertIsNone(result["diagnostic_appearance_proposal"])

    def test_forced_occlusion_overrides_all_appearance_and_blocks_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory, forced=True)
            context = bundle.load()
            report = fusion.build_fusion(context)
            for frame in (1, 2):
                for target_id in ("a", "b"):
                    result = target_result(report, frame, target_id)
                    self.assertEqual(result["state"], "occluded")
                    self.assertEqual(result["accepted_evidence"], "user-forced")
                    self.assertIsNone(result["diagnostic_appearance_proposal"])
            self.assertTrue(
                all(
                    item["status"] == "blocked-by-forced-occlusion"
                    for item in context["tracks"]["a"]["interval_diagnostics"]
                )
            )

    def test_agreed_geometry_remains_accepted_when_appearance_agrees(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            result = target_result(fusion.build_fusion(context), 1, "a")
            self.assertEqual(result["state"], "tracked")
            self.assertEqual(result["accepted_evidence"], "geometry-bidirectional")
            self.assertEqual(result["accepted_temporary_candidate_index"], 0)
            self.assertEqual(
                result["appearance_relation_to_authoritative_geometry"]["status"],
                "corroborating",
            )

    def test_agreed_geometry_remains_accepted_when_appearance_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            for view in context["joint_context"]["crop_views"]:
                for method in context["joint_context"]["primary_methods"]:
                    change_assignment(context, view, method, 1, {"a": 2, "b": 1})
            result = target_result(fusion.build_fusion(context), 1, "a")
            self.assertEqual(result["state"], "tracked")
            self.assertEqual(result["accepted_temporary_candidate_index"], 0)
            self.assertIsNone(result["diagnostic_appearance_proposal"])
            self.assertEqual(
                result["appearance_relation_to_authoritative_geometry"]["status"],
                "conflicting",
            )

    def test_ambiguous_geometry_gets_unanimous_non_authoritative_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            result = target_result(fusion.build_fusion(context), 1, "a")
            self.assertEqual(result["state"], "ambiguous")
            self.assertIsNone(result["accepted_box_xyxy"])
            self.assertEqual(
                result["diagnostic_appearance_proposal"]["status"],
                "candidate-proposed-unthresholded",
            )
            self.assertEqual(
                result["diagnostic_appearance_proposal"][
                    "temporary_candidate_index"
                ],
                0,
            )
            self.assertFalse(
                result["diagnostic_appearance_proposal"][
                    "appearance_is_authoritative"
                ]
            )

    def test_two_ambiguous_targets_get_injective_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            make_ambiguous(context, "b", 1, forward=1, backward=2)
            report = fusion.build_fusion(context)
            proposals = {
                target_id: target_result(report, 1, target_id)[
                    "diagnostic_appearance_proposal"
                ]["temporary_candidate_index"]
                for target_id in ("a", "b")
            }
            self.assertEqual(proposals, {"a": 0, "b": 1})
            self.assertEqual(len(set(proposals.values())), 2)

    def test_reserved_geometry_conflict_rejects_method_and_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            for view in context["joint_context"]["crop_views"]:
                for method in context["joint_context"]["primary_methods"]:
                    change_assignment(context, view, method, 1, {"a": 1, "b": 0})
            report = fusion.build_fusion(context)
            proposal = target_result(report, 1, "a")["diagnostic_appearance_proposal"]
            self.assertEqual(proposal["status"], "no-proposal")
            self.assertEqual(
                proposal["reason"], "incompatible-with-accepted-geometry"
            )

    def test_exact_joint_tie_produces_no_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            record = joint_frame(context, "whole_box", "endpoint_mean_score", 1)
            make_exact_assignment_tie(record)
            proposal = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(proposal["status"], "no-proposal")
            self.assertEqual(proposal["reason"], "exact-assignment-tie")

    def test_missing_and_null_joint_evidence_produce_no_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory, joint_frames=[1]).load()
            make_ambiguous(context, "a", 2)
            missing = target_result(fusion.build_fusion(context), 2, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(missing["reason"], "missing-joint-frame")
            make_ambiguous(context, "a", 1)
            record = joint_frame(context, "whole_box", "endpoint_mean_score", 1)
            record["score_matrix"][0]["candidate_scores"][0]["score"] = None
            record["evaluable"] = False
            record["unevaluable_reason"] = "null-scores"
            for field in fusion.USABLE_ASSIGNMENT_FIELDS:
                record.pop(field, None)
            null = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(null["reason"], "null-scores")

        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory, insufficient=True).load()
            make_ambiguous(context, "a", 1, forward=0, backward=0)
            insufficient = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(insufficient["status"], "no-proposal")
            self.assertEqual(insufficient["reason"], "insufficient-candidates")

    def test_crop_view_disagreement_produces_no_consolidated_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            change_assignment(
                context, "inner_box", "endpoint_mean_score", 1, {"a": 2, "b": 1}
            )
            proposal = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(proposal["reason"], "crop-view-disagreement")

    def test_reference_method_disagreement_produces_no_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            second_reference = context["joint_context"]["primary_methods"][1]
            for view in context["joint_context"]["crop_views"]:
                change_assignment(context, view, second_reference, 1, {"a": 2, "b": 1})
            proposal = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(proposal["reason"], "reference-method-disagreement")

    def test_full_gallery_never_drives_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            for view in context["joint_context"]["crop_views"]:
                change_assignment(
                    context,
                    view,
                    joint_tool.FULL_GALLERY_METHOD,
                    1,
                    {"a": 2, "b": 1},
                )
            report = fusion.build_fusion(context)
            proposal = target_result(report, 1, "a")["diagnostic_appearance_proposal"]
            self.assertEqual(proposal["temporary_candidate_index"], 0)
            evidence = next(item for item in report["frames"] if item["frame"] == 1)[
                "joint_appearance"
            ]["views"]["whole_box"]["methods"]["full_gallery_score"]
            self.assertFalse(evidence["eligible_for_proposal"])

    def test_occluded_and_untracked_geometry_are_not_reacquired(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            for state, frame in (("occluded", 1), ("untracked", 2)):
                record = next(
                    item
                    for item in context["tracks"]["a"]["frames"]
                    if item["frame"] == frame
                )
                record.update(
                    {
                        "state": state,
                        "temporary_candidate_index": None,
                        "box_xyxy": None,
                        "evidence": None,
                        "reason": "synthetic",
                        "hypotheses": None,
                    }
                )
            report = fusion.build_fusion(context)
            for state, frame in (("occluded", 1), ("untracked", 2)):
                result = target_result(report, frame, "a")
                self.assertEqual(result["state"], state)
                self.assertIsNone(result["diagnostic_appearance_proposal"])


class FusionValidationTests(unittest.TestCase):
    def test_sidecar_anchor_track_appearance_and_joint_hash_mismatches(self):
        mutations = ("sidecar", "anchor", "track", "appearance", "joint")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                if mutation == "sidecar":
                    bundle.joint_doc["provenance"]["sidecar_sha256"] = "f" * 64
                    write_json(bundle.joint_path, bundle.joint_doc)
                elif mutation == "anchor":
                    bundle.joint_doc["provenance"]["targets"][0][
                        "anchor_file_sha256"
                    ] = "f" * 64
                    write_json(bundle.joint_path, bundle.joint_doc)
                elif mutation == "track":
                    bundle.track_docs["a"]["source"]["sidecar_sha256"] = "f" * 64
                    write_json(bundle.track_paths["a"], bundle.track_docs["a"])
                elif mutation == "appearance":
                    bundle.appearance_docs["a"]["source"]["fps"] = 99.0
                    write_json(bundle.appearance_paths["a"], bundle.appearance_docs["a"])
                else:
                    bundle.joint_doc["provenance"]["weights_sha256"] = "f" * 64
                    write_json(bundle.joint_path, bundle.joint_doc)
                with self.assertRaises(fusion.FusionError):
                    bundle.load()

    def test_resolved_anchor_and_forced_range_mismatches_are_rejected(self):
        for field in ("resolved_anchors", "forced_occlusion_ranges"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                bundle.track_docs["a"][field] = (
                    []
                    if field == "resolved_anchors"
                    else [{"start_frame": 1, "end_frame": 1, "note": None}]
                )
                write_json(bundle.track_paths["a"], bundle.track_docs["a"])
                with self.assertRaisesRegex(fusion.FusionError, field):
                    bundle.load()

    def test_candidate_box_and_order_mismatches_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            report = bundle.appearance_docs["a"]
            report["views"]["whole_box"]["frames"][0]["candidates"][0][
                "box_xyxy"
            ] = [1.0, 0.0, 10.0, 20.0]
            write_json(bundle.appearance_paths["a"], report)
            bundle.joint_doc["provenance"]["targets"][0][
                "appearance_report_sha256"
            ] = file_hash(bundle.appearance_paths["a"])
            write_json(bundle.joint_path, bundle.joint_doc)
            with self.assertRaisesRegex(fusion.FusionError, "does not match the sidecar"):
                bundle.load()

    def test_temporal_anchor_distances_support_intervals_one_and_eleven(self):
        for interval in (1, 11):
            with self.subTest(interval=interval), tempfile.TemporaryDirectory() as directory:
                context = Bundle(directory, interval=interval).load()
                report = fusion.build_fusion(context)
                result = target_result(report, interval, "a")["temporal_anchor_distance"]
                self.assertEqual(result["previous"]["distance_source_frames"], interval)
                self.assertEqual(result["previous"]["distance_sampled_steps"], 1)
                self.assertEqual(result["next"]["distance_source_frames"], interval * 2)
                self.assertEqual(result["next"]["distance_sampled_steps"], 2)

    def test_temp_reordering_preserves_box_relative_not_persistent_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            make_ambiguous(context, "a", 1)
            report = fusion.build_fusion(context)
            proposal = target_result(report, 1, "a")["diagnostic_appearance_proposal"]
            frame = next(item for item in report["frames"] if item["frame"] == 1)
            sidecar_box = context["sidecar"]["all_boxes"][1][
                proposal["temporary_candidate_index"]
            ]
            self.assertEqual(proposal["box_xyxy"], list(sidecar_box))
            self.assertTrue(
                report["policy"]["temporary_candidate_indexes_are_per_frame_only"]
            )

    def test_partial_joint_frame_coverage_is_permitted(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory, joint_frames=[1]).load()
            report = fusion.build_fusion(context)
            self.assertEqual(report["summary"]["joint_appearance_frame_count"], 1)
            self.assertEqual(report["summary"]["geometry_only_frame_count"], 3)

    def test_output_contains_no_forbidden_artifacts_or_identity_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            report = fusion.build_fusion(Bundle(directory).load())
            serialized = json.dumps(report).lower()
            for forbidden in (
                "source_video",
                "video_path",
                "embedding",
                "crop_image",
                "production_identity",
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertFalse(report["policy"]["appearance_is_authoritative"])
            self.assertIsNone(report["policy"]["appearance_confidence_threshold"])

    def test_output_path_restriction_collision_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "first.json"
            second = root / "second.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            inputs = [("first", first), ("second", second)]
            with mock.patch.object(fusion, "OUTPUT_ROOT", root):
                fusion.validate_paths(inputs, root / "output.json")
                with self.assertRaisesRegex(fusion.FusionError, "overwrite"):
                    fusion.validate_paths(inputs, first)
                with self.assertRaisesRegex(fusion.FusionError, "beneath"):
                    fusion.validate_paths(inputs, root.parent / "outside.json")
                output = root / "atomic.json"
                with mock.patch.object(fusion.os, "link", side_effect=OSError("fail")):
                    with self.assertRaisesRegex(fusion.FusionError, "without overwriting"):
                        fusion.write_output(output, {"schema": "test"})
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(output.name + ".tmp").exists())

    def test_cli_requires_at_least_two_targets(self):
        with self.assertRaisesRegex(fusion.FusionError, "At least two"):
            fusion.parse_target_paths(["a=file.json"], "--track")

    def test_source_input_structures_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            before = copy.deepcopy(
                {
                    "tracks": context["tracks"],
                    "anchors": context["anchors"],
                    "appearances": context["appearances"],
                    "joint": context["joint"],
                    "sidecar": context["sidecar"],
                }
            )
            fusion.build_fusion(context)
            after = {
                "tracks": context["tracks"],
                "anchors": context["anchors"],
                "appearances": context["appearances"],
                "joint": context["joint"],
                "sidecar": context["sidecar"],
            }
            self.assertEqual(before, after)

    def test_source_files_remain_byte_for_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            paths = [
                bundle.sidecar_path,
                bundle.joint_path,
                *bundle.anchor_paths.values(),
                *bundle.track_paths.values(),
                *bundle.appearance_paths.values(),
            ]
            before = {path: path.read_bytes() for path in paths}
            fusion.build_fusion(bundle.load())
            self.assertEqual(before, {path: path.read_bytes() for path in paths})


class FusionAuditCorrectionTests(unittest.TestCase):
    def test_geometry_outside_anchor_intervals_is_always_conservative(self):
        forced_after_last = [{"start_frame": 5, "end_frame": 5}]
        for frame, state in ((6, "tracked"), (8, "tracked"), (6, "ambiguous")):
            with self.subTest(position="after-final-anchor", frame=frame, state=state):
                sidecar, anchors, track = geometry_validation_case(
                    forced_ranges=forced_after_last
                )
                set_geometry_record(track, sidecar, frame, state)
                with self.assertRaisesRegex(
                    fusion.FusionError, "outside every allowed anchor interval"
                ):
                    fusion.validate_geometry_track(track, sidecar, anchors, "a")

        for frame, state in ((0, "tracked"), (1, "ambiguous")):
            with self.subTest(position="before-first-anchor", frame=frame, state=state):
                sidecar, anchors, track = geometry_validation_case()
                set_geometry_record(track, sidecar, frame, state)
                with self.assertRaisesRegex(
                    fusion.FusionError, "outside every allowed anchor interval"
                ):
                    fusion.validate_geometry_track(track, sidecar, anchors, "a")

    def test_forced_range_before_first_anchor_does_not_open_tracking(self):
        sidecar, anchors, track = geometry_validation_case(
            forced_ranges=[{"start_frame": 0, "end_frame": 0}]
        )
        fusion.validate_geometry_track(track, sidecar, anchors, "a")
        set_geometry_record(track, sidecar, 1, "tracked")
        with self.assertRaisesRegex(
            fusion.FusionError, "outside every allowed anchor interval"
        ):
            fusion.validate_geometry_track(track, sidecar, anchors, "a")

    def test_unblocked_allowed_interval_accepts_tracked_and_ambiguous_records(self):
        sidecar, anchors, track = geometry_validation_case()
        self.assertEqual(
            next(item for item in track["frames"] if item["frame"] == 3)["state"],
            "tracked",
        )
        fusion.validate_geometry_track(track, sidecar, anchors, "a")
        set_geometry_record(track, sidecar, 3, "ambiguous")
        fusion.validate_geometry_track(track, sidecar, anchors, "a")

    def test_tracked_state_requires_matching_forward_and_backward_hypotheses(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            record = bundle.track_docs["a"]["frames"][1]
            record["hypotheses"]["backward"].update(
                {
                    "temporary_candidate_index": 2,
                    "box_xyxy": bundle.sidecar_raw["all_boxes"][1][2],
                }
            )
            write_json(bundle.track_paths["a"], bundle.track_docs["a"])
            with self.assertRaisesRegex(fusion.FusionError, "bidirectional"):
                bundle.load()

    def test_anchor_records_are_bound_to_exact_durable_anchors(self):
        mutations = ("candidate", "evidence", "missing", "spurious")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                if mutation == "spurious":
                    record = bundle.track_docs["a"]["frames"][1]
                    record.update(
                        {
                            "state": "anchor",
                            "temporary_candidate_index": 0,
                            "box_xyxy": bundle.sidecar_raw["all_boxes"][1][0],
                            "evidence": "user-anchor",
                            "reason": None,
                            "hypotheses": None,
                        }
                    )
                else:
                    record = bundle.track_docs["a"]["frames"][0]
                    if mutation == "candidate":
                        record["temporary_candidate_index"] = 2
                        record["box_xyxy"] = bundle.sidecar_raw["all_boxes"][0][2]
                    elif mutation == "evidence":
                        record["evidence"] = "bidirectional"
                    else:
                        record.update(
                            {
                                "state": "untracked",
                                "temporary_candidate_index": None,
                                "box_xyxy": None,
                                "evidence": None,
                                "reason": "outside-anchor-coverage",
                            }
                        )
                write_json(bundle.track_paths["a"], bundle.track_docs["a"])
                with self.assertRaises(fusion.FusionError):
                    bundle.load()

    def test_authoritative_candidate_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            source = bundle.track_docs["a"]["frames"][1]
            bundle.track_docs["b"]["frames"][1] = copy.deepcopy(source)
            write_json(bundle.track_paths["b"], bundle.track_docs["b"])
            with self.assertRaisesRegex(fusion.FusionError, "conflicting authoritative"):
                bundle.load()

    def test_blocked_interval_cannot_hide_tracked_propagation(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory, forced_single=True)
            record = bundle.track_docs["a"]["frames"][2]
            candidate = bundle.sidecar_raw["all_boxes"][2][0]
            hypothesis = {
                "box_xyxy": list(candidate),
                "temporary_candidate_index": 0,
                "score": 0.9,
                "iou": 0.8,
                "center_displacement_diagonals": 0.1,
                "area_fold_change": 1.1,
            }
            record.update(
                {
                    "state": "tracked",
                    "box_xyxy": list(candidate),
                    "temporary_candidate_index": 0,
                    "evidence": "bidirectional",
                    "reason": None,
                    "hypotheses": {
                        "forward": copy.deepcopy(hypothesis),
                        "backward": copy.deepcopy(hypothesis),
                    },
                }
            )
            write_json(bundle.track_paths["a"], bundle.track_docs["a"])
            with self.assertRaisesRegex(
                fusion.FusionError, "outside every allowed anchor interval"
            ):
                bundle.load()

    def test_temporal_distances_respect_forced_barriers_at_intervals_one_and_eleven(self):
        for interval in (1, 11):
            with self.subTest(interval=interval):
                frames = [index * interval for index in range(5)]
                sidecar = {
                    "fps": 10.0,
                    "sample_indices": frames,
                    "frame_to_position": {
                        frame: index for index, frame in enumerate(frames)
                    },
                }
                anchors = [
                    {"anchor_id": "left", "frame": frames[0]},
                    {"anchor_id": "right", "frame": frames[4]},
                ]
                ranges = [
                    {"start_frame": frames[2], "end_frame": frames[2]}
                ]
                before = fusion._distance_to_anchors(
                    frames[1], sidecar, anchors, ranges
                )
                self.assertEqual(before["previous"]["frame"], frames[0])
                self.assertIsNone(before["next"])
                self.assertEqual(
                    before["chronological"]["next"]["blocking_forced_range"],
                    ranges[0],
                )
                forced = fusion._distance_to_anchors(
                    frames[2], sidecar, anchors, ranges
                )
                self.assertIsNone(forced["previous"])
                self.assertIsNone(forced["next"])
                after = fusion._distance_to_anchors(
                    frames[3], sidecar, anchors, ranges
                )
                self.assertIsNone(after["previous"])
                self.assertEqual(after["next"]["frame"], frames[4])
                self.assertEqual(
                    after["next"]["distance_sampled_steps"], 1
                )
                self.assertEqual(
                    after["next"]["distance_source_frames"], interval
                )
                self.assertEqual(
                    after["next"]["distance_seconds"], interval / 10.0
                )

    def test_frame_ranges_require_sampled_inclusive_boundaries(self):
        for interval in (1, 11):
            sidecar = {
                "sample_indices": [0, interval, interval * 2, interval * 3],
                "frame_to_position": {
                    frame: index
                    for index, frame in enumerate(
                        [0, interval, interval * 2, interval * 3]
                    )
                },
            }
            self.assertEqual(
                fusion._selected_frames(sidecar, (interval, interval * 2)),
                [interval, interval * 2],
            )
            self.assertEqual(
                fusion._selected_frames(sidecar, (interval, interval)),
                [interval],
            )
            self.assertEqual(fusion._selected_frames(sidecar, None), sidecar["sample_indices"])
            with self.assertRaisesRegex(fusion.FusionError, "START"):
                fusion._selected_frames(sidecar, (1 if interval == 11 else 4, interval * 2))
            with self.assertRaisesRegex(fusion.FusionError, "END"):
                fusion._selected_frames(sidecar, (interval, interval * 3 + 1))
            with self.assertRaises(fusion.FusionError):
                fusion._selected_frames(sidecar, (-interval, interval))
            with self.assertRaisesRegex(fusion.FusionError, "reversed"):
                fusion._selected_frames(sidecar, (interval * 2, interval))

    def test_joint_optimizer_claims_are_recomputed(self):
        mutations = ("best", "best_score", "runner", "margin", "independent")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                record = serialized_joint_frame(bundle)
                if mutation == "best":
                    record["best_assignment"] = copy.deepcopy(
                        record["runner_up_assignment"]
                    )
                elif mutation == "best_score":
                    record["best_score"] += 0.25
                elif mutation == "runner":
                    record["runner_up_assignment"] = copy.deepcopy(
                        record["best_assignment"]
                    )
                    record["runner_up_score"] = record["best_score"]
                elif mutation == "margin":
                    record["best_minus_runner_up_margin"] += 0.25
                else:
                    record["independent_top_choices"][0][
                        "temporary_candidate_index"
                    ] = 2
                bundle.write_joint()
                with self.assertRaises(fusion.FusionError):
                    bundle.load()

    def test_joint_tied_best_set_and_flag_are_recomputed(self):
        for mutation in ("incomplete", "flag"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                make_serialized_tie_bundle(bundle)
                record = serialized_joint_frame(bundle)
                self.assertTrue(record["is_exact_assignment_tie"])
                if mutation == "incomplete":
                    record["tied_best_assignments"].pop()
                else:
                    record["is_exact_assignment_tie"] = False
                bundle.write_joint()
                with self.assertRaises(fusion.FusionError):
                    bundle.load()

    def test_partial_primary_method_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            del bundle.joint_doc["views"]["whole_box"]["methods"][
                "endpoint_mean_score"
            ]
            bundle.write_joint()
            with self.assertRaisesRegex(fusion.FusionError, "missing primary"):
                bundle.load()

    def test_target_order_is_logically_invariant(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            bundle.joint_doc["provenance"]["targets"].reverse()
            for view in bundle.joint_doc["views"].values():
                for method in view["methods"].values():
                    for frame in method["frames"]:
                        for field in (
                            "score_matrix",
                            "independent_top_choices",
                            "best_assignment",
                            "runner_up_assignment",
                        ):
                            if isinstance(frame.get(field), list):
                                frame[field].reverse()
                        for assignment in frame.get("tied_best_assignments", []):
                            assignment.reverse()
                        for collision in frame.get("independent_collisions", []):
                            collision["target_ids"].reverse()
            bundle.write_joint()
            context = bundle.load()
            make_ambiguous(context, "a", 1)
            proposal = target_result(fusion.build_fusion(context), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            self.assertEqual(proposal["temporary_candidate_index"], 0)

    def test_candidate_reordering_tracks_physical_boxes_not_indexes(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            normal = Bundle(first).load()
            reordered = Bundle(second, reorder_candidates=True).load()
            make_ambiguous(normal, "a", 1)
            make_ambiguous(reordered, "a", 1, forward=2, backward=0)
            normal_proposal = target_result(fusion.build_fusion(normal), 1, "a")[
                "diagnostic_appearance_proposal"
            ]
            reordered_proposal = target_result(
                fusion.build_fusion(reordered), 1, "a"
            )["diagnostic_appearance_proposal"]
            self.assertNotEqual(
                normal_proposal["temporary_candidate_index"],
                reordered_proposal["temporary_candidate_index"],
            )
            self.assertEqual(
                normal_proposal["box_xyxy"], reordered_proposal["box_xyxy"]
            )

    def test_permutation_limit_is_enforced_by_fusion(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            with mock.patch.object(joint_tool, "MAX_ENUMERATED_ASSIGNMENTS", 1):
                with self.assertRaisesRegex(fusion.FusionError, "exceeding the limit"):
                    bundle.load()

    def test_appearance_metadata_mismatches_reach_isolated_validation(self):
        mutations = ("fps", "architecture", "preprocessing", "crop_views")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                original_joint_preprocessing = copy.deepcopy(
                    bundle.joint_doc["provenance"]["preprocessing"]
                )
                for target_id, report in bundle.appearance_docs.items():
                    if mutation == "fps":
                        report["source"]["fps"] = 99.0
                    elif mutation == "architecture":
                        report["model"]["architecture"] = "different-model"
                    elif mutation == "preprocessing":
                        report["model"]["preprocessing"]["color"] = "BGR"
                    else:
                        del report["views"]["inner_box"]
                    write_json(bundle.appearance_paths[target_id], report)
                if mutation == "preprocessing":
                    bundle.joint_doc["provenance"]["preprocessing"] = (
                        original_joint_preprocessing
                    )
                for item in bundle.joint_doc["provenance"]["targets"]:
                    item["appearance_report_sha256"] = file_hash(
                        bundle.appearance_paths[item["target_id"]]
                    )
                bundle.write_joint()
                with self.assertRaises(fusion.FusionError):
                    bundle.load()

    def test_reference_frame_mismatch_reaches_fusion_provenance_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            for target_id, report in bundle.appearance_docs.items():
                rename_reference_method(report, 3, 2)
                write_json(bundle.appearance_paths[target_id], report)
            for item in bundle.joint_doc["provenance"]["targets"]:
                item["appearance_report_sha256"] = file_hash(
                    bundle.appearance_paths[item["target_id"]]
                )
            bundle.write_joint()
            with self.assertRaisesRegex(
                fusion.FusionError,
                "Appearance and joint reference frames do not match",
            ):
                bundle.load()

    def test_malformed_joint_scalar_and_collection_types_raise_fusion_error(self):
        cases = (
            (
                "numeric-sidecar-hash",
                lambda bundle: bundle.joint_doc["provenance"].__setitem__(
                    "sidecar_sha256", 7
                ),
                "sidecar_sha256",
            ),
            (
                "null-sidecar-hash",
                lambda bundle: bundle.joint_doc["provenance"].__setitem__(
                    "sidecar_sha256", None
                ),
                "sidecar_sha256",
            ),
            (
                "list-crop-view",
                lambda bundle: bundle.joint_doc.__setitem__(
                    "crop_views", [["whole_box"], "inner_box"]
                ),
                "crop_views",
            ),
            (
                "object-crop-view",
                lambda bundle: bundle.joint_doc.__setitem__(
                    "crop_views", [{"name": "whole_box"}, "inner_box"]
                ),
                "crop_views",
            ),
            (
                "mixed-analyzed-frames",
                lambda bundle: bundle.joint_doc.__setitem__(
                    "analyzed_frames", [1, "2"]
                ),
                "analyzed_frames",
            ),
            (
                "unhashable-analyzed-frame",
                lambda bundle: bundle.joint_doc.__setitem__(
                    "analyzed_frames", [1, []]
                ),
                "analyzed_frames",
            ),
            (
                "non-list-targets",
                lambda bundle: bundle.joint_doc["provenance"].__setitem__(
                    "targets", {"a": "report"}
                ),
                "targets must be a list",
            ),
            (
                "non-object-target",
                lambda bundle: bundle.joint_doc["provenance"]["targets"].__setitem__(
                    0, []
                ),
                "targets\\[0\\]",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                mutate(bundle)
                bundle.write_joint()
                with self.assertRaisesRegex(fusion.FusionError, message):
                    bundle.load()

    def test_malformed_joint_target_ids_raise_fusion_error(self):
        for value in (7, None, ["a"], {"id": "a"}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                bundle.joint_doc["provenance"]["targets"][0]["target_id"] = value
                bundle.write_joint()
                with self.assertRaisesRegex(fusion.FusionError, "target_id is invalid"):
                    bundle.load()

    def test_missing_and_malformed_provenance_raise_fusion_error(self):
        for value in (None, [], 7):
            with self.subTest(kind="joint", value=value), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                bundle.joint_doc["provenance"] = value
                bundle.write_joint()
                with self.assertRaisesRegex(fusion.FusionError, "provenance must be an object"):
                    bundle.load()

        for value in (None, [], 7):
            with self.subTest(kind="appearance", value=value), tempfile.TemporaryDirectory() as directory:
                bundle = Bundle(directory)
                bundle.appearance_docs["a"]["source"] = value
                write_json(bundle.appearance_paths["a"], bundle.appearance_docs["a"])
                target = next(
                    item
                    for item in bundle.joint_doc["provenance"]["targets"]
                    if item["target_id"] == "a"
                )
                target["appearance_report_sha256"] = file_hash(
                    bundle.appearance_paths["a"]
                )
                bundle.write_joint()
                with self.assertRaisesRegex(fusion.FusionError, "source must be an object"):
                    bundle.load()

    def test_assignment_validation_errors_are_translated_to_fusion_error(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            bundle.joint_doc["provenance"]["weights_sha256"] = "not-a-hash"
            bundle.write_joint()
            with self.assertRaisesRegex(
                fusion.FusionError,
                "must be exactly 64 hexadecimal SHA-256 characters",
            ) as raised:
                bundle.load()
            self.assertNotIsInstance(raised.exception, joint_tool.AssignmentError)

    def test_track_id_and_actual_sidecar_content_mismatches_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            bundle.track_docs["a"]["track_id"] = "wrong-track"
            write_json(bundle.track_paths["a"], bundle.track_docs["a"])
            with self.assertRaisesRegex(fusion.FusionError, "Track id"):
                bundle.load()
        with tempfile.TemporaryDirectory() as directory:
            bundle = Bundle(directory)
            bundle.sidecar_raw["boxes"][0] = [1.0, 0.0, 10.0, 20.0]
            write_json(bundle.sidecar_path, bundle.sidecar_raw)
            with self.assertRaisesRegex(fusion.FusionError, "sidecar_sha256"):
                bundle.load()

    def test_nested_output_is_defensively_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Bundle(directory).load()
            report = fusion.build_fusion(context)
            original_track = copy.deepcopy(context["tracks"])
            original_joint = copy.deepcopy(context["normalized_joint"])
            frame = report["frames"][1]
            frame["targets"][0]["source_geometry"]["hypotheses"]["forward"][
                "box_xyxy"
            ][0] = -999
            method = frame["joint_appearance"]["views"]["whole_box"]["methods"][
                "endpoint_mean_score"
            ]
            method["best_assignment"][0]["score"] = -999
            self.assertEqual(context["tracks"], original_track)
            self.assertEqual(context["normalized_joint"], original_joint)

    def test_summary_denominators_match_synthetic_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            report = fusion.build_fusion(Bundle(directory).load())
            for target_id, summary in report["summary"]["targets"].items():
                records = [
                    next(
                        target
                        for target in frame["targets"]
                        if target["target_id"] == target_id
                    )
                    for frame in report["frames"]
                ]
                self.assertEqual(sum(summary["state_counts"].values()), len(records))
                self.assertEqual(
                    sum(summary["accepted_evidence_counts"].values()), len(records)
                )
                self.assertEqual(
                    sum(summary["proposal_status_counts"].values()),
                    sum(record["state"] == "ambiguous" for record in records),
                )

    def test_atomic_publication_preserves_racing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "race.json"

            def create_destination_then_fail(source, destination, **_kwargs):
                Path(destination).write_bytes(b"racing-owner")
                raise FileExistsError("destination exists")

            with mock.patch.object(fusion, "OUTPUT_ROOT", root), mock.patch.object(
                fusion.os, "link", side_effect=create_destination_then_fail
            ):
                with self.assertRaisesRegex(fusion.FusionError, "without overwriting"):
                    fusion.write_output(output, {"schema": "test"})
            self.assertEqual(output.read_bytes(), b"racing-owner")
            self.assertFalse(output.with_name(output.name + ".tmp").exists())

    def test_fully_populated_cli_rejects_labels_and_threshold(self):
        arguments = [
            "--sidecar", "sidecar.json",
            "--joint-assignment", "joint.json",
            "--anchors", "a=a.json",
            "--anchors", "b=b.json",
            "--track", "a=at.json",
            "--track", "b=bt.json",
            "--appearance", "a=aa.json",
            "--appearance", "b=ba.json",
            "--output", "checkpoints/dev-preview/out.json",
        ]
        for unsupported in (("--labels", "a=1:0"), ("--threshold", "0.5")):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                fusion.parse_args(arguments + list(unsupported))
            self.assertIn("unrecognized arguments", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
