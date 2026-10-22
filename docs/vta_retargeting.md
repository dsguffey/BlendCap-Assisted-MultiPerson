# VTA FK retargeting V1

This development-branch workflow bakes one generated VTA Character's **final
evaluated motion** onto a compatible FK armature. Mixamo (including source-world
size/placement and frame-zero reference) has passed owner QA. Unreal/Epic UE4 is
automated/synthetic-proof validated only. Standard generated Rigify human body FK
has passed owner body/workflow QA. Optional existing captured finger motion is
now carried for Mixamo, Rigify, UE4, UE5 and Godot Humanoid; finger visual acceptance
is pending. UE5/Godot are structural-proof validated, not owner accepted. ARP and
CloudRig remain blocked, not bake-supported. See the [current adapter audit](vta_native_adapter_audit.md).
This is not a Public 1.0 release.

## Use

In a loaded VTA Scene and **Object Mode**, open the 3D View sidebar (**N**) →
**VTA → Retargeting**. Use the native armature-only pickers/eyedroppers
to select the generated VTA Character and imported target. Pick armatures,
not body/clothing mesh parts. Expand **Bone mapping (read-only)** to inspect the
actual source/target names. Automatic detection reports the resolved type;
ambiguous families require explicit Target profile selection. General mapping
edits and arbitrary control rigs are deferred, not silently substituted.
Choose the inclusive Start/End range (frame 1 or later), then **Retarget Animation**.
Frame **0** is reserved for the imported target's original placement,
scale and neutral bind/rest pose (Mixamo T-pose; Rigify's generated rest pose).
Source frame numbers are never shifted.
A completed operation assigns a **new normal FK Action**. Esc cancels;
failed/cancelled work is discarded. F3 search remains available over the 3D View;
retargeting is no longer offered in the Preview menu.

The original BlendCap retarget UI is hidden, not deleted. Its presets, maps,
operators and engine remain available underneath for future skeleton support;
they are not certified as VTA workflows. **Credits** is a separate section at the
bottom of the VTA sidebar, retaining BlendCap's contribution, copyright, GPL
license/notices/source links and its existing donation/support action.

The previous Action is not edited, including keys outside the requested range.
The new Action holds a `vta_previous_action` ID reference so the previous Action
remains saved and available in Blender's Action selector. Choosing that Action
restores its keyed animation. Rigify Actions additionally own the FK/follow
settings described below; an older Action without those keys does not restore
unkeyed switch values automatically. Older pose-only Actions do not own Object Mode channels:
when switching back to one, frame 0 of the new Action supplies the imported object
transform reference if needed. No NLA strip, live constraint or helper rig is retained.
The output needs no private runtime to play or export as ordinary animation.

## Supported target

Mixamo requirements below remain unchanged. The additional **Unreal/Epic UE4 FK**
profile requires the common three-spine/one-neck naming and exact hierarchy,
including an unparented `root` above `pelvis`, 20 required body bones and optional
`ball_l`/`ball_r`. Root stays neutral; composite motion is applied only to pelvis.
The same scale/rest/Action safeguards apply. A distinct **UE5 Manny/Quinn** profile
handles five spine bones, two neck bones and neutral metacarpals; it does not use
the UE4 mapping. **Godot Humanoid** supports exact standard bone names/hierarchy
in Blender, without a Godot runtime. Arbitrary control rigs are not accepted.
Full names, validation status and limitations are listed
in the [profile audit](vta_retarget_profiles.md).

Local, editable, unparented Mixamo FK armature; positive uniform object scale,
including centimetre FBX imports and rotated objects. Standard `mixamorig:`,
`mixamorig`, unprefixed names and unambiguous separator namespaces such as
`mixamorig1:` are recognized. Required body bones: Hips, Spine, Spine1, Spine2,
Neck, Head; both Shoulder/Arm/ForeArm/Hand and UpLeg/Leg/Foot chains. Exact
Mixamo parent relationships are required. ToeBase is optional; fingers are not
solved. Bone rotation mode must be quaternion, pose scale neutral, and normal
inheritance options enabled. Object transforms and edit-bone rest data are
never applied to geometry or rewritten in edit-bone data. Object location,
rotation, scale and delta channels are keyed in the new Action to align the
animated character while preserving its imported reference at frame 0.

For Mixamo/UE4, targets with constraints/control rigs, drivers, NLA/tweak mode, animated
armature data, mirrored/nonuniform/sheared transforms, animated object
transforms, ambiguous namespaces or missing required bones are refused before
publication. An intact VTA-generated alignment Action may be retargeted again;
its recorded imported reference prevents compounded scaling. Edited/generated
object tracks with a stale receipt, modifiers or changed rotation mode are refused,
not silently adopted as a new import reference. Ordinary previous pose Actions are supported. Unsupported targets
should be copied/prepared explicitly by the user, not silently normalized.

### Standard generated Rigify human

Select the **already-generated armature object**, not its metarig or mesh. The
same generic panel detects **Rigify**, resolves 22 body controls, and performs
all FK setup automatically. No generator/API is required at retarget runtime.
The bounded profile is validated against the stock Blender 5.2 human body graph;
complete consistent namespaces and different proportions are allowed. Modified
body topology, ambiguous skeleton families, external dependencies, NLA/tweak
mode, data Actions, driven FK controls and unsafe transforms are refused.
Older/different generated layouts are not implied compatible.

Keys go to normal spine/neck/head, shoulder and limb **FK controls**. The existing
BlendCap evaluator is reused; no DEF/ORG/MCH keys or permanent helpers are added.
Anatomical DEF joint rest points supply calibration and leg sizing because the
reverse-spine FK pivots are not anatomical pelvis origins. Evaluated pelvis
translation is applied once through the normal `torso` location. Object alignment
and frame-zero imported rest follow the same uniform-scale policy as Mixamo.

The fresh Action keys four limb `IK_FK` switches to **1 (FK)**, including frame 0,
and pins the original limb-follow and torso parent/neck/head-follow settings.
This deliberately leaves successful output editable through ordinary FK controls.
The original eleven values are recorded in `vta_rigify_reference` on the new
Action; failure/cancellation restores the pre-operation values. Prior Actions
are not rewritten. Switching to an older Action with unkeyed switches can require
restoring its original settings; VTA does not add Action-switch observers.
Unrelated properties, unresolved fingers/face/tweaks, all generated constraints, object/data
drivers, widgets, rest data and regeneration metadata are preserved. Frame 0
neutralizes the owned body controls, not every generated mechanism/finger bone.

Resolved finger FK chains also key their normal neutral master/tip controls; no
DEF keys, finger IK switches or new finger solve are needed for the stock rig.
Curvature properties and generated drivers remain untouched.

This is **not** IK/contact locking, tweak solving, FK→IK conversion, custom
Rigify feature-set support or regeneration. Similar character proportions remain
the expectation; contact/proportion cleanup is manual.

## Implementation boundary and motion policy

The private core authorizes the Character/source and emits a JSON-compatible
`vta-final-motion` snapshot (v1 body-only, v2 with optional fingers): opaque IDs, FPS, inclusive frame range, semantic
joint tree/rest directions, final global transforms and availability per frame,
plus final-motion provenance/state digest. Matrices are row-major 4×4,
right-handed Z-up metres, with proper unit rotations and evaluated translations.
No tracking, repair, identity heuristics or ProjectState structure enters the
adapter. This is a host API boundary, not a new process-separation claim.

The host samples evaluated global bone matrices (Object Mode corrections,
repairs and existing cleanup included), temporarily enables hidden rigs and
restores frame, pose, properties and visibility. It does not regenerate motion
or change project/evidence files. V1 requires a continuously available range;
unavailable source samples are refused, never bridged.

Public target calibration uses actual rest matrices and anatomical joint-head
directions to reconcile A/T-pose differences. It feeds calibrated source-rest
overrides to the existing BlendCap fast FK evaluator and bulk F-curve writer.
The evaluator's parent quaternion is now in **armature space**, matching its
desired armature-space rotation even when the target object is rotated.
VTA disables generic leg anchoring, posed-height scaling and facial policies.

The evaluated composite pelvis translation already includes source Root/Hips
and object motion. The target is uniformly resized using the source/target
reference rest-leg-length ratio and aligned at the reference pelvis. Animated
pelvis positions then match source world positions **once**, rather than scaled
displacements from the target's imported origin. Source uniform size changes are
derived from evaluated leg segment lengths and applied around the animated
pelvis without scaling its displacement twice. This maintains source scene
placement and relative Character size while retaining the target's proportions.
Child meshes follow the animated armature object; raw mesh geometry/materials,
parent inverse and bind data are unchanged. The expected target is an ordinary
Mixamo import with its meshes parented to its armature, not a custom attachment
or control system. Different proportions still require manual contact cleanup;
there is no IK/contact solve or per-bone stretching.
Existing source jumps remain jumps. FK keys are sampled per integer frame with
constant interpolation and existing quaternion sign-continuity handling.
The fresh Action explicitly keys neutral non-pelvis locations, unit body scales
and neutral unmapped channels so old pose values cannot leak into the new bake.
For plain FK profiles, at frame 0 all pose bones are neutral, including fingers, and the object uses its
original imported transform channels. There is a deliberate constant-key jump
from the reference pose to source-aligned motion, not a transition animation.

All baking and validation occur on disposable source/staging objects and a
fresh Action. The real target receives that Action only after every frame is
validated and source/project/target safety guards still pass. Cancellation,
mid-write failure and stale-state refusal remove staging resources and restore
the previous target state. Unregister also cancels in-flight work.

## Optional captured fingers

The existing BlendCap body exporter already preserves three articulated joints
per thumb/finger, five digits per side: **30 optional finger joints**. Nonrotating
tip markers are not extra phalanges. In the existing wrestling source, both
Characters have all 30 bones and 90 finger rotation curves; measured local finger
motion varies by roughly 4–15 degrees. This does not certify tracking accuracy.

Previously `retarget_source_bindings()` exported only the 22 body joints, the
normalized contract rejected extra IDs, and profiles had no finger rows. Ordinary
FK output neutralized the unmapped fingers. The fix samples **final evaluated**
finger transforms with the body (including Object Mode corrections), normalizes
opaque semantic IDs such as `left_thumb_1` through `left_thumb_3`, and feeds the
same calibrated FK evaluator and staged Action transaction. No detector, hand
solver, smoothing, amplification, contact/grasp inference or raw-evidence edit.

Body-only snapshots retain v1 and their existing numeric representation. V2 adds
optional joints in the same `joints`/`samples[].poses` containers. Missing/invalid
finger rest or parent chains are omitted; missing/nonfinite optional samples are
not fabricated or keyed, and descendants require a present parent. The standard
constant-key evaluator holds the preceding keyed pose across an optional gap;
there is no new gap interpolation or invented observation. Whole-Character/body
availability refusal remains unchanged. Safe valid prefixes/partial hands bake;
missing/unsupported optional mappings do not invalidate the body. Existing global
target safety refusals (for example arbitrary constrained plain-FK rigs) remain.

The generic panel shows a read-only finger mapping count when source bones exist;
the completion message reports mapped/valid-source counts. No dedicated panel or
per-finger settings. Unresolved fingers retain the profile's prior behavior:
plain FK neutral/rest channels; Rigify's unowned current pose. Prior Actions and
their keys stay unchanged/recoverable. Frame 0, source placement/uniform scale,
repeat-bake reference, cancellation and transactional publication are unchanged.

| Family | Finger convention | Current bake status |
|---|---|---|
| Mixamo | `LeftHandThumb1–3`, Index/Middle/Ring/Pinky; Right equivalent | 30 optional; real existing capture/model proof |
| Rigify | `thumb.01–03.L/R`, `f_index`/`f_middle`/`f_ring`/`f_pinky` | 30 normal FK controls; real generated-rig proof |
| Unreal UE4 | `thumb/index/middle/ring/pinky_01–03_l/r` | 30 optional; synthetic UE4 hierarchy proof |
| UE5 Manny/Quinn | 01–03 phalanges; non-thumb chains require their native metacarpal parent | 30 optional; extended skeleton structural proof, metacarpals neutral |
| ARP | `c_thumb1–3.l/r` etc., from preserved BlendCap presets | Naming catalog only; control adapter pending |
| CloudRig | `FK-Finger_Thumb1–3.L/R` etc., from preserved presets | Naming catalog only; control adapter pending |
| Godot Humanoid | Thumb Metacarpal/Proximal/Distal; other digits Proximal/Intermediate/Distal (`Little`, not Pinky) | 30 optional; standard skeleton structural proof, no runtime integration |

Catalogs are not claims of target ecosystem support. Godot names follow its
[standard Humanoid profile](https://docs.godotengine.org/en/stable/classes/class_skeletonprofilehumanoid.html),
not arbitrary user bone names. ARP/CloudRig catalogs remain detection/research
only; their generated control graphs are not certified by skeleton proofs.

## Scope and validation

Automated tests cover final Object Mode corrections, A/T calibration, namespaces,
missing bones, rotated centimetre targets, root once-only, quaternion continuity,
old Action preservation, cancellation and intentional partial-write failures,
different target sizes/positions, child meshes, frame-zero rest, delta transforms,
repeat-bake reference preservation and rejection of edited object tracks.
Local real-asset validation compares 22 bones over 163 frames against the frozen
disposable oracle and checks fresh-process persistence. The third-party Mixamo
QA asset/media remain local, not distributed with this repository.

Rigify validation adds real stock generation, mixed Euler/quaternion FK controls,
198-driver/constraint preservation, property rollback, namespaces, source Object
Mode corrections, fresh save/reopen, editable FK output and finite moving skinned
geometry. The 163-frame wrestling proof preserves source pelvis placement within
0.000003 m and keyed FK orientations within 0.0001 degrees. Known-motion deform
comparison stays within 0.000002 m / 0.70 degrees (stock B-bone tangent differences).
The finger proof transfers 30/30 joints over 163 existing captured frames on a
real Mixamo model, a standard Rigify target and a synthetic UE4 skeleton. Body
matrices are **exactly unchanged** with/without fingers in that comparison.
Finger orientation error is below 0.0004 degrees; all joints remain finite and
show articulation. Save/reopen/repeat and multi-character isolation are checked.
The finger milestone passed **669 public + 428 private**, zero skips/failures/errors, including
74 focused retarget/Rigify/finger tests and zero MediaPipe import attempts;
proof receipts remain local QA evidence.
owner Rigify body/workflow QA passed, finger visual acceptance remains pending.

Not certified: arbitrary rigs, ARP/CloudRig, IK, finger/face solving,
advanced twist distribution, foot/contact locking, collisions or a profile editor.
Missing/damped source finger articulation, proportion differences and source
reconstruction artifacts are not repaired by retargeting.

The subsequent UE5/Godot pass adds extended native hierarchies, neutral extra
bones, independent structural skins, four-target isolation and fresh-process
save/reopen/repeat tests. It leaves the private motion contract and BlendCap
engine unchanged. Current results and the ARP/CloudRig decision boundary are
recorded in the [adapter audit](vta_native_adapter_audit.md). Named-profile
retargeting is **not yet feature-complete** for the requested 1.0 list.
