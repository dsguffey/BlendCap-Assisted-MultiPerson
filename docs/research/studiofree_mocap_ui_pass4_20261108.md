# StudioFree MoCap — bounded UI Pass 4

Implemented and locally deployed for owner UI review. No push, tag, release or installer work.

## Interface and selection

Top-level order: **MoCap Setup → Analyze → Character Management → Character Assignment → Update Tracking → Animation → Overview → Export / Retarget → Resources**.

Character Management contains Create New Character, Available Characters, Rename Character and Delete Character. Available Characters is session-only presentation state, scoped to the current Scene/source. It does not change assignment target, Candidate, marks, generation/tracking selection, native object/preview selection or retarget selection. Creation selects the new management entry without selecting it for assignment. Rename/Delete use that management entry and preserve the existing authoritative mutation, confirmation and recovery paths. They remain disabled without a valid management selection. No project schema or migration was introduced.

The assignment-target Character selector is immediately above Candidate selection. Lifecycle controls and the Repeat sidebar button are removed from Character Assignment. Occluded, Not Visible and No Valid Candidate remain unchanged. Existing assignment/preview/timeline selection coupling is retained; this pass isolates management selection rather than redesigning those established interactions. Setup, Overview, generation and tracking behavior remain otherwise unchanged.

## Repeat and workspace behavior

Repeat Last Assignment creates an ordinary pending assignment mark and advances one frame, clamped at the source end. It requires the assignment target to match the last applied Character and a valid current Candidate; it does not silently switch the target of other pending marks. Normal replacement/conflict marking behavior is shared. Repeating an identical valid mark is idempotent; stale evidence is rejected without advancing or changing the mark. Navigation failures roll back the pending-mark change. Apply Marked remains necessary for authority.

The existing Repeat operator and Preview J shortcut remain, with Repeat Last Assignment Mark available in the MoCap command menus, including the opted-in Dope Sheet. Normal Apply Marked records the last applied target. Repeat does not start an authoritative operation or a modal busy handoff.

Explicit Create MoCap Workspace still appends a distinct preset each time and assigns that exact new workspace to the invoking window. Normal Blender RNA also requests an open 3D View sidebar and the MoCap tab. Sidebar failure is non-blocking. Native background tests verify distinct appended workspaces and sidebar visibility flags; a real-workspace/proxy-window test verifies the exact window assignment. Background Blender reports the sidebar category as UNSUPPORTED and defers visible workspace switching, so visible tab focus/category still require owner UI review. No context hacks or workspace creation on registration were added.

## Validation

- Full public suite: **906 passed**, zero skips/errors/failures. Later focused regressions cover the final stale-mark guard and optional-sidebar failure handling.
- Final focused suite: **92 passed**, zero skips/errors/failures, including foreground Repeat lifecycle without a timer/modal handoff.
- Installed unit suite: **21 passed**, zero skips/errors/failures. Counts overlap the source suites and are not additive unique coverage.
- Fresh source and installed Blender registration/draw smoke: all nine panels draw and ten existing operator IDs resolve. Native management EnumProperty changes preserve other selections in both directions. Repeated explicit workspace creation succeeds without changing the Scene.
- Real engine IPC lifecycle proof on fresh disposable copies: Create/Rename/Delete select and mutate the management target through existing requests; original review/suppression/tracking/repairs and all native animation-curve hashes remain unchanged. Repeat advances 27 → 28 with **zero engine calls** and unchanged project-state/analysis-cache bytes. Only subsequent Apply Marked commits the assignment. A stale pending cache hash is rejected without writes or advancement.
- Private implementation import guard remained active in host checks. No private production code, protocol, provider, runtime configuration or persistence changes were needed. Compilation and diff checks passed.
- Old test assertions were updated for the preceding product naming pass and explicit management selection. Initial disposable lifecycle harness failures were recorder-variable/fixture-reuse errors; corrected fresh-copy runs passed. Background shutdown reported a small allocation warning with exit zero; no foreground memory claim is made.

Implementation commits: `5eda92fa645f71f279fd6f5a894fc961eff7bdd9` (management isolation and mark-only Repeat) and `01018c4` (stale Repeat evidence guard). The final report/test commit follows these. Production files: `blendcap/registration.py`, `blendcap/vta_review_ui.py`, `blendcap/vta_marks.py`, `blendcap/vta_workspace.py`, and new `blendcap/vta_management_selection.py`; focused tests accompany them.

## Deployment and owner preservation

The five UI files are deployed to the installed addon and deployment adapter-staging. Pre-pass files and metadata are retained under ignored `checkpoints/dev-preview/vta_ui_pass4_20261108/rollback`, including the absence of the new management-selection module. Final installed/staging hashes, deployment closure and canonical owner preservation are checked by the adjacent finalization report. Engine and analysis payloads are unchanged.

The owner-facing file remains **C:\Users\dsguf\Desktop\QA.blend** with its existing **QA_vta** companion. No owner preparation, migration, recovery, regeneration, assignment recreation or diagnostic modification was performed. The starting manifest covers 85 files; final verification checks both their hashes and the complete file set. Internal lifecycle copies contain deliberate test mutations and must not replace the owner file.

Restart Blender and continue interface review in the existing QA file. Check management/assignment separation, Repeat mark-and-advance followed by Apply Marked, and visible workspace/sidebar focus. This is UI Pass 4 review readiness, not broader product acceptance.
