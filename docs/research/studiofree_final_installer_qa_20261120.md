# StudioFree MoCap 1.0 — bounded final-installer QA checkpoint

Date: 2026-11-20. Branch: `codex/mixamo-retarget-v1`. Starting public/private
commits: `a62880b` / `d2efb5a`. This is an internal engineering checkpoint,
not a commercial release or clean-machine acceptance.

## Environment and artifact

No usable disposable Windows Sandbox, Hyper-V VM, VirtualBox, VMware, or existing
test-machine configuration was found. The Windows feature query itself could not
access CIM, so it does not prove that every optional feature is absent. No VM
was created and the Inno EXE was **not run** on the owner's normal profile.
Blender 5.2.0 CLI was available. The existing final E package was reused:
`StudioFreeMoCap-1.0.0-dev.m5-INTERNAL-TEST-b309afe.zip`, 10,579,681,752
bytes. Its full archive integrity and 35,750-entry, 15,597,842,545-byte payload
checks passed in the [prior internal validation](studiofree_internal_installer_validation_20261115.md);
they were not repeated here. This pass read the ZIP central directory (35,753
entries) and confirmed its embedded EXE SHA-256 matches the staged EXE and the
prior report: `11c06751bde3fc46b8a4fe0f0a6d5834c0d4ace96bac5fc6803df715dfe1c707`.
That limited check does not replace a full archive integrity test. The staged E
installer EXE is 2,098,924 bytes and
`Get-AuthenticodeSignature` reports `NotSigned`. Earlier test signing covered a
separate synthetic EXE and must not be presented as a signature on this final
package.

## Newly executed checks

| Check | Result |
| --- | --- |
| Disposable Blender extension install from E payload `blendcap.zip` | **Passed** with Blender's `--command extension install-file` in a new isolated profile. |
| E compiled runtime resolution from setup-format `runtime.json` | **Passed** with roots pointing only to the staged payload and disposable adapter. |
| Create MoCap Workspace and blank MoCap Scene | **Passed** in Blender background mode; the tagged MoCap workspace had mode enabled. |
| Disposable `.blend` Save As and reopen | **Passed**; Scene ownership tag and MoCap workspace mode persisted. |
| Actual Inno EXE startup, sidecar authentication, installer transaction and rollback | **Blocked by environment**; no disposable Windows machine was available. |
| Analyze, Character creation, candidate assignment, tracking, real-model generation, usable skeletal output, edit, export/retarget | **Not performed**. The background run did not provide an active MoCap workspace window context for normal UI operators. Prior direct GPU inference and a compiled SAM3 candidate job remain separate evidence, not this end-to-end workflow. |
| Physical network isolation / offline grace on a fresh machine | **Not performed**; the headless process set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, which is weaker than blocking network access. |
| Full A→B installer update, repair, rollback, uninstall; clean-machine GUI/GPU tests | **Not performed**. The prior isolated maintenance CLI tests remain valid but do not exercise Inno's registration, selection or startup. |

The Blender test used a new profile and disposable `LOCALAPPDATA` under ignored
`.build/internal-validation/`, installed only the packaged extension, and
generated only a disposable blank project. It did not use an owner QA clip or
the owner `QA.blend` / `QA_vta`. Blender tried to write a thumbnail under the
owner home directory during Save As, but the write failed and the target file
does not exist. No owner profile, runtime, entitlement, QA project or installed
extension was changed. This is a useful packaged-adapter smoke, not an
installed-application or clean-machine result.

## NVIDIA and space requirements

The payload pins body Torch `2.7.1+cu128` and analysis Torch `2.11.0+cu130`.
NVIDIA's [CUDA 13.0 release notes](https://docs.nvidia.com/cuda/archive/13.0.1/cuda-toolkit-release-notes/index.html)
give driver branch **580 or newer** as the CUDA 13.x minor-compatibility
baseline; this is a vendor compatibility floor, **not a validated minimum for
StudioFree's exact Windows model stack**. The only validated GPU/driver remains
RTX 5070 Ti with driver **616.92**. The exact minimum GPU and working Windows
driver still need clean-machine testing. The prior 60 GB free-space recommendation
for the internal full install is an estimate; no actual install time, peak disk
use or installer disk check was measured this pass.

## Disposition

**Validation partially complete — specific remaining tests.** The actual Inno
EXE, real clip to editable animation workflow, fresh-machine GPU/driver floor,
and physically offline behavior remain technical release gates. Acquire or
provide a disposable Windows VM/test machine with Blender 5.2 and an NVIDIA GPU
path, then create and execute a test-signed internal installer package and the full
workflow there. Owner visual GUI acceptance follows. Production publisher signing,
commercial redistribution permissions and Inno commercial licensing remain
separate release approvals. No full-payload verification, package build,
deployment, merge, tag or release was performed here.
