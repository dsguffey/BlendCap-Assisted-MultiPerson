"""Discover and validate the separately installed private VTA Core runtime.

This public module owns the one process-local import boundary used by the
Blender shell.  It never copies, packages, or implements the private core.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib
import os
from pathlib import Path
import sys
from typing import Callable, MutableSequence


DEVELOPMENT_ROOT = Path(r"C:\Design\AI\Projects\Video to Animation\Video-to-Animation")
REQUIRED_API = (
    "load_project_state",
    "load_analysis_cache",
    "build_frame_review",
    "build_review_decision_request",
    "apply_review_decision_and_persist",
    "create_character_and_persist",
)


class VtaCoreStatus(str, Enum):
    AVAILABLE = "available"
    NOT_CONFIGURED = "not-configured"
    CONFIGURED_PATH_MISSING = "configured-path-missing"
    CONFIGURED_PATH_INVALID = "configured-path-invalid"
    IMPORT_FAILED = "import-failed"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class VtaCoreResolution:
    status: VtaCoreStatus
    message: str
    root: Path | None = None
    core: object | None = None

    @property
    def available(self) -> bool:
        return self.status is VtaCoreStatus.AVAILABLE and self.core is not None


def default_roots(environ: dict[str, str] | None = None) -> tuple[Path, ...]:
    """Return deterministic product defaults followed by a dev convenience."""
    values = os.environ if environ is None else environ
    result: list[Path] = []
    local = values.get("LOCALAPPDATA")
    if local:
        result.append(Path(local) / "Programs" / "VTA")
    program_files = values.get("ProgramFiles")
    if program_files:
        result.append(Path(program_files) / "VTA")
    result.append(DEVELOPMENT_ROOT)
    return tuple(result)


def discover_vta_core(
    configured_location: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
    roots: tuple[Path, ...] | None = None,
    importer: Callable[[str], object] = importlib.import_module,
    sys_path: MutableSequence[str] | None = None,
) -> VtaCoreResolution:
    """Resolve, validate, and import the private runtime for this process.

    A non-empty saved preference is authoritative: if it later moves or becomes
    invalid, the user sees that state rather than silently switching projects.
    """
    configured = _configured_path(configured_location)
    if configured is not None:
        return _resolve_root(configured, configured=True, importer=importer, sys_path=sys_path)
    for root in roots if roots is not None else default_roots(environ):
        result = _resolve_root(root, configured=False, importer=importer, sys_path=sys_path)
        if result.available:
            return result
    return VtaCoreResolution(
        VtaCoreStatus.NOT_CONFIGURED,
        "Video to Animation Core is not configured. Choose its installation folder.",
    )


def _configured_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    return Path(text) if text else None


def _resolve_root(
    root: Path,
    *,
    configured: bool,
    importer: Callable[[str], object],
    sys_path: MutableSequence[str] | None,
) -> VtaCoreResolution:
    root = Path(root)
    if not root.is_dir():
        if configured:
            return VtaCoreResolution(
                VtaCoreStatus.CONFIGURED_PATH_MISSING,
                "Video to Animation Core is unavailable. The configured location could not be found.",
                root,
            )
        return VtaCoreResolution(VtaCoreStatus.NOT_CONFIGURED, "", root)
    package = root / "vta_core" / "__init__.py"
    if not package.is_file():
        if configured:
            return VtaCoreResolution(
                VtaCoreStatus.CONFIGURED_PATH_INVALID,
                "The selected folder is not a valid Video to Animation installation.",
                root,
            )
        return VtaCoreResolution(VtaCoreStatus.NOT_CONFIGURED, "", root)
    normalized_root = str(root.resolve())
    target_path = sys.path if sys_path is None else sys_path
    _insert_path_once(target_path, normalized_root)
    try:
        core = importer("vta_core")
    except Exception as exc:
        return VtaCoreResolution(
            VtaCoreStatus.IMPORT_FAILED,
            "Video to Animation Core could not be loaded from the selected location.",
            root,
        )
    module_file = getattr(core, "__file__", None)
    try:
        module_path = Path(module_file).resolve()
    except (TypeError, OSError):
        module_path = None
    if module_path != package.resolve():
        return VtaCoreResolution(
            VtaCoreStatus.INCOMPATIBLE,
            "A different Video to Animation Core is already loaded in this Blender session.",
            root,
        )
    missing = [name for name in REQUIRED_API if not callable(getattr(core, name, None))]
    if missing:
        return VtaCoreResolution(
            VtaCoreStatus.INCOMPATIBLE,
            "Video to Animation Core is incompatible with this BlendCap extension.",
            root,
        )
    return VtaCoreResolution(
        VtaCoreStatus.AVAILABLE,
        "Video to Animation Core is available.",
        root,
        core,
    )


def _insert_path_once(target: MutableSequence[str], root: str) -> None:
    normalized = os.path.normcase(os.path.normpath(root))
    if any(
        os.path.normcase(os.path.normpath(str(item))) == normalized
        for item in target
        if isinstance(item, str)
    ):
        return
    target.insert(0, root)
