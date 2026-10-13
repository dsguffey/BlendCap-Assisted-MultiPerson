# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate preview evidence consumed by the public VTA boundary.

These helpers validate sampled sidecars and resolve explicit human anchors.
They do not track, score identity, propagate observations or publish files.
Snapshot-resolution tolerances retain the existing public boundary contract.
"""

import math


ANCHOR_MINIMUM_IOU = 0.85
ANCHOR_MAXIMUM_CENTER_DISPLACEMENT = 0.10
ANCHOR_MAXIMUM_AREA_FOLD_CHANGE = 1.20


class PreviewEvidenceError(RuntimeError):
    """Preview evidence cannot be validated without ambiguity."""


def _finite_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PreviewEvidenceError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise PreviewEvidenceError(f"{label} must be finite")
    return number


def _box(value, label, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise PreviewEvidenceError(f"{label} must be a four-value box")
    if not isinstance(value, list) or len(value) != 4:
        raise PreviewEvidenceError(f"{label} must be a four-value box")
    result = tuple(
        _finite_float(item, f"{label}[{i}]") for i, item in enumerate(value)
    )
    if result[2] <= result[0] or result[3] <= result[1]:
        raise PreviewEvidenceError(f"{label} has non-positive width or height")
    return result


def _point(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise PreviewEvidenceError(f"{label} must be a two-value point")
    return tuple(
        _finite_float(item, f"{label}[{i}]") for i, item in enumerate(value)
    )


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _iou(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = _area(a) + _area(b) - intersection
    return intersection / union if union > 0 else 0.0


def _center_displacement(reference, candidate):
    reference_x = (reference[0] + reference[2]) / 2.0
    reference_y = (reference[1] + reference[3]) / 2.0
    candidate_x = (candidate[0] + candidate[2]) / 2.0
    candidate_y = (candidate[1] + candidate[3]) / 2.0
    distance = math.hypot(candidate_x - reference_x, candidate_y - reference_y)
    diagonal = math.hypot(
        reference[2] - reference[0], reference[3] - reference[1]
    )
    return distance / diagonal if diagonal > 0 else math.inf


def _area_fold_change(a, b):
    area_a = _area(a)
    area_b = _area(b)
    return max(area_a, area_b) / min(area_a, area_b)


def _contains(box, point):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def validate_sidecar(raw, digest=""):
    if raw.get("version") != 2:
        raise PreviewEvidenceError(
            f"Unsupported sidecar version {raw.get('version')!r}; expected 2"
        )
    fps = _finite_float(raw.get("fps"), "sidecar fps")
    if fps <= 0:
        raise PreviewEvidenceError("sidecar fps must be positive")

    indices_raw = raw.get("sample_indices")
    selected_raw = raw.get("boxes")
    candidates_raw = raw.get("all_boxes")
    for value, label in (
        (indices_raw, "sample_indices"),
        (selected_raw, "boxes"),
        (candidates_raw, "all_boxes"),
    ):
        if not isinstance(value, list):
            raise PreviewEvidenceError(f"sidecar {label} must be a list")

    count = len(indices_raw)
    if count < 2:
        raise PreviewEvidenceError("sidecar needs at least two samples")
    if len(selected_raw) != count or len(candidates_raw) != count:
        raise PreviewEvidenceError(
            "sidecar sample_indices, boxes, and all_boxes lengths must match"
        )

    indices = []
    candidates = []
    for i, frame in enumerate(indices_raw):
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise PreviewEvidenceError(
                f"sidecar sample_indices[{i}] must be a non-negative integer"
            )
        if indices and frame <= indices[-1]:
            raise PreviewEvidenceError("sidecar sample_indices must be strictly increasing")
        indices.append(frame)
        _box(selected_raw[i], f"sidecar boxes[{i}]", allow_none=True)
        frame_candidates = candidates_raw[i]
        if not isinstance(frame_candidates, list):
            raise PreviewEvidenceError(f"sidecar all_boxes[{i}] must be a list")
        candidates.append(
            [
                _box(candidate, f"sidecar all_boxes[{i}][{j}]")
                for j, candidate in enumerate(frame_candidates)
            ]
        )

    interval = indices[1] - indices[0]
    if interval <= 0 or any(
        indices[i] - indices[i - 1] != interval for i in range(2, count)
    ):
        raise PreviewEvidenceError("sidecar must use one constant positive sample interval")

    return {
        "fps": fps,
        "sample_indices": indices,
        "sampling_interval": interval,
        "all_boxes": candidates,
        "frame_to_position": {frame: i for i, frame in enumerate(indices)},
        "sha256": digest,
    }


def _validate_source_metadata(source, sidecar):
    if source is None:
        return
    if not isinstance(source, dict):
        raise PreviewEvidenceError("anchors source must be an object")
    expected_hash = source.get("sidecar_sha256")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or not expected_hash:
            raise PreviewEvidenceError("anchors source.sidecar_sha256 must be a string")
        if sidecar["sha256"] and expected_hash.lower() != sidecar["sha256"]:
            raise PreviewEvidenceError("anchor sidecar hash does not match the input sidecar")
    expected_fps = source.get("fps")
    if expected_fps is not None:
        expected_fps = _finite_float(expected_fps, "anchors source.fps")
        tolerance = max(0.001, sidecar["fps"] * 1e-6)
        if abs(expected_fps - sidecar["fps"]) > tolerance:
            raise PreviewEvidenceError("anchor FPS does not match the input sidecar")
    expected_interval = source.get("sampling_interval")
    if expected_interval is not None:
        if (
            isinstance(expected_interval, bool)
            or not isinstance(expected_interval, int)
            or expected_interval != sidecar["sampling_interval"]
        ):
            raise PreviewEvidenceError(
                "anchor sampling interval does not match the input sidecar"
            )


def _validate_ranges(raw_ranges, sidecar):
    if raw_ranges is None:
        raw_ranges = []
    if not isinstance(raw_ranges, list):
        raise PreviewEvidenceError("forced_occlusion_ranges must be a list")

    sampled = sidecar["frame_to_position"]
    ranges = []
    for i, item in enumerate(raw_ranges):
        if not isinstance(item, dict):
            raise PreviewEvidenceError(f"forced_occlusion_ranges[{i}] must be an object")
        start = item.get("start_frame")
        end = item.get("end_frame")
        for value, field in ((start, "start_frame"), (end, "end_frame")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise PreviewEvidenceError(
                    f"forced_occlusion_ranges[{i}].{field} must be an integer"
                )
            if value not in sampled:
                raise PreviewEvidenceError(
                    f"forced_occlusion_ranges[{i}].{field} must be a sampled frame"
                )
        if start > end:
            raise PreviewEvidenceError(f"forced_occlusion_ranges[{i}] is reversed")
        note = item.get("note")
        if note is not None and not isinstance(note, str):
            raise PreviewEvidenceError(f"forced_occlusion_ranges[{i}].note must be a string")
        result = {"start_frame": start, "end_frame": end}
        if note is not None:
            result["note"] = note
        ranges.append(result)

    ranges.sort(key=lambda item: (item["start_frame"], item["end_frame"]))
    for previous, current in zip(ranges, ranges[1:]):
        if current["start_frame"] <= previous["end_frame"]:
            raise PreviewEvidenceError("forced occlusion ranges must not overlap")
    return ranges


def _frames_in_ranges(ranges, sample_indices):
    return {
        frame
        for frame in sample_indices
        if any(
            item["start_frame"] <= frame <= item["end_frame"]
            for item in ranges
        )
    }


def _resolve_anchor(anchor, sidecar, label):
    if not isinstance(anchor, dict):
        raise PreviewEvidenceError(f"{label} must be an object")
    anchor_id = anchor.get("anchor_id")
    if not isinstance(anchor_id, str) or not anchor_id:
        raise PreviewEvidenceError(f"{label}.anchor_id must be a non-empty string")
    frame = anchor.get("frame")
    if isinstance(frame, bool) or not isinstance(frame, int):
        raise PreviewEvidenceError(f"{label}.frame must be an integer")
    if frame not in sidecar["frame_to_position"]:
        raise PreviewEvidenceError(f"{label}.frame must be a sampled frame")

    point_raw = anchor.get("clicked_point_xy")
    snapshot_raw = anchor.get("box_snapshot_xyxy")
    if point_raw is None and snapshot_raw is None:
        raise PreviewEvidenceError(
            f"{label} needs clicked_point_xy and/or box_snapshot_xyxy"
        )
    point = _point(point_raw, f"{label}.clicked_point_xy") if point_raw else None
    snapshot = (
        _box(snapshot_raw, f"{label}.box_snapshot_xyxy")
        if snapshot_raw
        else None
    )
    note = anchor.get("note")
    if note is not None and not isinstance(note, str):
        raise PreviewEvidenceError(f"{label}.note must be a string")

    position = sidecar["frame_to_position"][frame]
    candidates = sidecar["all_boxes"][position]
    matches = []
    for candidate_index, candidate in enumerate(candidates):
        point_match = point is None or _contains(candidate, point)
        snapshot_match = snapshot is None or (
            _iou(snapshot, candidate) >= ANCHOR_MINIMUM_IOU
            and _center_displacement(snapshot, candidate)
            <= ANCHOR_MAXIMUM_CENTER_DISPLACEMENT
            and _area_fold_change(snapshot, candidate)
            <= ANCHOR_MAXIMUM_AREA_FOLD_CHANGE
        )
        if point_match and snapshot_match:
            matches.append((candidate_index, candidate))

    if len(matches) != 1:
        raise PreviewEvidenceError(
            f"{label} resolves to {len(matches)} candidates; expected exactly one"
        )
    candidate_index, box = matches[0]
    result = {
        "anchor_id": anchor_id,
        "frame": frame,
        "box_xyxy": list(box),
        "temporary_candidate_index": candidate_index,
    }
    if point is not None:
        result["clicked_point_xy"] = list(point)
    if snapshot is not None:
        result["box_snapshot_xyxy"] = list(snapshot)
    if note is not None:
        result["note"] = note
    result["_box"] = box
    return result


def validate_anchors(raw, sidecar):
    if raw.get("schema") != "blendcap-preview-target-anchors":
        raise PreviewEvidenceError("Unsupported anchor schema")
    if raw.get("version") != 1:
        raise PreviewEvidenceError("Unsupported anchor schema version; expected 1")
    track_id = raw.get("track_id")
    if not isinstance(track_id, str) or not track_id:
        raise PreviewEvidenceError("anchors track_id must be a non-empty string")
    coordinate_space = raw.get("coordinate_space")
    if coordinate_space != "source-pixels-xyxy":
        raise PreviewEvidenceError("anchors coordinate_space must be source-pixels-xyxy")
    _validate_source_metadata(raw.get("source"), sidecar)

    ranges = _validate_ranges(raw.get("forced_occlusion_ranges"), sidecar)
    forced_frames = _frames_in_ranges(ranges, sidecar["sample_indices"])

    raw_anchors = raw.get("anchors")
    if not isinstance(raw_anchors, list) or not raw_anchors:
        raise PreviewEvidenceError("anchors must contain at least one anchor")
    resolved = [
        _resolve_anchor(anchor, sidecar, f"anchors[{i}]")
        for i, anchor in enumerate(raw_anchors)
    ]
    ids = [item["anchor_id"] for item in resolved]
    if len(ids) != len(set(ids)):
        raise PreviewEvidenceError("anchor_id values must be unique")
    frames = [item["frame"] for item in resolved]
    if len(frames) != len(set(frames)):
        raise PreviewEvidenceError("only one anchor is allowed at each sampled frame")
    for item in resolved:
        if item["frame"] in forced_frames:
            raise PreviewEvidenceError(
                f"anchor {item['anchor_id']} is inside a forced occlusion range"
            )
    resolved.sort(key=lambda item: item["frame"])
    return {
        "track_id": track_id,
        "anchors": resolved,
        "forced_occlusion_ranges": ranges,
        "forced_frames": forced_frames,
    }
