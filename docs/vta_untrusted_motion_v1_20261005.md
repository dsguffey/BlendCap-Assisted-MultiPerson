# V1 Cleanup (backend Untrusted Motion) — implementation and QA handoff

## Current Cleanup UX checkpoint — 2026-10-09

Cleanup Range is the user-facing name for a Character-specific interval where
automated motion is not trusted and requires animator attention. Internal
`untrusted_motion` and ProjectState 0.6 semantics are unchanged.

Normal channel-row click selects only that Character and activates it. Ctrl-click
toggles membership; newly selected becomes active. Removing the active member
chooses the first remaining displayed Character, or none. Channel-region drag
selects Character rows (Shift/Ctrl adds); timeline-region drag still selects keys.
Blank channel click/box clears the set unless additive. The set belongs to that
Dope Sheet/source session and is never persisted. Only its active member is shared
with Preview/sidebar. Preview selection does not import bulk targets into a Dope
Sheet. Source/project changes reset the contextual set; frame refresh preserves it.
Native list-item theme colors distinguish selected and stronger active rows.

Cleanup alone is bulk-capable: Mark/Clear target exactly the selected rows, with
no sidebar fallback when empty. The private command locks, validates the expected
project hash, edits all targets in memory, and publishes once; errors cannot save
a partial bulk operation. Rename/Delete/Clear Assignment/Update Tracking remain
single-active-Character operations; existing destructive confirmations remain.
Review-key commands target their own selected key IDs. Selection does not move
the playhead; Go to Review Key remains explicit. Cleanup hatching is paint-only.

Cleanup Start / Cleanup End remain editable inclusive Blender-frame fields.
Start = Current Frame changes only Start; End = Current Frame changes only End.
Neither authors a range. VTA Commands mirrors both boundary actions, Mark for
Cleanup, and Clear Cleanup Range using the same operator. No hotkeys added;
explicit action identifiers allow later bindings. Preview status is
**Animation: Needs Cleanup**; spans use **NEEDS CLEANUP** where text fits.

Tracking ticks are state-only: brighter cyan-grey = accepted `visible` match;
dim grey = unresolved derived record (all non-visible states use the dim color).
They are not confidence, selected-key, or active-row indicators. Two small help
lines in the N-panel explain this. Candidate selection without a Character still
works and never invents one; Assign still requires a valid Character.

Verification: 17 new public tests, three new private tests. Public **371 total,
365 passed, six existing skips**; private **233 passed**. Focused VTA public
**184 passed**, private Cleanup **21 passed**. Compilation and whitespace checks
pass; three source and three installed headless lifecycle cycles pass, with five
runtime files hash-synced after backup. Full-suite shutdown retains Blender's tiny
baseline unfreed-block diagnostic; tests/process pass and lifecycle exits cleanly.
Live visual acceptance remains user QA. Backups live
under `checkpoints/dev-preview/vta_cleanup_selection_20261009/backups/`.
No native desktop control, inference, tracker tuning, Recovery or push.

Pre-1.0: reversible Suppress/Ignore Candidate (retain raw detector evidence),
and separately easier overlapping-box selection (cycling/context list).
Neither is implemented here. Hotkeys also remain future work.

VTA is an animation-assistance system, not a promise of fully automatic
reconstruction. The user marks a Character's motion **Untrusted** when visual
evidence cannot support trustworthy automation, deliberately handing the interval
to the animator. **Hold** is the downstream response, not the authoritative state.

## Active baseline and archive

Both active branches are `codex/untrusted-motion-v1`, based on public
`9cd19fcdeef7330744e4f91493a9a33fb2645b14` and private
`39484e805db2c7e9b19606988475bec8cbba2aee`. The approved UI/research stay intact.
Only the general source-isolation invalidation fix was salvaged from Recovery;
multi-revision dependencies/visibility remain deferred. A compatibility guard
refuses to edit recovered projects rather than silently ignore evidence ownership.
No manual boxes, threshold/scoring changes, inference, automatic reacquisition
or push. Public/private original Recovery branches/tags remain unchanged.

Archive: `C:/Design/AI/Projects/Video to Animation/Recovery Archive 2026-10-05/`.
See verified full-history bundles, `archive_manifest.json`, and
`RECOVERY_REINTRODUCTION_HANDOFF.md`. Recovery is deferred, not rejected: boxes
can repair observations but not genuinely missing pose information. Same-provider
Redetect did not improve merged 67/73/81. Ignored artifacts remain at recorded
local paths; they are not included in Git bundles.

## Storage and user workflow

Authoritative ProjectState **0.6** loads 0.5 read-only with zero intervals.
Character/source/inclusive source bounds are independent of identity keys;
keys remain stored, visible and selectable. Overlaps union for the same Character;
adjacent ranges remain separate. **Clear Cleanup Range** subtracts inclusive bounds,
possibly shortening/splitting/removing. Clearing changes eligibility only, never
revives invalidated predictions or runs Update Tracking.

Select Character rows; in the existing VTA Dope Sheet `N` sidebar use
**Cleanup → Cleanup Start / Cleanup End → Mark for Cleanup**. Bounds are visible
Blender frames, converted using the existing offset path. Separate current-frame
actions set each boundary. Patterned spans, boundary strokes and NEEDS CLEANUP text
(where it fits) draw on that Character's row. DPI/text size/zoom share existing
row geometry; paint does not intercept key hit testing. Preview/sidebar shows
**Animation: Needs Cleanup** separately from assignment/presence state.

Propagation clips to Character-specific barriers. Marking retires inside records
and dependent paths crossing the interval, retaining evidence/history. Independently
bounded regions and other Characters remain valid. Unscoped legacy records retire
conservatively for that Character/source. After separation, explicitly confirm an
assignment anchor and Update Tracking; neither interval ending nor clearing creates
trust. A future Recovery cannot clear motion truth automatically.

Downstream V1 Holds the last trustworthy articulated pose AND root transform;
no smoothing, interpolation or hallucination. No preceding reconstructed state
means unavailable, not an invented pose. This milestone defines that contract;
it does not implement reconstruction. Private details: `docs/untrusted_motion_v1.md`.

## Verification and disposable QA

Pre-feature gate: private 212 passing (one salvaged regression), public 343 tests
(337 passed / six existing skips). Added tests exercise intervals/authority,
independence, barriers/local retirement, post-anchor resumption, persistence,
migration, stale requests/locking/failures, UI routing/status, inclusive cells,
scaled paint geometry and unchanged key selection. Final gate: private **230
passed**, public **354 tests / 348 passed / six existing skips**. Focused suites:
18 private and 11 public tests pass. Compilation, whitespace checks and exact
comparison of tracker metrics/matching/constants against pre-Recovery pass.
Public and installed add-ons pass three headless lifecycle cycles each; all
seven synchronized runtime files match by hash after preserving the old files
under the archive's `installed-v3-runtime/`. Full public-suite shutdown prints
Blender's small unfreed-block diagnostic; tests and process exit pass, and focused
and lifecycle runs exit cleanly. No live visual acceptance is claimed.

QA fixture:
`checkpoints/dev-preview/vta_untrusted_motion_20261005/fixture/VTA_Overlay_Wrestling.blend`.
Yellow Blender 67–90 and White 70–85 are mechanical QA ranges, NOT new semantic
truth. All original files/caches/scoring artifacts and identity keys are unchanged.
Three-Character synthetic tests prove valid tracking independence. The extra
fixture QA Character has no invented identity/pose. An unsaved working clone
tests arbitrary post-anchor mechanics without claiming candidate correctness.
Headless reopen/backend/real View2D and lifecycle checks pass; live acceptance
remains user QA. No native desktop control.

## Manual QA

After restarting Blender, use the disposable fixture above in VTA Mode (`N`).

1. Select one Character, Ctrl-select a second; confirm one is visibly active.
2. Use current-frame Start and End at two playhead positions.
3. Mark for Cleanup; confirm both selected rows receive the range.
4. Deselect one Character; confirm subsequent Cleanup affects only the remaining row.
5. Glance at the tracking ticks and their small explanation.

Next milestone: user live QA and narrow repairs only. No automatic smoothing,
manual-box Recovery, threshold tuning or reconstruction without separate approval.
