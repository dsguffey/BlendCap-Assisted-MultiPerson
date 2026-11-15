"""Copy the reviewed offline AI source/weights into portable component roots.

This creates internal technical inputs, not redistribution approval. Source
files are selected from explicit production trees; model bytes are checked
against the recorded identities before and after copying.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


WEIGHTS = {
    "body/checkpoints/sam-3d-body-dinov3/model.ckpt": "b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf",
    "body/checkpoints/sam-3d-body-dinov3/model_config.yaml": "1012fc3f39cb5e90e3f8fbadf7bded31604bfafdce0321d17a7c1a2d3f08b88d",
    "body/checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt": "352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc",
    "body/checkpoints/moge-2-vits-normal/model.pt": "79a16621928c2bf0ed04659218c55c01075e950507f40bb3332fb4c873d3e1dc",
    "models/sam3.pt": "9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e",
}
SOURCE_EXTENSIONS = {".py", ".json", ".yaml", ".yml", ".txt", ".md", ".npz", ".gz"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def candidates(public: Path, analysis: Path, installed_body: Path, sam3: Path):
    for name in ("save_mhr_data.py", "npz_to_bvh.py"):
        yield public / name, "body/" + name
    for file in sorted((public / "pipeline").rglob("*")):
        if file.is_file() and file.suffix.lower() in SOURCE_EXTENSIONS and "__pycache__" not in file.parts:
            yield file, "body/" + file.relative_to(public).as_posix()
    for file in sorted((public / "models" / "fast-sam-3d-body").rglob("*")):
        if file.is_file() and file.suffix.lower() in SOURCE_EXTENSIONS and not any(
                part in {"tests", "test", "examples", "__pycache__"} for part in file.parts):
            yield file, "body/" + file.relative_to(public).as_posix()
    dino = public / "models" / "dinov3_hub" / "facebookresearch_dinov3_main"
    for file in sorted(dino.rglob("*")):
        rel = file.relative_to(dino)
        if (file.is_file() and file.suffix.lower() in SOURCE_EXTENSIONS and
                rel.parts[0] in {"dinov3", "hubconf.py", "LICENSE.md"} and
                not any(part in {"train", "run", "tests", "test", "__pycache__"} for part in rel.parts)):
            yield file, "body/" + file.relative_to(public).as_posix()
    for name in ("LICENSE",):
        yield analysis / name, "analysis_source/" + name
    for file in sorted((analysis / "src" / "sam_3d_pose_estimation").rglob("*")):
        if file.is_file() and file.suffix.lower() in SOURCE_EXTENSIONS and "__pycache__" not in file.parts:
            yield file, "analysis_source/" + file.relative_to(analysis).as_posix()
    vendor = analysis / "vendor" / "sam3-main"
    yield vendor / "LICENSE", "analysis_source/vendor/sam3-main/LICENSE"
    sam = vendor / "sam3"
    for file in sorted(sam.rglob("*")):
        rel = file.relative_to(sam)
        if (file.is_file() and file.suffix.lower() in SOURCE_EXTENSIONS and
                rel.parts[0] in {"__init__.py", "logger.py", "model_builder.py", "model", "sam", "assets", "train", "perflib"} and
                (rel.parts[0] != "train" or file.suffix.lower() == ".py") and
                not any(part in {"__pycache__", "tests", "test"} for part in rel.parts)):
            yield file, "analysis_source/" + file.relative_to(analysis).as_posix()
    for name in sorted(WEIGHTS):
        if name.startswith("body/"):
            yield installed_body / name.removeprefix("body/"), name
        else:
            yield sam3, name


def build(public: Path, analysis: Path, installed_body: Path, sam3: Path,
          output: Path, lock: Path | None) -> dict:
    public, analysis, installed_body, sam3 = (p.resolve(strict=True) for p in
                                                (public, analysis, installed_body, sam3))
    output = output.resolve()
    if not output.is_dir() or not (output / "body" / "Scripts" / "python.exe").is_file():
        raise ValueError("Existing portable body runtime is required")
    expected = json.loads(lock.read_text(encoding="utf-8")) if lock else None
    if expected and (expected.get("schema") != "studiofree-portable-assets-lock" or
                     expected.get("version") != 1):
        raise ValueError("Invalid AI asset lock")
    files = []
    selected = list(candidates(public, analysis, installed_body, sam3))
    for source, name in selected:
        if (not source.is_file() or source.is_symlink() or
                not any(source.is_relative_to(root) for root in (public, analysis, installed_body, sam3.parent))):
            raise ValueError(f"Missing, linked, or escaping asset: {source}")
        actual = sha256(source)
        if name in WEIGHTS and actual != WEIGHTS[name]:
            raise ValueError(f"Model asset identity mismatch: {name}")
        item = {"path": name, "bytes": source.stat().st_size, "sha256": actual}
        files.append(item)
        if expected and item not in expected["files"]:
            raise ValueError(f"Source asset differs from pinned lock: {name}")
    if expected and sorted(files, key=lambda x: x["path"]) != sorted(expected["files"], key=lambda x: x["path"]):
        raise ValueError("Asset lock file set differs from production closure")
    for (source, name), item in zip(selected, files, strict=True):
        target = output / name
        if target.exists():
            if sha256(target) != item["sha256"]:
                raise FileExistsError(f"Asset target differs from pinned source: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(target) != item["sha256"]:
            raise RuntimeError(f"Asset copy failed integrity check: {name}")
    return {"schema": "studiofree-portable-assets-build", "files": files,
            "source_roots": {"public": str(public), "analysis": str(analysis),
                             "installed_body": str(installed_body), "sam3": str(sam3)},
            "bytes": sum(item["bytes"] for item in files)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--analysis-source", type=Path, required=True)
    parser.add_argument("--installed-body", type=Path, required=True)
    parser.add_argument("--sam3-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.public, args.analysis_source, args.installed_body,
                   args.sam3_checkpoint, args.output, args.lock)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(result["files"]), "bytes": result["bytes"]}))


if __name__ == "__main__":
    main()
