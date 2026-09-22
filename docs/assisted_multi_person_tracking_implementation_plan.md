# Assisted Multi-Person Tracking Implementation Plan

## Milestone

The next milestone is a standalone, offline review-and-apply workflow. It
turns the existing version-2 preview sidecar, durable anchors, conservative
geometry track, and optional fusion report into auditable human decisions and
new immutable anchor/track revisions.

This milestone does not change Blender capture, run new appearance inference,
promote appearance results, or implement automatic reacquisition.

## Blender Preview review surface

The Blender-native candidate-review surface is implemented in the public
BlendCap integration. Standalone VSE Preview is the primary interactive
surface; the Preview region in Sequencer & Preview is also supported. A
Sequencer-only view is timeline-only and has no box interaction.

Candidate inspection uses Blender's native `WorkSpaceTool`. Temporary
selection is keyed by the durable `observation_id` in the current analysis
revision. Clicking selects for inspection only; assigning a Character remains
a separate explicit command. When boxes overlap, the smallest containing box
wins, with stable `observation_id` ordering for equal-area ties.

Live Blender 5.2 user QA passed for box selection, candidate-dropdown
synchronization, panel-to-overlay synchronization, empty clicks, overlay-off
behavior, stale-selection clearing after frame changes, and explicit candidate
assignment with persistence/reload. New semantic Characters are now created
explicitly from the review panel through private-core authority and canonical
project-state persistence. Creation selects the new Character only as
temporary UI intent; Assign remains the sole candidate-to-Character action.
The Preview tool is registered as its own visible toolbar group. Broader VTA UI
reorganization and Character rename/delete remain deferred.

## Proposed files

Add:

- `build_preview_target_review.py` - validate inputs, decode only requested
  source frames sequentially, and build the offline review packet;
- `apply_preview_target_review.py` - validate exported decisions, resolve
  frame-local candidate positions to exact sidecar boxes, create revised
  anchors/ranges, and regenerate the affected track interval; and
- `tests/test_preview_target_review.py` - standard-library synthetic tests for
  schemas, policy, bounded changes, and output safety.

The first milestone should reuse `track_preview_target.py` validation and
tracking functions without modifying existing capture or diagnostic scripts.
A later Blender timeline and panel workflow may add controls around the same
schemas; the current overlay and temporary candidate-selection surface does
not persist decisions or replace the standalone review-and-apply workflow.

## Data flow

```text
source video
  + version-2 preview sidecar
  + version-1 target anchors
  + version-1 geometry track
  + optional version-1 fusion diagnostic
      -> offline review packet
      -> human decision export
      -> strict provenance and semantic validation
      -> immutable revised anchors and track
      -> affected-interval equivalence check
```

The review packet reads optional appearance suggestions only from an existing,
validated fusion report. It never loads OSNet, weights, embeddings, or crops.

## Decision schema

Use:

```json
{
  "schema": "blendcap-preview-target-review-decisions",
  "version": 1,
  "review_id": "...",
  "created_at_utc": "...",
  "source": {
    "sidecar_sha256": "...",
    "anchors_sha256": "...",
    "track_sha256": "...",
    "fusion_sha256": null,
    "video_sha256": "...",
    "fps": 0.0,
    "sampling_interval": 1
  },
  "target_ids": ["..."],
  "frame_range": [0, 0],
  "decisions": [],
  "statement": {
    "appearance_is_non_authoritative": true,
    "temporary_candidate_indexes_are_per_frame_only": true
  }
}
```

Each decision contains `target_id`, `frame`, `action`, and optional `note`.
The allowed actions are:

- `select-existing-candidate`, with one `temporary_candidate_index` valid only
  on that frame;
- `merged-shared-detection`, with a frame-local shared candidate and an
  inclusive `start_frame`/`end_frame` plus a required note;
- `no-valid-candidate`, with an inclusive range and required note;
- `not-visible`, with an inclusive range and optional note; and
- `uncertain`, which creates no anchor or forced range.

The review decision may carry a TEMP position as transient user input. The
apply tool must resolve it against the hash-bound sidecar and must not copy the
TEMP position into a durable anchor identity.

## Revision schema

Use:

```json
{
  "schema": "blendcap-preview-target-review-revision",
  "version": 1,
  "revision_id": "...",
  "created_at_utc": "...",
  "parent_revision": {
    "filename": null,
    "sha256": null
  },
  "inputs": {
    "decisions_sha256": "...",
    "sidecar_sha256": "...",
    "base_anchors_sha256": "...",
    "base_track_sha256": "...",
    "fusion_sha256": null
  },
  "affected_intervals": [],
  "semantic_changes": [],
  "outputs": {
    "anchors_filename": "...",
    "anchors_sha256": "...",
    "track_filename": "...",
    "track_sha256": "..."
  },
  "verification": {
    "outside_affected_intervals_unchanged": true,
    "forced_ranges_not_bridged": true,
    "temporary_candidate_indexes_persisted_as_identity": false
  }
}
```

The revised anchor output remains the existing
`blendcap-preview-target-anchors` version-1 schema. Candidate selections become
durable records containing frame, exact sidecar `box_snapshot_xyxy`, and box
center `clicked_point_xy`; they do not retain TEMP indexes. The revised track
remains `blendcap-preview-target-track` version 1.

Revisions are append-only. Undo selects an earlier revision as the active
input, or writes a compensating revision referencing the current revision. It
never deletes or edits history.

## Human workflow

1. Build a queue from ambiguous frames and explicitly requested untracked or
   occlusion-boundary frames.
2. Display the previous sampled frame, exact review frame, and next sampled
   frame at useful resolution.
3. Draw all boxes with collision-free markers and a separate frame-local TEMP
   legend.
4. Show forward and backward geometry candidates, their scores and geometric
   metrics, and where either chain stopped.
5. If a validated fusion report supplies one, show the appearance candidate as
   an optional, visibly non-authoritative suggestion. It must never be
   preselected.
6. Let the user select an existing candidate, mark a merged/shared detection,
   mark no valid candidate, mark the target not visible, or leave the frame
   uncertain.
7. Resolve an existing-candidate selection immediately to the exact sidecar
   box when applying the export.
8. Represent merged/no-valid/not-visible spans as explicit inclusive forced
   ranges. Never infer their extent automatically.
9. After separation, let the user select a candidate to create a new durable
   anchor.
10. Regenerate only the affected anchor-bounded interval. No interval may enter
    or cross a forced range.
11. Publish new anchor, track, and revision files without overwriting their
    parents.

The HTML packet should support previous/next navigation, keyboard navigation,
progress, notes, local undo/redo, persistent local draft state, and JSON export.
It starts with no decisions and blocks export of incomplete or semantically
invalid required actions.

## Safety invariants

- Inputs are read-only and bound by exact SHA-256 hashes.
- Output paths are restricted to an explicitly allowed workspace directory.
- Inputs and outputs cannot collide through normalization, case differences,
  links, or existing destinations.
- Temporary output is created exclusively in the destination directory and
  published atomically without replacement.
- Candidate positions are validated on their source frame before use.
- A selected candidate is persisted only as an exact box snapshot and center.
- Anchors inside forced ranges and overlapping/reversed forced ranges are
  rejected.
- A shared detection is never converted into two ordinary identities.
- Appearance never changes state, creates an authoritative box, or reacquires
  an `occluded` or `untracked` target.
- Accepted geometry is never silently replaced.
- Tracking and temporal evidence never bridge a forced range.
- All frames outside the affected interval remain semantically and
  byte-for-byte equivalent after canonical serialization.
- Source video paths, embeddings, crops, weights, and persistent TEMP
  identities are absent from decision and revision outputs.

## Focused tests

Use synthetic fixtures; ordinary tests must not need video codecs, model
weights, CUDA, Blender, or ignored research artifacts.

Cover:

1. Strict sidecar, anchor, track, fusion, and decision schema validation.
2. Exact hash and target-set compatibility.
3. Candidate selection resolves to the exact sidecar box and center.
4. Candidate reordering cannot create persistent identity.
5. Stale packet or stale decision hashes are rejected.
6. Missing, duplicate, boolean, negative, or out-of-range candidate positions.
7. Merged/shared, no-valid, not-visible, and uncertain action validation.
8. Required shared candidate and note validation.
9. Reversed, overlapping, unsampled, and anchor-conflicting ranges.
10. Forced-boundary non-bridging.
11. Interval-1 and interval-11 affected-interval calculation.
12. Valid tracked and ambiguous records within an unblocked interval.
13. Exact equivalence outside affected intervals.
14. Appearance suggestions never preselect a choice or alter resulting state.
15. Missing, tied, partial, and incompatible suggestion presentation.
16. Parent revision hashes, semantic diffs, and compensating undo revisions.
17. Input immutability and defensive copying.
18. Output-path restriction and input/output collision handling.
19. Existing/concurrently created destination preservation.
20. Atomic-publication failure cleanup.
21. HTML starts blank, retains navigation state, and blocks incomplete export.
22. Collision-free marker/legend generation and frame-local TEMP wording.

## Implementation order

1. Implement the decision/revision data model and strict, Blender-free
   validators.
2. Implement decision application, durable anchor/range generation, semantic
   diffs, and no-clobber revision publication.
3. Add bounded-interval calculation and verify that regeneration cannot change
   records outside the affected interval.
4. Build the offline three-frame review renderer and self-contained HTML UI.
5. Run focused and complete repository tests, static checks, and a read-only
   self-audit.
6. Verify the workflow on existing ambiguous and forced-range artifacts without
   modifying their frozen inputs.
7. Only after the standalone workflow is stable, add a thin Blender timeline
   and panel wrapper around the same schemas.

## Completion criteria

The milestone is complete when a user can:

- open a real ambiguous interval with neutral candidate context;
- inspect geometry hypotheses and an optional appearance suggestion;
- select the correct existing candidate or record a semantic failure;
- export a complete hash-bound decision document;
- create a new immutable anchor and track revision;
- change only the intended legal anchor-bounded interval;
- preserve forced ranges and every unrelated frame;
- reload the revision with exact provenance validation; and
- undo without deleting or mutating the audit trail.

No completion criterion permits automatic appearance promotion or automatic
reacquisition.
