"""Diagnose one-to-one assignments across target-specific appearance reports."""

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = (SCRIPT_DIR / "checkpoints" / "dev-preview").resolve()
APPEARANCE_SCHEMA = "blendcap-preview-target-appearance-diagnostic"
APPEARANCE_VERSION = 1
SCHEMA = "blendcap-preview-multi-target-assignment-diagnostic"
VERSION = 1
FULL_GALLERY_METHOD = "full_gallery_score"
MAX_ENUMERATED_ASSIGNMENTS = 1_000_000
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class AssignmentError(RuntimeError):
    """A clear input-validation or diagnostic failure."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Compare target-specific appearance reports with injective, "
            "per-frame diagnostic assignments. TEMP positions remain "
            "per-frame diagnostics and are not persistent identities."
        )
    )
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        metavar="TARGET_ID=REPORT_PATH",
        help="Repeat for each target-specific appearance report",
    )
    parser.add_argument(
        "--frame-range",
        metavar="START:END",
        help="Optional inclusive frame range",
    )
    parser.add_argument(
        "--labels",
        action="append",
        default=[],
        metavar="TARGET_ID=FRAME:TEMP,...",
        help="Optional repeatable human ground truth",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def _hash_file(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssignmentError(f"Cannot hash report {path}: {exc}") from exc
    return digest.hexdigest()


def _load_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AssignmentError(f"Cannot read appearance report {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssignmentError(f"Appearance report must contain an object: {path}")
    return value


def parse_target_specs(values):
    targets = {}
    for value in values:
        target_id, separator, path_text = value.partition("=")
        if not separator or not target_id or not path_text:
            raise AssignmentError("--target must use TARGET_ID=REPORT_PATH")
        if target_id in targets:
            raise AssignmentError(f"Duplicate target id: {target_id}")
        targets[target_id] = Path(path_text)
    if len(targets) < 2:
        raise AssignmentError("At least two unique targets are required")
    return dict(sorted(targets.items()))


def parse_frame_range(value):
    if value is None:
        return None
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        start, end = (int(part) for part in parts)
    except ValueError as exc:
        raise AssignmentError("--frame-range must use START:END") from exc
    if start > end:
        raise AssignmentError("--frame-range is reversed")
    return start, end


def parse_label_specs(values, target_ids):
    labels = {}
    for value in values:
        target_id, separator, label_text = value.partition("=")
        if not separator or not target_id:
            raise AssignmentError(
                "--labels must use TARGET_ID=FRAME:TEMP,FRAME:TEMP"
            )
        if target_id not in target_ids:
            raise AssignmentError(f"Labels reference unknown target: {target_id}")
        if target_id in labels:
            raise AssignmentError(f"Duplicate --labels entry for target: {target_id}")
        target_labels = {}
        if label_text:
            for item in label_text.split(","):
                try:
                    frame_text, temporary_text = item.split(":")
                    frame = int(frame_text)
                    temporary_index = int(temporary_text)
                except ValueError as exc:
                    raise AssignmentError(
                        "--labels must use TARGET_ID=FRAME:TEMP,FRAME:TEMP"
                    ) from exc
                if frame in target_labels:
                    raise AssignmentError(
                        f"Duplicate label for target {target_id}, frame {frame}"
                    )
                if frame < 0 or temporary_index < 0:
                    raise AssignmentError("Label frames and TEMP positions must be non-negative")
                target_labels[frame] = temporary_index
        labels[target_id] = target_labels
    return labels


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_paths(target_paths, output):
    resolved_inputs = set()
    for target_id, path in target_paths.items():
        if not path.is_file():
            raise AssignmentError(f"Report for target {target_id} not found: {path}")
        resolved_inputs.add(_resolved(path))
    if output.suffix.lower() != ".json":
        raise AssignmentError("Output filename must end in .json")
    resolved_output = _resolved(output)
    if OUTPUT_ROOT not in resolved_output.parents:
        raise AssignmentError(
            "Output must be beneath the ignored checkpoints/dev-preview directory"
        )
    if resolved_output in resolved_inputs:
        raise AssignmentError("Output must not overwrite an input report")
    if output.exists():
        raise AssignmentError(f"Output already exists: {output}")
    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise AssignmentError(f"Temporary output already exists: {temporary}")


def load_reports(target_paths):
    reports = {}
    hashes = {}
    for target_id, path in target_paths.items():
        reports[target_id] = _load_json(path)
        hashes[target_id] = _hash_file(path)
    return reports, hashes


def _require_finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssignmentError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise AssignmentError(f"{label} must be finite")
    return number


def _require_sha256(value, label):
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise AssignmentError(
            f"{label} must be exactly 64 hexadecimal SHA-256 characters"
        )
    return value


def _validate_ranking(ranking, candidate_count, label):
    if not isinstance(ranking, dict):
        raise AssignmentError(f"{label} must be an object")
    indexes = ranking.get("temporary_candidate_indexes")
    if not isinstance(indexes, list):
        raise AssignmentError(
            f"{label}.temporary_candidate_indexes must be a list"
        )
    if any(
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= candidate_count
        for index in indexes
    ):
        raise AssignmentError(f"{label} contains an invalid TEMP position")
    if len(indexes) != len(set(indexes)):
        raise AssignmentError(f"{label} repeats a TEMP position")
    top = ranking.get("top_temporary_candidate_index")
    if top is not None and (
        isinstance(top, bool)
        or not isinstance(top, int)
        or top < 0
        or top >= candidate_count
    ):
        raise AssignmentError(
            f"{label}.top_temporary_candidate_index is invalid"
        )
    if indexes and top != indexes[0]:
        raise AssignmentError(
            f"{label}.top_temporary_candidate_index must match the first ranking"
        )
    if not indexes and top is not None:
        raise AssignmentError(
            f"{label}.top_temporary_candidate_index must be null for an empty ranking"
        )


def _validate_frame_payload(prefix, view_name, frame, required_methods):
    frame_number = frame["frame"]
    candidates = frame.get("candidates")
    if not isinstance(candidates, list):
        raise AssignmentError(
            f"{prefix} view {view_name} frame {frame_number}.candidates must be a list"
        )
    rankings = frame.get("rankings")
    if not isinstance(rankings, dict):
        raise AssignmentError(
            f"{prefix} view {view_name} frame {frame_number}.rankings must be an object"
        )
    for method in required_methods:
        if method not in rankings:
            raise AssignmentError(
                f"{prefix} view {view_name} frame {frame_number}.rankings "
                f"is missing {method}"
            )
        _validate_ranking(
            rankings[method],
            len(candidates),
            f"{prefix} view {view_name} frame {frame_number}.rankings.{method}",
        )
    full_gallery_score_presence = []
    for expected_index, candidate in enumerate(candidates):
        label = (
            f"{prefix} view {view_name} frame {frame_number} "
            f"candidate TEMP {expected_index}"
        )
        if not isinstance(candidate, dict):
            raise AssignmentError(f"{label} must be an object")
        if candidate.get("temporary_candidate_index") != expected_index:
            raise AssignmentError(
                f"{label}.temporary_candidate_index must equal its per-frame position"
            )
        box = candidate.get("box_xyxy")
        if not isinstance(box, list) or len(box) != 4:
            raise AssignmentError(f"{label}.box_xyxy must contain four values")
        for value in box:
            _require_finite_number(value, f"{label}.box_xyxy")
        scores = candidate.get("scores")
        if not isinstance(scores, dict):
            raise AssignmentError(f"{label}.scores must be an object")
        for method in required_methods:
            if method not in scores:
                raise AssignmentError(f"{label}.scores is missing {method}")
            if scores[method] is not None:
                _require_finite_number(scores[method], f"{label}.scores.{method}")
        full_gallery_score_presence.append(FULL_GALLERY_METHOD in scores)
    if full_gallery_score_presence and len(set(full_gallery_score_presence)) != 1:
        raise AssignmentError(
            f"{prefix} view {view_name} frame {frame_number} candidates have "
            "inconsistent full-gallery score fields"
        )
    ranking_has_full_gallery = FULL_GALLERY_METHOD in rankings
    candidates_have_full_gallery = (
        full_gallery_score_presence[0] if full_gallery_score_presence else ranking_has_full_gallery
    )
    if ranking_has_full_gallery != candidates_have_full_gallery:
        raise AssignmentError(
            f"{prefix} view {view_name} frame {frame_number} has inconsistent "
            "full-gallery rankings and scores"
        )
    if ranking_has_full_gallery:
        _validate_ranking(
            rankings[FULL_GALLERY_METHOD],
            len(candidates),
            f"{prefix} view {view_name} frame {frame_number}.rankings."
            f"{FULL_GALLERY_METHOD}",
        )
    return ranking_has_full_gallery


def _validate_report(target_id, report):
    prefix = f"Report for target {target_id}"
    if report.get("schema") != APPEARANCE_SCHEMA:
        raise AssignmentError(f"{prefix} has an unsupported schema")
    if report.get("version") != APPEARANCE_VERSION:
        raise AssignmentError(f"{prefix} has an unsupported schema version")
    source = report.get("source")
    model = report.get("model")
    experiment = report.get("experiment")
    views = report.get("views")
    for value, label in (
        (source, "source"),
        (model, "model"),
        (experiment, "experiment"),
        (views, "views"),
    ):
        if not isinstance(value, dict):
            raise AssignmentError(f"{prefix} {label} must be an object")
    for field in ("sidecar_sha256", "weights_sha256", "anchors_sha256"):
        _require_sha256(source.get(field), f"{prefix} source.{field}")
    fps = _require_finite_number(source.get("fps"), f"{prefix} source.fps")
    if fps <= 0:
        raise AssignmentError(f"{prefix} source.fps must be positive")
    interval = source.get("sampling_interval")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
        raise AssignmentError(f"{prefix} source.sampling_interval must be positive")
    architecture = model.get("architecture")
    if not isinstance(architecture, str) or not architecture:
        raise AssignmentError(f"{prefix} model.architecture must be a nonempty string")
    preprocessing = model.get("preprocessing")
    if not isinstance(preprocessing, dict) or not preprocessing:
        raise AssignmentError(f"{prefix} model.preprocessing must be a nonempty object")
    references = experiment.get("reference_anchor_frames")
    if (
        not isinstance(references, list)
        or len(references) != 2
        or any(isinstance(frame, bool) or not isinstance(frame, int) for frame in references)
        or references[0] == references[1]
        or any(frame < 0 for frame in references)
        or references != sorted(references)
    ):
        raise AssignmentError(
            f"{prefix} must contain two distinct ascending non-negative reference frames"
        )
    if not views:
        raise AssignmentError(f"{prefix} has no crop views")
    required_methods = tuple(
        f"similarity_to_anchor_{frame}" for frame in references
    ) + ("endpoint_mean_score",)
    frame_sets = []
    full_gallery_presence = []
    for view_name, view in views.items():
        if not isinstance(view_name, str) or not isinstance(view, dict):
            raise AssignmentError(f"{prefix} contains an invalid crop view")
        frames = view.get("frames")
        if not isinstance(frames, list):
            raise AssignmentError(f"{prefix} view {view_name} frames must be a list")
        numbers = []
        for index, frame in enumerate(frames):
            if (
                not isinstance(frame, dict)
                or isinstance(frame.get("frame"), bool)
                or not isinstance(frame.get("frame"), int)
                or frame["frame"] < 0
            ):
                raise AssignmentError(
                    f"{prefix} view {view_name} frame record {index} is invalid"
                )
            numbers.append(frame["frame"])
            full_gallery_presence.append(
                _validate_frame_payload(prefix, view_name, frame, required_methods)
            )
        if len(numbers) != len(set(numbers)):
            raise AssignmentError(f"{prefix} view {view_name} repeats frame numbers")
        frame_sets.append(set(numbers))
    if any(frame_set != frame_sets[0] for frame_set in frame_sets[1:]):
        raise AssignmentError(f"{prefix} crop views do not contain the same frames")
    if full_gallery_presence and len(set(full_gallery_presence)) != 1:
        raise AssignmentError(
            f"{prefix} has inconsistent full-gallery availability across frames/views"
        )


def _compatibility_value(report, field):
    if field == "sidecar_sha256":
        return report["source"][field]
    if field in ("fps", "sampling_interval", "weights_sha256"):
        return report["source"][field]
    if field == "architecture":
        return report["model"].get(field)
    if field == "preprocessing":
        return report["model"].get(field)
    if field == "reference_anchor_frames":
        return report["experiment"].get(field)
    raise AssertionError(field)


def validate_compatibility(reports):
    for target_id, report in reports.items():
        _validate_report(target_id, report)
    target_ids = sorted(reports)
    baseline = reports[target_ids[0]]
    labels = {
        "sidecar_sha256": "sidecar SHA-256",
        "fps": "FPS",
        "sampling_interval": "sampling interval",
        "weights_sha256": "weights hash",
        "architecture": "model architecture",
        "preprocessing": "preprocessing",
        "reference_anchor_frames": "reference frames",
    }
    for field, label in labels.items():
        expected = _compatibility_value(baseline, field)
        for target_id in target_ids[1:]:
            if _compatibility_value(reports[target_id], field) != expected:
                raise AssignmentError(f"Reports have mismatched {label}")
    view_names = list(baseline["views"])
    expected_views = set(view_names)
    for target_id in target_ids[1:]:
        if set(reports[target_id]["views"]) != expected_views:
            raise AssignmentError("Reports have mismatched crop views")
    references = tuple(baseline["experiment"]["reference_anchor_frames"])
    primary_methods = tuple(
        f"similarity_to_anchor_{frame}" for frame in references
    ) + ("endpoint_mean_score",)
    return {
        "target_ids": target_ids,
        "baseline": baseline,
        "crop_views": view_names,
        "reference_frames": references,
        "primary_methods": primary_methods,
    }


def _frame_map(report, view_name):
    return {frame["frame"]: frame for frame in report["views"][view_name]["frames"]}


def select_frames(reports, crop_views, frame_range):
    frame_sets = {
        target_id: set(_frame_map(report, crop_views[0]))
        for target_id, report in reports.items()
    }
    if frame_range is not None:
        start, end = frame_range
        frame_sets = {
            target_id: {
                frame for frame in target_frames if start <= frame <= end
            }
            for target_id, target_frames in frame_sets.items()
        }
    requested_union = set().union(*frame_sets.values())
    frames = sorted(requested_union)
    if not frames:
        raise AssignmentError("No report frames match the requested range")
    missing_by_target = {
        target_id: sorted(requested_union - target_frames)
        for target_id, target_frames in frame_sets.items()
        if requested_union - target_frames
    }
    if missing_by_target:
        details = "; ".join(
            f"{target_id}={missing_frames}"
            for target_id, missing_frames in sorted(missing_by_target.items())
        )
        raise AssignmentError(
            "Reports have mismatched frame coverage; missing frames by target: "
            f"{details}"
        )
    return frames, []


def _candidate_layout(frame, target_id, view_name):
    candidates = frame.get("candidates")
    if not isinstance(candidates, list):
        raise AssignmentError(
            f"Target {target_id}, view {view_name}, frame {frame.get('frame')} "
            "has no candidate list"
        )
    boxes = []
    for expected_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise AssignmentError("Appearance candidate must be an object")
        if candidate.get("temporary_candidate_index") != expected_index:
            raise AssignmentError(
                "Candidate TEMP positions must be contiguous per-frame positions"
            )
        box = candidate.get("box_xyxy")
        if not isinstance(box, list) or len(box) != 4:
            raise AssignmentError("Candidate box_xyxy must contain four values")
        boxes.append(
            tuple(
                _require_finite_number(
                    value,
                    f"Target {target_id} frame {frame.get('frame')} candidate box",
                )
                for value in box
            )
        )
    return boxes


def validate_candidate_alignment(reports, context, frames):
    target_ids = context["target_ids"]
    for view_name in context["crop_views"]:
        maps = {
            target_id: _frame_map(reports[target_id], view_name)
            for target_id in target_ids
        }
        for frame_number in frames:
            baseline = _candidate_layout(
                maps[target_ids[0]][frame_number], target_ids[0], view_name
            )
            for target_id in target_ids[1:]:
                actual = _candidate_layout(
                    maps[target_id][frame_number], target_id, view_name
                )
                if actual != baseline:
                    raise AssignmentError(
                        f"Mismatched candidate boxes at view {view_name}, "
                        f"frame {frame_number}"
                    )


def _score(candidate, method, label):
    scores = candidate.get("scores")
    if not isinstance(scores, dict) or method not in scores:
        raise AssignmentError(f"Missing {method} score for {label}")
    value = scores[method]
    if value is None:
        return None
    return _require_finite_number(value, f"{label} {method}")


def _serialize_assignment(target_ids, assignment, score_rows):
    return [
        {
            "target_id": target_id,
            "temporary_candidate_index": assignment[index],
            "score": score_rows[index][assignment[index]],
        }
        for index, target_id in enumerate(target_ids)
    ]


def _ground_truth_for_frame(labels, target_ids, frame, candidate_count):
    values = {}
    missing = []
    inconsistent = []
    for target_id in target_ids:
        if target_id not in labels or frame not in labels[target_id]:
            missing.append(target_id)
            continue
        value = labels[target_id][frame]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value >= candidate_count
        ):
            inconsistent.append(target_id)
            continue
        values[target_id] = value
    complete = not missing and not inconsistent
    return values, missing, inconsistent, complete


def _assignment_index_map(serialized_assignment):
    return {
        item["target_id"]: item["temporary_candidate_index"]
        for item in serialized_assignment
    }


def _evaluate_ground_truth(result, labels, target_ids, frame_number, candidate_count):
    ground_truth, missing, inconsistent, complete = _ground_truth_for_frame(
        labels, target_ids, frame_number, candidate_count
    )
    joint = _assignment_index_map(result["best_assignment"])
    independent = {
        item["target_id"]: item["temporary_candidate_index"]
        for item in result["independent_top_choices"]
    }
    exact_tie = result["is_exact_assignment_tie"]
    per_target = []
    for target_id in target_ids:
        label_available = target_id in ground_truth
        target_result = {
            "target_id": target_id,
            "label_available": label_available,
            "correct": None,
        }
        if label_available and not exact_tie:
            target_result["correct"] = joint[target_id] == ground_truth[target_id]
        per_target.append(target_result)

    tie_match = None
    if exact_tie and complete:
        tied_matches = []
        for assignment in result["tied_best_assignments"]:
            indexes = _assignment_index_map(assignment)
            tied_matches.append(
                all(indexes[target_id] == ground_truth[target_id] for target_id in target_ids)
            )
        if all(tied_matches):
            tie_match = "all"
        elif any(tied_matches):
            tie_match = "some"
        else:
            tie_match = "none"

    metrics_available = complete and not exact_tie
    simultaneous = None
    correct_repair = None
    incorrect_forced_target_ids = None
    if metrics_available:
        simultaneous = all(item["correct"] for item in per_target)
        has_collision = bool(result["independent_collisions"])
        correct_repair = has_collision and simultaneous
        incorrect_forced_target_ids = []
        if has_collision:
            for target_id in target_ids:
                label = ground_truth[target_id]
                independently_correct = independent[target_id] == label
                joint_changed_target = joint[target_id] != independent[target_id]
                jointly_incorrect = joint[target_id] != label
                if independently_correct and joint_changed_target and jointly_incorrect:
                    incorrect_forced_target_ids.append(target_id)

    return {
        "available": bool(ground_truth),
        "complete_labels": complete,
        "ambiguous_exact_tie": exact_tie,
        "missing_target_ids": missing,
        "inconsistent_target_ids": inconsistent,
        "per_target": per_target,
        "tied_best_assignments_ground_truth_match": tie_match,
        "simultaneous_all_target_correct": simultaneous,
        "correct_collision_repair": correct_repair,
        "incorrect_forced_target_ids": incorrect_forced_target_ids,
        "incorrect_forced_assignment": (
            bool(incorrect_forced_target_ids)
            if incorrect_forced_target_ids is not None
            else None
        ),
    }


def analyze_method_frame(
    reports,
    target_ids,
    view_name,
    frame_number,
    method,
    labels,
):
    report_frames = [
        _frame_map(reports[target_id], view_name)[frame_number]
        for target_id in target_ids
    ]
    candidate_count = len(report_frames[0]["candidates"])
    result = {
        "frame": frame_number,
        "timestamp_seconds": report_frames[0].get("timestamp_seconds"),
        "candidate_count": candidate_count,
        "evaluable": False,
        "unevaluable_reason": None,
    }
    if candidate_count < len(target_ids):
        result["unevaluable_reason"] = "insufficient-candidates"
        return result
    score_rows = []
    matrix = []
    contains_null = False
    for target_index, target_id in enumerate(target_ids):
        scores = []
        for temporary_index, candidate in enumerate(
            report_frames[target_index]["candidates"]
        ):
            value = _score(
                candidate,
                method,
                f"target {target_id}, view {view_name}, frame {frame_number}, "
                f"TEMP {temporary_index}",
            )
            contains_null = contains_null or value is None
            scores.append(value)
        score_rows.append(scores)
        matrix.append(
            {
                "target_id": target_id,
                "candidate_scores": [
                    {
                        "temporary_candidate_index": index,
                        "score": score,
                    }
                    for index, score in enumerate(scores)
                ],
            }
        )
    result["score_matrix"] = matrix
    if contains_null:
        result["unevaluable_reason"] = "null-scores"
        return result
    possible_count = math.perm(candidate_count, len(target_ids))
    result["calculated_permutation_count"] = possible_count
    if possible_count > MAX_ENUMERATED_ASSIGNMENTS:
        result["unevaluable_reason"] = "assignment-enumeration-limit"
        return result
    independent = []
    selected_tops = {}
    for target_index, target_id in enumerate(target_ids):
        scores = score_rows[target_index]
        maximum = max(scores)
        tied = [index for index, score in enumerate(scores) if score == maximum]
        selected = tied[0]
        selected_tops.setdefault(selected, []).append(target_id)
        independent.append(
            {
                "target_id": target_id,
                "temporary_candidate_index": selected,
                "score": maximum,
                "tied_top_temporary_candidate_indexes": tied,
                "is_tie": len(tied) > 1,
            }
        )
    collisions = [
        {
            "temporary_candidate_index": temporary_index,
            "target_ids": collided_targets,
        }
        for temporary_index, collided_targets in sorted(selected_tops.items())
        if len(collided_targets) > 1
    ]
    assignments = []
    for assignment in itertools.permutations(
        range(candidate_count), len(target_ids)
    ):
        total = sum(
            score_rows[target_index][temporary_index]
            for target_index, temporary_index in enumerate(assignment)
        )
        assignments.append((total, assignment))
    assignments.sort(key=lambda item: (-item[0], item[1]))
    if not assignments:
        result["unevaluable_reason"] = "no-complete-assignment"
        return result
    best_score, best_assignment = assignments[0]
    runner = assignments[1] if len(assignments) > 1 else None
    tied_best = [item for item in assignments if item[0] == best_score]
    result.update(
        {
            "evaluable": True,
            "unevaluable_reason": None,
            "independent_top_choices": independent,
            "independent_collisions": collisions,
            "best_assignment": _serialize_assignment(
                target_ids, best_assignment, score_rows
            ),
            "best_score": best_score,
            "runner_up_assignment": (
                _serialize_assignment(target_ids, runner[1], score_rows)
                if runner is not None
                else None
            ),
            "runner_up_score": runner[0] if runner is not None else None,
            "best_minus_runner_up_margin": (
                best_score - runner[0] if runner is not None else None
            ),
            "is_exact_assignment_tie": len(tied_best) > 1,
            "tied_best_assignments": [
                _serialize_assignment(target_ids, assignment, score_rows)
                for _, assignment in tied_best
            ],
            "enumerated_assignment_count": len(assignments),
            "collision_constraint_applied": bool(collisions),
        }
    )
    result["ground_truth_evaluation"] = _evaluate_ground_truth(
        result, labels, target_ids, frame_number, candidate_count
    )
    return result


def _evaluation_summary(frame_results, target_ids):
    evaluable = [item for item in frame_results if item["evaluable"]]
    margins = [
        item["best_minus_runner_up_margin"]
        for item in evaluable
        if item["best_minus_runner_up_margin"] is not None
    ]
    ground_truth_evaluable = [
        item
        for item in evaluable
        if item.get("ground_truth_evaluation", {}).get("available")
    ]
    complete_labeled = [
        item
        for item in ground_truth_evaluable
        if item["ground_truth_evaluation"]["complete_labels"]
        and not item["ground_truth_evaluation"]["ambiguous_exact_tie"]
    ]
    per_target = {}
    for target_id in target_ids:
        correctness = [
            entry["correct"]
            for item in ground_truth_evaluable
            for entry in item["ground_truth_evaluation"]["per_target"]
            if entry["target_id"] == target_id and entry["correct"] is not None
        ]
        per_target[target_id] = {
            "correct_frames": sum(correctness),
            "evaluated_frames": len(correctness),
        }
    return {
        "total_frames": len(frame_results),
        "evaluable_frames": len(evaluable),
        "unevaluable_frames": len(frame_results) - len(evaluable),
        "independent_collision_frames": sum(
            bool(item.get("independent_collisions")) for item in evaluable
        ),
        "exact_assignment_tie_frames": sum(
            item.get("is_exact_assignment_tie", False) for item in evaluable
        ),
        "minimum_assignment_margin": min(margins) if margins else None,
        "median_assignment_margin": statistics.median(margins) if margins else None,
        "ground_truth": {
            "frames_with_any_valid_labels": len(ground_truth_evaluable),
            "frames_with_complete_labels": len(complete_labeled),
            "exact_tie_ambiguous_frames": sum(
                item.get("is_exact_assignment_tie", False)
                and item.get("ground_truth_evaluation", {}).get("available", False)
                for item in evaluable
            ),
            "per_target": per_target,
            "simultaneous_all_target_correct_frames": sum(
                item["ground_truth_evaluation"][
                    "simultaneous_all_target_correct"
                ]
                for item in complete_labeled
            ),
            "correct_collision_repairs": sum(
                item["ground_truth_evaluation"]["correct_collision_repair"]
                for item in complete_labeled
            ),
            "incorrect_forced_assignments": sum(
                len(item["ground_truth_evaluation"]["incorrect_forced_target_ids"])
                for item in complete_labeled
            ),
            "incorrect_forced_assignment_frames": sum(
                bool(item["ground_truth_evaluation"]["incorrect_forced_target_ids"])
                for item in complete_labeled
            ),
        },
    }


def _label_diagnostics(reports, target_ids, frames, labels, crop_view):
    missing = []
    inconsistent = []
    unused = []
    report_missing = []
    report_mismatches = []
    maps = {
        target_id: _frame_map(reports[target_id], crop_view)
        for target_id in target_ids
    }
    analyzed = set(frames)
    for target_id in target_ids:
        for frame, value in labels.get(target_id, {}).items():
            if frame not in analyzed:
                unused.append(
                    {"target_id": target_id, "frame": frame, "temporary_candidate_index": value}
                )
        for frame in frames:
            provided = labels.get(target_id, {}).get(frame)
            candidate_count = len(maps[target_id][frame]["candidates"])
            if provided is None:
                missing.append({"target_id": target_id, "frame": frame})
            elif provided >= candidate_count:
                inconsistent.append(
                    {
                        "target_id": target_id,
                        "frame": frame,
                        "temporary_candidate_index": provided,
                        "reason": "outside-candidate-list",
                    }
                )
            stored = maps[target_id][frame].get("human_confirmed_label")
            if stored is None:
                report_missing.append({"target_id": target_id, "frame": frame})
            elif provided is not None and stored.get(
                "temporary_candidate_index"
            ) != provided:
                report_mismatches.append(
                    {
                        "target_id": target_id,
                        "frame": frame,
                        "provided_temporary_candidate_index": provided,
                        "report_temporary_candidate_index": stored.get(
                            "temporary_candidate_index"
                        ),
                    }
                )
    return {
        "provided": {
            target_id: [
                {"frame": frame, "temporary_candidate_index": value}
                for frame, value in sorted(labels.get(target_id, {}).items())
            ]
            for target_id in target_ids
        },
        "missing_labels": missing,
        "inconsistent_labels": inconsistent,
        "unused_labels": unused,
        "appearance_report_labels_missing": report_missing,
        "appearance_report_label_mismatches": report_mismatches,
    }


def build_diagnostic(reports, report_hashes, frame_range=None, labels=None):
    labels = labels or {}
    context = validate_compatibility(reports)
    frames, warnings = select_frames(
        reports, context["crop_views"], frame_range
    )
    validate_candidate_alignment(reports, context, frames)
    target_ids = context["target_ids"]
    for target_id in target_ids:
        _require_sha256(
            report_hashes.get(target_id),
            f"Report hash for target {target_id}",
        )
    anchor_hashes = {
        reports[target_id]["source"]["anchors_sha256"]
        for target_id in target_ids
    }
    full_gallery_by_target = {
        target_id: FULL_GALLERY_METHOD
        in reports[target_id]["views"][context["crop_views"][0]]["frames"][0]["rankings"]
        for target_id in target_ids
    }
    full_gallery_present = all(full_gallery_by_target.values())
    full_gallery_comparable = len(anchor_hashes) == 1
    exploratory_methods = []
    if any(full_gallery_by_target.values()) and not full_gallery_present:
        missing_targets = [
            target_id
            for target_id, present in full_gallery_by_target.items()
            if not present
        ]
        warnings.append(
            "Full-gallery assignment omitted because scores are unavailable for "
            f"targets: {missing_targets}."
        )
    if full_gallery_present:
        warning = None
        if not full_gallery_comparable:
            warning = (
                "Full-gallery scores are non-comparable exploratory evidence: "
                "anchor-file hashes differ, and appearance reports do not record "
                "gallery frame composition."
            )
            warnings.append(warning)
        exploratory_methods.append(
            {
                "method": FULL_GALLERY_METHOD,
                "comparable_gallery_composition": full_gallery_comparable,
                "warning": warning,
            }
        )
    methods = list(context["primary_methods"])
    if full_gallery_present:
        methods.append(FULL_GALLERY_METHOD)
    views = {}
    for view_name in context["crop_views"]:
        method_results = {}
        for method in methods:
            frame_results = [
                analyze_method_frame(
                    reports,
                    target_ids,
                    view_name,
                    frame,
                    method,
                    labels,
                )
                for frame in frames
            ]
            method_results[method] = {
                "classification": (
                    "primary"
                    if method in context["primary_methods"]
                    else "exploratory-full-gallery"
                ),
                "frames": frame_results,
                "evaluation_summary": _evaluation_summary(
                    frame_results, target_ids
                ),
            }
        views[view_name] = {"methods": method_results}
    baseline = context["baseline"]
    label_diagnostics = _label_diagnostics(
        reports,
        target_ids,
        frames,
        labels,
        context["crop_views"][0],
    )
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "diagnostic_scope": (
            "Diagnostic-only per-frame appearance assignment. TEMP positions "
            "are not persistent identities; no target track or production "
            "identity decision is produced."
        ),
        "evaluation_definitions": {
            "correct_collision_repair": (
                "A fully labeled, non-tied frame where independent choices "
                "collide and the joint assignment is correct for every target."
            ),
            "incorrect_forced_assignment": (
                "A target on a fully labeled, non-tied collision frame whose "
                "independent choice was correct and whose changed joint choice "
                "is incorrect."
            ),
            "exact_assignment_tie": (
                "Ground-truth accuracy is ambiguous and receives no correctness "
                "credit when multiple complete assignments share the best score."
            ),
        },
        "provenance": {
            "sidecar_sha256": baseline["source"]["sidecar_sha256"],
            "fps": baseline["source"]["fps"],
            "sampling_interval": baseline["source"]["sampling_interval"],
            "weights_sha256": baseline["source"]["weights_sha256"],
            "model_architecture": baseline["model"]["architecture"],
            "preprocessing": baseline["model"]["preprocessing"],
            "reference_anchor_frames": list(context["reference_frames"]),
            "targets": [
                {
                    "target_id": target_id,
                    "appearance_report_sha256": report_hashes[target_id],
                    "anchor_file_sha256": reports[target_id]["source"][
                        "anchors_sha256"
                    ],
                }
                for target_id in target_ids
            ],
        },
        "analyzed_frames": frames,
        "crop_views": list(context["crop_views"]),
        "evidence_methods": {
            "primary": list(context["primary_methods"]),
            "exploratory": exploratory_methods,
        },
        "warnings": warnings,
        "ground_truth": label_diagnostics,
        "views": views,
    }


def write_output(path, diagnostic):
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                diagnostic,
                handle,
                indent=2,
                ensure_ascii=True,
                allow_nan=False,
            )
            handle.write("\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        raise AssignmentError(
            f"Cannot write diagnostic {path}; temporary output may remain: {exc}"
        ) from exc


def main(argv=None):
    args = parse_args(argv)
    try:
        target_paths = parse_target_specs(args.target)
        validate_paths(target_paths, args.output)
        labels = parse_label_specs(args.labels, set(target_paths))
        reports, report_hashes = load_reports(target_paths)
        diagnostic = build_diagnostic(
            reports,
            report_hashes,
            parse_frame_range(args.frame_range),
            labels,
        )
        write_output(args.output, diagnostic)
    except AssignmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved multi-target assignment diagnostic: {args.output}")
    print(f"Targets: {len(target_paths)}")
    print(f"Analyzed frames: {len(diagnostic['analyzed_frames'])}")
    print("TEMP positions remain per-frame diagnostics, not identities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
