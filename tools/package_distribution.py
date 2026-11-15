"""Create one offline customer download from a verified release payload.

The ZIP contains a small Inno installer and its sidecar payload. It is a build
artifact, never a source of redistribution approval or publisher authenticity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

try:
    from .studiofree_release import sha256, verify_stage
    from .studiofree_maintenance import verify_payload
except ImportError:
    from studiofree_release import sha256, verify_stage
    from studiofree_maintenance import verify_payload


def package(installer: Path, payload: Path, output: Path, *, test_fixture: bool = False) -> dict:
    installer = installer.resolve(strict=True)
    payload = payload.resolve(strict=True)
    manifest = verify_stage(payload)
    if not test_fixture:
        verify_payload(payload, complete=True)
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise ValueError("An Inno installer EXE is required")
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
    args = parser.parse_args()
    receipt = package(args.installer, args.payload, args.output, test_fixture=args.test_fixture)
    print(f"Offline distribution: {args.output} ({receipt['payload_files']} payload files)")


if __name__ == "__main__":
    main()
