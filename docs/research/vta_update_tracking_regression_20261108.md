# Update Tracking foreground registration regression — 2026-11-08

**Classification: B, pre-start stall. Narrow public registration fix committed and deployed; owner foreground retry remains necessary.** Only Clear Selected Review Keys and Update Tracking have been reported as post-deployment owner failures. Other product workflows are not thereby manually accepted or rejected.

## Cause and correction

The shared busy wrapper added `modal` and `cancel` methods inside `register_properties()`, after `registration.register()` had already called `bpy.utils.register_class` for the operators. Blender captures optional native callback availability during registration. Python subsequently showed those methods, but the registered native operator had not acquired the timer/cancellation callbacks needed to start and finish deferred work.

A fresh installed registration-boundary probe captured both Update Tracking and Create Character with **modal=false, cancel=false, wrapped=false when registered**, followed by modal=true only afterward. This is concrete ordering evidence, not an inference from elapsed time. Blender's registration implementation selects native callback pointers from the methods present then: [rna_Operator_register](https://github.com/blender/blender/blob/main/source/blender/makesrna/intern/rna_wm.cc). Merely accepting `modal_handler_add` does not demonstrate native timer callback availability.

Fix: move the unchanged wrapper-installation statements to `prepare_operator_callbacks()`, called before the native class-registration loop. Keep actual property registration in its existing phase. This registers the deferred modal/cancel callbacks for Update Tracking and Create Character, while preserving the direct confirmed routes for review management and Repeat. No broader locking or tracking implementation change.

The earlier Clear Keys/family fixes avoided the deferred route for confirmed edits, but the earlier tests called Python modal methods directly and missed the native registration boundary. The earlier confirmation-handoff explanation was therefore incomplete. The new fresh-process regression asserts callback availability at the exact registration call and fails the old order. It does not inject UI events or inspect hypothetical ownership merely from Python attributes after registration.

## Reproduction and ownership

Latest Desktop QA.blend and its current QA_vta companion were copied/read-only hashed before testing (75 files). The screenshot establishes white selected at visible frame **20**, with `A VTA operation owns this project; wait or cancel it.` visible. At process inspection the owner Blender session was no longer running, so no live CPU/stack attribution is claimed. The canonical journal had pending=null and no analysis lock or host pending marker was present.

The warning comes from `generation_unavailable_reason()` and `_analysis_locked()`. The latter also considers the public helper's in-memory busy flag; it is not proof of an engine-owned project lease or a live worker. Before the first timer callback that flag legitimately becomes true. With the missing callback, work never starts and the flag is not cleared. This is a UI lifecycle fault, not a demonstrated systemic locking flaw.

Actual engine transactions retain their crash-released lease, sentinel, CAS/revision checks, journal, durable uncertain-response recovery and protected previous-result Restore. Uncertain authority must still remain explicitly recoverable rather than being silently discarded. Unknown external lock formats remain fail-closed. No lock deletion/recovery policy was weakened.

## Scope, timing and profiling

On the current owner-state copy, the private tracking plan for white at visible 20/source 19 has hard keys at **visible 19/source 18** and **visible 35/source 34**. The mutable interval is source 19–33: **15 analyzed frames**. The copied current decisions produce 0 matched/15 unresolved in this interval; the repair does not change that result or invent assignments. Deleted review decisions and prior-region reconciliation remain governed by the existing engine algorithm.

| Measurement | Result |
| --- | --- |
| Owner foreground symptom before fix | Frozen/very slow, busy ownership warning; no finite owner stopwatch reproduced by GUI automation |
| Installed native registration before fix | Both deferred operators missing modal/cancel at registration |
| Original tracking body, background mode | **1.662 s**, FINISHED, exact Restore afterward |
| Source repaired foreground branch | **1.708 s**, FINISHED, ownership cleared |
| Installed repaired foreground branch | **1.697 s**, FINISHED, ownership cleared |
| Installed pre-start setup | 0.000129 s; zero engine calls before the test delivered the timer callback |
| Installed lost response after real commit | **2.112 s**, FINISHED, one mutation plus status recovery, ownership/pending state cleared |

Foreground branches were exercised with a test window-manager scheduler and direct callback delivery in background Blender. Native registration was verified separately against Blender's real register_class boundary. The configured timer interval is 60 ms. Actual desktop event-delivery delay, redraw/pixel latency and final manual acceptance are not claimed as automated measurements.

Normal execution: **2 child processes**, one `tracking-update`, one `review-observation`; **one reconciliation** for the one selected scope. No per-frame/key/Character subprocess loop. Each child loads ProjectState once (two loads total). Normal pre-fix body: tracking child 1.043 s, of which ready startup 0.068 s and request/response round trip 0.965 s; observation 0.537 s, startup 0.090 s and round trip 0.436 s. Installed after-fix child totals 1.063/0.552 s, with about 0.082 s additional public validation/presentation work. These body times are effectively unchanged: the fix makes native dispatch reach that fast path.

Private cProfile instrumentation was confined to diagnostic child launchers, preserving the one-shot wire boundary and importing no private implementation into Blender. It inflated the overall diagnostic run to 2.820 s. Representative **profiled** inclusive timings, which overlap and must not be added: ProjectState load 0.288 s; state JSON parse/validation 0.275 s; tracking-domain orchestration/cache/trial-state work 0.285 s; boundary/scope planning 0.000209 s; actual reconcile 0.00211 s; protected Restore capture 0.206 s; state serialization 0.0637 s; atomic publications 0.0403 s (seven calls: six journal/metadata writes plus project publication). Observation child: load about 0.296 s, observation build about 0.668 s with instrumentation. Counts/phases are diagnostic, not wall-clock acceptance thresholds.

The old d16d497 public path called the private backend directly in-process inside a review transaction, then refreshed. The bounded algorithm and explicit invocation remain the same. The separated path adds the accepted one-shot startup, revision/journal/protected-Restore work and a neutral observation, but no inference or additional reconciliation. The UI polish wrapper added an avoidable native registration-order error. Cached tracking is still fast; no daemon or direct-import shortcut is warranted. Observation/Restore work remains necessary for the current authoritative/recovery contract and was not deferred speculatively.

No provider/model inference, body reconstruction, SAM3, camera/depth solve, generation planning or retargeting was called. No generic provider job was launched. Native animation was not regenerated or edited. No protocol, schema, storage, tracking boundaries, five-second fallback horizon, selected-Character scope or pending/incorporated semantics changed.

## Validation

- **883 public tests passed**, zero failures/errors/skips (~120 s); includes the new fresh-process native registration test.
- **537 private tests passed**, zero failures/errors/skips (~47 s), in the established OpenCV-capable environment. Covers hard boundaries, small/default-horizon intervals, multiple pending edits in a prior region, unchanged/no-op updates, assignment preservation and state roundtrips.
- Installed fresh registration proof: both deferred operators have modal/cancel/wrapped=true **before** native registration; zero private host imports.
- Installed **14 lifecycle/UI tests passed**, covering success, ESC, failed startup, exceptions, load/reset cleanup, context changes and the preceding confirmed-edit repairs.
- Installed real Update Tracking on the current QA-state copy completed; the UI busy predicate was true before callback delivery and false afterward; pending recovery cleared. Identity decisions and repair ranges remained identical, and protected Restore recovered exact original project bytes.
- Installed tracking-specific lost-response test discarded the reply after the actual engine committed: calls were exactly `tracking-update`, `mutation-status`, `review-observation`; no duplicate reconciliation/submission, no residual busy/pending state, and exact Restore passed.
- Full suites also exercise real child process death/lost replies, journals/recovery and native Save/Reopen/Save As/Restore, generation and Depth/retarget/export safety. No claim of a fresh inference run, a live tracking-child kill at every fault phase, or a native interactive close/reopen under a frozen foreground UI.
- All 75 canonical owner files remained hash-identical. Native Depth/animation remain untouched; testing never writes the canonical QA file or companion. Compilation and diff sanity checked.

An initial auxiliary native-handler probe added a second handler and crashed during teardown; it was discarded as an invalid diagnostic, not treated as evidence of an owner/production crash. Follow-up proofs capture registration without creating handlers and exit normally. Other harness setup errors (reused diagnostic directory and instrumentation import order) were corrected without product changes. Final test totals above are successful suite results.

## Commit, deployment and manual QA

Source/test commit: **e4e5ea996b6d32749328e2e58f9a858506e47c0d**, `Register deferred tracking callbacks before native operators`. Only production files changed are `blendcap/registration.py` and `blendcap/vta_review_ui.py`; tests are `test_blendcap_vta_busy_registration.py` and `vta_busy_registration_native.py`. This report is recorded in a subsequent local documentation commit. Private core is unchanged at `7576ec948919f9a0c35072776f54b740747b9264`.

The two committed production files were deployed to the installed public adapter and its matching adapter-staging tree in the existing 20261031-16c12a0-f1f27f3 deployment. Exact old files and deployment metadata are preserved in `checkpoints/dev-preview/vta_update_tracking_20261108/rollback`; earlier rollback snapshots remain intact. Explicit default installed runtime/engine roots are retained, with protocol 7/build 1.0.0-dev.m5. No developer fallback, private implementation import, authority bypass, mixed worker, model/configuration change, installer work, push, tag or release.

Manual QA remains **C:\Users\dsguf\Desktop\QA.blend**, based on that exact latest owner file and QA_vta. Preparation changes: **None**. Special modifications in owner data: **None**. Diagnostic snapshots must not replace it. Restart Blender, select white at frame 20 and retry Update Tracking. Verify it finishes promptly and the ownership warning clears. If an uncertain-operation recovery warning appears, use explicit Retry Reconciliation rather than deleting locks/markers. Other product workflows remain manually unvalidated; this handoff requests only the tracking retry.
