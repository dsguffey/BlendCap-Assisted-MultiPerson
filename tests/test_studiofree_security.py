"""Adversarial release-authentication checks on tiny disposable payloads."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.studiofree_maintenance import verify_payload
from tools.package_distribution import verify_publisher


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "installer" / "verify_sidecar.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


class SidecarSecurityTests(unittest.TestCase):
    def setUp(self):
        if not POWERSHELL.is_file():
            self.skipTest("Windows PowerShell is required for installer preflight")
        work = ROOT / ".build" / "test-temp"
        work.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(temporary.cleanup)
        self.payload = Path(temporary.name) / "payload"
        (self.payload / "engine").mkdir(parents=True)
        (self.payload / "engine" / "vta_engine.exe").write_bytes(b"engine fixture")
        self._manifest()

    def _manifest(self, path="engine/vta_engine.exe"):
        content = (self.payload / "engine" / "vta_engine.exe").read_bytes()
        manifest = dict(schema="studiofree-package", version=1,
                        files=[dict(path=path, bytes=len(content), sha256=hashlib.sha256(content).hexdigest())])
        (self.payload / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        return hashlib.sha256((self.payload / "package.json").read_bytes()).hexdigest()

    def _check(self, digest):
        return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(PREFLIGHT), "-Payload", str(self.payload),
                               "-ExpectedManifestSha256", digest],
                              capture_output=True, text=True, timeout=30)

    def test_signed_manifest_anchor_and_member_hash(self):
        original = self._manifest()
        self.assertEqual(self._check(original).returncode, 0)
        self._manifest(path="engine/changed.exe")
        self.assertNotEqual(self._check(original).returncode, 0)
        current = hashlib.sha256((self.payload / "package.json").read_bytes()).hexdigest()
        self.assertNotEqual(self._check(current).returncode, 0)

    def test_tampered_or_extra_payload_fails_before_execution(self):
        anchor = self._manifest()
        (self.payload / "engine" / "vta_engine.exe").write_bytes(b"injected code")
        self.assertNotEqual(self._check(anchor).returncode, 0)
        (self.payload / "engine" / "vta_engine.exe").write_bytes(b"engine fixture")
        (self.payload / "engine" / "sitecustomize.py").write_text("# injected", encoding="utf-8")
        self.assertNotEqual(self._check(anchor).returncode, 0)

    def test_missing_payload_executable_is_rejected(self):
        anchor = self._manifest()
        (self.payload / "engine" / "vta_engine.exe").unlink()
        self.assertNotEqual(self._check(anchor).returncode, 0)

    def test_manifest_path_escape_is_rejected_even_with_matching_digest(self):
        self.assertNotEqual(self._check(self._manifest(path="../outside.exe")).returncode, 0)

    def test_linked_install_target_is_rejected_before_delete(self):
        outside = self.payload.parent / "owner"
        outside.mkdir()
        linked = self.payload.parent / "install-link"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory links unavailable")
        result = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(PREFLIGHT), "-InstallTarget", str(linked / "payload")],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_linked_payload_member_is_rejected(self):
        outside = self.payload.parent / "outside.txt"
        outside.write_bytes(b"engine fixture")
        target = self.payload / "engine" / "vta_engine.exe"
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable")
        self.assertNotEqual(self._check(self._manifest()).returncode, 0)

    def test_installed_release_rejects_undeclared_code(self):
        from tests.test_studiofree_release_lifecycle import fixture
        payload = fixture(self.payload.parent, "A")
        (payload / "engine" / "sitecustomize.py").write_text("# injected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Undeclared"):
            verify_payload(payload)

    def test_customer_packaging_rejects_untrusted_installer(self):
        installer = self.payload.parent / "unsigned.exe"
        installer.write_bytes(b"not signed")
        with self.assertRaisesRegex(ValueError, "thumbprint"):
            verify_publisher(installer, "")
        with self.assertRaisesRegex(ValueError, "signature"):
            verify_publisher(installer, "a" * 40)

    def test_customer_publisher_gate_accepts_windows_trust_and_rejects_mismatch(self):
        # The OS PowerShell binary supplies a real trusted Authenticode fixture.
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() != "PSMODULEPATH"}
        result = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
             "$s=Get-AuthenticodeSignature -LiteralPath $env:STUDIOFREE_FIXTURE; "
             "if ($s.Status -ne 'Valid') { exit 2 }; $s.SignerCertificate.Thumbprint"],
            env={**environment, "STUDIOFREE_FIXTURE": str(POWERSHELL)},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            self.skipTest("Windows PowerShell has no trusted Authenticode signer")
        thumbprint = result.stdout.strip()
        verify_publisher(POWERSHELL, thumbprint)
        with self.assertRaisesRegex(ValueError, "publisher"):
            verify_publisher(POWERSHELL, "0" * 40)


if __name__ == "__main__":
    unittest.main()
