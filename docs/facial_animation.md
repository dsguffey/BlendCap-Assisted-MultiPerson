# Facial animation V1

This is approximate expression capture from the **same VTA source**, not facial
scanning, phoneme-accurate lip sync, head replacement or universal rig conversion.
Production implementation is available on `codex/facial-animation-v1`; owner
visual acceptance with a compatible facial mesh is still required. No Public 1.0
release is implied.

## Owner workflow

1. Load the source-backed VTA Scene and select the intended Character row.
   Assign/Update Tracking normally; existing Character evidence is the identity
   authority. No separate face identity or manually labelled blink is needed.
2. Import/select a local mesh with relative ARKit-style named shape keys in that
   same Scene. With exactly one Character row selected, use **Bind Active Facial
   Mesh** in the Animation section of VTA Review.
3. Set the existing Animation Start/End controls, select the desired bound
   Character row(s), then **Generate Facial Animation Range**. The range is
   inclusive and uses the existing source/Blender offset, without rounding FPS.
4. Scrub and inspect the result. Cancel retains the previously published output.

Binding persists as Blender object metadata with an independent binding ID and
project/source/Scene/Character IDs. Object/Scene display names are not identity.
One mesh per Character is supported. Shared, linked, cross-Scene, duplicated or
ambiguous targets are refused. Regeneration does not reassign an existing output
binding; use an independent target when a binding/target must change.

Names match case-insensitively with optional underscores. Known canonical
`Left`/`Right` channels also accept explicit `_L`/`_R` suffix aliases, for example
`eyeBlink_L` -> `eyeBlinkLeft`. The binding stores the original target key name;
neither mesh names nor internal evidence are renamed. A canonical key and its
alias on the same target are ambiguous and rejected, with no preferred winner.
The 51 measured
expressions are mapped by name, not index; `_neutral` is auxiliary raw evidence,
not an animated control. `tongueOut` is unsupported and left untouched. Missing
expression names and unanimated target keys appear in operation Details. Partial
compatible targets are supported; there is no automatic facial-rig construction.

## Evidence and missing faces

An independent MediaPipe process receives plain source/assignment contexts; it
cannot import Blender, BlendCap or private VTA modules. It uses only assigned
Character/head ROIs. Multiple plausible faces or another Character's overlapping
context are ambiguous, never selected by confidence, size or order. Unsupported
head context is unavailable; there is no full-frame identity fallback.

Immutable raw JSON records observed/not_detected/ambiguous/unavailable/failed
states, source frame and exact source-time timestamp, body assignment provenance,
face/crop coordinates, named weights, neutral auxiliary value, model/package and
configuration identity. Missing observations have null weights, not measured
neutral. Provider-wide failures reject the operation before publication.

The private core derives animation independently for each validated Character run:

- Benign missing time of **at most 80 ms**, bounded by observations, interpolates.
  This permits two absent samples at 25 fps or four at 60 fps, not dozens.
- Longer benign absence eases to zero/default expression over **120 ms**, holds
  neutral, then eases back over 120 ms when observations return.
- Ambiguity, failed evidence and hard association boundaries switch to derived
  neutral immediately and reset continuity. They are never bridged. New
  project/Scene/source/Character/provider/model runs are not stitched together.

Derived interpolation/easing does not mutate raw measurements. Neutral fallback
means zero values on compatible expression keys, not a claim that the camera
observed a neutral face. Face size, angle, occlusion and lighting affect quality;
subtle speech and partial blinks can legitimately produce modest weights.

### Temporal stability

Derived animation uses `vta-face-temporal-1`, a centered channel-aware weighted
filter, not a trailing EMA. Its radius is the nearest sample count to 40 ms on
each side (minimum one): three samples total at 25 fps, five at 60 fps. Neighbor
weights fall linearly with distance; segment endpoints remain unchanged. Source
FPS/key times do not change. There is no causal accumulator or left/right mixing.

- Blink coefficients pass through exactly.
- Squint/wide eyelids mix 16% neighboring values, capped at 0.015 weight change.
- Jaw and mouthClose mix 20%, capped at 0.025.
- Brow/eye-look mix 50%, capped at 0.040.
- Smile/cheek/lip shapes mix 50%, capped at 0.050.

Correction caps protect large expression peaks/transitions. Only full windows of
uninterrupted observed samples are eligible. Missing, interpolated, easing,
failed, ambiguous and hard-boundary samples are untouched; windows cannot cross
them. The 80 ms / 120 ms gap policy is unchanged, so identity-rejection pops can
remain. Raw evidence is unchanged. Filtered rows are derived; owned Blender
Actions retain per-frame derivation/filter provenance separately from raw data.
No smoothing settings are added; manual visual acceptance is still required.

## Non-destructive Blender output

Each target gets a dedicated VTA-owned Action/KEY slot and NLA track. The existing
active user Action/slot and unrelated curves/values remain unchanged. Generation
is refused if user curves/drivers already control matched keys, generated output
was edited/shared, or NLA playback/tweak/solo state is unsafe. It does not silently
overwrite even out-of-range user curves on matched controls.
Matched expression controls must be zero/neutral before first binding/generation:
Blender otherwise resets an existing unkeyed non-neutral pose outside NLA strips.
Such a target is refused unchanged; preserve it by choosing an independent mesh.

Regeneration replaces only the selected samples in the owned facial output;
previous owned samples outside that range remain numerically equal. Disjoint
ranges do not fill intervening frames. New layers stage muted; all selected
targets and the evidence index publish together. Failure rolls back staged layers
and restores prior output. Body, camera, assignments and Scene FPS are not edited.

## Persistence and copy lifecycle

ProjectState remains **0.9**. Separate versioned companion contracts are
`vta-face-evidence-1` and `vta-face-index-1` under `<project>_vta/facial`.
Hash-named immutable JSON plus an atomically replaced index preserve successful
raw runs. Blender stores target bindings and owned animation. Save/reload retains
both; native Save As copies the referenced facial evidence and index into the
independent companion, not transient `facial/jobs` requests, cancellation markers
or worker logs. Existing project operation locking serializes publication.

## Runtime and installation assets

The existing external Python 3.12 runtime with **MediaPipe 0.10.32** is used, not
Blender's Python. No dependency reinstall or OpenCV conflict repair is performed.
The pinned manifest is [`face_models.json`](../face_models.json). Installation
must supply its two model files in the add-on's `checkpoints/mediapipe` directory;
every capture verifies local hashes. Missing/corrupt files produce structured
failure with prior output intact. Capture never downloads models and blocks
network access in the provider. A fresh install must provision these assets;
installer redesign is outside this implementation.

The manifest identifies the upstream model license as Apache-2.0. Distribution
must retain the applicable upstream licenses/notices; this feature does not
change MediaPipe/Blender/component licensing obligations. No FaceitRig-derived
calibration asset, ICT solver/refinement or GPL facial solver is imported into
the private core. Blender application remains public; private VTA owns only
provider-neutral validation, persistence and animation decisions.

## Validation and QA provenance

Automated coverage includes contract and scope rejection, fractional FPS,
time-based gaps, model failures, target conflicts, user Action/slot preservation,
range isolation, repeated application and multi-target rollback. A real external
provider run on the existing assigned close-up yielded 93 observations / 100
frames; same-process and fresh-process reopen, renamed native Save As and
operator cancellation were checked on disposable copies.

Those copies contain a synthetic named-control triangle solely to evaluate
curves. **This is a stress-test/diagnostic fixture and is not suitable for normal
visual QA.** They are not owner handoffs. The persistent Desktop/QA.blend and
QA_vta are unchanged; visual QA requires an actual compatible facial mesh in the
existing `VTA - Woman Talking/Smiling` Scene, not recreating assignments.
