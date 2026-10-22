# Native retarget adapter follow-up — 2026-10-22

## Disposition

UE5 Manny/Quinn and Godot Humanoid now use the existing native FK path.
ARP and CloudRig remain **detected/refused**, not production bake adapters.
The requested named-profile list is **not feature-complete**; no feature-freeze,
main promotion, tag or release is claimed. Mixamo and Rigify body QA remain
accepted. Generic/custom mapping, external integration and new rig families are
not implemented. Evaluate one mature external generic retargeter separately
before Public 1.0; this pass does not perform that integration/evaluation.

## Supported additions

| Detail | UE5 Manny/Quinn | Godot Humanoid |
|---|---|---|
| Detection | Exact pelvis + spine_04/05 + neck_02 signature; fixed five-spine/two-neck parents | Exact Hips + UpperChest + LeftUpperArm signature and standard parents |
| Animated layer | Ordinary local quaternion skeleton, not Unreal Control Rig | Ordinary local quaternion skeleton in Blender, not a Godot BoneMap adapter |
| Body coverage | 20 required + 2 optional toes | 20 required + 2 optional toes |
| Optional fingers | 30 phalanges; non-thumb chains require their metacarpal between hand and first phalanx | 30 standard names; thumb Metacarpal/Proximal/Distal, other digits Proximal/Intermediate/Distal; Little, not Pinky |
| Root | Neutral unparented root, pelvis motion once | Neutral unparented Root, Hips motion once |
| Property setup | None; no control switches/drivers | None; no control switches/drivers |
| Proof | Independent extended naming/hierarchy fixture, weighted triangles; not a real Epic mesh | Independent standard naming/hierarchy fixture, weighted triangles; not Godot runtime/export certification |

UE5 maps lower/middle/chest to spine_01/03/05, neck to neck_01 and head to head.
The native intervening spine_02/04 and neck_02 stay locally neutral and inherit
the mapped motion. Both neck bones and all five spine bones are required.
Rest directions and per-bone roll calibrate through the existing engine, not
hard-coded Unreal coordinate transforms. The existing positive uniform object
scale requirement accepts rotated centimetre imports. Metacarpals, twist,
corrective, IK and other unmapped bones receive neutral local channels and
inherit normally: **no twist distribution/corrective solve** is claimed. A mesh
requiring Unreal corrective/PoseDriver behavior still needs cleanup. Missing or
unsupported optional finger ancestry omits that finger and its descendants.

Godot follows its exact
[SkeletonProfileHumanoid convention](https://docs.godotengine.org/en/stable/classes/class_skeletonprofilehumanoid.html).
Spine/Chest/UpperChest are the three source spine levels. Arbitrary renamed
asset skeletons or Godot BoneMaps are outside this profile. No engine/editor,
installer dependency or new export feature is added.

Both retain consistent complete namespaces, strict hierarchy/inheritance checks,
frame-zero imported reference, source-world alignment, uniform sizing and target
proportions. Explicit UE4 cannot accept UE5; Mixamo cannot accept Godot by its
shared Hips name. Mixed control-family signatures/markers cannot bypass refusal
by forcing a skeleton profile. Existing constraints, object/data drivers, NLA,
parented/nonuniform/edited-object-animation safeguards remain in force.
These are not complex driver-preserving control adapters: constrained/driven
targets are refused before staging.

## ARP: evidence and blocker

The owner's existing ARP 3.70.38 archive contains a stock human rig asset.
It was loaded read-only into disposable Blender 5.2 factory startup with owner
scripts disabled; no ARP Python API was imported or installed. It has **335
bones and 130 object drivers**. An older customized owner rig was inspected
read-only first, but was not used as a standard support proof or redistributed.

The stock asset itself reports legacy invalid drivers targeting the obsolete
`custom_shape_scale[0]` path in Blender 5.2. Controllers have Euler rotations,
mechanism/scale-fix parents, FK/IK drivers, and finger base/rotation/curl helpers.
Root hierarchy is c_pos → c_traj → c_root_master.x, with a separate c_root.x;
legacy c_pos XY / pelvis Z splitting is not the VTA root contract. Hand/foot
ik_fk_switch uses 1 for FK, consistent with
[ARP's documented controller behavior](https://www.lucky3d.fr/auto-rig-pro/doc/rig_behaviour_doc.html).
The 30 preset finger names exist, but do not form ordinary direct FK chains.

**Decision boundary:** obtain a healthy standard generated ARP target compatible
with the supported Blender version, then validate a bounded control-state,
anatomical-root and finger-helper adapter. Repairing or silently suppressing
existing rig drivers would violate the preservation requirement. A toy renamed
FK fixture would not prove this graph works. No positive bake, rollback,
save/repeat, deformation or isolation certification is claimed for ARP.

This is currently a **compatibility/proof blocker, not a demonstrated proprietary
API requirement or licensing prohibition**. The inspected archive's own license
marks the stock armature assets CC0; that is provenance for this local audit,
not permission to bundle other ARP materials. No code, asset or model from ARP
is included in the commit. Do not generalize this old version's graph to current
ARP releases.

## CloudRig: evidence and blocker

Downloaded the small official **2.2.31** extension into ignored disposable
research storage, SHA-256
`dbcc777d9f33dee6174bd1ae49982e58f602dc8e9aa2a4be96b1fe7fd659d18b`.
It is GPL-3.0-or-later, per its included license and the
[official extension listing](https://extensions.blender.org/add-ons/cloudrig/).
The actual downloaded version/manifest governs this audit, not a cached web
listing. Its stock Cloud Human was generated in factory startup with disposable
configuration paths, no owner preferences/install changes: **474 bones, 516
object drivers**. Generation completed with two upstream warnings; this is an
audit fixture, not a certified VTA output. Modern CloudRig is standalone, not
merely a Rigify feature-set clone; see the
[official introduction](https://studio.blender.org/tools/addons/cloudrig/introduction).

The real graph has TORSO-Spine/HIP-Spine and **two spine FK segments**,
FK-Spine/FK-Chest. The VTA source contract has pelvis plus three spine levels.
Spine_ik and four ik_left/right_arm/leg properties are normally 1 and require
0 for FK; hinge/follow, stretchy IK, ARMATURE parent blending and STRETCH_TO
deformation dependencies also remain. First finger controls use Hand or
FK-Finger_*_Carpal parents, not the old presumed direct mapped Hand control.
All 30 preset phalange names exist; names alone do not prove safe transport.

**Decision boundary:** a real control adapter must explicitly consolidate the
source torso onto this shorter FK chain, calibrate anatomical pelvis/torso
translation, manage keyed FK/follow state transactionally, account for finger
carpals and validate the generated deform graph. The legacy preset maps source
Spine to HIP-Spine and source Hips only to TORSO-Spine location; enabling it would
not preserve the current source-pelvis rotation/root contract. Rigify's fixed
topology/hash and 22 control mapping cannot be reused as certification.
No alternate engine, source-motion contract change, lossy duplicate target map,
driver/constraint edits or speculative torso solver was introduced. A positive
bake/save/repeat/safety/isolation proof is still required, not claimed here.
There is **no identified proprietary-license/API blocker** for CloudRig.

## Validation and preservation

New automated tests cover both supported additions with no prior Action,
existing/outside-range keys, cancel during bake, injected partial-write and
pre-publication failure, discarded staging, editable new curves, calibration,
frame-zero reference, root once-only, repeat scaling, optional/partial fingers,
neutral extra bones, finite correctly weighted deformation, object-scoped four
target isolation, and fresh-process save/reopen/re-bake. Independent fixture
parents are not copied from the production profile table. Control-family
refusal coverage includes current CloudRig object/data markers.

The long disposable proof uses the two frozen existing evaluated VTA wrestling
captures, **163 frames each**, on two UE5 and two Godot structural targets.
It does not rerun tracking or inference. Body matrices with/without fingers are
**exactly unchanged (maximum difference 0.0)**. All targets resolve 22 body + 30
fingers. Maximum pelvis error is <0.0000014 m; maximum calibrated rotation error
is <0.00013 degrees. Skinning stays finite, frame zero matches reference, other
objects stay identical, old Actions remain unchanged. Fresh reopen and repeat
pass. These metrics validate the adapter contract, not source motion accuracy.

Final validation: **683 public + 428 private tests**, zero skips/errors/failures,
zero MediaPipe import attempts. Focused retarget/Rigify/finger/native coverage:
**88 tests**. Existing Mixamo/Rigify/UE4 and finger regressions remain passing.
Tracked Python sources compile; whitespace checks pass. The private core,
normalized contract and BlendCap retarget engine are unchanged.

Local evidence is under `checkpoints/dev-preview/vta_native_adapters_20261021`:
audit inventories, proof/fresh-reopen receipts and full-suite logs. Third-party
assets, diagnostic .blend files, captured snapshots, downloaded code and logs
remain ignored and are not published. The two pre-existing process-proof files
remain untracked. No installed-addon deployment or owner handoff is performed.
The owner QA.blend/QA_vta and standing backup are unchanged (**96 file hashes**).

## Consolidated visual QA planning

Do not create another owner fixture yet. Before accepting new target families,
use a real UE5 Manny/Quinn mesh and a standard-named Godot humanoid mesh: inspect
both Characters on frames 0, 6, 22, 26, 30 and the range end, plus short playback.
Check body placement/size, chest/neck, root, skinned limbs, thumb/finger motion
and asymmetric hands; repeat one target while observing the other. Existing
Mixamo and stock Rigify targets provide the minimum real-model finger checks.
UE4 remains proof-only until real-asset visual acceptance. ARP/CloudRig are not
offered for visual retarget QA until their specific blockers/proofs are resolved.
