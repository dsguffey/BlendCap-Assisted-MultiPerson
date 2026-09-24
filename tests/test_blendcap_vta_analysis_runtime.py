"""Deterministic tests for the single offline SAM3 Analyze resolver."""

import json
from pathlib import Path
import tempfile
import unittest

from blendcap_vta_analysis_runtime import (
    AnalysisRuntimeUnavailable, resolve_analysis_runtime,
)


class AnalysisRuntimeResolutionTests(unittest.TestCase):
    def _fixture(self, directory):
        parent = Path(directory)
        core = parent / "Video-to-Animation"
        core.mkdir()
        evaluation = parent / "Kinesia Evaluation"
        reports = evaluation / "reports"
        reports.mkdir(parents=True)
        python = evaluation / "environment" / ".venv" / "Scripts" / "python.exe"
        source = evaluation / "source"
        checkpoint = evaluation / "models" / "sam3" / "sam3.pt"
        detector = source / "src" / "sam_3d_pose_estimation" / "stream_detector.py"
        builder = source / "vendor" / "sam3-main" / "sam3" / "model_builder.py"
        for path in (python, checkpoint, detector, builder):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        (reports / "installation_manifest.json").write_text(json.dumps({
            "schema": "kinesia-isolated-installation-manifest", "version": 1,
            "source": {"checkout": "source", "commit": "pinned"},
        }), encoding="utf-8")
        (reports / "phase2_pytorch_stream_detector_adapter_manifest.json").write_text(
            json.dumps({
                "schema": "kinesia-pytorch-stream-detector-adapter-manifest", "version": 1,
                "base_source": {"commit": "pinned"},
                "adapter": {"windows_backend": "pytorch-sam3", "device": "cuda"},
                "environment": {"path": "environment/.venv"},
                "checkpoint": {"path_within_evaluation": "models/sam3/sam3.pt"},
            }), encoding="utf-8")
        return core, python, source, checkpoint

    def test_frozen_installation_manifest_resolves_without_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            core, python, source, checkpoint = self._fixture(directory)
            seen = []
            runtime = resolve_analysis_runtime(
                core, Path(directory) / "missing-blendcap-python.exe",
                probe=lambda executable, root: seen.append((executable, root)) or True,
            )
            self.assertEqual((runtime.python, runtime.source, runtime.checkpoint),
                             (python.resolve(), source.resolve(), checkpoint.resolve()))
            self.assertEqual(runtime.origin, "evaluated-installation-manifest")
            self.assertEqual(seen, [(python, source)])

    def test_existing_blendcap_interpreter_is_only_used_after_successful_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            core, python, source, checkpoint = self._fixture(directory)
            blendcap = Path(directory) / "blendcap" / "venv" / "Scripts" / "python.exe"
            blendcap.parent.mkdir(parents=True)
            blendcap.write_bytes(b"fixture")
            tried = []
            def probe(executable, _source):
                tried.append(executable)
                return executable == blendcap
            runtime = resolve_analysis_runtime(core, blendcap, probe=probe)
            self.assertEqual(runtime.origin, "blendcap-venv")
            self.assertEqual(runtime.python, blendcap.resolve())
            self.assertEqual((runtime.source, runtime.checkpoint), (source, checkpoint))
            self.assertEqual(tried, [python, blendcap])

    def test_explicit_preference_is_fallback_and_incomplete_runtime_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            core, python, source, checkpoint = self._fixture(directory)
            configured = Path(directory) / "custom" / "python.exe"
            configured.parent.mkdir()
            configured.write_bytes(b"fixture")
            runtime = resolve_analysis_runtime(
                core, Path(directory) / "missing.exe", configured_python=str(configured),
                probe=lambda executable, _source: executable == configured,
            )
            self.assertEqual(runtime.origin, "user-preference")
            self.assertEqual(runtime.python, configured.resolve())
            isolated_core = Path(directory) / "isolated" / "core"
            manual = resolve_analysis_runtime(
                isolated_core, Path(directory) / "missing.exe",
                configured_python=str(configured), configured_source=str(source),
                configured_checkpoint=str(checkpoint), probe=lambda *_: True,
            )
            self.assertEqual(manual.origin, "user-preference")
            self.assertEqual(manual.source, source.resolve())
            with self.assertRaisesRegex(AnalysisRuntimeUnavailable,
                                        "Analysis environment unavailable.*BlendCap Preferences"):
                resolve_analysis_runtime(core, Path(directory) / "missing.exe",
                                         probe=lambda *_: False)
            # A preference without a recorded source/checkpoint cannot select an
            # arbitrary system Python by its filename alone.
            with self.assertRaises(AnalysisRuntimeUnavailable):
                resolve_analysis_runtime(isolated_core,
                                         Path(directory) / "missing.exe",
                                         configured_python=str(configured),
                                         probe=lambda *_: True)

    def test_existing_blendcap_environment_can_use_configured_model_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, source, checkpoint = self._fixture(directory)
            isolated_core = Path(directory) / "isolated" / "core"
            blendcap = Path(directory) / "blendcap" / "venv" / "Scripts" / "python.exe"
            blendcap.parent.mkdir(parents=True)
            blendcap.write_bytes(b"fixture")
            runtime = resolve_analysis_runtime(
                isolated_core, blendcap, configured_source=str(source),
                configured_checkpoint=str(checkpoint), probe=lambda *_: True,
            )
            self.assertEqual(runtime.origin, "blendcap-venv")
            self.assertEqual(runtime.python, blendcap.resolve())

    def test_manifest_escape_or_mismatched_backend_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            core, _, _, _ = self._fixture(directory)
            path = (Path(directory) / "Kinesia Evaluation" / "reports" /
                    "phase2_pytorch_stream_detector_adapter_manifest.json")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["environment"]["path"] = "../outside"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(AnalysisRuntimeUnavailable):
                resolve_analysis_runtime(core, Path(directory) / "missing.exe",
                                         probe=lambda *_: True)
            manifest["environment"]["path"] = "environment/.venv"
            manifest["adapter"]["windows_backend"] = "mlx-sam3"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(AnalysisRuntimeUnavailable):
                resolve_analysis_runtime(core, Path(directory) / "missing.exe",
                                         probe=lambda *_: True)


if __name__ == "__main__":
    unittest.main()
