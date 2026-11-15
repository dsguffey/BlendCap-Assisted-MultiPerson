# StudioFree MoCap 1.0 — Windows distribution compliance audit

2026-11-15 · Commercial packaging decision record · **No redistribution
approval or legal opinion**

## Evidence boundary

This audit uses the actual 35,565-file internal test stage whose `package.json`
SHA-256 is `d7d73d1d9caf59a2312b0e21a6c022455ca2c0d3a0a0441f6ec2ca2ca2568813`
(public `1062d043`, private `f7f214f`) and the current public `818735c` /
private `486d4b3` source and locks. The stage still has the earlier 113-file
`analysis_source`; the current [asset lock](../../tools/portable_assets_lock.json)
selects 97 source files. Its existing five component inventories are
`internal-test-only`. The [AI runtime lock](../../tools/portable_ai_lock.json)
pins all 83 wheel-role entries by version and RECORD hash. The companion
[native inventory](studiofree_distribution_native_inventory_20261115.json)
records paths, sizes and SHA-256 for **all 455 `.dll`, `.pyd`, `.exe` files** in
that stage. The [notice draft](studiofree_distribution_notices_draft/README.md)
contains **178 complete license/notice files** and a hash manifest. Rebuild
these inventories against the final customer stage; do not copy `approved`
from this research.

The older [redistribution matrix](../../../Video-to-Animation/docs/research/vta_redistribution_matrix_20261023.json)
is research context, **not** the current bill of materials. Its Ultralytics,
THOP, YOLO, ONNX/DirectML/TensorRT and optional media rows are not in this
minimum payload. Blender 5.2 is an external host prerequisite, not part of the
inspected installer. The owner already obtained GPL/public-private architecture
advice from John Kivus on 2026-10-27; this audit uses the accepted separation
and does not re-adjudicate it.

## Artifact-level disposition

“Conditional” means a distribution grant exists but the listed conditions
must be met. “Unresolved” means the exact file or route lacks enough evidence;
it is not a statement that commercial use is prohibited.

| Component and exact evidence | Verified terms / disposition | Required release action |
|---|---|---|
| Public `blendcap.zip`, workers and maintenance protocol; GPL text and notices are inside ZIP; public commit `818735c` | GPL-3.0-or-later public integration. Commercial copying is permitted subject to GPL. Private engine is separately compiled under the accepted architecture. | Deliver the exact public corresponding source and applicable build/install scripts, retain notices and warranty text, identify modifications, and ensure customer terms do not restrict GPL rights. Audit the final ZIP/source archive match. |
| Compiled `engine/` (45 files) and `maintenance/` (10); native file hashes in the native inventory | Owner proprietary engine plus CPython, NumPy/OpenBLAS, OpenSSL/libffi and VC runtime dependencies. No third-party grant covers the engine as a whole. | Preserve the accepted separation. Map bundled native notices and Microsoft redistribution entitlement before final customer terms. |
| `analysis_source/src/sam_3d_pose_estimation/stream_detector.py`, SHA-256 `217b24598208a9afd6442a93412e72d4e30f32173e0faf38f30e0c831d59ab56`; private [provenance](../../../Video-to-Animation/vendor/kinesia_stream_detector/PROVENANCE.md) | Prior audit classifies the wrapper CC0-1.0; its local source was **untracked** in a CC0 Kinesia checkout. This is a provenance caveat, not a runtime reproducibility defect. | Retain CC0 text. Confirm the local adapter was authored or licensed for StudioFree distribution; the enclosing checkout license alone does not prove rights in an untracked addition. |
| `analysis_source/vendor/sam3-main/` and `models/sam3.pt`, SHA-256 `9999e234…3cf8c9e`, 3,450,062,241 bytes; private [SAM3 provenance](../../../Video-to-Animation/vendor/sam3-main/PROVENANCE.md) | [SAM License 2025-11-19](https://github.com/facebookresearch/sam3/blob/main/LICENSE): source **and weights** have an express, conditional redistribution grant. The copied code's license variant is preserved verbatim. | Provide that complete SAM3 agreement with code and weights; maintain trade-control/use restrictions in customer terms. Retain source headers. Verify owner's gated model acquisition record; no automatic per-customer Hugging Face approval is inferred from the license. |
| SAM3 `tokenizer_ve.py` and BPE vocabulary in the 97-file source lock | Meta SAM terms; tokenizer source credits OpenAI CLIP and open_clip, whose source notices are MIT. | Include the complete OpenAI CLIP and open_clip MIT notices from the notice draft; retain Meta headers and SAM agreement. |
| `body/checkpoints/sam-3d-body-dinov3/model.ckpt` `b5a2f9d3…9664f150abf`, 2,109,129,346 bytes; `model_config.yaml` in asset lock; Fast-SAM-3D-Body source | [SAM 3D Body agreement](https://huggingface.co/facebook/sam-3d-body-dinov3/blob/main/LICENSE) grants conditional redistribution of model/code. Fast wrapper has MIT changes over Meta SAM code. | Ship the **separate, longer** SAM 3D Body agreement and Fast wrapper MIT notice; retain Meta file headers. Do not substitute the shorter SAM3 code agreement. |
| DINOv3 architecture under `body/models/dinov3_hub/`; backbone inside the SAM 3D Body checkpoint | [DINOv3 License 2025-08-19](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md) conditionally permits code/model distribution. No independent DINO checkpoint is in this payload. | Include the complete DINOv3 agreement already in the public extension; retain source headers and apply its trade-control/use terms. |
| `body/checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt` `352e271a…e11dc0f`, 696,110,248 bytes | The prior audit independently matched these bytes to Meta MHR v1.0.1 `assets.zip` carrying [Apache-2.0](https://github.com/facebookresearch/MHR/releases/tag/v1.0.1). Permissive redistribution. | Include full MHR Apache text and any retained source notices; draft supplies the tagged license. |
| `body/checkpoints/moge-2-vits-normal/model.pt` `79a16621…3e1dc`, 140,550,416 bytes; `moge` 2.0.0 wheel | [Model card declares MIT](https://huggingface.co/Ruicheng/moge-2-vits-normal); MoGe source license includes MIT and DINOv2 Apache text. Permissive redistribution on recorded provenance. | Keep the exact wheel license and weight identity; include its notices. |
| `control`, `body`, `analysis` CPython 3.12.10; archive SHA-256 `8649692d…39415d` | [PSF License v2](https://docs.python.org/3.12/license.html) permits binary distribution with license/copyright and incorporated-software notices. | Keep each runtime's complete `LICENSE.txt`; retain source-archive pin and document any modifications. |
| Body Torch 2.7.1+cu128 / vision 0.22.1+cu128; analysis Torch 2.11.0+cu130 / vision 0.26.0+cu130; remaining 79 wheel-role entries in AI lock | PyTorch/torchvision BSD-3-Clause plus supplied third-party `LICENSE`/`NOTICE`; other packages carry their own permissive/copyleft terms. Wheel RECORD identities and the **135 wheel license files** in notice draft are exact. | Preserve every wheel's bundled license/NOTICE file and do a final native-component-to-notice check. ANTLR 4.9.3 and pycocotools 2.0.11 wheels lacked full license files; draft supplies upstream full BSD texts. |
| CUDA 12.8/13.0 DLLs in `torch/lib/` and `torchvision/` (cudart, cuBLAS, cuFFT, cuRAND, cuSOLVER, cuSPARSE, NVRTC, nvJPEG, CUPTI, nvToolsExt and related files) | [CUDA 12.8](https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html) and [13.0](https://docs.nvidia.com/cuda/archive/13.0.0/eula/index.html) EULA Attachment A conditionally lists redistributable Windows runtime libraries. PyTorch's BSD grant does **not** supersede NVIDIA terms. | Match each final DLL to Attachment A, include terms consistent with NVIDIA's distribution requirements, ensure private app-local use, and review customer terms. `analysis/.../nvperf_host.dll` is **not identified** in Attachment A; alternate `nvrtc*.alt.dll` variants also need a file-specific basis or removal after a focused dependency check. |
| cuDNN 9 DLLs in both Torch trees (body 9.7.1, analysis 9.19.0, confirmed by packaged `torch.backends.cudnn.version()`) | [cuDNN 9.7.1](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.7.1/reference/eula.html) and [9.19.0](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.19.0/reference/eula.html) SDK terms conditionally permit runtime `.dll` distribution with application functionality and pass-through requirements. | Retain complete applicable agreements in customer legal materials; validate exact DLL/version mapping and final customer terms. |
| OpenCV analysis 4.11.0.86 and body 5.0.0.93; FFmpeg DLL SHA-256 `f0460320…debed67` and `7aabff02…33f9aa31` | [OpenCV Apache-2.0](https://opencv.org/LICENSE/); [opencv-python wheels](https://github.com/opencv/opencv-python) state bundled FFmpeg is LGPL-2.1. Both exact DLL MD5s match OpenCV's official 4.11.0 and 5.0.0 `ffmpeg.cmake` pins. Conditional, not prohibited. | Retain both `cv2/LICENSE*` texts; supply matching FFmpeg source/build/configuration and LGPL source/relink route for each DLL; do final codec/patent review for intended markets. See identities below. |
| Other native binaries: NumPy/SciPy OpenBLAS, Intel OpenMP, OpenSSL/libffi, libuv, zlib, CPython `.pyd`, VC runtime DLLs; all 455 files in native inventory | Permissive/third-party notices generally travel with Python and wheels, but per-DLL mapping has not been certified. [Microsoft says VC runtime redistribution is limited to licensed Visual Studio users](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170). | Finish exact native-to-license mapping and preserve original license files. Owner supplies Visual Studio/Build Tools entitlement evidence or choose an independently licensed runtime route; do not infer rights from inclusion in third-party wheels. |
| Inno Setup 7.x build tool; installer script `installer/studiofree_mocap.iss`; **compiler is not in payload** | [Inno's current purchase FAQ](https://jrsoftware.org/inno-setup/purchase) requests commercial licenses but says purchase is **not strictly required**. | Owner may buy/record a commercial license as requested by Inno; absence is not a legal release blocker under the published FAQ. Preserve the installer tool's own license if the compiler itself is ever shipped. |

### Exact OpenCV FFmpeg source route

The staged analysis DLL's MD5 `a8c3560c8f20e1ae465bef81580fa92c` matches
[OpenCV 4.11.0's pin](https://raw.githubusercontent.com/opencv/opencv/4.11.0/3rdparty/ffmpeg/ffmpeg.cmake)
at binary commit `d63d7c154c57242bf2283be61166be2bd30ec47e`; its companion
`ffmpeg/4.x_20241226_src` branch resolves to `02a08baa7b8d330364c35477ba50524db8e4d335`.
The body's MD5 `a821a1135251859655090c795af05789` matches
[OpenCV 5.0.0's pin](https://raw.githubusercontent.com/opencv/opencv/5.0.0/3rdparty/ffmpeg/ffmpeg.cmake)
at binary commit `06dc20cad65dc7fcf784f70c95d46750520889a7`; its
`ffmpeg/5.x_20260602_src` branch resolves to
`664c0098dcb47b361f20c1d6a518653c23f5f2b5`. Both branches are in
`opencv/opencv_3rdparty`; an [OpenCV maintainer identified the `_src` branches](https://github.com/opencv/opencv/issues/30500)
as containing build scripts/options. These are verified **source routes**, not
yet assembled corresponding-source deliveries. FFmpeg's own [LGPL checklist](https://www.ffmpeg.org/legal.html)
calls for matching source, build explanation and an accessible customer route.

## Release work and decisions

**Codex can complete before a customer package:** regenerate the final manifest
from release commits; assemble the final notice index with all full texts;
package exact public corresponding source and the two FFmpeg `_src` snapshots
with build instructions; map every native DLL to its license; and establish
whether `nvperf_host.dll` and alternate NVRTC DLLs are required and separately
redistributable. Do not drop a runtime DLL solely from this paper audit.

**Owner administrative decisions:** confirm authorship/distribution authority
for the previously untracked Kinesia adapter; retain records of permitted Meta
model acquisition and the intended sales territories; provide valid Microsoft
VC redistribution entitlement; approve the customer-facing Meta/NVIDIA/FFmpeg
terms and source-access route; decide whether to purchase Inno's requested
commercial license. Inno purchase itself is not mandatory in its current FAQ.

**Narrow qualified review, only if facts cannot close them:** ask NVIDIA or
counsel about any retained DLL absent from Attachment A and the final app/GPL
customer-terms interaction under the accepted architecture. Seek territory-
specific codec/patent advice if selling into markets where the included codecs
create material patent exposure. This is not a request to redo the October GPL
architecture analysis.

**Disposition:** the payload's engineering path is ready for commercial
packaging work, and no inspected model license categorically forbids
redistribution. It is **not ready to mark `redistribution: approved`** until
the named source/notice/rights evidence and owner decisions are closed. No
installer was published and the release gate was not changed.
