# Engine boundary — Architecture Cleanup Milestones 1–2

Implementation verified 2026-10-28. Source coverage and Character rename now use
an owned standalone private engine. Other domain workflows and presentation still
use the legacy in-process backend. This is a validated vertical slice, not full
application isolation or a release/deployment claim.

## Transport and ownership

`blendcap_vta_engine.EngineClient` launches one child per explicit request using
an existing standalone Python, `-I -B`, inherited pipes and `shell=False`. There is
no listener, resident service, arbitrary RPC, private host import or silent legacy
fallback on migrated operations. The private entry point blocks Blender/BlendCap
imports. The client owns timeout, pipe cleanup and kill/reap of its child.

Neutral `vta_protocol/` definitions are original CC0-1.0 code, vendored verbatim
in both repositories. Protocol v2 has an exact source-SHA handshake in addition
to version, capabilities and child PID. Synchronized package bytes are required,
including newline representation. Tests independently compare the copies.

Strict deterministic JSON remains bounded to 16 KiB, with exact fields, IDs,
types, finite numbers and complexity limits. Paths are bounded absolute local
paths, rejecting traversal, device/network and alternate-stream syntax. Names
are inert Unicode data; existing domain trim/casefold/uniqueness rules remain.
Internal newlines are escaped, not newly prohibited. Receipts and their envelopes
must fit the wire limit before publication. No pickle or executable payloads.

Allowlist: `source-coverage`, `character-state`, `character-rename`,
`review-checkpoint`, `review-restore`, `mutation-status`. Coverage remains read-only.
Observation/checkpoint operations may create bounded engine history metadata.

The canonical companion `project_state.json` is the sole domain authority.
Blender owns its native objects, animation and Undo tokens; cached names are only
presentation. Engine snapshots/receipts are immutable recovery/history, not a
second editable database. Existing ProjectState 0.9 is unchanged.

## Mutation and Undo

Rename submits project/Character IDs, expected project revision and proposed name.
Revision is `{number, sha256}`: a monotonic project counter plus exact document
hash. Changed mutations/restores and observed legacy writes advance it. Stale
requests fail without overwriting; the user must reopen/reconfirm against fresh
state. Unobserved external ABA cannot be detected while legacy writers remain.
An observed external change starts a new native Undo epoch on the next explicit
edit; older tokens cannot overwrite that new baseline.
Project-wide duplicate-name scope is deliberately preserved, including its known
cross-Scene limitation.

A request UUID is also its idempotency key. Receipts contain request/operation/
project IDs, before/after checkpoints, changed flag and renamed Character result.
An identical retry returns the original receipt even after later Undo. Reusing
an ID with different content fails. Mutation status resolves applied/not-applied/
absent after recovery; transport failure alone does not prove non-application.

`.vta-engine/` holds the head, compressed content-addressed snapshots and request
records. An OS lease serializes engine requests and cooperates with the existing
project-operation sentinel. Only an abandoned engine-marked sentinel can be
reclaimed under that lease; unknown/legacy locks are retained. Before/after
snapshots and prepared intent precede atomic canonical publication, then receipt
and head completion. Recovery accepts only the exact before or after hash. A
third document blocks recovery without overwrite.

The shared review Undo bridge now stores opaque checkpoint IDs in native Text
blocks. Undo/Redo selects a prior engine checkpoint and issues a fresh CAS restore;
it never sends project documents or replays rename business logic. Legacy review
commands still run their existing guarded writers, surrounded by engine checkpoint
capture so interleaved rename/review Undo cannot overwrite each other's history.
The old public snapshot-restore method and legacy rename route are unavailable.

Native Blender Undo/Save cannot be atomically vetoed by their post/pre handlers.
On failed reconciliation, native state and authority may temporarily disagree:
VTA shows a durable warning, pauses further domain edits and offers **Retry
Reconciliation**. Native Undo/Redo may continue; the newest desired checkpoint is
retained separately from an uncertain request. Retry checks its receipt first.
Save/Reopen preserves unresolved intent; Load never silently rewrites authority.
Save As copies committed journal/snapshot/pending JSON, excludes locks/temp files,
and rebinds the pending warning to the independent companion. Normal reopen starts
a new native Undo session; persistent native Undo history is not promised.

## Bounds and remaining work

Canonical documents/decompressed snapshots: 64 MiB. Compressed snapshot history:
128 MiB; request receipts: 4096. Exhaustion fails closed and retains history rather
than silently deleting reachable Undo states. History retention/compaction must be
addressed before broad deployment: large projects can exhaust this conservative
M2 budget after relatively few distinct full-document edits. This build is not
installed into the owner's addon.

On a read-only copy of the 29 MB owner project, startup was about 55–59 ms,
rename exchange/lifetime 1.48/1.55 s, and Undo/Redo 1.70/1.77 s. Observation was
about 0.65 s. These are diagnostics, not acceptance thresholds or claims of instant
interaction. Snapshot/document work dominates startup. Revisit presentation/state
cost in M3; consider a resident process for measured cache needs, and M4 provider
jobs/progress/cancellation. No async job migration is included in M2.

Same-user files/executable configuration are OS-trusted; this is not a sandbox or
protection against a malicious engine. Exact hash checks do not make unrelated
legacy writers globally atomic. An OS/power failure during lock initialization
can leave an unknown sentinel requiring inspection; it is never stolen. Parent
crash supervision, packaging/version matching, history lifecycle and clean-machine
recovery remain M5 work. No shipping/license certification is implied.

Verification includes full public/private suites, real subprocess crashes at four
publication stages, timeout/corrupt/mismatched response recovery, CAS/idempotency,
OS lease contention, private-import blocking, actual native repeated Undo/Redo,
Save/Reopen and independent pending Save As recovery. Existing native Depth Undo
and source registration/compile proofs also pass. Detailed evidence and estimates
are in the private M2 research report and canonical boundary document.
