# VTA public host: workflow and technical guide

This guide describes the current Video-to-Animation (VTA) integration in the
BlendCap-derived Blender host. VTA supports independent reviewed Characters
from a source video; it does not guarantee correct automatic identity or pose
through contact/occlusion. Final pre-1.0 QA is pending, not a feature-freeze claim.

## Public component and runtime boundary

The public component owns Blender presentation, commands, source decoding,
workspace setup, camera/armature integration and transactional output import.
The separately configured VTA runtime owns project/review persistence, detector
evidence, bounded identity tracking, reconstruction planning and repair schedules.
Public adapters validate neutral source/evidence/animation contracts. Blender
selection and detector candidate numbers are not durable Character identities.

A compatible runtime and its licensed model assets are required for source-backed
analysis/generation. Configure the runtime through VTA preferences; unavailable or
incompatible runtimes fail clearly. Blank Scene/session controls do not require
inference or a source. The public source, interfaces and license notices remain
available; this guide does not replace any component's license or distribution
requirements. See [Licensing and Credits](14-licensing-and-credits.md).

The preview-evidence adapter uses `blendcap_vta_preview_validation.py` for sampled
sidecar and explicit anchor validation, and `blendcap_vta_boundary.py` for verified
coordinate/provenance translation. Validation resolves existing evidence only;
it cannot propagate a track or assign semantic identity.

## Scene and source workflow

1. **New VTA Scene** creates an active, genuinely blank VTA-owned Scene: no source,
   strips, camera, rigs, Characters or review/repair data.
2. Save the .blend before **Choose Clip**. Choosing validates/identifies a movie,
   creates or opens its companion project, binds the source and configures its
   strip/timing. It does not run analysis or reconstruction.
3. **Load VTA Scene** selects an existing owned Scene and configures its VTA
   editors. Multiple valid Scenes expose a picker; a single Scene loads directly.
4. **Enter VTA Mode** restores the last successfully loaded/created VTA Scene once.
   **Exit** stops VTA review presentation and resets VTA-managed restrictive Dope
   Sheet filters. It does not remove ordinary scene content.
5. While a source is bound, in-place replacement is disabled. Use **X / Clear
   Source**, then Choose Clip. Clear detaches scoped VTA content, not underlying
   project decisions, caches or animation history. Unrelated content survives;
   unsafe shared dependencies fail before detachment.

Blank and source-backed VTA Scenes can coexist. Rechoosing an existing source
reopens its history, but does not automatically relink old rigs or run inference.
Scenes sharing one companion share durable review decisions; their Blender
animation objects/Actions are independent.

Only explicit Enter/Load/New commands may change the main Scene once. Background
observers never enforce a Scene, editor pin or camera. After changing editors or
the main Scene, use **Load VTA Scene** to restore presentation. A mismatch clears
stale VTA content/disables source actions, not workspace roles or command access.
No editor-history restoration, automatic mismatch recovery or broad Scene browser.

## Timing, review and assignments

Source-backed owned Scenes use constant-rate source FPS, including fractional
rates through `fps_base`. One Blender frame represents one source frame:
Blender frame 1 is source index 0. **Match FPS to Source** changes only the owned
Scene's rate, not key times or repair ranges. Unrelated Scenes are untouched.
Blender's **Use Preview Range** independently controls playback bounds.

Analyze fills candidate evidence without creating identities. Create Characters
explicitly, select a candidate and Assign it, or author No Valid Candidate,
Occluded or Not Visible. Human keys remain authoritative and candidate ownership
is one-to-one. A human assignment retires only conflicting derived ownership;
it cannot silently override another authoritative assignment.

**Repeat Last Assignment** (`J` in its scoped review context) is the inexpensive
manual fallback. It validates a current-frame candidate through the normal Assign
path and advances only after success; it does not reuse stale observation IDs.
Suppress/restore applies to exact evidence, not an inferred persistent person.

**Update Tracking** uses existing human anchors and conservative bounded matching.
Ambiguous evidence stays unresolved; a box count or confidence score is not
identity truth. Additional automatic signals have not demonstrated reliable net
correction savings on the evaluated contact footage. This is a limited product
decision, not proof that all automatic tracking methods fail.

Idle/poll/draw paths use read-only cached presentation. Scrubbing updates
correct-frame overlays without repeatedly loading the project or model.

## Generation, repair and placement

Generate reviewed animation explicitly, for the source or an inclusive range.
Characters produce independent rigs. Bounded regeneration preserves generated
motion outside the request, and both generation paths preserve supported user
Object Mode transforms, keys, interpolation and handles. VTA owns generated
skeletal/root-motion and availability channels, not arbitrary animator edits.

ProjectState **0.8** uses Character/source-bound **Freeze** and **Interpolate**
repair ranges. Rejection is independent of identity/tracking and is never erased
by regeneration. Raw reconstruction is retained separately from repaired output:
Freeze uses the latest eligible raw pose; Interpolate uses eligible raw endpoints,
shortest-path quaternion interpolation and linear root translation. Missing
required anchors fail explicitly; repaired output is not reused as raw evidence.

Camera-space reconstruction is registered through the calibrated source camera.
An owned identity `VTA_SceneRoot` organizes camera and rigs only—it is not a motion
or calibration transform. Unsupported parents, edited roots, drivers/constraints
or output/channel changes fail safely rather than rewriting user work. Output
adoption/import is transactional.

**Camera View** or **VTA Commands → Snap to Source Camera** restores the calibrated
3D view after orbiting. It changes presentation, not camera/rig transforms.
Deleting the source camera preserves finished animation and ordinary Blender
exportability; regeneration can fail when its registration metadata is missing.

For static-camera **FIRST** reconstruction, full-image source-index-0 intrinsics
are derived once and reused across Characters, frames, bounded runs and repair
anchors. Calibration/cache and output fingerprints prevent silently mixing
incompatible policies. Incompatible bounded updates fail before Blender mutation;
whole-source regeneration is required to migrate old output. Invalid FIRST
calibration fails without per-frame fallback. **EVERY** inference is unchanged.
This does not solve general zoom/moving-camera calibration or physical depth truth.

## Supported limits

Constant-frame-rate, canonical 1:1 source timing is supported. VFR/custom retiming,
in-place source replacement, generalized Recovery/reanalysis, contact/foot-lock
solving and automatic perfect overlap reconstruction are not VTA V1 promises.
Monocular depth/pose errors can remain even with correct reviewed identity.
Use human assignments and explicit repairs where evidence is insufficient.

Do not confuse inherited BlendCap capture/cleanup features with VTA guarantees.
Respect source-footage rights and model/component licenses.

## Development and validation

Use a Blender version compatible with `blender_manifest.toml` and the existing
[installation](03-installation.md), [requirements](02-system-requirements.md) and
[contribution](../CONTRIBUTING.md) guidance. Runtime assets/installers are not
supplied by the public source checkout alone.

Run the public `tests/test_blendcap_vta_*.py` suite in background Blender.
For the complete development suite, make the compatible private development
runtime discoverable; private-dependent tests may skip without it. For example,
from the public checkout:

```powershell
blender --background --factory-startup --python-expr "import pathlib,sys,unittest; sys.dont_write_bytecode=True; root=pathlib.Path.cwd(); sys.path[:0]=[str(root),str(root.parent/'Video-to-Animation')]; names=['tests.'+p.stem for p in sorted((root/'tests').glob('test_*.py'))]; result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names)); assert result.wasSuccessful()"
```

Focused validation modules are `tests.test_blendcap_vta_preview_validation` and
`tests.test_blendcap_vta_boundary`. Tests use synthetic evidence and mocked model
interfaces; no new inference is needed. Coverage includes source/cache identity,
explicit sessions, frame-correct overlays, timing, source lifecycle, repair,
camera projection, user-curve preservation, rollback and reload.

A correct fixture or passing synthetic suite is not independent 3D ground truth
or completed manual release QA. Internal experiment machinery and real QA media
are intentionally not part of this public maintenance guide.

