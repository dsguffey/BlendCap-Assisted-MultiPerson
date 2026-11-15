# StudioFree MoCap 1.0 — third-party notices draft

**Draft for the audited internal Windows payload; not a customer notice or
redistribution approval.** The authoritative file list is `manifest.json`.
Each entry has the complete, unedited license or notice bytes, its SHA-256, and
the source from which it was copied. `payload/` paths mirror the inspected
component tree; `blendcap-zip/` paths mirror the extension ZIP. The three
`additional/` license texts for MHR, ANTLR and pycocotools and the two CLIP
notices close identified omissions in the staged payload. The Fast-SAM-3D-Body
MIT text was copied from the public source tree because the stage omitted it.

The inspected package is an **older internal test stage** (public `1062d043`,
private `f7f214f`), identified by `inspected_package_sha256` in the manifest.
Its `analysis_source` has 113 files; current source commits `818735c` and
`486d4b3` select 97. This draft is useful for obligations and text identity,
but it must be regenerated and checked against the final release payload.

## Inclusion plan

1. Keep the complete GPL text, author notices and public extension source in
   the extension package. Publish corresponding public source, including
   applicable build/install scripts, for the exact released commit using the
   accepted public/private separation strategy. Do not attach a restrictive
   proprietary EULA to the public GPL-covered component.
2. Include **both** SAM agreements as separate complete texts: SAM3 from
   `payload/analysis_source/vendor/sam3-main/LICENSE` and SAM 3D Body from
   `blendcap-zip/licenses/SAM_LICENSE.txt`. Also include the complete DINOv3
   agreement from the extension and the separately licensed MHR, MoGe,
   Fast-SAM-3D-Body, OpenAI CLIP and open_clip notices. Keep their original
   headers and do not imply that any upstream author endorses StudioFree.
3. Keep the complete CPython `LICENSE.txt` in each Python runtime and the
   wheel `.dist-info` license/NOTICE trees. The manifest records exact paths
   and hashes for the audited stage. In particular preserve both Torch
   `LICENSE` and `NOTICE`, OpenCV `LICENSE.txt` and
   `LICENSE-3RD-PARTY.txt`, and the FFmpeg LGPL text embedded in the latter.
4. Before shipping, provide corresponding FFmpeg source and build/configuration
   material for **each** included OpenCV DLL. The exact OpenCV binary/source
   branch identities are recorded in the companion compliance audit. Preserve
   LGPL rights and make the source/relink route and notices accessible to
   customers. The source archive and customer access route are not yet in this
   draft.
5. The NVIDIA CUDA/cuDNN agreements and customer pass-through terms require a
   final delivery decision. Do not silently treat the copied Torch wheel's
   license as a substitute for the separate NVIDIA terms. File-level review of
   `nvperf_host.dll` and alternate NVRTC DLLs remains open.
6. Confirm Microsoft VC runtime distribution entitlement for the exact
   binaries in the native inventory. Do not infer that CPython/PyTorch wheel
   inclusion alone grants StudioFree a separate redistribution right.

The current extension's `licenses/THIRD_PARTY.txt` is historical: it describes
some optional ONNX, MediaPipe and other components absent from this minimum
payload. Use an updated release-specific notice index before publishing. The
full upstream license texts copied here are evidence, not a replacement for
the final installer/user terms or a release approval flag.
