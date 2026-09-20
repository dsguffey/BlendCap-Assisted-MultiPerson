#!/usr/bin/env python3
"""Build an offline, non-authoritative target-review packet.

The packet presents conservative geometry evidence and, optionally, an
existing fusion diagnostic.  It never runs appearance inference and never
changes a track.  TEMP candidate positions are deliberately frame-local.
"""

from __future__ import annotations

import argparse
import colorsys
import copy
import hashlib
import html
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from track_preview_target import TrackError, build_track, validate_sidecar


REVIEW_SCHEMA = "blendcap-preview-target-review-packet"
REVIEW_VERSION = 1
DECISION_SCHEMA = "blendcap-preview-target-review-decisions"
DECISION_VERSION = 1
ALLOWED_STATES = {"ambiguous", "untracked", "occluded", "anchor", "tracked"}


class ReviewError(ValueError):
    """A user-facing review packet validation or publication error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReviewError(f"Cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must contain a JSON object")
    return value


def parse_target_paths(values: list[str], option: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ReviewError(f"{option} must use TARGET_ID=PATH")
        target_id, raw_path = value.split("=", 1)
        if not target_id or target_id.strip() != target_id:
            raise ReviewError(f"{option} target ID must be a non-empty trimmed string")
        if target_id in result:
            raise ReviewError(f"Duplicate {option} target ID: {target_id}")
        if not raw_path:
            raise ReviewError(f"{option} path must not be empty")
        result[target_id] = Path(raw_path)
    return result


def parse_frame_range(value: str | None, sidecar: dict) -> tuple[int, int] | None:
    if value is None:
        return None
    pieces = value.split(":")
    if len(pieces) != 2:
        raise ReviewError("--frame-range must use START:END")
    try:
        start, end = (int(piece) for piece in pieces)
    except ValueError as exc:
        raise ReviewError("--frame-range boundaries must be integers") from exc
    if start > end:
        raise ReviewError("--frame-range is reversed")
    sampled = sidecar["frame_to_position"]
    if start not in sampled:
        raise ReviewError("--frame-range start must be an exact sampled frame")
    if end not in sampled:
        raise ReviewError("--frame-range end must be an exact sampled frame")
    return start, end


def _load_and_validate_track(path: Path, anchors_path: Path, sidecar: dict) -> tuple[dict, dict]:
    anchors_raw = load_json(anchors_path, "anchor file")
    track_raw = load_json(path, "geometry track")
    try:
        rebuilt = build_track(sidecar, copy.deepcopy(anchors_raw))
    except TrackError as exc:
        raise ReviewError(f"Invalid anchor file {anchors_path}: {exc}") from exc
    if track_raw != rebuilt:
        raise ReviewError(
            f"Geometry track {path} does not exactly match a rebuild from its anchors"
        )
    return anchors_raw, track_raw


def validate_inputs(
    sidecar_path: Path,
    anchor_paths: dict[str, Path],
    track_paths: dict[str, Path],
    fusion_path: Path | None = None,
) -> dict:
    if not anchor_paths:
        raise ReviewError("At least one --anchors input is required")
    if set(anchor_paths) != set(track_paths):
        raise ReviewError("--anchors and --track target-ID sets must match exactly")
    sidecar_raw = load_json(sidecar_path, "sidecar")
    sidecar_hash = sha256_file(sidecar_path)
    try:
        sidecar = validate_sidecar(sidecar_raw, sidecar_hash)
    except TrackError as exc:
        raise ReviewError(f"Invalid sidecar: {exc}") from exc

    anchors: dict[str, dict] = {}
    tracks: dict[str, dict] = {}
    provenance: dict[str, dict] = {}
    for target_id in sorted(anchor_paths):
        anchors_raw, track_raw = _load_and_validate_track(
            track_paths[target_id], anchor_paths[target_id], sidecar
        )
        anchors[target_id] = anchors_raw
        tracks[target_id] = track_raw
        provenance[target_id] = {
            "anchors_sha256": sha256_file(anchor_paths[target_id]),
            "track_sha256": sha256_file(track_paths[target_id]),
            "track_id": track_raw["track_id"],
        }

    fusion = None
    fusion_hash = None
    if fusion_path is not None:
        fusion = load_json(fusion_path, "fusion report")
        fusion_hash = sha256_file(fusion_path)
        _validate_fusion(fusion, sidecar, tracks, provenance)

    return {
        "sidecar": sidecar,
        "sidecar_raw": sidecar_raw,
        "anchors": anchors,
        "tracks": tracks,
        "target_provenance": provenance,
        "fusion": fusion,
        "fusion_sha256": fusion_hash,
    }


def _validate_fusion(
    fusion: dict,
    sidecar: dict,
    tracks: dict[str, dict],
    target_provenance: dict[str, dict] | None = None,
) -> None:
    if fusion.get("schema") != "blendcap-preview-geometry-appearance-fusion-diagnostic":
        raise ReviewError("Unsupported fusion schema")
    if fusion.get("version") != 1:
        raise ReviewError("Unsupported fusion version; expected 1")
    source = fusion.get("source")
    if not isinstance(source, dict):
        raise ReviewError("fusion source must be an object")
    if source.get("sidecar_sha256") != sidecar["sha256"]:
        raise ReviewError("fusion sidecar hash does not match")
    if source.get("fps") != sidecar["fps"]:
        raise ReviewError("fusion FPS does not match")
    if source.get("sampling_interval") != sidecar["sampling_interval"]:
        raise ReviewError("fusion sampling interval does not match")
    policy = fusion.get("policy")
    if not isinstance(policy, dict) or policy.get("appearance_is_authoritative") is not False:
        raise ReviewError("fusion must explicitly mark appearance as non-authoritative")
    if target_provenance is not None:
        provenance = fusion.get("provenance")
        if not isinstance(provenance, dict):
            raise ReviewError("fusion provenance must be an object")
        target_values = provenance.get("targets")
        if not isinstance(target_values, list):
            raise ReviewError("fusion provenance.targets must be a list")
        by_target = {}
        for i, value in enumerate(target_values):
            if not isinstance(value, dict):
                raise ReviewError(f"fusion provenance.targets[{i}] must be an object")
            target_id = value.get("target_id")
            if target_id in by_target or target_id not in target_provenance:
                raise ReviewError("fusion provenance contains an invalid target ID")
            by_target[target_id] = value
        if set(by_target) != set(target_provenance):
            raise ReviewError("fusion provenance target-ID set does not match")
        for target_id, expected in target_provenance.items():
            value = by_target[target_id]
            if value.get("track_id") != expected["track_id"]:
                raise ReviewError(f"fusion target {target_id} track ID does not match")
            if value.get("anchors_sha256") != expected["anchors_sha256"]:
                raise ReviewError(f"fusion target {target_id} anchor hash does not match")
            if value.get("geometry_track_sha256") != expected["track_sha256"]:
                raise ReviewError(f"fusion target {target_id} track hash does not match")
    frames = fusion.get("frames")
    if not isinstance(frames, list):
        raise ReviewError("fusion frames must be a list")
    expected_targets = set(tracks)
    track_maps = {
        target: {item["frame"]: item for item in track["frames"]}
        for target, track in tracks.items()
    }
    seen_frames = set()
    for i, item in enumerate(frames):
        if not isinstance(item, dict):
            raise ReviewError(f"fusion frames[{i}] must be an object")
        frame = item.get("frame")
        if frame not in sidecar["frame_to_position"] or frame in seen_frames:
            raise ReviewError(f"fusion frames[{i}].frame is invalid or duplicated")
        seen_frames.add(frame)
        target_items = item.get("targets")
        if not isinstance(target_items, list):
            raise ReviewError(f"fusion frames[{i}].targets must be a list")
        by_target = {}
        for j, target_item in enumerate(target_items):
            if not isinstance(target_item, dict):
                raise ReviewError(f"fusion frames[{i}].targets[{j}] must be an object")
            target_id = target_item.get("target_id")
            if target_id in by_target or target_id not in expected_targets:
                raise ReviewError(f"fusion frames[{i}] has an invalid target ID")
            by_target[target_id] = target_item
        if set(by_target) != expected_targets:
            raise ReviewError(f"fusion frame {frame} target-ID set does not match")
        candidates = sidecar["all_boxes"][sidecar["frame_to_position"][frame]]
        for target_id, target_item in by_target.items():
            geometry = target_item.get("source_geometry")
            expected_geometry = {
                key: copy.deepcopy(value)
                for key, value in track_maps[target_id][frame].items()
                if key != "frame"
            }
            if geometry != expected_geometry:
                raise ReviewError(
                    f"fusion frame {frame} target {target_id} geometry differs from track"
                )
            if target_item.get("state") != expected_geometry["state"]:
                raise ReviewError(
                    f"fusion frame {frame} target {target_id} state differs from geometry"
                )
            proposal = target_item.get("diagnostic_appearance_proposal")
            if proposal is not None:
                if not isinstance(proposal, dict):
                    raise ReviewError("fusion appearance proposal must be an object or null")
                status = proposal.get("status")
                if status == "no-proposal":
                    if set(proposal) != {"status", "reason"}:
                        raise ReviewError("fusion no-proposal evidence contains unsupported fields")
                    if not isinstance(proposal.get("reason"), str) or not proposal["reason"]:
                        raise ReviewError("fusion no-proposal evidence requires a reason")
                    if proposal.get("temporary_candidate_index") is not None or proposal.get("box_xyxy") is not None:
                        raise ReviewError("fusion no-proposal evidence must not name a candidate")
                    continue
                if status != "candidate-proposed-unthresholded":
                    raise ReviewError("fusion proposal has an unsupported status")
                allowed_proposal_fields = {
                    "status",
                    "temporary_candidate_index",
                    "threshold_status",
                    "appearance_is_authoritative",
                    "box_xyxy",
                }
                if set(proposal) != allowed_proposal_fields:
                    raise ReviewError("fusion proposal contains unsupported fields")
                index = proposal.get("temporary_candidate_index")
                if isinstance(index, bool) or not isinstance(index, int):
                    raise ReviewError("fusion proposal candidate index must be an integer")
                if index < 0 or index >= len(candidates):
                    raise ReviewError("fusion proposal candidate index is out of range")
                box = proposal.get("box_xyxy")
                if box != list(candidates[index]):
                    raise ReviewError("fusion proposal box does not match the sidecar")
                if expected_geometry["state"] != "ambiguous":
                    raise ReviewError("fusion proposals may appear only on ambiguous targets")
                if proposal.get("appearance_is_authoritative") is not False:
                    raise ReviewError("fusion proposal must be explicitly non-authoritative")
                if proposal.get("threshold_status") != "not-configured":
                    raise ReviewError("fusion proposal must not configure a threshold")


def _fusion_map(fusion: dict | None) -> dict[tuple[int, str], dict]:
    if fusion is None:
        return {}
    result = {}
    for frame_item in fusion["frames"]:
        for target_item in frame_item["targets"]:
            result[(frame_item["frame"], target_item["target_id"])] = target_item
    return result


def _public_direction(value):
    if value is None:
        return None
    if value.get("kind") != "hypothesis":
        return copy.deepcopy(value)
    return {
        "kind": "hypothesis",
        "value": {
            key: copy.deepcopy(item)
            for key, item in value["value"].items()
            if key != "_box"
        },
    }


def _directional_diagnostics(sidecar: dict, track: dict) -> dict[int, dict]:
    """Recreate chain-stop context using the tracker's validated algorithm."""
    from track_preview_target import run_direction

    result = {}
    positions = sidecar["frame_to_position"]
    anchors = track["resolved_anchors"]
    diagnostics = track["interval_diagnostics"]
    for left, right, interval in zip(anchors, anchors[1:], diagnostics):
        interior = sidecar["sample_indices"][
            positions[left["frame"]] + 1 : positions[right["frame"]]
        ]
        base = {
            "start_anchor_id": left["anchor_id"],
            "end_anchor_id": right["anchor_id"],
            "interval_status": interval["status"],
            "forward_stop": None,
            "backward_stop": None,
        }
        if interval["status"] != "evaluated":
            for frame in interior:
                result[frame] = copy.deepcopy(base)
            continue
        forward = run_direction(sidecar, interior, tuple(left["box_xyxy"]))
        backward = run_direction(sidecar, list(reversed(interior)), tuple(right["box_xyxy"]))
        for direction, values in (("forward", forward), ("backward", backward)):
            for frame, value in values.items():
                if value["kind"] != "hypothesis":
                    base[f"{direction}_stop"] = {
                        "frame": frame,
                        "kind": value["kind"],
                        "reason": value.get("reason"),
                    }
                    break
        for frame in interior:
            item = copy.deepcopy(base)
            item["forward_at_frame"] = _public_direction(forward.get(frame))
            item["backward_at_frame"] = _public_direction(backward.get(frame))
            result[frame] = item
    return result


def build_review_model(
    context: dict,
    video_sha256: str,
    frame_range: tuple[int, int] | None = None,
    include_states: set[str] | None = None,
) -> dict:
    include_states = include_states or {"ambiguous"}
    if not include_states or not include_states <= ALLOWED_STATES:
        raise ReviewError("include states contain an unsupported geometry state")
    sidecar = context["sidecar"]
    start, end = frame_range or (
        sidecar["sample_indices"][0], sidecar["sample_indices"][-1]
    )
    fusion_by_key = _fusion_map(context["fusion"])
    directional = {
        target_id: _directional_diagnostics(sidecar, track)
        for target_id, track in context["tracks"].items()
    }
    queue = []
    frame_records = {}
    for target_id in sorted(context["tracks"]):
        for record in context["tracks"][target_id]["frames"]:
            frame = record["frame"]
            if not (start <= frame <= end) or record["state"] not in include_states:
                continue
            queue.append({"target_id": target_id, "frame": frame})
            if frame not in frame_records:
                position = sidecar["frame_to_position"][frame]
                frame_records[frame] = {
                    "frame": frame,
                    "timestamp_seconds": frame / sidecar["fps"],
                    "candidates": copy.deepcopy(sidecar["all_boxes"][position]),
                    "targets": {},
                    "image": f"images/frame_{frame:06d}.png",
                }
            fusion_item = fusion_by_key.get((frame, target_id))
            appearance_diagnostic = (
                copy.deepcopy(fusion_item.get("diagnostic_appearance_proposal"))
                if fusion_item is not None
                else None
            )
            frame_records[frame]["targets"][target_id] = {
                "geometry": copy.deepcopy(record),
                "directional_chain": copy.deepcopy(
                    directional[target_id].get(frame)
                ),
                "appearance_suggestion": (
                    copy.deepcopy(appearance_diagnostic)
                    if isinstance(appearance_diagnostic, dict)
                    and appearance_diagnostic.get("status")
                    == "candidate-proposed-unthresholded"
                    else None
                ),
                "appearance_diagnostic": appearance_diagnostic,
            }
    if not queue:
        raise ReviewError("No review items match the requested range and states")
    review_id = hashlib.sha256(
        json.dumps(
            {
                "sidecar": sidecar["sha256"],
                "targets": context["target_provenance"],
                "fusion": context["fusion_sha256"],
                "video": video_sha256,
                "range": [start, end],
                "states": sorted(include_states),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": REVIEW_SCHEMA,
        "version": REVIEW_VERSION,
        "review_id": f"review-{review_id}",
        "source": {
            "sidecar_sha256": sidecar["sha256"],
            "video_sha256": video_sha256,
            "fusion_sha256": context["fusion_sha256"],
            "fps": sidecar["fps"],
            "sampling_interval": sidecar["sampling_interval"],
            "targets": copy.deepcopy(context["target_provenance"]),
        },
        "target_ids": sorted(context["tracks"]),
        "sample_indices": list(sidecar["sample_indices"]),
        "forced_occlusion_ranges": {
            target_id: copy.deepcopy(track["forced_occlusion_ranges"])
            for target_id, track in context["tracks"].items()
        },
        "frame_range": [start, end],
        "included_geometry_states": sorted(include_states),
        "queue": queue,
        "frames": [frame_records[frame] for frame in sorted(frame_records)],
        "policy": {
            "appearance_is_non_authoritative": True,
            "appearance_never_preselected": True,
            "temporary_candidate_indexes_are_per_frame_only": True,
        },
    }


def _marker_positions(boxes: list[list[float]], width: int, height: int) -> list[tuple[int, int]]:
    """Return separated marker positions; leader lines connect them to boxes."""
    result: list[tuple[int, int]] = []
    radius = max(10, min(width, height) // 55)
    for box in boxes:
        preferred = (int(box[0]) + radius, int(box[1]) + radius)
        candidates = [preferred]
        for ring in range(1, 12):
            step = radius * 3 * ring
            candidates.extend(
                [
                    (preferred[0] + step, preferred[1]),
                    (preferred[0], preferred[1] + step),
                    (preferred[0] - step, preferred[1]),
                    (preferred[0], preferred[1] - step),
                ]
            )
        selected = None
        for x, y in candidates:
            x = min(max(radius + 2, x), width - radius - 2)
            y = min(max(radius + 2, y), height - radius - 2)
            if all(math.hypot(x - px, y - py) >= radius * 2.5 for px, py in result):
                selected = (x, y)
                break
        if selected is None:
            raise ReviewError("Cannot place collision-free candidate markers")
        result.append(selected)
    return result


def _candidate_color(index: int, count: int) -> tuple[int, int, int]:
    hue = (index / max(count, 1) + 0.11) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.78, 1.0)
    return int(blue * 255), int(green * 255), int(red * 255)


def _render_panel(cv2, frame_image, frame: int, sidecar: dict, current: bool):
    image = frame_image.copy()
    height, width = image.shape[:2]
    position = sidecar["frame_to_position"][frame]
    boxes = sidecar["all_boxes"][position]
    markers = _marker_positions(boxes, width, height)
    for index, (box, marker) in enumerate(zip(boxes, markers)):
        color = _candidate_color(index, len(boxes))
        x1, y1, x2, y2 = (int(round(value)) for value in box)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 3 if current else 2)
        origin = (x1, y1)
        cv2.line(image, origin, marker, color, 2)
        cv2.circle(image, marker, max(10, min(width, height) // 55), color, -1)
        cv2.putText(image, str(index), (marker[0] - 6, marker[1] + 7), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, cv2.LINE_AA)
    header = f"{'REVIEW' if current else 'CONTEXT'} frame {frame}  {frame / sidecar['fps']:.3f}s"
    cv2.rectangle(image, (0, 0), (width, 42), (20, 20, 20), -1)
    cv2.putText(image, header, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (245, 245, 245), 2, cv2.LINE_AA)
    legend_height = 52 + 28 * len(boxes)
    canvas = cv2.copyMakeBorder(image, 0, legend_height, 0, 0, cv2.BORDER_CONSTANT, value=(25, 25, 25))
    cv2.putText(canvas, "TEMP markers are per-frame positions, not identities", (12, height + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    for index in range(len(boxes)):
        color = _candidate_color(index, len(boxes))
        y = height + 52 + 27 * index
        cv2.circle(canvas, (24, y - 6), 9, color, -1)
        cv2.putText(canvas, f"TEMP {index}", (44, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (245, 245, 245), 1, cv2.LINE_AA)
    return canvas


def render_review_images(video_path: Path, sidecar: dict, model: dict, output_dir: Path) -> dict[str, str]:
    try:
        import cv2
    except ImportError as exc:
        raise ReviewError("OpenCV is required to render a review packet") from exc
    review_frames = sorted(item["frame"] for item in model["frames"])
    indices = sidecar["sample_indices"]
    positions = sidecar["frame_to_position"]
    needed = set()
    neighbors = {}
    for frame in review_frames:
        position = positions[frame]
        triplet = [indices[max(0, position - 1)], frame, indices[min(len(indices) - 1, position + 1)]]
        neighbors[frame] = triplet
        needed.update(triplet)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise ReviewError(f"Cannot open source video {video_path}")
    decoded_fps = float(capture.get(cv2.CAP_PROP_FPS))
    tolerance = max(0.001, sidecar["fps"] * 1e-6)
    if not math.isfinite(decoded_fps) or decoded_fps <= 0 or abs(decoded_fps - sidecar["fps"]) > tolerance:
        capture.release()
        raise ReviewError(
            f"Source video FPS {decoded_fps!r} does not match sidecar FPS {sidecar['fps']!r}"
        )
    decoded = {}
    maximum = max(needed)
    source_frame = 0
    try:
        while source_frame <= maximum:
            ok, image = capture.read()
            if not ok:
                raise ReviewError(f"Video ended before required source frame {maximum}")
            if source_frame in needed:
                decoded[source_frame] = image.copy()
            source_frame += 1
    finally:
        capture.release()
    if set(decoded) != needed:
        raise ReviewError("Not every requested context frame was decoded")

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True)
    hashes = {}
    for review_frame in review_frames:
        panels = [
            _render_panel(cv2, decoded[frame], frame, sidecar, frame == review_frame)
            for frame in neighbors[review_frame]
        ]
        max_height = max(panel.shape[0] for panel in panels)
        padded = [
            cv2.copyMakeBorder(panel, 0, max_height - panel.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(25, 25, 25))
            for panel in panels
        ]
        composite = cv2.hconcat(padded)
        destination = image_dir / f"frame_{review_frame:06d}.png"
        if not cv2.imwrite(str(destination), composite):
            raise ReviewError(f"Failed to write {destination}")
        hashes[destination.relative_to(output_dir).as_posix()] = sha256_file(destination)
    return hashes


def _html_document(model: dict) -> str:
    data = json.dumps(model, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(model["review_id"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font:16px system-ui,sans-serif;margin:0;background:#17191d;color:#f2f4f7}}header,main{{max-width:1500px;margin:auto;padding:14px}}
.warning{{background:#4a3210;border:2px solid #e9a83a;padding:10px}}img{{width:100%;height:auto;background:#222}}label{{display:block;margin:.7rem 0 .2rem}}
select,input,textarea,button{{font:inherit;padding:.45rem}}textarea{{width:100%;min-height:4rem;box-sizing:border-box}}.row{{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}}
.meta{{color:#c6cbd3}}button{{cursor:pointer}}#status.bad{{color:#ff9b9b}}#status.good{{color:#8ee6a1}}.suggestion{{border-left:4px solid #d98cff;padding-left:10px}}
</style></head><body><header><h1>Target review</h1><div class="warning"><strong>Appearance is non-authoritative.</strong> Suggestions are never preselected. TEMP numbers are frame-local positions, not identities.</div></header>
<main><div class="row"><button id="prev">Previous</button><button id="next">Next</button><span id="progress"></span></div><h2 id="heading"></h2><p id="meta" class="meta"></p><img id="image" alt="Three-panel candidate context">
<pre id="geometry"></pre><p id="suggestion" class="suggestion"></p>
<label for="action">Human decision</label><select id="action"><option value="">Select an action…</option><option value="select-existing-candidate">Select existing candidate</option><option value="merged-shared-detection">Merged/shared detection</option><option value="no-valid-candidate">No valid candidate</option><option value="not-visible">Not visible</option><option value="uncertain">Uncertain</option></select>
<div id="candidateRow"><label for="candidate">Frame-local candidate</label><select id="candidate"></select></div>
<label for="replace">Forced range operation</label><select id="replace"></select>
<div class="row"><div><label for="start">Range start</label><input id="start" type="number"></div><div><label for="end">Range end</label><input id="end" type="number"></div></div>
<label for="note">Note</label><textarea id="note"></textarea><div class="row"><button id="undo">Undo</button><button id="redo">Redo</button><button id="export">Export decisions</button><span id="status"></span></div></main>
<script>const packet={data};const key='blendcap-review:'+packet.review_id;let index=0;let decisions={{}};let undoStack=[];let redoStack=[];
const $=id=>document.getElementById(id);const sampled=new Set(packet.sample_indices);const current=()=>packet.queue[index];const itemKey=i=>i.target_id+'@'+i.frame;const frameData=f=>packet.frames.find(x=>x.frame===f);
function snapshot(){{return JSON.stringify(decisions)}}function save(){{localStorage.setItem(key,JSON.stringify(decisions))}}function restore(s){{decisions=JSON.parse(s);save();render()}}
function valid(d,i){{if(!d||!d.action||typeof d.note!=='string')return false;const f=frameData(i.frame),candidate=Number.isInteger(d.temporary_candidate_index)&&d.temporary_candidate_index>=0&&d.temporary_candidate_index<f.candidates.length,range=Number.isInteger(d.start_frame)&&Number.isInteger(d.end_frame)&&sampled.has(d.start_frame)&&sampled.has(d.end_frame)&&packet.frame_range[0]<=d.start_frame&&d.start_frame<=i.frame&&i.frame<=d.end_frame&&d.end_frame<=packet.frame_range[1],replace=!d.replaces_forced_range||(packet.frame_range[0]<=d.replaces_forced_range.start_frame&&d.replaces_forced_range.end_frame<=packet.frame_range[1]&&packet.forced_occlusion_ranges[i.target_id].some(r=>r.start_frame===d.replaces_forced_range.start_frame&&r.end_frame===d.replaces_forced_range.end_frame));if(d.action==='select-existing-candidate')return candidate;if(d.action==='merged-shared-detection')return candidate&&d.note.trim()&&range&&replace;if(d.action==='no-valid-candidate')return d.note.trim()&&range&&replace;if(d.action==='not-visible')return range&&replace;return d.action==='uncertain'}}
function store(){{const i=current(),f=frameData(i.frame),action=$('action').value;undoStack.push(snapshot());redoStack=[];let d={{target_id:i.target_id,frame:i.frame,action,note:$('note').value}};if(['select-existing-candidate','merged-shared-detection'].includes(action)&&$('candidate').value!=='')d.temporary_candidate_index=Number($('candidate').value);if(['merged-shared-detection','no-valid-candidate','not-visible'].includes(action)){{if($('start').value!=='')d.start_frame=Number($('start').value);if($('end').value!=='')d.end_frame=Number($('end').value);if($('replace').value){{const r=$('replace').value.split(':').map(Number);d.replaces_forced_range={{start_frame:r[0],end_frame:r[1]}}}}}}decisions[itemKey(i)]=d;save();renderProgress()}}
function renderProgress(){{const complete=packet.queue.filter(i=>valid(decisions[itemKey(i)],i)).length;$('progress').textContent=complete+'/'+packet.queue.length+' complete'}}
function render(){{const i=current(),f=frameData(i.frame),d=decisions[itemKey(i)]||{{}},t=f.targets[i.target_id];$('heading').textContent='Frame '+i.frame+' — target '+i.target_id;$('meta').textContent=f.timestamp_seconds.toFixed(3)+' s · '+f.candidates.length+' candidates · item '+(index+1)+'/'+packet.queue.length;$('image').src=f.image;$('geometry').textContent='Geometry: '+JSON.stringify(t.geometry,null,2)+'\\nDirectional chain: '+JSON.stringify(t.directional_chain,null,2);const s=t.appearance_suggestion;$('suggestion').textContent=s?'NON-AUTHORITATIVE appearance suggestion: '+JSON.stringify(s):'No appearance suggestion available. Diagnostic: '+JSON.stringify(t.appearance_diagnostic);$('candidate').innerHTML='<option value="">Choose TEMP…</option>'+f.candidates.map((_,n)=>'<option value="'+n+'">TEMP '+n+'</option>').join('');$('replace').innerHTML='<option value="">Add a new forced range</option>'+packet.forced_occlusion_ranges[i.target_id].map(r=>'<option value="'+r.start_frame+':'+r.end_frame+'">Replace '+r.start_frame+'–'+r.end_frame+'</option>').join('');$('action').value=d.action||'';$('candidate').value=d.temporary_candidate_index===undefined?'':String(d.temporary_candidate_index);$('replace').value=d.replaces_forced_range?d.replaces_forced_range.start_frame+':'+d.replaces_forced_range.end_frame:'';$('start').value=d.start_frame===undefined?i.frame:d.start_frame;$('end').value=d.end_frame===undefined?i.frame:d.end_frame;$('note').value=d.note||'';renderProgress()}}
['action','candidate','replace','start','end','note'].forEach(id=>$(id).addEventListener('change',store));$('prev').onclick=()=>{{index=Math.max(0,index-1);render()}};$('next').onclick=()=>{{index=Math.min(packet.queue.length-1,index+1);render()}};document.onkeydown=e=>{{if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft')$('prev').click();if(e.key==='ArrowRight')$('next').click()}};
$('undo').onclick=()=>{{if(undoStack.length){{redoStack.push(snapshot());restore(undoStack.pop())}}}};$('redo').onclick=()=>{{if(redoStack.length){{undoStack.push(snapshot());restore(redoStack.pop())}}}};
$('export').onclick=()=>{{const missing=packet.queue.filter(i=>!valid(decisions[itemKey(i)],i));if(missing.length){{$('status').className='bad';$('status').textContent='Complete every review item before export.';return}}const source={{sidecar_sha256:packet.source.sidecar_sha256,video_sha256:packet.source.video_sha256,fusion_sha256:packet.source.fusion_sha256,fps:packet.source.fps,sampling_interval:packet.source.sampling_interval,targets:packet.source.targets}};const out={{schema:'{DECISION_SCHEMA}',version:{DECISION_VERSION},review_id:packet.review_id,created_at_utc:new Date().toISOString(),source,target_ids:packet.target_ids,frame_range:packet.frame_range,decisions:packet.queue.map(i=>decisions[itemKey(i)]),statement:{{appearance_is_non_authoritative:true,temporary_candidate_indexes_are_per_frame_only:true}}}};const blob=new Blob([JSON.stringify(out,null,2)+'\\n'],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=packet.review_id+'-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);$('status').className='good';$('status').textContent='Exported.'}};
try{{decisions=JSON.parse(localStorage.getItem(key)||'{{}}')}}catch(_){{decisions={{}}}}render();</script></body></html>"""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_output_directory(output: Path, inputs: list[Path], workspace: Path | None = None) -> Path:
    workspace = (workspace or Path.cwd()).resolve(strict=True)
    allowed = (workspace / "checkpoints" / "dev-preview").resolve(strict=False)
    resolved = output.resolve(strict=False)
    if not _is_relative_to(resolved, allowed) or resolved == allowed:
        raise ReviewError("Output directory must be beneath checkpoints/dev-preview")
    if resolved.exists():
        raise ReviewError(f"Output directory already exists: {output}")
    for source in inputs:
        if resolved == source.resolve(strict=False):
            raise ReviewError("Output directory collides with an input")
    return resolved


def publish_packet(output: Path, model: dict, video: Path, sidecar: dict) -> dict:
    destination = validate_output_directory(output, [video])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        image_hashes = render_review_images(video, sidecar, model, temporary)
        manifest = copy.deepcopy(model)
        manifest["image_sha256"] = image_hashes
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        html_path = temporary / "index.html"
        html_path.write_text(_html_document(model), encoding="utf-8", newline="\n")
        destination = validate_output_directory(output, [video])
        try:
            os.rename(temporary, destination)
        except OSError as exc:
            raise ReviewError(f"Cannot publish review packet without replacement: {exc}") from exc
        return {
            "manifest_sha256": sha256_file(destination / "manifest.json"),
            "html_sha256": sha256_file(destination / "index.html"),
            "image_count": len(image_hashes),
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--anchors", action="append", default=[], metavar="TARGET_ID=PATH")
    parser.add_argument("--track", action="append", default=[], metavar="TARGET_ID=PATH")
    parser.add_argument("--fusion", type=Path)
    parser.add_argument("--frame-range")
    parser.add_argument("--include-state", action="append", choices=sorted(ALLOWED_STATES))
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        anchor_paths = parse_target_paths(args.anchors, "--anchors")
        track_paths = parse_target_paths(args.track, "--track")
        if not args.video.is_file():
            raise ReviewError(f"Source video does not exist: {args.video}")
        inputs = [args.video, args.sidecar, *anchor_paths.values(), *track_paths.values()]
        if args.fusion:
            inputs.append(args.fusion)
        validate_output_directory(args.output_dir, inputs)
        context = validate_inputs(args.sidecar, anchor_paths, track_paths, args.fusion)
        frame_range = parse_frame_range(args.frame_range, context["sidecar"])
        video_hash = sha256_file(args.video)
        model = build_review_model(
            context,
            video_hash,
            frame_range,
            set(args.include_state or ["ambiguous"]),
        )
        result = publish_packet(args.output_dir, model, args.video, context["sidecar"])
    except ReviewError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved target review packet: {args.output_dir}")
    print(f"Review items: {len(model['queue'])}; images: {result['image_count']}")
    print("Appearance suggestions are non-authoritative and never preselected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
