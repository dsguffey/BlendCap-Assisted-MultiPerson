"""Render read-only visual review sheets for preview target anchors."""

import argparse
import os
import re
import sys
from pathlib import Path

import track_preview_target as tracker


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = (SCRIPT_DIR / "checkpoints" / "dev-preview").resolve()

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
CYAN = (255, 255, 0)
MAGENTA = (255, 0, 255)
AMBER = (0, 191, 255)

TEMP_WARNING = "TEMP indexes are per-frame positions - NOT identities"
IDENTITY_WARNING = "Visual review only - no identity is inferred"


class ReviewError(RuntimeError):
    """A clear, user-facing validation or rendering failure."""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Render three-panel PNGs for human review of durable preview "
            "anchors. TEMP indexes are per-frame positions, not identities."
        )
    )
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--anchors", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def display_track_label(track_id):
    """Remove a sampling-step suffix while preserving the durable track id."""
    return re.sub(r"-step\d+$", "", track_id)


def safe_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "anchor"


def load_and_validate(sidecar_path, anchors_path):
    sidecar_raw = tracker._load_json(sidecar_path, "Sidecar")
    anchor_raw = tracker._load_json(anchors_path, "Anchor file")
    require_anchor_source_metadata(anchor_raw)
    sidecar = tracker.validate_sidecar(
        sidecar_raw, tracker._sha256(sidecar_path)
    )
    anchors = tracker.validate_anchors(anchor_raw, sidecar)
    require_anchor_snapshots(anchors)
    return sidecar, anchors


def require_anchor_source_metadata(anchor_document):
    source = anchor_document.get("source")
    if not isinstance(source, dict):
        raise ReviewError("Anchor file must contain source metadata")
    for field in ("sidecar_sha256", "fps", "sampling_interval"):
        if field not in source:
            raise ReviewError(f"Anchor source metadata is missing {field}")


def require_anchor_snapshots(anchor_document):
    for anchor in anchor_document["anchors"]:
        if "box_snapshot_xyxy" not in anchor:
            raise ReviewError(
                f"Anchor {anchor['anchor_id']} has no box_snapshot_xyxy; "
                "a durable snapshot is required for visual review"
            )


def build_anchor_review_specs(sidecar, anchor_document):
    indices = sidecar["sample_indices"]
    specs = []
    for anchor in anchor_document["anchors"]:
        position = sidecar["frame_to_position"][anchor["frame"]]
        panels = []
        for role, panel_position in (
            ("previous", position - 1),
            ("anchor", position),
            ("next", position + 1),
        ):
            if panel_position < 0 or panel_position >= len(indices):
                panels.append(
                    {
                        "role": role,
                        "frame": None,
                        "candidates": [],
                        "missing": True,
                    }
                )
                continue
            frame = indices[panel_position]
            panel = {
                "role": role,
                "frame": frame,
                "candidates": sidecar["all_boxes"][panel_position],
                "missing": False,
            }
            if role == "anchor":
                panel.update(
                    {
                        "anchor_id": anchor["anchor_id"],
                        "anchor_snapshot_xyxy": anchor[
                            "box_snapshot_xyxy"
                        ],
                        "anchor_temporary_candidate_index": anchor[
                            "temporary_candidate_index"
                        ],
                    }
                )
            panels.append(panel)

        specs.append(
            {
                "anchor_id": anchor["anchor_id"],
                "anchor_frame": anchor["frame"],
                "display_label": display_track_label(
                    anchor_document["track_id"]
                ),
                "filename": (
                    f"anchor_frame_{anchor['frame']:06d}_"
                    f"{safe_filename(anchor['anchor_id'])}.png"
                ),
                "panels": panels,
            }
        )
    return specs


def build_forced_review_specs(sidecar, anchor_document):
    specs = []
    positions = sidecar["frame_to_position"]
    indices = sidecar["sample_indices"]
    for range_index, item in enumerate(
        anchor_document["forced_occlusion_ranges"]
    ):
        start_position = positions[item["start_frame"]]
        end_position = positions[item["end_frame"]]
        midpoint_position = (start_position + end_position) // 2
        panels = []
        for role, position in (
            ("start", start_position),
            ("midpoint", midpoint_position),
            ("end", end_position),
        ):
            panels.append(
                {
                    "role": role,
                    "frame": indices[position],
                    "candidates": sidecar["all_boxes"][position],
                    "missing": False,
                }
            )
        spec = {
            "range_index": range_index,
            "start_frame": item["start_frame"],
            "end_frame": item["end_frame"],
            "panels": panels,
        }
        if "note" in item:
            spec["note"] = item["note"]
        specs.append(spec)
    return specs


def required_frames(anchor_specs, forced_specs):
    return sorted(
        {
            panel["frame"]
            for spec in anchor_specs
            for panel in spec["panels"]
            if panel["frame"] is not None
        }
        | {
            panel["frame"]
            for spec in forced_specs
            for panel in spec["panels"]
        }
    )


def planned_output_paths(output_dir, anchor_specs):
    return [output_dir / spec["filename"] for spec in anchor_specs] + [
        output_dir / "forced_occlusion_ranges.png"
    ]


def _resolved(path):
    return path.expanduser().resolve(strict=False)


def validate_paths(video, sidecar, anchors, output_dir, output_paths):
    for path, label in (
        (video, "Video"),
        (sidecar, "Sidecar"),
        (anchors, "Anchor file"),
    ):
        if not path.is_file():
            raise ReviewError(f"{label} not found: {path}")

    resolved_output = _resolved(output_dir)
    if OUTPUT_ROOT not in resolved_output.parents:
        raise ReviewError(
            "Output directory must be beneath checkpoints/dev-preview: "
            f"{output_dir}"
        )
    if output_dir.exists() and not output_dir.is_dir():
        raise ReviewError(f"Output directory is not a directory: {output_dir}")

    inputs = {_resolved(video), _resolved(sidecar), _resolved(anchors)}
    for output in output_paths:
        resolved = _resolved(output)
        if resolved.parent != resolved_output:
            raise ReviewError(f"Generated PNG escapes output directory: {output}")
        if resolved in inputs:
            raise ReviewError(f"Generated PNG would overwrite an input: {output}")
        if output.exists():
            raise ReviewError(
                f"Output already exists; choose a new directory: {output}"
            )
        temporary = output.with_name(output.stem + ".tmp.png")
        if temporary.exists():
            raise ReviewError(
                f"Temporary output already exists; choose a new directory: "
                f"{temporary}"
            )


def _load_imaging_modules():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ReviewError(
            "OpenCV and NumPy are required to render anchor review PNGs"
        ) from exc
    return cv2, np


def decode_required_frames(video_path, sidecar, frames, cv2):
    capture = None
    try:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise ReviewError(f"Cannot open source video: {video_path}")

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if width <= 0 or height <= 0:
            raise ReviewError("Source video reports invalid dimensions")
        if (
            frame_count > 0
            and sidecar["sample_indices"][-1] >= frame_count
        ):
            raise ReviewError(
                f"Sample frame {sidecar['sample_indices'][-1]} is outside "
                f"the source frame count {frame_count}"
            )
        if video_fps > 0:
            tolerance = max(0.01, sidecar["fps"] * 0.001)
            if abs(video_fps - sidecar["fps"]) > tolerance:
                raise ReviewError(
                    f"Video FPS {video_fps:.9g} does not match sidecar FPS "
                    f"{sidecar['fps']:.9g}"
                )

        wanted = set(frames)
        decoded = {}
        last_required = max(frames)
        source_index = 0
        while source_index <= last_required:
            ok, frame = capture.read()
            if not ok:
                missing = min(wanted - decoded.keys())
                raise ReviewError(
                    f"Video decoding stopped at source frame {source_index}; "
                    f"required frame {missing} was not decoded"
                )
            if source_index in wanted:
                decoded[source_index] = frame.copy()
            source_index += 1
        return decoded, width, height
    finally:
        if capture is not None:
            capture.release()


def _timestamp(seconds):
    total_ms = int(round(seconds * 1000.0))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _draw_box(cv2, image, box, color, thickness, label):
    height, width = image.shape[:2]
    x1 = max(0, min(width - 1, int(round(box[0]))))
    y1 = max(0, min(height - 1, int(round(box[1]))))
    x2 = max(0, min(width - 1, int(round(box[2]))))
    y2 = max(0, min(height - 1, int(round(box[3]))))
    cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.45, min(0.8, width / 1600.0))
    text_thickness = max(1, thickness // 2)
    (text_width, text_height), baseline = cv2.getTextSize(
        label, font, scale, text_thickness
    )
    text_x = max(0, min(width - text_width - 8, x1))
    text_y = y1 - 8
    if text_y - text_height - baseline < 0:
        text_y = min(height - baseline - 3, y1 + text_height + baseline + 9)
    cv2.rectangle(
        image,
        (text_x, text_y - text_height - baseline - 4),
        (text_x + text_width + 8, text_y + baseline + 3),
        BLACK,
        -1,
    )
    cv2.putText(
        image,
        label,
        (text_x + 4, text_y),
        font,
        scale,
        color,
        text_thickness,
        cv2.LINE_AA,
    )


def _draw_lines(cv2, image, lines):
    font = cv2.FONT_HERSHEY_SIMPLEX
    width = image.shape[1]
    scale = max(0.48, min(0.72, width / 1700.0))
    thickness = 2
    line_height = max(23, int(round(31 * scale / 0.68)))
    max_width = max(
        cv2.getTextSize(text, font, scale, thickness)[0][0]
        for text, _color in lines
    )
    panel_height = 13 + line_height * len(lines)
    cv2.rectangle(
        image,
        (7, 7),
        (min(width - 1, 25 + max_width), min(image.shape[0] - 1, 7 + panel_height)),
        BLACK,
        -1,
    )
    for index, (text, color) in enumerate(lines):
        cv2.putText(
            image,
            text,
            (15, 31 + index * line_height),
            font,
            scale,
            color,
            thickness,
            cv2.LINE_AA,
        )


def _panel_role(role):
    return {
        "previous": "PREVIOUS SAMPLED FRAME",
        "anchor": "EXACT ANCHOR FRAME",
        "next": "NEXT SAMPLED FRAME",
        "start": "FORCED RANGE START",
        "midpoint": "FORCED RANGE MIDPOINT",
        "end": "FORCED RANGE END",
    }[role]


def render_panel(
    panel,
    decoded,
    fps,
    width,
    height,
    cv2,
    np,
    display_label=None,
    forced_range=None,
):
    if panel["missing"]:
        image = np.zeros((height, width, 3), dtype=np.uint8)
        lines = [
            (_panel_role(panel["role"]), WHITE),
            ("No sampled frame exists in this direction", AMBER),
            (TEMP_WARNING, AMBER),
            (IDENTITY_WARNING, AMBER),
        ]
        _draw_lines(cv2, image, lines)
        return image

    image = decoded[panel["frame"]].copy()
    selected_index = panel.get("anchor_temporary_candidate_index")
    for temporary_index, candidate in enumerate(panel["candidates"]):
        if selected_index is None or temporary_index != selected_index:
            _draw_box(
                cv2,
                image,
                candidate,
                CYAN,
                2,
                f"TEMP {temporary_index}",
            )

    if selected_index is not None:
        _draw_box(
            cv2,
            image,
            panel["anchor_snapshot_xyxy"],
            MAGENTA,
            5,
            f"ANCHOR: {display_label}",
        )

    lines = [
        (_panel_role(panel["role"]), WHITE),
        (f"Source frame: {panel['frame']}", WHITE),
        (f"Timestamp: {_timestamp(panel['frame'] / fps)}", WHITE),
        (f"YOLO person boxes: {len(panel['candidates'])}", WHITE),
    ]
    if selected_index is not None:
        lines.extend(
            [
                (f"ANCHOR: {display_label}", MAGENTA),
                (f"Resolved here as TEMP {selected_index} only", MAGENTA),
            ]
        )
    elif forced_range is not None:
        lines.append(
            (
                "Forced occlusion: "
                f"{forced_range['start_frame']}-{forced_range['end_frame']}",
                MAGENTA,
            )
        )
        if forced_range.get("note"):
            lines.append((f"Note: {forced_range['note']}", MAGENTA))
    else:
        lines.append(("Context only - no anchor on this frame", CYAN))
    lines.extend([(TEMP_WARNING, AMBER), (IDENTITY_WARNING, AMBER)])
    _draw_lines(cv2, image, lines)
    return image


def _fit_panel(cv2, image, maximum_width=720):
    height, width = image.shape[:2]
    if width <= maximum_width:
        return image
    scale = maximum_width / float(width)
    return cv2.resize(
        image,
        (maximum_width, max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def render_anchor_sheet(spec, decoded, fps, width, height, cv2, np):
    panels = [
        _fit_panel(
            cv2,
            render_panel(
                panel,
                decoded,
                fps,
                width,
                height,
                cv2,
                np,
                display_label=spec["display_label"],
            ),
        )
        for panel in spec["panels"]
    ]
    return cv2.hconcat(panels)


def render_forced_sheet(specs, decoded, fps, width, height, cv2, np):
    if not specs:
        image = np.zeros((max(360, height // 2), min(1280, width), 3), dtype=np.uint8)
        _draw_lines(
            cv2,
            image,
            [
                ("FORCED OCCLUSION REVIEW", WHITE),
                ("No forced occlusion ranges are defined", AMBER),
                (TEMP_WARNING, AMBER),
                (IDENTITY_WARNING, AMBER),
            ],
        )
        return image

    rows = []
    for spec in specs:
        panels = [
            _fit_panel(
                cv2,
                render_panel(
                    panel,
                    decoded,
                    fps,
                    width,
                    height,
                    cv2,
                    np,
                    forced_range=spec,
                ),
            )
            for panel in spec["panels"]
        ]
        rows.append(cv2.hconcat(panels))
    return cv2.vconcat(rows)


def write_png(path, image, cv2):
    temporary = path.with_name(path.stem + ".tmp.png")
    try:
        if not cv2.imwrite(str(temporary), image):
            raise ReviewError(f"OpenCV could not write PNG: {temporary}")
        os.replace(temporary, path)
    except OSError as exc:
        raise ReviewError(f"Cannot write PNG {path}: {exc}") from exc


def render_reviews(video, output_dir, sidecar, anchors, anchor_specs, forced_specs):
    cv2, np = _load_imaging_modules()
    frames = required_frames(anchor_specs, forced_specs)
    decoded, width, height = decode_required_frames(
        video, sidecar, frames, cv2
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    try:
        for spec in anchor_specs:
            output = output_dir / spec["filename"]
            sheet = render_anchor_sheet(
                spec, decoded, sidecar["fps"], width, height, cv2, np
            )
            write_png(output, sheet, cv2)
            generated.append(output)

        forced_output = output_dir / "forced_occlusion_ranges.png"
        forced_sheet = render_forced_sheet(
            forced_specs,
            decoded,
            sidecar["fps"],
            width,
            height,
            cv2,
            np,
        )
        write_png(forced_output, forced_sheet, cv2)
        generated.append(forced_output)
        return generated
    except Exception as exc:
        raise ReviewError(
            f"Rendering failed after {len(generated)} completed PNG(s); "
            f"the output directory may be incomplete: {exc}"
        ) from exc


def main():
    args = parse_args()
    try:
        sidecar, anchors = load_and_validate(args.sidecar, args.anchors)
        anchor_specs = build_anchor_review_specs(sidecar, anchors)
        forced_specs = build_forced_review_specs(sidecar, anchors)
        outputs = planned_output_paths(args.output_dir, anchor_specs)
        validate_paths(
            args.video,
            args.sidecar,
            args.anchors,
            args.output_dir,
            outputs,
        )
        generated = render_reviews(
            args.video,
            args.output_dir,
            sidecar,
            anchors,
            anchor_specs,
            forced_specs,
        )
    except (ReviewError, tracker.TrackError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Saved {len(anchor_specs)} anchor review PNG(s)")
    print(f"Saved 1 forced-occlusion review sheet")
    print(f"Output directory: {args.output_dir}")
    print(TEMP_WARNING)
    print(IDENTITY_WARNING)
    print(f"Generated files: {len(generated)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
