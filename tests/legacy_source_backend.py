"""Frozen pre-M5 GPL source adapter, parity tests only."""
import importlib
from pathlib import Path
from typing import Callable
from blendcap_vta_review import *

class PrivateCoreBackend:
    """M5 source/camera compatibility adapter; production jobs/retarget use IPC."""

    def __init__(
        self,
        core: object | None = None,
        importer: Callable[[str], object] = importlib.import_module,
    ) -> None:
        if core is None:
            try:
                core = importer("vta_core")
            except (ImportError, ModuleNotFoundError) as exc:
                raise VtaReviewUiError(
                    "Private VTA core is unavailable in Blender's Python environment."
                ) from exc
        self._core = core
        self._source_digest_cache: dict[Path, tuple[int, int, str]] = {}

    def export_retarget_motion(self, location: ReviewLocation, **snapshot):
        """Return only the private-owned normalized final-motion contract."""
        project, source = self._load_project_source(location)
        return self._core.build_retarget_motion(project, source_id=source['source_id'], **snapshot)

    def _load_context(self, location: ReviewLocation) -> tuple[object, object, str]:
        project, source = self._load_project_source(location)
        active = project.to_dict().get("active_analysis_revision_by_source", {})
        revision_id = active.get(source["source_id"])
        if not isinstance(revision_id, str) or not revision_id:
            raise VtaReviewUiError("The active source has no active analysis revision.")
        try:
            cache = self._core.load_analysis_cache(location.companion_root, revision_id)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core") or isinstance(exc, OSError):
                raise VtaReviewUiError("The active analysis cache is missing or unavailable.") from exc
            raise
        return project, cache, revision_id

    def _load_project_source(self, location: ReviewLocation) -> tuple[object, dict]:
        try:
            project = self._core.load_project_state(location.companion_root)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core") or isinstance(exc, OSError):
                raise VtaReviewUiError("The VTA project state is missing or unavailable.") from exc
            raise
        source_hash = self._source_hash(location.source_path)
        raw = project.to_dict()
        sources = [
            item for item in raw.get("sources", [])
            if item.get("video_sha256") == source_hash
        ]
        if len(sources) != 1:
            raise VtaReviewUiError(
                "The active VSE source does not uniquely match the VTA project."
            )
        source = sources[0]
        if not source["start_frame"] <= location.source_frame <= source["end_frame"]:
            raise VtaReviewUiError("The mapped source frame is outside VTA source coverage.")
        return project, source

    def _source_hash(self, path: Path) -> str:
        try:
            stat = path.stat()
        except OSError as exc:
            raise VtaReviewUiError(f"Cannot inspect the active source video: {exc}") from exc
        resolved = path.resolve()
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._source_digest_cache.get(resolved)
        if cached is not None and cached[:2] == signature:
            return cached[2]
        digest = sha256_file(resolved)
        self._source_digest_cache[resolved] = (signature[0], signature[1], digest)
        return digest
