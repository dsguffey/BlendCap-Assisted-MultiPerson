# StudioFree 1.0 offline payload inventory — 2026-11-15

Status: **build input research, not redistribution approval or a customer
package**. This narrows the earlier private
`docs/research/vta_redistribution_matrix_20261023.json` to the active Windows
x64, Blender 5.2, PyTorch/CUDA paths. All locations below are read-only local
evidence. The shipping bytes must be rebuilt in portable component roots,
hashed file by file, and approved before `components.json` can mark them
`redistribution: approved`.

## Actual production components

| Component | Consumer / purpose | Evaluated local source | Installed size and version | Disposition |
|---|---|---|---|---|
| Public extension | Blender UI, GPL provider workers, result import | Public repository tracked source | ~1.5 MiB ZIP; product 1.0.0-dev.m5 | Build from source; ship GPL and third-party notices |
| Compiled engine | Control IPC, durable jobs, candidate and camera compiled modes | Private source commit, Nuitka `.dist` | ~65 MiB, Python 3.12 build | Rebuild from committed source; no private `.py`/`.pyc` |
| `control` | Offline installer/maintenance Python; compiled runtime contract | Official CPython 3.12.10 Windows x64 archive, pinned by SHA-256 | Relocated technical probe: 841 files, 37,248,766 bytes | Portable assembly proven; final notice/release review still required |
| `body` | `blendcap_vta_provider_worker.py` executes `save_mhr_data.py`, Fast SAM 3D Body, DINOv3 architecture, MoGe, BVH export | Installed BlendCap `venv`, tracked public body source and `models` | Development venv 6.83 GiB; Torch 2.7.1+cu128, torchvision 0.22.1+cu128, imported OpenCV 5.0.0 (overlapping installed OpenCV wheels), NumPy 2.4.3, SciPy 1.17.1 | Required; curate portable import/native DLL closure; exclude unrelated installed packages |
| `analysis` | Compiled candidate process attaches its site-packages; SAM3 PyTorch inference | Kinesia `environment/.venv` | Development venv 3.25 GiB; Torch 2.11.0+cu130, torchvision 0.26.0+cu130, OpenCV 4.11.0.86, NumPy 1.26.4 | Required; distinct evaluated stack, do not assume interchangeable with body |
| `analysis_source` | `stream_detector.py` loads vendored `sam3.model_builder` and local tokenizer | Kinesia `source/src` and `source/vendor/sam3-main` | `src` <1 MiB; vendor ~20 MiB | Required subset plus SAM terms and tokenizer notices |
| `models` | `vta_candidate_provider.py` loads SAM3 checkpoint | Kinesia `models/sam3/sam3.pt` | 3,450,062,241 bytes | Required; custom SAM terms and original acquisition/access evidence |
| Body weights | `save_mhr_data.py`/Fast SAM 3D Body and MoGe | Installed BlendCap `checkpoints` | Four required files ~2.79 GiB | Required; separate model grants/notice obligations |

The installed body environment contains ~5,549 MiB of `torch` files and the
analysis environment ~2,712 MiB. The two different evaluated CUDA/Torch
versions cannot be merged safely without a new inference regression. Native
CUDA/cuDNN and OpenCV video DLLs are part of their respective runtime closure;
the NVIDIA and bundled codec terms must be preserved and checked for the exact
shipping DLLs. Windows NVIDIA driver and Blender remain system prerequisites.

`tools/build_portable_control.py` extracted a **37.25 MB** disposable control
runtime from a SHA-256-pinned official CPython 3.12.10 archive. Its relocated
`python.exe -I` passed `ssl` and `ctypes` imports with `sys.base_prefix` inside
the extracted directory. The source archive SHA-256 was
`8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d`.
The build retained Python's `LICENSE.txt` and omitted docs, headers, pip and
developer tooling. This is a technical control-runtime proof, not approval of
the remaining AI components or a complete customer release. Python's
[official license](https://docs.python.org/3.12/license.html) permits binary
redistribution subject to its terms and incorporated-software notices.

## Required weight identities

| Runtime path | Bytes | SHA-256 | Terms / source |
|---|---:|---|---|
| `models/sam3.pt` | 3,450,062,241 | `9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e` | Meta SAM License; Kinesia `models/sam3/sam3.pt` |
| `body/checkpoints/sam-3d-body-dinov3/model.ckpt` | 2,109,129,346 | `b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf` | Meta SAM License; installed BlendCap checkpoint |
| `body/checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt` | 696,110,248 | `352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc` | MHR asset; installed BlendCap checkpoint; independently recorded Apache-2.0 provenance needs final matching |
| `body/checkpoints/moge-2-vits-normal/model.pt` | 140,550,416 | `79a16621928c2bf0ed04659218c55c01075e950507f40bb3332fb4c873d3e1dc` | MoGe MIT model card in prior matrix; installed BlendCap checkpoint |
| `body/checkpoints/sam-3d-body-dinov3/model_config.yaml` | 1,488 | `1012fc3f39cb5e90e3f8fbadf7bded31604bfafdce0321d17a7c1a2d3f08b88d` | Meta SAM configuration; installed BlendCap checkpoint |

The public runtime contract now additionally checks the offline body source
entry points and DINOv3 `hubconf.py`. The body worker sets the local DINOv3
path, `pretrained=False`, and local MoGe checkpoint path. Its former online
DINOv3 fallback was removed. `USE_ORT=0` and `FOV_TRT=0` are fixed by the
current worker; ONNX/TensorRT exports are not minimum-package components.

## Excluded from the minimum payload

Kinesia `sam3-mlx` (~3.17 GiB), duplicate SAM 3D Body weights (~2.61 GiB),
Kinesia Torch hub cache, installed YOLO weights, Ultralytics/THOP, GVHMR,
SMPL/SMPL-X, optional ONNX/DirectML/TensorRT paths, dormant face models,
Gradio/web UI, research runs, downloads, tests, build tools, owner QA footage,
and user projects are not on the active production path. Installed venv folders
contain many more packages than the required import closure. They must not be
copied wholesale or treated as portable.

## Clearance and size gate

The upstream [SAM3 agreement](https://huggingface.co/facebook/sam3/blob/main/LICENSE),
[SAM 3D Body agreement](https://huggingface.co/facebook/sam-3d-body-dinov3/blob/main/LICENSE),
and [DINOv3 agreement](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md)
provide conditional redistribution terms, including agreement delivery and
other use restrictions. They are not a blanket approval of a StudioFree
commercial installer. Exact NVIDIA CUDA/cuDNN, OpenCV/FFmpeg, Python/PyTorch,
GPL public-worker, and private-engine combined-product obligations remain to
be resolved for the actual file list. None of the local Meta weights has been
placed in a customer distribution by this pass.

The evaluated development runtime and required weights total **about 16 GiB
uncompressed** before portable CPython, notices, and deduplication. This is an
upper-bound source snapshot, not a measured customer ZIP size. The new ZIP
builder uses ZIP64 with moderate compression, but a full-package size cannot
be reported until the curated portable components exist. The decisive missing
technical artifact is a portable, pinned Python/native dependency closure for
`body` and `analysis`; the decisive distribution gate is disposition
of the exact custom-license and native-library obligations above.
