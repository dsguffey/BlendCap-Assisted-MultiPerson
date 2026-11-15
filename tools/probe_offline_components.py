"""Fail a release build if staged Python runtimes use developer installations.

This is a build-time import closure probe, not a GPU inference or clean-machine
test. It never downloads models or uses the owner's activation state.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

if __package__:
    from .studiofree_maintenance import verify_payload
else:
    from studiofree_maintenance import verify_payload


PROBE = r'''
import importlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve(strict=True)
role = sys.argv[2]
sources = Path(sys.argv[3]).resolve(strict=True)
executable = Path(sys.executable).resolve(strict=True)
base = Path(sys.base_prefix).resolve(strict=True)
if not executable.is_relative_to(root) or not base.is_relative_to(root):
    raise RuntimeError("Python executable/base escapes its packaged component")
for member in sys.path:
    if member and Path(member).exists() and not Path(member).resolve().is_relative_to(root):
        raise RuntimeError("Python import path escapes its packaged component: " + member)
modules = {"control": ("ssl", "ctypes"),
           "body": ("torch", "torchvision", "cv2", "numpy", "scipy", "PIL"),
           "analysis": ("torch", "torchvision", "cv2", "numpy", "PIL", "pkg_resources", "huggingface_hub")}
for name in modules[role]:
    importlib.import_module(name)
if role == "body":
    sys.path.insert(0, str(root))
    importlib.import_module("save_mhr_data")
elif role == "analysis":
    sys.path.insert(0, str(sources / "src"))
    sys.path.insert(0, str(sources / "vendor" / "sam3-main"))
    importlib.import_module("sam_3d_pose_estimation.stream_detector")
    importlib.import_module("sam3.model_builder")
print("STUDIOFREE_OFFLINE_PROBE=" + json.dumps({"role": role, "python": sys.version.split()[0],
      "executable": str(executable), "base": str(base)}, sort_keys=True))
'''


def probe(payload: Path) -> list[dict]:
    payload = payload.resolve(strict=True)
    verify_payload(payload, complete=True)
    routes = json.loads((payload / "runtime-routes.json").read_text(encoding="utf-8"))
    reports = []
    for role in ("control", "body", "analysis"):
        executable = payload / role / routes[role + "_python"]
        root = payload / role
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "TORCH_HOME", "HF_HOME"):
            environment.pop(name, None)
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
        environment["PATH"] = os.pathsep.join((str(executable.parent), str(root),
                                                 str(system_root / "System32")))
        result = subprocess.run((str(executable), "-I", "-c", PROBE,
                                 str(root), role, str(payload / "analysis_source")),
                                cwd=payload, env=environment, capture_output=True,
                                text=True, timeout=180, check=False)
        marker = "STUDIOFREE_OFFLINE_PROBE="
        lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
        if result.returncode or len(lines) != 1:
            raise RuntimeError(f"Packaged {role} Python import closure failed: "
                               + (result.stderr or result.stdout)[-2000:])
        reports.append(json.loads(lines[0][len(marker):]))
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.payload), indent=2))


if __name__ == "__main__":
    main()
