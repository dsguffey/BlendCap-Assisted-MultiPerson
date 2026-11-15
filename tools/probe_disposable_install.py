"""Run a real Blender CLI/compiled-engine maintenance probe in an isolated profile.

The five external dependency components are tiny placeholders. This proves the
transaction and CLI wiring only, not full AI inference or clean-machine setup.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from studiofree_maintenance import BlenderCLI, Lifecycle, verify_payload
from studiofree_release import package_manifest


def write(path: Path, data: bytes = b"fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="Verified core stage with real compiled engine")
    parser.add_argument("blender", type=Path)
    args = parser.parse_args()
    source = args.payload.resolve(strict=True)
    report = json.loads((source.parent / "build-report.json").read_text())
    if report["complete"]:
        raise ValueError("This probe is only for a core stage with synthetic dependencies")
    scratch = Path(__file__).resolve().parents[1] / ".build" / "test-temp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="installer-cli-", dir=scratch) as folder:
        root = Path(folder)
        payload = root / "payload"
        shutil.copytree(source, payload)
        for component, name in (("control", "python.exe"), ("body", "venv/Scripts/python.exe"),
                                ("analysis", "Scripts/python.exe"),
                                ("analysis_source", "src/sam_3d_pose_estimation/stream_detector.py"),
                                ("models", "sam3.pt")):
            write(payload / component / name)
        write(payload / "body" / "save_mhr_data.py")
        from vta_protocol.runtime import BODY_REQUIRED_CODE
        for name in BODY_REQUIRED_CODE:
            write(payload / "body" / name)
        for name in ("checkpoints/sam-3d-body-dinov3/model.ckpt",
                     "checkpoints/sam-3d-body-dinov3/model_config.yaml",
                     "checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt",
                     "checkpoints/moge-2-vits-normal/model.pt"):
            write(payload / "body" / name)
        routes = dict(control_python="python.exe", body_python="venv/Scripts/python.exe",
                      analysis_python="Scripts/python.exe", body_root=".", kinesia_root=".",
                      sam3_checkpoint="sam3.pt")
        write(payload / "runtime-routes.json", json.dumps(routes).encode())
        private_release = json.loads((payload / "engine" / "release.json").read_text())
        (payload / "package.json").unlink()
        manifest = package_manifest(payload, private_release, report["public_commit"], report["private_commit"])
        write(payload / "package.json", json.dumps(manifest, sort_keys=True).encode())
        verify_payload(payload)
        extension_root = root / "blender" / "extensions"
        config_root = root / "blender" / "config"
        extension_root.mkdir(parents=True)
        config_root.mkdir()
        env = dict(__import__("os").environ,
                   BLENDER_USER_EXTENSIONS=str(extension_root),
                   BLENDER_USER_CONFIG=str(config_root),
                   LOCALAPPDATA=str(root / "local"))
        blender = BlenderCLI(args.blender, env=env)
        program = root / "program"
        data = root / "local" / "VTA"
        lifecycle = Lifecycle(program, data, blender)
        project = root / "owner" / "Project.blend"
        license_path = data / "license" / "entitlement.dpapi"
        unrelated = extension_root / "user_default" / "unrelated" / "owner.txt"
        for path in (project, license_path, unrelated):
            write(path, path.name.encode())
        identity = lifecycle.install(payload)
        from vta_protocol.runtime import resolve
        runtime = json.loads(lifecycle.runtime_path.read_text())
        resolved = resolve(runtime)
        if resolved["execution_mode"] != "compiled":
            raise AssertionError("Installed adapter did not resolve compiled runtime")
        target = lifecycle.release(identity) / "body" / "save_mhr_data.py"
        target.write_bytes(b"corrupt")
        restored = lifecycle.repair(payload)
        if restored != ["body/save_mhr_data.py"]:
            raise AssertionError(f"Repair did not restore the expected file: {restored}")
        lifecycle.uninstall()
        if lifecycle.current() is not None or lifecycle.runtime_path.exists():
            raise AssertionError("Uninstall left an active runtime")
        for path in (project, license_path, unrelated):
            if path.read_bytes() != path.name.encode():
                raise AssertionError(f"Uninstall changed an unrelated/user file: {path}")
        print("Disposable real Blender CLI + compiled engine install/repair/uninstall probe passed")


if __name__ == "__main__":
    main()
