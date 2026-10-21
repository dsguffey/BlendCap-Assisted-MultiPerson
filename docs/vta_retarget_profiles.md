# Retarget profile expansion — 2026-10-21

This is a **partial implementation and explicit control-rig decision point**,
not completion/acceptance of all four requested families. Mixamo FK, its Object
Mode UI and source-aligned/frame-zero behavior have passed owner QA. Unreal/Epic
UE4 FK is automated/synthetic-proof validated only. Rigify/ARP/CloudRig are
diagnosed and refused clearly, **not implemented or full-bake validated**.

## Status and chosen target semantics

| Target | Current supported layer/status | Detection / coverage | Root policy |
|---|---|---|---|
| Mixamo | Ordinary local FK skeleton; owner accepted | Unambiguous Hips namespace; 20 required body bones, 2 optional toes | Hips has no parent; composite pelvis once |
| Unreal/Epic UE4 | Local quaternion FK/deform skeleton; automated/proof only | pelvis, spine_01–03, neck_01, head; bilateral clavicle/upperarm/lowerarm/hand/thigh/calf/foot; ball toes optional | Required unparented root; pelvis child of root; root stays neutral, composite motion goes to pelvis once |
| Rigify | Live generated FK control adapter **pending** | torso + upper_arm_fk.L + thigh_fk.L or generated rig_id; no fallback to DEF-name matching | Must distinguish torso translation, hips rotation and anatomical DEF pelvis; legacy XY/Z split not adopted |
| Auto-Rig Pro | Live generated FK control adapter **pending** | c_root_master.x + c_arm_fk.l + c_thigh_fk.l signature; known preset variant only | Legacy c_pos XY / c_root_master.x Z split not adopted |
| CloudRig | Live generated FK control adapter **pending** | TORSO-Spine + FK-UpperArm.L + FK-Thigh.L or cloudrig marker; known preset variant only | Legacy root XY / TORSO-Spine Z split and automatic IK conversion not adopted |

Supported FK profiles resolve bone namespaces consistently (empty or a complete
prefix ending in `:`, `_`, `.`, `-`, `|`; historical Mixamo `mixamorig` also works).
Required bones/hierarchy/inheritance are checked; no fuzzy matching, silent
partial bake or fallback. Multiple families require explicit profile selection;
multiple namespaces still require separate target skeletons. Control-family
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

## Real Rigify evidence / why this is not just another map

The bundled Blender 5.2 stock human Rigify generator was run in factory startup
with only in-memory preferences enabled (no user preference save, no owner QA).
It produced **706 bones and 114 object drivers**. Real inventory is recorded in
the ignored `checkpoints/dev-preview/vta_profiles_20261021` audit directory.

- **DEF:** spine bones have live Copy Transforms + Stretch To. Direct DEF keys
  would be overridden; disabling constraints/drivers is not a safe invisible map.
- **ORG:** generated intermediate layers are not the intended animator controls
  and retain mechanism dependencies.
- **FK controls (recommended next):** limb controls and spine_fk bones have
  generated MCH parents, AVERAGE/ALIGNED inheritance and FK/IK property drivers.
  Torso, hips and anatomical DEF pelvis are different rest points/layers.
- Native object copy correctly remaps self-referencing constraints/drivers. The
  staging-isolation probe found **zero references back to the original object**.
  However the current adapter's `animation_data_clear()` removes **all 114
  drivers**. The plain-FK guard correctly refuses this rig today.

The next bounded control-adapter proposal should preserve the generated graph on
isolated staging, own only explicitly enumerated FK/follow property curves in
the new Action, leave prior Actions/properties unchanged on failure, use
anatomical observation points distinct from control origins, and validate the
evaluated DEF/skinned output after fresh reopen. Do not globally weaken the
plain-FK guard, permanently mute the rig or use synthetic control names as proof.
This does not require private/GPL boundary changes or a replacement FK engine,
but it changes the target transaction/calibration contract beyond this map-only
extension. Owner direction is required before that adapter work continues.

Owner steering: Rigify is one ecosystem, not the canonical VTA rig or a template
for future architecture. Standard body FK is the next candidate; no custom
feature sets, tweak solving, IK parity, widget/control-layer UI or generic
control framework. A normal user should see only target/profile/mapping/action
and limitations. If the isolated standard-FK adapter cannot remain small, stop
again rather than expanding target-specific machinery automatically.

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

49 focused tests pass, including existing Mixamo regressions; full suites:
**644 public + 425 private**, zero skips/failures/errors, zero MediaPipe imports.
Epic tests cover namespace/detection, missing bones/hierarchy refusal, optional
toes, A/T calibration, rolls/rotated centimetre scale, Object Mode corrections,
frame-zero alignment, no/prior/outside-range Action state, cancellation and
injected partial-write failure. The synthetic skinned proof completes a 20-frame
bake, saves, freshly reopens and re-bakes without compound scale; max sampled
pelvis error is <0.00000027 m. This is convention/hierarchy proof, **not real Epic
asset visual acceptance**.

Proofs: `epic-ue4-proof.blend`, `epic-proof.json`, `epic-fresh-reload.json` under
`checkpoints/dev-preview/vta_profiles_20261021`. Rigify audit: `real-rigify-audit.blend`
and `real_rigify_inventory.json`. No positive ARP/CloudRig bake fixture is claimed.
**These are stress-test/diagnostic fixtures and are not suitable for normal visual QA.**

## Owner QA and release boundary

`C:\Users\dsguf\Desktop\QA.blend` and `QA_vta` are not changed in this pass.
No unready Rigify target or pre-baked proof is inserted into the owner file.
Standing last-known-good backup remains unchanged because Rigify QA preparation
is on hold. There are **no Rigify manual steps yet**; the requested live Rigify
handoff must follow safe control-adapter implementation and validation.

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
