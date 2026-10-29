# M5 production payload provenance — 2026-10-29

This records the intended supported configuration and observed dependencies. It
does not approve final shipping bytes. The detailed private inventory is
`docs/research/vta_m5_package_provenance_20261029.json`: 35 component/asset records,
plus actual model-load observations of 51 body and 40 analysis distributions.
Those counts overlap and are not a prescription to ship every installed package.

| Component | Observed identity / role | Distribution requirements / planned acquisition |
|---|---|---|
| Blender | 5.2.x Windows host | User-installed; GPL host; not bundled by VTA setup plan |
| Public adapter, body/BVH helpers, camera frame provider | M5 source checkpoint, BlendCap-derived | GPL-3.0-or-later; retain notices and provide corresponding covered source/build/install material |
| Private authority/job/rotation implementation | M5 source checkpoint | Owner proprietary component; separate engine processes; no GPL implementation imports |
| Shared wire/runtime/rotation-result validators | Protocol 6, layout 1 | Original CC0-1.0 contract, identical in both components |
| SAM3, SAM3D Body, DINOv3 | Existing exact code/weight/config masters | Custom Meta terms; preserve actual agreements, downstream obligations and required acknowledgments; setup bundle/mirror only after final terms/provenance review |
| MHR | Existing `mhr_model.pt`, SHA `352e271a…7377bc` | Prior audit matched the exact asset to official MHR v1.0.1 Apache-2.0 assets; retain independent grant/provenance and applicable notices |
| MoGe 2 small normal | Existing source and `moge-2-vits-normal/model.pt` | MIT plus Apache-2.0 incorporated source; exact model-card/master provenance retained; final weight-grant review still required |
| Kinesia stream adapter | Existing frozen PyTorch SAM3 adapter | CC0 wrapper; vendored SAM/tokenizer/dependencies retain their own terms |
| CPython/control NumPy | Standalone control process; separate AI environments | PSF/NumPy and actual native library notices; setup provisions pinned runtime rather than relying on developer Python |
| Body Torch / torchvision | 2.7.1+cu128 / 0.22.1+cu128 | BSD cores plus actual third-party and NVIDIA runtime terms |
| Analysis Torch / torchvision | 2.11.0+cu130 / 0.26.0+cu130 | Separate pinned environment; BSD cores plus actual third-party and NVIDIA runtime terms |
| CUDA/cuDNN DLL closure | Body CUDA 12.8; analysis CUDA 13.0 | Exact version-specific NVIDIA redistributable/notice/assent conditions; no assumption that Torch BSD licenses NVIDIA DLLs |
| OpenCV / embedded FFmpeg/codecs | Effective body cv2 5.0.0; analysis 4.11.0 | OpenCV/wrapper and actual native codec licenses/notices; freeze one tested wheel per environment |
| Other Python/native helpers | Actual loaded distribution inventory | Per-package licenses, wheel hashes and native notices; not a blanket license for either venv |
| NVIDIA driver / possible VC runtime prerequisite | Existing system dependencies | Driver user/system-owned; setup checks/acquires supported official VC prerequisite under applicable vendor terms |
| Workspace/profile assets | Current VTA program workspaces and profile data | Include only audited program assets; verify embedded-content provenance; no QA scenes/models/footage |

The body environment contains overlapping `opencv-python 4.13.0.92` and
`opencv-contrib-python 5.0.0.93` metadata, while the effective imported cv2 is
**5.0.0**. The future installer must not copy this overlay blindly or report the
4.13 metadata as the actual imported library. Resolve a single pinned tested
wheel and re-run the native/camera/provider proofs on that clean closure.

Primary terms were rechecked for [SAM3](https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE),
[SAM3D Body](https://raw.githubusercontent.com/facebookresearch/sam-3d-body/main/LICENSE),
[DINOv3](https://raw.githubusercontent.com/facebookresearch/dinov3/main/LICENSE.md),
[MHR's asset release](https://github.com/facebookresearch/MHR/releases/tag/v1.0.1),
and [MoGe](https://raw.githubusercontent.com/microsoft/MoGe/main/LICENSE).
These support retaining the existing conditional distribution strategy; process
separation is not a legal clearance of the eventual combined payload.

The supported candidate does not need MediaPipe, legacy Face/ICT assets,
SMPL/SMPL-X, PVA-Net/GVHMR/HTD-Refine, YOLO/Ultralytics/THOP detector weights,
training data, research viewers, unselected ONNX/TensorRT/MLX/DirectML backends,
owner QA/media, or bundled Mixamo/Unreal/ARP/CloudRig example assets/plugins.
Some may remain installed or in historical research; that does not make them a
shipping dependency. No restricted research asset was added by M5.

Final package work must freeze archive hashes, full native/codec SBOM, model
terms/assent and acquisition, GPL source delivery, VC/NVIDIA obligations,
workspace provenance, authentic/signed update masters and counsel's exact-payload
review. No new licensing restriction requiring a different architecture was
identified. The paid/offline-installer clearance remains a release gate.
