"""Focused tests for the standalone appearance diagnostic."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import diagnose_preview_target_appearance as diagnostic

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency-free environments skip images
    np = None

try:
    import torch
    import appearance_osnet
except ImportError:  # pragma: no cover - dependency-free environments skip model tests
    torch = None
    appearance_osnet = None


class FakeEmbedder:
    """Deterministic descriptor from crop color; no model or checkpoint needed."""

    def embed_batch(self, rgb_crops):
        return [
            [
                float(crop[:, :, 0].mean()) + 1.0,
                float(crop[:, :, 1].mean()) + 1.0,
                float(crop[:, :, 2].mean()) + 1.0,
                7.0,
            ]
            for crop in rgb_crops
        ]


def synthetic_inputs(
    candidate_order=(0, 1),
    frames=(33, 34, 44, 52),
    reference_frames=(33, 52),
):
    frames = list(frames)
    red_box = (0.0, 0.0, 40.0, 80.0)
    black_box = (40.0, 0.0, 80.0, 80.0)
    base_boxes = [red_box, black_box]
    sidecar = {
        "fps": 30.0,
        "sample_indices": frames,
        "sampling_interval": 1,
        "all_boxes": [
            [base_boxes[index] for index in candidate_order] for _ in frames
        ],
        "frame_to_position": {frame: index for index, frame in enumerate(frames)},
        "sha256": "sidecar-digest",
    }
    anchors = {
        "track_id": "red-shorts-step1",
        "anchors": [
            {
                "anchor_id": f"red-{reference_frames[0]:03d}",
                "frame": reference_frames[0],
                "box_snapshot_xyxy": list(red_box),
            },
            {
                "anchor_id": f"red-{reference_frames[1]:03d}",
                "frame": reference_frames[1],
                "box_snapshot_xyxy": list(red_box),
            },
        ],
    }
    decoded = {}
    for frame in frames:
        image = np.zeros((80, 80, 3), dtype=np.uint8)
        image[:, :40] = [0, 0, 230]  # BGR red
        image[:, 40:] = [8, 8, 8]
        decoded[frame] = image
    return decoded, sidecar, anchors


def report_keys(value):
    keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(report_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(report_keys(child))
    return keys


@unittest.skipIf(np is None, "NumPy is not installed")
class AppearanceReportTests(unittest.TestCase):
    def build_report(self, candidate_order=(0, 1), evaluation_frames=None):
        decoded, sidecar, anchors = synthetic_inputs(candidate_order)
        frames = evaluation_frames or [34, 44]
        red_index = candidate_order.index(0)
        black_index = candidate_order.index(1)
        return diagnostic.build_report(
            decoded=decoded,
            sidecar=sidecar,
            anchors=anchors,
            evaluation_frames=frames,
            reference_frames=(33, 52),
            labels={44: red_index} if 44 in frames else {},
            critical=(44, red_index, black_index),
            embedder=FakeEmbedder(),
            batch_size=2,
            source_metadata={
                "sidecar_sha256": "s",
                "anchors_sha256": "a",
                "target_track_sha256": "t",
                "weights_sha256": "w",
                "fps": 30.0,
                "sampling_interval": 1,
            },
        )

    def test_inner_box_uses_approved_relative_bounds(self):
        self.assertEqual(
            diagnostic.view_box((10.0, 20.0, 110.0, 220.0), "inner_box"),
            (18.0, 30.0, 102.0, 210.0),
        )
        self.assertEqual(
            diagnostic.view_box((10.0, 20.0, 110.0, 220.0), "whole_box"),
            (10.0, 20.0, 110.0, 220.0),
        )

    def test_fake_descriptor_ranks_red_above_black_in_both_views(self):
        report = self.build_report()
        for view_name in ("whole_box", "inner_box"):
            frame44 = next(
                item
                for item in report["views"][view_name]["frames"]
                if item["frame"] == 44
            )
            for score_name in diagnostic.SCORE_NAMES:
                self.assertEqual(
                    frame44["rankings"][score_name][
                        "top_temporary_candidate_index"
                    ],
                    0,
                )
                self.assertGreater(
                    frame44["rankings"][score_name]["top_two_margin"], 0
                )
            self.assertTrue(frame44["anchor_33_and_52_top_agree"])

    def test_candidate_reordering_changes_temp_positions_not_box_scores(self):
        first = self.build_report((0, 1))
        second = self.build_report((1, 0))
        for view_name in diagnostic.VIEW_SPECS:
            first_frame = first["views"][view_name]["frames"][1]
            second_frame = second["views"][view_name]["frames"][1]
            first_by_box = {
                tuple(item["box_xyxy"]): item["scores"]
                for item in first_frame["candidates"]
            }
            second_by_box = {
                tuple(item["box_xyxy"]): item["scores"]
                for item in second_frame["candidates"]
            }
            self.assertEqual(first_by_box, second_by_box)
            self.assertEqual(
                first_frame["rankings"]["full_gallery_score"][
                    "top_temporary_candidate_index"
                ],
                0,
            )
            self.assertEqual(
                second_frame["rankings"]["full_gallery_score"][
                    "top_temporary_candidate_index"
                ],
                1,
            )

    def test_exact_anchor_reference_is_excluded_on_its_own_frame(self):
        decoded, sidecar, anchors = synthetic_inputs()
        frames = diagnostic.analyze_view(
            "whole_box",
            decoded,
            sidecar,
            anchors,
            [33],
            (33, 52),
            {},
            FakeEmbedder(),
            4,
        )
        for candidate in frames[0]["candidates"]:
            self.assertIsNone(
                candidate["scores"]["similarity_to_anchor_33"]
            )
            self.assertIsNotNone(
                candidate["scores"]["similarity_to_anchor_52"]
            )
            self.assertEqual(
                candidate["scores"]["endpoint_mean_score"],
                candidate["scores"]["similarity_to_anchor_52"],
            )

    def test_gallery_uses_durable_snapshots_not_candidate_positions(self):
        decoded, sidecar, anchors = synthetic_inputs((1, 0))
        results = diagnostic.analyze_view(
            "whole_box",
            decoded,
            sidecar,
            anchors,
            [44],
            (33, 52),
            {},
            FakeEmbedder(),
            8,
        )
        self.assertEqual(
            results[0]["rankings"]["full_gallery_score"][
                "top_temporary_candidate_index"
            ],
            1,
        )

    def test_report_has_no_raw_descriptors_crops_assignments_or_states(self):
        report = self.build_report()
        forbidden = {
            "embedding",
            "embeddings",
            "raw_embedding",
            "crop",
            "crop_image",
            "identity",
            "identity_assignment",
            "state",
        }
        self.assertTrue(forbidden.isdisjoint(report_keys(report)))
        encoded = json.dumps(report, allow_nan=False)
        self.assertNotIn("data:image", encoded)

    def test_overlap_is_labeled_only_as_a_proxy(self):
        report = self.build_report()
        proxy = report["views"]["whole_box"]["frames"][0]["candidates"][0][
            "opponent_contamination_proxy"
        ]
        self.assertIn("possible", proxy["interpretation"])
        self.assertIn("not proof", proxy["interpretation"])

    def test_alternate_references_drive_dynamic_report_fields(self):
        decoded, sidecar, anchors = synthetic_inputs(
            frames=(0, 7, 14), reference_frames=(0, 14)
        )
        report = diagnostic.build_report(
            decoded=decoded,
            sidecar=sidecar,
            anchors=anchors,
            evaluation_frames=[7],
            reference_frames=(0, 14),
            labels={7: 0},
            critical=(7, 0, 1),
            embedder=FakeEmbedder(),
            batch_size=2,
            source_metadata={},
        )
        self.assertEqual(
            report["experiment"]["reference_anchor_frames"], [0, 14]
        )
        expected_scores = {
            "similarity_to_anchor_0",
            "similarity_to_anchor_14",
            "endpoint_mean_score",
            "full_gallery_score",
        }
        for view in report["views"].values():
            frame = view["frames"][0]
            self.assertEqual(set(frame["candidates"][0]["scores"]), expected_scores)
            self.assertEqual(set(frame["rankings"]), expected_scores)
            self.assertEqual(
                set(frame["human_confirmed_label"]["ranks"]), expected_scores
            )
            self.assertIn("anchor_0_and_14_top_agree", frame)
            self.assertIn(
                "anchor_0_and_14_top_agree",
                view["critical_frame_measurement"],
            )
            self.assertNotIn("anchor_33_and_52_top_agree", frame)
            self.assertNotIn("similarity_to_anchor_33", frame["rankings"])

    def test_default_references_preserve_legacy_report_field_names(self):
        report = self.build_report()
        frame = report["views"]["whole_box"]["frames"][0]
        self.assertEqual(tuple(frame["rankings"]), diagnostic.SCORE_NAMES)
        self.assertIn("anchor_33_and_52_top_agree", frame)


class AppearanceValidationTests(unittest.TestCase):
    def setUp(self):
        self.sidecar = {
            "sample_indices": [0, 7, 14, 33, 42, 52],
            "frame_to_position": {
                frame: index
                for index, frame in enumerate([0, 7, 14, 33, 42, 52])
            },
        }
        self.anchors = {
            "anchors": [
                {"frame": 0, "box_snapshot_xyxy": [0, 0, 1, 1]},
                {"frame": 14, "box_snapshot_xyxy": [0, 0, 1, 1]},
                {"frame": 33, "box_snapshot_xyxy": [0, 0, 1, 1]},
                {"frame": 42, "box_snapshot_xyxy": [0, 0, 1, 1]},
                {"frame": 52, "box_snapshot_xyxy": [0, 0, 1, 1]},
            ],
            "forced_frames": {42},
        }

    def test_reference_frames_accept_valid_alternate_and_existing_pairs(self):
        self.assertEqual(
            diagnostic.parse_reference_frames(
                "14,0", self.sidecar, self.anchors
            ),
            (0, 14),
        )
        self.assertEqual(
            diagnostic.parse_reference_frames(
                "33,52", self.sidecar, self.anchors
            ),
            (33, 52),
        )

    def test_reference_frames_reject_duplicates(self):
        with self.assertRaisesRegex(
            diagnostic.AppearanceError, "two distinct"
        ):
            diagnostic.parse_reference_frames(
                "14,14", self.sidecar, self.anchors
            )

    def test_reference_frames_reject_sampled_non_anchor(self):
        with self.assertRaisesRegex(diagnostic.AppearanceError, "durable anchor"):
            diagnostic.parse_reference_frames(
                "0,7", self.sidecar, self.anchors
            )

    def test_reference_frames_reject_unsampled_frame(self):
        with self.assertRaisesRegex(diagnostic.AppearanceError, "sampled frame"):
            diagnostic.parse_reference_frames(
                "0,13", self.sidecar, self.anchors
            )

    def test_reference_frames_reject_forced_occlusion_frame(self):
        with self.assertRaisesRegex(
            diagnostic.AppearanceError, "forced occlusion"
        ):
            diagnostic.parse_reference_frames(
                "0,42", self.sidecar, self.anchors
            )

    def test_cli_accepts_runs_with_and_without_track(self):
        common = [
            "diagnose_preview_target_appearance.py",
            "--video",
            "video.mp4",
            "--sidecar",
            "sidecar.json",
            "--anchors",
            "anchors.json",
            "--weights",
            "weights.pt",
            "--output",
            "output.json",
        ]
        with mock.patch.object(sys, "argv", common):
            self.assertIsNone(diagnostic.parse_args().track)
        with mock.patch.object(
            sys, "argv", common + ["--track", "track.json"]
        ):
            self.assertEqual(diagnostic.parse_args().track, Path("track.json"))

    def test_load_and_validate_skips_track_when_omitted(self):
        sidecar = {"sha256": "digest"}
        anchors = {
            "anchors": [
                {"anchor_id": "a", "box_snapshot_xyxy": [0, 0, 1, 1]}
            ]
        }
        with (
            mock.patch.object(
                diagnostic,
                "_load_json",
                side_effect=[{"version": 2}, {"source": {}}],
            ) as load,
            mock.patch.object(
                diagnostic, "require_anchor_source_metadata"
            ),
            mock.patch.object(
                diagnostic.tracker, "_sha256", return_value="digest"
            ),
            mock.patch.object(
                diagnostic.tracker,
                "validate_sidecar",
                return_value=sidecar,
            ),
            mock.patch.object(
                diagnostic.tracker,
                "validate_anchors",
                return_value=anchors,
            ),
            mock.patch.object(diagnostic, "validate_track") as validate_track,
        ):
            result = diagnostic.load_and_validate(
                Path("sidecar.json"), Path("anchors.json"), None
            )
        self.assertEqual(result, (sidecar, anchors))
        self.assertEqual(load.call_count, 2)
        validate_track.assert_not_called()

    def test_load_and_validate_preserves_supplied_track_validation(self):
        sidecar = {"sha256": "digest"}
        anchors = {
            "anchors": [
                {"anchor_id": "a", "box_snapshot_xyxy": [0, 0, 1, 1]}
            ]
        }
        track = {"schema": "blendcap-preview-target-track"}
        with (
            mock.patch.object(
                diagnostic,
                "_load_json",
                side_effect=[{"version": 2}, {"source": {}}, track],
            ) as load,
            mock.patch.object(
                diagnostic, "require_anchor_source_metadata"
            ),
            mock.patch.object(
                diagnostic.tracker, "_sha256", return_value="digest"
            ),
            mock.patch.object(
                diagnostic.tracker,
                "validate_sidecar",
                return_value=sidecar,
            ),
            mock.patch.object(
                diagnostic.tracker,
                "validate_anchors",
                return_value=anchors,
            ),
            mock.patch.object(diagnostic, "validate_track") as validate_track,
        ):
            result = diagnostic.load_and_validate(
                Path("sidecar.json"), Path("anchors.json"), Path("track.json")
            )
        self.assertEqual(result, (sidecar, anchors))
        self.assertEqual(load.call_count, 3)
        validate_track.assert_called_once_with(track, sidecar, anchors)

    def test_source_metadata_uses_hash_or_null_for_optional_track(self):
        sidecar = {
            "sha256": "sidecar",
            "fps": 30.0,
            "sampling_interval": 1,
        }
        with mock.patch.object(
            diagnostic, "_hash_file", side_effect=["anchors", "weights"]
        ) as hash_file:
            without_track = diagnostic.source_metadata(
                sidecar, Path("anchors.json"), None, Path("weights.pt")
            )
        self.assertIsNone(without_track["target_track_sha256"])
        self.assertEqual(hash_file.call_count, 2)

        with mock.patch.object(
            diagnostic,
            "_hash_file",
            side_effect=["anchors", "track", "weights"],
        ) as hash_file:
            with_track = diagnostic.source_metadata(
                sidecar,
                Path("anchors.json"),
                Path("track.json"),
                Path("weights.pt"),
            )
        self.assertEqual(with_track["target_track_sha256"], "track")
        self.assertEqual(hash_file.call_count, 3)

    def test_labels_are_validated_as_per_frame_positions(self):
        sidecar = {
            "frame_to_position": {0: 0, 2: 1},
            "all_boxes": [[(0, 0, 1, 1)], [(0, 0, 1, 1), (1, 1, 2, 2)]],
        }
        self.assertEqual(diagnostic.parse_labels("0:0,2:1", sidecar), {0: 0, 2: 1})
        with self.assertRaises(diagnostic.AppearanceError):
            diagnostic.parse_labels("2:2", sidecar)

    def test_unsafe_output_and_input_collision_are_rejected(self):
        input_paths = [Path(f"input-{index}.json") for index in range(5)]
        with mock.patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(diagnostic.AppearanceError, "beneath"):
                diagnostic.validate_paths(
                    *input_paths,
                    Path("appearance.json"),
                )
            no_track_output = diagnostic.OUTPUT_ROOT / "no-track.json"
            diagnostic.validate_paths(
                input_paths[0],
                input_paths[1],
                input_paths[2],
                None,
                input_paths[4],
                no_track_output,
            )
            with self.assertRaisesRegex(diagnostic.AppearanceError, "overwrite"):
                collision = diagnostic.OUTPUT_ROOT / "input-0.json"
                diagnostic.validate_paths(
                    collision,
                    input_paths[1],
                    input_paths[2],
                    input_paths[3],
                    input_paths[4],
                    collision,
                )


@unittest.skipIf(torch is None, "PyTorch is not installed")
class OSNetArchitectureTests(unittest.TestCase):
    def test_random_weight_architecture_outputs_512_dimensions(self):
        model = appearance_osnet.osnet_ain_x1_0(
            num_classes=3, include_classifier=True
        ).eval()
        with torch.no_grad():
            output = model(torch.randn(1, 3, 256, 128))
        self.assertEqual(tuple(output.shape), (1, 512))

    def test_safe_loader_uses_weights_only_cpu_and_strips_tested_prefix(self):
        tensor = torch.ones(1)
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(
                appearance_osnet.torch,
                "load",
                return_value={"state_dict": {"module.weight": tensor}},
            ) as load,
        ):
            state = appearance_osnet.load_checkpoint_state(Path("weights.pt"))
        self.assertEqual(list(state), ["weight"])
        load.assert_called_once_with(
            Path("weights.pt"), weights_only=True, map_location="cpu"
        )

    def test_common_model_prefix_is_explicitly_supported(self):
        tensor = torch.ones(1)
        state = appearance_osnet._strip_supported_prefix(
            {"model.weight": tensor, "model.bias": tensor}
        )
        self.assertEqual(set(state), {"weight", "bias"})

    def test_complete_prefixed_osnet_checkpoint_is_compatible(self):
        source = appearance_osnet.osnet_ain_x1_0(
            num_classes=7, include_classifier=True
        )
        wrapped = {
            "state_dict": {
                f"module.{key}": value
                for key, value in source.state_dict().items()
            }
        }
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(appearance_osnet.torch, "load", return_value=wrapped),
        ):
            loaded = appearance_osnet.load_osnet_ain_x1_0(Path("weights.pt"))
        self.assertEqual(loaded.classifier.out_features, 7)
        self.assertFalse(loaded.training)

    def test_mixed_prefixes_are_rejected(self):
        tensor = torch.ones(1)
        with self.assertRaises(appearance_osnet.CheckpointError):
            appearance_osnet._strip_supported_prefix(
                {"module.weight": tensor, "bias": tensor}
            )

    def test_compatible_state_dict_loads_and_incompatible_keys_fail(self):
        model = torch.nn.Linear(3, 2)
        appearance_osnet.load_state_dict_strict(model, model.state_dict())
        missing = {"weight": model.state_dict()["weight"]}
        with self.assertRaisesRegex(
            appearance_osnet.CheckpointError, "missing"
        ):
            appearance_osnet.load_state_dict_strict(model, missing)
        unexpected = dict(model.state_dict())
        unexpected["extra"] = torch.ones(1)
        with self.assertRaisesRegex(
            appearance_osnet.CheckpointError, "unexpected"
        ):
            appearance_osnet.load_state_dict_strict(model, unexpected)


if __name__ == "__main__":
    unittest.main()
