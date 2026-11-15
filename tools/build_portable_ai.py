"""Assemble isolated Windows AI runtimes from a pinned CPython archive and wheels.

Installed site-packages are build inputs only. Each selected distribution must
have a wheel RECORD, and every copied file is checked against its RECORD hash.
The lock records exact distribution versions and RECORD identities without
embedding developer paths in the installed components.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess

if __package__:
    from .build_portable_control import CPYTHON_31210_SHA256, build as build_control, digest
else:
    from build_portable_control import CPYTHON_31210_SHA256, build as build_control, digest


PACKAGES = {
    "body": (
        "antlr4-python3-runtime", "attrs", "braceexpand", "brotli", "certifi",
        "charset-normalizer", "click", "colorama", "cycler", "einops",
        "filelock", "fonttools", "fsspec", "h11", "httpcore", "httpx",
        "huggingface_hub", "idna", "kiwisolver", "lightning-utilities",
        "matplotlib", "moge", "mpmath", "networkx", "numpy", "omegaconf",
        "opencv-contrib-python", "packaging", "pillow", "Pygments",
        "pyparsing", "python-dateutil", "pytorch-lightning", "PyYAML",
        "rich", "roma", "scipy", "setuptools", "six", "sympy", "termcolor",
        "torch", "torchmetrics", "torchvision", "tqdm", "typing_extensions",
        "utils3d", "yacs",
    ),
    "analysis": (
        "anyio", "certifi", "click", "colorama", "dill", "einops",
        "filelock", "ftfy", "h11", "httpcore", "httpx", "huggingface_hub",
        "idna", "iopath", "mpmath", "numpy", "opencv-python", "packaging",
        "pillow", "portalocker", "psutil", "pycocotools", "Pygments",
        "PyYAML", "regex", "rich", "safetensors", "setuptools", "sympy",
        "timm", "torch", "torchvision", "tqdm", "typing_extensions", "wcwidth",
    ),
}


def canonical(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def safe_member(raw: str) -> Path:
    name = raw.replace("\\", "/")
    parts = name.split("/")
    if (not name or name.startswith("/") or ":" in name or
            any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(f"Unsafe wheel RECORD member: {raw!r}")
    return Path(*parts)


def checked_file(source: Path, site: Path, record_hash: str) -> None:
    if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(site):
        raise ValueError(f"Wheel member missing, linked, or outside site-packages: {source}")
    if record_hash:
        algorithm, _, encoded = record_hash.partition("=")
        if algorithm != "sha256" or not encoded:
            raise ValueError(f"Unsupported wheel RECORD digest: {source}")
        actual = base64.urlsafe_b64encode(bytes.fromhex(digest(source))).rstrip(b"=").decode("ascii")
        if actual != encoded:
            raise ValueError(f"Installed wheel file differs from RECORD: {source}")


def distribution_index(site: Path) -> dict[str, metadata.Distribution]:
    result = {}
    for dist in metadata.distributions(path=[site]):
        name = canonical(dist.metadata["Name"])
        if name in result:
            raise ValueError(f"Duplicate installed distribution: {name}")
        result[name] = dist
    return result


def selected_files(site: Path, role: str, expected: dict | None) -> tuple[list, list]:
    index = distribution_index(site)
    files: dict[str, tuple[Path, str, str]] = {}
    versions = []
    locked = {canonical(row["name"]): row for row in expected["roles"][role]} if expected else {}
    if expected and set(locked) != {canonical(name) for name in PACKAGES[role]}:
        raise ValueError("Lock distribution set differs from production closure")
    for requested in PACKAGES[role]:
        name = canonical(requested)
        if name not in index:
            raise ValueError(f"Missing {role} build input distribution: {requested}")
        dist = index[name]
        record_path = Path(str(dist._path)) / "RECORD"
        checked_file(record_path, site, "")
        record_sha = digest(record_path)
        item = {"name": name, "version": dist.version, "record_sha256": record_sha}
        if expected and item != locked[name]:
            raise ValueError(f"Build input differs from pinned lock: {name}")
        versions.append(item)
        with record_path.open(newline="", encoding="utf-8") as stream:
            for raw, row_hash, _size in csv.reader(stream):
                # Wheel entry points and man pages installed outside
                # site-packages are not part of the embedded runtime.
                if raw.replace("\\", "/").startswith(("../../Scripts/", "../../share/")):
                    continue
                rel = safe_member(raw)
                if (rel.suffix.lower() in {".pyc", ".pyo", ".pth"} or
                        any(part in {"__pycache__", "tests", "test"} for part in rel.parts)):
                    continue
                if rel.name == "direct_url.json" or rel.parts[0].lower() in {"tests", "test"}:
                    continue
                source = site / rel
                checked_file(source, site, row_hash)
                key = rel.as_posix().casefold()
                if key in files:
                    old_source, old_hash, old_name = files[key]
                    if old_source != source or (old_hash and row_hash and old_hash != row_hash):
                        raise ValueError(f"Overlapping wheel members: {old_name}, {name}: {rel}")
                    continue
                files[key] = (source, row_hash, name)
    return sorted(files.values(), key=lambda row: row[0].as_posix().casefold()), sorted(versions, key=lambda row: row["name"])


def build(archive: Path, site: Path, role: str, output: Path, lock: Path | None) -> dict:
    archive, site, output = archive.resolve(strict=True), site.resolve(strict=True), output.resolve()
    if role not in PACKAGES or not site.name == "site-packages":
        raise ValueError("Expected a supported role and explicit site-packages input")
    if output.exists():
        raise FileExistsError("Portable AI output must be a new directory")
    expected = json.loads(lock.read_text(encoding="utf-8")) if lock else None
    if expected and (expected.get("schema") != "studiofree-portable-ai-lock" or
                     expected.get("version") != 1 or role not in expected.get("roles", {}) or
                     expected.get("cpython_sha256") != CPYTHON_31210_SHA256):
        raise ValueError("Portable AI input lock is incompatible")
    source_files, versions = selected_files(site, role, expected)
    if digest(archive) != CPYTHON_31210_SHA256:
        raise ValueError("CPython archive differs from pinned source")
    try:
        py_report = build_control(archive, output)
        scripts = output / "Scripts"
        scripts.mkdir()
        for name in ("python.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            (output / name).rename(scripts / name)
        (scripts / "python312._pth").write_text(
            "..\\Lib\n..\\DLLs\n..\\Lib\\site-packages\nimport site\n", encoding="ascii")
        destination = output / "Lib" / "site-packages"
        report_files = []
        for source, _row_hash, owner in source_files:
            rel = source.relative_to(site)
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            report_files.append({"path": rel.as_posix(), "owner": owner,
                                 "bytes": target.stat().st_size, "sha256": digest(target)})
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            environment.pop(name, None)
        check = subprocess.run((str(scripts / "python.exe"), "-I", "-B", "-c",
                                "import sys,pathlib,ssl,ctypes; "
                                "r=pathlib.Path(sys.argv[1]).resolve(); "
                                "assert pathlib.Path(sys.base_prefix).resolve().is_relative_to(r); "
                                "assert all(pathlib.Path(x).resolve().is_relative_to(r) for x in sys.path if x); "
                                "print(sys.version.split()[0])", str(output)),
                               cwd=output, env=environment, capture_output=True, text=True, timeout=30)
        if check.returncode or check.stdout.strip() != "3.12.10":
            raise RuntimeError("Packaged Python isolation failed: " + check.stderr[-1000:])
        return {"schema": "studiofree-portable-ai-build", "role": role,
                "python": "3.12.10", "cpython_sha256": CPYTHON_31210_SHA256,
                "distributions": versions, "source_site": str(site),
                "python_bytes": py_report["bytes"], "package_bytes": sum(x["bytes"] for x in report_files),
                "files": report_files}
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--role", choices=sorted(PACKAGES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.archive, args.site, args.role, args.output, args.lock)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"role": args.role, "python": report["python"],
                      "distributions": len(report["distributions"]),
                      "files": len(report["files"]),
                      "bytes": report["python_bytes"] + report["package_bytes"]}))


if __name__ == "__main__":
    main()
