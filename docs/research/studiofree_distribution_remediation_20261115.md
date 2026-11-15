# StudioFree 1.0 distribution remediation — 2026-11-15

This records engineering and source evidence for the proposed Windows payload.
It is **not** an approval to sell or a legal opinion. The explicit five-component
`redistribution: approved` gate remains closed. The older
[artifact audit](studiofree_distribution_compliance_20261115.md),
[native inventory](studiofree_distribution_native_inventory_20261115.json),
[AI wheel lock](../../tools/portable_ai_lock.json), and
[asset lock](../../tools/portable_assets_lock.json) remain the detailed input
records. The 35,565-file internal stage is a technical fixture, not a release.

## Resolved engineering obligations

| Issue | Evidence and disposition |
|---|---|
| FFmpeg corresponding source | The OpenCV 4.11.0 DLL SHA-256 `f0460320…debed67` is identical to the DLL in `opencv/opencv_3rdparty` source commit `02a08baa7b8d330364c35477ba50524db8e4d335`. Its `sources/` tree contains the exact FFmpeg **n4.4.5** source archive, libvpx 1.15.0, AOM 3.11.0, OpenH264 API headers 1.8.0, OpenCV wrapper source, and build scripts/configuration. The full OpenCV source is pinned to that branch's submodule commit `09892c9d1706f40342bda0bc404580f63492d9f8`. The 5.0.0 DLL SHA-256 `7aabff02…33f9aa31` likewise matches source commit `664c0098dcb47b361f20c1d6a518653c23f5f2b5`, containing FFmpeg **n7.1**, libvpx 1.16.0, AOM 3.14.1 and wrapper/build material, with full OpenCV commit `a0a660fcb1e58a295e6caa6aee64ed4d369b0181`. The precise archive hashes are in [the source lock](../../tools/distribution_source_lock.json). These are upstream-built source packages for the exact binary commits, rather than unrelated upstream tarballs. [OpenCV's source branches](https://github.com/opencv/opencv_3rdparty) and [FFmpeg's LGPL guidance](https://ffmpeg.org/legal.html) are the primary references. |
| Broken OpenH264 header archive in OpenCV 5.0 source branch | Upstream's `openh264-api-headers-v2.5.0.tar.xz` is only 32 bytes and empty: its archive script still names `codec/api/svc`, while the v2.5.0 checkout uses `codec/api/wels`. The bundle substitutes a deterministic, nonempty six-file archive of the API headers and LICENSE from OpenH264 commit `8c7008aeb6335e7d36ab0d9a023a63f82a8eaac0`, pinned at SHA-256 `b826bfb9…ecbae6`. The explanatory file records this packaging correction; the shipped OpenCV DLL is unchanged. The OpenH264 library itself is not bundled. |
| NVIDIA uncertain DLLs | In disposable relocated copies of both packaged Python runtimes, removed `analysis/torch/lib/nvperf_host.dll`, `analysis/torch/lib/nvrtc64_130_0.alt.dll`, and `body/torch/lib/nvrtc64_120_0.alt.dll`. With those absent, SAM3 initialized and inferred a synthetic image on GPU; SAM 3D Body/DINOv3/MHR initialized and inferred one synthetic reviewed box on GPU. A second focused pass also removed `analysis/torch/lib/cusolverMg64_12.dll` and `body/torch/lib/cusolverMg64_11.dll`; both model paths passed again. New `build_portable_ai.py` output excludes all five wheel members, while still verifying original wheel RECORD identities. The legal assembler rejects their return. This covers the evaluated 1.0 single-GPU inference paths; optional Torch profiling, compilation and multi-GPU solvers are outside this production route. |
| Notices and public source | [The compliance builder](../../tools/studiofree_compliance.py) validates 182 complete notice/agreement files against actual staged bytes and the pinned wheel/asset locks. It copies them into `payload/legal/Third-Party-Notices/`, adds a deterministic ZIP of the exact staged GPL public extension source and public build/install scripts, adds the two matching OpenCV FFmpeg source sets, and hashes every legal file in `payload/legal/manifest.json`. The 1.0 installer ZIP carries that tree in the same offline download; the maintenance path installs, repairs and rolls it back with the immutable payload. No private-engine implementation source is copied. The release stage requires all three pinned source checkouts; customer distribution packaging rejects a missing or changed legal tree. |
| NVIDIA agreement text | The notice baseline now includes full archived HTML copies of official CUDA 12.8/13.0 and cuDNN 9.7.1/9.19.0 agreements, each with provenance and SHA-256. Those are separate from the Torch BSD notice. The [CUDA 12.8](https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html), [CUDA 13.0](https://docs.nvidia.com/cuda/archive/13.0.0/eula/index.html), [cuDNN 9.7.1](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.7.1/reference/eula.html), and [cuDNN 9.19.0](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.19.0/reference/eula.html) terms impose distribution conditions. Their inclusion does not itself prove every retained NVIDIA DLL is redistributable. |

Static PE import inspection also found `caffe2_nvrtc.dll` imports the ordinary
`nvrtc64_120_0.dll` or `nvrtc64_130_0.dll`, not either `.alt.dll`;
`cusparse64_12.dll` imports its matching `nvJitLink` DLL. The FFmpeg plugin
remains a separate DLL in `cv2/`, so a customer can replace
that library. The bundle also gives the source, wrappers, dependency versions,
configure/build scripts and LGPL text for both copies. The source ZIP contains
all public files used to construct the extension; the separate proprietary
engine is not represented as GPL corresponding source. The source mechanism
does not assert byte-identical reproduction with a different compiler toolchain.

## Microsoft runtime route

The audited stage contains four copies each of `vcruntime140.dll` and
`vcruntime140_1.dll` in `control`, `body/Scripts`, `analysis/Scripts`, and
`engine`; all eight are Microsoft-signed version **14.42.34438.0**. Their
respective SHA-256 values are `052ad6a2…063d5027f4` and
`6a99bc01…6acbc6c8`. The three Python component copies originate from the
pinned CPython 3.12.10 embeddable archive; the compiled engine copy is in its
Nuitka output. The body and engine each contain a Microsoft-signed NumPy
`msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll`, version **14.40.33810.0**,
SHA-256 `a4c2229b…f5beb9cde`, also tracked through the NumPy wheel/engine
inventories. App-local deployment is the current technical route. Microsoft
[limits redistributing these files to licensed Visual Studio users](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)
and its [REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
controls the exact permitted files. Microsoft signatures and presence in an
upstream wheel establish provenance, **not** StudioFree's separate entitlement.
The owner must retain evidence of a valid Visual Studio/Build Tools license
and confirm the customer terms and exact REDIST files before approval. A
system-wide `vc_redist.x64.exe` prerequisite would still require that license
and would change the tested offline app-local layout, so it is not silently
substituted here.

## Provenance and remaining release decisions

- **Kinesia:** The [private source snapshot](../../../Video-to-Animation/vendor/kinesia_stream_detector/PROVENANCE.md)
  retains byte-for-byte `stream_detector.py` (SHA-256 `217b2459…31d59ab56`)
  and CC0 text. The [September 13 adapter manifest](../../../Kinesia%20Evaluation/reports/phase2_pytorch_stream_detector_adapter_manifest.json)
  records the exact local patch (SHA-256 `0511be3f…fa9457b1cef38fd`) and source
  hash. This is strong provenance for the local addition but does not prove
  that the untracked file was authored by the CC0 upstream maintainer. The
  owner should retain the patch/work record and affirm StudioFree's authority
  to distribute its own adapter contribution. SAM3 vendor files retain their
  separate Meta license.
- **Meta acquisition:** Keep the accepted-access/download record for SAM3
  `sam3.pt`, SAM 3D Body `model.ckpt`, MHR assets and DINOv3 code: account or
  organization, source/revision, date, terms accepted, and SHA-256. Existing
  asset hashes and local provenance identify the bytes but do not record every
  account click or agreement acceptance. No repeat of the already accepted
  GPL/public-private architecture decision is requested.
- **Sales and customer terms:** Owner selects intended sales territories and
  approves customer terms that preserve GPL, LGPL and Meta/NVIDIA rights and
  restrictions. Qualified counsel is warranted only for a material codec
  patent question in those territories or an unresolved retained native DLL
  not covered by its provider's distribution grant. The CUDA/cuDNN pass-through
  and Microsoft entitlement must be settled before marking components
  `redistribution: approved`.
- **Retained nvJitLink:** Both `nvJitLink_120_0.dll` and `nvJitLink_130_0.dll`
  are direct imports of their respective `cusparse64_12.dll`; the tested
  inference path needs cuSPARSE. CUDA Attachment A names the Windows JIT-link
  library `libnvJitLink.dll`, while [NVIDIA's own nvJitLink installation guide](https://docs.nvidia.com/cuda/nvjitlink/index.html)
  names it `nvJitLink.dll`. The version suffix is expressly contemplated by
  the [CUDA EULA](https://docs.nvidia.com/cuda/archive/13.0.0/eula/index.html),
  but the `lib` prefix discrepancy is not resolved by the documents. This is
  one narrow NVIDIA or qualified-counsel question before assigning an approved
  disposition to these required binaries. No inference-safe deletion is claimed.
- **Installer tool:** The Inno compiler is not shipped; its commercial license
  purchase is an owner preference under the [current FAQ](https://jrsoftware.org/inno-setup/purchase),
  not a demonstrated mandatory release blocker.

## Validation and gate

The disposable GPU probes passed after removing all five named NVIDIA DLLs.
The source assembler matched both staged OpenCV DLL hashes to upstream source
branches; its internal probe built and verified **205** legal files, including
both actual FFmpeg source archives, one corrected OpenH264 header archive,
182 complete notice texts and public source. The public source ZIP contains
no `vta_core/` implementation. Focused release/compliance tests passed.
These probes used the existing **internal** stage; no final customer payload
was built or redistributed. A final release build must regenerate this bundle
from its actual candidate package and pass the unchanged per-component
redistribution approvals. Then complete installer validation can begin.
