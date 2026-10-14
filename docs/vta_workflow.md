# VTA public host: workflow and technical guide

This guide describes the current Video-to-Animation (VTA) integration in the
BlendCap-derived Blender host. VTA supports independent reviewed Characters
from a source video; it does not guarantee correct automatic identity or pose
through contact/occlusion. Final Core 1.0 functional QA is pending, not a feature-freeze
or public/commercial-release claim. Public 1.0 additionally requires commercial,
licensing, security, packaging, installation/update, first-run and support readiness.

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

Set Animation Start/End, optionally use the current-frame setters, then choose
**Generate Animation Range** below them. This is the only normal generation
command. The displayed source bounds are separate from the selected range,
reviewed coverage and generated coverage. Set the range to the displayed source
bounds to generate the full source; scene playback/preview bounds do not define it.
Characters produce independent rigs. Bounded regeneration preserves generated
motion outside the request, and both generation paths preserve supported user
Object Mode transforms, keys, interpolation and handles. Selecting a Character
in Preview or the sidebar also activates its existing VTA Dope Sheet row without
moving the playhead, changing filters or modifying project state. VTA owns generated
skeletal/root-motion and availability channels, not arbitrary animator edits.

The **Characters** scope is explicit: **All Reviewed** (default) or **Selected
Rows** in the VTA Dope Sheet. Compatible selected-only updates preserve other
Characters' Actions/transforms/availability. Shared calibration is still source-wide:
an incompatible old result requires consent to update all existing Characters before
the selected request; selected scope cannot bypass that gate.

ProjectState **0.8** uses Character/source-bound **Freeze** and **Interpolate**
repair ranges. Rejection is independent of identity/tracking and is never erased
by regeneration. Raw reconstruction is retained separately from repaired output:
Freeze uses the latest eligible raw pose; Interpolate uses eligible raw endpoints,
shortest-path quaternion interpolation and linear root translation. Missing
required anchors fail explicitly; repaired output is not reused as raw evidence.
Every frame inside an Interpolate range is rejected/replaced; good unrepaired
anchor poses must be outside it. An identity assignment alone is not pose approval.

Evidence-only Interpolate preflight runs before launching reconstruction and names
the Character and inclusive repair range when either eligible unrepaired anchor is
missing. Actual raw-pose success remains a final safety gate: eligible evidence is
not a guarantee that inference produces a valid pose. Select a repair strip, edit
Start/End, then **Apply Start / End** to resize it in place. Overlap with another
repair fails rather than silently merging. **Freeze / Interpolate** create a repair
when none is selected; otherwise they convert only the selected exact repair.
Selected text shows the stored type/endpoints; the current type is disabled.
Hover timeline keys, rows, repair strips and derived marks for short explanations.
Derived marks are tracking results, not generated-pose keys.

Camera-space reconstruction is registered through the calibrated source camera.
An owned identity `VTA_SceneRoot` organizes camera and rigs only—it is not a motion
or calibration transform. Unsupported parents, edited roots, drivers/constraints
or output/channel changes fail safely rather than rewriting user work. Output
adoption/import is transactional.

**Camera View** or **VTA Commands → Snap to Source Camera** restores the calibrated
3D view after orbiting. It changes presentation, not camera/rig transforms.
Before first generation, the implementation attempts to display UI-scaled guidance
centred in the usable viewport, clear of the toolbar/sidebar, without creating
fake objects. Its guards hide it when generated rigs are present or the Scene/workspace
is unrelated; live visibility is not yet accepted (see below). First successful
output snaps the relevant VTA workspace to Camera View;
later updates preserve the user's orbit. A context change/Local View skips this
convenience; explicit Camera View remains available.

Checkpoint QA (2026-10-14): blank-state guidance is **still not visibly working**
in live QA despite headless raster checks. This is non-blocking UI debt, deferred
to the next/Public 1.0 UI pass; it is not an accepted visible feature yet.

**Move Along Camera Depth** in the 3D VTA sidebar translates one selected generated
rig object along the source camera's forward/back axis, independent of viewport
orientation. It does not alter bones, source camera, reconstruction or animation
curves. Drag along the projected screen axis; perpendicular motion is ignored;
Shift is fine control; Enter/click confirms; Esc cancels. Orbit slightly if depth
is nearly head-on. Ordinary Blender undo is supported. Keyframe Object Mode location
explicitly if you want this adjustment animated; this tool does not propagate or
insert correction keys. Locked/driven/NLA/unsupported-parent rigs fail safely.
Camera-depth functionality passed manual QA. N-panel invocation obscures useful
viewport area; placement/interaction refinement is deferred to Public 1.0 UX work.
Deleting the source camera preserves finished animation and ordinary Blender
exportability; regeneration can fail when its registration metadata is missing.

For static-camera **FIRST** reconstruction, full-image source-index-0 intrinsics
are derived once and reused across Characters, frames, bounded runs and repair
anchors. Calibration/cache and output fingerprints prevent silently mixing
incompatible policies. Before pose reconstruction, an incompatible existing result
offers **Update Animation and Continue / Cancel**. Migration covers the smallest
contiguous interval containing all old generated poses and visible output for all
Characters sharing that source, excluding user Object Mode keys and hidden guards.
The worker reconstructs migration first, then the requested range with the same
calibration. Both publish in one transaction; failure or cancellation retains the
old animation. If compatibility is unchanged, outside motion is preserved normally.
New/compatible generation has no decision-file dependency. Only incompatible
existing output waits for explicit consent. Replies and cancellation are keyed to
one generation ID; late old dialogs cannot affect a new request. The host cleans
only its stop/decision markers after worker exit, never an active writer's temp.
Publication is Windows long-path-safe and duplicate identical consent is idempotent.
Intentional cancellation is a normal result, not a worker crash.
No whole-source prerequisite exists for a new project's first bounded generation.
Expected failures appear near the operation that failed, without an interrupting
error popup: generation by Generate Animation Range, tracking by Update Tracking,
and repair edits by Repair Animation. The primary message states the failure and
whether prior work was retained. Expand **Details** for Character/range, validation
rule, recovery and whether reconstruction started; raw logs are not dumped there.
**Go to Log** opens the containing folder and selects the relevant file in Windows
Explorer, without changing the clipboard. Preflight failures can create a small
diagnostic log; if no log exists, the button is disabled and Details says so. Invalid FIRST
calibration fails without per-frame fallback. **EVERY** inference is unchanged.
This does not solve general zoom/moving-camera calibration or physical depth truth.

## Core polish checkpoint disposition — 2026-10-14

Repair preflight PASS; suppression PASS / previously verified; camera-depth
functionality PASS. Compact error / Details / Go to Log presentation is
provisionally accepted, with final live verification required before Public 1.0
completion. Blank 3D guidance and Camera Depth UI placement remain deferred as
described above. Prior accepted Interpolate/SceneRoot/Camera View QA stays valid.

Core 1.0 is moving toward a **soft freeze**, not a hard feature freeze. Architecture
and core workflows are presumed stable; QA, bug fixes, documentation, release
readiness and UI/UX refinement continue normally. Narrow features remain allowed
when real use exposes a concrete need. Speculative features, broad redesigns and
new technical subsystems are deferred by default. This is an accepted checkpoint,
not Public 1.0, a release tag or a release publication.

## Supported limits

Constant-frame-rate, canonical 1:1 source timing is supported. VFR/custom retiming,
in-place source replacement, generalized Recovery/reanalysis, contact/foot-lock
solving and automatic perfect overlap reconstruction are not VTA V1 promises.
Monocular depth/pose errors can remain even with correct reviewed identity.
Use human assignments and explicit repairs where evidence is insufficient.

Suppress Candidate excludes that observation before automatic geometry ranking,
including its runner-up margin. Removing a duplicate can therefore allow confident
continuation to extend farther after Update Tracking. It is negative evidence for
all Characters, not an assignment to another Character. Raw detector evidence stays
unchanged and Restore remains available; no tracking thresholds are relaxed.

Do not confuse inherited BlendCap capture/cleanup features with VTA guarantees.
Respect source-footage rights and model/component licenses.

## Development and validation

Windows path length is a first-class constraint. Keep generated trees and temp/IPC
leaves short, avoid repeating parent context, and use compact collision-checked IDs
where full hashes are unnecessary. Generation run leaves use a 16-hex ID with
exclusive directory creation; IPC retains full request identity and short temps.
Never assume Windows long-path opt-in. Test worst-case absolute paths in embedded
Blender as well as the external worker. IPC uses extended paths; third-party
model/export readers may not. Generation preflights their worst-case path budget
and asks for a shorter project folder before model launch if it reaches 260 chars.
Do not rename/refactor working paths merely for style or weaken request ownership.

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

