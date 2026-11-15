"""Focused wheel-integrity and production-closure checks for portable AI builds."""
import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import build_portable_ai


class PortableAiBuildTests(unittest.TestCase):
    def test_record_hashes_are_checked_and_test_modules_are_omitted(self):
        temp_root = Path(__file__).resolve().parents[1] / ".build" / "test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            site = Path(folder) / "site-packages"
            dist = site / "demo-1.0.dist-info"
            dist.mkdir(parents=True)
            files = {
                "demo/__init__.py": b"value = 1\n",
                "demo/tests/test_demo.py": b"assert True\n",
                "demo-1.0.dist-info/METADATA": b"Name: demo\nVersion: 1.0\n",
                "demo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
            }
            rows = []
            for name, data in files.items():
                path = site / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rows.append(f"{name},sha256={encoded},{len(data)}")
            rows.append("demo-1.0.dist-info/RECORD,,")
            (dist / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
            with patch.dict(build_portable_ai.PACKAGES, {"body": ("demo",)}):
                selected, versions = build_portable_ai.selected_files(site, "body", None)
                paths = {path.relative_to(site).as_posix() for path, _, _ in selected}
                self.assertIn("demo/__init__.py", paths)
                self.assertNotIn("demo/tests/test_demo.py", paths)
                lock = {"roles": {"body": versions}}
                build_portable_ai.selected_files(site, "body", lock)
                (site / "demo/__init__.py").write_bytes(b"changed\n")
                with self.assertRaisesRegex(ValueError, "differs from RECORD"):
                    build_portable_ai.selected_files(site, "body", lock)


if __name__ == "__main__":
    unittest.main()
