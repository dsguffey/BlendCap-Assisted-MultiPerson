"""Fuse geometry and appearance evidence without producing identity decisions."""

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
import stat
import sys
from collections import Counter
from pathlib import Path

import diagnose_preview_multi_target_assignment as joint_tool
import diagnose_preview_target_appearance as appearance_tool
import track_preview_target as tracker


SCHEMA = "blendcap-preview-geometry-appearance-fusion-diagnostic"
VERSION = 1
JOINT_SCHEMA = "blendcap-preview-multi-target-assignment-diagnostic"
JOINT_VERSION = 1
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = (SCRIPT_DIR / "checkpoints" / "dev-preview").resolve()
VALID_STATES = {"anchor", "tracked", "ambiguous", "occluded", "untracked"}


class FusionError(RuntimeError):
    """A clear input-validation or diagnostic failure."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Combine geometry tracks and joint appearance assignments into a "
            "non-authoritative diagnostic report. TEMP positions remain "
            "per-frame positions, not identities."
        )
    )
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--joint-assignment", required=True, type=Path)
    parser.add_argument(
        "--anchors", action="append", required=True, metavar="TARGET_ID=PATH"
    )
    parser.add_argument(
        "--track", action="append", required=True, metavar="TARGET_ID=PATH"
    )
    parser.add_argument(
        "--appearance", action="append", required=True, metavar="TARGET_ID=PATH"
    )
    parser.add_argument("--frame-range", metavar="START:END")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def parse_target_paths(values, option):
    result = {}
    for value in values:
        target_id, separator, path_text = value.partition("=")
        if not separator or not target_id or not path_text:
            raise FusionError(f"{option} must use TARGET_ID=PATH")
        if target_id in result:
            raise FusionError(f"{option} repeats target id {target_id}")
        result[target_id] = Path(path_text)
    if len(result) < 2:
        raise FusionError("At least two unique targets are required")
    return dict(sorted(result.items()))


def parse_frame_range(value):
    if value is None:
        return None
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        start, end = (int(part) for part in parts)
    except ValueError as exc:
        raise FusionError("--frame-range must use START:END") from exc
    if start < 0 or start > end:
        raise FusionError("--frame-range must be non-negative and not reversed")
    return start, end


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_paths(all_input_paths, output):
    resolved_inputs = set()
    for label, path in all_input_paths:
        if not path.is_file():
            raise FusionError(f"{label} does not exist: {path}")
        resolved_inputs.add(_resolved(path))
    if output.suffix.lower() != ".json":
        raise FusionError("Output filename must end in .json")
    resolved_output = _resolved(output)
    if OUTPUT_ROOT not in resolved_output.parents:
        raise FusionError("Output must be beneath checkpoints/dev-preview")
    if resolved_output in resolved_inputs:
        raise FusionError("Output must not overwrite an input")
    if output.exists():
        raise FusionError(f"Output already exists: {output}")
    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise FusionError(f"Temporary output already exists: {temporary}")


def _hash_file(path, label):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FusionError(f"Cannot hash {label} {path}: {exc}") from exc
    return digest.hexdigest()


def _load_json(path, label):
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise FusionError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FusionError(f"{label} must contain a JSON object")
    return value


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FusionError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise FusionError(f"{label} must be finite")
    return number


def _same_number(left, right):
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(left_number) or not math.isfinite(right_number):
        return False
    tolerance = max(0.001, abs(right_number) * 1e-6)
    return abs(left_number - right_number) <= tolerance


def _require_sha256(value, label):
    try:
        joint_tool._require_sha256(value, label)
    except joint_tool.AssignmentError as exc:
        raise FusionError(str(exc)) from exc
    return value.lower()


def _require_target_sets(named_maps, joint_targets):
    if not isinstance(joint_targets, list) or any(
        not isinstance(target_id, str) or not target_id
        for target_id in joint_targets
    ):
        raise FusionError("Expected target ids must be nonempty strings")
    if len(joint_targets) != len(set(joint_targets)):
        raise FusionError("Expected target ids must be unique")
    expected = set(joint_targets)
    if len(expected) < 2:
        raise FusionError("Joint report must contain at least two unique targets")
    for label, mapping in named_maps:
        if not isinstance(mapping, dict) or any(
            not isinstance(target_id, str) or not target_id for target_id in mapping
        ):
            raise FusionError(f"{label} target ids must be nonempty strings")
        actual = set(mapping)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise FusionError(
                f"{label} target ids do not match joint targets; "
                f"missing={missing}, extra={extra}"
            )


def _frame_map(records, label):
    if not isinstance(records, list):
        raise FusionError(f"{label} must be a list")
    result = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise FusionError(f"{label}[{index}] must be an object")
        frame = record.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise FusionError(f"{label}[{index}].frame must be an integer")
        if frame in result:
            raise FusionError(f"{label} repeats frame {frame}")
        result[frame] = record
    return result


def _public_anchor(anchor):
    return tracker._public_anchor(anchor)


def _validate_candidate_reference(
    item, candidates, label, allow_none=False, require_metrics=False
):
    if item is None:
        if allow_none:
            return
        raise FusionError(f"{label} must be an object")
    if not isinstance(item, dict):
        raise FusionError(f"{label} must be an object")
    candidate_index = item.get("temporary_candidate_index")
    box = item.get("box_xyxy")
    if (
        isinstance(candidate_index, bool)
        or not isinstance(candidate_index, int)
        or candidate_index < 0
        or candidate_index >= len(candidates)
    ):
        raise FusionError(f"{label}.temporary_candidate_index is invalid")
    if box != list(candidates[candidate_index]):
        raise FusionError(f"{label}.box_xyxy does not match the sidecar candidate")
    if require_metrics:
        for field in (
            "score",
            "iou",
            "center_displacement_diagonals",
            "area_fold_change",
        ):
            _finite(item.get(field), f"{label}.{field}")


def validate_geometry_track(raw, sidecar, validated_anchors, target_id):
    label = f"Geometry track for target {target_id}"
    source = raw.get("source")
    if not isinstance(source, dict):
        raise FusionError(f"{label}.source must be an object")
    _require_sha256(source.get("sidecar_sha256"), f"{label}.source.sidecar_sha256")
    try:
        appearance_tool.validate_track(raw, sidecar, validated_anchors)
    except appearance_tool.AppearanceError as exc:
        raise FusionError(f"{label}: {exc}") from exc
    expected_anchors = [
        _public_anchor(anchor) for anchor in validated_anchors["anchors"]
    ]
    if raw.get("resolved_anchors") != expected_anchors:
        raise FusionError(f"{label}.resolved_anchors do not match the anchor file")
    if raw.get("forced_occlusion_ranges") != validated_anchors[
        "forced_occlusion_ranges"
    ]:
        raise FusionError(
            f"{label}.forced_occlusion_ranges do not match the anchor file"
        )
    frames = raw.get("frames")
    if not isinstance(frames, list) or len(frames) != len(sidecar["sample_indices"]):
        raise FusionError(f"{label}.frames do not cover every sampled frame")
    forced_frames = validated_anchors["forced_frames"]
    anchor_by_frame = {
        anchor["frame"]: anchor for anchor in validated_anchors["anchors"]
    }
    frame_map = {}
    for position, (expected_frame, record) in enumerate(
        zip(sidecar["sample_indices"], frames)
    ):
        frame_label = f"{label}.frames[{position}]"
        if not isinstance(record, dict) or record.get("frame") != expected_frame:
            raise FusionError(f"{frame_label} is not aligned with the sidecar")
        frame_map[expected_frame] = record
        state = record.get("state")
        if state not in VALID_STATES:
            raise FusionError(f"{frame_label}.state is invalid")
        candidates = sidecar["all_boxes"][position]
        if state in {"anchor", "tracked"}:
            _validate_candidate_reference(record, candidates, frame_label)
        elif record.get("temporary_candidate_index") is not None or record.get(
            "box_xyxy"
        ) is not None:
            raise FusionError(
                f"{frame_label} must not contain an accepted candidate for state {state}"
            )
        hypotheses = record.get("hypotheses")
        if hypotheses is not None:
            if not isinstance(hypotheses, dict):
                raise FusionError(f"{frame_label}.hypotheses must be an object or null")
            for direction in ("forward", "backward"):
                _validate_candidate_reference(
                    hypotheses.get(direction),
                    candidates,
                    f"{frame_label}.hypotheses.{direction}",
                    allow_none=True,
                    require_metrics=True,
                )
        if expected_frame in forced_frames:
            if not (
                state == "occluded"
                and record.get("evidence") == "user-forced"
                and record.get("reason") == "forced-occlusion-range"
                and hypotheses is None
            ):
                raise FusionError(f"{frame_label} does not preserve forced occlusion")
        durable_anchor = anchor_by_frame.get(expected_frame)
        if durable_anchor is not None:
            if state != "anchor":
                raise FusionError(f"{frame_label} does not preserve its durable anchor")
            if (
                record.get("temporary_candidate_index")
                != durable_anchor["temporary_candidate_index"]
                or record.get("box_xyxy") != durable_anchor["box_xyxy"]
            ):
                raise FusionError(
                    f"{frame_label} does not match its resolved durable anchor candidate"
                )
            if (
                record.get("evidence") != "user-anchor"
                or record.get("reason") is not None
                or hypotheses is not None
            ):
                raise FusionError(
                    f"{frame_label} does not match the tracker anchor record schema"
                )
        elif state == "anchor":
            raise FusionError(f"{frame_label} is a spurious non-durable anchor")
        if state == "tracked":
            if record.get("evidence") != "bidirectional" or hypotheses is None:
                raise FusionError(f"{frame_label} is not bidirectionally supported")
            forward = hypotheses.get("forward")
            backward = hypotheses.get("backward")
            if (
                forward is None
                or backward is None
                or forward["temporary_candidate_index"]
                != record["temporary_candidate_index"]
                or backward["temporary_candidate_index"]
                != record["temporary_candidate_index"]
            ):
                raise FusionError(f"{frame_label} has inconsistent bidirectional evidence")
    diagnostics = raw.get("interval_diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) != max(
        0, len(expected_anchors) - 1
    ):
        raise FusionError(f"{label}.interval_diagnostics are invalid")
    sampled = sidecar["sample_indices"]
    allowed_interval_frames = set()
    for diagnostic, left, right in zip(
        diagnostics, validated_anchors["anchors"], validated_anchors["anchors"][1:]
    ):
        if not isinstance(diagnostic, dict):
            raise FusionError(f"{label}.interval_diagnostics entry is invalid")
        if (
            diagnostic.get("start_anchor_id") != left["anchor_id"]
            or diagnostic.get("end_anchor_id") != right["anchor_id"]
        ):
            raise FusionError(
                f"{label}.interval_diagnostics anchor ids do not match durable anchors"
            )
        interior = [frame for frame in sampled if left["frame"] < frame < right["frame"]]
        crosses_forced = any(frame in forced_frames for frame in interior)
        expected_status = (
            "blocked-by-forced-occlusion" if crosses_forced else "evaluated"
        )
        if diagnostic.get("status") != expected_status:
            raise FusionError(
                f"{label}.interval_diagnostics bridges or misstates a forced range"
            )
        if not crosses_forced:
            allowed_interval_frames.update(interior)

    for frame in sampled:
        if frame in forced_frames or frame in anchor_by_frame:
            continue
        record = frame_map[frame]
        state = record["state"]
        if frame in allowed_interval_frames:
            if state not in {"tracked", "ambiguous", "occluded"}:
                raise FusionError(
                    f"{label} frame {frame} is inside an evaluated anchor interval "
                    "but does not contain evaluated geometry"
                )
            continue
        if not (
            state == "untracked"
            and record.get("temporary_candidate_index") is None
            and record.get("box_xyxy") is None
            and record.get("evidence") is None
            and record.get("reason") == "outside-anchor-coverage"
            and record.get("hypotheses") is None
        ):
            raise FusionError(
                f"{label} frame {frame} is outside every allowed anchor interval "
                "and must remain conservatively untracked"
            )


def _validate_joint_provenance(joint, sidecar, target_ids):
    if joint.get("schema") != JOINT_SCHEMA:
        raise FusionError("Joint report has an unsupported schema")
    if joint.get("version") != JOINT_VERSION:
        raise FusionError("Joint report has an unsupported schema version")
    provenance = joint.get("provenance")
    if not isinstance(provenance, dict):
        raise FusionError("Joint report provenance must be an object")
    sidecar_sha256 = _require_sha256(
        provenance.get("sidecar_sha256"),
        "Joint report provenance.sidecar_sha256",
    )
    if sidecar_sha256 != sidecar["sha256"]:
        raise FusionError("Joint report provenance.sidecar_sha256 does not match")
    fps = provenance.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise FusionError("Joint report provenance.fps must be numeric")
    if not math.isfinite(float(fps)) or float(fps) <= 0:
        raise FusionError("Joint report provenance.fps must be positive and finite")
    if not _same_number(fps, sidecar["fps"]):
        raise FusionError("Joint report provenance.fps does not match")
    sampling_interval = provenance.get("sampling_interval")
    if (
        isinstance(sampling_interval, bool)
        or not isinstance(sampling_interval, int)
        or sampling_interval <= 0
    ):
        raise FusionError(
            "Joint report provenance.sampling_interval must be a positive integer"
        )
    if sampling_interval != sidecar["sampling_interval"]:
        raise FusionError("Joint report provenance.sampling_interval does not match")
    targets = provenance.get("targets")
    if not isinstance(targets, list):
        raise FusionError("Joint report provenance.targets must be a list")
    target_map = {}
    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise FusionError(f"Joint report provenance.targets[{index}] is invalid")
        target_id = item.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise FusionError(
                f"Joint report provenance.targets[{index}].target_id is invalid"
            )
        if target_id in target_map:
            raise FusionError(f"Joint report repeats target id {target_id}")
        for field in ("appearance_report_sha256", "anchor_file_sha256"):
            _require_sha256(
                item.get(field),
                f"Joint report provenance target {target_id}.{field}",
            )
        target_map[target_id] = item
    if set(target_map) != set(target_ids):
        raise FusionError("Joint report target ids are incompatible")
    architecture = provenance.get("model_architecture")
    if not isinstance(architecture, str) or not architecture:
        raise FusionError("Joint report provenance.model_architecture is invalid")
    preprocessing = provenance.get("preprocessing")
    if not isinstance(preprocessing, dict) or not preprocessing:
        raise FusionError("Joint report provenance.preprocessing is invalid")
    _require_sha256(
        provenance.get("weights_sha256"), "Joint report provenance.weights_sha256"
    )
    references = provenance.get("reference_anchor_frames")
    if (
        not isinstance(references, list)
        or len(references) != 2
        or any(isinstance(frame, bool) or not isinstance(frame, int) for frame in references)
        or references[0] >= references[1]
    ):
        raise FusionError("Joint report provenance.reference_anchor_frames is invalid")
    crop_views = joint.get("crop_views")
    if (
        not isinstance(crop_views, list)
        or not crop_views
        or any(not isinstance(view, str) or not view for view in crop_views)
        or len(crop_views) != len(set(crop_views))
    ):
        raise FusionError("Joint report crop_views is invalid")
    evidence_methods = joint.get("evidence_methods")
    if not isinstance(evidence_methods, dict):
        raise FusionError("Joint report evidence_methods must be an object")
    primary = evidence_methods.get("primary")
    expected_primary = [
        f"similarity_to_anchor_{references[0]}",
        f"similarity_to_anchor_{references[1]}",
        "endpoint_mean_score",
    ]
    if primary != expected_primary:
        raise FusionError("Joint report primary evidence methods are invalid")
    return {
        "provenance": provenance,
        "targets": target_map,
        "references": references,
        "crop_views": crop_views,
        "primary_methods": expected_primary,
    }


def _validate_appearance_reports(
    reports, report_hashes, anchor_hashes, track_hashes, sidecar, joint_context
):
    try:
        appearance_context = joint_tool.validate_compatibility(reports)
    except joint_tool.AssignmentError as exc:
        raise FusionError(f"Appearance report compatibility: {exc}") from exc
    if list(appearance_context["reference_frames"]) != joint_context["references"]:
        raise FusionError("Appearance and joint reference frames do not match")
    if set(appearance_context["crop_views"]) != set(joint_context["crop_views"]):
        raise FusionError("Appearance and joint crop views do not match")
    provenance = joint_context["provenance"]
    baseline = appearance_context["baseline"]
    if not _same_number(baseline["source"].get("fps"), sidecar["fps"]):
        raise FusionError("Appearance report FPS does not match the sidecar")
    if baseline["source"].get("sampling_interval") != sidecar["sampling_interval"]:
        raise FusionError(
            "Appearance report sampling interval does not match the sidecar"
        )
    if baseline["model"]["architecture"] != provenance["model_architecture"]:
        raise FusionError("Appearance and joint model architecture do not match")
    if baseline["model"]["preprocessing"] != provenance["preprocessing"]:
        raise FusionError("Appearance and joint preprocessing do not match")
    if baseline["source"]["weights_sha256"] != provenance["weights_sha256"]:
        raise FusionError("Appearance and joint weights hash do not match")
    for target_id, report in reports.items():
        recorded = joint_context["targets"][target_id]
        if report_hashes[target_id] != recorded["appearance_report_sha256"]:
            raise FusionError(
                f"Appearance report hash for target {target_id} does not match joint provenance"
            )
        if anchor_hashes[target_id] != recorded["anchor_file_sha256"]:
            raise FusionError(
                f"Anchor hash for target {target_id} does not match joint provenance"
            )
        source = report["source"]
        if source["anchors_sha256"] != anchor_hashes[target_id]:
            raise FusionError(
                f"Appearance source.anchors_sha256 for target {target_id} does not match"
            )
        if source["sidecar_sha256"] != sidecar["sha256"]:
            raise FusionError(
                f"Appearance source.sidecar_sha256 for target {target_id} does not match"
            )
        track_hash = source.get("target_track_sha256")
        if track_hash is not None and track_hash != track_hashes[target_id]:
            raise FusionError(
                f"Appearance source.target_track_sha256 for target {target_id} does not match"
            )
    return appearance_context


def _score_map(row, candidate_count, label):
    if not isinstance(row, dict):
        raise FusionError(f"{label} must be an object")
    scores = row.get("candidate_scores")
    if not isinstance(scores, list) or len(scores) != candidate_count:
        raise FusionError(f"{label}.candidate_scores do not cover every candidate")
    result = {}
    for position, item in enumerate(scores):
        if not isinstance(item, dict) or item.get("temporary_candidate_index") != position:
            raise FusionError(f"{label}.candidate_scores[{position}] is misaligned")
        score = item.get("score")
        if score is not None:
            score = _finite(score, f"{label}.candidate_scores[{position}].score")
        result[position] = score
    return result


def _validate_serialized_assignment(
    assignment, target_ids, score_maps, candidate_count, label
):
    if not isinstance(assignment, list) or len(assignment) != len(target_ids):
        raise FusionError(f"{label} must assign every target exactly once")
    result = {}
    used = set()
    for index, item in enumerate(assignment):
        if not isinstance(item, dict):
            raise FusionError(f"{label}[{index}] must be an object")
        target_id = item.get("target_id")
        candidate = item.get("temporary_candidate_index")
        if target_id not in target_ids or target_id in result:
            raise FusionError(f"{label}[{index}].target_id is invalid or duplicated")
        if (
            isinstance(candidate, bool)
            or not isinstance(candidate, int)
            or candidate < 0
            or candidate >= candidate_count
        ):
            raise FusionError(f"{label}[{index}].temporary_candidate_index is invalid")
        if candidate in used:
            raise FusionError(f"{label} assigns candidate {candidate} more than once")
        expected_score = score_maps[target_id][candidate]
        if item.get("score") != expected_score:
            raise FusionError(f"{label}[{index}].score does not match the score matrix")
        result[target_id] = candidate
        used.add(candidate)
    if set(result) != set(target_ids):
        raise FusionError(f"{label} has an incompatible target set")
    return result


USABLE_ASSIGNMENT_FIELDS = (
    "independent_top_choices",
    "independent_collisions",
    "best_assignment",
    "best_score",
    "runner_up_assignment",
    "runner_up_score",
    "best_minus_runner_up_margin",
    "is_exact_assignment_tie",
    "tied_best_assignments",
    "enumerated_assignment_count",
    "collision_constraint_applied",
)


def _require_no_usable_assignment_claims(frame, label):
    present = [field for field in USABLE_ASSIGNMENT_FIELDS if frame.get(field) is not None]
    if present:
        raise FusionError(
            f"{label} is unevaluable but contains usable assignment fields: {present}"
        )


def _validate_independent_claims(frame, target_ids, score_maps, label):
    raw = frame.get("independent_top_choices")
    if not isinstance(raw, list) or len(raw) != len(target_ids):
        raise FusionError(f"{label}.independent_top_choices is invalid")
    actual = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise FusionError(
                f"{label}.independent_top_choices[{index}] must be an object"
            )
        target_id = item.get("target_id")
        if target_id not in target_ids or target_id in actual:
            raise FusionError(
                f"{label}.independent_top_choices[{index}].target_id is invalid"
            )
        scores = score_maps[target_id]
        maximum = max(scores.values())
        tied = sorted(candidate for candidate, score in scores.items() if score == maximum)
        expected = {
            "temporary_candidate_index": tied[0],
            "score": maximum,
            "tied_top_temporary_candidate_indexes": tied,
            "is_tie": len(tied) > 1,
        }
        for field, value in expected.items():
            if item.get(field) != value:
                raise FusionError(
                    f"{label}.independent_top_choices target {target_id}.{field} is inconsistent"
                )
        actual[target_id] = tied[0]
    collisions = {}
    for target_id, candidate in actual.items():
        collisions.setdefault(candidate, []).append(target_id)
    expected_collisions = {
        (candidate, tuple(sorted(collided_targets)))
        for candidate, collided_targets in collisions.items()
        if len(collided_targets) > 1
    }
    raw_collisions = frame.get("independent_collisions")
    if not isinstance(raw_collisions, list):
        raise FusionError(f"{label}.independent_collisions must be a list")
    actual_collisions = set()
    for index, item in enumerate(raw_collisions):
        if not isinstance(item, dict):
            raise FusionError(
                f"{label}.independent_collisions[{index}] must be an object"
            )
        candidate = item.get("temporary_candidate_index")
        collided_targets = item.get("target_ids")
        if (
            isinstance(candidate, bool)
            or not isinstance(candidate, int)
            or not isinstance(collided_targets, list)
            or len(collided_targets) < 2
            or any(target not in target_ids for target in collided_targets)
            or len(collided_targets) != len(set(collided_targets))
        ):
            raise FusionError(
                f"{label}.independent_collisions[{index}] is invalid"
            )
        actual_collisions.add((candidate, tuple(sorted(collided_targets))))
    if len(actual_collisions) != len(raw_collisions) or actual_collisions != expected_collisions:
        raise FusionError(f"{label}.independent_collisions is inconsistent")
    if frame.get("collision_constraint_applied") != bool(expected_collisions):
        raise FusionError(f"{label}.collision_constraint_applied is inconsistent")


def _validate_recomputed_assignment_claims(
    frame, target_ids, score_maps, candidate_count, label
):
    permutation_count = math.perm(candidate_count, len(target_ids))
    if permutation_count > joint_tool.MAX_ENUMERATED_ASSIGNMENTS:
        raise FusionError(
            f"{label} requires {permutation_count} assignments, exceeding the "
            f"limit {joint_tool.MAX_ENUMERATED_ASSIGNMENTS}"
        )
    assignments = []
    for candidate_assignment in itertools.permutations(
        range(candidate_count), len(target_ids)
    ):
        total = sum(
            score_maps[target_id][candidate_assignment[index]]
            for index, target_id in enumerate(target_ids)
        )
        assignments.append((total, candidate_assignment))
    assignments.sort(key=lambda item: (-item[0], item[1]))
    if not assignments:
        raise FusionError(f"{label} has no complete injective assignment")
    best_score, best_signature = assignments[0]
    runner_score, runner_signature = assignments[1]
    tied_signatures = [
        signature for score, signature in assignments if score == best_score
    ]
    best = _validate_serialized_assignment(
        frame.get("best_assignment"),
        target_ids,
        score_maps,
        candidate_count,
        f"{label}.best_assignment",
    )
    expected_best = dict(zip(target_ids, best_signature))
    if best != expected_best:
        raise FusionError(f"{label}.best_assignment is not the canonical optimum")
    runner = _validate_serialized_assignment(
        frame.get("runner_up_assignment"),
        target_ids,
        score_maps,
        candidate_count,
        f"{label}.runner_up_assignment",
    )
    expected_runner = dict(zip(target_ids, runner_signature))
    if runner != expected_runner or runner == best:
        raise FusionError(f"{label}.runner_up_assignment is not genuinely second-best")
    expected_margin = best_score - runner_score
    for field, expected in (
        ("best_score", best_score),
        ("runner_up_score", runner_score),
        ("best_minus_runner_up_margin", expected_margin),
    ):
        value = frame.get(field)
        _finite(value, f"{label}.{field}")
        if value != expected:
            raise FusionError(f"{label}.{field} does not match recomputation")
    tied_raw = frame.get("tied_best_assignments")
    if not isinstance(tied_raw, list) or not tied_raw:
        raise FusionError(f"{label}.tied_best_assignments is invalid")
    tied_actual = []
    for index, assignment in enumerate(tied_raw):
        normalized = _validate_serialized_assignment(
            assignment,
            target_ids,
            score_maps,
            candidate_count,
            f"{label}.tied_best_assignments[{index}]",
        )
        tied_actual.append(tuple(normalized[target_id] for target_id in target_ids))
    if len(tied_actual) != len(set(tied_actual)) or set(tied_actual) != set(
        tied_signatures
    ):
        raise FusionError(
            f"{label}.tied_best_assignments is incomplete or contains extras"
        )
    expected_tie = len(tied_signatures) > 1
    if frame.get("is_exact_assignment_tie") is not expected_tie:
        raise FusionError(f"{label}.is_exact_assignment_tie is inconsistent")
    if expected_margin == 0.0 and not expected_tie:
        raise FusionError(f"{label} has an unflagged zero-margin assignment tie")
    if frame.get("calculated_permutation_count") != permutation_count:
        raise FusionError(f"{label}.calculated_permutation_count is inconsistent")
    if frame.get("enumerated_assignment_count") != permutation_count:
        raise FusionError(f"{label}.enumerated_assignment_count is inconsistent")
    _validate_independent_claims(frame, target_ids, score_maps, label)


def validate_joint_frames(joint, reports, sidecar, joint_context):
    analyzed = joint.get("analyzed_frames")
    if not isinstance(analyzed, list) or not analyzed:
        raise FusionError("Joint report analyzed_frames must be a nonempty list")
    if any(isinstance(frame, bool) or not isinstance(frame, int) for frame in analyzed):
        raise FusionError("Joint report analyzed_frames must contain only integers")
    if len(analyzed) != len(set(analyzed)) or analyzed != sorted(analyzed):
        raise FusionError("Joint report analyzed_frames must be unique and sorted")
    sampled = set(sidecar["sample_indices"])
    if any(frame not in sampled for frame in analyzed):
        raise FusionError("Joint report contains a frame absent from the sidecar")
    target_ids = sorted(joint_context["targets"])
    views = joint.get("views")
    if not isinstance(views, dict) or set(views) != set(joint_context["crop_views"]):
        raise FusionError("Joint report views do not match crop-view provenance")
    normalized = {}
    for view_name in joint_context["crop_views"]:
        view = views[view_name]
        methods = view.get("methods") if isinstance(view, dict) else None
        if not isinstance(methods, dict):
            raise FusionError(f"Joint view {view_name}.methods must be an object")
        missing_primary = [
            method
            for method in joint_context["primary_methods"]
            if method not in methods
        ]
        if missing_primary:
            raise FusionError(
                f"Joint view {view_name}.methods is missing primary methods: {missing_primary}"
            )
        normalized[view_name] = {}
        for method_name, method in methods.items():
            if not isinstance(method, dict):
                raise FusionError(f"Joint method {view_name}.{method_name} is invalid")
            classification = method.get("classification")
            if method_name in joint_context["primary_methods"]:
                if classification != "primary":
                    raise FusionError(
                        f"Joint method {view_name}.{method_name} is not primary"
                    )
            elif method_name == joint_tool.FULL_GALLERY_METHOD:
                if classification != "exploratory-full-gallery":
                    raise FusionError("Joint full-gallery method is not exploratory")
            else:
                raise FusionError(f"Joint report contains unknown method {method_name}")
            frame_map = _frame_map(
                method.get("frames"), f"Joint {view_name}.{method_name}.frames"
            )
            if set(frame_map) != set(analyzed):
                raise FusionError(
                    f"Joint {view_name}.{method_name} frame coverage is inconsistent"
                )
            normalized[view_name][method_name] = frame_map
            for frame_number, frame in frame_map.items():
                position = sidecar["frame_to_position"][frame_number]
                candidates = sidecar["all_boxes"][position]
                candidate_count = len(candidates)
                if frame.get("candidate_count") != candidate_count:
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number} candidate_count does not match"
                    )
                evaluable = frame.get("evaluable")
                if not isinstance(evaluable, bool):
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number}.evaluable is invalid"
                    )
                unevaluable_reason = frame.get("unevaluable_reason")
                if (
                    not evaluable
                    and unevaluable_reason == "insufficient-candidates"
                ):
                    if candidate_count >= len(target_ids):
                        raise FusionError(
                            f"Joint {view_name}.{method_name} frame {frame_number} "
                            "incorrectly reports insufficient candidates"
                        )
                    if frame.get("score_matrix") is not None:
                        raise FusionError(
                            f"Joint {view_name}.{method_name} frame {frame_number}.score_matrix "
                            "must be absent for insufficient candidates"
                        )
                    _require_no_usable_assignment_claims(
                        frame,
                        f"Joint {view_name}.{method_name} frame {frame_number}",
                    )
                    for target_id in target_ids:
                        appearance_frames = _frame_map(
                            reports[target_id]["views"][view_name]["frames"],
                            f"Appearance target {target_id} view {view_name}.frames",
                        )
                        appearance_frame = appearance_frames.get(frame_number)
                        appearance_candidates = (
                            appearance_frame.get("candidates")
                            if isinstance(appearance_frame, dict)
                            else None
                        )
                        if not isinstance(appearance_candidates, list) or len(
                            appearance_candidates
                        ) != candidate_count:
                            raise FusionError(
                                f"Appearance target {target_id} frame {frame_number} candidates are invalid"
                            )
                        for candidate_index, appearance_candidate in enumerate(
                            appearance_candidates
                        ):
                            if (
                                appearance_candidate.get(
                                    "temporary_candidate_index"
                                )
                                != candidate_index
                                or appearance_candidate.get("box_xyxy")
                                != list(candidates[candidate_index])
                            ):
                                raise FusionError(
                                    f"Appearance target {target_id} view {view_name} frame {frame_number} candidate {candidate_index} does not match the sidecar"
                                )
                    continue
                if candidate_count < len(target_ids):
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number} "
                        "must report insufficient candidates"
                    )
                matrix = frame.get("score_matrix")
                if not isinstance(matrix, list) or len(matrix) != len(target_ids):
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number}.score_matrix is invalid"
                    )
                score_maps = {}
                for row_index, row in enumerate(matrix):
                    target_id = row.get("target_id") if isinstance(row, dict) else None
                    if target_id not in target_ids or target_id in score_maps:
                        raise FusionError(
                            f"Joint {view_name}.{method_name} frame {frame_number}.score_matrix[{row_index}] target is invalid"
                        )
                    score_maps[target_id] = _score_map(
                        row,
                        candidate_count,
                        f"Joint {view_name}.{method_name} frame {frame_number} target {target_id}",
                    )
                for target_id in target_ids:
                    appearance_frames = _frame_map(
                        reports[target_id]["views"][view_name]["frames"],
                        f"Appearance target {target_id} view {view_name}.frames",
                    )
                    if frame_number not in appearance_frames:
                        raise FusionError(
                            f"Appearance target {target_id} lacks joint frame {frame_number}"
                        )
                    appearance_frame = appearance_frames[frame_number]
                    appearance_candidates = appearance_frame.get("candidates")
                    if not isinstance(appearance_candidates, list) or len(
                        appearance_candidates
                    ) != candidate_count:
                        raise FusionError(
                            f"Appearance target {target_id} frame {frame_number} candidates are invalid"
                        )
                    for candidate_index, appearance_candidate in enumerate(
                        appearance_candidates
                    ):
                        if (
                            appearance_candidate.get("temporary_candidate_index")
                            != candidate_index
                            or appearance_candidate.get("box_xyxy")
                            != list(candidates[candidate_index])
                        ):
                            raise FusionError(
                                f"Appearance target {target_id} view {view_name} frame {frame_number} candidate {candidate_index} does not match the sidecar"
                            )
                        appearance_score = appearance_candidate.get("scores", {}).get(
                            method_name
                        )
                        if appearance_score != score_maps[target_id][candidate_index]:
                            raise FusionError(
                                f"Joint score for target {target_id}, view {view_name}, method {method_name}, frame {frame_number}, candidate {candidate_index} does not match appearance"
                            )
                contains_null = any(
                    score is None
                    for scores in score_maps.values()
                    for score in scores.values()
                )
                assignment_count = math.perm(candidate_count, len(target_ids))
                if assignment_count > joint_tool.MAX_ENUMERATED_ASSIGNMENTS:
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number} requires "
                        f"{assignment_count} assignments, exceeding the limit "
                        f"{joint_tool.MAX_ENUMERATED_ASSIGNMENTS}"
                    )
                if contains_null:
                    if evaluable or unevaluable_reason != "null-scores":
                        raise FusionError(
                            f"Joint {view_name}.{method_name} frame {frame_number} "
                            "must be unevaluable with reason null-scores"
                        )
                    _require_no_usable_assignment_claims(
                        frame,
                        f"Joint {view_name}.{method_name} frame {frame_number}",
                    )
                    continue
                if not evaluable or unevaluable_reason is not None:
                    raise FusionError(
                        f"Joint {view_name}.{method_name} frame {frame_number} has usable "
                        "scores but is not coherently evaluable"
                    )
                _validate_recomputed_assignment_claims(
                    frame,
                    target_ids,
                    score_maps,
                    candidate_count,
                    f"Joint {view_name}.{method_name} frame {frame_number}",
                )
    return {"analyzed_frames": analyzed, "views": normalized}


def load_and_validate(
    sidecar_path,
    joint_path,
    anchor_paths,
    track_paths,
    appearance_paths,
):
    sidecar_raw = _load_json(sidecar_path, "Sidecar")
    try:
        sidecar = tracker.validate_sidecar(sidecar_raw, _hash_file(sidecar_path, "sidecar"))
    except tracker.TrackError as exc:
        raise FusionError(f"Sidecar validation: {exc}") from exc
    joint = _load_json(joint_path, "Joint assignment report")
    provisional_provenance = joint.get("provenance")
    if not isinstance(provisional_provenance, dict):
        raise FusionError("Joint report provenance must be an object")
    target_ids = list(anchor_paths)
    _require_target_sets(
        (
            ("Anchor inputs", anchor_paths),
            ("Track inputs", track_paths),
            ("Appearance inputs", appearance_paths),
        ),
        target_ids,
    )
    target_ids.sort()
    try:
        joint_context = _validate_joint_provenance(joint, sidecar, target_ids)
    except joint_tool.AssignmentError as exc:
        raise FusionError(f"Joint report validation: {exc}") from exc
    anchors_raw = {}
    anchors = {}
    tracks = {}
    appearances = {}
    hashes = {"anchors": {}, "tracks": {}, "appearances": {}}
    raw_warnings = joint.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(
        not isinstance(warning, str) for warning in raw_warnings
    ):
        raise FusionError("Joint report warnings must be a list of strings")
    warnings = list(raw_warnings)
    for target_id in target_ids:
        anchors_raw[target_id] = _load_json(
            anchor_paths[target_id], f"Anchor file for target {target_id}"
        )
        anchor_source = anchors_raw[target_id].get("source")
        if not isinstance(anchor_source, dict):
            raise FusionError(
                f"Anchor file for target {target_id}.source must be an object"
            )
        for field in ("sidecar_sha256", "fps", "sampling_interval"):
            if field not in anchor_source:
                raise FusionError(
                    f"Anchor file for target {target_id}.source is missing {field}"
                )
        hashes["anchors"][target_id] = _hash_file(
            anchor_paths[target_id], f"anchor file for target {target_id}"
        )
        try:
            anchors[target_id] = tracker.validate_anchors(
                anchors_raw[target_id], sidecar
            )
        except tracker.TrackError as exc:
            raise FusionError(f"Anchor validation for target {target_id}: {exc}") from exc
        tracks[target_id] = _load_json(
            track_paths[target_id], f"Geometry track for target {target_id}"
        )
        hashes["tracks"][target_id] = _hash_file(
            track_paths[target_id], f"geometry track for target {target_id}"
        )
        validate_geometry_track(tracks[target_id], sidecar, anchors[target_id], target_id)
        appearances[target_id] = _load_json(
            appearance_paths[target_id], f"Appearance report for target {target_id}"
        )
        hashes["appearances"][target_id] = _hash_file(
            appearance_paths[target_id], f"appearance report for target {target_id}"
        )
    for frame in sidecar["sample_indices"]:
        reservations = []
        for target_id in target_ids:
            record = tracks[target_id]["frames"][sidecar["frame_to_position"][frame]]
            if record["state"] in {"anchor", "tracked"}:
                reservations.append(
                    (target_id, record["temporary_candidate_index"])
                )
        duplicated = [
            candidate
            for candidate, count in Counter(
                candidate for _, candidate in reservations
            ).items()
            if count > 1
        ]
        if duplicated:
            raise FusionError(
                f"Frame {frame} has conflicting authoritative candidate reservations: {duplicated}"
            )
    appearance_context = _validate_appearance_reports(
        appearances,
        hashes["appearances"],
        hashes["anchors"],
        hashes["tracks"],
        sidecar,
        joint_context,
    )
    for target_id in target_ids:
        source = appearances[target_id].get("source")
        if not isinstance(source, dict):
            raise FusionError(
                f"Appearance report for target {target_id}.source must be an object"
            )
        if source.get("target_track_sha256") is None:
            warnings.append(
                f"Appearance report for target {target_id} has null track provenance; "
                "the supplied geometry track was validated independently."
            )
    normalized_joint = validate_joint_frames(
        joint, appearances, sidecar, joint_context
    )
    return {
        "sidecar": sidecar,
        "joint": joint,
        "joint_context": joint_context,
        "normalized_joint": normalized_joint,
        "anchors": anchors,
        "tracks": tracks,
        "appearances": appearances,
        "appearance_context": appearance_context,
        "hashes": {
            **hashes,
            "sidecar": sidecar["sha256"],
            "joint": _hash_file(joint_path, "joint assignment report"),
        },
        "warnings": list(dict.fromkeys(warnings)),
        "target_ids": target_ids,
    }


def _selected_frames(sidecar, frame_range):
    frames = sidecar["sample_indices"]
    if frame_range is not None:
        start, end = frame_range
        positions = sidecar["frame_to_position"]
        if start not in positions:
            raise FusionError(
                f"--frame-range START {start} must be an exact sampled sidecar frame"
            )
        if end not in positions:
            raise FusionError(
                f"--frame-range END {end} must be an exact sampled sidecar frame"
            )
        if start > end:
            raise FusionError("--frame-range must not be reversed")
        frames = frames[positions[start] : positions[end] + 1]
    if not frames:
        raise FusionError("No sampled sidecar frames match the requested range")
    return frames


def _distance_to_anchors(frame, sidecar, anchors, forced_ranges):
    position = sidecar["frame_to_position"][frame]
    previous = [anchor for anchor in anchors if anchor["frame"] <= frame]
    following = [anchor for anchor in anchors if anchor["frame"] >= frame]

    def record(anchor):
        if anchor is None:
            return None
        anchor_position = sidecar["frame_to_position"][anchor["frame"]]
        distance_frames = abs(frame - anchor["frame"])
        return {
            "anchor_id": anchor["anchor_id"],
            "frame": anchor["frame"],
            "distance_source_frames": distance_frames,
            "distance_sampled_steps": abs(position - anchor_position),
            "distance_seconds": distance_frames / sidecar["fps"],
        }

    def chronological_record(anchor):
        value = record(anchor)
        if value is None:
            return None
        low = min(frame, anchor["frame"])
        high = max(frame, anchor["frame"])
        blocking_range = next(
            (
                item
                for item in forced_ranges
                if item["start_frame"] <= high and item["end_frame"] >= low
            ),
            None,
        )
        return {
            **value,
            "usable_for_threshold_evidence": blocking_range is None,
            "boundary_blocked": blocking_range is not None,
            "blocking_forced_range": (
                dict(blocking_range) if blocking_range is not None else None
            ),
        }

    chronological_previous = chronological_record(previous[-1] if previous else None)
    chronological_next = chronological_record(following[0] if following else None)

    return {
        "previous": (
            None
            if chronological_previous is None
            or chronological_previous["boundary_blocked"]
            else {
                key: value
                for key, value in chronological_previous.items()
                if key
                not in {
                    "usable_for_threshold_evidence",
                    "boundary_blocked",
                    "blocking_forced_range",
                }
            }
        ),
        "next": (
            None
            if chronological_next is None or chronological_next["boundary_blocked"]
            else {
                key: value
                for key, value in chronological_next.items()
                if key
                not in {
                    "usable_for_threshold_evidence",
                    "boundary_blocked",
                    "blocking_forced_range",
                }
            }
        ),
        "chronological": {
            "previous": chronological_previous,
            "next": chronological_next,
        },
    }


def _forced_status(frame, ranges):
    containing = next(
        (
            item
            for item in ranges
            if item["start_frame"] <= frame <= item["end_frame"]
        ),
        None,
    )
    return {
        "is_user_forced": containing is not None,
        "range": dict(containing) if containing is not None else None,
    }


def _geometry_copy(record):
    return copy.deepcopy({
        "state": record["state"],
        "reason": record.get("reason"),
        "evidence": record.get("evidence"),
        "temporary_candidate_index": record.get("temporary_candidate_index"),
        "box_xyxy": record.get("box_xyxy"),
        "hypotheses": record.get("hypotheses"),
    })


def _individual_margin(appearance_frame, method):
    rankings = appearance_frame["rankings"][method]
    return rankings.get("top_two_margin")


def _method_evidence(
    frame_record,
    target_ids,
    appearance_frames,
    view_name,
    method_name,
    reservations,
):
    if frame_record is None:
        return {
            "available": False,
            "eligible_for_proposal": method_name != joint_tool.FULL_GALLERY_METHOD,
            "status": "missing-joint-frame",
        }
    evidence = {
        "available": True,
        "eligible_for_proposal": method_name != joint_tool.FULL_GALLERY_METHOD,
        "evaluable": frame_record["evaluable"],
        "unevaluable_reason": frame_record.get("unevaluable_reason"),
        "is_exact_assignment_tie": frame_record.get("is_exact_assignment_tie"),
        "best_assignment": copy.deepcopy(frame_record.get("best_assignment")),
        "best_score": frame_record.get("best_score"),
        "runner_up_assignment": copy.deepcopy(
            frame_record.get("runner_up_assignment")
        ),
        "runner_up_score": frame_record.get("runner_up_score"),
        "best_minus_runner_up_margin": frame_record.get(
            "best_minus_runner_up_margin"
        ),
        "independent_top_choices": copy.deepcopy(
            frame_record.get("independent_top_choices")
        ),
        "independent_collisions": copy.deepcopy(
            frame_record.get("independent_collisions")
        ),
        "target_measurements": [],
        "compatibility": {
            "status": None,
            "conflicting_target_ids": [],
        },
    }
    if method_name == joint_tool.FULL_GALLERY_METHOD:
        evidence["status"] = "exploratory-not-proposal-evidence"
    elif not frame_record["evaluable"]:
        evidence["status"] = frame_record.get("unevaluable_reason") or "unevaluable"
    elif frame_record.get("is_exact_assignment_tie"):
        evidence["status"] = "exact-assignment-tie"
    else:
        evidence["status"] = "comparable-primary-evidence"
    if not frame_record["evaluable"]:
        return evidence
    assignment = _assignment_map(frame_record)
    score_rows = {
        row["target_id"]: {
            item["temporary_candidate_index"]: item["score"]
            for item in row["candidate_scores"]
        }
        for row in frame_record["score_matrix"]
    }
    independent = {
        item["target_id"]: item
        for item in frame_record["independent_top_choices"]
    }
    conflicts = []
    for target_id in target_ids:
        assigned = assignment[target_id]
        assigned_index = assigned["temporary_candidate_index"]
        scores = score_rows[target_id]
        alternatives = [
            score
            for candidate, score in scores.items()
            if candidate != assigned_index and score is not None
        ]
        best_alternative = max(alternatives) if alternatives else None
        appearance_frame = appearance_frames[target_id][view_name]
        evidence["target_measurements"].append(
            {
                "target_id": target_id,
                "candidate_scores": [
                    {
                        "temporary_candidate_index": candidate,
                        "score": score,
                    }
                    for candidate, score in sorted(scores.items())
                ],
                "independent_top_temporary_candidate_index": independent[target_id][
                    "temporary_candidate_index"
                ],
                "independent_top_score": independent[target_id]["score"],
                "independent_top_is_tie": independent[target_id]["is_tie"],
                "assigned_temporary_candidate_index": assigned_index,
                "assigned_score": assigned["score"],
                "best_alternative_score": best_alternative,
                "assigned_minus_best_alternative_margin": (
                    assigned["score"] - best_alternative
                    if best_alternative is not None
                    else None
                ),
                "individual_appearance_top_two_margin": _individual_margin(
                    appearance_frame, method_name
                ),
            }
        )
        if target_id in reservations and assigned_index != reservations[target_id]:
            conflicts.append(target_id)
    evidence["compatibility"]["conflicting_target_ids"] = conflicts
    evidence["compatibility"]["status"] = (
        "incompatible-with-accepted-geometry" if conflicts else "compatible"
    )
    if conflicts and method_name != joint_tool.FULL_GALLERY_METHOD:
        evidence["status"] = "incompatible-with-accepted-geometry"
    return evidence


def _joint_frame_evidence(context, frame, reservations):
    joint_frames = set(context["normalized_joint"]["analyzed_frames"])
    target_ids = context["target_ids"]
    result = {
        "available": frame in joint_frames,
        "threshold_status": "not-configured",
        "crop_view_agreement": {},
        "reference_method_agreement": {},
        "views": {},
    }
    appearance_frames = {
        target_id: {
            view_name: _frame_map(
                context["appearances"][target_id]["views"][view_name]["frames"],
                f"Appearance target {target_id} view {view_name}.frames",
            ).get(frame)
            for view_name in context["joint_context"]["crop_views"]
        }
        for target_id in target_ids
    }
    if frame not in joint_frames:
        result["status"] = "missing-joint-frame"
        return result
    for view_name in context["joint_context"]["crop_views"]:
        result["views"][view_name] = {"methods": {}}
        for method_name, method_frames in context["normalized_joint"]["views"][
            view_name
        ].items():
            result["views"][view_name]["methods"][method_name] = _method_evidence(
                method_frames.get(frame),
                target_ids,
                appearance_frames,
                view_name,
                method_name,
                reservations,
            )
    references = context["joint_context"]["primary_methods"][:2]
    for target_id in target_ids:
        per_view_reference = {}
        for view_name in context["joint_context"]["crop_views"]:
            methods = result["views"][view_name]["methods"]
            candidates = []
            for method in references:
                evidence = methods[method]
                if evidence.get("evaluable") and not evidence.get(
                    "is_exact_assignment_tie"
                ):
                    candidates.append(
                        _assignment_map(evidence)[target_id][
                            "temporary_candidate_index"
                        ]
                    )
            per_view_reference[view_name] = (
                len(candidates) == len(references) and len(set(candidates)) == 1
            )
        result["reference_method_agreement"][target_id] = per_view_reference
    for target_id in target_ids:
        per_method_crop = {}
        for method_name in context["joint_context"]["primary_methods"]:
            candidates = []
            for view_name in context["joint_context"]["crop_views"]:
                evidence = result["views"][view_name]["methods"][method_name]
                if evidence.get("evaluable") and not evidence.get(
                    "is_exact_assignment_tie"
                ):
                    candidates.append(
                        _assignment_map(evidence)[target_id][
                            "temporary_candidate_index"
                        ]
                    )
            per_method_crop[method_name] = (
                len(candidates) == len(context["joint_context"]["crop_views"])
                and len(set(candidates)) == 1
            )
        result["crop_view_agreement"][target_id] = per_method_crop
    result["status"] = "available"
    return result


def _assignment_map(evidence):
    return {
        item["target_id"]: item for item in evidence.get("best_assignment", [])
    }


def _authoritative_appearance_relation(
    context, target_id, joint_evidence, reserved_candidate
):
    """Describe primary appearance evidence without changing geometry authority."""
    if not joint_evidence["available"]:
        return {
            "status": "indeterminate",
            "reason": "missing-joint-frame",
            "primary_measurement_count": 0,
        }
    assigned_candidates = []
    unavailable_reasons = []
    for view_name in context["joint_context"]["crop_views"]:
        for method_name in context["joint_context"]["primary_methods"]:
            evidence = joint_evidence["views"][view_name]["methods"][method_name]
            if not evidence.get("evaluable"):
                unavailable_reasons.append(
                    evidence.get("unevaluable_reason") or "unevaluable"
                )
            elif evidence.get("is_exact_assignment_tie"):
                unavailable_reasons.append("exact-assignment-tie")
            else:
                assigned_candidates.append(
                    _assignment_map(evidence)[target_id][
                        "temporary_candidate_index"
                    ]
                )
    if unavailable_reasons:
        return {
            "status": "indeterminate",
            "reason": sorted(set(unavailable_reasons))[0],
            "primary_measurement_count": len(assigned_candidates),
        }
    agrees = [candidate == reserved_candidate for candidate in assigned_candidates]
    if agrees and all(agrees):
        status = "corroborating"
    elif agrees and not any(agrees):
        status = "conflicting"
    else:
        status = "mixed"
    return {
        "status": status,
        "reason": None,
        "primary_measurement_count": len(assigned_candidates),
    }


def _proposal_for_target(context, target_id, joint_evidence, reservations):
    if not joint_evidence["available"]:
        return {"status": "no-proposal", "reason": "missing-joint-frame"}
    candidates = []
    failures = []
    for view_name in context["joint_context"]["crop_views"]:
        methods = joint_evidence["views"][view_name]["methods"]
        for method_name in context["joint_context"]["primary_methods"]:
            evidence = methods[method_name]
            if not evidence.get("available"):
                failures.append("missing-joint-evidence")
            elif not evidence.get("evaluable"):
                failures.append(evidence.get("unevaluable_reason") or "unevaluable")
            elif evidence.get("is_exact_assignment_tie"):
                failures.append("exact-assignment-tie")
            elif evidence["compatibility"]["status"] != "compatible":
                failures.append("incompatible-with-accepted-geometry")
            else:
                candidates.append(
                    _assignment_map(evidence)[target_id][
                        "temporary_candidate_index"
                    ]
                )
    expected_count = len(context["joint_context"]["crop_views"]) * len(
        context["joint_context"]["primary_methods"]
    )
    if failures or len(candidates) != expected_count:
        reason = (
            "incompatible-with-accepted-geometry"
            if "incompatible-with-accepted-geometry" in failures
            else sorted(set(failures))[0]
            if failures
            else "missing-joint-evidence"
        )
        return {"status": "no-proposal", "reason": reason}
    unique = set(candidates)
    if len(unique) != 1:
        crop_agrees = all(
            joint_evidence["crop_view_agreement"][target_id].values()
        )
        reference_agrees = all(
            joint_evidence["reference_method_agreement"][target_id].values()
        )
        if not crop_agrees:
            reason = "crop-view-disagreement"
        elif not reference_agrees:
            reason = "reference-method-disagreement"
        else:
            reason = "primary-method-disagreement"
        return {"status": "no-proposal", "reason": reason}
    candidate = next(iter(unique))
    if candidate in reservations.values():
        owner = next(
            reserved_target
            for reserved_target, reserved_candidate in reservations.items()
            if reserved_candidate == candidate
        )
        if owner != target_id:
            return {
                "status": "no-proposal",
                "reason": "incompatible-with-accepted-geometry",
            }
    return {
        "status": "candidate-proposed-unthresholded",
        "temporary_candidate_index": candidate,
        "threshold_status": "not-configured",
        "appearance_is_authoritative": False,
    }


def build_fusion(context, frame_range=None):
    sidecar = context["sidecar"]
    frames = _selected_frames(sidecar, frame_range)
    target_ids = context["target_ids"]
    track_frames = {
        target_id: _frame_map(
            context["tracks"][target_id]["frames"],
            f"Geometry track target {target_id}.frames",
        )
        for target_id in target_ids
    }
    state_counts = {target_id: Counter() for target_id in target_ids}
    evidence_counts = {target_id: Counter() for target_id in target_ids}
    proposal_counts = {target_id: Counter() for target_id in target_ids}
    relation_counts = {target_id: Counter() for target_id in target_ids}
    output_frames = []
    warnings = list(context["warnings"])
    joint_frame_set = set(context["normalized_joint"]["analyzed_frames"])
    missing_joint_count = sum(frame not in joint_frame_set for frame in frames)
    if missing_joint_count:
        warnings.append(
            f"Joint appearance is unavailable for {missing_joint_count} analyzed frames; "
            "those frames remain geometry-governed."
        )
    for frame in frames:
        position = sidecar["frame_to_position"][frame]
        candidates = sidecar["all_boxes"][position]
        reservations = {}
        for target_id in target_ids:
            geometry = track_frames[target_id][frame]
            if geometry["state"] in {"anchor", "tracked"}:
                reservations[target_id] = geometry["temporary_candidate_index"]
        duplicate_reservations = [
            candidate
            for candidate, count in Counter(reservations.values()).items()
            if count > 1
        ]
        if duplicate_reservations:
            warnings.append(
                f"Frame {frame} has authoritative geometry reservation collisions: "
                f"{duplicate_reservations}."
            )
        joint_evidence = _joint_frame_evidence(context, frame, reservations)
        target_results = []
        pending_proposals = {}
        for target_id in target_ids:
            geometry = track_frames[target_id][frame]
            geometry_state = geometry["state"]
            state_counts[target_id][geometry_state] += 1
            forced = _forced_status(
                frame, context["anchors"][target_id]["forced_occlusion_ranges"]
            )
            if forced["is_user_forced"]:
                state = "occluded"
                accepted_evidence = "user-forced"
                accepted_box = None
                proposal = None
                reason = "forced-occlusion-range"
            elif geometry_state == "anchor":
                state = "anchor"
                accepted_evidence = "user-anchor"
                accepted_box = geometry["box_xyxy"]
                proposal = None
                reason = None
            elif geometry_state == "tracked":
                state = "tracked"
                accepted_evidence = "geometry-bidirectional"
                accepted_box = geometry["box_xyxy"]
                proposal = None
                reason = None
            elif geometry_state == "ambiguous":
                state = "ambiguous"
                accepted_evidence = None
                accepted_box = None
                proposal = _proposal_for_target(
                    context, target_id, joint_evidence, reservations
                )
                pending_proposals[target_id] = proposal
                reason = geometry.get("reason")
            else:
                state = geometry_state
                accepted_evidence = geometry.get("evidence")
                accepted_box = None
                proposal = None
                reason = geometry.get("reason")
            authoritative_relation = None
            if geometry_state in {"anchor", "tracked"} and not forced[
                "is_user_forced"
            ]:
                authoritative_relation = _authoritative_appearance_relation(
                    context,
                    target_id,
                    joint_evidence,
                    geometry["temporary_candidate_index"],
                )
                relation_counts[target_id][authoritative_relation["status"]] += 1
            evidence_counts[target_id][accepted_evidence or "none"] += 1
            target_results.append(
                {
                    "target_id": target_id,
                    "source_geometry": _geometry_copy(geometry),
                    "state": state,
                    "accepted_evidence": accepted_evidence,
                    "accepted_temporary_candidate_index": (
                        geometry.get("temporary_candidate_index")
                        if accepted_box is not None
                        else None
                    ),
                    "accepted_box_xyxy": copy.deepcopy(accepted_box),
                    "reason": reason,
                    "diagnostic_appearance_proposal": proposal,
                    "appearance_relation_to_authoritative_geometry": (
                        authoritative_relation
                    ),
                    "temporal_anchor_distance": _distance_to_anchors(
                        frame,
                        sidecar,
                        context["anchors"][target_id]["anchors"],
                        context["anchors"][target_id]["forced_occlusion_ranges"],
                    ),
                    "forced_boundary_status": forced,
                }
            )
        proposed = {
            target_id: proposal["temporary_candidate_index"]
            for target_id, proposal in pending_proposals.items()
            if proposal.get("status") == "candidate-proposed-unthresholded"
        }
        duplicate_proposals = {
            candidate
            for candidate, count in Counter(proposed.values()).items()
            if count > 1
        }
        if duplicate_proposals:
            for target in target_results:
                proposal = target["diagnostic_appearance_proposal"]
                if proposal and proposal.get("temporary_candidate_index") in duplicate_proposals:
                    target["diagnostic_appearance_proposal"] = {
                        "status": "no-proposal",
                        "reason": "non-injective-proposal-consensus",
                    }
        for target in target_results:
            proposal = target["diagnostic_appearance_proposal"]
            if proposal is not None:
                if proposal.get("status") == "candidate-proposed-unthresholded":
                    candidate = proposal["temporary_candidate_index"]
                    proposal["box_xyxy"] = list(candidates[candidate])
                proposal_counts[target["target_id"]][proposal["status"]] += 1
        output_frames.append(
            {
                "frame": frame,
                "timestamp_seconds": frame / sidecar["fps"],
                "candidate_count": len(candidates),
                "reserved_candidate_positions": [
                    {
                        "target_id": target_id,
                        "temporary_candidate_index": candidate,
                    }
                    for target_id, candidate in sorted(reservations.items())
                ],
                "targets": target_results,
                "joint_appearance": joint_evidence,
            }
        )
    provenance_targets = []
    for target_id in target_ids:
        provenance_targets.append(
            {
                "target_id": target_id,
                "track_id": context["anchors"][target_id]["track_id"],
                "anchors_sha256": context["hashes"]["anchors"][target_id],
                "geometry_track_sha256": context["hashes"]["tracks"][target_id],
                "appearance_report_sha256": context["hashes"]["appearances"][
                    target_id
                ],
            }
        )
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "diagnostic_scope": (
            "Geometry-plus-appearance evidence only. Source tracks remain unchanged; "
            "appearance proposals are non-authoritative and no production identity "
            "decision is produced."
        ),
        "source": {
            "sidecar_sha256": sidecar["sha256"],
            "fps": sidecar["fps"],
            "sampling_interval": sidecar["sampling_interval"],
        },
        "provenance": {
            "joint_assignment_sha256": context["hashes"]["joint"],
            "model_architecture": context["joint_context"]["provenance"][
                "model_architecture"
            ],
            "weights_sha256": context["joint_context"]["provenance"][
                "weights_sha256"
            ],
            "preprocessing": copy.deepcopy(
                context["joint_context"]["provenance"]["preprocessing"]
            ),
            "reference_anchor_frames": list(context["joint_context"]["references"]),
            "targets": provenance_targets,
        },
        "policy": {
            "precedence": [
                "user-forced-occlusion",
                "durable-user-anchor",
                "bidirectionally-agreed-geometry",
                "non-authoritative-unanimous-primary-appearance-proposal-for-ambiguous-geometry",
                "preserve-occluded-or-untracked-geometry",
            ],
            "appearance_confidence_threshold": None,
            "threshold_status": "not-configured",
            "appearance_is_authoritative": False,
            "temporary_candidate_indexes_are_per_frame_only": True,
            "runner_up_search_for_compatibility": False,
            "full_gallery_is_proposal_evidence": False,
        },
        "analyzed_frames": frames,
        "frames": output_frames,
        "summary": {
            "analyzed_frame_count": len(frames),
            "joint_appearance_frame_count": sum(
                frame in joint_frame_set for frame in frames
            ),
            "geometry_only_frame_count": missing_joint_count,
            "targets": {
                target_id: {
                    "state_counts": {
                        state: state_counts[target_id][state]
                        for state in sorted(VALID_STATES)
                    },
                    "accepted_evidence_counts": dict(
                        sorted(evidence_counts[target_id].items())
                    ),
                    "proposal_status_counts": dict(
                        sorted(proposal_counts[target_id].items())
                    ),
                    "authoritative_appearance_relation_counts": dict(
                        sorted(relation_counts[target_id].items())
                    ),
                }
                for target_id in target_ids
            },
        },
        "warnings": list(dict.fromkeys(warnings)),
    }


def _file_identity(file_stat):
    return file_stat.st_dev, file_stat.st_ino


def _cleanup_owned_temporary(path, identity):
    if identity is None:
        return False
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISLNK(current.st_mode) or _file_identity(current) != identity:
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def write_output(path, report):
    temporary = path.with_name(path.name + ".tmp")
    temporary_identity = None
    published = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        initial_parent = path.parent.resolve(strict=True)
        if initial_parent != OUTPUT_ROOT and OUTPUT_ROOT not in initial_parent.parents:
            raise FusionError("Output parent resolves outside checkpoints/dev-preview")
        if os.path.lexists(path):
            raise FusionError(f"Output already exists: {path}")
        if os.path.lexists(temporary):
            raise FusionError(f"Temporary output already exists: {temporary}")
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_identity = _file_identity(os.fstat(handle.fileno()))
        current_parent = path.parent.resolve(strict=True)
        resolved_output = _resolved(path)
        if (
            current_parent != initial_parent
            or (current_parent != OUTPUT_ROOT and OUTPUT_ROOT not in current_parent.parents)
            or (OUTPUT_ROOT not in resolved_output.parents)
        ):
            raise FusionError("Output destination changed or resolves outside checkpoints/dev-preview")
        if os.path.lexists(path):
            raise FusionError(f"Output appeared before publication and was preserved: {path}")
        os.link(temporary, path, follow_symlinks=False)
        published = True
        if not _cleanup_owned_temporary(temporary, temporary_identity):
            raise FusionError(
                f"Output was published safely, but its owned temporary file could not be removed: {temporary}"
            )
    except (OSError, TypeError, ValueError, FusionError) as exc:
        cleaned = _cleanup_owned_temporary(temporary, temporary_identity)
        cleanup_note = (
            "owned temporary file cleaned"
            if cleaned
            else "owned temporary file was absent or could not be safely removed"
        )
        raise FusionError(
            f"Cannot publish fusion diagnostic without overwriting {path}; "
            f"{cleanup_note}: {exc}"
        ) from exc


def main(argv=None):
    args = parse_args(argv)
    try:
        anchor_paths = parse_target_paths(args.anchors, "--anchors")
        track_paths = parse_target_paths(args.track, "--track")
        appearance_paths = parse_target_paths(args.appearance, "--appearance")
        input_paths = [
            ("Sidecar", args.sidecar),
            ("Joint assignment report", args.joint_assignment),
        ]
        for label, mapping in (
            ("Anchor", anchor_paths),
            ("Track", track_paths),
            ("Appearance", appearance_paths),
        ):
            input_paths.extend(
                (f"{label} for target {target_id}", path)
                for target_id, path in mapping.items()
            )
        validate_paths(input_paths, args.output)
        context = load_and_validate(
            args.sidecar,
            args.joint_assignment,
            anchor_paths,
            track_paths,
            appearance_paths,
        )
        report = build_fusion(context, parse_frame_range(args.frame_range))
        write_output(args.output, report)
    except (FusionError, joint_tool.AssignmentError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved geometry-appearance fusion diagnostic: {args.output}")
    print(f"Targets: {len(context['target_ids'])}")
    print(f"Analyzed frames: {len(report['analyzed_frames'])}")
    print("Appearance proposals are diagnostic-only and non-authoritative")
    return 0


if __name__ == "__main__":
    sys.exit(main())
