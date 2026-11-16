"""Per-user StudioFree MoCap install, update, repair, rollback and uninstall.

Called by the installer with a verified, complete release payload. This tool
only touches explicit installer-owned files. Project and entitlement roots are
never deletion targets. No shell commands or private source are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

if (Path(__file__).resolve().parents[1] / "vta_protocol").is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vta_protocol import VERSION as PROTOCOL  # noqa: E402
from vta_protocol.runtime import PRODUCT_VERSION as PRODUCT  # noqa: E402
COMPONENTS = ("engine", "control", "body", "analysis", "analysis_source", "models")
REUSE_MIN_BYTES = 64 * 1024 * 1024
ROUTE_OWNER = {"control_python": "control", "body_python": "body",
               "analysis_python": "analysis", "body_root": "body",
               "kinesia_root": "analysis_source", "sam3_checkpoint": "models"}


def safe_member(name: str) -> Path:
    if (not isinstance(name, str) or not name or len(name) > 512 or name.startswith("/")
            or "\\" in name or ":" in name or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("Unsafe package path")
    return Path(*name.split("/"))


def release_id(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value)
            or value.endswith(".") or value.split(".", 1)[0].casefold() in {"con", "prn", "aux", "nul"}
            or re.fullmatch(r"(?:com|lpt)[1-9](?:\..*)?", value, re.IGNORECASE)):
        raise ValueError("Invalid release ID")
    return value


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path, limit: int = 2_000_000) -> dict:
    if path.stat().st_size > limit:
        raise ValueError(f"Oversized metadata: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid metadata: {path.name}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def log_event(data_root: Path, operation: str, state: str, message: str = "") -> None:
    """Small support log; never records a key, entitlement, or environment dump."""
    folder = data_root / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "maintenance.jsonl"
    if path.is_file() and path.stat().st_size > 1_000_000:
        rotated = folder / "maintenance.previous.jsonl"
        rotated.unlink(missing_ok=True)
        os.replace(path, rotated)
    row = dict(at=datetime.now(timezone.utc).isoformat(), operation=operation,
               state=state, message=message[:300])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def regular(path: Path, root: Path) -> None:
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Package file is missing, linked, or outside its root")
    current = path.parent
    while current != root.parent:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Package directory link rejected")
        if current == root:
            break
        current = current.parent


def safe_destination(root: Path, rel: Path) -> Path:
    """Reject reparse points in every existing destination ancestor."""
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Linked release directory rejected")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Linked release member rejected")
    return current


def verify_payload(root: Path, *, complete: bool = True, allow_extra: bool = False,
                   allow_internal: bool = False) -> dict:
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Linked package root rejected")
    root = root.resolve(strict=True)
    # Portable AI wheels add tens of thousands of hashed members. The generic
    # metadata limit is too small for a complete release inventory.
    manifest = read_json(root / "package.json", 32_000_000)
    if (manifest.get("schema") != "studiofree-package" or manifest.get("version") != 1
            or manifest.get("product_version") != PRODUCT or manifest.get("protocol") != PROTOCOL):
        raise ValueError("Package version or protocol mismatch")
    if manifest.get("internal_test") and not allow_internal:
        raise ValueError("Internal technical-test payload cannot be installed")
    release_id(manifest.get("build_id"))
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 100_000:
        raise ValueError("Invalid package inventory")
    seen = set()
    for row in files:
        if set(row) != {"path", "bytes", "sha256", "component"}:
            raise ValueError("Invalid package file identity")
        name = row["path"]
        rel = safe_member(name)
        if name.casefold() in seen:
            raise ValueError("Duplicate package member")
        seen.add(name.casefold())
        path = root / rel
        regular(path, root)
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError(f"Package hash mismatch: {name}")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - {"package.json"}
    if not allow_extra and actual != {row["path"] for row in files}:
        raise ValueError("Undeclared file in package")
    release = read_json(root / "engine" / "release.json", 16384)
    if (release.get("fingerprint") != manifest.get("fingerprint") or
            release.get("build_id") != manifest["build_id"] or
            release.get("product_version") != PRODUCT or
            digest(root / "engine" / "vta_engine.exe") != release.get("executable_sha256")):
        raise ValueError("Private release identity mismatch")
    if complete:
        for component in COMPONENTS:
            if not any(name.startswith(component + "/") for name in seen):
                raise ValueError(f"Required component missing: {component}")
        if "runtime-routes.json" not in seen or "blendcap.zip" not in seen:
            raise ValueError("Runtime routes or extension archive missing")
        routes = read_json(root / "runtime-routes.json", 4096)
        if set(routes) != set(ROUTE_OWNER):
            raise ValueError("Runtime route mismatch")
        for name, value in routes.items():
            if value != ".":
                safe_member(value)
            owner = ROUTE_OWNER[name]
            path = root / owner / ("" if value == "." else value)
            if not path.exists():
                raise ValueError(f"Runtime route target missing: {name}")
    return manifest


def remove_owned(root: Path, manifest: dict) -> None:
    """Remove only inventoried files and now-empty directories."""
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Linked release directory rejected")
    for row in manifest["files"]:
        path = safe_destination(root, safe_member(row["path"]))
        path.unlink(missing_ok=True)
    (root / "package.json").unlink(missing_ok=True)
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise ValueError("Refusing linked directory during uninstall")
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def copy_release(payload: Path, staging: Path, manifest: dict, previous: Path | None,
                 *, allow_internal: bool = False) -> int:
    """Reuse identical large immutable model bytes without retaining prior paths.

    NTFS hardlinks are ref-counted, so uninstalling either release cannot remove
    the other's file. A failed link falls back to a copy. Final verification
    hashes every staged file before the release can become active.
    """
    prior = {}
    if previous is not None and previous.is_dir():
        try:
            previous_manifest = verify_payload(previous, allow_extra=True,
                                               allow_internal=allow_internal)
        except (OSError, ValueError, KeyError, TypeError):
            # An update must still be able to replace a damaged old release.
            # Never reuse bytes from a release that did not verify in full.
            previous = None
        else:
            prior = {row["path"]: row for row in previous_manifest["files"]}
    reused = 0
    staging.mkdir(parents=True)
    for row in manifest["files"]:
        rel = safe_member(row["path"])
        target = safe_destination(staging, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        old_row = prior.get(row["path"])
        if (previous is not None and rel.parts[0] in {"body", "models"}
                and row["bytes"] >= REUSE_MIN_BYTES and old_row is not None
                and old_row["bytes"] == row["bytes"] and old_row["sha256"] == row["sha256"]):
            source = safe_destination(previous, rel)
            try:
                os.link(source, target)
                reused += 1
                continue
            except OSError:
                pass
        shutil.copyfile(payload / rel, target)
    shutil.copyfile(payload / "package.json", staging / "package.json")
    return reused


class BlenderCLI:
    def __init__(self, exe: Path, *, env: dict | None = None, allow_missing: bool = False):
        self.exe = exe.resolve(strict=not allow_missing)
        self.env = dict(os.environ if env is None else env)

    def command(self, *arguments: str) -> str:
        result = subprocess.run([str(self.exe), "--command", "extension", *arguments],
                                env=self.env, text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"Blender extension command failed: {result.stderr[-1000:]}")
        return result.stdout

    def check_version(self) -> None:
        result = subprocess.run([str(self.exe), "--version"], env=self.env,
                                text=True, capture_output=True, timeout=30)
        if result.returncode or not result.stdout.startswith("Blender 5.2."):
            raise ValueError("StudioFree MoCap currently supports Blender 5.2.x only")

    def repository(self) -> Path:
        output = self.command("repo-list")
        match = re.search(r'(?m)^user_default:\s*\n(?:^[ \t].*\n)*?[ \t]+directory: "([^"]+)"', output)
        if not match:
            raise ValueError("Blender user_default extension repository is missing")
        path = Path(match.group(1)).resolve()
        if not path.is_absolute():
            raise ValueError("Blender repository is not a local absolute path")
        return path

    def install(self, archive: Path) -> Path:
        self.command("install-file", "-r", "user_default", "-e", str(archive))
        target = self.repository() / "blendcap"
        if not (target / "blender_manifest.toml").is_file():
            raise RuntimeError("Blender did not install the StudioFree extension")
        return target

    def remove(self) -> bool:
        if not self.exe.is_file():
            return False
        if (self.repository() / "blendcap").exists():
            self.command("remove", "blendcap")
        return True


class Lifecycle:
    def __init__(self, program: Path, data: Path, blender: BlenderCLI, *, smoke=None,
                 internal_validation: bool = False):
        self.program = program.resolve()
        self.data = data.resolve()
        if self.program == self.data or self.program.is_relative_to(self.data) or self.data.is_relative_to(self.program):
            raise ValueError("Program and user-state roots must be separate")
        self.blender = blender
        self.smoke = smoke or self._engine_smoke
        self.internal_validation = internal_validation
        self.record_path = self.program / "install-record.json"
        self.current_path = self.data / "current.json"
        self.runtime_path = self.data / "config" / "runtime.json"

    def record(self) -> dict:
        if not self.record_path.is_file():
            return {"schema": "studiofree-install-record", "version": 1, "releases": []}
        value = read_json(self.record_path)
        if value.get("schema") != "studiofree-install-record" or value.get("version") != 1 or not isinstance(value.get("releases"), list):
            raise ValueError("Invalid install record")
        for item in value["releases"]:
            release_id(item)
        return value

    def current(self) -> dict | None:
        if not self.current_path.is_file():
            return None
        value = read_json(self.current_path, 4096)
        if value.get("schema") != "studiofree-current" or value.get("version") != 1:
            raise ValueError("Invalid current-release record")
        release_id(value["active"])
        if value.get("previous") is not None:
            release_id(value["previous"])
        return value

    def release(self, identity: str) -> Path:
        return self.program / "releases" / release_id(identity)

    def _runtime(self, release: Path, adapter: Path) -> dict:
        routes = read_json(release / "runtime-routes.json", 4096)
        roots = {name: str(release / name) for name in COMPONENTS}
        roots["adapter"] = str(adapter)
        return dict(schema="vta-runtime", version=1, product_version=PRODUCT,
                    protocol=[PROTOCOL, PROTOCOL], mode="compiled", roots=roots, routes=routes)

    def _engine_smoke(self, release: Path) -> None:
        request = dict(protocol="vta-engine", version=PROTOCOL, kind="request",
                       request_id=uuid4().hex, operation="license-status", payload={})
        env = os.environ.copy()
        # A disposable lifecycle test never reads the owner's entitlement.
        env["LOCALAPPDATA"] = str(self.data.parent)
        result = subprocess.run([str(release / "engine" / "vta_engine.exe")],
                                input=json.dumps(request).encode(), capture_output=True,
                                env=env, timeout=30)
        lines = result.stdout.splitlines()
        if result.returncode or len(lines) < 2:
            raise RuntimeError("Compiled engine status smoke failed")
        ready, reply = (json.loads(line) for line in lines[:2])
        if (ready.get("kind") != "ready" or ready.get("version") != PROTOCOL or
                reply.get("kind") != "response" or reply.get("status") != "ok" or
                reply.get("operation") != "license-status"):
            raise RuntimeError("Compiled engine protocol/status smoke failed")

    def install(self, payload: Path, *, fail_after: str = "") -> str:
        self.blender.check_version()
        manifest = verify_payload(payload, allow_internal=self.internal_validation)
        identity = manifest["build_id"]
        old = self.current()
        if old and Path(old["blender"]).resolve() != self.blender.exe:
            raise ValueError("Update must use the Blender installation selected for the active release")
        if old is None and (self.blender.repository() / "blendcap").exists():
            raise ValueError("An unmanaged StudioFree extension already exists; choose a clean Blender profile or migrate it first")
        destination = self.release(identity)
        if destination.exists():
            verify_payload(destination, allow_extra=True,
                           allow_internal=self.internal_validation)
        else:
            staging = self.program / "staging" / uuid4().hex
            staging.parent.mkdir(parents=True, exist_ok=True)
            try:
                prior_root = self.release(old["active"]) if old else None
                copy_release(payload, staging, manifest, prior_root,
                             allow_internal=self.internal_validation)
                verify_payload(staging, allow_internal=self.internal_validation)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    remove_owned(staging, manifest)
        record = self.record()
        if identity not in record["releases"]:
            record["releases"].append(identity)
            atomic_json(self.record_path, record)
        if fail_after == "stage":
            raise RuntimeError("Injected failure after stage")
        self.smoke(destination)
        if fail_after == "smoke":
            raise RuntimeError("Injected engine smoke failure")
        previous_zip = self.release(old["active"]) / "blendcap.zip" if old else None
        try:
            adapter = self.blender.install(destination / "blendcap.zip")
            if fail_after == "extension":
                raise RuntimeError("Injected extension install failure")
            runtime = self._runtime(destination, adapter)
            from zipfile import ZipFile
            with ZipFile(destination / "blendcap.zip") as archive:
                manifest_text = archive.read("blender_manifest.toml").decode("utf-8")
            if f'version = "{PRODUCT}"' not in manifest_text:
                raise ValueError("Extension version differs from runtime")
            if fail_after == "switch":
                raise RuntimeError("Injected failure before active switch")
            # Runtime config is written first while Blender is closed. On crash
            # before current.json, the previous pointer remains and repair can
            # reselect it. The installer must require Blender to be closed.
            prior_runtime = self.runtime_path.read_bytes() if self.runtime_path.is_file() else None
            try:
                atomic_json(self.runtime_path, runtime)
                atomic_json(self.current_path, dict(schema="studiofree-current", version=1,
                                                    active=identity,
                                                    previous=(old["active"] if old and old["active"] != identity else old.get("previous") if old else None),
                                                    blender=str(self.blender.exe), adapter=str(adapter)))
            except Exception:
                if prior_runtime is None:
                    self.runtime_path.unlink(missing_ok=True)
                else:
                    temp = self.runtime_path.with_suffix(".restore")
                    temp.write_bytes(prior_runtime)
                    os.replace(temp, self.runtime_path)
                raise
            return identity
        except Exception:
            if previous_zip and previous_zip.is_file():
                self.blender.install(previous_zip)
            elif old is None:
                try:
                    self.blender.remove()
                except Exception:
                    pass
            raise

    def repair(self, payload: Path) -> list[str]:
        self.blender.check_version()
        manifest = verify_payload(payload, allow_internal=self.internal_validation)
        current = self.current()
        if current is None or manifest["build_id"] != current["active"]:
            raise ValueError("Repair requires the matching active full installer")
        if Path(current["blender"]).resolve() != self.blender.exe:
            raise ValueError("Repair must use the Blender installation selected for the active release")
        destination = self.release(current["active"])
        changed = []
        for row in manifest["files"]:
            rel = safe_member(row["path"])
            target = safe_destination(destination, rel)
            if not target.is_file() or target.stat().st_size != row["bytes"] or digest(target) != row["sha256"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
                try:
                    shutil.copyfile(payload / rel, temporary)
                    if digest(temporary) != row["sha256"]:
                        raise ValueError("Repair copy hash mismatch")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                changed.append(row["path"])
        if not (destination / "package.json").is_file():
            shutil.copyfile(payload / "package.json", destination / "package.json")
        verify_payload(destination, allow_extra=True,
                       allow_internal=self.internal_validation)
        self.smoke(destination)
        adapter = self.blender.install(destination / "blendcap.zip")
        atomic_json(self.runtime_path, self._runtime(destination, adapter))
        return changed

    def rollback(self) -> str:
        current = self.current()
        if current is None or current["previous"] is None:
            raise ValueError("No retained previous release")
        if Path(current["blender"]).resolve() != self.blender.exe:
            raise ValueError("Rollback must use the Blender installation selected for the active release")
        previous = self.release(current["previous"])
        verify_payload(previous, allow_extra=True,
                       allow_internal=self.internal_validation)
        self.smoke(previous)
        old = self.release(current["active"])
        try:
            adapter = self.blender.install(previous / "blendcap.zip")
            atomic_json(self.runtime_path, self._runtime(previous, adapter))
            atomic_json(self.current_path, dict(current, active=current["previous"], previous=current["active"], adapter=str(adapter)))
        except Exception:
            if (old / "blendcap.zip").is_file():
                self.blender.install(old / "blendcap.zip")
            raise
        return current["previous"]

    def uninstall(self) -> None:
        current = self.current()
        record = self.record()
        if current:
            if not self.blender.remove():
                log_event(self.data, "uninstall", "extension-removal-deferred",
                          "Blender is unavailable; application files will be removed")
            # Only remove config if its engine root belongs to our recorded release.
            if self.runtime_path.is_file():
                value = read_json(self.runtime_path, 32768)
                engine = value.get("roots", {}).get("engine")
                if engine == str(self.release(current["active"]) / "engine"):
                    self.runtime_path.unlink()
            self.current_path.unlink(missing_ok=True)
        for identity in record["releases"]:
            root = self.release(identity)
            if not root.exists():
                continue
            manifest = read_json(root / "package.json")
            remove_owned(root, manifest)
        self.record_path.unlink(missing_ok=True)
        # License, settings, logs, model caches and project roots are retained.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "install", "repair", "rollback", "uninstall"))
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--program-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--internal-validation", action="store_true",
                        help="Explicitly permit a marked internal-test payload in an isolated install")
    args = parser.parse_args()
    if args.operation in ("apply", "install", "repair") and args.payload is None:
        parser.error("install/repair requires --payload")
    if args.blender is None:
        current_path = args.data_root / "current.json"
        if not current_path.is_file() and args.operation != "uninstall":
            parser.error("--blender is required without an existing installation")
        if current_path.is_file():
            args.blender = Path(read_json(current_path, 4096)["blender"])
    cli = BlenderCLI(args.blender, allow_missing=args.operation == "uninstall") if args.blender else None
    lifecycle = Lifecycle(args.program_root, args.data_root, cli,
                          internal_validation=args.internal_validation)
    try:
        log_event(args.data_root, args.operation, "started")
        if args.operation == "apply":
            incoming = verify_payload(args.payload, allow_internal=args.internal_validation)
            if args.internal_validation and not incoming.get("internal_test"):
                raise ValueError("Internal validation requires an internal-test payload")
            current = lifecycle.current()
            if current and current["active"] == incoming["build_id"]:
                print("Repaired", len(lifecycle.repair(args.payload)), "files")
            else:
                print("Installed", lifecycle.install(args.payload))
        elif args.operation == "install":
            print("Installed", lifecycle.install(args.payload))
        elif args.operation == "repair":
            print("Repaired", len(lifecycle.repair(args.payload)), "files")
        elif args.operation == "rollback":
            print("Rolled back to", lifecycle.rollback())
        else:
            lifecycle.uninstall()
            print("StudioFree MoCap application files removed; entitlement and projects preserved")
        log_event(args.data_root, args.operation, "succeeded")
    except Exception as exc:
        log_event(args.data_root, args.operation, "failed", str(exc))
        print(f"StudioFree maintenance failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
