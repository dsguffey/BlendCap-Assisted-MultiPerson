"""Clean-source asset assembly rejects source drift before copying."""
import json
from pathlib import Path
import tempfile
import unittest

from tools import build_portable_ai_assets as assets


class PortableAiAssetsTests(unittest.TestCase):
    def test_source_only_uses_authoritative_adapter_and_fails_closed(self):
        temp_root = Path(__file__).resolve().parents[1] / ".build" / "test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            root = Path(folder)
            public, private = (root / name for name in ("public", "private"))
            public.mkdir()
            adapter = private / "vendor" / "kinesia_stream_detector"
            package = adapter / "src" / "sam_3d_pose_estimation"
            package.mkdir(parents=True)
            (adapter / "LICENSE").write_text("CC0 fixture\n")
            (package / "__init__.py").write_text("\n")
            detector = package / "stream_detector.py"
            detector.write_text("ADAPTER_REVISION = 'fixture'\n")
            vendor = private / "vendor" / "sam3-main"
            (vendor / "sam3").mkdir(parents=True)
            (vendor / "LICENSE").write_text("SAM fixture\n")
            (vendor / "sam3" / "__init__.py").write_text("\n")
            rows = [{"path": name, "bytes": source.stat().st_size,
                     "sha256": assets.sha256(source)}
                    for source, name in assets.candidates(public, private, Path(), Path())
                    if name.startswith("analysis_source/")]
            lock = root / "lock.json"
            lock.write_text(json.dumps({"schema": "studiofree-portable-assets-lock",
                                        "version": 1, "files": rows}))
            result = assets.build(public, private, None, None, root / "output", lock,
                                  analysis_source_only=True)
            copied = root / "output" / "analysis_source/src/sam_3d_pose_estimation/stream_detector.py"
            self.assertEqual(assets.sha256(copied), assets.sha256(detector))
            self.assertEqual(len(result["files"]), len(rows))
            detector.write_text("ADAPTER_REVISION = 'changed'\n")
            with self.assertRaisesRegex(ValueError, "differs from pinned lock"):
                assets.build(public, private, None, None, root / "rejected", lock,
                             analysis_source_only=True)
            self.assertFalse((root / "rejected").exists())
            detector.write_text("ADAPTER_REVISION = 'fixture'\n")
            (vendor / "sam3" / "__init__.py").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "differs from pinned lock"):
                assets.build(public, private, None, None, root / "rejected-vendor", lock,
                             analysis_source_only=True)
            self.assertFalse((root / "rejected-vendor").exists())


if __name__ == "__main__":
    unittest.main()
