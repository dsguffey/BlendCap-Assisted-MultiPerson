# SceneRoot and Camera View milestone — 2026-10-12

Public implementation record reflecting the authoritative shared Project
Documentation architecture and UX guidance. Those shared originals remain
canonical; this record does not replace them or include private core source.

## Scene organization and safety

Generated source cameras and independent Character rigs share an organizational
`VTA_SceneRoot` Empty. Ownership is established by `vta_scene_root_version=1`
and project/source metadata, never by object name alone. The Empty must remain
at identity, unanimated, undriven, unconstrained and without an ancestor.
Children use direct OBJECT parenting with identity parent inverse. Existing
local transforms and user Object Mode F-curves/handles remain unchanged.

Fresh temporary rigs complete camera registration unparented. Adoption of
surviving owned objects occurs inside the all-Character output transaction.
Failure restores hierarchy, parent inverse, camera/output datablocks and viewport
presentation; a newly created container is removed. Unsupported parents,
duplicate owned containers and edited roots fail rather than resetting user work.
Legacy unparented output gains the container on explicit generation/update.

SceneRoot does not own calibration math. ProjectState 0.8, Character authority,
tracking policy, Freeze/Interpolate and bounded regeneration are unchanged.
Deleting the source camera after generation preserves finished motion and
ordinary Blender exportability. Regeneration still needs camera registration
metadata and can fail after deletion; keep a camera-present copy for further work.

## Returning to the source clip

After orbiting the VTA 3D viewport, click **Camera View** in its header, or use
**VTA Commands → Snap to Source Camera** (also available in VTA Review).
The command selects the owned camera locally, resets viewport pan/zoom and
enables armature overlays. It does not move scene objects, change the scene's
active camera, generate animation or rewrite keys. From the VTA timeline/menu,
it targets the current workspace's 3D viewports; from a 3D viewport, only that
viewport is changed. Unrelated workspace menu calls do not change views.

Exit Local View with `/` first. Missing/ambiguous source cameras produce an
actionable warning without creating a replacement or changing calibration.

## Validation and manual-QA provenance

Automated coverage includes metadata/parent validation, legacy adoption,
preservation of user curves, bounded/full regeneration, transactional rollback,
camera deletion, view restoration and command registration lifecycle. Disposable
Freeze/Interpolate replay and same/fresh-process reload comparisons use evaluated
hidden rigs to avoid sampling stale dependency-graph state. No inference was run
for this milestone publication.

Manual QA file (local, not published):
`checkpoints/dev-preview/vta_scene_root_impl_20261011/manual-qa-clean/fixture/VTA_Overlay_Wrestling.blend`

Based on the accepted
`checkpoints/dev-preview/vta_repair_ranges_20261011/manual-interpolate/fixture/VTA_Overlay_Wrestling.blend`.
Preparation added only validated identity SceneRoot parenting. Animation,
assignments, repair ranges, visibility and user edits were preserved.
Known special test modifications: **None**.

The user accepted the clean hierarchy and representative-frame appearance,
and verified that Snap to Source Camera restores the calibrated clip view.
Earlier Interpolate/pre-SceneRoot visual QA and automated results remain valid.
Only the earlier SceneRoot visual-QA attempt is discarded: its supplied
`ip/fixture` contained diagnostic replay/stress modifications and was not
suitable for normal visual QA. Checkpoints, diagnostics, logs and generated
outputs are intentionally excluded from the public milestone commit.

Publication verification: 91 focused tests passed; full public suite 491 passed
and six existing skips (497 total). Compilation of all nine scoped Python files,
installed register/unregister/re-register and whitespace checks passed.
Fresh-process Freeze and Interpolate reloads, both with and without the source
camera, reported zero differences in world/local transforms, bone bases,
relative placement, visibility and available camera projections.
