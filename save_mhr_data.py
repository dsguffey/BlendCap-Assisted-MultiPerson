# save_mhr_data.py
# ================
# Reviewed body model support using Fast-SAM-3D-Body
# (USC PSI, arxiv 2603.15603). Model-loading support for the reviewed-box
# reconstruction worker; person discovery belongs to VTA Analyze.

import os

# Apple Silicon: allow torch ops not implemented on MPS to fall back to CPU
# silently instead of erroring. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# ── Offline operation ─────────────────────────────────────────────
# BlendCap runs fully offline after install. Force the model libraries into
# offline mode so they NEVER make a network round-trip during reconstruction:
#   - HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE: no Hugging Face HEAD requests
#     (MoGe weights load from the local cache only).
#   - HF_HUB_DISABLE_TELEMETRY: no usage pings.
# All weights are bundled locally by the installer; the DINOv3 repo loads via
# torch.hub source="local" (see backbones/dinov3.py). Must be set BEFORE torch
# / huggingface_hub import (several read these at import time).
# setdefault so a developer can still override to allow a deliberate download.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# ── Fast pipeline env vars (must be set BEFORE importing sam_3d_body) ──
# Conservative first-test settings: pure PyTorch path, torch.compile OFF
# (Windows-safety), all the layer-pruning and small-MoGe wins enabled.
os.environ.setdefault("LAYER_DTYPE", "fp32")            # FP32 (multi-person safe)
os.environ.setdefault("SKIP_KEYPOINT_PROMPT", "1")      # Skip kp-prompt encoding
os.environ.setdefault("USE_COMPILE", "0")               # torch.compile OFF for v1
os.environ.setdefault("USE_COMPILE_BACKBONE", "0")
os.environ.setdefault("DECODER_COMPILE", "0")
os.environ.setdefault("BODY_INTERM_PRED_LAYERS", "0,1,2")
os.environ.setdefault("HAND_INTERM_PRED_LAYERS", "0,1")
os.environ.setdefault("MHR_NO_CORRECTIVES", "1")        # Skip correctives
os.environ.setdefault("FOV_FAST", "1")                  # Fast MoGe mode
os.environ.setdefault("FOV_MODEL", "s")                 # Small MoGe (35M, was 331M)
os.environ.setdefault("FOV_LEVEL", "0")                 # Low MoGe resolution
os.environ.setdefault("GPU_HAND_PREP", "1")             # GPU hand preprocessing

import sys
import types
from pathlib import Path

import torch

# ── CUDA-stub patches for CPU-only torch builds ───────────────────
# Fast-SAM-3D-Body has ~50 torch.cuda.synchronize() / empty_cache()
# calls scattered through its source for timing/profiling instrumentation.
# On a torch-cpu build these raise "Torch not compiled with CUDA enabled"
# even though they're not doing useful work. Stub them to no-ops on CPU
# so the pipeline runs on machines without NVIDIA hardware. Untouched
# on CUDA — only patches when torch.cuda is actually unavailable.
if not torch.cuda.is_available():
    _cuda_noop = lambda *args, **kwargs: None
    torch.cuda.synchronize = _cuda_noop
    torch.cuda.empty_cache = _cuda_noop

# ── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.absolute()
CKPT_DIR = SCRIPT_DIR / "checkpoints"
os.environ["TORCH_HOME"] = str(CKPT_DIR / "torch")
os.environ["HF_HOME"] = str(CKPT_DIR / "huggingface")

# Point the vendored DINOv3 backbone loader at the repo we ship INSIDE the
# addon (architecture code only, ~1 MB) so it loads offline via torch.hub
# source="local" — see models/fast-sam-3d-body/.../backbones/dinov3.py. And
# hand the MoGe loader the checkpoints dir so it finds the pre-fetched local
# model.pt instead of pulling from Hugging Face. Both setdefault for override.
os.environ.setdefault(
    "DINOV3_HUB_DIR",
    str(SCRIPT_DIR / "models" / "dinov3_hub" / "facebookresearch_dinov3_main"),
)
os.environ.setdefault("BLENDCAP_MOGE_BASE", str(CKPT_DIR))

# Use the vendored Fast-SAM-3D-Body library in models/fast-sam-3d-body/.
# Self-contained — works wherever the script lives, including after the
# user moves the install dir.
FAST_DIR = SCRIPT_DIR / "models" / "fast-sam-3d-body"
if not FAST_DIR.exists():
    print(f"ERROR: Fast-SAM-3D-Body vendored library not found at {FAST_DIR}")
    print("       The addon install looks incomplete - reinstall BlendCap.")
    sys.exit(1)
sys.path.insert(0, str(FAST_DIR))

# Mock pyrender + the visualization.renderer module — we don't render,
# we save NPZ, and importing the real renderer would drag in heavy
# display dependencies the venv doesn't ship.
if "pyrender" not in sys.modules:
    _pr = types.ModuleType("pyrender")
    _pr.Node = type("Node", (), {})
    sys.modules["pyrender"] = _pr

_rkey = "sam_3d_body.visualization.renderer"
if _rkey not in sys.modules:
    _mod = types.ModuleType(_rkey)
    _mod.Renderer = type("Renderer", (), {})
    sys.modules[_rkey] = _mod

# pkg_resources stub for Python 3.12+
try:
    import pkg_resources
except ImportError:
    try:
        __import__("setuptools"); import pkg_resources
    except Exception:
        _pkg = types.ModuleType("pkg_resources")
        _pkg.DistributionNotFound = type("DistributionNotFound", (Exception,), {})
        _pkg.get_distribution = lambda x: None
        sys.modules["pkg_resources"] = _pkg




def emit_progress(pct, msg=""):
    print(f"[PROGRESS {int(pct)}] {msg}", flush=True)




def _get_device():
    """Pick the best torch device for the TORCH side of the pipeline.

    BLENDCAP_FORCE_CPU=1 overrides everything -> "cpu" (CPU-only inference
    backend; the addon injects it into the subprocess env - see
    the reconstruction worker). Otherwise priority is CUDA > (DirectML detected -> CPU
    hybrid) > MPS > CPU. On a DirectML box we deliberately return "cpu", NOT
    torch_directml.device(): torch-directml lacks the dynamic-shape ops the
    decoder/heads need, so torch runs on CPU while the heavy DINOv3
    backbone stays on the GPU via ONNX Runtime (USE_ORT, which picks
    DmlExecutionProvider on its own). Returns a string in every case.
    """
    if os.environ.get("BLENDCAP_FORCE_CPU", "0") == "1":
        print("[save_mhr_data] BLENDCAP_FORCE_CPU=1 -> all torch on CPU "
              "(CPU-only inference backend).")
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    try:
        import torch_directml
        if torch_directml.is_available():
            # DirectML hybrid: torch-directml can't run the
            # dynamic-shape ops (boolean-mask index, nonzero, dynamic gather)
            # that pervade the SAM3D decoder / the MHR head, so on
            # the raw DML device every frame fails with "The parameter is
            # incorrect." Route torch to CPU and keep ONLY the backbone on
            # DirectML via ONNX Runtime. The ORT backbone selects its EP
            # independently of the torch device, so this does NOT move it
            # off the GPU. (Set BLENDCAP_ORT_CPU=1 to also force the backbone
            # to CPU for a true full-CPU run.)
            print("[save_mhr_data] DirectML present -> torch on CPU; DINOv3 "
                  "backbone runs on DirectML via ONNX Runtime (USE_ORT).")
            return "cpu"
    except ImportError:
        pass
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_estimator_fast():
    """Load body/FOV models for explicit reviewed-box inference only."""
    device = _get_device()
    print(f"  Device: {device}")

    # The ORT path needs the backbone export (.onnx + its external
    # .onnx_data — the .onnx alone is useless) on disk. This helper must never
    # download exports. If an explicitly selected optional export is absent,
    # retain the existing PyTorch-backbone recovery. Runs BEFORE
    # every USE_ORT read below, so the backbone and MoGe blocks
    # all route consistently.
    if os.environ.get("USE_ORT", "0") == "1":
        _bb = Path(os.environ.get(
            "ORT_BACKBONE_PATH",
            str(CKPT_DIR / "onnx" / "backbone_dinov3_fp16.onnx"),
        ))
        if not (_bb.exists() and Path(str(_bb) + "_data").exists()):
            print("[WARNING] ONNX backbone missing - using the PyTorch backbone.")
            print(f"  ORT backbone export not on disk ({_bb}). Running the "
                  "PyTorch backbone for this run. No automatic download is attempted.")
            os.environ["USE_ORT"] = "0"

    print(f"  Pipeline env vars set:")
    for k in ("LAYER_DTYPE", "USE_COMPILE", "FOV_MODEL", "FOV_FAST",
              "BODY_INTERM_PRED_LAYERS", "HAND_INTERM_PRED_LAYERS",
              "MHR_NO_CORRECTIVES", "SKIP_KEYPOINT_PROMPT", "USE_ORT"):
        print(f"    {k}={os.environ.get(k)}")

    ckpt_path = CKPT_DIR / "sam-3d-body-dinov3" / "model.ckpt"
    mhr_path = CKPT_DIR / "sam-3d-body-dinov3" / "assets" / "mhr_model.pt"
    if not ckpt_path.exists():
        print(f"ERROR: Checkpoint not found: {ckpt_path}")
        sys.exit(1)

    from sam_3d_body import load_sam_3d_body, SAM3DBodyEstimator

    emit_progress(5, "Loading SAM 3D Body checkpoint")
    print(f"  Loading checkpoint: {ckpt_path}")
    model, model_cfg = load_sam_3d_body(
        checkpoint_path=str(ckpt_path), mhr_path=str(mhr_path), device=device)

    # Phase 3 cross-platform GPU path: swap the PyTorch DINOv3 backbone for
    # an ONNX Runtime session. Same .onnx file works on CUDA / DirectML /
    # CoreML / CPU EPs; ORT picks the best available at runtime. Decoder
    # and heads stay PyTorch (smaller, run fine on any backend).
    if os.environ.get("USE_ORT", "0") == "1":
        from pipeline.ort_backend import load_ort_backbone_for_sam3dbody
        onnx_path = os.environ.get(
            "ORT_BACKBONE_PATH",
            str(CKPT_DIR / "onnx" / "backbone_dinov3_fp16.onnx"),
        )
        emit_progress(8, "Wiring ORT backbone")
        active_ep = load_ort_backbone_for_sam3dbody(model, onnx_path)
        print(f"  ORT backbone active: {active_ep}")

    use_ort = os.environ.get("USE_ORT", "0") == "1"

    # FOV estimator (Fast variant uses MoGe-s automatically per env vars).
    # When USE_ORT=1, swap the MoGe encoder for an ORT session after load —
    # same pattern as the TRT wrapper in build_fov_estimator.py.
    fov_estimator = None
    try:
        from tools.build_fov_estimator import FOVEstimator
        emit_progress(22, "Loading FOV estimator (MoGe-s)")
        print("  Loading FOV estimator (moge2, model=s, fast=1)...")
        fov_estimator = FOVEstimator(name="moge2", device=device)
        # MoGe ORT path is gated separately. The exported encoder has a
        # fixed 512x512 input (its position-embedding sqrt baked a square
        # token grid into the trace), so forcing the pipeline to pre-resize
        # 1920x1080 -> 512x512 destroys aspect-ratio info that MoGe relies
        # on for accurate focal-length estimation. Empirically this pushes
        # body rotation diff from 0.11 deg (backbone-only) to 4.5 deg mean,
        # with 40% of frames > 5 deg. Off by default; opt in via
        # USE_ORT_MOGE=1 if the quality drop is acceptable.
        if use_ort and os.environ.get("USE_ORT_MOGE", "0") == "1":
            from pipeline.ort_backend import load_ort_moge_encoder
            moge_onnx_path = os.environ.get(
                "ORT_MOGE_PATH",
                str(CKPT_DIR / "onnx" / "moge_s_encoder_fp16.onnx"),
            )
            active_ep = load_ort_moge_encoder(
                fov_estimator.fov_estimator, moge_onnx_path,
            )
            fov_estimator.fixed_size = 512
            print(f"  MoGe encoder swapped to ORT: {active_ep}")
            print(f"  FOV pre-resize forced to 512x512 (ORT export constraint)")
        print("  FOV estimator OK")
    except Exception as e:
        print(f"  FOV estimator failed: {e}")

    estimator = SAM3DBodyEstimator(
        sam_3d_body_model=model, model_cfg=model_cfg,
        fov_estimator=fov_estimator)
    emit_progress(28, "Estimator ready")
    print("  Estimator ready.")
    return estimator
