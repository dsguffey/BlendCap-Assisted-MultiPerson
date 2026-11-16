"""Create one offline customer download from a verified release payload.

The ZIP contains a small Inno installer and its sidecar payload. It is a build
artifact, never a source of redistribution approval or publisher authenticity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

try:
    from .studiofree_release import sha256, verify_stage
    from .studiofree_maintenance import verify_payload
except ImportError:
    from studiofree_release import sha256, verify_stage
    from studiofree_maintenance import verify_payload


def verify_publisher(installer: Path, thumbprint: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", thumbprint):
        raise ValueError("A customer distribution requires an expected publisher certificate thumbprint")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:STUDIOFREE_VERIFY_TARGET; "
              "if ($s.Status -ne 'Valid' -or -not $s.SignerCertificate -or "
              "$s.SignerCertificate.Thumbprint -ne $env:STUDIOFREE_EXPECTED_PUBLISHER) { exit 3 }")
    environment = {**os.environ, "STUDIOFREE_VERIFY_TARGET": str(installer),
                   "STUDIOFREE_EXPECTED_PUBLISHER": thumbprint.upper()}
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=environment, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError("Installer signature is missing, untrusted, or from a different publisher")


def package(installer: Path, payload: Path, output: Path, *, test_fixture: bool = False,
            internal_validation: bool = False, expected_publisher_thumbprint: str = "") -> dict:
    installer = installer.resolve(strict=True)
    payload = payload.resolve(strict=True)
    manifest = verify_stage(payload)
    if test_fixture and internal_validation:
        raise ValueError("Fixture and internal-validation modes cannot be combined")
    if internal_validation and (not manifest.get("internal_test") or
                                not manifest.get("compliance_required")):
        raise ValueError("Internal distribution requires a marked, compliance-checked payload")
    if not test_fixture:
        verify_payload(payload, complete=True, allow_internal=internal_validation)
        try:
            from .studiofree_compliance import verify as verify_compliance
        except ImportError:
            from studiofree_compliance import verify as verify_compliance
        verify_compliance(payload)
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise ValueError("An Inno installer EXE is required")
    if not test_fixture and not internal_validation:
        verify_publisher(installer, expected_publisher_thumbprint)
    if output.exists():
        raise FileExistsError("Distribution output already exists")
    if output.is_relative_to(payload) or output.is_relative_to(installer.parent):
        raise ValueError("Distribution output must be outside its input trees")
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = [(installer, installer.name), (payload / "package.json", "payload/package.json")]
    entries.extend((payload / row["path"], "payload/" + row["path"])
                   for row in manifest["files"])
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=3, allowZip64=True) as archive:
            for source, name in entries:
                archive.write(source, arcname=name)
            receipt = dict(schema="studiofree-offline-distribution", version=1,
                           test_fixture=test_fixture,
                           internal_test=bool(manifest.get("internal_test")),
                           product_version=manifest["product_version"],
                           build_id=manifest["build_id"],
                           installer=installer.name, installer_sha256=sha256(installer),
                           package_sha256=sha256(payload / "package.json"),
                           payload_files=len(manifest["files"]),
                           payload_bytes=sum(row["bytes"] for row in manifest["files"]))
            archive.writestr("distribution.json", json.dumps(receipt, sort_keys=True) + "\n")
        with zipfile.ZipFile(output) as archive:
            expected = {name for _, name in entries} | {"distribution.json"}
            if set(archive.namelist()) != expected or archive.testzip() is not None:
                raise ValueError("Distribution ZIP failed integrity check")
        return receipt
    except Exception:
        output.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-fixture", action="store_true",
                        help="Allow a disposable core-only fixture; never distribute it")
    parser.add_argument("--internal-validation", action="store_true",
                        help="Package only a marked full internal-test stage")
    parser.add_argument("--expected-publisher-thumbprint", default="",
                        help="Required SHA-1 certificate thumbprint for a customer distribution")
    args = parser.parse_args()
    receipt = package(args.installer, args.payload, args.output,
                      test_fixture=args.test_fixture,
                      internal_validation=args.internal_validation,
                      expected_publisher_thumbprint=args.expected_publisher_thumbprint)
    print(f"Offline distribution: {args.output} ({receipt['payload_files']} payload files)")


if __name__ == "__main__":
    main()
