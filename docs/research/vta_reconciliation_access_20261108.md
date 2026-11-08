# Assignment recovery access — 2026-11-08

## Finding and scope

The owner's disabled Assign Marked screenshot initially showed no selected destination Character. Selecting the Character addressed that condition. The subsequent error was different: a durable assignment had committed, but the host retained unresolved presentation/recovery state. Retrying Assign is correctly blocked; it must not replay the saved edit.

The latest inspected canonical companion contains request `a0679263f0d74f9e937a084d65a07c31`, `review-batch`, action `assign`, white, source frames 19 and 21 (visible 20 and 22). Its authoritative receipt is applied at revision 9, matching the current engine head. These are the receipt's earlier batch items, not an assertion that the owner's later temporary marks at visible 26/29 have been applied. Candidate marks are session-only.

The recovery operator was registered in fresh installed Blender. Previously its button appeared only high in the parent Review and Animation sidebar. It had no VTA Commands menu entry. Python registration alone did not justify the earlier advice that F3 search would expose it. The owner reported no search result, and the actual foreground search index was not exercised through automation.

The original post-commit refresh failure was **not reproduced**. Unmodified installed recovery succeeded on the saved pending state in an independent copy. A fresh two-frame batch on copied data also committed and refreshed normally, using one review-batch call and its validated optional presentation reply. No speculative refresh, protocol, authority, storage or locking change was made.

## Bounded repair

- Expose Retry Reconciliation in VTA Commands, ahead of unavailable-review early returns; menu placement also supplies a search-discovery route.
- Expose the same recovery box at the start of Current Frame Review, including when the current review view is unavailable.
- Disable Assign/Suppress Marked submission while reconciliation is blocked, retaining marks and counts. Clear Marks remains a session-only action.
- Share a cheap in-memory `blocked()` predicate with the existing mutation guard. No disk/engine work occurs in recovery UI drawing.
- Keep recovery unchanged: verify project identity, inspect the exact request receipt/revision, refresh presentation, then clear the resolved intent. Never replay a mutation, ignore a superseding revision, or delete an unresolved marker to bypass protection.

Code/test commit: `9ff3f1b4e1261806b963a8971fc2be9c6f492250` — `Expose pending reconciliation beside review actions and in VTA menu`.

Production files: `blendcap/vta_review_ui.py`, `blendcap/vta_review_undo.py`, `blendcap/vta_marks.py`. Tests: `tests/test_blendcap_vta_ui_polish.py` (three new regressions).

## Verification

- Full public suite: **886 passed**, zero failures/errors/skips, 118.5 seconds.
- Full private suite: **537 passed**, zero failures/errors/skips, 39.8 seconds; private code unchanged.
- Fresh installed UI/lifecycle checks: **17 passed**, zero failures/errors/skips.
- Fresh installed recovery of the copied original pending request: **FINISHED in 1.505 seconds**. Exact calls: `project-state`, `mutation-status`, `review-observation`; **zero mutation calls/replays**. Timing is diagnostic, not a test threshold.
- Copied `project_state.json` remained byte-identical. Hashes of all **6,922 native animation curves**, including key coordinates, handles and interpolation, remained identical. Pending state cleared only in that disposable companion/in-memory Blender copy.
- Both recovery locations were exercised through their real draw callbacks with unavailable review data; buttons precede early returns and drawing issued no engine calls. No GUI input automation was used.
- Host private-import guard remained active during installed owner-copy proofs. Compilation and Git diff checks passed.
- All **84** canonical owner files remained hash-identical, with no files added or removed, including the canonical pending marker and receipt. No recovery was performed on the canonical companion.

Harness setup errors (a diagnostic Retry invoked after its copied intent had already been resolved, and a repeated assignment of already-assigned candidates) were corrected in disposable copies. They are not evidence of a production refresh fault. Final recovery proof starts from a fresh copy of the current owner state and asserts its call sequence and preservation.

## Deployment and owner handoff

The three public program files were copied to the installed adapter and matching adapter-staging tree of the existing `20261031-16c12a0-f1f27f3` deployment. Exact old files and deployment metadata are retained in ignored `checkpoints/dev-preview/vta_reconciliation_20261108/rollback`. Private engine, analysis, models and runtime configuration were not changed. No push, tag, release or installer work.

**Manual QA file:** `C:\Users\dsguf\Desktop\QA.blend`, with its `QA_vta` companion.

**Based on:** that exact latest saved owner file and companion; diagnostic snapshots were taken read-only before testing. The saved blend opens in Wrestling at frame 26; later foreground marks shown in screenshots are not assumed to be saved file data.

**Preparation changes to owner data:** None. **Special diagnostic modifications in owner data:** None. Diagnostic assignment/recovery copies under the ignored checkpoint directory must not replace canonical QA.

Restart Blender to load the updated adapter, reopen the same QA file, and choose **VTA Commands → Retry Reconciliation**, or use the button in **Current Frame Review**. Inspect the saved review keys before submitting any remaining marks. The original presentation failure remains unexplained; this handoff proves a safe explicit recovery path and corrects its accessibility, not that every possible original trigger has been eliminated.
