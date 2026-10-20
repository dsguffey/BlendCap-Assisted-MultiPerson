# VTA Mixamo FK V1

This development-branch workflow bakes one generated VTA Character's **final
evaluated motion** onto a compatible Mixamo armature. Owner visual acceptance
is pending; this is not a Public 1.0 release.

## Use

In a loaded VTA Scene, import a compatible Mixamo FBX, select the desired VTA
Character, then use **VTA Commands → Retarget Animation to Mixamo…**. Choose
the source Character armature, target armature and inclusive frame range. The
dialog reports the target bone match. A completed operation assigns a **new
normal FK Action**. Esc cancels; failed/cancelled work is discarded.

The previous Action is not edited, including keys outside the requested range.
The new Action holds a `vta_previous_action` ID reference so the previous Action
remains saved and available in Blender's Action selector. Choosing that Action
restores its animation. No NLA strip, live constraint or helper rig is retained.
The output needs no private runtime to play or export as ordinary animation.

## Supported target

Local, editable, unparented Mixamo FK armature; positive uniform object scale,
including centimetre FBX imports and rotated objects. Standard `mixamorig:`,
`mixamorig`, unprefixed names and unambiguous separator namespaces such as
`mixamorig1:` are recognized. Required body bones: Hips, Spine, Spine1, Spine2,
Neck, Head; both Shoulder/Arm/ForeArm/Hand and UpLeg/Leg/Foot chains. Exact
Mixamo parent relationships are required. ToeBase is optional; fingers are not
solved. Bone rotation mode must be quaternion, pose scale neutral, and normal
inheritance options enabled. Object transforms and edit-bone rest data are
never applied or rewritten.

Targets with constraints/control rigs, drivers, NLA/tweak mode, animated
armature data, mirrored/nonuniform/sheared transforms, animated object
transforms, ambiguous namespaces or missing required bones are refused before
publication. Ordinary previous pose Actions are supported. Unsupported targets
should be copied/prepared explicitly by the user, not silently normalized.

## Implementation boundary and motion policy

The private core authorizes the Character/source and emits a JSON-compatible
`vta-final-motion` v1 snapshot: opaque IDs, FPS, inclusive frame range, semantic
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
and object motion. Its displacement from reference rest is transferred **once**
to target Hips, using target/source rest-leg-length ratio. This preserves target
proportions without copying source per-bone scale. Absolute world placement is
target rest placement plus this scaled displacement; independently retargeted
Characters of different proportions are not an interaction/contact solve.
Existing source jumps remain jumps. FK keys are sampled per integer frame with
constant interpolation and existing quaternion sign-continuity handling.
The fresh Action explicitly keys neutral non-pelvis locations and unit body
scales so old pose-channel values cannot leak into the new bake.

All baking and validation occur on disposable source/staging objects and a
fresh Action. The real target receives that Action only after every frame is
validated and source/project/target safety guards still pass. Cancellation,
mid-write failure and stale-state refusal remove staging resources and restore
the previous target state. Unregister also cancels in-flight work.

## Scope and validation

Automated tests cover final Object Mode corrections, A/T calibration, namespaces,
missing bones, rotated centimetre targets, root once-only, quaternion continuity,
old Action preservation, cancellation and intentional partial-write failures.
Local real-asset validation compares 22 bones over 163 frames against the frozen
disposable oracle and checks fresh-process persistence. The third-party Mixamo
QA asset/media remain local, not distributed with this repository.

Not certified: arbitrary rigs, Rigify/ARP/CloudRig, IK, finger/face solving,
advanced twist distribution, foot/contact locking, collisions or a profile editor.
Static fingers, proportion differences and source reconstruction artifacts are
expected V1 limitations, not repaired by retargeting.
