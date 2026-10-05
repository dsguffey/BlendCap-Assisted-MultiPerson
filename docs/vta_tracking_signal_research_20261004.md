# Bounded tracking signal research — 2026-10-04

## Decision

**Keep V1 conservative and human-assisted. Do not promote color, OSNet,
velocity, flow, or the tested gate exceptions into production tracking.**
None of these fixed prototypes removed the known semantic errors while
improving sustained post-contact tracking. Crop/reference agreement is not
independent proof of identity. No confidence threshold was selected.

The smallest next production action is a bounded, human-authored recovery
workflow using existing review keys: inspect the actual discontinuity, choose
a valid separated-person observation, add an explicit anchor, and preview the
affected bounded update. Require a human to accept/correct the result; do not
promise that one anchor per contact episode is sufficient. The three already
tested corrections demonstrably were not sufficient. Where no valid actor
candidate exists, recovery/redetection is a separate later operation, not a
tracker threshold change. This report does not implement either workflow.

## Provenance and separation

- Public UI commit, independently verified and locally committed before research:
  `2882beddc015b16b638abfd3d4656820737ced5f`.
- Private core: `39484e805db2c7e9b19606988475bec8cbba2aee`.
- Public branch: `feature/vta-core-adapter-proof`; private: `adapter-seam-proof`.
- No production tracker, private core, schema, UI, provider, or thresholds changed.
- Harness: `tools/vta_tracking_signal_research.py`; synthetic tests:
  `tests/test_vta_tracking_signal_research.py`.
- Disposable reports: `checkpoints/dev-preview/vta_signal_research_20261004/`.
  `run01` is the initial measurement; `run02` is the finalized reproduction.
- Original fixture: `Visual Smoke Fixtures/VTA_Tracking_Wrestling_0_162/`
  `VTA_Overlay_Wrestling_vta` in the local Video-to-Animation parent directory.
- Original project hash:
  `f9d8db3bc0caf2d73db9cf2e5e78054f9a58608f675421e49b4b10aca5c902df`.
- Cache hash:
  `3311ff26b171188ec9761e0ddd1bed3368c2b30e9ec29f88c043b29e6c1cacae`.
- Frozen scoring truth:
  `fddfcb7746c990fd95175c2da01ad3098d08d5f23f71b61c0777f70b49d5362b`.
- Source `Wrestling1_altAngle.mp4` hash:
  `83d1aad4cbebdf15ee3305bc1eda0c15c2f4cc418e97da7a01cf429b2eb041bb`.
- OSNet checkpoint hash:
  `8a07e8da38946f7cee37f4561617bf8b6d2fe8f3a4027852893ea092e46d919f`.
- Run02 plan:
  `a60c07f372793b0f4f4e39f1c2c44511dfc4a5b0b5ddab11415ca73daa669ab4`.
- Run02 predictions:
  `84fea4613ab049aa7c4cb1e4798da9fbb22f2c4d2b6477ec054de773fb4933c9`.
- Run02 evaluation:
  `5b9c35c003e23e3a2ae34da509fba6a3bf01e44760b47ead87fbb915457dd571`.
- Run02 feature-cache hash:
  `d2e5edcea1417d5045f0ae8a16b9bb562d526f625f16ee179db6ec62421a5e37`.
- Run02 independent appearance measurements:
  `49e7d78512d102fded0bd6ca78c748ede8a6300650bb65752cfe72f956fc8492`.

The plan records the complete command, interpreter, source hashes, method
specification, HEADs and unchanged production thresholds before extraction.
Prediction functions do not accept labels. Predictions are written before
scoring opens the label file. This is **not a newly blinded holdout**: known
failures informed the research question. No rule/threshold sweep or tuning
followed scoring. Reports remain ignored and must not overwrite frozen inputs.

## Baseline and scoring

The actual private tracker was rerun unchanged. Its JSON is byte-identical to
the frozen geometry baseline:
`ae80ceb54387622d8c441681ca0476d2a51b9afb7339d82c7a66c4f57aa3a018`.
The original semantic evaluation summary also reproduces exactly. The
experimental geometry mirror matches **every record, box, score and reason**
of both the initial and prior corrected-core snapshots, with automatic checks.

All 163 source frames were decoded sequentially and all 1,984 cached candidate
boxes used unchanged. Canonical lower-left pixel xyxy is transformed only for
read-only image slicing. Features cover the entire clip, including contiguous
neighborhoods of frames 13/17, 61–91 and 91–121. Semantic scoring is restricted
to the existing 20 human-labelled Blender frames. No additional identity truth
was inferred from geometry, descriptors, flow, or nearby images.

Frame numbers below are **Blender-visible, one-based** unless explicitly
called source indices. Two Characters provide 38 independently scored cases
after excluding their frame-1 anchors: 32 candidate-labelled cases and six
no-valid-candidate cases. These are correlated observations from one clip,
not 38 independent statistical samples. Frame 141 Yellow's accepted set is
preserved. Outside-derived-scope cases are not silently removed.

## Fixed prototypes

- Geometry: unchanged core matching and five gates.
- Velocity: last two adjacent boxes predict center and log width/height;
  upstream matcher gates apply to predicted geometry. Resets at each anchor;
  no missing-frame bridge and no velocity estimated across a hard key. A full
  Kalman filter was not justified after this simple predictor failed the targets.
- Color: L2-normalized 2x2 spatial HSV histograms, 12x4x4 bins per cell.
- Neural appearance: existing OSNet-AIN x1.0, local MSMT17 weights, CUDA,
  existing 256x128 normalized-image preprocessing. No new model/download.
- Appearance references: available left/right candidate anchors of the local
  scope. Whole box and central 60%-width/80%-height crop must each independently
  select the same unique candidate for every reference. Exact ties or missing
  descriptors abstain. This is a qualitative consensus experiment, not a
  confidence rule.
- Rank: appearance chooses the candidate; selected candidate still passes
  production IoU, center, area, and minimum geometry-score gates. It does **not**
  enforce the original geometry runner margin; this changed experimental
  decision rule is intentionally reported rather than sold as unchanged policy.
- Veto: original geometry winner is withheld if appearance does not agree.
- Exception: unanimous appearance can bypass **only** the area-fold gate;
  IoU, displacement and minimum geometry score remain. No global threshold edit.
- Color+OSNet exception: both families must agree across crops/references.
- Flow: half-resolution grayscale, up to 40 inner-box corners, pyramidal LK,
  forward/backward consistency, median valid translation; then unchanged
  geometry matcher on the translated box. Technical LK settings are fixed in
  the plan, not calibrated on semantic results. No dense-flow subsystem.

All directional variants stop at their first abstention. Two usable bounding
anchors require bidirectional agreement, as in production. Human hard keys
reserve candidates; simultaneous derived conflicts would withhold both claims.
No conflict occurred and no hard-boundary violation was detected. There is no
automatic reacquisition after a stopped direction. These experiments measure
bounded continuity after an explicitly supplied anchor, not automatic births.

## Semantic results

### Initial frame-1 anchors: 32 valid-candidate cases

Wrong includes swaps and other wrong candidates. Six correctly conservative
no-valid cases are additional to this table and remain unresolved for every row.

| Variant | Correct resolved | Wrong resolved | Unresolved valid | Outside scope |
| --- | ---: | ---: | ---: | ---: |
| Geometry | 4 | 2 | 22 | 4 |
| Velocity/history | 4 | 2 | 22 | 4 |
| Sparse flow | 4 | 2 | 22 | 4 |
| Color rank / veto / exception | 3 | 2 | 23 | 4 |
| OSNet rank / exception | 4 | 1 | 23 | 4 |
| OSNet veto | 3 | 1 | 24 | 4 |
| Color+OSNet exception | 2 | 1 | 25 | 4 |

All variants retain Yellow's frame-13 semantic swap. Color also retains the
White wrong candidate there. OSNet abstains for White instead: one error is
removed at a coverage cost, but the principal swap is not prevented.
OSNet rank obtains three Yellow matches and one White match instead of the
baseline's two per Character; it is not a net correct-coverage gain.

### Previously authorized correction/reacquisition anchors

Only the earlier three supplied keys are used: paired frame 13 and Yellow
frame 91. They are copied from existing durable authority, never mined from
the scoring sheet. All supplied anchors are excluded as tracker successes.
29 candidate cases remain (14 Yellow, 15 White), plus six no-valid cases.

| Variant | Correct resolved | Wrong resolved | Unresolved valid | Outside scope |
| --- | ---: | ---: | ---: | ---: |
| Human anchors + geometry / velocity / flow | 0 | 1 | 22 | 6 |
| Human anchors + color or OSNet veto | 0 | 1 | 22 | 6 |
| Human anchors + color or OSNet rank | 0 | 3 | 20 | 6 |
| Human anchors + appearance exception (either/both families) | 0 | 3 | 24 | 2 |

White's existing frame-17 swap survives all variants in this setup. Appearance
ranking and exceptions additionally swap White at frames 19 and 25. Every
tested human-anchor variant keeps frames 106/121 unresolved or outside its
published scope. There is **no independently scored post-contact reacquisition**.

Cross-scheme comparisons must use the stored `common_unanchored_evaluations`:
exclude all three new anchor cases from both schemes. On those same 29 valid
cases, initial geometry has 4 correct, 0 wrong, 21 unresolved and 4 outside;
corrected geometry has 0 correct, 1 wrong, 22 unresolved and 6 outside. Do not
credit frame-13/91 human assignments as signal improvements or hide their
baseline errors when describing the original 32-case result.

### Critical frames and the discontinuity

- Frame 13: all initial-anchor variants retain Yellow's swap; OSNet withholds
  White. In the corrected scheme frame 13 is supplied authority, not scored.
- Frame 17: initial geometry/velocity/flow match both correctly; color withholds
  Yellow, OSNet withholds White. In the corrected scheme White swaps for every
  tested rule; Yellow abstains. Correcting a key did not make subsequent evidence
  sufficient.
- Blender frame 91 = source index 90: supplied Yellow reacquisition key in the
  corrected scheme. Do not confuse this with its **first step at source 91 /
  Blender 92**.
- Source 91: production IoU `0.416935`, normalized center displacement
  `0.207665`, area fold `2.187263 > 1.9`. Geometry, velocity, flow and
  non-exception appearance remain stopped. Appearance exceptions select the
  same candidate with geometry score `0.4495612799525628`, walk through source
  94, then stop at source 95. There is no human label at source 91–94: passing
  that step is **not** demonstrated semantic correctness. Later labelled
  frames 106/121 do not improve. Conditional relaxation is not established safe.

Independent appearance diagnostics further show reference sensitivity. With
all supplied anchors excluded, initial Yellow reference gets 8/14 correct
with either color crop and 11/14 with either OSNet crop. Initial White gets
5/15 with color, 4/15 whole OSNet and 5/15 inner OSNet. Yellow's frame-13
reference gets 0/14 with every tested descriptor/view, despite being an
authoritative human choice; White's frame-13 OSNet reference gets 1/15.
This does not invalidate human identity authority. Candidate crops can carry
pose, other-body, background or reference-specific information; no single
cause was established. A human-labelled box is not guaranteed to be an
identity-pure appearance template. Absolute/ranked scores for every source
frame, view and reference are stored in `appearance_measurements.json`.

## Detector boundary and human burden

Frames 67/73/81 have no valid separated-actor observations in the frozen
truth; their raw candidate counts are **2, 1, 2**, respectively. Extra boxes
do not prove two usable people. They remain unresolved for both Characters
in every variant. Tracking cannot split a merged actor or manufacture an
observation; never assign a merged box to both Characters.

The known contact neighborhood is one sustained difficult episode, not three
independent merge events. The clip ends in substantial overlap: do not claim
a demonstrated clean separation or general recovery cadence. Three extra
human keys fixed their exact frames, not a useful derived span. Determining
how many further anchors would be practical needs human judgement at actual
discontinuities and additional separation footage; no unprovided semantic
labels/anchors were invented. Choosing among clutter is cheaper than fixing
merged/missing person observations, but actual human correction time was not
measured.

## Measured cost and cacheability

Windows, existing development Python, Torch `2.7.1+cu128`, OpenCV `4.13.0`,
NumPy `2.4.3`, RTX 5070 Ti. No dependency installation or detector rerun.
Two whole-clip extractions produced identical keys, color descriptors and
OSNet values (maximum absolute OSNet difference 0), and all 20 prediction
sets matched exactly. Each matcher also repeats identically in memory.

| Operation | First run | Reproduction | Device / incremental burden |
| --- | ---: | ---: | --- |
| Decode 163 frames | 0.512 s | 0.436 s | CPU; existing decoder |
| 3,968 spatial color descriptors | 0.899 s | 0.863 s | CPU; no weights |
| OSNet initialization | 9.30 s | 3.10 s | CUDA; 17,293,009-byte checkpoint |
| 3,968 OSNet embeddings | 20.57 s | 6.89 s | CUDA; 357,129,216 peak allocated bytes |
| Adjacent bidirectional sparse flow | 14.33 s | 13.61 s | CPU; no weights |
| Cached bounded matching per variant | 0.38–1.15 ms | same order | CPU; early-stopping short walks |

Timings are wall-clock observations, not controlled isolated microbenchmarks.
OSNet averages approximately 1.7–5.2 ms per crop, color 0.22 ms per crop;
paired crops double per-candidate extraction. CUDA peak is PyTorch allocated
memory, not whole-driver VRAM. CPU neural inference was not benchmarked.
Grayscale working storage here is 84,499,200 bytes; streaming adjacent frames
would reduce that, but this prototype retains the clip for bidirectional flow.

Float32 prospective payloads: color 768 values/crop, 6,144 bytes/candidate for
two views, 12,189,696 bytes for this cache; OSNet 512 values/crop,
4,096 bytes/candidate, 8,126,464 bytes total. Current research stores float64
arrays (twice those raw payloads); compressed paired cache is 10,547,310 bytes.
This excludes keys/provenance/serialization overhead. Motion history requires
only two boxes per Character. Cached per-candidate adjacent flow vectors can
avoid rerunning optical flow on Update Tracking; they need source/adjacent-frame
provenance and validity flags, not just an unqualified motion vector.

If a signal becomes justified, prefer Analyze-time optional feature extraction
and an independently versioned/hash-bound feature sidecar, then cheap matching
on Update Tracking. No model should reload for each update. Do not add any
production cache schema now. This benchmark uses disposable feature arrays;
flow is computed once per extraction and reused in memory.

## Reusable encoders and packaging

Existing SAM3 retains aligned **256-dimensional decoder-query embeddings**
in the isolated Kinesia adapter, but the current VTA candidate cache has no
embeddings. They cannot be recovered from boxes/scores alone. Future reuse
during an already-required Analyze would avoid a separate encoder pass,
with nominal float32 payload of 1,024 bytes/candidate (2,031,616 bytes here),
but these are detector-query features, not proven identity descriptors.
This pass did not rerun SAM3 or validate their tracking accuracy.

Installed assets include SAM3 `sam3.pt` (3,450,062,241 bytes), SAM 3D Body
`model.ckpt` (2,109,129,346), MHR `mhr_model.pt` (696,110,248), and MLX SAM3
(3,402,867,661). DINOv3 code is vendored in the SAM 3D Body tree. No standalone
generic DINO identity extractor/checkpoint was established in the bounded
inventory; extracting a backbone from the body model is not a drop-in
candidate-feature API. No SAM 3D Body reconstruction was run.

The BlendCap installation also contains YOLO pose, MHR/body and a 140,550,416-byte
`model.pt`; presence/size is not evidence of a licensed, useful ReID feature
provider. Face/body reconstruction features were not tested as identity
signals: face visibility and articulated contact are methodological risks.
Torchreid and Transformers are not installed; the existing local OSNet
implementation avoids requiring either. Timm alone does not provide weights.

Any provider-specific features should be optional behind a separate
feature-provider interface: immutable candidate key + exact crop/geometry,
source/cache/model/config hash + feature dimension + missing-data status.
The matcher must work with a replaceable detector, and abstain if features
are absent/incompatible. No SAM3 internal tensor should become core authority.

## Licensing / distribution checkpoint

Engineering assessment, not legal clearance. Primary sources checked on
2026-10-04; recheck pinned artifacts before release:

- [OpenCV](https://opencv.org/license/): 4.5+ Apache-2.0. Commercial use and
  redistribution with required license/notices; no model weights for HSV/LK.
  Inspect binary wheel/codec third-party notices too. Already installed on
  Windows; no incremental library download for these prototypes.
- [Torchreid code](https://github.com/KaiyangZhou/deep-person-reid/blob/master/LICENSE):
  MIT; retain copyright/license. The
  [author's OSNet model card](https://huggingface.co/kaiyangzhou/osnet/blob/main/README.md)
  declares MIT and lists the exact local MSMT17 model. Thus code/model sources
  permit commercial use/redistribution subject to notices; preserve the
  checkpoint hash/source and review training-data/privacy obligations separately.
  There is no need to download or bundle another weight for this experiment.
  No production bundling is recommended given the negative results.
- [SAM3](https://github.com/facebookresearch/sam3/blob/main/LICENSE): custom SAM
  License, not Apache/MIT. Covers code and weights; grants use/distribution
  subject to agreement, copy-of-license, research acknowledgement and use
  restrictions. Commercial deployment is conditional, not unrestricted legal
  clearance; gated access is separate. Do not assume arbitrary redistribution
  or auto-download terms from the fact weights are installed.
- [DINOv3](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md):
  custom DINOv3 License covers code/weights with similar conditional grants,
  agreement distribution, research acknowledgement and restrictions. Backbone
  reuse must also respect any enclosing SAM 3D Body/MHR asset licenses. No
  clean standalone DINO footprint/runtime or shipping approval was established.

Human anchors and deterministic box prediction introduce no new model
license/download burden. Existing public GPL/private-core boundaries stay intact.

## Ranked V1 value

| Rank / approach | Swaps / reacquisition | Useful coverage | Cost / install / complexity | Generality / V1 decision |
| --- | --- | --- | --- | --- |
| 1. Human authority + conservative geometry | Explicit reviewed identity; three tested keys insufficient for propagation | Honest unresolved spans | No model/VRAM increment; human cost not timed | Keep V1; require inspection, not assumed continuity |
| 2. Existing geometry baseline | Known swaps remain; no scored recovery | 4 correct / 2 wrong initial cases | Sub-ms matching; zero added assets | Preserve until replacement proven; not semantic proof |
| 3. Cheap color, optional advisory only | Does not prevent target swaps | Color variants lose one correct match | <1 s extraction; ~12.2 MB float32 payload; no new dependency | Supporting clothing cue, not ReID; do not auto-promote |
| 4. Velocity/history | No scored improvement | Same as geometry | Sub-ms; two boxes; zero GPU/assets | Cheapest experiment but insufficient alone |
| 5. OSNet / dedicated ReID | Withholds one initial White error; Yellow swap persists; corrected rank adds swaps | No net initial correct gain | 17.3 MB weights; ~341 MiB allocated CUDA; ~8.1 MB float32 payload | Wrestling generalization failed this test; defer production dependency |
| 6. Sparse flow | No scored improvement; no discontinuity bridge | Same as geometry | ~14 s CPU; validity/provenance complexity | Contact/overlap risk; not enough V1 value here |
| 7. Conditional appearance gate exception | Passes unlabelled source-91 step; corrected swaps increase to three | No scored sustained recovery | Cheap cached matching; high policy-validation burden | Reject automatic exception on this evidence |
| Deferred: generic DINO / SAM query features | Not tested; prior Kinesia IDs not identity truth | Unknown | Existing heavy assets; coupling/licensing/runtime questions | Optional replaceable feature provider only after new hypothesis |

Color's low runtime does not justify shipping a worse automatic matcher.
Distinct Yellow/White clothing is a favorable cue, yet it still fails; no
same-color, cross-camera, lighting-change or cross-clip generality is claimed.
More resolved matches with more swaps is explicitly worse.

## Verification / handoff

15 focused synthetic research tests pass (motion, crop conversion, order/tie,
missing evidence, hard boundaries, directional disagreement, injectivity,
exception scope, scoring denominators/anchor exclusion, immutability and output
restriction/no-clobber). Together with existing semantic-scorer tests, 25/25
focused tests pass. The full public suite runs 343 tests: 337 pass, six skip.
Compile and whitespace checks pass. Both geometry
snapshots match exactly; all 20 variants repeat; all original fixture, scoring,
baseline, corrected-companion, video, weight and tracker-source input hashes
remain unchanged. No private code, production tracking, source fixture or UI
was changed during research. No native desktop control and no push.

Confidence is high in the negative result **for these fixed prototypes and this
cache**, low in general rankings across footage. No newly labelled holdout,
same-color test, clean post-contact sequence or source-91–94 semantic truth was
collected. Those are prerequisites for claiming automatic recovery or choosing
a threshold, not blockers to a genuinely assisted MVP.

Reproduce with the exact command in ignored `run02/plan.json`, changing only
`--output-dir` to a new ignored directory. Use the recorded development Python,
private HEAD, source, checkpoint, original companion and corrected companion.
The harness rejects existing output directories and validates frozen geometry
records before emitting an evaluation. Do not rerun into frozen directories.
