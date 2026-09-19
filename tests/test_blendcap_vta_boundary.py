"""Focused tests for the public BlendCap-to-VTA boundary translation."""

import copy
import unittest

import blendcap_vta_boundary as boundary


SIDECAR_HASH = "1" * 64
ANCHOR_HASH = "2" * 64


def sidecar(candidates=None):
    candidates = candidates or [[10, 20, 110, 220], [300, 40, 420, 260]]
    return {
        "version": 2,
        "fps": 25.0,
        "start_frame": 10,
        "end_frame": 31,
        "sample_indices": [10, 20, 30],
        "boxes": [items[0] if items else None for items in [candidates, candidates, candidates]],
        "all_boxes": [copy.deepcopy(candidates) for _ in range(3)],
    }


def anchors(snapshot=None):
    snapshot = snapshot or [300, 40, 420, 260]
    return {
        "schema": "blendcap-preview-target-anchors",
        "version": 1,
        "track_id": "preview-target-not-a-character",
        "coordinate_space": "source-pixels-xyxy",
        "source": {
            "sidecar_sha256": SIDECAR_HASH,
            "fps": 25.0,
            "sampling_interval": 10,
        },
        "anchors": [
            {
                "anchor_id": "preview-anchor-20",
                "frame": 20,
                "box_snapshot_xyxy": snapshot,
            }
        ],
        "forced_occlusion_ranges": [],
    }


def translate(sidecar_document=None, anchor_document=None, **overrides):
    values = {
        "sidecar_sha256": SIDECAR_HASH,
        "anchor_document_sha256": ANCHOR_HASH,
        "anchor_id": "preview-anchor-20",
        "source_id": "source-1",
        "source_width": 640,
        "source_height": 480,
        "source_dimensions_verified": True,
        "source_video_sha256": "3" * 64,
        "analysis_revision_id": "analysis-1",
        "provider": "blendcap-yolo-preview",
        "model": "yolo-person",
        "configuration_id": f"sidecar-sha256:{SIDECAR_HASH}",
    }
    values.update(overrides)
    return boundary.translate_reviewed_anchor(
        sidecar_document or sidecar(), anchor_document or anchors(), **values
    )


class BlendCapBoundaryTests(unittest.TestCase):
    def test_verified_geometry_converts_and_provenance_survives(self):
        payload = translate()

        self.assertEqual(payload["observation"]["box_xyxy"], [300.0, 220.0, 420.0, 440.0])
        self.assertEqual(payload["source"]["width"], 640)
        self.assertEqual(payload["source"]["height"], 480)
        self.assertEqual(payload["review_provenance"]["sidecar_sha256"], SIDECAR_HASH)
        self.assertEqual(payload["review_provenance"]["anchor_document_sha256"], ANCHOR_HASH)
        self.assertEqual(payload["analysis"]["provider"], "blendcap-yolo-preview")
        self.assertEqual(payload["observation"]["coordinate_contract"]["origin"], "lower-left")

    def test_missing_or_unverified_dimensions_fail_safely(self):
        cases = (
            {"source_width": None},
            {"source_height": None},
            {"source_height": 0},
            {"source_dimensions_verified": False},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(
                boundary.BoundaryTranslationError, "source (width|height|dimensions)"
            ):
                translate(**overrides)

    def test_candidate_reordering_cannot_cross_as_identity(self):
        first = translate()
        reordered_candidates = [[300, 40, 420, 260], [10, 20, 110, 220]]
        second = translate(
            sidecar_document=sidecar(reordered_candidates),
            anchor_document=anchors([300, 40, 420, 260]),
        )

        self.assertEqual(first["observation"], second["observation"])
        serialized = str(second)
        self.assertNotIn("temporary_candidate_index", serialized)
        self.assertNotIn("track_id", serialized)
        self.assertNotIn("character_id", serialized)


if __name__ == "__main__":
    unittest.main()
