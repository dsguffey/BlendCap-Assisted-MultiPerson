#!/usr/bin/env python3
"""Apply hash-bound human review decisions as immutable anchor/track revisions."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from build_preview_target_review import (
    DECISION_SCHEMA,
    DECISION_VERSION,
    ReviewError,
    load_json,
    sha256_file,
)
from blendcap_vta_boundary import (
    BoundaryTranslationError,
    translate_reviewed_anchor,
)
from track_preview_target import TrackError, build_track, validate_sidecar


REVISION_SCHEMA = "blendcap-preview-target-review-revision"
REVISION_VERSION = 1
RANGE_ACTIONS = {"merged-shared-detection", "no-valid-candidate", "not-visible"}
ALLOWED_ACTIONS = RANGE_ACTIONS | {"select-existing-candidate", "uncertain"}


class ApplyError(ValueError):
    """A user-facing decision validation or publication error."""


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ApplyError(f"{label} must be an object")
    return value


def _sampled_integer(value, label: str, sidecar: dict) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplyError(f"{label} must be an integer")
    if value not in sidecar["frame_to_position"]:
        raise ApplyError(f"{label} must be an exact sampled frame")
    return value


def _candidate_index(value, frame: int, sidecar: dict, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApplyError(f"{label} must be a non-negative integer")
    candidates = sidecar["all_boxes"][sidecar["frame_to_position"][frame]]
    if value >= len(candidates):
        raise ApplyError(f"{label} is out of range for frame {frame}")
    return value


def _hash_source(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
        raise ApplyError(f"decisions source.{label} must be a SHA-256 string")
    if sha256_file(path) != expected.lower():
        raise ApplyError(f"decisions source.{label} does not match {path}")


def validate_base(sidecar_path: Path, anchors_path: Path, track_path: Path) -> dict:
    sidecar_raw = load_json(sidecar_path, "sidecar")
    sidecar_hash = sha256_file(sidecar_path)
    try:
        sidecar = validate_sidecar(sidecar_raw, sidecar_hash)
    except TrackError as exc:
        raise ApplyError(f"Invalid sidecar: {exc}") from exc
    anchors_raw = load_json(anchors_path, "base anchor file")
    track_raw = load_json(track_path, "base geometry track")
    try:
        rebuilt = build_track(sidecar, copy.deepcopy(anchors_raw))
    except TrackError as exc:
        raise ApplyError(f"Invalid base anchors: {exc}") from exc
    if track_raw != rebuilt:
        raise ApplyError("Base geometry track does not exactly match a rebuild")
    return {
        "sidecar": sidecar,
        "sidecar_raw": sidecar_raw,
        "anchors": anchors_raw,
        "track": track_raw,
        "sidecar_sha256": sidecar_hash,
        "anchors_sha256": sha256_file(anchors_path),
        "track_sha256": sha256_file(track_path),
    }


def _validate_fusion_binding(path: Path, base: dict, target_id: str) -> None:
    fusion = load_json(path, "fusion report")
    if fusion.get("schema") != "blendcap-preview-geometry-appearance-fusion-diagnostic" or fusion.get("version") != 1:
        raise ApplyError("Fusion input has an unsupported schema or version")
    source = _require_object(fusion.get("source"), "fusion source")
    if source.get("sidecar_sha256") != base["sidecar_sha256"]:
        raise ApplyError("fusion sidecar hash does not match")
    if source.get("fps") != base["sidecar"]["fps"] or source.get("sampling_interval") != base["sidecar"]["sampling_interval"]:
        raise ApplyError("fusion source timing does not match")
    policy = _require_object(fusion.get("policy"), "fusion policy")
    if policy.get("appearance_is_authoritative") is not False:
        raise ApplyError("fusion must mark appearance as non-authoritative")
    provenance = _require_object(fusion.get("provenance"), "fusion provenance")
    values = provenance.get("targets")
    if not isinstance(values, list):
        raise ApplyError("fusion provenance.targets must be a list")
    matches = [
        value for value in values
        if isinstance(value, dict) and value.get("target_id") == target_id
    ]
    if len(matches) != 1:
        raise ApplyError("fusion provenance must contain the requested target exactly once")
    value = matches[0]
    if value.get("track_id") != base["track"]["track_id"]:
        raise ApplyError("fusion target track ID does not match")
    if value.get("anchors_sha256") != base["anchors_sha256"]:
        raise ApplyError("fusion target anchor hash does not match")
    if value.get("geometry_track_sha256") != base["track_sha256"]:
        raise ApplyError("fusion target track hash does not match")


def validate_decisions(
    raw: dict,
    base: dict,
    target_id: str,
    video_path: Path,
    fusion_path: Path | None = None,
) -> list[dict]:
    if raw.get("schema") != DECISION_SCHEMA:
        raise ApplyError("Unsupported decision schema")
    if raw.get("version") != DECISION_VERSION:
        raise ApplyError("Unsupported decision version; expected 1")
    review_id = raw.get("review_id")
    if not isinstance(review_id, str) or not review_id:
        raise ApplyError("decisions review_id must be a non-empty string")
    source = _require_object(raw.get("source"), "decisions source")
    if source.get("sidecar_sha256") != base["sidecar_sha256"]:
        raise ApplyError("decisions source.sidecar_sha256 does not match")
    if source.get("fps") != base["sidecar"]["fps"]:
        raise ApplyError("decisions source.fps does not match")
    if source.get("sampling_interval") != base["sidecar"]["sampling_interval"]:
        raise ApplyError("decisions source.sampling_interval does not match")
    _hash_source(video_path, source.get("video_sha256"), "video_sha256")
    expected_fusion = source.get("fusion_sha256")
    if expected_fusion is None:
        if fusion_path is not None:
            raise ApplyError("A fusion input was supplied but the decisions have no fusion hash")
    else:
        if fusion_path is None:
            raise ApplyError("The decisions require the hash-bound --fusion input")
        _hash_source(fusion_path, expected_fusion, "fusion_sha256")
        _validate_fusion_binding(fusion_path, base, target_id)
    target_sources = _require_object(source.get("targets"), "decisions source.targets")
    target_ids = raw.get("target_ids")
    if not isinstance(target_ids, list) or target_id not in target_ids:
        raise ApplyError("decisions target_ids does not contain the requested target")
    if any(not isinstance(item, str) or not item for item in target_ids) or len(target_ids) != len(set(target_ids)):
        raise ApplyError("decisions target_ids must contain unique non-empty strings")
    if set(target_sources) != set(target_ids):
        raise ApplyError("decisions source target-ID set must match target_ids")
    for item_target in target_ids:
        _require_object(
            target_sources[item_target], f"decisions source.targets.{item_target}"
        )
    target_source = target_sources[target_id]
    if target_source.get("anchors_sha256") != base["anchors_sha256"]:
        raise ApplyError("decisions anchor hash does not match the base anchor file")
    if target_source.get("track_sha256") != base["track_sha256"]:
        raise ApplyError("decisions track hash does not match the base geometry track")
    if target_source.get("track_id") != base["track"]["track_id"]:
        raise ApplyError("decisions track ID does not match the base geometry track")
    frame_range = raw.get("frame_range")
    if not isinstance(frame_range, list) or len(frame_range) != 2:
        raise ApplyError("decisions frame_range must contain two sampled frames")
    start = _sampled_integer(frame_range[0], "decisions frame_range start", base["sidecar"])
    end = _sampled_integer(frame_range[1], "decisions frame_range end", base["sidecar"])
    if start > end:
        raise ApplyError("decisions frame_range is reversed")
    statement = _require_object(raw.get("statement"), "decisions statement")
    if statement.get("appearance_is_non_authoritative") is not True:
        raise ApplyError("decisions must state that appearance is non-authoritative")
    if statement.get("temporary_candidate_indexes_are_per_frame_only") is not True:
        raise ApplyError("decisions must state that TEMP indexes are frame-local")

    values = raw.get("decisions")
    if not isinstance(values, list) or not values:
        raise ApplyError("decisions must be a non-empty list")
    validated = []
    ordinary_selections: dict[tuple[int, int], str] = {}
    shared_selections: dict[tuple[int, int], set[str]] = {}
    seen = set()
    for i, value in enumerate(values):
        item = _require_object(value, f"decisions[{i}]")
        item_target = item.get("target_id")
        if not isinstance(item_target, str) or not item_target:
            raise ApplyError(f"decisions[{i}].target_id must be a non-empty string")
        if item_target not in target_ids:
            raise ApplyError(f"decisions[{i}].target_id is not declared in target_ids")
        frame = _sampled_integer(item.get("frame"), f"decisions[{i}].frame", base["sidecar"])
        if not start <= frame <= end:
            raise ApplyError(f"decisions[{i}].frame is outside the decision frame range")
        decision_key = (item_target, frame)
        if decision_key in seen:
            raise ApplyError(f"Duplicate decision for target {item_target} frame {frame}")
        seen.add(decision_key)
        action = item.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ApplyError(f"decisions[{i}].action is unsupported")
        note = item.get("note", "")
        if not isinstance(note, str):
            raise ApplyError(f"decisions[{i}].note must be a string")
        result = {"target_id": item_target, "frame": frame, "action": action, "note": note}
        if action in {"select-existing-candidate", "merged-shared-detection"}:
            result["temporary_candidate_index"] = _candidate_index(
                item.get("temporary_candidate_index"), frame, base["sidecar"],
                f"decisions[{i}].temporary_candidate_index",
            )
            key = (frame, result["temporary_candidate_index"])
            if action == "select-existing-candidate":
                previous_target = ordinary_selections.get(key)
                if previous_target is not None and previous_target != item_target:
                    raise ApplyError(
                        "Two targets cannot select the same ordinary candidate; "
                        "use merged-shared-detection"
                    )
                ordinary_selections[key] = item_target
            else:
                shared_selections.setdefault(key, set()).add(item_target)
        if action in RANGE_ACTIONS:
            range_start = _sampled_integer(item.get("start_frame"), f"decisions[{i}].start_frame", base["sidecar"])
            range_end = _sampled_integer(item.get("end_frame"), f"decisions[{i}].end_frame", base["sidecar"])
            if range_start > range_end:
                raise ApplyError(f"decisions[{i}] range is reversed")
            if range_start < start or range_end > end:
                raise ApplyError(
                    f"decisions[{i}] range extends outside the reviewed frame range"
                )
            if not range_start <= frame <= range_end:
                raise ApplyError(f"decisions[{i}] review frame must be inside its range")
            if action in {"merged-shared-detection", "no-valid-candidate"} and not note.strip():
                raise ApplyError(f"decisions[{i}] requires an explanatory note")
            result.update(start_frame=range_start, end_frame=range_end)
            replacement = item.get("replaces_forced_range")
            if replacement is not None:
                replacement = _require_object(replacement, f"decisions[{i}].replaces_forced_range")
                old_start = _sampled_integer(replacement.get("start_frame"), f"decisions[{i}].replaces_forced_range.start_frame", base["sidecar"])
                old_end = _sampled_integer(replacement.get("end_frame"), f"decisions[{i}].replaces_forced_range.end_frame", base["sidecar"])
                if old_start < start or old_end > end:
                    raise ApplyError(
                        f"decisions[{i}] replacement range extends outside the reviewed frame range"
                    )
                result["replaces_forced_range"] = {"start_frame": old_start, "end_frame": old_end}
        if item_target == target_id:
            validated.append(result)
    conflict = set(ordinary_selections) & set(shared_selections)
    if conflict:
        raise ApplyError(
            "A candidate cannot be both an ordinary selection and a merged/shared detection"
        )
    if not validated:
        raise ApplyError(f"No decisions were supplied for target {target_id}")
    return validated


def _anchor_center(box: list[float]) -> list[float]:
    return [(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0]


def _apply_ranges(anchor_document: dict, decisions: list[dict]) -> list[dict]:
    ranges = copy.deepcopy(anchor_document.get("forced_occlusion_ranges", []))
    for item in decisions:
        if item["action"] not in RANGE_ACTIONS:
            continue
        replacement = item.get("replaces_forced_range")
        if replacement is not None:
            matches = [
                index for index, current in enumerate(ranges)
                if current["start_frame"] == replacement["start_frame"]
                and current["end_frame"] == replacement["end_frame"]
            ]
            if len(matches) != 1:
                raise ApplyError("replaces_forced_range must identify exactly one existing range")
            ranges.pop(matches[0])
        new_range = {"start_frame": item["start_frame"], "end_frame": item["end_frame"]}
        if item["note"]:
            new_range["note"] = item["note"]
        ranges.append(new_range)
    ranges.sort(key=lambda value: (value["start_frame"], value["end_frame"]))
    for left, right in zip(ranges, ranges[1:]):
        if right["start_frame"] <= left["end_frame"]:
            raise ApplyError("Resulting forced occlusion ranges overlap")
    return ranges


def apply_decisions(base: dict, decisions: list[dict], decisions_hash: str) -> tuple[dict, dict, list[dict]]:
    revised = copy.deepcopy(base["anchors"])
    revised["forced_occlusion_ranges"] = _apply_ranges(revised, decisions)
    anchors = revised.setdefault("anchors", [])
    existing_frames = {item["frame"] for item in anchors}
    semantic_changes = []
    for item in decisions:
        change = {key: copy.deepcopy(value) for key, value in item.items() if key != "temporary_candidate_index"}
        if item["action"] == "select-existing-candidate":
            if item["frame"] in existing_frames:
                raise ApplyError(f"A durable anchor already exists at frame {item['frame']}")
            position = base["sidecar"]["frame_to_position"][item["frame"]]
            box = list(
                base["sidecar"]["all_boxes"][position][
                    item["temporary_candidate_index"]
                ]
            )
            anchor = {
                "anchor_id": f"{base['track']['track_id']}-review-{item['frame']:06d}-{decisions_hash[:10]}",
                "frame": item["frame"],
                "clicked_point_xy": _anchor_center(box),
                "box_snapshot_xyxy": box,
            }
            if item["note"]:
                anchor["note"] = item["note"]
            anchors.append(anchor)
            existing_frames.add(item["frame"])
            change["resolved_box_xyxy"] = copy.deepcopy(box)
        elif item["action"] == "merged-shared-detection":
            position = base["sidecar"]["frame_to_position"][item["frame"]]
            change["resolved_shared_box_xyxy"] = list(
                base["sidecar"]["all_boxes"][position][
                    item["temporary_candidate_index"]
                ]
            )
        semantic_changes.append(change)
    anchors.sort(key=lambda item: item["frame"])
    try:
        revised_track = build_track(base["sidecar"], copy.deepcopy(revised))
    except TrackError as exc:
        raise ApplyError(f"Decisions produce invalid anchors or ranges: {exc}") from exc
    return revised, revised_track, semantic_changes


def verified_source_video_metadata(video_path: Path, expected_fps: float) -> dict:
    """Read dimensions from the reviewed source video and bind them to its FPS.

    Version-2 sidecars intentionally do not carry dimensions.  This call site
    already receives the source video for hash validation, so it must probe the
    same file instead of guessing dimensions from candidate boxes.
    """
    if not video_path.is_file():
        raise ApplyError(f"Source video does not exist: {video_path}")
    try:
        import cv2
    except ImportError as exc:
        raise ApplyError("OpenCV is required to verify source video dimensions") from exc
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise ApplyError(f"Cannot open source video: {video_path}")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise ApplyError("Source video did not provide positive width and height")
    if frame_count <= 0:
        raise ApplyError("Source video did not provide a positive frame count")
    tolerance = max(0.001, expected_fps * 1e-6)
    if not fps or abs(fps - expected_fps) > tolerance:
        raise ApplyError("Source video FPS does not match the review sidecar")
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
    }


def handoff_reviewed_selection_to_vta(
    base: dict,
    revised_anchors: dict,
    selection: dict,
    *,
    video_path: Path,
    expected_video_sha256: str,
    source_id: str,
    analysis_revision_id: str,
    provider: str,
    model: str,
    configuration_id: str,
    character_id: str,
    private_consumer,
):
    """Send one newly durable BlendCap selection across the neutral boundary.

    ``private_consumer`` is supplied by the private orchestration layer (for
    example ``vta_core.apply_reviewed_observation``).  Keeping it injected
    prevents GPL BlendCap code from containing private product logic while the
    actual review-application path remains directly usable.
    """
    if not callable(private_consumer):
        raise ApplyError("private_consumer must be callable")
    if selection.get("action") != "select-existing-candidate":
        raise ApplyError("Only a reviewed existing-candidate selection can cross the VTA boundary")
    frame = _sampled_integer(selection.get("frame"), "selection.frame", base["sidecar"])
    candidate_index = _candidate_index(
        selection.get("temporary_candidate_index"),
        frame,
        base["sidecar"],
        "selection.temporary_candidate_index",
    )
    resolved_box = base["sidecar"]["all_boxes"][base["sidecar"]["frame_to_position"][frame]][candidate_index]
    anchors = [
        item
        for item in revised_anchors.get("anchors", [])
        if item.get("frame") == frame and item.get("box_snapshot_xyxy") == list(resolved_box)
    ]
    if len(anchors) != 1:
        raise ApplyError(
            "Reviewed selection must resolve to exactly one newly durable anchor"
        )
    _hash_source(video_path, expected_video_sha256, "video_sha256")
    metadata = verified_source_video_metadata(video_path, base["sidecar"]["fps"])
    if frame >= metadata["frame_count"]:
        raise ApplyError("Reviewed selection frame is outside source-video coverage")
    try:
        payload = translate_reviewed_anchor(
            base["sidecar_raw"],
            revised_anchors,
            sidecar_sha256=base["sidecar_sha256"],
            anchor_document_sha256=hashlib.sha256(_canonical(revised_anchors)).hexdigest(),
            anchor_id=anchors[0]["anchor_id"],
            source_id=source_id,
            source_width=metadata["width"],
            source_height=metadata["height"],
            source_dimensions_verified=True,
            source_video_sha256=expected_video_sha256,
            analysis_revision_id=analysis_revision_id,
            provider=provider,
            model=model,
            configuration_id=configuration_id,
        )
    except BoundaryTranslationError as exc:
        raise ApplyError(f"Cannot translate reviewed selection to VTA: {exc}") from exc
    return payload, private_consumer(payload, character_id=character_id)


def _canonical(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _changed_intervals(old_track: dict, new_track: dict, interval: int) -> list[dict]:
    changed = [
        old["frame"] for old, new in zip(old_track["frames"], new_track["frames"])
        if old != new
    ]
    result = []
    for frame in changed:
        if result and frame == result[-1]["end_frame"] + interval:
            result[-1]["end_frame"] = frame
        else:
            result.append({"start_frame": frame, "end_frame": frame})
    return result


def _frames_between(sample_indices: list[int], start: int, end: int) -> set[int]:
    return {frame for frame in sample_indices if start <= frame <= end}


def _allowed_change_frames(base: dict, revised_anchors: dict, changes: list[dict]) -> set[int]:
    """Derive the bounded semantic scope independently from track differences."""
    sampled = base["sidecar"]["sample_indices"]
    old_anchor_frames = sorted(item["frame"] for item in base["anchors"]["anchors"])
    new_anchor_frames = sorted(item["frame"] for item in revised_anchors["anchors"])
    allowed: set[int] = set()
    for change in changes:
        action = change["action"]
        if action == "uncertain":
            continue
        if action == "select-existing-candidate":
            frame = change["frame"]
            previous = max((value for value in old_anchor_frames if value < frame), default=frame)
            following = min((value for value in old_anchor_frames if value > frame), default=frame)
            allowed.update(_frames_between(sampled, previous, following))
            continue
        if action in RANGE_ACTIONS:
            affected_ranges = [(change["start_frame"], change["end_frame"])]
            replacement = change.get("replaces_forced_range")
            if replacement:
                affected_ranges.append((replacement["start_frame"], replacement["end_frame"]))
            for start, end in affected_ranges:
                allowed.update(_frames_between(sampled, start, end))
                for anchor_frames in (old_anchor_frames, new_anchor_frames):
                    for left, right in zip(anchor_frames, anchor_frames[1:]):
                        if left < end and right > start:
                            allowed.update(_frames_between(sampled, left, right))
    return allowed


def build_revision(
    base: dict,
    revised_anchors: dict,
    revised_track: dict,
    semantic_changes: list[dict],
    decisions_path: Path,
    decisions_raw: dict,
    anchors_output: Path,
    track_output: Path,
    parent_revision: Path | None = None,
) -> tuple[dict, bytes, bytes]:
    anchor_bytes = _canonical(revised_anchors)
    track_bytes = _canonical(revised_track)
    parent = {"filename": None, "sha256": None}
    if parent_revision is not None:
        parent_raw = load_json(parent_revision, "parent revision")
        if parent_raw.get("schema") != REVISION_SCHEMA or parent_raw.get("version") != REVISION_VERSION:
            raise ApplyError("Parent revision has an unsupported schema or version")
        parent_outputs = _require_object(
            parent_raw.get("outputs"), "parent revision outputs"
        )
        if parent_outputs.get("anchors_sha256") != base["anchors_sha256"]:
            raise ApplyError("Parent revision anchor hash does not match the base anchors")
        if parent_outputs.get("track_sha256") != base["track_sha256"]:
            raise ApplyError("Parent revision track hash does not match the base track")
        parent = {"filename": parent_revision.name, "sha256": sha256_file(parent_revision)}
    affected = _changed_intervals(
        base["track"], revised_track, base["sidecar"]["sampling_interval"]
    )
    actually_changed = {
        old["frame"]
        for old, new in zip(base["track"]["frames"], revised_track["frames"])
        if old != new
    }
    allowed_changes = _allowed_change_frames(base, revised_anchors, semantic_changes)
    unexpected = sorted(actually_changed - allowed_changes)
    if unexpected:
        raise ApplyError(
            "Track regeneration changed frames outside the intended bounded scope: "
            + ", ".join(str(frame) for frame in unexpected)
        )
    changed_frames = {
        frame for span in affected
        for frame in base["sidecar"]["sample_indices"]
        if span["start_frame"] <= frame <= span["end_frame"]
    }
    outside_equal = all(
        old == new
        for old, new in zip(base["track"]["frames"], revised_track["frames"])
        if old["frame"] not in changed_frames
    )
    revision_id = f"revision-{uuid.uuid4()}"
    revision = {
        "schema": REVISION_SCHEMA,
        "version": REVISION_VERSION,
        "revision_id": revision_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "parent_revision": parent,
        "inputs": {
            "decisions_filename": decisions_path.name,
            "decisions_sha256": sha256_file(decisions_path),
            "sidecar_sha256": base["sidecar_sha256"],
            "base_anchors_sha256": base["anchors_sha256"],
            "base_track_sha256": base["track_sha256"],
            "fusion_sha256": decisions_raw["source"].get("fusion_sha256"),
        },
        "affected_intervals": affected,
        "semantic_changes": copy.deepcopy(semantic_changes),
        "outputs": {
            "anchors_filename": anchors_output.name,
            "anchors_sha256": hashlib.sha256(anchor_bytes).hexdigest(),
            "track_filename": track_output.name,
            "track_sha256": hashlib.sha256(track_bytes).hexdigest(),
        },
        "verification": {
            "outside_affected_intervals_unchanged": outside_equal,
            "changes_within_intended_bounded_scope": not unexpected,
            "forced_ranges_not_bridged": all(
                item["status"] != "evaluated"
                or not any(
                    r["start_frame"] < right["frame"] and r["end_frame"] > left["frame"]
                    for r in revised_track["forced_occlusion_ranges"]
                )
                for item, left, right in zip(
                    revised_track["interval_diagnostics"],
                    revised_track["resolved_anchors"],
                    revised_track["resolved_anchors"][1:],
                )
            ),
            "temporary_candidate_indexes_persisted_as_identity": False,
        },
    }
    if not outside_equal or not revision["verification"]["forced_ranges_not_bridged"]:
        raise ApplyError("Revision equivalence or forced-boundary verification failed")
    return revision, anchor_bytes, track_bytes


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_outputs(outputs: list[Path], inputs: list[Path], workspace: Path | None = None) -> list[Path]:
    workspace = (workspace or Path.cwd()).resolve(strict=True)
    allowed = (workspace / "checkpoints" / "dev-preview").resolve(strict=False)
    resolved_inputs = {str(path.resolve(strict=False)).casefold() for path in inputs}
    resolved = []
    for path in outputs:
        item = path.resolve(strict=False)
        if item.suffix.lower() != ".json" or not _inside(item, allowed):
            raise ApplyError("Every output must be a JSON file beneath checkpoints/dev-preview")
        if str(item).casefold() in resolved_inputs:
            raise ApplyError("An output path collides with an input")
        if item.exists():
            raise ApplyError(f"Output already exists: {path}")
        resolved.append(item)
    if len({str(item).casefold() for item in resolved}) != len(resolved):
        raise ApplyError("Output paths must be distinct")
    return resolved


def publish_outputs(documents: list[tuple[Path, bytes]], inputs: list[Path]) -> None:
    destinations = validate_outputs([path for path, _ in documents], inputs)
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: list[Path] = []
    published: list[tuple[Path, Path]] = []
    try:
        for destination, (_, content) in zip(destinations, documents):
            descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
            temporary_path = Path(name)
            temporary.append(temporary_path)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        destinations = validate_outputs([path for path, _ in documents], inputs)
        for temporary_path, destination in zip(temporary, destinations):
            try:
                os.link(temporary_path, destination)
            except OSError as exc:
                raise ApplyError(f"Cannot publish output without replacement: {destination}: {exc}") from exc
            published.append((temporary_path, destination))
    except Exception:
        for temporary_path, destination in reversed(published):
            try:
                if destination.exists() and os.path.samefile(temporary_path, destination):
                    destination.unlink()
            except OSError:
                pass
        raise
    finally:
        for temporary_path in temporary:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--track", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--fusion", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--parent-revision", type=Path)
    parser.add_argument("--output-anchors", type=Path, required=True)
    parser.add_argument("--output-track", type=Path, required=True)
    parser.add_argument("--output-revision", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    inputs = [args.video, args.sidecar, args.anchors, args.track, args.decisions]
    if args.fusion:
        inputs.append(args.fusion)
    if args.parent_revision:
        inputs.append(args.parent_revision)
    outputs = [args.output_anchors, args.output_track, args.output_revision]
    try:
        if not args.video.is_file():
            raise ApplyError(f"Source video does not exist: {args.video}")
        validate_outputs(outputs, inputs)
        base = validate_base(args.sidecar, args.anchors, args.track)
        decisions_raw = load_json(args.decisions, "decision file")
        decisions = validate_decisions(
            decisions_raw, base, args.target, args.video, args.fusion
        )
        decisions_hash = sha256_file(args.decisions)
        revised_anchors, revised_track, changes = apply_decisions(base, decisions, decisions_hash)
        revision, anchor_bytes, track_bytes = build_revision(
            base, revised_anchors, revised_track, changes, args.decisions,
            decisions_raw, args.output_anchors, args.output_track, args.parent_revision,
        )
        revision_bytes = _canonical(revision)
        publish_outputs(
            [
                (args.output_anchors, anchor_bytes),
                (args.output_track, track_bytes),
                (args.output_revision, revision_bytes),
            ],
            inputs,
        )
    except (ApplyError, ReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved revised anchors: {args.output_anchors}")
    print(f"Saved revised track: {args.output_track}")
    print(f"Saved immutable revision: {args.output_revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
