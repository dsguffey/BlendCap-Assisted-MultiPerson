"""Isolated, cache-only SAM3 analysis worker launched by Blender's VTA UI.

The private core owns source validation, decoding, provider translation and
checkpoints. This process never writes review keys or tracking revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


STATUS_NAME = "vta-analysis-status.json"
STOP_NAME = "vta-analysis-stop"
LOCK_NAME = "vta-analysis.lock"
PROMPT = "person"
CONFIDENCE = 0.5


class AnalysisLaunchError(RuntimeError):
    """A requested analysis cannot safely use the current project or runtime."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_range(start: int | None, end: int | None, first: int, last: int) -> tuple[int, int]:
    """None/None means the complete authoritative SourceReference coverage."""
    if start is None and end is None:
        return first, last
    if start is None or end is None:
        raise AnalysisLaunchError("Both range endpoints are required.")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)):
        raise AnalysisLaunchError("Range endpoints must be integer source frames.")
    if not first <= start <= end <= last:
        raise AnalysisLaunchError(f"Range must be within source frames {first}–{last}.")
    return start, end


def status_path(companion_root: Path) -> Path:
    return Path(companion_root) / "analysis" / STATUS_NAME


def write_status(companion_root: Path, payload: dict) -> None:
    """Durable observational status; never an identity or cache authority."""
    destination = status_path(companion_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
            prefix=f".{STATUS_NAME}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _source_for_video(project, source_path: Path):
    digest = sha256_file(source_path)
    matching = [item for item in project.to_dict()["sources"]
                if item.get("video_sha256") == digest]
    if len(matching) != 1:
        raise AnalysisLaunchError("Video SHA-256 does not uniquely match a project source.")
    return project.source_reference(matching[0]["source_id"])


def run_analysis(args: argparse.Namespace) -> dict:
    # This command is explicitly offline even when invoked outside Blender.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    core_root = Path(args.core_root).resolve()
    if not (core_root / "vta_core" / "__init__.py").is_file():
        raise AnalysisLaunchError("Configured private VTA core is unavailable.")
    if not (Path(args.kinesia_root).resolve() / "src" /
            "sam_3d_pose_estimation" / "stream_detector.py").is_file():
        raise AnalysisLaunchError("Configured evaluated SAM3 source is unavailable.")
    if not Path(args.checkpoint).resolve().is_file():
        raise AnalysisLaunchError("Configured offline SAM3 checkpoint is unavailable.")
    sys.path.insert(0, str(core_root))
    from vta_core.analysis_cache import AnalysisCache, load_analysis_cache, save_analysis_cache
    from vta_core.cache_evidence_promotion import (
        register_analysis_cache_revision, validate_analysis_cache_registration,
    )
    from vta_core.project_persistence import load_project_state, save_project_state
    from vta_core.providers.sam3_runtime import Sam3Runtime
    from vta_core.range_analysis import analyze_frame_range
    from vta_core.source_preflight import preflight_source
    from vta_core.whole_video_analysis import analyze_whole_video

    root = Path(args.companion_root).resolve()
    video = Path(args.video).resolve()
    project = load_project_state(root)
    source = _source_for_video(project, video)
    start, end = validate_range(args.start, args.end, source.start_frame, source.end_frame)
    # Validate the source before loading a multi-gigabyte model or changing the cache.
    validated_source = preflight_source(source, video)
    active = project.to_dict().get("active_analysis_revision_by_source", {}).get(source.source_id)
    cache = load_analysis_cache(root, active) if active else None
    if cache is not None:
        validate_analysis_cache_registration(project, cache, active)

    runtime = Sam3Runtime.for_evaluated_pytorch(
        Path(args.kinesia_root), Path(args.checkpoint), PROMPT, CONFIDENCE, "cuda"
    )
    base = {
        "schema": "blendcap-vta-analysis-status", "version": 1,
        "source_id": source.source_id, "start_frame": start, "end_frame": end,
        "source_video_sha256": source.video_sha256,
        "source_coverage": [source.start_frame, source.end_frame],
        "provider": "pytorch-sam3", "model": "sam3-image",
        "pid": os.getpid(), "newly_analyzed": 0, "skipped": 0,
        "completed_in_requested_range": 0,
    }
    stop = root / "analysis" / STOP_NAME
    stop.unlink(missing_ok=True)  # stale request from a completed earlier process
    try:
        write_status(root, {**base, "state": "analyzing"})
        provider = runtime.load()
        if cache is None:
            cache = AnalysisCache(f"sam3-{uuid.uuid4().hex}", source, provider.descriptor)
            register_analysis_cache_revision(project, cache)
            save_analysis_cache(cache, root)
            save_project_state(project, root)
        elif cache.provider != provider.descriptor:
            raise AnalysisLaunchError(
                "The active analysis revision uses different provider settings or weights. "
                "Analyze cannot replace it; use an explicit future Redetect workflow."
            )
        base["checkpoint_sha256"] = provider.descriptor.checkpoint_sha256

        def progress(snapshot):
            write_status(root, {
                **base, "state": snapshot.status.value,
                "revision_id": cache.revision_id,
                "current_frame": snapshot.current_frame,
                "newly_analyzed": snapshot.newly_analyzed_count,
                "skipped": snapshot.skipped_completed_count,
                "completed_in_requested_range": snapshot.completed_count,
            })

        if (args.start is None and args.end is None and source.start_frame == 0
                and source.end_frame == validated_source.frame_count - 1):
            # The core whole-video call additionally checks decoded coverage.
            result = analyze_whole_video(
                source, video, runtime, cache, root, on_progress=progress,
                should_stop=stop.exists,
                preflight=lambda _source, _path: validated_source,
            )
        else:
            result = analyze_frame_range(
                source, video, runtime, cache, root, start, end,
                on_progress=progress, should_stop=stop.exists,
                preflight=lambda _source, _path: validated_source,
            )
        completed = sum(cache.frame_result(frame) is not None for frame in range(start, end + 1))
        final = {
            **base, "state": "cancelled" if result.stopped else "complete",
            "revision_id": cache.revision_id,
            "newly_analyzed": result.newly_analyzed_count,
            "skipped": result.skipped_completed_count,
            "completed_in_requested_range": completed,
            "total_analyzed_frames": len(cache.analyzed_frames),
        }
        write_status(root, final)
        return final
    except Exception as exc:
        write_status(root, {**base, "state": "failed", "error": str(exc)})
        raise
    finally:
        runtime.unload()
        stop.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("core-root", "kinesia-root", "checkpoint", "companion-root", "video"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    args = parser.parse_args(argv)
    root = Path(args.companion_root).resolve()
    analysis = root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    lock = analysis / LOCK_NAME
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AnalysisLaunchError(
            "Another VTA analysis may be running. Inspect its status before removing the lock."
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        try:
            result = run_analysis(args)
        except Exception as exc:
            current = None
            try:
                current = json.loads(status_path(root).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if not isinstance(current, dict) or current.get("state") != "failed":
                write_status(root, {
                    "schema": "blendcap-vta-analysis-status", "version": 1,
                    "state": "failed", "error": str(exc), "pid": os.getpid(),
                })
            raise
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"VTA analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1)
