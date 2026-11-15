"""Build and verify a bounded StudioFree MoCap customer release stage.

This is developer tooling. It never uses the owner's active Blender profile,
runtime configuration, entitlement, or project files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PRIVATE_DEFAULT = ROOT.parent / "Video-to-Animation"
COMPONENTS = ("control", "body", "analysis", "analysis_source", "models")
SOURCE_SUFFIXES = {".py", ".json", ".toml", ".yaml", ".yml", ".txt", ".md", ".csv", ".blend"}
FORBIDDEN_PARTS = {".git", "__pycache__", ".build", "tests", "tools", "research", "logs"}
EXTENSION_FORBIDDEN = FORBIDDEN_PARTS | {"checkpoints", "venv"}
ROOT_EXCLUDE = {"vta_package.py", "save_face_data.py", "convert_source.py", "overlay_track.py"}


def run(*args: str, cwd: Path = ROOT, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=900, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}\n{result.stdout[-4000:]}")
    return result.stdout


def git_commit(repo: Path) -> str:
    return run("git", "rev-parse", "HEAD", cwd=repo).strip()


def require_committed_source(repo: Path) -> None:
    for args in (("git", "diff", "--quiet"), ("git", "diff", "--cached", "--quiet")):
        if subprocess.run(args, cwd=repo, check=False).returncode:
            raise ValueError(f"Commit tracked source changes before a complete release build: {repo}")
    status = run("git", "ls-files", "--others", "--exclude-standard", cwd=repo).splitlines()
    relevant = ((extension_member(name) or name.startswith("installer/") or
                 name in {"build_release.ps1", "tools/studiofree_release.py",
                          "tools/studiofree_maintenance.py"}) for name in status) if repo == ROOT else (
        (name.startswith(("vta_core/", "vta_protocol/")) or "/" not in name)
        and Path(name).suffix.lower() in {".py", ".json", ".toml"} for name in status)
    if any(relevant):
        raise ValueError(f"Untracked build-relevant source must be reviewed before release: {repo}")


def git_tracked(repo: Path) -> list[str]:
    raw = subprocess.check_output(("git", "ls-files", "-z"), cwd=repo)
    return sorted(p.decode("utf-8") for p in raw.split(b"\0") if p)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(name: str) -> Path:
    if (not name or len(name) > 512 or "\\" in name or ":" in name or
            name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError(f"Unsafe package member: {name!r}")
    return Path(*name.split("/"))


def regular_file(path: Path, boundary: Path) -> None:
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError(f"Missing, linked, or escaping source file: {path}")
    for ancestor in (path, *path.parents):
        if ancestor == boundary.parent:
            break
        if getattr(ancestor, "is_junction", lambda: False)():
            raise ValueError(f"Junction in package source: {ancestor}")


def extension_member(name: str) -> bool:
    path = safe_relative(name)
    parts = path.parts
    if any(part in EXTENSION_FORBIDDEN for part in parts) or any(part.endswith("_vta") for part in parts):
        return False
    if len(parts) == 1:
        return (name in {"blender_manifest.toml", "LICENSE", "NOTICE.txt", "face_calibration.json",
                         "blendcap_workspace.blend", "vta_workspace.blend", "vta_workspace_mode.blend"}
                or (path.suffix == ".py" and name not in ROOT_EXCLUDE))
    if parts[0] in {"blendcap", "pipeline", "vta_protocol", "retarget_maps", "rest_pose_presets", "licenses"}:
        return path.suffix in SOURCE_SUFFIXES
    # Public provider implementation is required by the body worker. Model weights
    # and untracked local checkpoints are never copied from the checkout.
    if parts[0] == "models":
        return path.suffix in {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md"}
    return False


def copy_extension_source(destination: Path) -> list[str]:
    names = [name for name in git_tracked(ROOT) if extension_member(name)]
    if "blender_manifest.toml" not in names or "__init__.py" not in names:
        raise ValueError("Tracked extension source is incomplete")
    for name in names:
        src = ROOT / safe_relative(name)
        regular_file(src, ROOT)
        dst = destination / safe_relative(name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return names


def blender_environment(temp: Path) -> dict:
    env = os.environ.copy()
    for key, sub in (("BLENDER_USER_CONFIG", "config"), ("BLENDER_USER_SCRIPTS", "scripts"),
                     ("BLENDER_USER_DATAFILES", "data"), ("BLENDER_USER_EXTENSIONS", "extensions")):
        path = temp / sub
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    return env


def build_extension(blender: Path, source: Path, output: Path, temp: Path) -> None:
    manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
    from vta_protocol.runtime import PRODUCT_VERSION
    if manifest["version"] != PRODUCT_VERSION or manifest["id"] != "blendcap" or manifest["blender_version_min"] != "5.2.0":
        raise ValueError("Extension metadata differs from the supported product release")
    env = blender_environment(temp)
    run(str(blender), "--command", "extension", "validate", str(source), env=env)
    run(str(blender), "--command", "extension", "build", "--source-dir", str(source),
        "--output-filepath", str(output), env=env)
    run(str(blender), "--command", "extension", "validate", str(output), env=env)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        for name in names:
            safe_relative(name.rstrip("/"))
            if any(part in EXTENSION_FORBIDDEN for part in name.split("/")):
                raise ValueError(f"Forbidden extension member: {name}")
        if "__init__.py" not in names or "blender_manifest.toml" not in names:
            raise ValueError("Extension archive is incomplete")


def copy_engine(dist: Path, target: Path) -> dict:
    release = json.loads((dist / "release.json").read_text(encoding="utf-8"))
    from vta_protocol.runtime import PRODUCT_VERSION
    if release.get("product_version") != PRODUCT_VERSION or release.get("protocol") != "vta-engine":
        raise ValueError("Compiled engine is incompatible with the adapter")
    if not (dist / "vta_engine.exe").is_file() or sha256(dist / "vta_engine.exe") != release.get("executable_sha256"):
        raise ValueError("Compiled engine differs from release identity")
    target.mkdir(parents=True)
    for source in sorted(dist.rglob("*")):
        if source.is_dir():
            continue
        regular_file(source, dist)
        rel = source.relative_to(dist)
        name = rel.as_posix()
        safe_relative(name)
        if any(part in EXTENSION_FORBIDDEN for part in rel.parts) or source.suffix.lower() in {".pdb", ".map", ".log", ".xml", ".c", ".h"}:
            raise ValueError(f"Forbidden compiled artifact: {name}")
        if source.suffix.lower() in {".py", ".pyc"} and (len(rel.parts) != 2 or rel.parts[0] != "vta_protocol"):
            raise ValueError(f"Private implementation source in compiled artifact: {name}")
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out)
    if not (target / "release.json").is_file():
        raise ValueError("Release identity missing from stage")
    return release


def copy_maintenance(target: Path) -> None:
    target.mkdir(parents=True)
    script = ROOT / "tools" / "studiofree_maintenance.py"
    if "tools/studiofree_maintenance.py" not in git_tracked(ROOT):
        raise ValueError("Maintenance implementation must be committed before staging")
    regular_file(script, ROOT)
    shutil.copyfile(script, target / script.name)
    protocol = target / "vta_protocol"
    protocol.mkdir()
    for name in git_tracked(ROOT):
        if not name.startswith("vta_protocol/") or not name.endswith(".py"):
            continue
        source = ROOT / safe_relative(name)
        regular_file(source, ROOT)
        shutil.copyfile(source, protocol / source.name)


def add_approved_components(spec_path: Path, destination: Path) -> None:
    """Copy only explicitly reviewed, content-pinned external payload files."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if set(spec) != {"components", "routes"} or set(spec["components"]) != set(COMPONENTS):
        raise ValueError("All five runtime/model component inventories are required")
    from vta_protocol.runtime import ROUTES
    if set(spec["routes"]) != set(ROUTES):
        raise ValueError("Runtime route inventory is incomplete")
    for name, route in spec["routes"].items():
        if not isinstance(route, str):
            raise ValueError(f"Invalid route: {name}")
        if route != ".":
            safe_relative(route)
    (destination / "runtime-routes.json").write_text(json.dumps(spec["routes"], sort_keys=True), encoding="utf-8")
    for component in COMPONENTS:
        row = spec["components"][component]
        if set(row) != {"root", "files", "redistribution"} or row["redistribution"] != "approved":
            raise ValueError(f"{component} lacks explicit redistribution approval")
        root = Path(row["root"]).resolve(strict=True)
        if not isinstance(row["files"], list) or not row["files"]:
            raise ValueError(f"Empty component inventory: {component}")
        seen = set()
        for item in row["files"]:
            if set(item) != {"path", "bytes", "sha256"}:
                raise ValueError("Component member lacks a pinned identity")
            rel = safe_relative(item["path"])
            if rel.as_posix().casefold() in seen:
                raise ValueError(f"Duplicate component member: {component}/{rel}")
            seen.add(rel.as_posix().casefold())
            source = root / rel
            regular_file(source, root)
            if source.stat().st_size != item["bytes"] or sha256(source) != item["sha256"]:
                raise ValueError(f"Component integrity mismatch: {component}/{rel}")
            target = destination / component / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    from vta_protocol.runtime import BODY_MODELS
    required = [destination / "body" / "save_mhr_data.py",
                destination / "analysis_source" / "src/sam_3d_pose_estimation/stream_detector.py",
                *[destination / "body" / safe_relative(name) for name in BODY_MODELS]]
    for path in required:
        if not path.is_file():
            raise ValueError(f"Required runtime/model component missing: {path.relative_to(destination)}")


def package_manifest(root: Path, release: dict, public_commit: str, private_commit: str) -> dict:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        regular_file(path, root)
        rel = path.relative_to(root).as_posix()
        safe_relative(rel)
        if (path.name.lower() in {"runtime.json", "entitlement.dpapi", "pyvenv.cfg"}
                or path.suffix.lower() in {".pth", ".pdb", ".map"}):
            raise ValueError(f"Machine-local or debug file in package: {rel}")
        if (any(part in FORBIDDEN_PARTS for part in Path(rel).parts)
                or (rel.startswith(("engine/", "maintenance/")) and any(part in {"checkpoints", "venv"} for part in Path(rel).parts))
                or (path.suffix.lower() in {".blend", ".blend1", ".log", ".pdb", ".pyc"}
                    and not rel.startswith("engine/vta_protocol/"))):
            raise ValueError(f"Forbidden staged file: {rel}")
        files.append(dict(path=rel, bytes=path.stat().st_size, sha256=sha256(path), component=rel.split("/")[0]))
    return dict(schema="studiofree-package", version=1, product_version=release["product_version"],
                build_id=release["build_id"], fingerprint=release["fingerprint"],
                protocol=8, public_commit=public_commit, private_commit=private_commit, files=files)


def verify_stage(stage: Path) -> dict:
    data = json.loads((stage / "package.json").read_text(encoding="utf-8"))
    if data.get("schema") != "studiofree-package" or data.get("version") != 1 or not data.get("files"):
        raise ValueError("Invalid package manifest")
    seen = set()
    for row in data["files"]:
        rel = safe_relative(row["path"])
        if rel.as_posix().casefold() in seen:
            raise ValueError("Duplicate package path")
        seen.add(rel.as_posix().casefold())
        path = stage / rel
        regular_file(path, stage)
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError(f"Package hash mismatch: {rel}")
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()} - {"package.json"}
    if actual != {r["path"] for r in data["files"]}:
        raise ValueError("Undeclared package file")
    return data


def stage(args: argparse.Namespace) -> Path:
    private = args.private.resolve(strict=True)
    dist = args.dist.resolve(strict=True)
    blender = args.blender.resolve(strict=True)
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError("Release stage must be a new directory")
    if not (private / "vta_engine.py").is_file():
        raise ValueError("Private source checkout missing")
    if args.complete:
        require_committed_source(ROOT)
        require_committed_source(private)
        for name in ("__init__.py", "runtime.py"):
            if (ROOT / "vta_protocol" / name).read_bytes() != (private / "vta_protocol" / name).read_bytes():
                raise ValueError("Public/private protocol definitions differ")
    if any(destination.is_relative_to(root) for root in (ROOT, private, dist)):
        # Only the explicit ignored .build/ location inside a checkout is safe.
        if not destination.is_relative_to(ROOT / ".build") and not destination.is_relative_to(private / ".build"):
            raise ValueError("Stage inside a checkout must live under .build")
    public_commit, private_commit = git_commit(ROOT), git_commit(private)
    source = destination / "extension-source"
    stage_root = destination / "payload"
    try:
        source.mkdir(parents=True)
        stage_root.mkdir()
        copy_extension_source(source)
        build_extension(blender, source, stage_root / "blendcap.zip", destination / "blender-profile")
        release = copy_engine(dist, stage_root / "engine")
        copy_maintenance(stage_root / "maintenance")
        if args.complete:
            info_path = stage_root / "engine" / "build-info.json"
            if not info_path.is_file():
                raise ValueError("Complete release requires compiled build provenance")
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if (info.get("source_commit") != private_commit or
                    info.get("release_fingerprint") != release["fingerprint"]):
                raise ValueError("Compiled engine provenance differs from private source commit")
        if args.components:
            add_approved_components(args.components.resolve(strict=True), stage_root)
        if args.complete and not args.components:
            raise ValueError("A customer installer requires approved runtime/model component inventories")
        data = package_manifest(stage_root, release, public_commit, private_commit)
        (stage_root / "package.json").write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        verify_stage(stage_root)
        (destination / "build-report.json").write_text(json.dumps({
            "schema": "studiofree-build-report", "public_commit": public_commit,
            "private_commit": private_commit, "product_version": data["product_version"],
            "build_id": data["build_id"], "fingerprint": data["fingerprint"],
            "blender": str(blender), "blender_version": run(str(blender), "--version").splitlines()[0],
            "build_python": platform.python_version(), "complete": bool(args.components),
            "files": len(data["files"]), "bytes": sum(r["bytes"] for r in data["files"]),
        }, indent=2) + "\n", encoding="utf-8")
        return stage_root
    except Exception:
        # A failed build remains inspectable under the ignored .build directory.
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", type=Path, default=PRIVATE_DEFAULT)
    parser.add_argument("--dist", type=Path, help="Existing Nuitka vta_engine.dist")
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--components", type=Path, help="Explicit approved pinned component inventories")
    parser.add_argument("--complete", action="store_true", help="Require all customer runtime/model payloads")
    parser.add_argument("--verify", type=Path, help="Verify an existing payload directory instead of building")
    args = parser.parse_args()
    if args.verify:
        data = verify_stage(args.verify)
        print(f"Verified {len(data['files'])} package files")
        return
    if not all((args.dist, args.blender, args.output)):
        parser.error("--dist, --blender, and --output are required for staging")
    result = stage(args)
    print(f"Staged {result}")


if __name__ == "__main__":
    main()
