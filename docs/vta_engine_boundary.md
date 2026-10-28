# Engine boundary — M2 simplification, 2026-10-28

## Current M3 boundary — completed in source, 2026-10-28

M3 moves tracking/review/identity/Character management behind the one-shot private
engine. `project_state.json` remains the only domain authority. Protocol v4 uses
explicit schemas, project-wide revision/hash CAS, request IDs, idempotent receipts,
atomic publication and status recovery. No ordinary domain Ctrl+Z is reintroduced.

Engine-owned: frame/Character/timeline observation, Assign/Repeat/reassign, hard
presence decisions, Character create/rename/delete, assignment/review-key clear,
candidate suppression, selected-Character cache-only tracking Update and prior-region
planning, pending/incorporated transitions, and repair-range edits. Existing private
rules remain authoritative; duplicate names are rejected, with no implicit merge.
Hints/dismissal, selections, Scene membership, formatting and native animation stay public.

Purpose-built neutral observations use a strict 16 KiB manifest and a checksum-verified
artifact in `.vta-engine/views`: maximum 32 MiB, 2048-frame window, bounded Character/
timeline collections. This contains presentation records, never ProjectState or live
private types. The public cache is disposable and revision-scoped. Unchanged polls,
draws and in-window scrubbing make no engine calls. Timers schedule at most one
background observation worker; they adopt ready DTOs on the main thread. Out-of-window
frames show loading rather than the previous frame's overlay. Explicit edits refresh
once; stale inputs fail explicitly. Canonical cache hashing is reused within one request.

**Corrections and Restore.** Assign/reassign/re-review explicitly correct ordinary
choices. Clear, deletion, suppression, repair replacement and Tracking Update retain
one protected previous result per companion. The engine stores a bounded compressed
prior document for exact dependent-state restoration, not a chronological history.
Restore is a new CAS mutation; it requires the same session, exact after-checkpoint
and unchanged guarded analysis inputs, then consumes the slot. A later domain change
invalidates it; restart/reopen clears ordinary UI availability. Native Depth edits
alone do not invalidate the slot. Durable uncertain-transaction recovery survives
reopen independently, including the prepared protected candidate/consumption record.
Generation and retarget output retention remain their existing M4 responsibilities.

The public host retains a bounded creation application receipt under
`.vta-host-bindings/<request-id>.json`. It records intended Scene/source membership,
not Character authority or decisions. Explicit Load/Retry can finish adoption after
lost replies or native Undo, checking current canonical IDs and unique native ownership.
It never silently selects among ambiguous Scenes, rewinds decisions or recreates animation.
Save As copies these receipts and recovery closure; derived views, locks/temp files and
resolved ordinary snapshot history are omitted. Delete removes the private dependency
closure; native objects/Actions/Depth keys are retained and output becomes stale.

**Actual separation.** Eighteen migrated methods were removed from production
`PrivateCoreBackend`; its frozen pre-M3 adapter is test-only. There is no in-process
fallback for migrated edits. Raw direct import sites remain **20 in six files**:
17 statements in analysis/reconstruction/rotation workers, two core import loaders,
and one computed camera import. Shared loaders now serve deferred generation,
retargeting and source bootstrap only. Existing provider workers remain mixed;
M3 does not claim zero host imports or completed provider separation.

**Measurements.** Same 29,186,332-byte owner document, 163-frame source: old cached
projection serialized analysis 163 times in 1.753 s; new observation serializes it
once, and 489 cached queries took 0.042 s with zero IPC. Uninstrumented new cold
observation measured 0.574 s plus 0.024 s public decode. Diagnostic command + refresh:
create 1.24–1.26 s, assign 1.286 s, reassign 1.284 s, hard state 1.330 s; old in-process
adapters measured about 0.50–0.57 s. Project parses fall from three to two per edit
(one mutation, one refresh). Two-Character tracking planning: 0.449 s. Instrumented
startup was 0.091–0.102 s; round trips about 0.47 s for observation, 0.57–0.62 s for
review mutations, 0.342 s for planning. Parsing, hashing and separate refresh dominate.
These are diagnostic samples, not thresholds or a claim of faster explicit edits.
One-shot remains appropriate; no persistent engine or per-Character revisioning added.

**Validation/deployment.** 834 public + 472 private full tests passed, zero skips/errors/failures. Fresh factory Blender under a private-import
blocker passes real assignment/hard-state/Restore, cached polls, native Undo isolation,
creation/Undo/explicit Load, lost-create reply, pending Save/Reopen/independent Save As
and Retry. Native Depth group drag/AutoKey/delete/three Undo/Redo cycles pass with
VTA Mode on and off. All 69 canonical owner QA files remain hash-identical. No owner
migration or installed deployment; source-only matching protocol packages are required.
Resolve experimental legacy general-Undo pending state in its prior build before upgrading.
No push/tag/release. Manual owner QA is deferred, not represented as completed.

M3 was harder than the initial estimate because bounded observation delivery and
native creation adoption needed real lifecycle proofs. Remaining estimate: M4 **4–6**
focused passes (generation planning/jobs/progress/cancel/providers/results/application),
M5 **2–3** (residual imports, recovery/process supervision, deployment/installer/security).
Total **6–9**, estimates rather than deadlines. M4 has not started.

This section supersedes earlier M1/M2 scope, Undo and proposed-Restore descriptions
below. Those sections remain historical evidence, not current implementation guidance.

Source-only Architecture C: **minimal VTA Undo**. Source coverage and Character
rename use the standalone private engine. Other domain operations/presentation
remain legacy in-process. No installed deployment, owner migration or full
application-isolation claim accompanies this checkpoint.

## Ownership and history

`project_state.json` remains authoritative (ProjectState 0.9 unchanged). Blender
owns native objects, Actions, Location/Depth keys and their Undo/Redo. Ordinary
VTA decisions—including rename, assignment, review and tracking—do not rewind
with Ctrl+Z. Correct them explicitly. There is no general domain history stack,
native domain token, snapshot generation or executable history-restore RPC.

Native Undo/Redo refreshes disposable presentation from authority and checks
Scene membership/native rig-output binding changes. Mismatches warn; VTA never
rewrites domain state, enforces the active Scene, recreates outputs or overwrites
native animation to hide a mismatch. Use explicit Load/refresh or generation as
appropriate. This checks bindings/provenance, not every evaluated animation sample.

The existing `vta_review_undo.py` module path now contains recovery plus native
presentation observers, not an Undo coordinator. The old private guarded
`restore_review_snapshot` API remains **installed-host compatibility only**:
the undeployed pre-refactor add-on still calls it. The new host has no such route.
Retire that API only with coordinated deployment; do not break the installed build.

## Transport and durable recovery

One explicit request launches one private Python child (`-I -B`, inherited stdio,
`shell=False`). No listener/service, generic RPC, pickle, private host import or
legacy fallback exists in migrated operations. Engine blocks Blender/BlendCap
imports. Client owns timeout, pipe cleanup and child kill/reap.

The original CC0 `vta_protocol` packages are byte-identical. Protocol **v3** checks
version, capabilities, child PID and exact definition hash. Allowlist:
`source-coverage`, `project-state`, `character-state`, `character-rename`,
`mutation-status`. Strict fields/types/IDs, 16 KiB JSON/node/depth bounds and local
path containment remain. Historical `review-restore` receipts are readable through
status only; that operation cannot be requested. No caller supplies project bytes.

Rename preserves trim/casefold/duplicate-name semantics and stable IDs. It submits
expected `{number, sha256}` revision, request UUID and proposed name. CAS, request
fingerprints and original idempotent receipts remain. Legacy observed changes
advance revisions; unobserved external ABA is not claimed detected.

The engine takes its OS lease and existing operation sentinel, validates the
canonical document, then writes prepared receipt → linked pending head → atomic
canonical publication → applied receipt → completed head. Before publication it
rereads and compares exact bytes; request-local hashing never substitutes for that
check. Recovery classifies only exact before/after hashes. Third-state conflicts
fail closed without overwrite. No full project snapshot is needed for this decision.

Uncertain outcomes remain durable in `.vta-engine` receipts/head and
`.vta-host-pending.json`, with a native warning Text for Save/Reopen. Domain edits
pause until **Retry Reconciliation**. Retry queries status and current authority;
it never blindly replays a rename. A known-not-applied operation requires a fresh
explicit Rename. Applied but superseded state requires review. Presentation failure
after commit remains pending and is not reported as an aborted mutation.

Save As copies recovery records/pending intent and rebinds the independent warning.
Locks/temp files and resolved ordinary snapshot history are excluded. Existing
legacy snapshots are not deleted; an unresolved legacy case retains its closure
in a copy. Old pending rename/capture can be read; **unresolved legacy Undo requests
must be resolved with the prior M2 build before upgrading**. They are never discarded
or reinterpreted. The canonical owner QA has no such pending state and needs no migration.

## Bounded Restore policy (future operation-specific work)

| Operation | Retention / invalidation |
|---|---|
| Rename | No Restore; rename again. |
| Ordinary assignment/review | Explicit correction. No general history. |
| Destructive reassignment, clear, deletion | Proposed one session-only protected prior slice per companion. Preserve affected identities, decisions and dependent tracking; reject after unrelated domain revision/source/evidence changes. Deletion may need a broader dependency closure. |
| Tracking Update | Proposed one previous compatible result and incorporation receipt. Same authority/source/evidence fingerprints required; session-only. |
| Generation | Keep accepted output and user animation durably until replacement succeeds. Domain result and Blender application have separate receipts. Reapply only with compatible source/Character/target fingerprints. |
| Retargeting | Preserve prior Action/slot/output references durably in the .blend; explicit restore against compatible target/mapping/current output. Never delete owner animation to reclaim history. |

No new Restore buttons or generalized restore system were implemented. Native Depth
edits alone should not invalidate a domain Restore candidate; native-output Restore
must still respect current manual animation. Ordinary convenience slots may clear
on restart. Unresolved recovery and retained user output must survive.

## Bounds, timing and remaining work

Canonical documents remain capped at 64 MiB; receipts at 4096 and 16 KiB each.
Receipt exhaustion fails closed without eviction. Safe receipt lifecycle/compaction
is future packaging work; receipts are recovery/idempotency data, not ordinary history.
Observation creates/updates revision metadata only when necessary, never snapshots.
Rename hashes each immutable before/after document once and serializes changed
domain state once; no-op serializes it zero times. No cross-request authority cache.
Unchanged rename preflight uses disk/identity guards instead of forced full reloads.

On the same 29 MB owner document, three diagnostic samples gave median rename
**0.703 s total / 0.633 s round trip / 0.060 s startup**, versus previous ~1.54 s
total. Existing observation fell from ~0.85 s to **0.392 s**. General restore timing
is no longer applicable: that operation was removed, not accelerated. Exact legacy
rename bytes match. Two large document hashes, parsing/validation and serialization
remain dominant; a resident engine is not justified by startup cost.

M3 estimate: 2–4 focused passes for CAS/receipt mutation families, bounded neutral
observations, identity/tracking semantics and selected explicit Restore policies.
M4: 3–5 for job IDs/progress/cancel/provider isolation and transactional retained
output. M5: 2–3 for remaining imports, coordinated deployment/compatibility retirement,
recovery UX, installer/runtime/versioning/security. **7–12 remaining passes**;
estimates, not deadlines. M3 has not started.

Same-user executable/filesystem configuration remains OS-trusted; IPC is not an
OS sandbox. Legacy writers are not globally atomic. Unknown operation sentinels
are never stolen. Parent-crash supervision and clean-machine packaging remain open.
This engineering boundary is not legal or shipping certification.
