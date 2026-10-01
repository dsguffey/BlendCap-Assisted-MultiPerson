# VTA review and semantic QA handoff

This is a scoring-only QA procedure. Human labels created here are **not**
ProjectState review keys, anchors, or bounded-tracking inputs. Do not tune the
geometry tracker until a completed human label file has been scored.

## Blender review checks

Use the copied continuous-analysis fixture
`Visual Smoke Fixtures/VTA_Tracking_Wrestling_0_162/VTA_Overlay_Wrestling.blend`
in the local Video-to-Animation project area.

1. In Analyze, select **Source Frame Range**, enter **Start Frame 4** and
   **End Frame 10**, and verify that the worker reports Blender frames 4–10
   inclusive (internally, source/cache indices 3–9). The controls reject
   reversed or out-of-source ranges; they do not silently clamp them.
2. Confirm the native progress state shows Analyzing, completed/total, and
   most recent Blender frame. The Analyze panel also shows a progress slider,
   **Current run** (completed range frames), **New this run / reused cached**,
   and **Total analyzed** across the source. On failure it shows a short
   summary; expand **Error Details** or use **Copy Details** for the technical
   error. Cancel requests a stop at the next between-frame checkpoint;
   completed cache records remain valid and untouched frames stay Not
   Analyzed. A later Analyze can resume.
3. During analysis, the VTA mutation controls are disabled (another Analyze,
   Character creation/management, assignments, hard review decisions, Update
   Tracking, and the VTA runtime preferences in Advanced). Scrubbing,
   playback, candidate/key inspection, and display controls remain available.
   Blender's ordinary VSE strip editing is outside this add-on's operation
   lock; do not change the source strip during an Analyze job.
4. Confirm Current Frame Review groups candidate assignment, active-Character
   hard decisions, and review-key clearing under one panel. Direct left-click,
   Ctrl-click, right-click menu, and click-through to native strips should
   remain as previously accepted. The dedicated Select VTA Review Keys tool
   remains. Drag/box selection is deferred: the current scoped hit-test
   handles click events, while a drag interceptor would risk native strip
   gestures and require a larger modal design.
5. After analysis completes, confirm overlay/candidate coverage refreshes
   without restarting Blender, the playhead does not jump, and Update Tracking
   can use the new cache. Inspect analyzed frames with zero candidates and
   untouched frames to confirm their distinct labels.

## Completed scoring-only semantic baseline

The completed v2 truth and deterministic report live beside the copied fixture:
`analysis/semantic_labels_completed_qa_20frames_v2.json` and
`analysis/semantic_evaluation_qa_20frames_v2.json`. Their SHA-256 values are
`fddfcb7746c990fd95175c2da01ad3098d08d5f23f71b61c0777f70b49d5362b`
and `db15bd9855dc20acf37ae5516237b26d9330c2785bcf225e42fdf35508d820b2`.
The user-authored frame-1 anchor maps Yellow to stable ID `wrestler-a`
(Candidate 2) and White to `wrestler-b` (Candidate 4); display names were not
used as the identity proof. Frame 1 remains in the truth file but is excluded
from independent accuracy. Frame 17 is included. Frame 141 Yellow retains an
accepted set of Candidate 2 **or** Candidate 5, not an invented single answer.

The unchanged bounded-geometry rerun exactly matched the frozen baseline.
Across 19 independently scored Blender frames (38 Character cases), each
Character has 2 correct candidate matches, 11 unresolved cases with a valid
candidate, 2 outside-derived-scope cases, and 3 correct conservative unresolved
cases where the human found no valid candidate. Yellow has 1 semantic swap;
White has 1 other wrong-candidate match, both on frame 13. Thus each has
5/19 correct cases overall and 2/3 correct among its three resolved cases;
both Characters are simultaneously correct on 5/19 frames. No candidate
conflict, hard-boundary violation, or derived match on no-valid frames was
observed. The tracker remains unresolved at frames 91, 106, and 121 after the
merged/no-valid frames 67, 73, and 81; no sampled reacquisition was observed.
Frames 141 and 161 lie outside the current derived scope, so frame-141
accepted-set truth is retained but not covered by a derived prediction.

The human notes identify merged people, phantom partial merges, incomplete
body boxes, a lifted pose, and denser later candidates. These are detector
quality observations, not proof of a single cause for every tracker outcome.
The conservative unresolved cases and the frame-13 wrong matches are distinct
tracker behaviors. These correlated frames are not independent statistical
samples; no thresholds or tracking signals were changed for this baseline.

Preview inspection now synchronizes assigned-box clicks with Current Character
and timeline Character context, leaves an unassigned click identity-neutral,
consumes ordinary left-drag in the explicit candidate-selection tool, and
uses compact dark label plates with deterministic common-case stacking. Live
Blender QA is still needed to confirm the interaction. Per-box hide/show is
deferred: it would require separate session/view-state and restore controls,
and is not needed for this baseline.

## Human semantic labels

The earlier blank v1 artifact is `analysis/semantic_labels_blank_qa_19frames.json`
beside the copied fixture. Keep it unchanged; the completed v2 artifact above
is a separate scoring-only file. All candidate positions are
**one-based, frame-local Blender Candidate N labels**, never persistent IDs.
For each Character and frame, fill `kind` with `candidate`, `occluded`,
`no-valid-candidate`, `not-visible`, or `skip`. A `candidate` requires that
frame's `candidate_position`; every other kind requires `null`. `skip` covers
human ambiguity and is excluded from accuracy. In v2, `accepted-set` instead
uses distinct ascending `candidate_positions`; frame 1 is excluded explicitly
as an authoritative anchor while retaining its Candidate 2/4 context.

Selected Blender-visible frames (source index is one less):

| Phase to inspect | Blender frames |
| --- | --- |
| Easier separated / approach | 1, 7, 13, 19 |
| Contact and movement | 17, 25, 31, 37, 43, 49, 55, 61 |
| Sparse or merged detector output | 67, 73, 81 |
| Later sustained overlap / difficult detections | 91, 106, 121, 141, 161 |

This clip remains in substantial ground overlap near its end. Do not infer
that it provides a clean post-occlusion separation or a reacquisition success
case; the completed labels and baseline above show no sampled reacquisition.

To regenerate a blank sheet, run `tools/vta_semantic_evaluation.py prepare`
with `--companion`, two repeatable `--character` IDs (`wrestler-a`,
`wrestler-b`), `--blender-offset 1`, the comma-separated visible frames above,
and a new `--output`. For a v2 sheet that preserves an accepted set and excludes
the authoritative anchor, also pass `--version 2 --exclude-anchor-frame 1`.
To evaluate, run its `score` command with `--companion`,
`--baseline analysis/geometry_baseline_frame0.json`, `--labels` pointing to the
completed copy, and a new `--output`. Existing outputs are never overwritten.
The scorer validates project/cache hashes and each frame's candidate catalog,
then writes per-frame outcomes and a deterministic summary. It never imports
or runs the tracker and never persists labels into authoritative state.

## Earlier structural baseline

The unchanged geometry rerun is byte-identical to the earlier baseline
(SHA-256 `ae80ceb54387622d8c441681ca0476d2a51b9afb7339d82c7a66c4f57aa3a018`).
All 163 source frames are analyzed, but each Character's current bounded
tracking scope contains 121 records (source frames 1–121). Wrestler A has
17 matched and 104 unresolved; Wrestler B has 16 matched and 105 unresolved.
Both have zero reported candidate conflicts and zero records outside their
scopes. The copied project-state SHA-256 remains
`f9d8db3bc0caf2d73db9cf2e5e78054f9a58608f675421e49b4b10aca5c902df`.
These structural counts predate the completed semantic evaluation above.
