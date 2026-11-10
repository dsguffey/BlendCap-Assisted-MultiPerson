# Limb visibility signals and temporal refinement — 2026-11-09

**R&D result: useful supporting cues, but no generally trustworthy automatic
visibility trigger yet. No production integration or deployment.**

The best existing-image cue in this pilot was coverage along a projected limb
segment in the Character's SAM3 mask. It was conservative but missed most hidden
samples. The deeper body-model audit also found previously unexported **per-hand
fusion-validity gates**. They are worth retaining for research, but are not
body-joint visibility probabilities. SmoothNet demonstrated substantial derivative
reduction in an isolated rotation-channel benchmark, with measurable changes to
visible poses; this is not visual or kinematic acceptance.

## Evidence, annotation and limitations

Source: `C:\Design\AI\Projects\Video to Animation\Input File\outdoor wrestling.mp4`,
1920×1080, 50 fps. Character: **black shirt**,
`character-d666da6acd6f4c8d8f263dcfd743be3d`.
Accepted generation: `ef6a7aeb29374298a070df9ac83515c8`, visible frames 1–350.
Raw arrays, analysis observations and the historical generation plan came from
the preceding investigation's byte-verified disposable snapshots, not new edits
to the owner project. The current saved `.blend` is not asserted to contain the
unsaved generated animation shown in the owner's recording.

Pilot window: **visible frames 270–349**, source indices 269–348: 80 consecutive
losslessly decoded frames. Reviewed chronological source-only sheets and assigned
V/P/H/U labels to the anatomical right shoulder, elbow, wrist/hand, hip, knee and
ankle/foot, plus the left elbow as a visible lowering-motion control.

**Annotation provenance matters:** these are provisional Codex visual annotations,
not independent human ground truth or David-approved labels. They are frozen,
human-readable and reviewable; uncertain regions are explicitly excluded from
binary metrics. No owner labeling, authoritative assignments or tracking records
were changed. Interval boundaries and anatomical-side judgments need independent
review before accepting a positive reliability claim.

The six right joints have 480 frame/joint entries: **101 Visible, 145 Partial,
175 Hidden, 59 Uncertain**. The left-elbow control adds 80 entries: 55 Visible,
25 Partial. There are **no fully visible right-wrist or right-ankle intervals** in
this window. Therefore their full visibility discrimination, and preservation of
a fast fully visible wrist/foot action, cannot be certified here. Reappearance
and fast lowering are covered for other regions; a clean punch/kick control is
not claimed.

All local artifacts are under the ignored directory:
`checkpoints/dev-preview/vta_visibility_signals_20261109/`.
`annotations.md`, `annotations.csv`, `annotations.json`, the 80 PNG frames and
eight chronological contact sheets preserve the annotations and their provenance.
`visibility_pilot.png` compares labels with mask coverage. These are diagnostic
artifacts, not a replacement QA project or a filtered animation handoff.

## Existing provider/model signal audit

| Signal / source | Meaning and granularity | Already computed? Discarded? | Availability and cost of retaining |
| --- | --- | --- | --- |
| SAM3 Candidate score | Probability-like detection/prompt score, 0–1; Candidate-level | Computed and saved | Available now; insufficient limb granularity. |
| SAM3 decoder query embedding | Appearance/query feature vector; Candidate-level | Computed upstream; not retained as joint reliability | Not calibrated uncertainty or visibility. Saving it would not solve this task. |
| SAM3 predicted person masks | Thresholded mask logits; pixel-level visible support, not anatomical part labels | Computed during the same grounding pass, discarded by the streaming detector/analysis adapter | Existing cached analysis has no masks. Reran only the installed SAM3 path offline for this pilot. Future analysis could retain a small RLE/bit-packed sidecar without another model pass. Generation currently has no mask input. |
| SAM3D global rotations / joint coordinates / camera / focal length | Model-estimated geometry; joint-level | Computed and saved | Available from current artifacts; permits approximate projection and temporal tests without body re-inference. Geometry is a prediction, not visibility evidence. |
| SAM3D projected 2D keypoints and camera depth | Image position and depth of a predicted keypoint, pixels and model-camera length units | Computed, omitted by VTA's body adapter | Cheap to retain; no new forward pass during future generation. No measured 2D keypoint confidence accompanies the returned coordinates. |
| SAM3D vertices | Predicted body surface; surface-level | Computed, omitted by VTA's body adapter | Could support rendered self-occlusion hypotheses, but needs surface/part correspondence and a visibility calculation. Much larger than joint metadata. A joint center is naturally inside skin, so a naive depth test would label ordinary joints hidden. |
| SAM3D `hand_valid_mask`, `valid_angle` and component tests | Boolean hand-refinement/fusion acceptance; separate left/right hands | Computed inside `run_inference`, not exported or saved | Newly captured from method-return locals without modifying the method. Tiny storage/runtime cost if intentionally exposed in future. Unavailable retrospectively from the saved NPZ. Not knee/leg confidence. |
| Wrist agreement and rotation disagreement | Normalized hand/body 2D wrist distance (nonnegative); rotation disagreement (radians) | Already computed for hand fusion, omitted | Potential graded supporting cues; small numeric arrays. They measure agreement between predictions, not independently observed visibility. |
| Hand crop/point validity | Crop size >64 pixels, projected hand points within crop, wrist-distance <0.25 and pose-angle checks | Already computed | Out-of-crop/weak-hand diagnostics; no general limb visibility interpretation. These thresholds remain unchanged. |
| Tracking authority / hard keys / continuity | Character identity and explicit user decisions | Saved | Must remain boundaries/authority. An assigned Character can still have an unseen arm. |
| Body-joint uncertainty, heatmap certainty, multiple pose hypotheses | Would require explicit uncertainty outputs or ensembles | No usable saved/exposed score found in the inspected path | This is a coordinate/parameter regressor, not a saved heatmap-confidence pipeline. Constant ones appended to coordinates or supplied-mask scores are placeholders, not confidence measurements. |

Code evidence: `blendcap_vta_body_provider.py::infer_reviewed_pose`,
`blendcap_vta_provider_worker.py`, installed Kinesia
`stream_detector.py::PyTorchSam3StreamDetector.detect`, SAM3
`sam3_image_processor.py`, SAM3D `mhr_head.py`, `camera_head.py` and
`sam3d_body.py::run_inference`. The previous report is not rewritten; this deeper
audit adds the internal hand-fusion findings.

## Mask experiment and signal-vs-label results

Used the existing configured SAM3 weights and detector in a separate process.
Captured its already-computed masks instead of changing its detection decisions.
All **80** target masks matched their exact historical reviewed boxes with IoU
at least **0.99795**. This association is not a new identity decision. No tracking
or project results were accepted or persisted.

Projected cached MHR skeletal joints using their camera translations, focal length
and source-centered pinhole convention. These are estimated joint centers rather
than the separate 70 mesh-regressed keypoints. Projection error is an explicit
failure mode; the overlays show some predictions on the wrong visible body region.
No independent 2D observation exists against which to measure reprojection error.
Projecting a prediction and comparing it to itself would be meaningless.

Tested five signals without fitting thresholds to this window:

- Candidate score; a predeclared score <0.9 flags weak detection.
- Fraction of a 5-pixel-radius disk inside the own mask.
- Absence from other retained person masks at that disk. Near-duplicate own
  detections were excluded. No front/back ordering is assumed.
- Coverage along the projected parent/joint segment, sampled at 21 locations.
- Image bounds; out-of-frame projected points are distinct from occlusion.

Mask support <0.5 flags potential low visibility. Partial and uncertain labels are
excluded from the following V-versus-H binary counts. They remain in the saved
distributions. These are **descriptive pilot results, not held-out accuracy**;
pooled metrics mix different joints, and adjacent frames are not independent.

| Signal (high means visibility support) | Pooled V/H AUC | Hidden precision | Hidden recall | Visible falsely flagged hidden |
| --- | ---: | ---: | ---: | ---: |
| Candidate score | 0.612 | Undefined: no flags | 0% | 0/156 |
| Own-mask point support | 0.683 | 79.2% | 45.7% | 21/156 (13.5%) |
| No other-mask support | 0.633 | 76.5% | 37.1% | 20/156 (12.8%) |
| Own-mask segment coverage | **0.742** | **96.8%** | **34.9%** | **2/156 (1.3%)** |
| In-image | 0.500 | Undefined: no flags | 0% | 0/156 |

Per-joint results are more informative than pooled AUC. Right-elbow segment
coverage has AUC **0.798** and catches 17/30 hidden elbow samples with no false
hidden flags among 20 visible elbow samples. Right-knee point support has AUC
**0.876**, catches 25/32 hidden samples and falsely flags 2/40 visible samples.
But segment coverage catches **0/32 hidden knee samples** at the same threshold.
The same rule does not generalize uniformly across limbs.

Clear failure cases:

- All 43 hidden right-hip samples pass the own-mask point test: self-occlusion
  projects onto another surface of the same person. Inside-own-mask is not visible.
- Early hidden right-elbow samples can also project inside own visible torso.
- Visible right shoulder at frames **288–289** falls below the segment threshold;
  estimated geometry/crop positioning can create false hidden flags.
- Other-person mask overlap cannot determine whether that person is in front.
  A behind-person mask or segmentation spill can falsely reject visible motion.
- Partial versus Hidden distributions overlap strongly: mean segment support is
  0.620 versus 0.635. This is not a reliable three-level visibility classifier.

The 55 visible left-elbow control samples have **0 segment-based hidden flags**;
the point and other-mask tests each falsely flag one. That is encouraging for
conservative support tests, but not enough to certify all fast visible motions.

Self-occlusion cannot be reliably distinguished by these masks. Other-person
occlusion can be *suggested* when own support disappears and another mask occupies
the location; depth ordering and accurate image correspondence are still missing.
Out-of-frame projections are detectable, but none occurred in this pilot. Motion
blur/weak evidence versus unusual visible pose is not established by the signals.

## Internal hand-fusion gate experiment

Reran the exact installed body provider for the same 80 frames with the historical
reviewed boxes and fixed focal length. A temporary process-local Python return
trace captured existing locals; no model branches or tensors were edited. Raw
rotations reproduced the accepted output to **0.0456° maximum** across all sampled
joints/frames, median frame-wise disagreement zero. This supports pairing the
new diagnostic gates with the earlier accepted pose trajectory.

The right-hand fusion gate rejects all **16/16 heavily hidden right-wrist** pilot
samples. It accepts **18/64 partially visible** samples and rejects the other 46.
No fully visible wrist sample exists, so visible-wrist specificity/AUC and fast
visible hand preservation are **not measurable** here. Wrist disagreement is not
monotonic in the labels: median normalized distance is 0.311 for Hidden and 0.343
for Partial. It is not a calibrated occlusion probability.

There are nine right-hand fusion-state transitions at frames 287, 291, 301, 306,
334, 335, 341, 345 and 346. This hard switch is an additional temporal-discontinuity
mechanism worth a future controlled ablation. **It has not been proven to cause
the main shaking**, does not explain legs, and was not disabled in this task.

## Temporal/geometric support, not a detector

SO(3) centered residuals are larger in some hidden regions: right-elbow median
3.44° Hidden versus 1.92° Visible; right-knee median 2.69° Hidden versus 1.48°
Visible. Partial knee motion has a larger median still, 4.32°, during the real
stance change/reappearance. This supports the occlusion-instability hypothesis
without making motion magnitude a safe visibility classifier. No angular clamp
or speed-based visibility classification was introduced.

## Open-source survey and two licensing buckets

**A = benchmark/reference only in this project pass. B = potentially shippable,
subject to the stated artifact/dependency/provenance checks; not commercial
clearance.** Published benchmark scores are not StudioFree test results.
No authors were contacted and no permissions were sought.

| Candidate | Fit and visibility relevance | Bucket / run decision |
| --- | --- | --- |
| [SmoothNet](https://github.com/cure-lab/SmoothNet) (ECCV 2022) | Temporal-only refiner consumes existing pose channels, including 6D rotations; no visibility prediction. Smallest practical temporal benchmark. [Code Apache-2.0](https://raw.githubusercontent.com/cure-lab/SmoothNet/main/LICENSE). | **B, conditional** for a neutral refiner. Official MMHuman3D checkpoint used locally as a reference; exact weight redistribution/training provenance not cleared for our payload. |
| [PoseMamba](https://github.com/nankingjing/PoseMamba) (AAAI 2025) | Video 2D-to-3D lifting, not a drop-in rotation-trajectory refiner or a validated visibility head. Requires 2D detections and custom selective-scan CUDA build. | **A for current technical fit**; not run. [Code](https://raw.githubusercontent.com/nankingjing/PoseMamba/main/LICENSE) and [official weight card](https://huggingface.co/nankingjings/PoseMamba-weights) declare Apache-2.0. This is not a noncommercial-code finding; downstream detector/data provenance remains separate. |
| [GEM-X Body Motion](https://github.com/NVlabs/GEM-X) (2026 release) | Learned temporal body model from matched Body Pose features, native77 2D observations and camera/time conditions. `motion-cache` does **not** accept arbitrary VTA rotations as its matched input. Broader replacement/provider experiment, not a small postfilter. | **B, longer-term**. Not run: new matched feature/detector/checkpoint/geometry stack and no validated local runtime. [Model overview](https://github.com/NVlabs/GEM-X/blob/main/docs/MODEL_OVERVIEW.md), [inference contract](https://github.com/NVlabs/GEM-X/blob/main/docs/DEMO.md). |
| [AmbiguousHPE / JointVisibilityInfo](https://github.com/GYTuuT/AmbiguousHPE) (TIP 2025; 2026 annotation update) | Mostly hand multi-hypothesis recovery. Visibility annotation uses known hand/object geometry and rendered depth; it is not an RGB-video visibility service. Human-body release requires training. | **A**. No root license found in the inspected tree; pretrained examples need MANO/data assets. No execution/download of those assets. [Annotation implementation](https://github.com/GYTuuT/AmbiguousHPE/blob/main/data/MultiAnnoAugment/hand_status_check.py). |
| [STRIDE](https://github.com/take2rohit/STRIDE) (WACV 2025) | Model-agnostic test-time temporal refinement; technically relevant to long occlusion but involves optimization/training per video. | **A**. [Academic software license](https://raw.githubusercontent.com/take2rohit/STRIDE/master/LICENSE.md) grants use to academic/nonprofit researchers; commercial-entity private research is not granted by that text. Not run; no outreach. |
| [VisOR](https://github.com/arid29/VisOR) (WACV 2026) | Domain-adaptation training using visibility curriculum. In released code the visibility input is a **per-image silhouette-area statistic**, not per-joint occlusion. PoseNDF/training/data stack required. | **A**. No root license found in inspected tree; not run. [Paper](https://openaccess.thecvf.com/content/WACV2026/papers/Dutta_Visibility_guided_Self-Supervised_Occlusion-Resilient_Human_Pose_Estimation_WACV_2026_paper.pdf). |
| [CIGPose](https://github.com/53mins/CIGPose) (CVPR 2026) | Whole-body image estimator uses uncertainty to intervene on confounded keypoint features; potentially relevant independent image evidence, but uncertainty is not automatically calibrated visibility. | **B, exploratory**. [Code Apache-2.0](https://raw.githubusercontent.com/53mins/CIGPose/main/LICENSE); exact linked checkpoint coverage not independently established. Not run: additional MMPose/MMCV/MMDetection setup; no task-specific calibration. |
| [MoPO](https://arxiv.org/html/2605.09856v1) (2026) | Very relevant architecture: joint-visibility detector, temporal completion, image/motion fusion and IK. More than a small plug-in filter. | **A**. Paper/reference only; no verified ready-to-run licensed code/weight package identified in this bounded search. Paper availability/license does not license implementation assets. |
| [RTMPose/MMPose](https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose) | Small independent 2D keypoint estimator, potential disagreement/score cue. Its keypoint score still needs occlusion calibration. | **B, exploratory**. [Code Apache-2.0](https://github.com/open-mmlab/mmpose/blob/main/LICENSE); exact weight rights/provenance need checking separately. No new detector installed or benchmarked. |

### Potential shipping paths: licenses, cost and ranking

1. **Existing SAM3 masks plus body hand-gate/geometry sidecars**: best incremental
   fit and lowest new integration burden. Adds no new model license beyond the
   existing stack; SAM/custom-Meta and existing runtime obligations remain. Mask
   capture is computationally feasible, but signal quality is only partial.
2. **Small neutral temporal reconstruction / SmoothNet-like reference**: highest
   provider independence, consumes trajectories, no GPU needed for the measured
   small network. Existing PyTorch/NumPy/SciPy runtime sufficed. Code Apache terms
   require retained license/notices and changed-file notices where applicable;
   checkpoint-specific provenance remains unresolved for bundling. An independently
   written simple interpolation method would need no learned weights, but still
   needs a validated reliability signal. Do not equate Apache source with automatic
   clearance of every training dataset or downloaded weight.
3. **CIGPose or RTMPose independent image cue**: code under Apache, PyTorch plus
   MMPose/MMCV/MMDetection (Apache code families); ONNX export could reduce eventual
   runtime dependencies. Actual exporter/runtime/weight bill has not been cleared.
   CIGPose's published whole-body configurations range roughly 2.3–18.7 GFLOPs per
   crop; local latency/VRAM unknown. Additional per-person image inference and
   skeleton mapping make this a larger change than retaining existing outputs.
4. **GEM-X Body Motion**: strongest complete temporal-body candidate in this survey
   for a future provider evaluation, but unmeasured on our clip. Source defaults
   to Apache with [OpenMDW-1.1 exceptions](https://github.com/NVlabs/GEM-X/blob/main/ATTRIBUTIONS.md)
   for specified hand/camera/smoothing utilities. The [model card](https://huggingface.co/nvidia/GEM-X)
   identifies the NVIDIA Open Model License and commercial use; its repository
   `LICENSE` file separately contains Apache text. Resolve this artifact-scope
   inconsistency before shipping. The [NVIDIA agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/)
   permits commercial models and redistribution with the agreement and attribution
   notice, subject to its conditions. [OpenMDW](https://raw.githubusercontent.com/NVlabs/GEM-X/main/LICENSES/LicenseRef-OpenMDW-1.1.txt)
   also requires applicable origin/license notices. Neither is a blanket clearance
   of third-party assets.

GEM's [current package](https://raw.githubusercontent.com/NVlabs/GEM-X/main/setup.cfg)
requires PyTorch ≥2.10, Transformers 5.3, SOMA-X 0.3.3 and other numerical/image
libraries; video preprocessing adds ONNX Runtime, matched ViTPose observations and
YOLOX detector weights. DINOv3/Meta, detector and transitive terms require exact
payload review. [SOMA runtime/assets](https://huggingface.co/nvidia/SOMA-X) identify
Apache-2.0; optional SMPL/SMPL-X/MANO assets remain separately licensed and are not
needed merely to choose the native SOMA identity route. [Installation guidance](https://nvlabs.github.io/SOMA-X/latest/installation.html)
explicitly keeps optional model files separate. Do not install optional benchmark
extras by default. The GEM model card lists A100 80GB hardware; this is **not proof
of a minimum VRAM requirement**, and viability on our 16GB card is unmeasured.
No full shipping stack in this survey is declared commercially cleared.

## SmoothNet benchmark actually executed

Used the [official MMHuman3D window-8 configuration](https://raw.githubusercontent.com/open-mmlab/mmhuman3d/main/configs/_base_/post_processing/smoothnet_windowsize8.py)
and its exact linked SPIN-trained checkpoint, SHA-256
`36e48ca1c4dc21b425ffed2346dba8131536653bc8e7364724dbe4de9545671c`.
Downloaded 42,196,079 bytes only into the ignored research directory. Code and
licenses were retained alongside it. The [upstream network classes](https://raw.githubusercontent.com/open-mmlab/mmhuman3d/main/mmhuman3d/core/post_processing/smooth/smoothnet.py)
were AST-selected without importing its installation stack. `weights_only=True`
loaded the weights. No SMPL/SMPL-X/MANO asset, training data, new package or model
service was used.

The 2,635,272-parameter CPU network was tested on seven joints' **global rotation
channels**, with rotation-safe 6D encoding/orthonormalization. The input is the
accepted 350-frame trajectory at its native 50 fps; the window is 8 frames, nominal
160 ms. Split computation at every historical user anchor, left regions shorter
than 8 frames unchanged, and restored every anchor exactly. No postfilter crossed
those boundaries. This is a channel-level benchmark, **not an applied coherent
FK skeleton, native animation, or validated production postprocessor**.

Same 80-frame visibility window, 270–349:

| Global joint rotation | Max step before → after | Mean angular acceleration before → after, rad/s² |
| --- | ---: | ---: |
| Right upper arm | 13.35° → 7.14° | 220.8 → 70.0 |
| Right forearm | 15.20° → 8.61° | 237.0 → 75.9 |
| Right hand | 15.20° → 11.70° | 336.9 → 107.7 |
| Right thigh | 21.07° → 9.73° | 193.7 → 57.9 |
| Right lower leg | 26.55° → 7.65° | 229.6 → 67.7 |
| Right foot | 27.98° → 8.57° | 245.8 → 74.0 |
| Left-elbow control | 23.96° → 23.96° | 231.4 → 130.6 |

Acceleration is the norm of the finite difference of SO(3) world-frame incremental
rotation-vector velocities at 50 Hz, not Euler differences. It includes real
acceleration. Across the full 350-frame benchmark the right-hand maximum changes
43.35° → 13.60°; this is **a different range**, not the 80-frame table statistic.

Visible-pose deviations reach **8.49° at the right knee**, **5.75° at the shoulder**
and **6.20° at the left-elbow control**. Exact hard-key restoration can retain large
steps near anchors. Neither accurate endpoint motion nor natural limb geometry
follows solely from lower derivatives. Time-rate/domain generalization of the
pretrained weights is unproven. There is no ground-truth cleanup-frame count and
no claim that these numeric reductions save that many manual corrections.

**Best observed result:** about 70% reduction in mean angular acceleration for the
affected right limb channels, with substantial maximum-step reductions, **at the
cost of changing visible motion**. No visual quality winner or best-achievable
reconstruction ceiling is established. Only SmoothNet was run as an external
temporal model. Other methods were skipped for the technical/legal/setup reasons
above; no benchmark-only licensing outreach occurred.

## Cost and persistence

Diagnostic timings on the existing RTX 5070 Ti / installed environments:

- SAM3 model load **15.84 s**, first captured frame **7.62 s**, subsequent typical
  frame **0.154 s**; 80-frame capture sum **19.88 s**. Peak Torch GPU allocation
  **4.33 GB**, not total system VRAM consumption. These are full SAM3 inference
  timings, not the incremental cost of merely retaining masks already computed.
- Body gate instrumentation: load **12.11 s**, first frame **19.05 s**, typical
  frame **0.217 s**; 80-frame sum **36.38 s**. These include warm-up/tracing and
  full inference. Exporting the small gate arrays during ordinary inference
  should be much cheaper; that incremental production cost is unmeasured.
- Offline mask association/projection/disk/segment analysis plus overlays:
  **6.89 s** total. Full-resolution masks for all retained people across 80 frames
  compressed to **4,176,918 bytes** in this pilot; other footage may compress worse.
- SmoothNet CPU forward/aggregation: **0.259 s** for the bounded historical
  regions. Startup/weight loading excluded. No timing threshold is a test oracle.

If evidence retention is approved next, save derived reliability diagnostics in a
versioned, source/observation/job-linked sidecar with explicit signal meaning,
joint convention and producer/model version. Preserve raw evidence separately
from filtering and record abstentions. A float32 vector over 127 joints costs
508 bytes/frame, about 178 KB for 350 frames; six limb floats cost only 8.4 KB for
350 frames. Storing full vertices is much larger (12×vertex-count bytes/frame).
Joint projections/depth and hand gates are cheap; mask caching can be optional.

Reproducibility, debugging and Save/Reopen favor persisting compact evidence rather
than transient recomputation. Masks belong to analysis observations; model gates
belong to generated pose evidence. A future neutral reconstructor consumes these,
source/Character identity, boundaries and user-authoritative edits. Blender UI and
retargeting should not infer visibility or own smoothing. **No ProjectState schema
or protocol change was made in this pass.**

## Recommended next experiment — stop for review

Do not deploy smoothing or call the trigger solved. Two low-cost candidates earned
a follow-up: **negative mask support along a limb** and **hand/body agreement with
the existing fusion gates**. They should be typed supporting signals, not renamed
visibility probabilities or proof that an unflagged joint is visible.

1. Independently review/correct the provisional annotations and include a second
   short, fully visible fast right-hand/right-foot sequence. Keep a held-out span.
   This can use existing clips; no new perception architecture is required.
2. Prototype a provider-side research sidecar exposing existing hand gates,
   projections and mask support, without changing reconstruction/fusion decisions.
   Compare joint-specific failure cases and calibration on held-out labels.
3. Only if that validates sufficiently precise low-reliability detection, test
   per-limb graded reconstruction offline: preserve reliable visible frames
   exactly; soften only flagged spans; use two verified anchors and quaternion
   interpolation in a coherent local-rotation/FK representation. Unknown visibility
   remains unknown; no indefinite holds or unsupported long-span bridges.
4. Split at explicit review keys, source/Character/identity/recovery discontinuities
   and authoritative repair/native-edit boundaries. Hand-gate acceptance alone
   must not authorize an anchor. Protect native Depth, root/camera policy and raw
   arrays. Expected numerical postprocessing is local/linear in frames and joints;
   measure it separately from perception.

For a later larger provider study, GEM-X Body Motion is worth considering after
its exact runtime/assets are reviewed. It is not the next small visibility fix.

## Validation, files and preservation

- 80 lossless source frames; 560 visual label entries including control, with
  explicit uncertainty and per-joint class coverage limitations.
- Six metric/support math checks; all 80 exact-box mask associations verified.
- 80 instrumented body frames reproduced cached pose output within 0.0456°;
  no private engine or Blender imports in either perception probe process.
- Five SmoothNet invariants passed: rotation encoding round trip, strict state
  loading, anchor preservation, unchanged unselected joints, finite SO(3) results.
- Initial body-probe launch failed on Windows stdout encoding when an upstream
  timing print used box-drawing characters. Corrected only the diagnostic harness
  to UTF-8 and reran all 80 frames successfully. No production fix was needed.
- Scripts compile; JSON and artifact alignment/preservation checks recorded in
  `verification.json`. Production suites were not rerun for a research-only pass;
  no new product-suite totals are claimed.
- This report is the only new non-ignored repository file from the current task.
  Research scripts, frames, masks, labels, outputs, upstream snapshots/licenses,
  logs and the reference checkpoint remain under the ignored R&D directory.
- Owner `.blend` and its complete 422-file companion were hashed before and after.
  Existing prior-task code edits/reports were left in place. No owner keys,
  assignments, native animation, source FPS or recovery state were changed.
- **No production change, package installation, commit, deployment, push, tag or
  release.** No normal manual before/after animation handoff is ready yet.

Key artifacts: `annotations.*`, `signal_rows.csv`, `signal_results.json`,
`hand_gate_rows.csv`, `hand_gate_results.json`, `motion_support_results.json`,
`smoothnet_results.json`, `mask_capture.json`, `visibility_pilot.png`, overlays,
source snapshots/provenance and `verification.json`. Upstream Git revisions and
downloaded text hashes are retained in `external/*_head.json` and source manifests.
