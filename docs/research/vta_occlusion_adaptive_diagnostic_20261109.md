# Occluded-limb temporal instability — diagnostic gate, 2026-11-09

**Disposition: stop before stabilization implementation.** Raw right-limb
instability is confirmed. Occlusion is a plausible explanation, but the current
artifacts do not establish a reliable per-limb visibility/confidence measure.
The existing Candidate score is unsuitable as a substitute. No filter, production
change, deployment, commit, or push was made in this investigation.

## Inputs and reproduction

- Owner recording: `C:\Users\dsguf\Desktop\2026-11-09 18-36-09.mp4`.
- Owner project: `C:\Users\dsguf\Desktop\QA.blend`, companion `QA_vta`.
- Source: `C:\Design\AI\Projects\Video to Animation\Input File\outdoor wrestling.mp4`;
  1920 × 1080, 50 fps.
- Character: **black shirt**, `character-d666da6acd6f4c8d8f263dcfd743be3d`.
- Accepted generation job: `ef6a7aeb29374298a070df9ac83515c8` in
  `QA_vta/.vta-engine/jobs`; 350 samples, visible frames **1–350**, source indices
  **0–349**. Its historical plan, rather than subsequently edited decisions, was
  used to identify the input observations.
- Recording at approximately 6.5 seconds shows visible frame 87. The right
  forearm/hand and right leg are obscured to varying degrees by the other wrestler
  in the inspected early sequence. This is a qualitative observation, not a
  frame-by-frame visibility annotation or ground-truth pose.

The saved `.blend` predates the accepted generation shown in the unsaved live
session. It is retained as a safety snapshot, **not represented as an exact replay
of that unsaved animation**. Measurements use the accepted job's actual raw
provider arrays and exact observation IDs. No model was rerun. They establish
instability upstream of native Blender application/retargeting; they do not
exclude additional downstream contributions.

Disposable evidence lives under:
`checkpoints/dev-preview/vta_occlusion_adaptive_20261109/` (ignored).
Seven byte-verified snapshots preserve the saved `.blend`, current project JSON,
analysis cache, historical plan, raw NPZ, derived NPZ and BVH. These inputs are
**not a standalone or owner-facing QA project**. Source contact sheets preserve
selected decoded frames; the original clip was read only.

## Available reliability signals

| Signal | Granularity | Finding |
| --- | --- | --- |
| Analysis Candidate score | Detection/Character candidate | Present; median 0.9414, range 0.8242–0.9609. Does not describe individual limb visibility. |
| Reviewed box and observation ID | Candidate/frame | Present; identifies the input crop/identity, not correctness of every joint. |
| Accepted tracking / hard review anchors | Character/frame | Present; identity authority is not a joint-confidence score. |
| Joint/limb confidence, visibility, uncertainty | Joint/limb | Absent from saved pose artifacts and the inspected estimator output dictionary. |
| 2D keypoint confidence | Joint | Absent. The estimator exposes predicted 2D coordinates, but not confidence in its returned dictionary; the adapter does not retain those coordinates. Predictions alone are not observations proving visibility. |
| Segmentation masks / overlap evidence | Pixel | Not retained in this analysis cache. The estimator's optional mask is an input mask; its placeholder `mask_score = ones` is not measured reliability. |
| Mesh / self-occlusion evidence | Surface/joint | The estimator exposes vertices, but current pose artifacts do not retain them. No visibility solver was added. |

Source inspection: `blendcap_vta_body_provider.py::infer_reviewed_pose` retains
only global rotations, joint coordinates, camera translation and focal length.
`models/fast-sam-3d-body/sam_3d_body/sam_3d_body_estimator.py` returns additional
geometry but no explicit body-joint confidence/visibility field in the inspected
dictionary. No claim is made that internal model features could never support a
future reliability estimate.

## Measurements

These are **global joint rotation changes per source frame**, at 50 fps, not
local Euler deltas. Matrices were projected numerically onto SO(3) before computing
geodesic angles. No filtering was applied. Position deltas are also recorded in
model coordinate units; they are not asserted to be calibrated world meters.

| Joint | 95th-percentile step, degrees | Maximum step, degrees | 95th-percentile centered rotation residual, degrees |
| --- | ---: | ---: | ---: |
| Hips | 3.43 | 5.72 | 1.92 |
| Spine | 3.86 | 5.05 | 1.95 |
| Right upper arm | 11.87 | 35.88 | 8.43 |
| Right forearm | 13.55 | 36.08 | 9.15 |
| Right hand | 15.92 | 43.35 | 11.44 |
| Right thigh | 10.12 | 33.80 | 8.23 |
| Right lower leg | 10.64 | 33.84 | 8.74 |
| Right foot | 12.99 | 37.13 | 9.91 |
| Left upper arm | 7.19 | 21.11 | 3.61 |
| Left forearm | 7.92 | 23.96 | 4.47 |
| Left thigh | 4.67 | 9.14 | 2.74 |
| Left lower leg | 5.79 | 12.50 | 3.91 |

The centered residual measures departure from the SO(3) midpoint of the previous
and next orientations. It detects nonuniform motion but is **not a noise label**:
real rapid acceleration also increases it. Right/left differences are likewise
confounded by different real motion and pose. No 180-degree flip was required to
produce the observed instability.

Useful visible-frame inspection windows: **3–9, 33–43, 167–173, 239–245,
303–309**. For example, the right hand changes 43.35 degrees into frame 170 while
the Candidate score is 0.9375; the upper arm changes 35.88 degrees into frame 242
at score 0.9453. Right lower leg peaks into frame 6 at 33.84 degrees and score
0.9375. Large steps thus occur while detection remains confident.

Pearson correlations between Candidate score and angular step for the six right
limb joints range from **−0.160 to +0.113**. This provides no useful basis for a
common confidence-driven rule on this clip. It does **not** disprove a relationship
between actual occlusion and estimator error: the score measures something else,
the samples are temporally correlated, and no formal causal/statistical inference
is warranted.

## Why no prototype was shipped

The requested diagnostic gate has not passed. A low-visibility weighting function
cannot be validated from these signals. Defining visibility by the size of a jump
and then reporting fewer jumps would be circular. It could flatten genuine fast
grappling motion. Nor are there verified reliable poses on both sides of every
hidden span; selecting interpolation endpoints merely because they are nearby
would turn uncertain guesses into asserted truth.

Accordingly:

- No temporal stabilizer, per-limb weights, two-sided reconstruction, kinematic
  constraints or changes to root/depth were applied.
- There is no before/after improvement percentage, filtered BVH or manual A/B
  handoff. Visible fast motion preservation has **not** been demonstrated for a
  candidate filter.
- Partial/heavy/long occlusion and fast-motion discrimination have **not** passed
  a stabilization acceptance test. Save/Reopen and native-key preservation tests
  of a new filter are inapplicable because none was implemented or saved.

## Smallest next experiment

Use a short, explicitly annotated set of visible/partial/hidden intervals for the
same right limbs, including genuinely visible fast motion and verified anchors
on both sides where they exist. First compare centered residuals against those
independent labels. An offline **oracle-visibility** experiment could then test
whether two-sided rotation-safe interpolation actually helps, without pretending
that automatic visibility detection already exists. Fully hidden long spans with
no trusted endpoints must remain unresolved, rather than silently held or bridged.

This would establish the value of the reconstruction method before adding any
automatic reliability mechanism. No new perception stack or production schema
change is recommended on the present evidence.

If justified later, the intended placement is derived neutral/native skeletal
reconstruction before retargeting, using provider-neutral reliability evidence.
It must preserve raw arrays and the process boundary, work in a coherent local
rotation/forward-kinematic representation, and honor source/Character/recovery
boundaries, review hard keys and authoritative repair/native edits. The current
historical plan has continuity ID 0 throughout, 17 user-anchor frames and no
repair samples. **No new boundary enforcement was exercised by a filter here.**

## Validation and preservation

- Reproducible script: ignored evidence directory's `diagnose.py`; uses existing
  NumPy only. No new dependency, download, model inference, or license introduced.
- Six math checks pass: known angle, rotation-wrap handling, constant-speed
  midpoint, injected one-frame excursion, constant angular steps, and fast
  constant-speed motion. These validate measurement math, not a stabilizer.
- Five data checks pass: rotation/position shapes, finite values, contiguous
  frame association and observation-score availability for all 350 samples.
- Seven copied inputs hash-match their originals. Initial six input hashes still
  match. A full before/after hash manifest covers owner `.blend` plus all 422
  companion files; results are recorded beside this report's local evidence.
- The diagnostic script compiles. No public/private production suite rerun was
  required for a report-only outcome; no new full-suite result is claimed.
- Current investigation adds this report and ignored local diagnostic artifacts
  only. Pre-existing uncommitted Character-context and repair-authority fixes
  remain separate and were not reverted, staged or deployed by this task.
- No canonical owner file was modified; no manual QA action is required yet.
- No commit, push, tag, release, or production deployment.

Machine-readable evidence: `diagnostic_summary.json`, `per_frame.csv`,
`raw_metrics.json`, `confidence_proxy_metrics.json`, `snapshot_provenance.json`,
`input_hashes.json`, and owner manifests under the ignored evidence directory.
