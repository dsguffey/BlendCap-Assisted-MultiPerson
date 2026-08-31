"""Measure anchored OSNet appearance similarity without assigning identity."""

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import track_preview_target as tracker


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = (SCRIPT_DIR / "checkpoints" / "dev-preview").resolve()
SCHEMA = "blendcap-preview-target-appearance-diagnostic"
VERSION = 1
DEFAULT_LABELS = "0:2,11:2,22:2,33:3,44:3,52:3,55:3,58:3,67:3"
VIEW_SPECS = {
    "whole_box": (0.0, 1.0, 0.0, 1.0),
    "inner_box": (0.08, 0.92, 0.05, 0.95),
}
SCORE_NAMES = (
    "similarity_to_anchor_33",
    "similarity_to_anchor_52",
    "endpoint_mean_score",
    "full_gallery_score",
)


class AppearanceError(RuntimeError):
    """A clear validation or diagnostic failure."""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Measure OSNet-AIN x1.0 similarities for preview candidates. "
            "TEMP indexes are per-frame diagnostic positions, not identities."
        )
    )
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--anchors", required=True, type=Path)
    parser.add_argument(
        "--track",
        type=Path,
        help=(
            "Optional geometry track used only for provenance and alignment "
            "validation"
        ),
    )
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--frame-range",
        default="34:51",
        help="Inclusive sampled-frame range (default: 34:51)",
    )
    parser.add_argument(
        "--reference-frames",
        default="33,52",
        help="Two durable anchor frames (default: 33,52)",
    )
    parser.add_argument(
        "--labels",
        default=DEFAULT_LABELS,
        help="Comma-separated human labels FRAME:TEMP",
    )
    parser.add_argument("--critical-frame", type=int, default=44)
    parser.add_argument("--critical-candidate", type=int, default=3)
    parser.add_argument("--critical-competitor", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    return parser.parse_args()


def _hash_file(path, label):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AppearanceError(f"Cannot hash {label.lower()} {path}: {exc}") from exc
    return digest.hexdigest()


def _load_json(path, label):
    try:
        return tracker._load_json(path, label)
    except tracker.TrackError as exc:
        raise AppearanceError(str(exc)) from exc


def require_anchor_source_metadata(raw):
    source = raw.get("source")
    if not isinstance(source, dict):
        raise AppearanceError("Anchor file must contain source metadata")
    for field in ("sidecar_sha256", "fps", "sampling_interval"):
        if field not in source:
            raise AppearanceError(f"Anchor source metadata is missing {field}")


def validate_track(raw, sidecar, anchors):
    if raw.get("schema") != "blendcap-preview-target-track":
        raise AppearanceError("Unsupported target-track schema")
    if raw.get("version") != 1:
        raise AppearanceError("Unsupported target-track schema version; expected 1")
    if raw.get("track_id") != anchors["track_id"]:
        raise AppearanceError("Track id does not match the anchor file")
    source = raw.get("source")
    if not isinstance(source, dict):
        raise AppearanceError("Target track must contain source metadata")
    expected = {
        "sidecar_sha256": sidecar["sha256"],
        "fps": sidecar["fps"],
        "sampling_interval": sidecar["sampling_interval"],
    }
    if source.get("sidecar_sha256", "").lower() != expected["sidecar_sha256"]:
        raise AppearanceError("Target-track sidecar hash does not match")
    try:
        track_fps = float(source.get("fps"))
    except (TypeError, ValueError) as exc:
        raise AppearanceError("Target-track FPS must be numeric") from exc
    tolerance = max(0.001, sidecar["fps"] * 1e-6)
    if not math.isfinite(track_fps) or abs(track_fps - sidecar["fps"]) > tolerance:
        raise AppearanceError("Target-track FPS does not match")
    if source.get("sampling_interval") != expected["sampling_interval"]:
        raise AppearanceError("Target-track sampling interval does not match")
    frames = raw.get("frames")
    if not isinstance(frames, list) or len(frames) != len(sidecar["sample_indices"]):
        raise AppearanceError("Target-track frames must align with the sidecar")
    actual_frames = []
    for index, item in enumerate(frames):
        if not isinstance(item, dict) or not isinstance(item.get("frame"), int):
            raise AppearanceError(f"Target-track frames[{index}] is invalid")
        actual_frames.append(item["frame"])
    if actual_frames != sidecar["sample_indices"]:
        raise AppearanceError("Target-track frame numbers do not align with sidecar")


def load_and_validate(sidecar_path, anchor_path, track_path):
    sidecar_raw = _load_json(sidecar_path, "Sidecar")
    anchor_raw = _load_json(anchor_path, "Anchor file")
    track_raw = (
        _load_json(track_path, "Target track")
        if track_path is not None
        else None
    )
    require_anchor_source_metadata(anchor_raw)
    try:
        sidecar = tracker.validate_sidecar(
            sidecar_raw, tracker._sha256(sidecar_path)
        )
        anchors = tracker.validate_anchors(anchor_raw, sidecar)
    except tracker.TrackError as exc:
        raise AppearanceError(str(exc)) from exc
    for anchor in anchors["anchors"]:
        if "box_snapshot_xyxy" not in anchor:
            raise AppearanceError(
                f"Anchor {anchor['anchor_id']} needs a durable box snapshot"
            )
    if track_raw is not None:
        validate_track(track_raw, sidecar, anchors)
    return sidecar, anchors


def parse_frame_range(value, sidecar):
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        start, end = (int(part) for part in parts)
    except ValueError as exc:
        raise AppearanceError("--frame-range must be START:END") from exc
    if start > end:
        raise AppearanceError("--frame-range is reversed")
    if start not in sidecar["frame_to_position"] or end not in sidecar["frame_to_position"]:
        raise AppearanceError("--frame-range endpoints must be sampled frames")
    frames = [frame for frame in sidecar["sample_indices"] if start <= frame <= end]
    if not frames:
        raise AppearanceError("--frame-range contains no sampled frames")
    return frames


def parse_reference_frames(value, anchors):
    try:
        frames = [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise AppearanceError("--reference-frames must contain two integers") from exc
    if len(frames) != 2 or frames[0] == frames[1]:
        raise AppearanceError("--reference-frames must contain two distinct frames")
    anchor_frames = {anchor["frame"] for anchor in anchors["anchors"]}
    if any(frame not in anchor_frames for frame in frames):
        raise AppearanceError("Each reference frame must have a durable anchor")
    if set(frames) != {33, 52}:
        raise AppearanceError(
            "This diagnostic requires reference anchor frames 33 and 52"
        )
    return (33, 52)


def parse_labels(value, sidecar):
    labels = {}
    if not value.strip():
        return labels
    for item in value.split(","):
        try:
            frame_text, index_text = item.split(":")
            frame, temporary_index = int(frame_text), int(index_text)
        except ValueError as exc:
            raise AppearanceError("--labels must use FRAME:TEMP entries") from exc
        if frame in labels:
            raise AppearanceError(f"Duplicate human label for frame {frame}")
        if frame not in sidecar["frame_to_position"]:
            raise AppearanceError(f"Human label frame {frame} is not sampled")
        candidates = sidecar["all_boxes"][sidecar["frame_to_position"][frame]]
        if temporary_index < 0 or temporary_index >= len(candidates):
            raise AppearanceError(
                f"Human label {frame}:{temporary_index} is outside its candidate list"
            )
        labels[frame] = temporary_index
    return labels


def validate_critical(frame, candidate, competitor, sidecar):
    if frame not in sidecar["frame_to_position"]:
        raise AppearanceError("Critical frame is not sampled")
    count = len(sidecar["all_boxes"][sidecar["frame_to_position"][frame]])
    for value, label in ((candidate, "candidate"), (competitor, "competitor")):
        if value < 0 or value >= count:
            raise AppearanceError(f"Critical {label} TEMP index is outside the list")
    if candidate == competitor:
        raise AppearanceError("Critical candidate and competitor must differ")


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_paths(video, sidecar, anchors, track, weights, output):
    required_inputs = [
        (video, "Video"),
        (sidecar, "Sidecar"),
        (anchors, "Anchor file"),
        (weights, "Weights"),
    ]
    if track is not None:
        required_inputs.append((track, "Target track"))
    for path, label in required_inputs:
        if not path.is_file():
            raise AppearanceError(f"{label} not found: {path}")
    if output.suffix.lower() != ".json":
        raise AppearanceError("Output filename must end in .json")
    resolved_output = _resolved(output)
    if OUTPUT_ROOT not in resolved_output.parents:
        raise AppearanceError(
            "Output must be beneath the ignored checkpoints/dev-preview directory"
        )
    inputs = {
        _resolved(path)
        for path in (video, sidecar, anchors, track, weights)
        if path is not None
    }
    if resolved_output in inputs:
        raise AppearanceError("Output must not overwrite any input")
    if output.exists():
        raise AppearanceError(f"Output already exists: {output}")
    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise AppearanceError(f"Temporary output already exists: {temporary}")


def decode_frames(video_path, sidecar, required, cv2):
    capture = None
    try:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise AppearanceError(f"Cannot open source video: {video_path}")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if width <= 0 or height <= 0:
            raise AppearanceError("Source video reports invalid dimensions")
        if frame_count > 0 and sidecar["sample_indices"][-1] >= frame_count:
            raise AppearanceError("The sidecar contains a frame outside the video")
        if video_fps > 0:
            tolerance = max(0.01, sidecar["fps"] * 0.001)
            if abs(video_fps - sidecar["fps"]) > tolerance:
                raise AppearanceError("Video FPS does not match the sidecar")
        wanted = set(required)
        decoded = {}
        final_frame = max(wanted)
        source_frame = 0
        while source_frame <= final_frame:
            ok, image = capture.read()
            if not ok:
                missing = min(wanted - decoded.keys())
                raise AppearanceError(
                    f"Video decoding stopped before required frame {missing}"
                )
            if source_frame in wanted:
                decoded[source_frame] = image.copy()
            source_frame += 1
        return decoded
    finally:
        if capture is not None:
            capture.release()


def view_box(box, view_name):
    if view_name not in VIEW_SPECS:
        raise AppearanceError(f"Unknown crop view {view_name}")
    left, right, top, bottom = VIEW_SPECS[view_name]
    width = box[2] - box[0]
    height = box[3] - box[1]
    return (
        box[0] + left * width,
        box[1] + top * height,
        box[0] + right * width,
        box[1] + bottom * height,
    )


def extract_rgb_crop(frame_bgr, box, view_name):
    crop_box = view_box(box, view_name)
    height, width = frame_bgr.shape[:2]
    x1 = max(0, min(width, int(math.floor(crop_box[0]))))
    y1 = max(0, min(height, int(math.floor(crop_box[1]))))
    x2 = max(0, min(width, int(math.ceil(crop_box[2]))))
    y2 = max(0, min(height, int(math.ceil(crop_box[3]))))
    if x2 <= x1 or y2 <= y1:
        raise AppearanceError(
            f"{view_name} crop has no pixels after clipping: {list(box)}"
        )
    return frame_bgr[y1:y2, x1:x2, ::-1].copy()


def _normalize(vector):
    values = tuple(float(value) for value in vector)
    norm = math.sqrt(sum(value * value for value in values))
    if not values or not math.isfinite(norm) or norm <= 0:
        raise AppearanceError("Descriptor must be finite and non-zero")
    result = tuple(value / norm for value in values)
    if not all(math.isfinite(value) for value in result):
        raise AppearanceError("Descriptor contains non-finite values")
    return result


def _cosine(left, right):
    if len(left) != len(right):
        raise AppearanceError("Descriptor dimensions do not match")
    return sum(a * b for a, b in zip(left, right))


def _maximum_other_iou(candidates, candidate_index):
    if len(candidates) < 2:
        return 0.0
    candidate = candidates[candidate_index]
    return max(
        tracker._iou(candidate, other)
        for index, other in enumerate(candidates)
        if index != candidate_index
    )


def _rank(candidates, score_name):
    scored = [
        (item["scores"][score_name], item["temporary_candidate_index"])
        for item in candidates
        if item["scores"][score_name] is not None
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    margin = scored[0][0] - scored[1][0] if len(scored) >= 2 else None
    return {
        "temporary_candidate_indexes": [item[1] for item in scored],
        "top_temporary_candidate_index": scored[0][1] if scored else None,
        "top_two_margin": margin,
    }


def _descriptor_batches(crops, embedder, batch_size):
    if not crops:
        return []
    output = []
    for start in range(0, len(crops), batch_size):
        batch = embedder.embed_batch(crops[start : start + batch_size])
        if len(batch) != len(crops[start : start + batch_size]):
            raise AppearanceError("Embedder returned the wrong batch length")
        output.extend(_normalize(vector) for vector in batch)
    return output


def analyze_view(
    view_name,
    decoded,
    sidecar,
    anchors,
    evaluation_frames,
    reference_frames,
    labels,
    embedder,
    batch_size,
):
    anchor_crops = [
        extract_rgb_crop(
            decoded[anchor["frame"]], anchor["box_snapshot_xyxy"], view_name
        )
        for anchor in anchors["anchors"]
    ]
    anchor_vectors = _descriptor_batches(anchor_crops, embedder, batch_size)
    gallery = {
        anchor["frame"]: vector
        for anchor, vector in zip(anchors["anchors"], anchor_vectors)
    }

    candidate_keys = []
    candidate_crops = []
    for frame in evaluation_frames:
        position = sidecar["frame_to_position"][frame]
        for temporary_index, box in enumerate(sidecar["all_boxes"][position]):
            candidate_keys.append((frame, temporary_index))
            candidate_crops.append(
                extract_rgb_crop(decoded[frame], box, view_name)
            )
    candidate_vectors = _descriptor_batches(candidate_crops, embedder, batch_size)
    vectors = dict(zip(candidate_keys, candidate_vectors))

    frame_results = []
    for frame in evaluation_frames:
        position = sidecar["frame_to_position"][frame]
        boxes = sidecar["all_boxes"][position]
        candidates = []
        for temporary_index, box in enumerate(boxes):
            vector = vectors[(frame, temporary_index)]
            endpoint_scores = []
            separate = []
            for reference_frame in reference_frames:
                score = (
                    None
                    if frame == reference_frame
                    else _cosine(vector, gallery[reference_frame])
                )
                separate.append(score)
                if score is not None:
                    endpoint_scores.append(score)
            gallery_scores = [
                _cosine(vector, reference)
                for anchor_frame, reference in gallery.items()
                if anchor_frame != frame
            ]
            candidates.append(
                {
                    "temporary_candidate_index": temporary_index,
                    "box_xyxy": list(box),
                    "opponent_contamination_proxy": {
                        "maximum_candidate_box_iou": _maximum_other_iou(
                            boxes, temporary_index
                        ),
                        "interpretation": (
                            "Box overlap is only a possible opponent-contamination "
                            "proxy, not proof of pixel contamination."
                        ),
                    },
                    "scores": {
                        "similarity_to_anchor_33": separate[0],
                        "similarity_to_anchor_52": separate[1],
                        "endpoint_mean_score": (
                            sum(endpoint_scores) / len(endpoint_scores)
                            if endpoint_scores
                            else None
                        ),
                        "full_gallery_score": (
                            sum(gallery_scores) / len(gallery_scores)
                            if gallery_scores
                            else None
                        ),
                    },
                }
            )
        rankings = {name: _rank(candidates, name) for name in SCORE_NAMES}
        anchor_tops = [
            rankings["similarity_to_anchor_33"]["top_temporary_candidate_index"],
            rankings["similarity_to_anchor_52"]["top_temporary_candidate_index"],
        ]
        record = {
            "frame": frame,
            "timestamp_seconds": frame / sidecar["fps"],
            "candidate_count": len(candidates),
            "candidates": candidates,
            "rankings": rankings,
            "anchor_33_and_52_top_agree": (
                anchor_tops[0] == anchor_tops[1]
                if None not in anchor_tops
                else None
            ),
        }
        if frame in labels:
            confirmed = labels[frame]
            record["human_confirmed_label"] = {
                "temporary_candidate_index": confirmed,
                "ranks": {
                    name: (
                        rankings[name]["temporary_candidate_indexes"].index(confirmed)
                        + 1
                        if confirmed in rankings[name]["temporary_candidate_indexes"]
                        else None
                    )
                    for name in SCORE_NAMES
                },
            }
        frame_results.append(record)
    return frame_results


def _label_measurements(frames, labels):
    measurements = {}
    for score_name in SCORE_NAMES:
        labeled = [frame for frame in frames if frame["frame"] in labels]
        evaluable = [
            frame
            for frame in labeled
            if frame["rankings"][score_name][
                "top_temporary_candidate_index"
            ]
            is not None
        ]
        correct = sum(
            frame["rankings"][score_name]["top_temporary_candidate_index"]
            == labels[frame["frame"]]
            for frame in evaluable
        )
        measurements[score_name] = {
            "top_rank_matches": correct,
            "evaluated_labels": len(evaluable),
            "self_reference_exclusions": len(labeled) - len(evaluable),
            "top_rank_fraction": correct / len(evaluable) if evaluable else None,
        }
    return measurements


def _critical_measurement(frames, frame_number, candidate, competitor):
    frame = next(item for item in frames if item["frame"] == frame_number)
    by_index = {
        item["temporary_candidate_index"]: item for item in frame["candidates"]
    }
    differences = {}
    for score_name in SCORE_NAMES:
        chosen_score = by_index[candidate]["scores"][score_name]
        competitor_score = by_index[competitor]["scores"][score_name]
        differences[score_name] = (
            chosen_score - competitor_score
            if chosen_score is not None and competitor_score is not None
            else None
        )
    return {
        "frame": frame_number,
        "human_confirmed_temporary_candidate_index": candidate,
        "competing_temporary_candidate_index": competitor,
        "confirmed_minus_competing_score": differences,
        "rankings": frame["rankings"],
        "anchor_33_and_52_top_agree": frame[
            "anchor_33_and_52_top_agree"
        ],
    }


def build_report(
    decoded,
    sidecar,
    anchors,
    evaluation_frames,
    reference_frames,
    labels,
    critical,
    embedder,
    batch_size,
    source_metadata,
):
    views = {}
    for view_name in VIEW_SPECS:
        frames = analyze_view(
            view_name,
            decoded,
            sidecar,
            anchors,
            evaluation_frames,
            reference_frames,
            labels,
            embedder,
            batch_size,
        )
        views[view_name] = {
            "frames": frames,
            "human_label_measurements": _label_measurements(frames, labels),
            "critical_frame_measurement": _critical_measurement(
                frames, critical[0], critical[1], critical[2]
            ),
        }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "diagnostic_scope": (
            "Appearance measurements only; TEMP positions are per-frame, and "
            "no final target track is produced."
        ),
        "source": source_metadata,
        "model": {
            "architecture": "osnet_ain_x1_0",
            "upstream_commit": (
                "f6ba78eceb38712266318e8c7eefde174af15606"
            ),
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
            "evaluation_frames": list(evaluation_frames),
            "human_confirmed_labels": [
                {"frame": frame, "temporary_candidate_index": labels[frame]}
                for frame in sorted(labels)
            ],
            "provisional_reference_points": {
                "top_rank_matches": "8/9",
                "critical_frame_margin": 0.05,
                "enforced_as_pass_fail": False,
            },
        },
        "views": views,
    }


class OSNetEmbedder:
    def __init__(self, weights, device):
        try:
            import torch
            from PIL import Image
            from torchvision import transforms

            from appearance_osnet import CheckpointError, load_osnet_ain_x1_0
        except ImportError as exc:
            raise AppearanceError(
                "Torch, torchvision, NumPy, Pillow, and OpenCV are required"
            ) from exc
        try:
            model = load_osnet_ain_x1_0(weights)
        except CheckpointError as exc:
            raise AppearanceError(str(exc)) from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise AppearanceError("CUDA was requested but is not available")
        self.torch = torch
        self.Image = Image
        self.device = torch.device(device)
        self.model = model.to(self.device).eval()
        self.transform = transforms.Compose(
            [
                transforms.Resize((256, 128)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ]
        )

    def embed_batch(self, rgb_crops):
        tensors = [
            self.transform(self.Image.fromarray(crop)) for crop in rgb_crops
        ]
        batch = self.torch.stack(tensors).to(self.device)
        with self.torch.no_grad():
            output = self.model(batch)
            output = self.torch.nn.functional.normalize(output, p=2, dim=1)
        return output.cpu().tolist()


def write_report(path, report):
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        raise AppearanceError(
            f"Cannot write report {path}; temporary output may remain: {exc}"
        ) from exc


def source_metadata(sidecar, anchors_path, track_path, weights_path):
    return {
        "sidecar_sha256": sidecar["sha256"],
        "anchors_sha256": _hash_file(anchors_path, "Anchor file"),
        "target_track_sha256": (
            _hash_file(track_path, "Target track")
            if track_path is not None
            else None
        ),
        "weights_sha256": _hash_file(weights_path, "Weights"),
        "fps": sidecar["fps"],
        "sampling_interval": sidecar["sampling_interval"],
    }


def main():
    args = parse_args()
    try:
        validate_paths(
            args.video,
            args.sidecar,
            args.anchors,
            args.track,
            args.weights,
            args.output,
        )
        sidecar, anchors = load_and_validate(
            args.sidecar, args.anchors, args.track
        )
        range_frames = parse_frame_range(args.frame_range, sidecar)
        reference_frames = parse_reference_frames(
            args.reference_frames, anchors
        )
        labels = parse_labels(args.labels, sidecar)
        validate_critical(
            args.critical_frame,
            args.critical_candidate,
            args.critical_competitor,
            sidecar,
        )
        evaluation_frames = sorted(
            set(range_frames) | set(labels) | {args.critical_frame}
        )
        required_frames = sorted(
            set(evaluation_frames)
            | {anchor["frame"] for anchor in anchors["anchors"]}
        )
        if args.batch_size <= 0:
            raise AppearanceError("--batch-size must be positive")
        try:
            import cv2
        except ImportError as exc:
            raise AppearanceError("OpenCV is required to read the video") from exc
        decoded = decode_frames(args.video, sidecar, required_frames, cv2)
        embedder = OSNetEmbedder(args.weights, args.device)
        report = build_report(
            decoded,
            sidecar,
            anchors,
            evaluation_frames,
            reference_frames,
            labels,
            (
                args.critical_frame,
                args.critical_candidate,
                args.critical_competitor,
            ),
            embedder,
            args.batch_size,
            source_metadata(
                sidecar, args.anchors, args.track, args.weights
            ),
        )
        write_report(args.output, report)
    except AppearanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Saved appearance diagnostic: {args.output}")
    print(f"Evaluated frames: {len(evaluation_frames)}")
    print("Views: whole_box, inner_box (reported separately)")
    print("TEMP indexes are per-frame diagnostic positions, not identities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
