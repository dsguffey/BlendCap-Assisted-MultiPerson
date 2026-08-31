"""Render sampled multi-person YOLO diagnostics from a v2 preview sidecar."""

import argparse
import json
import math
import sys
from pathlib import Path

import cv2


BOX_TOLERANCE = 1e-4
AMBER = (0, 165, 255)
GREEN = (0, 255, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


class DiagnosticError(RuntimeError):
    """A clear, user-facing validation or rendering failure."""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Render only the source frames sampled in a version-2 "
            "preview_boxes.json sidecar. Candidate indexes are temporary "
            "per-frame positions, not actor IDs."
        )
    )
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _finite_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DiagnosticError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise DiagnosticError(f"{label} must be finite")
    return number


def _box(value, label, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise DiagnosticError(f"{label} must be a four-value box")
    if not isinstance(value, list) or len(value) != 4:
        raise DiagnosticError(f"{label} must be a four-value box")
    box = tuple(_finite_float(v, f"{label}[{i}]") for i, v in enumerate(value))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise DiagnosticError(f"{label} has non-positive width or height")
    return box


def _boxes_match(a, b):
    return all(abs(av - bv) <= BOX_TOLERANCE for av, bv in zip(a, b))


def load_sidecar(path):
    if not path.is_file():
        raise DiagnosticError(f"Sidecar not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"Cannot read sidecar {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise DiagnosticError("Sidecar root must be a JSON object")
    if raw.get("version") != 2:
        raise DiagnosticError(
            f"Unsupported sidecar version {raw.get('version')!r}; expected 2"
        )

    fps = _finite_float(raw.get("fps"), "fps")
    if fps <= 0:
        raise DiagnosticError("fps must be positive")

    sample_indices = raw.get("sample_indices")
    selected_raw = raw.get("boxes")
    candidates_raw = raw.get("all_boxes")
    for value, label in (
        (sample_indices, "sample_indices"),
        (selected_raw, "boxes"),
        (candidates_raw, "all_boxes"),
    ):
        if not isinstance(value, list):
            raise DiagnosticError(f"{label} must be a list")

    count = len(sample_indices)
    if count == 0:
        raise DiagnosticError("Sidecar contains no sampled frames")
    if len(selected_raw) != count or len(candidates_raw) != count:
        raise DiagnosticError(
            "sample_indices, boxes, and all_boxes must have matching lengths"
        )
    if count < 2:
        raise DiagnosticError(
            "At least two sampled frames are required to infer the sampling interval"
        )

    indices = []
    for i, value in enumerate(sample_indices):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DiagnosticError(
                f"sample_indices[{i}] must be a non-negative integer"
            )
        if indices and value <= indices[-1]:
            raise DiagnosticError("sample_indices must be strictly increasing")
        indices.append(value)

    interval = indices[1] - indices[0]
    if interval <= 0 or any(
        indices[i] - indices[i - 1] != interval for i in range(2, count)
    ):
        raise DiagnosticError(
            "sample_indices must use one constant positive sampling interval"
        )

    selected = []
    all_boxes = []
    warnings = []
    for i in range(count):
        selected_box = _box(selected_raw[i], f"boxes[{i}]", allow_none=True)
        frame_candidates = candidates_raw[i]
        if not isinstance(frame_candidates, list):
            raise DiagnosticError(f"all_boxes[{i}] must be a list")
        parsed_candidates = [
            _box(candidate, f"all_boxes[{i}][{j}]")
            for j, candidate in enumerate(frame_candidates)
        ]

        if selected_box is not None:
            matches = [
                j
                for j, candidate in enumerate(parsed_candidates)
                if _boxes_match(selected_box, candidate)
            ]
            if not matches:
                warnings.append(
                    f"frame {indices[i]}: selected box is absent from all_boxes"
                )
            elif len(matches) > 1:
                warnings.append(
                    f"frame {indices[i]}: selected box matches duplicate "
                    f"candidates {matches}; highlighting the first"
                )

        selected.append(selected_box)
        all_boxes.append(parsed_candidates)

    return {
        "fps": fps,
        "sample_indices": indices,
        "interval": interval,
        "selected": selected,
        "all_boxes": all_boxes,
        "warnings": warnings,
    }


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_paths(video, sidecar, output):
    if not video.is_file():
        raise DiagnosticError(f"Video not found: {video}")
    video_resolved = _resolved(video)
    sidecar_resolved = _resolved(sidecar)
    output_resolved = _resolved(output)
    if output_resolved in (video_resolved, sidecar_resolved):
        raise DiagnosticError("Output must not overwrite the video or sidecar")
    if output.suffix.lower() != ".mp4":
        raise DiagnosticError("Output filename must end in .mp4")
    if output.exists():
        raise DiagnosticError(f"Output already exists; choose a new path: {output}")


def _draw_box(frame, box, color, thickness, label):
    height, width = frame.shape[:2]
    x1 = max(0, min(width - 1, int(round(box[0]))))
    y1 = max(0, min(height - 1, int(round(box[1]))))
    x2 = max(0, min(width - 1, int(round(box[2]))))
    y2 = max(0, min(height - 1, int(round(box[3]))))
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.65
    text_thickness = 2
    (text_width, text_height), baseline = cv2.getTextSize(
        label, font, scale, text_thickness
    )
    text_x = max(0, min(width - text_width - 6, x1))
    text_y = y1 - 8
    if text_y - text_height - baseline < 0:
        text_y = min(height - baseline - 2, y1 + text_height + baseline + 8)
    cv2.rectangle(
        frame,
        (text_x, text_y - text_height - baseline - 3),
        (text_x + text_width + 6, text_y + baseline + 2),
        BLACK,
        -1,
    )
    cv2.putText(
        frame,
        label,
        (text_x + 3, text_y),
        font,
        scale,
        color,
        text_thickness,
        cv2.LINE_AA,
    )


def _timestamp(seconds):
    total_ms = int(round(seconds * 1000.0))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _draw_metadata(frame, frame_index, fps, person_count, selected_label):
    lines = [
        f"Source frame: {frame_index}",
        f"Timestamp: {_timestamp(frame_index / fps)}",
        f"YOLO person boxes: {person_count}",
        f"Selected: {selected_label}",
        "TEMP indexes are per-frame positions - NOT actor IDs",
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.68
    thickness = 2
    line_height = 28
    max_width = max(
        cv2.getTextSize(line, font, scale, thickness)[0][0] for line in lines
    )
    panel_height = 14 + line_height * len(lines)
    cv2.rectangle(frame, (8, 8), (28 + max_width, 8 + panel_height), BLACK, -1)
    for i, line in enumerate(lines):
        color = AMBER if i == len(lines) - 1 else WHITE
        cv2.putText(
            frame,
            line,
            (18, 34 + i * line_height),
            font,
            scale,
            color,
            thickness,
            cv2.LINE_AA,
        )


def annotate_frame(frame, frame_index, fps, selected, candidates):
    matches = []
    if selected is not None:
        matches = [
            i
            for i, candidate in enumerate(candidates)
            if _boxes_match(selected, candidate)
        ]
    selected_index = matches[0] if matches else None

    for i, candidate in enumerate(candidates):
        if i != selected_index:
            _draw_box(frame, candidate, AMBER, 2, f"TEMP {i}")

    if selected is None:
        selected_label = "NONE"
    elif selected_index is None:
        selected_label = "NOT FOUND IN all_boxes"
        _draw_box(frame, selected, GREEN, 4, "SELECTED - NOT IN all_boxes")
    else:
        selected_label = f"TEMP {selected_index}"
        _draw_box(
            frame,
            candidates[selected_index],
            GREEN,
            4,
            f"SELECTED - TEMP {selected_index}",
        )

    _draw_metadata(
        frame, frame_index, fps, len(candidates), selected_label
    )


def render(video_path, output_path, sidecar, progress):
    capture = None
    writer = None
    try:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise DiagnosticError(f"Cannot open source video: {video_path}")

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if width <= 0 or height <= 0:
            raise DiagnosticError("Source video reports invalid dimensions")
        if frame_count > 0 and sidecar["sample_indices"][-1] >= frame_count:
            raise DiagnosticError(
                f"Sample frame {sidecar['sample_indices'][-1]} is outside "
                f"the source frame count {frame_count}"
            )
        if video_fps > 0:
            tolerance = max(0.01, sidecar["fps"] * 0.001)
            if abs(video_fps - sidecar["fps"]) > tolerance:
                raise DiagnosticError(
                    f"Video FPS {video_fps:.9g} does not match sidecar FPS "
                    f"{sidecar['fps']:.9g}"
                )

        output_width = width + width % 2
        output_height = height + height % 2
        output_fps = sidecar["fps"] / sidecar["interval"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(output_path), fourcc, output_fps, (output_width, output_height)
        )
        progress["output_created"] = output_path.exists()
        if not writer.isOpened():
            raise DiagnosticError(f"Cannot create MP4 output: {output_path}")
        progress["output_created"] = True

        sample_position = 0
        next_sample = sidecar["sample_indices"][sample_position]
        source_index = 0
        while sample_position < len(sidecar["sample_indices"]):
            ok, frame = capture.read()
            if not ok:
                raise DiagnosticError(
                    f"Video decoding stopped at source frame {source_index}; "
                    f"next required sample is {next_sample}"
                )

            if source_index == next_sample:
                if output_width != width or output_height != height:
                    frame = cv2.copyMakeBorder(
                        frame,
                        0,
                        output_height - height,
                        0,
                        output_width - width,
                        cv2.BORDER_CONSTANT,
                        value=BLACK,
                    )
                annotate_frame(
                    frame,
                    source_index,
                    sidecar["fps"],
                    sidecar["selected"][sample_position],
                    sidecar["all_boxes"][sample_position],
                )
                writer.write(frame)
                progress["rendered"] += 1
                sample_position += 1
                if sample_position == len(sidecar["sample_indices"]):
                    break
                next_sample = sidecar["sample_indices"][sample_position]

            source_index += 1

        return output_fps, output_width, output_height
    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()


def main():
    args = parse_args()
    progress = {"rendered": 0, "output_created": False}
    try:
        sidecar = load_sidecar(args.sidecar)
        validate_paths(args.video, args.sidecar, args.output)
        for warning in sidecar["warnings"]:
            print(f"WARNING: {warning}")
        output_fps, width, height = render(
            args.video, args.output, sidecar, progress
        )
    except (DiagnosticError, OSError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if progress["output_created"]:
            print(
                f"Output may be incomplete: {args.output} "
                f"({progress['rendered']} sampled frames written)",
                file=sys.stderr,
            )
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected rendering failure: {exc}", file=sys.stderr)
        if progress["output_created"]:
            print(
                f"Output may be incomplete: {args.output} "
                f"({progress['rendered']} sampled frames written)",
                file=sys.stderr,
            )
        return 1
    except KeyboardInterrupt:
        print("ERROR: Rendering interrupted", file=sys.stderr)
        if progress["output_created"]:
            print(
                f"Output may be incomplete: {args.output} "
                f"({progress['rendered']} sampled frames written)",
                file=sys.stderr,
            )
        return 130

    print(f"Rendered {progress['rendered']} sampled frames")
    print(f"Source FPS: {sidecar['fps']:.9g}")
    print(f"Sampling interval: {sidecar['interval']} frames")
    print(f"Output FPS: {output_fps:.9g}")
    print(f"Output dimensions: {width}x{height}")
    print(f"Saved diagnostic video: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
