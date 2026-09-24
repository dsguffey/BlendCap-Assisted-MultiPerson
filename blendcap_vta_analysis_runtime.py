"""Resolve the existing offline CUDA SAM3 runtime for VTA Analyze.

The evaluated runtime is described by its installation manifests, not by an
absolute developer-machine path. This module does not install or load models.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys


class AnalysisRuntimeUnavailable(RuntimeError):
    """No verified local environment can run the evaluated SAM3 provider."""


@dataclass(frozen=True)
class AnalysisRuntime:
    python: Path
    source: Path
    checkpoint: Path
    origin: str


_PROBE = (
    "import sys; from pathlib import Path; "
    "source=Path(sys.argv[1]); "
    "sys.path.insert(0,str(source/'vendor'/'sam3-main')); "
    "import cv2, PIL, torch, sam3.model_builder; "
    "assert torch.cuda.is_available(), 'CUDA unavailable'"
)


def _within(root: Path, value: str) -> Path | None:
    """Accept only paths recorded relative to one evaluation root."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def _manifest_runtime(core_root: Path) -> AnalysisRuntime | None:
    """Read the already-frozen evaluated installation layout beside VTA core."""
    evaluation = core_root.resolve().parent / "Kinesia Evaluation"
    reports = evaluation / "reports"
    try:
        install = json.loads((reports / "installation_manifest.json").read_text(encoding="utf-8"))
        adapter = json.loads((reports / "phase2_pytorch_stream_detector_adapter_manifest.json")
                             .read_text(encoding="utf-8"))
        if (install.get("schema") != "kinesia-isolated-installation-manifest"
                or install.get("version") != 1
                or adapter.get("schema") != "kinesia-pytorch-stream-detector-adapter-manifest"
                or adapter.get("version") != 1
                or adapter["adapter"]["windows_backend"] != "pytorch-sam3"
                or adapter["adapter"]["device"] != "cuda"
                or install["source"]["commit"] != adapter["base_source"]["commit"]):
            return None
        environment = _within(evaluation, adapter["environment"]["path"])
        source = _within(evaluation, install["source"]["checkout"])
        checkpoint = _within(evaluation, adapter["checkpoint"]["path_within_evaluation"])
        if environment is None or source is None or checkpoint is None:
            return None
        executable = (environment / "Scripts" / "python.exe" if sys.platform == "win32"
                      else environment / "bin" / "python")
        return AnalysisRuntime(executable, source, checkpoint, "evaluated-installation-manifest")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _probe_python(python: Path, source: Path) -> bool:
    """Check imports and CUDA in the child Python without loading the model."""
    environment = os.environ.copy()
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONNOUSERSITE="1")
    try:
        result = subprocess.run(
            [str(python), "-c", _PROBE, str(source)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=45, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _usable(runtime: AnalysisRuntime, probe) -> bool:
    return (runtime.python.is_file() and runtime.checkpoint.is_file()
            and (runtime.source / "src" / "sam_3d_pose_estimation"
                 / "stream_detector.py").is_file()
            and (runtime.source / "vendor" / "sam3-main" / "sam3"
                 / "model_builder.py").is_file()
            and probe(runtime.python, runtime.source))


def resolve_analysis_runtime(
    core_root: Path,
    blendcap_python: Path,
    *,
    configured_python: str = "",
    configured_source: str = "",
    configured_checkpoint: str = "",
    probe=_probe_python,
) -> AnalysisRuntime:
    """Prefer the recorded evaluated runtime, then verified installed/fallback paths.

    The BlendCap interpreter is considered only if it passes the same SAM3
    import/CUDA probe; its mere existence does not make it a valid environment.
    """
    recorded = _manifest_runtime(Path(core_root))
    candidates: list[AnalysisRuntime] = []
    if recorded is not None:
        candidates.append(recorded)
    source = Path(configured_source).expanduser() if configured_source.strip() else (
        recorded.source if recorded is not None else None)
    checkpoint = (Path(configured_checkpoint).expanduser()
                  if configured_checkpoint.strip() else
                  (recorded.checkpoint if recorded is not None else None))
    if source is not None and checkpoint is not None:
        candidates.append(AnalysisRuntime(
            Path(blendcap_python), source, checkpoint, "blendcap-venv",
        ))
        if configured_python.strip():
            candidates.append(AnalysisRuntime(
                Path(configured_python).expanduser(), source, checkpoint,
                "user-preference",
            ))
    for candidate in candidates:
        if _usable(candidate, probe):
            return AnalysisRuntime(candidate.python.resolve(), candidate.source.resolve(),
                                   candidate.checkpoint.resolve(), candidate.origin)
    raise AnalysisRuntimeUnavailable(
        "Analysis environment unavailable. Open BlendCap Preferences → Video to Animation "
        "and configure the existing CUDA SAM3 runtime."
    )
