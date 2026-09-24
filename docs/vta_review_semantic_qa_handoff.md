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
   most recent Blender frame. Cancel requests a stop at the next between-frame
   checkpoint; completed cache records remain valid and untouched frames stay
   Not Analyzed. A later Analyze can resume.
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

## Human semantic labels

The blank artifact is `analysis/semantic_labels_blank_qa_19frames.json` beside
the copied fixture. Work on a separate copy with a new filename; never change
the blank template or the copied ProjectState. All candidate positions are
**one-based, frame-local Blender Candidate N labels**, never persistent IDs.
For each Character and frame, fill `kind` with `candidate`, `occluded`,
`no-valid-candidate`, `not-visible`, or `skip`. A `candidate` requires that
frame's `candidate_position`; every other kind requires `null`. `skip` covers
human ambiguity and is excluded from accuracy.

Selected Blender-visible frames (source index is one less):

| Phase to inspect | Blender frames |
| --- | --- |
| Easier separated / approach | 1, 7, 13, 19 |
| Contact and movement | 25, 31, 37, 43, 49, 55, 61 |
| Sparse or merged detector output | 67, 73, 81 |
| Later sustained overlap / difficult detections | 91, 106, 121, 141, 161 |

This clip remains in substantial ground overlap near its end. Do not infer
that it provides a clean post-occlusion separation or a reacquisition success
case; the human labels must establish whether one exists.

To regenerate a blank sheet, run `tools/vta_semantic_evaluation.py prepare`
with `--companion`, two repeatable `--character` IDs (`wrestler-a`,
`wrestler-b`), `--blender-offset 1`, the comma-separated visible frames above,
and a new `--output`. To evaluate, run its `score` command with `--companion`,
`--baseline analysis/geometry_baseline_frame0.json`, `--labels` pointing to the
completed copy, and a new `--output`. Existing outputs are never overwritten.
The scorer validates project/cache hashes and each frame's candidate catalog,
then writes per-frame outcomes and a deterministic summary. It never imports
or runs the tracker and never persists labels into authoritative state.

## Structural baseline, not semantic accuracy

The unchanged geometry rerun is byte-identical to the earlier baseline
(SHA-256 `ae80ceb54387622d8c441681ca0476d2a51b9afb7339d82c7a66c4f57aa3a018`).
All 163 source frames are analyzed, but each Character's current bounded
tracking scope contains 121 records (source frames 1–121). Wrestler A has
17 matched and 104 unresolved; Wrestler B has 16 matched and 105 unresolved.
Both have zero reported candidate conflicts and zero records outside their
scopes. The copied project-state SHA-256 remains
`f9d8db3bc0caf2d73db9cf2e5e78054f9a58608f675421e49b4b10aca5c902df`.
These are structural facts only; semantic identity correctness remains
unknown until independent human labels are supplied.
