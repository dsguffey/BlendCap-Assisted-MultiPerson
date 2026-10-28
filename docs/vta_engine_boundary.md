# Engine boundary — M2 simplification, 2026-10-28

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
