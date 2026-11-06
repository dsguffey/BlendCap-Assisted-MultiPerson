# Clear Selected Review Keys regression — 2026-11-05

Status: bounded public repair deployed; owner foreground confirmation QA remains required. This investigation does not manually accept other workflows in the newly deployed build.

The foreground busy wrapper deferred the confirmation dialog's execute callback into a second modal handler. It could return RUNNING_MODAL without executing the edit, retaining the busy state and cursor. Background Blender bypasses this wrapper, explaining the initially fast background results. During the owner's live stall, read-only inspection found no engine child, pending mutation marker or new mutation receipt. This points to the foreground handoff rather than expensive deletion or corrupt project data.

The exact pre-fix wrapper reproduced the callback/liveness defect in a confirmation-callback harness: one timer, a second modal handler, RUNNING_MODAL, zero edits and busy retained. No GUI events were automated. The owner's literal >10-second native foreground wait was not recreated with mouse/keyboard automation; owner acceptance of the full native dialog/event loop remains necessary. The regression tests instead assert terminal completion, exactly one batch mutation, no second timer/modal handler, cursor cleanup on failure and duplicate-submission protection. Blender's upstream source also identifies modal-from-exec as bad practice: [dialog callback](https://github.com/blender/blender/blob/main/source/blender/windowmanager/intern/wm_operators.cc), [operator execution](https://github.com/blender/blender/blob/main/source/blender/windowmanager/intern/wm_event_system.cc). This supports the lifecycle repair; it is not evidence of a captured live native stack.

## Repair and ownership

`vta_busy.install(..., defer=False)` provides an exception-safe synchronous wait cursor for confirmed management execution. Registration uses this path only for BLENDCAP_OT_vta_review_manage. Other operators retain their existing deferral. The shared management operator's other confirmation actions receive the same lifecycle correction, with unchanged semantics. No speculative changes to other operators were made.

Clear Selected Review Keys removes only selected durable authoritative identity/review decisions across frames and Characters, returning them to No Decision. It does not remove skeletal, retarget or native Location/Depth animation. Existing tracking invalidation rules still apply. The pre-separation baseline d16d497 also used private review-management/persistence; this was never a purely native animation edit.

The private one-shot transaction, revision/CAS checks, confirmation checks, file-backed observation, durable pending recovery and protected Restore are unchanged. No direct private import, authority bypass, mixed worker, persistent engine or protocol/schema change was introduced. Native Depth Undo/Redo remains Blender-owned. Current accepted authoritative review removal uses explicit protected Restore, not native Ctrl-Z replay; that policy was preserved.

## Measurements

| Case | Result |
| --- | --- |
| Owner's actual foreground report | >10 seconds / still busy; no engine child when inspected |
| Pre-fix foreground callback harness | RUNNING_MODAL, zero edits, busy retained; requires a second modal dispatch |
| Repaired callback tests | FINISHED, exactly one batch edit, zero new timers/modal handlers |
| Original body, background saved QA, one / all 55 keys | ~1.75 / ~1.77 seconds |
| Screenshot subset, original body with foreground wrapper bypassed | 1.718 seconds |
| Screenshot subset, repaired source foreground branch | 1.698 seconds |
| Screenshot subset, repaired installed foreground branch | 1.767 seconds |
| Forced dependency-graph evaluation after installed edit | 0.0116 seconds |

The screenshot-specific proof used white's 12 keys at source frames 18–29 (visible 19–30), the actual VTA Dope Sheet context and playhead 125. This approximates the pictured key box; the image does not establish exact selection endpoints.

Normal execution: one review-management mutation process and one review-observation process, independent of key count. Installed mutation total 1.129 seconds; observation 0.558 seconds. Earlier phase measurements showed ~0.07-second mutation startup, ~1.03-second round trip, ~0.09-second observation startup and ~0.44–0.46-second observation round trip. Public observation decode/validation is ~0.02–0.03 seconds. No per-key process, native key deletion, native Undo snapshot or production explicit dependency-graph update occurs. One batched project publication, one protected previous-state capture and the existing journal/intent writes remain. Clear itself does not Save the blend file.

The underlying transaction still takes ~1.8 seconds, above the aspirational <1-second target. This repair removes the foreground scheduling stall rather than claiming to eliminate one-shot boundary overhead. A deliberately overlapping asynchronous observer caused redundant observations (~3 seconds, four children), but did not reproduce the reported stall and was not optimized speculatively.

## Validation

- Full public suite: **875 passed**, zero failures/errors/skips (~117 seconds).
- Full private suite: **537 passed**, zero failures/errors/skips (~44 seconds), using the existing body/runtime test interpreter. An initial bare-Blender-Python run lacked cv2; it was rerun in the same existing environment used for the preceding milestone. No dependencies or private code changed.
- Focused observation/fastpath: 15 passed. UI polish/lifecycle: 7 passed, including two new tests.
- Native Depth group/single deletion and drag Undo/Redo passed with VTA mode on/off, three cycles per operation, zero engine-history calls.
- M3 workflow passed: authoritative edits, protected Restore, pending recovery, Save/Reopen, independent Save As, native history isolation and private-import guard.
- Actual installed extension/default runtime passed the screenshot-specific edit with the repaired foreground branch explicitly exercised. No busy state remained. One-key, 55-key and 12-key scopes were exercised in disposable owner-state copies.
- Protected Restore recovered byte-exact project JSON in timing proofs.
- Native Action coordinates, handles, interpolation and selection were unchanged by clearing; Save/Reopen passed. Diagnostic-only fake users retained orphan Actions that normal native Save otherwise drops. This modification exists only in an internal diagnostic copy.
- Full suites cover generation, retarget/export and assignment/suppression safety. No new provider inference or full fresh-clip QA was run for this two-file UI repair.
- Compilation, diff sanity and installed/source byte matching checked at completion. No automated foreground pixel-latency acceptance is claimed.

## Deployment and owner preservation

Two public program files were updated in the installed adapter and its matching adapter-staging copy: `blendcap/vta_busy.py` and `blendcap/vta_review_ui.py`. Private engine, runtime configuration, interpreters and models are unchanged. Exact old files and deployment metadata are backed up under `checkpoints/dev-preview/vta_clear_keys_20261105/rollback`; restore the two files in each recorded destination to roll back. The earlier pre-M5 rollback remains intact. No manual file copying or canonical migration is needed.

QA.blend and all authoritative/native owner data remain hash-identical. Of 73 initially inventoried files, 72 are unchanged. One disposable `.vta-engine/views` observation artifact rotated at 19:12:38 during the owner's restarted Blender session (process started 19:12:32): the old view disappeared and a new view appeared. This is recorded rather than falsely claiming byte identity for an actively used transient cache.

Manual QA file: `C:\Users\dsguf\Desktop\QA.blend`, based on that same latest owner file and QA_vta companion. Preparation changes: **None**. Special test modifications in the owner file: **None**. Internal diagnostics must not replace the canonical file.

Files changed: the two public modules, `tests/test_blendcap_vta_ui_polish.py`, and this report. Private repository unchanged. Local public commit only; no push/tag/release or unrelated UI changes. Next: restart Blender, open canonical QA.blend, repeat the pictured batch clear, verify completion/cursor recovery, and optionally Restore Last Protected Edit to retain the test keys.
