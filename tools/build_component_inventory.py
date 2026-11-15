"""Hash five portable components into an internal technical-test inventory.

This tool deliberately cannot issue redistribution approval. A reviewed
customer inventory must be prepared separately before a complete build.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .studiofree_release import COMPONENTS, regular_file, safe_relative, sha256
else:
    from studiofree_release import COMPONENTS, regular_file, safe_relative, sha256


ROUTES = {"control_python": "python.exe", "body_python": "Scripts/python.exe",
          "analysis_python": "Scripts/python.exe", "body_root": ".",
          "kinesia_root": ".", "sam3_checkpoint": "sam3.pt"}


def build(root: Path) -> dict:
    root = root.resolve(strict=True)
    result = {}
    for component in COMPONENTS:
        source = (root / component).resolve(strict=True)
        if source.parent != root or not source.is_dir():
            raise ValueError(f"Missing portable component: {component}")
        files = []
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            regular_file(path, source)
            rel = path.relative_to(source).as_posix()
            safe_relative(rel)
            if any(part in {"__pycache__", "tests", "test"} for part in path.parts) or path.suffix.lower() in {".pyc", ".pyo", ".pth", ".log", ".pdb"}:
                raise ValueError(f"Disposable file in component: {component}/{rel}")
            files.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
        if not files:
            raise ValueError(f"Empty component: {component}")
        result[component] = {"root": str(source), "files": files,
                             "redistribution": "internal-test-only"}
    for route, rel in ROUTES.items():
        owner = {"control_python": "control", "body_python": "body",
                 "analysis_python": "analysis", "body_root": "body",
                 "kinesia_root": "analysis_source", "sam3_checkpoint": "models"}[route]
        if not (root / owner / rel).exists():
            raise ValueError(f"Missing route target: {route}")
    return {"components": result, "routes": ROUTES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Inventory output must be new")
    result = build(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({key: len(value["files"]) for key, value in result["components"].items()}))


if __name__ == "__main__":
    main()
