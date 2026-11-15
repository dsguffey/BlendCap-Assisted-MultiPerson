# StudioFree MoCap Windows installer lifecycle (Public 1.0 work)

Status: **release architecture and disposable transaction proof; customer payload incomplete**.
No owner QA file, installed Blender profile, or active VTA runtime is used by the
tests in this document. The current development build is not a customer release.

## Source and compiled build maintenance

The private repository is the **only authoritative private implementation**.
`tools/build_nuitka_standalone.ps1` compiles its committed source with Nuitka;
the generated `vta_engine.dist` is never edited as a second codebase. Its
`release.json` identity is checked by the engine; `build-info.json` records the
private source commit, build time, Python and Nuitka versions, build ID,
protocol, product version, fingerprint and trusted Gumroad product hash. The
release stage records both public and private commits and hashes every owned
payload file. Full staging refuses dirty tracked source, untracked package
source, protocol drift and a compiled engine from another private commit.

Normal development still runs the private source, source tests and development
adapter without an installer. The release path is: commit source → source tests
→ Nuitka compile → compiled probes → extension build → approved component stage
→ hash manifest → Inno Setup compile. `build_release.ps1` is the entry point.
Its `-ProbePython` argument selects a developer test interpreter with OpenCV
and NumPy for the disposable compiled worker-flow probe; the customer installer
does not use that interpreter. The build fails early if those probe packages
are unavailable.
The commercial licensing gate is the intentional compiled-mode difference;
application logic remains in one private source tree.

## Technology and supported host

Inno Setup **7.1.1 x64** was selected for a single EXE, conventional Installed
Apps uninstall, non-admin per-user installation and integrated signing hooks.
WiX/MSIX add packaging complexity without a present enterprise/MSIX requirement.
Official Inno Setup documentation requests a paid commercial license for
commercial use; the owner must obtain it before commercial release. A signed
official compiler was installed **portably under ignored `.build/tools`** for
syntax validation; no machine-wide installer-tool installation is required on
this development machine. The compiler is not included in Git.

The customer target is **Windows x64 and Blender 5.2.x**. The extension manifest
now declares 5.2.0 as its minimum. The legacy 4.2 wording in older BlendCap
documentation is not evidence that StudioFree's current private workflow has
been validated there. Blender is separately installed. No Blender binary is
modified or bundled. The installer uses Blender's `extension install-file -r
user_default -e` and `extension remove` commands.

## Ownership and retention

| Path / data | Owner | Update / repair | Uninstall |
|---|---|---|---|
| `%LOCALAPPDATA%/Programs/VTA/Installer/payload` | Inno Setup | Replaced by full installer | Removed |
| `%LOCALAPPDATA%/Programs/VTA/releases/<build>` | StudioFree setup | New immutable release beside old; exact-file repair | Owned files removed |
| `%LOCALAPPDATA%/Programs/VTA/install-record.json` | StudioFree setup | Updated with release IDs | Removed |
| `%LOCALAPPDATA%/VTA/config/runtime.json`, `current.json` | StudioFree setup | Switches compatible engine/adapter; previous retained | Removed only when pointing to owned release |
| Blender 5.2 `user_default/blendcap` | StudioFree setup | Reinstalled through Blender CLI | Removed through Blender CLI |
| `%LOCALAPPDATA%/VTA/license/entitlement.dpapi` | User entitlement | Preserved; no new use on repair/update | Preserved |
| `%LOCALAPPDATA%/VTA/logs` and ambiguous settings/caches | User support data | Preserved | Preserved |
| User `.blend`, adjacent `_vta`, exports and animation | User project | Never scanned or changed | Preserved |

The maintenance helper refuses an unmanaged existing `blendcap` extension on a
fresh install. Updates remain tied to the selected Blender installation. The
installer checks for a running Blender process and asks the user to close it;
it never kills Blender or saves a project. On an update, the previous release
is retained; rollback reselects its runtime and extension ZIP. All deletion
uses a manifest file list and removes only empty directories. Unknown files are
left in place. The entitlement is neither deleted nor deactivated on uninstall.
The uninstaller runs managed cleanup before Inno removes its payload and stops
if cleanup fails. Blender 5.2's bundled Python must still be present when
uninstalling; if Blender was removed first, restore Blender 5.2 or rerun the
StudioFree installer before uninstalling. This dependency should be removed
before a broadly distributed customer release.

## Release package and model/runtime boundary

`tools/studiofree_release.py` builds the extension from **tracked operational
public files** in an isolated Blender profile. It copies the compiled engine
only after checking the release identity and rejecting private `.py`/`.pyc`,
debug maps, repo metadata and caches. A complete customer stage requires an
explicit reviewed `components.json` with an approved source root, exact file
size and SHA-256 for every file in `control`, `body`, `analysis`,
`analysis_source` and `models`, plus six relative runtime routes. It rejects
developer `pyvenv.cfg`, `.pth`, runtime config, DPAPI state, `.blend` projects,
logs and private implementation source. The generated `package.json` records
relative path, size, SHA-256 and component for every installer-owned file.

Current local measurements: the compiled engine distribution is about **65 MiB**;
the generated public extension ZIP is about **1.5 MiB**. The development
analysis environment is about **3.25 GiB**, the model directory about **9.01
GiB**, and the installed development addon tree about **9.64 GiB** including
local environments/assets. These development paths are **not** a portable or
redistribution-approved customer payload. A monolithic offline installer would
therefore be very large. The simpler 1.0 direction is a small core installer
plus separately versioned, hash-pinned model/runtime components, using only
sources cleared for redistribution or an approved authenticated provisioning
flow. The stage format supports either bundled components or later verified
provisioning; it does not yet download anything. A package cannot be marked
complete by pointing at an arbitrary copied development virtual environment.

Windows x64, a supported NVIDIA driver/GPU for the tested inference path, and
Blender 5.2.x remain system prerequisites. CPython/Torch/CUDA/OpenCV closure,
SAM3, SAM 3D Body, DINO, MHR and MoGe exact artifacts need a pinned portable
component inventory before a customer installer can be built. Their final
redistribution/provenance decision remains in the later compliance milestone.
No final legal conclusion is made here.

## Transactions and validation

`tools/studiofree_maintenance.py` verifies the package before staging it under
a new release ID. It smoke-tests compiled `license-status` with disposable
`LOCALAPPDATA`, installs the extension through Blender CLI, then atomically
writes the managed runtime and current-release records. A same-version installer
rerun is Repair. Repair restores only corrupt/missing manifest-owned files from
the verified installer payload, reinstalls the extension and reselects runtime.
Update installs a new full release beside the previous one. Rollback swaps back
to the retained previous release. Uninstall removes owned files and the
extension while preserving entitlement and projects. Support events go to
`%LOCALAPPDATA%/VTA/logs/maintenance.jsonl` without keys or environment dumps.

Source tests cover A→B update, rollback, failure before switch, corrupt stage,
missing dependency, repair, uninstall ownership and entitlement preservation.
The committed licensed compiled build passed control IPC, DPAPI licensing and
offline-gate probes, plus disposable durable generation, candidate-analysis
and camera-cleanup worker probes. Those probes create a test entitlement in an
isolated local profile; they do not contact Gumroad or read the owner's key.
The disposable CLI probe uses the **real Blender 5.2 CLI and real compiled
engine** with synthetic tiny dependency placeholders in a temporary profile.
It proves extension installation/removal, compiled status, runtime selection,
repair and preservation. It does **not** prove GPU inference or an end-user
installer transaction. Inno Setup 7.1.1 compiled the installer source against a
synthetic fixture; the resulting EXE is explicitly an unsigned test artifact.

Operational SHA-256 hashes detect corruption; they are not publisher
authentication. Authenticode signing through Inno Setup `SignTool` (including
signed uninstall/temp copies), signed release/payload provenance, and a
timestamped production signing process belong to the security milestone.
The package has a place for public source and notices, but final Third-Party
Notices and asset provenance are also deferred. No auto-updater or telemetry is
part of the 1.0 full-installer update strategy.

## Remaining gate and later clean-machine QA

A normal customer **cannot yet** install and run all StudioFree AI operations
from this package because the portable, legally cleared runtime/model component
inventory is not complete. The owner must not distribute the synthetic test
installer. A clean Windows VM/machine later needs: Blender 5.2 detection and
selection; fresh install and unlicensed protected denial; activation; real
candidate/tracking/generation/camera operation; A→B update; rollback; repair of
runtime/model/extension; interrupted update; uninstall/reinstall with entitlement
and project preservation; Unicode/long paths, disk-space and offline behavior;
and signed-package verification. Owner GUI QA should be limited to install,
opening Blender, checking StudioFree/activation, a disposable workflow, then
repair/update/uninstall after closing Blender.
