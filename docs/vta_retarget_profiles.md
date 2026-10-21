# Retarget profile expansion — 2026-10-21

This is a **bounded profile expansion**, not completion/acceptance of all four
requested families. Mixamo FK, its Object
Mode UI and source-aligned/frame-zero behavior have passed owner QA. Unreal/Epic
UE4 FK is automated/synthetic-proof validated only. Standard generated Rigify
human body FK is implemented and real-rig/automated validated, awaiting owner
visual QA. ARP/CloudRig remain diagnosed/refused, not bake-supported.

## Status and chosen target semantics

| Target | Current supported layer/status | Detection / coverage | Root policy |
|---|---|---|---|
| Mixamo | Ordinary local FK skeleton; owner accepted | Unambiguous Hips namespace; 20 required body bones, 2 optional toes | Hips has no parent; composite pelvis once |
| Unreal/Epic UE4 | Local quaternion FK/deform skeleton; automated/proof only | pelvis, spine_01–03, neck_01, head; bilateral clavicle/upperarm/lowerarm/hand/thigh/calf/foot; ball toes optional | Required unparented root; pelvis child of root; root stays neutral, composite motion goes to pelvis once |
| Rigify | Stock generated Blender 5.2 human body FK; real-rig proof, owner QA pending | Generated rig_id, torso/limb FK signatures, 22 controls, stock body dependency topology; no fallback to DEF-name matching | Reverse spine_fk rotations; anatomical DEF pelvis calibration; normal torso location gets composite pelvis translation once |
| Auto-Rig Pro | Live generated FK control adapter **pending** | c_root_master.x + c_arm_fk.l + c_thigh_fk.l signature; known preset variant only | Legacy c_pos XY / c_root_master.x Z split not adopted |
| CloudRig | Live generated FK control adapter **pending** | TORSO-Spine + FK-UpperArm.L + FK-Thigh.L or cloudrig marker; known preset variant only | Legacy root XY / TORSO-Spine Z split and automatic IK conversion not adopted |

Supported FK profiles resolve bone namespaces consistently (empty or a complete
prefix ending in `:`, `_`, `.`, `-`, `|`; historical Mixamo `mixamorig` also works).
Required bones/hierarchy/inheritance are checked; no fuzzy matching, silent
partial bake or fallback. Multiple families require explicit profile selection;
multiple namespaces still require separate target skeletons. Ambiguous Rigify +
Mixamo/Epic structures are refused even with explicit profile selection. Control-family
signatures/markers cannot be bypassed by forcing the Mixamo/Epic profile.

The UI is **3D View → Object Mode → N → VTA → Retargeting**, with armature-only
native eyedroppers, target profile selector, resolved type/count and read-only
mapping. Detected-only control entries describe their refusal, not a bake claim.
No generic/custom profile editor, IK UI or new private/runtime/schema API.

## Existing BlendCap audit

`retarget_maps/blendcap_standard` contains BlendCap→Rigify new/old and Mixamo→Rigify,
BlendCap/Mixamo→ARP, and BlendCap/Mixamo→CloudRig presets. No Unreal preset existed.
The Rigify new/old distinction is mainly facial; body rows use head/neck,
shoulder, limb FK and spine_fk controls. ARP uses c_* FK controls. CloudRig uses
FK-*, HIP-Spine and TORSO-Spine controls. Fingers/facial rows in those presets
are not imported into VTA body V1.

All three control presets assume generated mechanism/deform layers, not a plain
FK skeleton. Rigify and ARP legacy setup writes FK switches to 1, set-only and
not keyed. CloudRig's `ik_*` switches use the inverse convention (0 = FK);
legacy preset enables automatic IK conversion and disables world location.
Legacy posed-height scaling, split Root/Hips channels and set-only switch edits
do not meet the approved VTA alignment, old-Action and rollback contract.

No stock preset or legacy Apply Retargeting route was enabled as a shortcut.
Those maps/operators remain preserved for reference. The existing engine can
simulate many constraints, but explicitly skips unsupported solver types/spaces;
that is not permission to certify an arbitrary generated control rig.

## Real Rigify evidence and bounded adapter

The bundled Blender 5.2 stock human Rigify generator was run in factory startup
with only in-memory preferences enabled (no user preference save, no owner QA).
It produced **706 bones, 114 object drivers and 84 armature-data B-bone drivers**.
Real inventory is recorded in the ignored
`checkpoints/dev-preview/vta_profiles_20261021` audit directory. Generation is
fixture preparation only: production uses already-generated targets, no Rigify API.

- **DEF:** spine bones have live Copy Transforms + Stretch To. Direct DEF keys
  would be overridden; disabling constraints/drivers is not a safe invisible map.
- **ORG:** generated intermediate layers are not the intended animator controls
  and retain mechanism dependencies.
- **Chosen FK controls:** limb controls and spine_fk bones have
  generated MCH parents, AVERAGE/ALIGNED inheritance and FK/IK property drivers.
  Torso, hips and anatomical DEF pelvis are different rest points/layers.
- The adapter copies the generated object **and armature data**, retaining all
  drivers and constraints. Self IDs on copied data drivers are explicitly rebound
  to staging. The isolation test finds no references to the original target.
  Plain Mixamo/UE4 safety rules remain unchanged; the exception is Rigify-specific.

The approved adapter is `blendcap/vta_rigify_fk.py`, not a parallel engine. It
adds automatic property setup, anatomical observations, torso translation and
deform validation around the existing calibrated FK evaluator/bulk writer.
Mapping: spine_fk through spine_fk.003; neck/head; shoulder, upper_arm_fk,
forearm_fk, hand_fk, thigh_fk, shin_fk, foot_fk, toe_fk bilaterally. Shoulder
Euler order is preserved (stock YXZ); quaternion controls remain quaternion.
Source toes are optional; this bounded stock generated target graph includes toes.

Four limb IK_FK values become 1 in the **new** Action, including frame 0. Seven
limb-follow/torso parent/head-follow values retain their captured originals and
are explicitly keyed to avoid sampling remnants. All eleven original settings
are recorded on the new Action; cancellation/failure restores the pre-operation
state. Successful output intentionally stays FK-editable. Old Actions are retained
unchanged; selecting an old Action that lacks switch keys does not automatically
restore unkeyed properties. No Action-switch observer is introduced.

Keys are restricted to 26 normal body controls plus those properties and object
alignment. DEF/ORG/MCH bones are not keyed; unrelated fingers/face/tweaks and
properties are preserved. Stock constraints, all 198 drivers, custom shapes,
rest data and regeneration metadata remain intact. Customized body topology,
external driver/constraint dependencies, driven FK controls, data Actions and
ambiguous structures are refused, rather than solved generically. The topology
signature is a stock **5.2** layout gate, not a universal Rigify version claim;
it excludes proportions/rest roll and accepts consistent namespaces.

Real-rig checks cover a known 20-frame FK motion and 163 frames of existing
wrestling motion, weighted geometry, save/reopen/re-bake, editable FK keys,
property rollback (including injected publication failure), graph mutation refusal,
source Object Mode corrections, original placement and non-compounding scale.
Known-motion DEF comparison: max position error <0.000002 m; orientation <0.70°,
dominated by the normal middle-spine B-bone tangent. Wrestling: pelvis <0.000003 m,
FK orientation <0.0001°, anatomical DEF/ORG head attachment <0.000003 local units.
No exact unsplit-bone roll equality is claimed: Rigify distributes B-bone roll.
The full evaluated graph also checks finite geometry and body direction/attachment.
Frame 0 is imported object + neutral owned body rest, not a reset of every
generated finger/face/mechanism control.

Owner steering: Rigify is one ecosystem, not the canonical VTA rig or a template
for future architecture. Standard body FK is the bounded supported path; no custom
feature sets, tweak solving, IK parity, widget/control-layer UI or generic
control framework. A normal user should see only target/profile/mapping/action
and limitations. The adapter hooks may be reusable for other generated targets,
but do not establish ARP/CloudRig compatibility or justify a generic framework.

ARP and CloudRig live assets were not available in the scoped QA assets. Their
presets and switch behavior were audited; their generated graphs were **not**
assumed identical to Rigify. No commercial add-on/API/code/model was installed,
downloaded or bundled. They need their own isolated control-adapter proofs.

## Unreal/Epic scope and verification

UE4-style three-spine/one-neck bone naming is supported, with strict expected
parents and an extra neutral root. UE5 Manny/Quinn (`spine_04/05`, `neck_02`),
Control Rig, arbitrary humanoid variants, IK, twist distribution and finger solve
are explicitly outside this profile. No Epic asset or engine is bundled.

The unchanged normalized final-motion DTO, rest calibration, public BlendCap fast
FK evaluator/bulk writer and staged publication are used. Frame 0 is imported
reference rest; animated frames are uniformly source-sized with source world
pelvis positions. Root stays neutral, repeat bake uses the imported reference,
and source Object Mode corrections transfer without doubled translation.
Similar proportions are assumed; exact contacts are manual cleanup.

62 focused tests pass, including existing Mixamo regressions and 13 real Rigify
tests; full suites: **657 public + 425 private**, zero skips/failures/errors,
zero MediaPipe imports.
Epic tests cover namespace/detection, missing bones/hierarchy refusal, optional
toes, A/T calibration, rolls/rotated centimetre scale, Object Mode corrections,
frame-zero alignment, no/prior/outside-range Action state, cancellation and
injected partial-write failure. The synthetic skinned proof completes a 20-frame
bake, saves, freshly reopens and re-bakes without compound scale; max sampled
pelvis error is <0.00000027 m. This is convention/hierarchy proof, **not real Epic
asset visual acceptance**.

Current Rigify, repeat Epic and frozen Mixamo comparison reports are under
`checkpoints/dev-preview/vta_rigify_fk_20261021`. The earlier Epic proof and
`real-rigify-audit.blend` / `real_rigify_inventory.json` remain preserved under
`checkpoints/dev-preview/vta_profiles_20261021`. No positive ARP/CloudRig bake fixture is claimed.
**These are stress-test/diagnostic fixtures and are not suitable for normal visual QA.**

## Owner QA and release boundary

The latest owner `C:\Users\dsguf\Desktop\QA.blend` is the source for the prepared
handoff at that same stable path. Only a local stock Rigify target, simple skinned
QA mannequin/widgets and generic retarget picker choices are added; **no result
is pre-baked**, no owner assignments or prior Actions are regenerated. Existing
owner evaluated samples match exactly after staged save/reopen. Companion
authority/cache/media/output bytes are preserved; only the .blend ownership
receipt is renewed at publication. The standing last-known-good QA backup remains
unchanged until owner acceptance. Use normal Object Mode → N → VTA → Retargeting,
target **VTA Rigify QA**, source **yellow**, frames **1–163**. Inspect frame 0
after baking for the imported neutral reference. Manual visual acceptance is pending.

All code/profile changes are public/GPL. Private repository unchanged. No new
licensing conclusion, proprietary dependency, facial work, main promotion, tag
or release. Mixamo acceptance does not imply acceptance of any new target family.

## Primary technical references

- Local bundled Rigify source and generated graph are the tested version.
- [Rigify generated rig features](https://docs.staging.blender.org/manual/en/latest/addons/rigify/rig_features.html)
  and [rig component layers](https://developer.blender.org/docs/features/animation/rigify/rig_class/).
- [ARP export documentation](https://lucky3d.fr/auto-rig-pro/doc/ge_export_doc.html)
  distinguishes controller/root/export skeleton responsibilities; it is not a
  redistribution or runtime-license clearance.
- [Epic skeleton documentation](https://dev.epicgames.com/documentation/unreal-engine/skeletons-in-unreal-engine)
  describes skeleton/mesh animation relationships; exact supported names and
  parents above are the narrow VTA contract, not universal Epic coverage.
