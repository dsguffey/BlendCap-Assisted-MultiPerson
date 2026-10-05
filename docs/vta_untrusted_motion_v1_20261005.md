# V1 Untrusted Motion — implementation and QA handoff

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
adjacent ranges remain separate. **Clear Range** subtracts inclusive bounds,
possibly shortening/splitting/removing. Clearing changes eligibility only, never
revives invalidated predictions or runs Update Tracking.

Select Current Character; in the existing VTA Dope Sheet `N` sidebar use
**Motion Trust → Start Frame / End Frame → Mark Untrusted**. Bounds are visible
Blender frames, converted using the existing offset path. **Use Current Frame**
sets a one-frame range. Patterned spans, boundary strokes and UNTRUSTED text
(where it fits) draw on that Character's row. DPI/text size/zoom share existing
row geometry; paint does not intercept key hit testing. Preview/sidebar shows
**Motion: Untrusted** separately from assignment/presence state.

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

1. Restart Blender after extension sync. Open only the disposable file above.
2. Enable VTA Mode, press `N`; select Yellow then White. Verify independent
   patterned 67–90 / 70–85 spans and Motion: Untrusted at Blender frame 74.
3. Click keys through shading: selection must not move time; Go to Review Key
   remains explicit. Zoom/change text size; spans, rows and keys stay aligned.
4. Test one-frame, overlapping and adjacent ranges. Clear a Yellow subrange;
   White stays Untrusted. No inference/tracking starts and retired results do
   not spontaneously reappear.
5. Update Tracking inside Untrusted reports a concise refusal. After separation,
   visually inspect/Assign a valid candidate, then explicitly Update Tracking.
   Geometry acceptance alone is not semantic proof; inspect the result.
6. Save/reopen/restart: intervals and identity keys persist. Normal Analyze and
   review retain their behavior. No Recovery controls should appear.

Next milestone: user live QA and narrow repairs only. No automatic smoothing,
manual-box Recovery, threshold tuning or reconstruction without separate approval.
