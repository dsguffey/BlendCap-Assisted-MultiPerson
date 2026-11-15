# Portable AI runtime technical build — 2026-11-15

Status: **internal technical validation only**. No model, wheel, or installer
artifact in this build has been marked commercially redistribution-approved.
No Blender process or owner QA file was used.

## Consolidation decision

All three evaluated runtimes use CPython **3.12.10**. The `body` environment
uses Torch **2.7.1+cu128** and torchvision **0.22.1+cu128**; `analysis` uses
Torch **2.11.0+cu130** and torchvision **0.26.0+cu130**. A disposable overlay
of the analysis stack plus body-only packages initialized and ran one SAM 3D
Body GPU inference, while the baseline analysis stack ran SAM3 GPU inference.
That overlay was not a self-contained unified environment. SAM3 initialized on
the body stack, but its first inference hit GPU out-of-memory on the test
machine, so equivalence on that stack is unproven. Combining the stacks also
changes the compiled analysis attachment layout and raises the minimum NVIDIA
driver requirements for the body route. Keep **one Python version, two pinned
inference stacks** for 1.0. The installed test driver was 616.92 on an RTX
5070 Ti. [CUDA 12.8](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html)
and [CUDA 13.0](https://docs.nvidia.com/cuda/archive/13.0.1/cuda-toolkit-release-notes/index.html)
give the relevant driver compatibility baselines; the installer prerequisite
must be stated for the exact packaged CUDA stack.

## Construction and evidence

`tools/build_portable_ai.py` assembles `body` and `analysis` from the official
SHA-256-pinned CPython 3.12.10 Windows archive and selected installed wheel
RECORD entries. It verifies each copied wheel member against its RECORD hash,
checks versions and RECORD identities against `tools/portable_ai_lock.json`,
omits test/cache/entry-point files, and writes an external per-file provenance
report. The interpreter uses relative `python312._pth` paths. It has no venv
configuration, package `*.pth`, developer Python path, or download step.
The build requires explicit local `--site` inputs and does not alter them.

`tools/build_portable_ai_assets.py` selects production body code and the
SAM3 source subset, verifies the five model/configuration hashes, and pins all
361 copied source/weight files with `tools/portable_assets_lock.json`. The
internal build read body weights from the installed extension and SAM3 from
the Kinesia evaluation directory. The Kinesia source checkout at `4cbc2b6`
has an essential untracked `stream_detector.py`; its exact bytes are locked,
but a clean release builder still needs an archived or committed authoritative
source input. No Kinesia checkout files were changed.

| Component | Files | Bytes |
|---|---:|---:|
| `control` | 841 | 37,248,766 |
| `body` including code and weights | 18,395 | 9,159,444,949 |
| `analysis` | 16,158 | 3,111,256,544 |
| `analysis_source` | 113 | 4,047,397 |
| `models` SAM3 | 1 | 3,450,062,241 |

The internal full stage, including the compiled engine and extension archive,
contains **35,565 files / 15,831,461,100 bytes uncompressed**. Its
`package.json` is 6,676,511 bytes, so maintenance's package-manifest size
limit was raised to 32 MB while retaining the per-file hash and count checks.
The source-aware package filter now permits the required
`body/models/fast-sam-3d-body/tools/` files while still excluding a top-level
build `tools/` directory.

An existing compiled test engine and previously built extension archive were
used to stage the full payload without launching Blender. The five component
inventories use `redistribution: internal-test-only`; the stage carries
`internal_test: true`. Normal installation verification rejects it. The
production `--components --complete` path still requires explicit
`redistribution: approved` for every component. Its existing `build_release.ps1`
entry point consumes the same pinned component layout after approval.

## Validation

Both Python runtimes were renamed to new installation roots and imported
Torch, torchvision, OpenCV, NumPy, and their native libraries with all Python
search paths inside the corresponding component. CUDA was available.
Packaged SAM3 source/checkpoint initialized and inferred on a 256×256 blank
image; packaged SAM 3D Body/DINOv3/MoGe/MHR source and checkpoints initialized
and inferred one synthetic reviewed box. The full stage was then relocated and
both GPU inference checks passed again using only paths inside the relocated
stage. Hugging Face/Transformers offline flags were set. These are technical
pipeline probes, not a clean-machine or full production-job test.

The internal 35,565-file stage passed file-hash verification and the offline
control/body/analysis import probe. The probe now invokes packaged Python
with `-B`, so it leaves the immutable stage's file list unchanged. Focused
release/packaging tests passed (34 tests, two skipped).

## Remaining release inputs

1. Resolve exact artifact-level redistribution terms and notices for the
   included Meta SAM3/SAM 3D Body/DINOv3 assets, MHR and MoGe weights, and
   the actual PyTorch/CUDA/cuDNN and OpenCV/FFmpeg binary list. The prior
   redistribution matrix is the starting point; the internal manifests identify
   the precise bytes for review.
2. Preserve the untracked Kinesia `stream_detector.py` as an authoritative,
   reproducible source artifact before a customer build.
3. Rebuild the compiled engine and extension from the release commits, then
   validate the complete Inno EXE plus sidecar ZIP and installer lifecycle in
   a disposable target. This pass did not run Blender or publish a ZIP.
