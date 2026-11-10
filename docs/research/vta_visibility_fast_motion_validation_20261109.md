# Visibility versus visible fast motion — follow-up, 2026-11-09

**Decision: B — promising but insufficient overall. No stabilization implemented.**

Negative limb-mask support stayed quiet on the newly selected visible controls.
However, the fixed combined rules retained known visible-shoulder activations,
and the sustained knee problem remained unflagged. Hand-refinement failure was
particularly unsuitable: it rejected 36/62 visibly moving wrist samples. Low
recall alone is not the reason for B; the remaining projection/visibility ambiguity
and known visible activations prevent treating these rules as a generally safe
per-limb reliability signal.

This follows [the initial visibility/model survey](vta_visibility_signals_and_temporal_models_20261109.md).
No model survey, product design or historical report was rewritten.

## Automatically selected control and visual proof

Scanned all **888 source frames** of the same 50 fps outdoor-wrestling clip at
25-frame spacing, then reviewed every frame of a short promising region. No owner
search or annotation was requested.

**Primary control: visible frames 558–619, no-shirt Character's right arm.** The
elbow/forearm/wrist region remains discernible as the wrestler raises the held foot
and leans back. This is significant real motion in the source; the raw wrist
rotations also have nontrivial velocity. It is a control from the other
owner-created Character in the same project/source, not the black-shirt Character.

**Secondary control: frames 558–619, black-shirt Character's lifted left leg.**
The knee/shank is exposed while the leg rises. The held ankle/foot itself is
partially covered by the opponent's hands and is **not** labeled fully visible.
The initial draft called this the right leg; source/projection review corrected
that anatomical assignment **before control classification metrics were computed**.
The rejected draft remains in the ignored checkpoint for provenance.

The 80-frame context **540–619** supplies preceding samples for derivative and
trailing-window calculations. Primary specificity uses only 558–619. Eight
representative frames are preserved: **558, 566, 574, 582, 590, 598, 606, 614**.
`visible_fast_proof.jpg` is an aspect-preserving source-image contact sheet, not a
filtered-animation comparison. Light dust and grip contact make this a useful
challenging control rather than clean studio footage.

All evidence is under the ignored directory:
`checkpoints/dev-preview/vta_visibility_validation_20261109/`.

## Provisional labels and data provenance

Labels are **Codex visual annotations, not authoritative or independently validated
ground truth**. They judge the visible joint neighborhood, not visibility of every
finger or the internally hidden joint center. A wrist can be identifiable while
its palm/fingers contact the foot. No existing owner keys/assignments were changed.

The new controls include 560 frame/joint labels over the 80 context frames:

| Character | Joint / region | Provisional labels |
| --- | --- | --- |
| Black shirt | Lifted left knee/shank | Visible, 540–619 |
| Black shirt | Left ankle/foot | Partial, 540–619; excluded from visible specificity |
| No shirt | Right shoulder, right elbow/forearm, left elbow | Visible, 540–619 |
| No shirt | Right wrist | Partial, 540–557; Visible, 558–619 |
| No shirt | Left wrist | Partial, 540–619 |

Five Visible joint regions × 62 primary frames give **310 correlated frame/joint
samples**, not 310 independent trials. The primary right-elbow/right-wrist test has
62 frames for each joint. The earlier difficult-window labels at **270–349** were
reused unchanged: 175 Hidden, 170 Partial, 156 Visible and 59 Uncertain entries,
including its left-elbow control. Its Hidden/Partial regions are not relabeled to
improve a rule's score.

The older window uses the accepted raw generation
`ef6a7aeb29374298a070df9ac83515c8`. For the later controls, no accepted generated
pose existed in that snapshot. Therefore ran **160 disposable body inferences**
(80 frames × two people), using exact existing cached observation boxes associated
through visually checked anchor boxes and IoU. These are research associations,
not owner-authoritative assignments or a tracking update. Fixed source focal
length and 50 fps were retained. Raw outputs and existing hand gates were saved
only in this checkpoint.

Reran existing SAM3 once for each of the 80 control frames to capture its masks.
All 160 person/frame associations matched their selected cached boxes at IoU
**≥0.99727**. No new model, package, trained weight or perception architecture was
introduced. Private engine and Blender imports remained blocked in the probe
processes. No owner project was opened for writing or regenerated.

## Fixed rules, not a fitted classifier

`rules_preregistered.json` fixes the rules before control pose/signal evaluation:

- Low own-mask segment support: coverage **<0.5**, using the preceding experiment's
  21 projected parent-to-joint samples and small pixel neighborhoods.
- Other-person support: another retained person mask covers **>0.5** of the joint
  neighborhood; this is overlap, not verified front/back order.
- Poor continuity: angular step **>10°/frame OR angular acceleration >250 rad/s²**.
- Sustained poor continuity: poor on **at least 5 of the trailing 9 frames**,
  nominally 180 ms of context at 50 fps.
- Hand invalid: the existing final fusion gate rejects the relevant hand.
  **Hand gates apply only to wrist/hand rows, never to legs.**

Thresholds are diagnostic operating points, not physiological limits, user settings
or production policy. No threshold sweep or fitted classifier was used. Testing
several preregistered combinations still does not establish universal performance.

## Signal specificity on the new visible controls

| Signal / rule | Hidden flags in old difficult window | Partial flags in old window | New visible false flags |
| --- | ---: | ---: | ---: |
| Step >10° only | 10/175 (5.7%) | 18/170 | 3/310 (1.0%) |
| Acceleration >250 only | 74/175 (42.3%) | 88/170 | 64/310 (20.6%) |
| Own-mask segment low | 61/175 (34.9%) | 63/170 | **0/310** |
| Other-mask overlap | 65/175 (37.1%) | 73/170 | 3/310 (1.0%) |
| Hand invalid, wrists only | 16/16 (100%) | 46/64 | **36/62 (58.1%)** |
| Low segment AND poor motion | 33/175 (18.9%) | 40/170 | **0/310** |
| Other mask AND poor motion | 34/175 (19.4%) | 47/170 | 2/310 (0.6%) |
| Low segment AND other mask AND poor motion | 24/175 (13.7%) | 30/170 | **0/310** |
| Low segment AND sustained poor motion | 28/175 (16.0%) | 44/170 | **0/310** |
| Relevant hand invalid AND poor motion | 10/16 (62.5%) | 32/64 | **21/62 (33.9%)** |

False-negative counts for the Hidden samples are respectively 165, 101, 114, 110,
0 wrist samples, 142, 141, 151, 147 and 6 wrist samples. The differing denominators
are deliberate: a hand gate has no evidence about knees or ankles.

For the primary **right wrist**, step alone flags **3/62**, acceleration alone
**31/62**, and the hand gate **36/62**. Mask-segment rules flag **0/62**, including
those higher-motion frames. The right-elbow mask rules also flag **0/62**.
Mask coverage therefore does not trigger merely because the visible arm moves.

The hand gate is not necessarily malfunctioning. Its job is to decide whether a
detailed hand refinement agrees with the body/crop prediction; it can reject that
refinement during a grip while the wrist region is still visible. This invalidates
its use as a direct wrist-occlusion label, not its original internal purpose.

The visible secondary left knee produces 3/62 false flags for joint-point own-mask
support and other-mask overlap, but **0/62 for segment support**. Projection bounds
and reconstruction-crop bounds remain valid for the main visible control joints;
they do not explain or repair the visibility ambiguity here. Candidate score <0.9
would reject **50/62 visible left-knee samples**, illustrating its inappropriate
Character-level granularity when other body regions are less well observed.

## Known visible activations remain in the earlier window

The old difficult window contains 156 Visible samples, which must not disappear
from the assessment just because a new control was added. Own-mask segment support
and **all three mask-based AND combinations still flag the visible right shoulder
at frames 288–289**. Adding a motion or overlap condition does not eliminate those
projection/coverage failures.

Using Hidden samples as positives and **all old/new Visible samples** as negatives,
descriptive precision is:

- Segment only: **61/(61+2) = 96.8%**.
- Segment AND poor motion: **33/(33+2) = 94.3%**.
- Segment AND overlap AND poor motion: **24/(24+2) = 92.3%**.
- Segment AND sustained poor motion: **28/(28+2) = 93.3%**.

These proportions depend on sampled prevalence and provisional labels. The
combinations improve selectivity/recall tradeoffs but did **not** improve measured
precision beyond the simple mask cue. Zero new-control flags do not prove zero
population risk, especially with temporally adjacent samples from one clip.

A distal-arm-only scope excluding the shoulder has no observed visible false
flags in these data and is a promising narrow research candidate. Selecting that
scope after seeing failures is **post-hoc**, not a new validated general rule.
Its value should not be presented as a solution to sustained leg jitter.

## Sustained leg instability, not just peaks

Rotations were projected numerically onto SO(3). Angular velocity uses world-frame
incremental rotation vectors at 50 Hz; acceleration/jerk are their finite
differences. Large numerical derivatives are not physiological injury thresholds
or direct ground-truth error. All comparisons use the same frame rate.

| Interval / joint | Median step | RMS step | Mean acceleration, rad/s² | Mean jerk, rad/s³ | Velocity-direction consistency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hidden right knee, 270–301 (32 frames) | **4.88°** | **5.15°** | 214.6 | 18,563 | 0.483 |
| Hidden right ankle, 285–311 (27 frames) | **3.75°** | **7.85°** | 324.3 | 28,836 | 0.090 |
| Whole difficult right knee, 270–349 | 4.15° | 5.97° | 225.7 | 19,628 | 0.181 |
| Whole difficult right ankle, 270–349 | 4.19° | 6.07° | 243.1 | 20,950 | 0.124 |
| Visible moving right elbow, 558–619 | 2.01° | 2.75° | 115.0 | 10,704 | 0.672 |
| Visible moving right wrist, 558–619 | **3.84°** | **5.09°** | **281.9** | **25,901** | 0.307 |
| Visible moving left knee, 558–619 | 2.48° | 3.13° | 192.0 | 17,576 | 0.136 |

Consistency is the magnitude of summed angular velocity divided by the sum of
velocity magnitudes over the interval; it ranges 0–1 and is descriptive, not a
visibility score. Real turning motion can legitimately reduce it.

The hidden right knee exceeds **3° on 87.5% of frames**, **5° on 46.9%**, but
**10° on none**. A peak-only test would miss this entire sustained interval.
Across 270–349 the knee exceeds 3° on 73.8% of frames and has 15 strong reversals
among 76 eligible adjacent-velocity comparisons. The ankle has 13/78. Strong
reversal means cosine <−0.25 with both steps >1°.

The visible wrist still has high angular derivatives and 27/60 reversals; the
visible left-knee estimate has 27/54. **Source visibility does not guarantee that
the frame-independent model's raw pose is accurate.** This is why motion/visibility
statistics alone cannot prove which raw excursions are real expression of the
action and which are estimation chatter.

SO(3) tangent orientation variance is also saved (e.g. 1,104 deg² for the hidden
knee and 553 deg² for the visible moving wrist). It includes real travel and is
not treated as noise variance. Hidden-ankle global RMS step is 7.85°, versus 2.80°
for its parent-relative local rotation: much of the global change is inherited
from upstream motion. A future reconstruction must handle the chain coherently,
not independently clamp child global rotations.

No defensible count of frames **requiring manual cleanup** can be obtained from
these labels and raw estimates alone. The JSON explicitly leaves that field null.
Motion/rule flag counts are reported as diagnostic candidates, not invented manual
cleanup counts or ground-truth pose errors.

## Do combinations identify sustained problematic spans?

They identify some arm spans. For example, segment AND sustained-poor-motion flags
right elbow **279–295** and wrist **279–294**. Some flagged wrist samples are Partial
rather than Hidden; neither label automatically authorizes replacing the pose.

The leg result is much weaker:

- Every segment-based AND rule catches **0/32 Hidden right-knee samples**.
- Segment AND motion catches 9/54 Hidden right-ankle samples; adding overlap leaves
  5/54. Segment AND sustained poor motion catches 3/54.
- Short ankle runs such as 306–308 or 311–313 do not cover the continuous knee
  instability. Some neighboring labels are Partial/Uncertain, not confirmed Hidden.

The low knee recall is consistent with the earlier geometry failure: a hidden
joint/segment can project onto another visible surface of the same person. No
amount of requiring *additional motion evidence* makes missing negative image
evidence appear. Lowering mask thresholds or accumulating more motion rules would
need new validation and could damage visible motion.

## Decision and smallest next experiment

**B overall.** The new control provides meaningful positive evidence for the
specificity of negative mask coverage. It also gives a concrete negative result
for hand-gate visibility and acceleration-only rules. There is a narrow high-
precision arm candidate, but no demonstrated general automatic knee/leg trigger.
This is not a requirement for full recall or perfect labels: known visible
activations, post-hoc scope selection and image/geometry ambiguity remain.

Do not keep piling on speed/reversal thresholds. The smallest missing signal is
**independent part-specific image support/correspondence**, rather than another
scalar from the same 3D prediction or a whole-person silhouette. It must answer
whether the projected knee/wrist corresponds to visible evidence for that body
part, not merely to some visible surface/person.

Recommended bounded next experiment: evaluate one small independent 2D body-
keypoint/visibility or calibrated-uncertainty producer on these **same frozen
windows and boxes**, keeping high-speed visible specificity primary. The existing
survey identifies RTMPose/CIGPose-style candidates; choose an exact legally usable
checkpoint before running, without outreach for benchmark-only models. This is
not approval to install a large stack, replace reconstruction or change schemas.
The hand/body agreement failure here rules out simply renaming that gate
"visibility."

An arm-only negative-mask research prototype could be considered separately, with
the post-hoc limitation explicit, but no full stabilization is recommended for
deployment from this validation. Any eventual per-limb reconstruction still needs
two trustworthy anchors where available, visible-pose preservation, coherent
local rotations/FK, and source/identity/review/recovery/native-edit boundaries.

## Artifacts, checks and preservation

- `scan_*.jpg`: full-clip scan. `visible_fast_proof.jpg`: eight representative
  source frames. `fast_control_signals.png`: wrist motion/mask/hand-gate comparison.
- `control_annotations.*`: frozen provisional labels, limitations and side
  correction history. `control_cases.json`: exact cached-observation associations.
- `black_shirt_raw.npz`, `no_shirt_raw.npz`, `control_pose_capture.json`:
  **disposable diagnostic output only**, not accepted project animation.
- `rules_preregistered.json`, `validation_rows.csv`, `validation_results.json`,
  `control_signal_specificity.json`, `trigger_runs.json`: counts and interval metrics.
- `verification.json`: math/data alignment, compilation, source identity and full
  owner preservation checks. No public/private product-suite result is claimed for
  this research-only pass.
- Final verification passed **6 synthetic math invariants**, compilation of all
  **4 diagnostic scripts**, 160 body-output/80 mask-frame alignment checks and the
  private/Blender import guards. Source SHA-256 matches the accepted job; all
  **251 previously manifested research artifacts** remain unchanged.
- Canonical `QA.blend` and all **422 companion files** were hashed before/after.
  No owner keys, assignments, project state, native animation, FPS or source media
  were changed. Previous task-owned dirty code remains separate and untouched.
- **No stabilization, production-code change, new package/model download, commit,
  deployment, push, tag, release or owner QA handoff.**
