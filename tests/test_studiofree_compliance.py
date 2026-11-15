"""Small fail-closed checks for the customer compliance sidecar."""
from pathlib import Path
import tarfile
import tempfile
import unittest

from tools import studiofree_compliance as compliance

ROOT = Path(__file__).resolve().parents[1]


class ComplianceTests(unittest.TestCase):
    def test_paths_cannot_escape_source_bundle(self):
        for name in ("../secret", "C:/secret", "/secret", "a//b", "a\\b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                compliance.safe(name)

    def test_empty_upstream_source_archive_is_rejected(self):
        work = ROOT / ".build/test-temp"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            archive = Path(directory) / "empty.tar.xz"
            with tarfile.open(archive, "w:xz"):
                pass
            with self.assertRaisesRegex(ValueError, "Empty corresponding-source"):
                compliance._source_archive(archive)

    def test_all_reviewed_native_exclusions_are_named(self):
        self.assertEqual(len(compliance.EXCLUDED_NATIVE), 5)
        self.assertEqual({Path(name).name for name in compliance.EXCLUDED_NATIVE},
                         {"nvperf_host.dll", "nvrtc64_130_0.alt.dll",
                          "nvrtc64_120_0.alt.dll", "cusolverMg64_11.dll",
                          "cusolverMg64_12.dll"})


if __name__ == "__main__":
    unittest.main()
