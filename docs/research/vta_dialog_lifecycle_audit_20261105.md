# Foreground dialog/busy lifecycle family audit â€” 2026-11-05

Baseline: public `e6c96e1b2ce9d9edf60006fd2ae0ceb73f819ca8` (the deployed Clear Selected Review Keys repair). This is a focused follow-up audit, not an architecture change or release. Follow-up fixes are **committed locally and deployed**, with installed validation complete; owner interactive QA remains pending. See the deployment receipt below.

## Findings and scope

Inventoried 34 operator classes with direct confirmation/file-selector or modal-timer calls across public addon source, including inactive legacy definitions for completeness. Traced the four shared busy-helper registrations and indirect delegation from Repeat Last Assignment to Assign's confirmation path. Inventory details are local in `checkpoints/dev-preview/vta_dialog_audit_20261105/inventory.json`.

| Path | Finding / disposition |
| --- | --- |
| Review management (clear, rename, delete) | Already uses direct confirmed execution after the preceding repair. Regression tests still pass. |
| Repeat Last Assignment | Delegates to Assign.invoke, which can display a replacement dialog, but was still installed with timer deferral. Same callback-handoff risk. Source now uses direct execution with cursor cleanup, including the ordinary no-confirmation route. Assignment validation and frame advancement remain unchanged. |
| Assign Marked / Suppress Marked replacements | Confirmation returned to execute, which always started another modal handler. Source now applies a confirmed batch directly, preserving its checkpoint, scope, evidence and source-alignment validation. Ordinary unconfirmed batches retain their existing deferred route. |
| Short-edit helper (Update Tracking / Create Character) | No confirmation path found. Reproduced setup-failure leak: failing modal registration left the busy flag/timer alive. Added idempotent cleanup using the saved window/manager, plus native cancellation cleanup. ESC, load/reset and frame-change cancellation tested. |
| Marked-candidate cancellation/setup | ESC was ignored by the modal implementation; failed handler setup could leave busy true. Added cancellation that retains unsubmitted marks, and timer cleanup on setup failure. Cancelling a dialog before execute does not try to clean up nonexistent work. |
| VTA retarget | Not wrapped by the short-edit helper. Its dialog still leads into a genuinely incremental, cancellable native bake. Timer/modal registration failure left its running guard set; source now closes the stream and clears resources if setup fails. Completion and ESC state-machine tests pass. **Dialog-to-bake handoff remains a structural risk requiring separate native foreground verification or a designed handoff repair.** We did not make a long bake synchronous or change Undo/publication semantics. |
| Ordinary Assign / Mark Review State / candidate suppression | Confirmation executes directly; no shared-helper timer handoff found. |
| Depth-mode confirmation / Load Scene / Clear Source / file selection | No shared-helper timer handoff found. File-selector modal invocation is an intentional Blender-owned UI lifecycle. No changes. |
| Source-calibration/job decisions / camera restore | Confirmation returns terminal status while an independent job controller or application timer continues the job. No second modal handler on the dialog operator found. No changes. |
| Native selection, legacy retarget/FK-IK and other timer definitions | Selection timers are interaction lifecycles. The inspected legacy bake timers start from invoke rather than from a confirmation execute callback. No changes or restoration of retired UI. |

The two additional short-review paths reproduce the same scheduling contract problem as Clear Keys: a confirmed execute callback can return RUNNING_MODAL before applying the edit. Tests now require a terminal result and exactly one application without a second timer/modal registration. This is a family of UI integration errors, not evidence of slow per-key IPC or incorrect private/public authority ownership. It is not a blanket certification of every interactive Blender workflow.

## Source repairs

- `blendcap/vta_review_ui.py`: select direct busy execution for Repeat Last Assignment, preserving the preceding management repair and deferral for non-dialog short edits.
- `blendcap/vta_marks.py`: complete confirmed marked replacements directly; retain the normal deferred route; release resources on failed setup, ESC, completion and errors; retain marks on failed/cancelled submission.
- `blendcap/vta_busy.py`: consolidate idempotent release; save the original timer manager/window; clean up on setup/redraw errors, native cancellation, load/reset and terminal completion. Do not clear a different active operation's guard.
- `blendcap/vta_retarget.py`: clean up only failed timer/modal-handler setup. Native bake scheduling, cancellation, publication and Undo semantics otherwise unchanged.
- `tests/test_blendcap_vta_dialog_lifecycle.py`: seven focused tests covering direct repeat completion, cancelled preparation, both confirmed marked operations, failure/ESC mark preservation, failed timer setup, helper cancellation/load/context changes, and retarget cancellation/completion/setup failure.

No private implementation imports, new engine service, protocol change, project mutation shortcut, private history, schema change or provider routing change. No production owner animation or project data was used as a write target. No Computer Use, keyboard/mouse automation or UI event injection.

## Validation and remaining work

- Full public suite: **882 passed**, zero failures/errors/skips (~115 seconds).
- Focused UI/lifecycle: **14 passed**, zero failures/errors/skips; repeated after actual source-addon registration to exercise real registration policy rather than only individually wrapped test classes.
- Regression oracles are terminal results, exact application counts, absence of additional modal/timer handoffs for confirmed short edits, preservation of marks on cancellation/failure, and cleared resources/guards. No wall-clock acceptance thresholds.
- Public suite includes review transactions, recovery, generation, native Depth editing and retarget/export coverage. No fresh model inference or full owner visual workflow was run.
- Private code is unchanged. The private suite was not rerun for this source-only public follow-up; the immediately preceding Clear Keys milestone passed 537 private tests. Do not count that as a fresh private run here.
- Compilation/diff sanity and read-only owner preservation checks completed at the end of the pass.

The retarget dialog entry deserves a separate bounded follow-up: preserve its incremental bake, transactional publication and native Undo while testing or correcting how confirmed settings hand off to the bake. State-machine tests alone cannot establish native popup/event-loop behavior. This audit intentionally does not claim that remaining risk is fixed.

The audit initially left source changes undeployed; the subsequently authorized deployment is recorded below. Owner QA.blend/QA_vta are not replaced, regenerated or migrated. No new owner-facing QA filename or manual setup is introduced.

## Fast retarget follow-up

The owner authorized a quick focused check. **No additional retarget stall was reproduced, and no larger handoff rewrite is justified by this check.**

The normal VTA Retarget sidebar button explicitly sets `operator_context = 'EXEC_DEFAULT'`. It bypasses `invoke()` and its settings popup, using the rig/range choices already shown in the panel. This explains why that ordinary workflow can work independently of any popup concern. The alternate invoke/search-menu entry still opens the settings dialog.

A small background Blender proof exercised the actually registered native RNA operator through `bpy.ops.blendcap.vta_retarget('EXEC_DEFAULT', ...)`, with its real native event timer and modal registration. Only the bake/export generator was replaced with five harmless yields. The operator instance remained valid after the execute callback and garbage collection; four steps remained RUNNING_MODAL, the last step completed FINISHED, and both the timer and running guard were cleared. A second invocation cancelled before any work and also cleaned up. The UNDO declaration remains present; this is not a claim of testing the actual foreground Undo stack.

Modal methods were stepped directly as test functions. No native UI events, mouse/keyboard input or popup were automated. Thus this proves native RNA entry, instance lifetime and cleanup, but **not actual popup-to-timer event delivery**. Initial test scaffolding incorrectly mocked an RNA poll callback; it was corrected to use the real registered poll with only loaded-context/lock helpers stubbed. The final proof passed and exited normally. Local evidence: `checkpoints/dev-preview/vta_dialog_audit_20261105/retarget_entry_probe.json` and its log/script.

No additional production changes, full bake, deployment or commit were made for this quick follow-up. The earlier remaining-risk wording should be read as an unverified alternate-entry concern, not a demonstrated failure of retargeting. The smallest remaining check is owner manual confirmation through the alternate search-menu dialog; ordinary button operation and interactive popup behavior must not be conflated. Canonical QA files remain untouched.


## Authorized commit/deployment receipt

Source/test commit: **6016d1db58e444722570c44fd6947eb9f483afd4**, `Complete confirmed review edits and release busy resources`, on `codex/mixamo-retarget-v1`. Private core remains **7576ec948919f9a0c35072776f54b740747b9264**, unchanged. The documentation receipt is a separate local commit following installed validation; no push, tag or release.

Focused source validation was rerun after real addon registration: **14 passed**, zero failures/errors/skips. The prior full audit validation of **882 public tests** applies to the same source/test changes. No additional production changes were introduced during deployment.

Four committed public files were deployed to the existing installed adapter and its matching adapter-staging copy: vta_busy.py, vta_review_ui.py, vta_marks.py and vta_retarget.py. Retarget's only change is exception cleanup for a demonstrated timer/modal-handler startup failure (its guard previously remained set). No retarget bake, popup handoff, publication, mapping or Undo redesign was made. The normal Retarget button remains previously passed; alternate invoke/search-menu popup still needs the short manual check described above.

Installed adapter: `C:\Users\dsguf\AppData\Roaming\Blender Foundation\Blender\5.2\extensions\user_default\blendcap`. Engine/staging deployment: `C:\Users\dsguf\AppData\Local\Programs\VTA\deployments\20261031-16c12a0-f1f27f3`. Existing default trusted runtime config and explicit engine/control/provider roots retained; protocol 7 and build 1.0.0-dev.m5 unchanged. No developer production fallback, private implementation import, authority bypass or legacy worker path. Private engine, analysis source, models and runtime configuration were not edited. Installed files match the local source commit and compile successfully; registration/default-runtime checks passed.

Exact previous installed/staged program files and deployment metadata are backed up in `checkpoints/dev-preview/vta_dialog_deployment_20261105/rollback`. Earlier Clear Keys and pre-M5 rollback snapshots remain intact. Only the four inventoried public program files were copied; no installer work or broad deployment-tree replacement.

Installed automated results:

- **14 focused tests passed**, using the actual installed registered namespace and default runtime. This covers Clear Keys terminal completion, Repeat delegation, confirmed assign/suppress marked callbacks, stale/failure/cancel mark retention, timer setup failures, busy cleanup, retarget completion/cancel/setup cleanup and native export preservation.
- Real separated-engine operations on a verified disposable copy of current owner companion state, under the host private-import guard: confirmed **Assign Marked** and **Suppress Marked** each executed exactly one review-batch mutation. Success cleared submitted marks and busy/pending state; protected Restore returned byte-exact original project JSON after each batch.
- Forced stale-checkpoint confirmed submission returned CANCELLED with marks retained, **zero engine calls**, no project publication and no stuck busy state.
- Real **Repeat Last Assignment** preparation/delegation reached replacement confirmation; confirmed execution made one source-coverage read and exactly one review-reassign mutation. It proceeded to the next frame, updated the intended Character and cleared busy/pending state. An initial test-only attempt to use a protected Restore slot after ordinary reassignment was corrected: reassignment uses explicit correction, not the destructive-batch Restore slot. No product behavior was changed for that test expectation.
- Separate actual installed **Clear Selected Review Keys** foreground-branch proof reached exactly one review-management mutation, completed without a second modal wait, cleared busy/pending state and restored the disposable project byte-exactly.

Confirmation display was substituted and foreground branches were explicitly exercised in background Blender; no popup/event injection or mouse/keyboard automation. These checks establish installed callback/engine behavior, **not manual native popup/event-loop acceptance**. Disposable synthetic scenes/copies and injected failures are diagnostics and must not replace owner QA.

All **73 owner QA files remained hash-identical** during this deployment pass. Manual QA file stays `C:\Users\dsguf\Desktop\QA.blend`, based on that exact latest owner file and QA_vta companion. Preparation changes: **None**. Special owner test modifications: **None**. Owner last-known-good project recovery point was not replaced.

Next manual QA is restricted to: (1) Clear Selected Review Keys; (2) Repeat Last Assignment replacing an existing decision; (3) Assign/Suppress Marked requiring replacement confirmation; (4) alternate Retarget popup if convenient, checking finish/cancel. Restart Blender first. **The rest of the newly deployed product remains manually unvalidated; passing these checks does not establish full post-deployment QA.**
