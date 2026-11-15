"""Build the offline maintenance interpreter from a pinned CPython archive.

The archive is an explicit input. No download, developer venv, or global Python
installation becomes a runtime dependency of the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


CPYTHON_31210_SHA256 = "8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d"
ROOT_FILES = {"python.exe", "python3.dll", "python312.dll", "vcruntime140.dll",
              "vcruntime140_1.dll", "LICENSE.txt"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def selected(name: str) -> bool:
    parts = name.split("/")
    if len(parts) == 1:
        return name in ROOT_FILES
    return parts[0] in {"Lib", "DLLs"} and not (
        parts[:2] == ["Lib", "site-packages"] or
        "__pycache__" in parts or name.endswith((".pyc", ".pyo")))


def build(archive: Path, output: Path) -> dict:
    archive = archive.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Control-runtime output must be a new directory")
    if digest(archive) != CPYTHON_31210_SHA256:
        raise ValueError("CPython 3.12.10 source archive SHA-256 mismatch")
    files = []
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                name = member.filename
                if member.is_dir() or not selected(name):
                    continue
                parts = name.split("/")
                if ("\\" in name or ":" in name or name.startswith("/") or
                        any(part in ("", ".", "..") for part in parts)):
                    raise ValueError("Unsafe CPython archive path")
                target = output.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                files.append({"path": name, "bytes": target.stat().st_size,
                              "sha256": digest(target)})
        if not ROOT_FILES.issubset({row["path"] for row in files}):
            raise ValueError("CPython archive lacks a required control-runtime file")
        result = subprocess.run((str(output / "python.exe"), "-I", "-B", "-c",
                                 "import ctypes,ssl,sys,pathlib; "
                                 "r=pathlib.Path(sys.argv[1]).resolve(); "
                                 "assert pathlib.Path(sys.base_prefix).resolve()==r; "
                                 "print(sys.version.split()[0])", str(output)),
                                cwd=output, capture_output=True, text=True, timeout=30)
        if result.returncode or result.stdout.strip() != "3.12.10":
            raise RuntimeError("Relocated control Python failed: " + result.stderr[-1000:])
        return {"source_sha256": CPYTHON_31210_SHA256,
                "version": "3.12.10", "files": files,
                "bytes": sum(row["bytes"] for row in files)}
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.archive, args.output)
    print(json.dumps({"version": report["version"], "files": len(report["files"]),
                      "bytes": report["bytes"], "source_sha256": report["source_sha256"]}))


if __name__ == "__main__":
    main()
