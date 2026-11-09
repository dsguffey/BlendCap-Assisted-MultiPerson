# VTA runtime and installation foundation — M5, 2026-10-29

Current status (2026-11-09): M1–M5 foundation is accepted and locally deployed;
protocol 7/layout 1/ProjectState 0.9. UI Passes 1–5 use that separated runtime.
Users install Blender separately. Final setup should provision matching private
runtime/provider environments and assets where terms permit. Compilation/IP
protection and forgiving lightweight licensing are roadmap prototypes, not
implemented packaging features or fixed vendor/technology choices. Final
installer/update/repair/uninstall, ARAP T2, payload/source/notices compliance and
clean-machine release QA remain open. See [current roadmap](current_status.md).


This is the architecture for a future setup package, plus developer proofs. It
is not a released installer, a redistribution clearance, or Public 1.0. M1–M4
ownership remains accepted. The tested initial host is Windows x64 Blender
5.2.x with an NVIDIA CUDA GPU. Wider platform/Blender support is not claimed.
The historical extension manifest minimum is not evidence of wider testing;
the final setup targets the tested line. No additional registration restriction
was introduced for source users during M5.

## Processes and authority

1. Blender imports only the GPL adapter and neutral CC0 protocol. It owns UI,
   Scenes, native animation, manual Location/Depth, target rig adapters and
   native application/Undo. Ordinary domain edits do not acquire general Undo.
2. A standalone private engine handles one explicit control request and exits.
   It alone interprets/mutates authoritative companion state. Strict protocol,
   revisions, CAS, receipts and explicit reconciliation remain in force.
3. A durable private job orchestrator survives the short control process and
   may finish after Blender closes. It launches an isolated provider process.
   Body inference and native BVH export reuse GPL helpers in the public provider;
   private analysis orchestration calls the existing SAM3 adapter. No private
   process imports GPL implementation. Models stay warm within that job only.
4. Neutral, bounded, hash-identified files carry provider results. Private
   validation/acceptance precedes public native application. Acknowledged
   application does not mean that Blender has saved an Action.
5. Rotational camera cleanup uses private authoritative preflight and a private
   solver entrypoint. Blender validates the neutral solve and applies native
   Root curves with the existing transactional/Restore safeguards. The private
   estimator was already private; no GPL algorithm was moved into it.

## Runtime ownership and roots

| Owner | Components |
|---|---|
| User/system | Separately installed Blender; supported NVIDIA hardware/driver; Windows |
| VTA setup | GPL adapter/helpers and corresponding source/notices; private engine; CC0 protocol; standalone control Python with NumPy; separate pinned body/analysis environments; approved provider code/assets; manifests/configuration; update/repair inventory |
| Upstream, provisioned by setup under applicable terms | CPython/native libraries; Torch/torchvision/CUDA/cuDNN wheel closure; OpenCV/its bundled FFmpeg and codecs; exact model/config/vocabulary files; optional official VC runtime acquisition |
| User/project | .blend, companion authority/evidence/results, manual animation, source media, exports; never part of install/uninstall ownership |

Recommended per-user layout (program files are immutable while active):

```text
%LOCALAPPDATA%/Programs/VTA/releases/<product-build>/
  adapter/                  GPL public program component
  engine/                   proprietary engine and its own CC0 protocol copy
  control/                  standalone CPython + minimal pinned dependencies
  package.json              exact file hashes, provenance and obligations
%LOCALAPPDATA%/VTA/
  config/runtime.json       setup-owned trusted roots/routes; no project input
  current.json              active coherent release and manifest identity
  runtimes/<content-id>/     body bundle / analysis environment + adapter code
  assets/<content-id>/       immutable approved models/configs/vocabulary
  downloads/                verified masters/wheels, reusable for repair
  cache/                    mutable Torch/HF/framework caches
  logs/                     setup/repair/runtime diagnostics, bounded
  repair/                   previous activation and repair/update metadata
<Blender user extension repository>/blendcap/   managed adapter registration
<user-chosen project>/Name.blend + Name_vta/    never inside install roots
```

The body bundle currently expects its public helpers, vendored architectures
and model/config files under fixed relative subpaths. Setup provisions this
tested tree under its explicit `body` root; it must not scatter required files
across guessed developer locations. Large runtime/assets are reusable across
program releases rather than downloaded for every adapter update. Model import
and execution use trusted masters; arbitrary project-selected weights are not
supported. Cache roots are writable user data, not program/model directories.

`vta_protocol/runtime.py` specifies layout v1. The bounded `vta-runtime` manifest
contains one product build (`1.0.0-dev.m5`), a protocol range, explicit execution
mode (`source`, `installed`, `test`), seven trusted component roots, and six
relative routes. Roots: adapter, engine, control, body, analysis,
analysis_source, models. Routes select the three Python interpreters, body
bundle, analysis source and SAM3 checkpoint. Traversal, network/ADS paths,
unknown fields, missing components/assets and incompatible versions fail clearly.
Runtime executables/configuration cannot come from a project companion.
Protocol v7 optionally pairs an eighth `review_control` root with a relative
`review_python` route. Both must be present together and pass the same local-root,
containment, file and interpreter-name checks. This setup-owned, minimal standard-
library runtime serves assignment/reassignment, assign/suppress batches, candidate
suppression and bounded review observations only. Other batches, protected Restore,
Tracking Update, generation, camera and retarget controls keep `control_python`
and its existing dependencies. If the pair is absent, review uses the explicitly
configured general control runtime. No executable is guessed. Source benchmarks
used an already-installed CPython 3.14.6; final setup must separately pin/provision
and validate its own reviewed interpreter. This does not implement that installer.
The current adapter's body helper is imported before the separate model bundle
is added to the provider path. A subprocess regression with a deliberately
conflicting runtime helper proves that old installed code cannot shadow it.

Precedence: explicit configuration path, then `VTA_RUNTIME_CONFIG` for deliberate
developer/test use, then setup's `%LOCALAPPDATA%/VTA/config/runtime.json`. An
explicit invalid configuration fails; there is no sibling checkout, evaluation
manifest or unrelated installed-addon fallback. An explicit Core-location
preference must agree with the trusted manifest's engine root; it cannot bypass
runtime configuration or implicitly select Blender's Python. Provider launches
require the loaded adapter, engine and configured roots to agree. The ordinary
installed route uses the configured standalone control Python, independently of
Blender's interpreter. No private root is inserted into Blender's `sys.path`.

## Bootstrap and compatibility

Setup detects selected Blender 5.2.x installations and provisions the matching
adapter into the per-user extension repository. It verifies registration in
factory background Blender before activation and records exactly which locations
it owns. It never overwrites an unrelated extension or silently enables multiple
VTA generations. Test enable/disable and compatibility before replacing the
registered copy; quiesce jobs and close Blender for program replacement.

Normal users do not clone repositories, run pip, choose checkpoint files or edit
environment variables. Setup installs a pinned CPython and offline/hash-locked
dependency closure, including native wheel DLLs, in managed versioned locations.
Do not relocate an arbitrary copied development venv: construct/provision it at
its final versioned location and activate only after tests pass. Source execution
can explicitly reference existing environments; that is not a clean install.

Models are bundled/mirrored only after exact-artifact provenance and downstream
terms are approved. Otherwise setup must implement the upstream-authorized account,
assent/download flow and validate exact hashes; never embed the owner's token or
require manual checkpoint hunting. Driver/VC prerequisites need explicit supported
vendor acquisition paths. No newly required model family was added by M5.

Protocol v7 and its complete definition hash are checked at control startup.
Provider adapter definitions are checked before model launch. One product build
identifies the coherent adapter/engine/runtime selection; provider/package/model
hashes describe its contents rather than adding independent user version knobs.
ProjectState remains 0.9. A component mismatch asks for matching setup/repair;
it does not silently use an older worker. Legacy Blender extension display
metadata is historical until final package/version freeze.

Simple review mutations may return a separate optional presentation descriptor
after durable publication, while the authoritative process still holds its lease.
The host checks request/project/source/frame/evidence scope, committed checkpoint,
artifact schema/hash and the current project/head/source/cache bytes before
adoption. Changed or invalid data causes a fresh observation RPC, not a second
mutation or rollback. The durable receipt remains unchanged; status/lost-response
recovery works without this disposable projection. No authority is deferred and
no synchronous control work was added to draw/poll callbacks. Existing busy
feedback remains. Matching adapter/engine/protocol deployment is required; this
optimization was validated in source and was not deployed to the owner install.

The owner approved retiring the old in-addon Install/Reinstall/Uninstall mutation
paths. Their stable command IDs now return a setup/repair message and neither
launch installers nor delete addon-local environments. Obsolete wizard presentation
and vendor setup scripts are not the new setup architecture. The first pre-1.0
UI pass removed their Preferences/sidebar exposure and internalized legacy command
search entries; remaining vendor scripts are not the final setup payload. Existing owner installation was not
modified. Final setup UX is still to be implemented.

## Update, repair and uninstall

Use a manifest-driven hybrid: stage a full coherent program release; reuse pinned,
verified runtime/assets by content identity. Verify all files and component
compatibility, run registration/control/provider smoke, then atomically replace
the small activation pointer. Retain the last good pointer/release. Interrupted
staging leaves the prior release active. Never update active Python environments
or DLLs in place. Full runtime replacement is preferable to incremental pip
mutation for 1.0; unchanged asset masters need not be redownloaded.

`vta_package.py` is a bounded developer proof: strict provenance/file allowlists,
deterministic new staging trees, streaming SHA/size verification, atomic per-file
repair from a verified independent master, a proposed activation record and a
pure uninstall plan. It does not download, extract archives, execute payloads,
register an extension, perform a real activation/update, or act as final setup.
Conditional/unapproved redistribution entries cannot be staged by the proof.
Repair is only for quiesced, manifest-owned program files; an interrupted
multi-component update uses the separate versioned-release model above.

Repair diagnoses missing/corrupt program/runtime/model files, mismatches and
adapter damage using the shipping manifest. It restores only exact owned files,
never scans/reconstructs user projects, never overwrites project companions,
animation or logs, and never treats a symlink as an owned payload. Final setup
must implement dependency-master acquisition and adapter re-registration.

Uninstall uses recorded owned files/install roots and Blender registrations.
Remove exclusively VTA-owned runtime/model components only when no retained
release references them. Do not recursively delete unknown files from an install
directory. Projects/media/.blend/companions/exports always remain. Preserve logs
and caches by default; optional explicit cleanup is confined to VTA-owned user
data. Uninstall/update must not kill processes based on a saved project PID.

## Process and artifact lifecycle

One-shot commands have bounded deadlines and kill/reap on timeout. A host crash
may leave a command to finish its atomic transaction; the next control call
reconciles the journal. A durable inference job intentionally survives Blender
exit, finishes to a terminal result, and requires explicit recovery after reopen.
No automatic inference replay or native output application occurs.

Providers acquire a live handle to their owning job process on Windows and exit
if it dies. POSIX direct children watch parent identity. Camera solve children
watch Blender instead. Handles are live launch authority, not saved status/PIDs.
Normal close/cancel releases models/GPU memory; hard owner death releases the OS
process. Lock leases are crash-released. Stale PID/status never authorizes a kill
or rerun. Starting grace expiry or a lost running lease marks the job unknown;
duplicate launch attempts observe the original job, never launch twice. Shutdown/
reboot follows the same unknown/terminal receipt recovery. This is durable
recovery plus bounded supervision, not a permanent service or perfect OS sandbox.

| Artifact | Retention / cleanup |
|---|---|
| Active/prepared/awaiting/unknown job | Protected; resolve explicitly, never TTL-delete |
| Ready unapplied / accepted outputs | Preserve manifests, validated result and output closure; no automatic deletion of the only recoverable output |
| Terminal provider scratch | Explicit `job-cleanup`, minimum age 24 h; acquire control + run leases, preflight containment/links, at most 4096 entries; delete only fixed `provider/` scratch |
| Failed/cancelled job | Same scratch reclamation; retain small outcome/diagnostic records for idempotency/recovery |
| Journals / protected Restore | Retained; no generic history purge; existing receipt/job caps fail clearly instead of evicting authority |
| Observation windows | Existing two-window disposable cache policy |
| Source stages | Immutable before/after hashes, request-scoped publication receipt, candidate removed after publication; at most 32 unresolved candidate stages |
| Retarget snapshots | Public input removed in `finally`; neutral outputs bounded by the existing artifact cap; explicit support cleanup after leases/consumers settle |
| Diagnostic logs | Existing bounded provider stdout, job traceback tail, and last 64 lifecycle events; no mandatory telemetry |

Job IDs and timestamped lifecycle records tie logs to provider/package/model
provenance. Project logs remain under the companion (including `.vta-engine/jobs`)
and Advanced → Open Logs Folder still opens that common base. Contextual Go to
Log remains. Setup/repair/global logs belong under the user log root above.
Future optional support bundles should include selected manifests, versions and
bounded logs after path/secret review; no project media, credentials or automatic
cloud upload. `blendcap_vta_errors.py` defines compact actionable primary failure
categories; detailed provider text/tracebacks stay in Details/local logs.

## Load AI and GPU lifetime

For this job-scoped architecture, Load AI means runtime/model/integrity/device
readiness verification, not a promise of a cross-job warm model. It must not run
inference or change project authority. The first actual job may still cold-load.
No new buttons were added by M5. Unload is idempotent when idle; active owned work
must cancel/settle and release provider resources. Authoritative state is never
unloaded. Cross-job provider residency is deferred until realistic cold/warm
measurements justify its additional lifetime/security/recovery complexity; an
authoritative daemon remains excluded.

## Proof and remaining release work

M5 tests explicit source/installed/test configurations, malformed/missing roots,
path traversal, mismatch rejection before launch, deterministic staging, corrupt/
missing file repair, project exclusion, inert retired installer commands, actual
provider parent loss, active-job cleanup protection, orphan/stale status, and
source-publication lost-reply recovery. The relocated control/adapter proof runs
without private/public developer source imports or installed addon copies. It
uses the user-installed Blender/Python prerequisite and does not install fresh
GPU environments/weights: **it is not a full clean-machine GPU/offline install**.

Final setup must reproduce pinned wheels/native DLLs/models from audited masters
on a clean VM/machine, validate driver/VC requirements, offline/reinstall/update/
repair/uninstall, space/Unicode/long paths and interruption/rollback. Exact wheel
and per-DLL/codec SBOM, signed/authentic masters/update metadata, model assent,
GPL corresponding-source delivery and final combined-payload review remain
release gates. ARAP T2 must cover malicious files/reparse races, permissions,
resource exhaustion, provenance/authenticity, archive handling if later added,
crash supervision, diagnostic redaction and the exact final shipping bytes.

Dedicated pre-1.0 UX requirements remain: Mark → Assign Marked / Suppress Marked
with batch-first actions; immediate busy state; honest generation phases/progress
and Cancel; explicit operation latency/busy feedback (assignment+refresh ~1.29 s
previously observed); labels/layout/Advanced cleanup. Benchmark realistic clip
lengths/cold-warm throughput before considering cross-job residency. No batch
marking, UX redesign, polished installer, release candidate or release was started
automatically by M5.
