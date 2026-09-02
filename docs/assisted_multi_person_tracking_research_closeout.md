# Assisted Multi-Person Tracking Research Closeout

## Status and scope

This document closes the diagnostic research phase for human-assisted
multi-person identity tracking. It records the policy conclusions supported by
the frozen experiments under `checkpoints/dev-preview`. Those artifacts are
ignored local research data; their detailed per-frame measurements remain in
the frozen evaluation reports.

The reported frame counts come from a small number of contiguous video
sequences. Adjacent frames are strongly correlated and must not be treated as
independent statistical samples. The results support an assisted workflow for
the tested material, not a general accuracy claim.

## Experimental evidence

### Alternate-angle yellow and white wrestlers

Frames 0-14 were the clean positive control.

- Geometry produced 13 bidirectionally tracked frames plus two anchors for
  each target. All 30 labeled target-frame decisions were correct.
- Appearance corroborated all 26 tracked target-frame decisions.
- Endpoint-mean joint assignment was simultaneously correct on all 15 frames
  for both crop views.
- Each single-anchor method was simultaneously correct on all 14 evaluable
  frames. Exact-anchor self-reference exclusion made one endpoint unavailable
  to its corresponding single-anchor method.
- Yellow's individual appearance ranking was correct everywhere evaluated.
  White had one individual whole-box/anchor-0 error, which the injective joint
  assignment repaired.
- No complete joint assignment was wrong and no exact assignment tie occurred.
- User-forced ranges 15-38 and 42-52 remained occluded. Frames 40-41 remained
  conservative where geometry was not legally anchor-bounded.

This establishes that appearance can corroborate accepted geometry and repair
a straightforward independent-choice collision.

### Original red and black wrestlers, ambiguous frames 46-51

Human ground truth was red TEMP 3 and black TEMP 2 throughout this interval.

- Black geometry retained six authoritative TEMP 2 results, all correct.
- Red geometry remained ambiguous on all six frames and made no authoritative
  identity decision.
- Red received six non-authoritative TEMP 3 appearance proposals, all correct.
- Every comparable primary joint crop/method result selected the correct
  red/black pair; there were no ties.
- Red's individual appearance ranking was inconsistent: depending on crop and
  reference it was correct on 0/6, 2/6, or 6/6 frames. Black's individual
  ranking was correct on all primary results except one inner/anchor-52 frame.
- Injective joint assignment repaired the correlated independent-choice errors
  without displacing black's accepted geometry.

This is the clearest positive example for a visible, explicitly
non-authoritative appearance suggestion on an ambiguous geometry frame.

### Blinded red/black frames 1-52

The amended frozen ground truth contains 51 ordinary injective frames and one
merged/shared frame requiring abstention.

Geometry remained conservative and correct:

- Red had two authoritative anchors, both correct; 49 frames were ambiguous
  and one was occluded.
- Red's forward hypotheses were correct on 42/42 frames where present, while
  its backward hypotheses were correct on only 7/25.
- Black had 20 authoritative decisions (18 tracked plus two anchors), all
  correct.
- No authoritative geometry decision was wrong. The evaluation contained no
  case where appearance corrected a wrong authoritative geometry result.

Individual appearance results varied materially:

| Crop and evidence | Red | Black |
| --- | ---: | ---: |
| Whole box, anchor 33 | 41/51 | 51/51 |
| Whole box, anchor 52 | 43/51 | 44/51 |
| Whole box, endpoint mean | 46/52 | 50/52 |
| Inner box, anchor 33 | 46/51 | 51/51 |
| Inner box, anchor 52 | 31/51 | 36/51 |
| Inner box, endpoint mean | 46/52 | 51/52 |

Joint assignment was better but not infallible:

- Anchor-33 evidence was correct on all 50 evaluable ordinary frames in both
  crop views.
- Endpoint-mean evidence was correct on all 51 ordinary frames in both views.
- Anchor-52-only evidence produced ten genuinely wrong complete assignments:
  whole-box frames 28 and 29, and inner-box frames 1, 4, 5, 23, 28, 34, 36,
  and 40.
- No exact assignment tie occurred.

Fusion exposed 40 correct red proposals and no incorrect proposal. This covers
40/49 ordinary ambiguous red frames; nine remained unresolved. The merged
frame 25 received neither an authoritative assignment nor a proposal.

### Forced and merged frames 53-66

Frames 55 and 58 had ordinary, separate detections. Eleven other frames had a
merged/shared detection, and frame 59 had no valid candidate for either target.
The latter 12 frames required abstention.

- Every comparable primary joint result was correct on frames 55 and 58.
- The raw joint optimizer still produced assignments on all 12 required-
  abstention frames. Those assignments have no valid identity correctness.
- Some invalid-assignment margins overlapped or exceeded margins on ordinary
  frames, so margin magnitude did not identify the semantic failure.
- Fusion retained both targets as user-forced `occluded` on all 12 frames:
  24/24 forced target-frame abstentions were honored.
- Forced frames exposed no appearance proposal or usable cross-boundary anchor
  evidence. No interval bridged a forced range.
- Red's durable anchors at frames 55 and 58 remained authoritative. Black
  remained `untracked` there.

Forced occlusion is therefore an essential human-authored semantic input, not
something that can safely be inferred from appearance scores.

### Final reacquisition holdout, frames 165-187

The holdout contains:

- 15 ordinary injective frames, 166-180;
- five structurally insufficient one-candidate frames, 165 and 181-184; and
- three semantically insufficient frames, 185-187, where TEMP 1 contains both
  wrestlers and TEMP 0 is a hand/body fragment.

Ordinary simultaneous joint correctness was:

| Crop and evidence | Correct frames |
| --- | ---: |
| Whole box, anchor 33 | 12/15 |
| Whole box, anchor 52 | 12/15 |
| Whole box, endpoint mean | 13/15 |
| Inner box, anchor 33 | 12/15 |
| Inner box, anchor 52 | 12/15 |
| Inner box, endpoint mean | 13/15 |

Across the 90 primary crop/method/frame results, 16 complete assignments were
wrong. These are correlated measurements over the same 15 source frames.

- All five one-candidate frames were rejected by all six primary crop/method
  combinations: 30/30 structurally unevaluable results.
- All 18 primary results on frames 185-187 produced a complete injective
  assignment despite there being no valid two-person ground truth.
- Both target galleries independently preferred merged TEMP 1 in every such
  comparison. Injectivity forced the other target onto the fragment.
- Invalid assignments were not reliably low-margin. Inner/anchor-33 frame 186
  had margin `0.1655473824181719`, above that method's ordinary-frame maximum
  of `0.14512410302719791`.
- Actual fusion kept both targets `untracked` on all 23 frames and produced no
  proposal or promotion.

The diagnostic counterfactual in which untracked targets were made eligible
for the existing unanimous rule produced:

- nine correct ordinary proposals;
- six ordinary abstentions;
- five structurally insufficient abstentions; and
- two semantically invalid proposals, on frames 186 and 187.

Frame 185 abstained because the primary crop/reference results disagreed. This
holdout rules out automatic reacquisition with the current evidence. It also
shows that raw candidate count is not a person-validity test.

## Final research decisions

| Question | Decision |
| --- | --- |
| Automatically promote identity from appearance? | No. Individual and complete joint errors remain. |
| Automatically reacquire an untracked target? | No. The holdout produced unanimous fragment/merged false proposals. |
| Select a numerical confidence threshold? | No. Invalid and ordinary margins overlap, and the footage is too limited and correlated. |
| Show non-authoritative appearance suggestions? | Yes, for ambiguous geometry under the validated consensus policy. |
| Require human post-occlusion anchors? | Yes. This is the version-one reacquisition mechanism. |
| Preserve forced occlusion and merged/no-valid semantics? | Yes. They are required safety inputs. |

Appearance may corroborate geometry or help a human choose a candidate. It may
not create an authoritative target identity, overrule accepted geometry, or
bridge an occlusion.

## Version-one evidence policy

Anchor files must reject any anchor inside a forced-occlusion range, so a legal
anchor and a forced directive never compete on the same frame.

1. A user-forced frame is `occluded`, with a null box and no appearance
   proposal. Geometry and temporal evidence cannot enter or cross the range.
2. A durable user anchor outside all forced ranges is authoritative. Its
   durable identity evidence is the frame, clicked point, and exact box
   snapshot.
3. A geometry result is authoritative only when validated forward and backward
   hypotheses select the same sidecar candidate inside a legal anchor-bounded
   interval.
4. Ambiguous geometry remains `ambiguous` with a null authoritative box. An
   injective, evaluable, untied, compatible, unanimous primary appearance
   result may be exposed only as `candidate-proposed-unthresholded`.
5. `occluded` and `untracked` geometry require human intervention. Appearance
   does not reacquire them in version one.
6. Tied, missing, null, incompatible, insufficient-candidate, merged/shared,
   or fragment evidence remains unresolved. A runner-up assignment is never
   searched merely to manufacture compatibility.

TEMP indexes are frame-local positions only. They must never be persisted or
interpreted as actor identities.

## Assisted-MVP boundary

The research-complete components required by the assisted MVP are durable
per-target anchors, conservative bidirectional geometry, explicit forced and
merged/no-valid semantics, injective non-authoritative appearance suggestions,
human post-occlusion anchoring, and hash-bound revision history.

The remaining MVP work is engineering: a usable review interface, applying
human decisions to immutable anchor/track revisions, bounded interval
regeneration, and eventually making target-specific capture consume only an
authoritative reviewed track while refusing unresolved spans.

The following research is deferred and must not block the assisted MVP:

- person-validity or box-quality classification;
- automatic fragment/body-part rejection;
- automatic reacquisition;
- numerical threshold calibration;
- broader independent cross-clip evaluation;
- alternative or ensembled appearance models;
- HSV or other appearance fusion; and
- automatic merged-person detection.
