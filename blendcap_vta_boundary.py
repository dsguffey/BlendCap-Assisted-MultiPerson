"""Translate durable BlendCap review evidence into a neutral VTA payload.

This module is intentionally BlendCap-specific.  It validates the existing
preview sidecar and anchor formats, converts their OpenCV/YOLO upper-left box
coordinates at the public/private boundary, and emits JSON-compatible data.
It does not import the private core and cannot assign semantic identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from track_preview_target import TrackError, validate_anchors, validate_sidecar


BOUNDARY_SCHEMA = "vta-neutral-reviewed-observation"
BOUNDARY_VERSION = 1
EXTERNAL_COORDINATE_CONTRACT = {
    "box_format": "xyxy",
    "units": "pixels",
    "origin": "upper-left",
}
CANONICAL_COORDINATE_CONTRACT = {
    "box_format": "xyxy",
    "units": "pixels",
    "origin": "lower-left",
}


class BoundaryTranslationError(RuntimeError):
    """BlendCap evidence cannot be translated without ambiguity."""


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BoundaryTranslationError(f"{label} must be a non-empty string")
    return value


def _sha256(value: object, label: str) -> str:
    text = _nonempty_string(value, label).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise BoundaryTranslationError(f"{label} must be a 64-character SHA-256")
    return text


def _dimension(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BoundaryTranslationError(f"{label} must be a positive integer")
    return value


def upper_left_to_lower_left_xyxy(box: object, source_height: object) -> list[float]:
    """Convert pixel xyxy from an upper-left origin to a lower-left origin."""
    height = _dimension(source_height, "source height")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise BoundaryTranslationError("box must contain four coordinates")
    try:
        x1, y1, x2, y2 = (float(value) for value in box)
    except (TypeError, ValueError) as exc:
        raise BoundaryTranslationError("box coordinates must be numeric") from exc
    if x2 <= x1 or y2 <= y1:
        raise BoundaryTranslationError("box must have positive width and height")
    return [x1, float(height) - y2, x2, float(height) - y1]


def translate_reviewed_anchor(
    sidecar_document: dict[str, Any],
    anchor_document: dict[str, Any],
    *,
    sidecar_sha256: str,
    anchor_document_sha256: str,
    anchor_id: str,
    source_id: str,
    source_width: int | None,
    source_height: int | None,
    source_dimensions_verified: bool,
    source_video_sha256: str | None,
    analysis_revision_id: str,
    provider: str,
    model: str,
    configuration_id: str,
) -> dict[str, Any]:
    """Build one neutral reviewed-observation payload.

    ``source_width`` and ``source_height`` must come from source-video metadata
    obtained by the BlendCap capture path.  The current version-2 preview
    sidecar does not persist them, so omission or unverified values are fatal.
    """
    if source_dimensions_verified is not True:
        raise BoundaryTranslationError(
            "source dimensions must be verified from source-video metadata"
        )
    width = _dimension(source_width, "source width")
    height = _dimension(source_height, "source height")
    sidecar_digest = _sha256(sidecar_sha256, "sidecar_sha256")
    anchor_digest = _sha256(anchor_document_sha256, "anchor_document_sha256")
    video_digest = (
        _sha256(source_video_sha256, "source_video_sha256")
        if source_video_sha256 is not None
        else None
    )

    try:
        sidecar = validate_sidecar(sidecar_document, digest=sidecar_digest)
        anchors = validate_anchors(anchor_document, sidecar)
    except TrackError as exc:
        raise BoundaryTranslationError(f"invalid BlendCap evidence: {exc}") from exc

    requested_anchor_id = _nonempty_string(anchor_id, "anchor_id")
    matches = [
        item for item in anchors["anchors"] if item["anchor_id"] == requested_anchor_id
    ]
    if len(matches) != 1:
        raise BoundaryTranslationError(
            f"anchor_id {requested_anchor_id!r} must resolve exactly once"
        )
    anchor = matches[0]
    canonical_box = upper_left_to_lower_left_xyxy(anchor["box_xyxy"], height)
    frame = anchor["frame"]

    source_identifier = _nonempty_string(source_id, "source_id")
    revision_identifier = _nonempty_string(
        analysis_revision_id, "analysis_revision_id"
    )
    provider_name = _nonempty_string(provider, "provider")
    model_name = _nonempty_string(model, "model")
    configuration = _nonempty_string(configuration_id, "configuration_id")
    observation_material = {
        "sidecar_sha256": sidecar_digest,
        "anchor_document_sha256": anchor_digest,
        "anchor_id": requested_anchor_id,
        "frame": frame,
        "box_xyxy": canonical_box,
    }
    observation_digest = hashlib.sha256(
        json.dumps(observation_material, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()

    return {
        "schema": BOUNDARY_SCHEMA,
        "version": BOUNDARY_VERSION,
        "source": {
            "source_id": source_identifier,
            "fps": sidecar["fps"],
            "start_frame": sidecar["sample_indices"][0],
            "end_frame": sidecar["sample_indices"][-1],
            "width": width,
            "height": height,
            "video_sha256": video_digest,
            "coordinate_contract": dict(CANONICAL_COORDINATE_CONTRACT),
        },
        "analysis": {
            "revision_id": revision_identifier,
            "provider": provider_name,
            "model": model_name,
            "configuration_id": configuration,
        },
        "observation": {
            "observation_id": f"blendcap-observation-{observation_digest}",
            "frame": frame,
            "timestamp_seconds": frame / sidecar["fps"],
            "box_xyxy": canonical_box,
            "coordinate_contract": dict(CANONICAL_COORDINATE_CONTRACT),
        },
        "review_provenance": {
            "kind": "user-reviewed-candidate",
            "external_anchor_id": requested_anchor_id,
            "sidecar_sha256": sidecar_digest,
            "anchor_document_sha256": anchor_digest,
            "external_coordinate_contract": dict(EXTERNAL_COORDINATE_CONTRACT),
        },
    }
