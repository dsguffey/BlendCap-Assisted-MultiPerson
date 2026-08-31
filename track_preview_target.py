"""Build one conservative target track from anchored preview detections."""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


PARAMETERS = {
    "minimum_iou": 0.35,
    "maximum_center_displacement_diagonals": 0.30,
    "maximum_area_fold_change": 1.60,
    "minimum_score": 0.60,
    "minimum_runner_up_margin": 0.15,
}
BOX_TOLERANCE = 1e-4
ANCHOR_MINIMUM_IOU = 0.85
ANCHOR_MAXIMUM_CENTER_DISPLACEMENT = 0.10
ANCHOR_MAXIMUM_AREA_FOLD_CHANGE = 1.20


class TrackError(RuntimeError):
    """A user-facing schema, anchor, or tracking error."""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build a persistent single-target track from a version-2 preview "
            "sidecar and user-authored anchors."
        )
    )
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--anchors", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def _finite_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TrackError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise TrackError(f"{label} must be finite")
    return number


def _box(value, label, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise TrackError(f"{label} must be a four-value box")
    if not isinstance(value, list) or len(value) != 4:
        raise TrackError(f"{label} must be a four-value box")
    result = tuple(
        _finite_float(item, f"{label}[{i}]") for i, item in enumerate(value)
    )
    if result[2] <= result[0] or result[3] <= result[1]:
        raise TrackError(f"{label} has non-positive width or height")
    return result


def _point(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise TrackError(f"{label} must be a two-value point")
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


def _boxes_match(a, b):
    return all(abs(av - bv) <= BOX_TOLERANCE for av, bv in zip(a, b))


def _contains(box, point):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _load_json(path, label):
    if not path.is_file():
        raise TrackError(f"{label} not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackError(f"Cannot read {label.lower()} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrackError(f"{label} root must be a JSON object")
    return value


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise TrackError(f"Cannot hash sidecar {path}: {exc}") from exc
    return digest.hexdigest()


def validate_sidecar(raw, digest=""):
    if raw.get("version") != 2:
        raise TrackError(
            f"Unsupported sidecar version {raw.get('version')!r}; expected 2"
        )
    fps = _finite_float(raw.get("fps"), "sidecar fps")
    if fps <= 0:
        raise TrackError("sidecar fps must be positive")

    indices_raw = raw.get("sample_indices")
    selected_raw = raw.get("boxes")
    candidates_raw = raw.get("all_boxes")
    for value, label in (
        (indices_raw, "sample_indices"),
        (selected_raw, "boxes"),
        (candidates_raw, "all_boxes"),
    ):
        if not isinstance(value, list):
            raise TrackError(f"sidecar {label} must be a list")

    count = len(indices_raw)
    if count < 2:
        raise TrackError("sidecar needs at least two samples")
    if len(selected_raw) != count or len(candidates_raw) != count:
        raise TrackError(
            "sidecar sample_indices, boxes, and all_boxes lengths must match"
        )

    indices = []
    candidates = []
    for i, frame in enumerate(indices_raw):
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise TrackError(
                f"sidecar sample_indices[{i}] must be a non-negative integer"
            )
        if indices and frame <= indices[-1]:
            raise TrackError("sidecar sample_indices must be strictly increasing")
        indices.append(frame)
        _box(selected_raw[i], f"sidecar boxes[{i}]", allow_none=True)
        frame_candidates = candidates_raw[i]
        if not isinstance(frame_candidates, list):
            raise TrackError(f"sidecar all_boxes[{i}] must be a list")
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
        raise TrackError("sidecar must use one constant positive sample interval")

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
        raise TrackError("anchors source must be an object")
    expected_hash = source.get("sidecar_sha256")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or not expected_hash:
            raise TrackError("anchors source.sidecar_sha256 must be a string")
        if sidecar["sha256"] and expected_hash.lower() != sidecar["sha256"]:
            raise TrackError("anchor sidecar hash does not match the input sidecar")
    expected_fps = source.get("fps")
    if expected_fps is not None:
        expected_fps = _finite_float(expected_fps, "anchors source.fps")
        tolerance = max(0.001, sidecar["fps"] * 1e-6)
        if abs(expected_fps - sidecar["fps"]) > tolerance:
            raise TrackError("anchor FPS does not match the input sidecar")
    expected_interval = source.get("sampling_interval")
    if expected_interval is not None:
        if (
            isinstance(expected_interval, bool)
            or not isinstance(expected_interval, int)
            or expected_interval != sidecar["sampling_interval"]
        ):
            raise TrackError(
                "anchor sampling interval does not match the input sidecar"
            )


def _validate_ranges(raw_ranges, sidecar):
    if raw_ranges is None:
        raw_ranges = []
    if not isinstance(raw_ranges, list):
        raise TrackError("forced_occlusion_ranges must be a list")

    sampled = sidecar["frame_to_position"]
    ranges = []
    for i, item in enumerate(raw_ranges):
        if not isinstance(item, dict):
            raise TrackError(f"forced_occlusion_ranges[{i}] must be an object")
        start = item.get("start_frame")
        end = item.get("end_frame")
        for value, field in ((start, "start_frame"), (end, "end_frame")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TrackError(
                    f"forced_occlusion_ranges[{i}].{field} must be an integer"
                )
            if value not in sampled:
                raise TrackError(
                    f"forced_occlusion_ranges[{i}].{field} must be a sampled frame"
                )
        if start > end:
            raise TrackError(f"forced_occlusion_ranges[{i}] is reversed")
        note = item.get("note")
        if note is not None and not isinstance(note, str):
            raise TrackError(f"forced_occlusion_ranges[{i}].note must be a string")
        result = {"start_frame": start, "end_frame": end}
        if note is not None:
            result["note"] = note
        ranges.append(result)

    ranges.sort(key=lambda item: (item["start_frame"], item["end_frame"]))
    for previous, current in zip(ranges, ranges[1:]):
        if current["start_frame"] <= previous["end_frame"]:
            raise TrackError("forced occlusion ranges must not overlap")
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
        raise TrackError(f"{label} must be an object")
    anchor_id = anchor.get("anchor_id")
    if not isinstance(anchor_id, str) or not anchor_id:
        raise TrackError(f"{label}.anchor_id must be a non-empty string")
    frame = anchor.get("frame")
    if isinstance(frame, bool) or not isinstance(frame, int):
        raise TrackError(f"{label}.frame must be an integer")
    if frame not in sidecar["frame_to_position"]:
        raise TrackError(f"{label}.frame must be a sampled frame")

    point_raw = anchor.get("clicked_point_xy")
    snapshot_raw = anchor.get("box_snapshot_xyxy")
    if point_raw is None and snapshot_raw is None:
        raise TrackError(
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
        raise TrackError(f"{label}.note must be a string")

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
        raise TrackError(
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
        raise TrackError("Unsupported anchor schema")
    if raw.get("version") != 1:
        raise TrackError("Unsupported anchor schema version; expected 1")
    track_id = raw.get("track_id")
    if not isinstance(track_id, str) or not track_id:
        raise TrackError("anchors track_id must be a non-empty string")
    coordinate_space = raw.get("coordinate_space")
    if coordinate_space != "source-pixels-xyxy":
        raise TrackError("anchors coordinate_space must be source-pixels-xyxy")
    _validate_source_metadata(raw.get("source"), sidecar)

    ranges = _validate_ranges(raw.get("forced_occlusion_ranges"), sidecar)
    forced_frames = _frames_in_ranges(ranges, sidecar["sample_indices"])

    raw_anchors = raw.get("anchors")
    if not isinstance(raw_anchors, list) or not raw_anchors:
        raise TrackError("anchors must contain at least one anchor")
    resolved = [
        _resolve_anchor(anchor, sidecar, f"anchors[{i}]")
        for i, anchor in enumerate(raw_anchors)
    ]
    ids = [item["anchor_id"] for item in resolved]
    if len(ids) != len(set(ids)):
        raise TrackError("anchor_id values must be unique")
    frames = [item["frame"] for item in resolved]
    if len(frames) != len(set(frames)):
        raise TrackError("only one anchor is allowed at each sampled frame")
    for item in resolved:
        if item["frame"] in forced_frames:
            raise TrackError(
                f"anchor {item['anchor_id']} is inside a forced occlusion range"
            )
    resolved.sort(key=lambda item: item["frame"])
    return {
        "track_id": track_id,
        "anchors": resolved,
        "forced_occlusion_ranges": ranges,
        "forced_frames": forced_frames,
    }


def _candidate_metrics(reference, candidate):
    overlap = _iou(reference, candidate)
    displacement = _center_displacement(reference, candidate)
    area_change = _area_fold_change(reference, candidate)
    center_component = 1.0 - min(
        displacement / PARAMETERS["maximum_center_displacement_diagonals"],
        1.0,
    )
    area_component = 1.0 - min(
        math.log(area_change)
        / math.log(PARAMETERS["maximum_area_fold_change"]),
        1.0,
    )
    score = 0.55 * overlap + 0.30 * center_component + 0.15 * area_component
    return {
        "iou": overlap,
        "center_displacement_diagonals": displacement,
        "area_fold_change": area_change,
        "score": score,
    }


def _match_candidate(reference, candidates):
    if not candidates:
        return None, "detector-miss"
    gated = []
    for candidate_index, candidate in enumerate(candidates):
        metrics = _candidate_metrics(reference, candidate)
        if (
            metrics["iou"] >= PARAMETERS["minimum_iou"]
            and metrics["center_displacement_diagonals"]
            <= PARAMETERS["maximum_center_displacement_diagonals"]
            and metrics["area_fold_change"]
            <= PARAMETERS["maximum_area_fold_change"]
        ):
            gated.append((metrics["score"], candidate_index, candidate, metrics))
    if not gated:
        return None, "no-reliable-candidate"
    gated.sort(key=lambda item: (-item[0], item[1]))
    best = gated[0]
    if best[0] < PARAMETERS["minimum_score"]:
        return None, "score-below-minimum"
    if (
        len(gated) > 1
        and best[0] - gated[1][0] < PARAMETERS["minimum_runner_up_margin"]
    ):
        return None, "non-unique-candidate"
    return {
        "box_xyxy": list(best[2]),
        "temporary_candidate_index": best[1],
        "score": best[3]["score"],
        "iou": best[3]["iou"],
        "center_displacement_diagonals": best[3][
            "center_displacement_diagonals"
        ],
        "area_fold_change": best[3]["area_fold_change"],
        "_box": best[2],
    }, None


def run_direction(sidecar, frames, reference_box, blocked_frames=frozenset()):
    results = {}
    reference = reference_box
    for frame in frames:
        if frame in blocked_frames:
            results[frame] = {"kind": "blocked", "reason": "user-forced"}
            break
        position = sidecar["frame_to_position"][frame]
        hypothesis, reason = _match_candidate(
            reference, sidecar["all_boxes"][position]
        )
        if hypothesis is None:
            results[frame] = {"kind": "rejected", "reason": reason}
            break
        results[frame] = {"kind": "hypothesis", "value": hypothesis}
        reference = hypothesis["_box"]
    return results


def _public_hypothesis(hypothesis):
    return {
        key: value
        for key, value in hypothesis.items()
        if key != "_box"
    }


def _anchor_record(frame, anchor):
    return {
        "frame": frame,
        "state": "anchor",
        "box_xyxy": list(anchor["_box"]),
        "temporary_candidate_index": anchor["temporary_candidate_index"],
        "evidence": "user-anchor",
        "reason": None,
        "hypotheses": None,
    }


def _forced_record(frame):
    return {
        "frame": frame,
        "state": "occluded",
        "box_xyxy": None,
        "temporary_candidate_index": None,
        "evidence": "user-forced",
        "reason": "forced-occlusion-range",
        "hypotheses": None,
    }


def _untracked_record(frame):
    return {
        "frame": frame,
        "state": "untracked",
        "box_xyxy": None,
        "temporary_candidate_index": None,
        "evidence": None,
        "reason": "outside-anchor-coverage",
        "hypotheses": None,
    }


def _combine_frame(frame, candidates, forward, backward):
    forward_hypothesis = (
        forward["value"] if forward and forward["kind"] == "hypothesis" else None
    )
    backward_hypothesis = (
        backward["value"]
        if backward and backward["kind"] == "hypothesis"
        else None
    )
    hypotheses = {
        "forward": (
            _public_hypothesis(forward_hypothesis)
            if forward_hypothesis is not None
            else None
        ),
        "backward": (
            _public_hypothesis(backward_hypothesis)
            if backward_hypothesis is not None
            else None
        ),
    }

    if forward_hypothesis is not None and backward_hypothesis is not None:
        if _boxes_match(forward_hypothesis["_box"], backward_hypothesis["_box"]):
            return {
                "frame": frame,
                "state": "tracked",
                "box_xyxy": list(forward_hypothesis["_box"]),
                "temporary_candidate_index": forward_hypothesis[
                    "temporary_candidate_index"
                ],
                "evidence": "bidirectional",
                "reason": None,
                "hypotheses": hypotheses,
            }
        return {
            "frame": frame,
            "state": "ambiguous",
            "box_xyxy": None,
            "temporary_candidate_index": None,
            "evidence": None,
            "reason": "forward-backward-disagreement",
            "hypotheses": hypotheses,
        }

    if forward_hypothesis is not None or backward_hypothesis is not None:
        return {
            "frame": frame,
            "state": "ambiguous",
            "box_xyxy": None,
            "temporary_candidate_index": None,
            "evidence": None,
            "reason": "single-direction-only",
            "hypotheses": hypotheses,
        }

    reason = "detector-miss" if not candidates else "no-bidirectional-target-match"
    return {
        "frame": frame,
        "state": "occluded",
        "box_xyxy": None,
        "temporary_candidate_index": None,
        "evidence": None,
        "reason": reason,
        "hypotheses": hypotheses,
    }


def _public_anchor(anchor):
    return {key: value for key, value in anchor.items() if key != "_box"}


def build_track(sidecar, anchor_document):
    validated = validate_anchors(anchor_document, sidecar)
    anchors = validated["anchors"]
    forced_frames = validated["forced_frames"]
    anchor_by_frame = {item["frame"]: item for item in anchors}
    records = {}
    for frame in sidecar["sample_indices"]:
        if frame in forced_frames:
            records[frame] = _forced_record(frame)
        elif frame in anchor_by_frame:
            records[frame] = _anchor_record(frame, anchor_by_frame[frame])
        else:
            records[frame] = _untracked_record(frame)

    interval_diagnostics = []
    positions = sidecar["frame_to_position"]
    for left, right in zip(anchors, anchors[1:]):
        left_position = positions[left["frame"]]
        right_position = positions[right["frame"]]
        interior_frames = sidecar["sample_indices"][
            left_position + 1 : right_position
        ]
        if any(frame in forced_frames for frame in interior_frames):
            interval_diagnostics.append(
                {
                    "start_anchor_id": left["anchor_id"],
                    "end_anchor_id": right["anchor_id"],
                    "status": "blocked-by-forced-occlusion",
                }
            )
            continue

        forward = run_direction(sidecar, interior_frames, left["_box"])
        backward = run_direction(
            sidecar, list(reversed(interior_frames)), right["_box"]
        )
        for frame in interior_frames:
            position = positions[frame]
            records[frame] = _combine_frame(
                frame,
                sidecar["all_boxes"][position],
                forward.get(frame),
                backward.get(frame),
            )
        interval_diagnostics.append(
            {
                "start_anchor_id": left["anchor_id"],
                "end_anchor_id": right["anchor_id"],
                "status": "evaluated",
            }
        )

    return {
        "schema": "blendcap-preview-target-track",
        "version": 1,
        "track_id": validated["track_id"],
        "source": {
            "sidecar_sha256": sidecar["sha256"],
            "fps": sidecar["fps"],
            "sampling_interval": sidecar["sampling_interval"],
        },
        "parameters": dict(PARAMETERS),
        "resolved_anchors": [_public_anchor(anchor) for anchor in anchors],
        "forced_occlusion_ranges": validated["forced_occlusion_ranges"],
        "interval_diagnostics": interval_diagnostics,
        "frames": [records[frame] for frame in sidecar["sample_indices"]],
    }


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_output_path(output, sidecar_path, anchors_path):
    if output.suffix.lower() != ".json":
        raise TrackError("Output filename must end in .json")
    if _resolved(output) in (_resolved(sidecar_path), _resolved(anchors_path)):
        raise TrackError("Output must not overwrite the sidecar or anchor file")
    if output.exists():
        raise TrackError(f"Output already exists; choose a new path: {output}")


def write_track(path, track):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(track, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    except OSError as exc:
        raise TrackError(f"Cannot write target track {path}: {exc}") from exc


def main():
    args = parse_args()
    try:
        sidecar_raw = _load_json(args.sidecar, "Sidecar")
        anchor_raw = _load_json(args.anchors, "Anchor file")
        sidecar = validate_sidecar(sidecar_raw, _sha256(args.sidecar))
        track = build_track(sidecar, anchor_raw)
        if args.validate_only:
            print(
                f"Validated {len(track['resolved_anchors'])} anchors and "
                f"{len(track['forced_occlusion_ranges'])} forced ranges"
            )
            return 0
        validate_output_path(args.output, args.sidecar, args.anchors)
        write_track(args.output, track)
    except TrackError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    counts = {}
    for frame in track["frames"]:
        counts[frame["state"]] = counts.get(frame["state"], 0) + 1
    summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    print(f"Saved target track: {args.output}")
    print(f"Frame states: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
