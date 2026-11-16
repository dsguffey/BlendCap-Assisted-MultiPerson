# StudioFree MoCap 1.0 internal full-installer validation (2026-11-15)

This is a technical **internal-test-only** record. It does not approve model,
native-library, or installer redistribution and does not authorize a customer
release. The public and private source branches were
`codex/mixamo-retarget-v1`. The final internal package was built from public
`b309afe` and private `d8c06b6` with a disposable Gumroad product identifier;
no owner license or live Gumroad seat was used.

## Payload and safeguards

- The release stage contains the public Blender extension; compiled, source-free
  private engine; pinned control, body and analysis CPython 3.12.10 runtimes;
  pinned analysis source, real SAM3/SAM 3D Body/DINOv3/MHR/MoGe assets; runtime
  routes; maintenance code; and the 205-file corresponding-source and notice
  bundle. The final E stage has 35,750 manifest entries and 15,597,842,545 payload
  bytes. Its package manifest has `internal_test=true` and
  `compliance_required=true`; every component manifest retains
  `redistribution: internal-test-only`.
- The one-download ZIP contains the marked Inno Setup EXE and full sidecar
  payload. ZIP integrity was checked after writing. The corrected ZIP is
  `StudioFreeMoCap-1.0.0-dev.m5-INTERNAL-TEST-b309afe.zip`, 10,579,681,752
  bytes (about 10.58 GB decimal).
  The installer EXE SHA-256 is
  `11c06751bde3fc46b8a4fe0f0a6d5834c0d4ace96bac5fc6803df715dfe1c707`.
- Production stage/package validation still rejects internal components.
  The separate `--internal-validation` path requires a marked stage, and the
  internal Inno variant has a distinct AppId, name, installation roots and
  explicit isolated-profile environment guard.
- The ZIP, extracted sidecar, installer-owned source copy and installed
  immutable release can temporarily require roughly 57.4 GB together.
  Allow at least 60 GB free for this full internal installation. This is
  a conservative engineering estimate, not an installer minimum-space check.

## Focused checks

| Check | Result |
|---|---|
| Both marked production and internal Inno variants compile | Passed |
| Public release and negative-gate tests | 38 passed, 2 expected skips |
| Latest uninstall manifest-limit regression tests | 18 lifecycle tests run: 16 passed, 2 expected hard-link skips |
| Full A and final E stage manifest and notice bundle validation | Passed |
| Final E internal ZIP creation and archive integrity | Passed; receipt marks actual payload `internal_test=true`, `test_fixture=false` |
| Disposable A installation through packaged maintenance and isolated Blender CLI | Passed; installed release, runtime config, compiled engine smoke and isolated extension verified |
| Same-version A repair | Passed; zero files changed |
| Corrupted A body runtime file | Passed; packaged repair restored exactly one file |
| Corrupted A `models/sam3.pt` | Passed; packaged repair restored exactly one file, then full release verified |
| Disposable owner-like projects, `_vta` state, export, settings, entitlement, unrelated extension after repair | Preserved byte-for-byte |
| Injected failure before A→E active switch | Passed; A remained active with its runtime roots intact |
| Full A→E update | Passed; E became active with A retained as previous; large assets used verified-copy fallback because hard links are denied here |
| E→A rollback | Passed; A returned to active with E retained as previous |
| Resume an earlier partial uninstall with oversized manifest | Passed using corrected packaged maintenance; only recorded release files removed, owner-like state preserved |
| Full E reinstall after cleanup | Passed; owner-like project, companion, export, settings, entitlement and unrelated extension preserved |
| Packaged uninstall when Blender is unavailable | Passed; extension removal deferred, owned release files removed, owner-like state preserved |
| Reinstall followed by normal packaged uninstall | Passed; isolated extension removed, owner-like project, companion, export, settings and entitlement preserved |
| Packaged control/body/analysis Python import closure | Passed for final E payload (CPython 3.12.10) |
| Compiled engine protocol and isolated licensing gate | Passed |
| Compiled generation/candidate/camera routes with fixture provider | Passed on final E engine; this is not actual model inference |
| Installed A compiled generation/candidate/camera routes | Passed with installed engine and body Python, using disposable fixture provider; valid output persistence, rejection and error publication checked |
| B-stage SAM3 candidate detector GPU inference | Passed with packaged analysis runtime, vendored source and real `sam3.pt`; synthetic 256 px frame returned zero detections |
| B-stage SAM 3D Body GPU inference | Passed with packaged body runtime; one synthetic-box result, local DINOv3, MHR and MoGe-s loaded |
| Installed A SAM 3D Body GPU inference | Passed with isolated installed body runtime and no development Python import paths |
| Installed A SAM3 GPU inference | Passed with isolated installed analysis runtime, vendored source and repaired model file |
| Real SAM3 candidate job through rebuilt compiled engine E | Passed with installed and final E-stage analysis runtimes and model; job reached ready and published its result |
| Real installer EXE execution | Pending: a disposable Windows VM is required to avoid owner machine registration/profile changes |
| Full real-model generation and clean-machine Blender GUI workflow | Pending |
| Physically disabled-network test | Pending; offline flags and local model paths were exercised |

The real Inno EXE was deliberately **not** executed
on the owner's Windows installation. The internal distribution is therefore
not yet a complete end-to-end installer validation, regardless of the CLI
maintenance results.

The sandbox denied `os.link` even for a two-file probe under the disposable
build root. The maintenance implementation falls back to a verified copy when
linking fails. A full payload update here can validate that fallback, but
physical large-asset hard-link reuse needs a filesystem where links are
permitted.

Performance follow-up only: several maintenance paths rehash the same complete
15.6 GB payload and 35,750-file release more than once within one transaction.
This pass kept those integrity checks intact; any deduplication of redundant
verification should be a separate bounded task with the trust boundary kept
explicit.

The first real installed candidate job revealed that a compiled worker did not
expose the portable analysis runtime's dynamically imported CPython standard
library. Direct SAM3 inference had passed, so earlier fixture-provider tests
did not detect this boundary defect. Private commits `1adf893`, `45d462c`
and `d8c06b6` add the matching portable `Lib`/`DLLs` paths and a finder for
stdlib children of already-compiled packages. Focused tests and the rebuilt
compiled engine E's real candidate job passed. Earlier A/B packages are
historical disposable test artifacts; only E is the final internal payload.

## Kinesia adapter provenance

The private repository's `vendor/kinesia_stream_detector/PROVENANCE.md`
records the exact 12,440-byte adapter hash, September 13 new-file patch and
retained Codex `Add File` action. These support project creation/adaptation,
not an absolute claim of original authorship. The MLX path adapts Kinesia's
tracked CC0 `detect_stream.py`; the PyTorch path uses the separately vendored
Meta SAM3 APIs. No additional concrete adapter-specific redistribution barrier
was identified. Kinesia CC0 and Meta SAM obligations remain in the existing
notice and compliance gate.

## Disposition

**Installer implemented with remaining validation.** The corrected internal
EXE and sidecar ZIP are complete, and packaged maintenance and representative
real GPU inference have passed. Actual Inno EXE execution in a disposable
Windows environment and clean-machine Blender GUI workflow remain untested.
ARAP T2 security hardening can proceed while those isolated installer checks
are scheduled. Commercial redistribution approval, signing and purchase
decisions remain separate and closed.
