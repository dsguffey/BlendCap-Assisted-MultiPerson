# V1 Hold Animation (backend Untrusted Motion) — implementation and QA handoff

## Missing-control registration repair — 2026-10-09

Manual QA found Show Suppressed but no Suppress button: the feature-module class
tuple was not the live registration tuple. The central `blendcap/registration.py`
now imports/registers Suppress Candidate, Generate Animation, its Animation panel,
and the existing Copy Analysis Error operator. No schema, private core, thresholds,
inference, identity or output semantics changed. The original lifecycle gate did
not assert these operators existed; the new tests fail on that original omission.
Two regressions verify complete declared-class coverage and actual Blender RNA,
restore properties, panel draw continuation, and unregister/re-register cleanup.
Public: 388 total / 382 passed / six existing skips. Source and installed each
pass three explicit RNA lifecycle cycles. Installed registration is backed up and
hash-synced; restart Blender. Candidate Assignment now exposes Suppress/Restore
beneath Show Suppressed Candidates, enabled when a current candidate is selected.
Evidence: `checkpoints/dev-preview/vta_registration_repair_20261009/`.

## Reviewed skeletal output checkpoint — 2026-10-09

Current terminology is **Hold Animation / Hold Range / Hold Start / Hold End**.
The following older checkpoints are historical. Current normative policy lives
in the amended Project Documentation DESIGN_BIBLE / ARCHITECTURE / STORAGE_CACHE.

- ProjectState 0.7 adds reversible revision/observation-scoped suppression;
  0.5/0.6 load without suppression. Raw detector caches are never edited.
- Assigned suppression requires confirmation, atomically clears the exact
  conflicting current key and retains that key in audit history. Only consumed
  observation tracking dependencies retire; restore requires explicit updates.
- Normal Preview hides suppression; Show Suppressed reveals subdued labelled
  boxes and Restore. Repeated clicks cycle containing boxes, resetting on
  source/frame/pointer/transform changes. No Character is required to inspect.
- Generate / Update Animation explicitly calls the installed existing
  Fast-SAM-3D-Body estimator with reviewed boxes, exporting Character NPZ/BVH
  and durable-ID-owned Blender armatures. No legacy person selection, detector
  inference, new review keys, automatic tracking or smoothing across Hold.
- Final evaluated pose AND root are constant through inclusive Hold and until
  a new post-Hold user candidate key. No prior successful pose is unavailable,
  never a fabricated rest pose. Other Characters are independent.
- Review changes retain older output and mark it stale. Generate explicitly to
  publish a new immutable run. Only owned projections in the target Scene are
  replaced; unrelated objects and other Scene projections survive.

Real installed CUDA/FP32 body output was verified: two distinct 54-bone rigs,
18 total reconstructed samples, 241 exact held Character-frame evaluations.
Current source review supports 17 white samples but only one yellow sample;
no unsupported moving output was fabricated. Model pose quality/visual
acceptance still requires human QA. Public suite: 386 total, 380 passed, six
existing skips; private: 249 passed. No scoring/threshold/keymap/theme changes.

Original canonical fixture is unchanged against the task-start copy. The older
repair report's project hash predates a user edit; it is not the task baseline.
Current output QA derivative (with matching companion):
`checkpoints/dev-preview/vta_output_20261009/final_fixture/VTA_Overlay_Wrestling.blend`.
Verification/backups: `checkpoints/dev-preview/vta_output_20261009/`.
Previous runs and model warnings remain there; no generated files are committed.

Restart Blender before QA. Only four checks: suppress/show/restore a bad
candidate; generate distinct Character rigs; mark a short Hold and regenerate
to inspect pose/root while the other Character continues where supported;
confirm that ending Hold alone does not resume, while explicit post-Hold
assignment/Update Tracking/regeneration does. No native desktop automation used.

## Corrective fixture/runtime checkpoint — 2026-10-09

Manual QA exposed two public presentation defects, not stale installation or
lost authority. Blender dispatches CHANNELS events through **Animation Channels
(EMPTY/WINDOW)**, not Dopesheet/CHANNELS. The existing mouse actions now register
in that native map; operator polling still confines handling to opted-in VTA
Dope Sheet CHANNELS/WINDOW regions. Timeline handling stays Dopesheet/WINDOW;
no hotkeys or selection semantics changed. The opaque custom row canvas now
composites native list-item alpha against the Dope Sheet background. Default
transparent white is no longer painted as an opaque white row.

The actual user-prefs-loaded extension was already hash-synced before repair;
no duplicate copy took precedence. Installed runtime:
`C:/Users/dsguf/AppData/Roaming/Blender Foundation/Blender/5.2/extensions/user_default/blendcap`.
The changed runtime file was backed up and refreshed; all five relevant files
match source afterward. Three source and three installed lifecycle cycles pass.

Current canonical human-QA fixture:
`C:/Design/AI/Projects/Video to Animation/BlendCap-Assisted-MultiPerson/checkpoints/dev-preview/vta_cleanup_selection_20261009/fixture/VTA_Overlay_Wrestling.blend`.
Only **white/yellow** remain. Standard confirmed Character-deletion commands
removed **a** and **QA independence only** from this new companion copy only.
Exact state comparison proves only the Character list changed: both frame-0
identity keys, all tracking revisions/records (68 effective ticks), both Cleanup
ranges, source, analysis revision/linkage and all cache/scoring files are retained.
The .blend/layout itself is byte-identical to the original, whose entire fixture
tree remains unchanged. Historical semantic files are copied, not relabeled.

Selection mechanics passed on the original uncleaned fixture before cleanup,
then on the reopened new copy with blank initial selection. Tests cover the
loaded legacy row/session configuration, native dispatched keymap/scoping and
transparent theme colors. Public **374 total / 368 passed / six existing skips**;
focused VTA **187 passed**; private unchanged **233 passed**. Compilation and
whitespace checks pass. The full-public shutdown retains its tiny baseline
unfreed-block diagnostic; lifecycle exits cleanly. Overlay candidates and draw
callback are validated with mocked GPU drawing, not live rendered acceptance.
Live native click/visual acceptance remains user QA; no desktop automation.
Checks and pre-repair backups live under the same checkpoint (`repair_backups/`,
`repair_verification.json`). Candidate suppression and overlap cycling remain
deferred; schema, tracker, Cleanup, Recovery and provider behavior are unchanged.

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

Historical October 5 QA fixture (preserved, no longer canonical):
`checkpoints/dev-preview/vta_untrusted_motion_20261005/fixture/VTA_Overlay_Wrestling.blend`.
Yellow Blender 67–90 and White 70–85 are mechanical QA ranges, NOT new semantic
truth. All original files/caches/scoring artifacts and identity keys are unchanged.
Three-Character synthetic tests prove valid tracking independence. The extra
fixture QA Character has no invented identity/pose. An unsaved working clone
tests arbitrary post-anchor mechanics without claiming candidate correctness.
Headless reopen/backend/real View2D and lifecycle checks pass; live acceptance
remains user QA. No native desktop control.

## Manual QA

After restarting Blender, use the new canonical fixture in VTA Mode.

1. Open the new canonical .blend.
2. Click white.
3. Ctrl-click yellow.
4. Confirm one is active and both can be selected.
5. Confirm the old test Characters are gone.

Next milestone: user live QA and narrow repairs only. No automatic smoothing,
manual-box Recovery, threshold tuning or reconstruction without separate approval.
