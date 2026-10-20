# VTA public host: workflow and technical guide

This guide describes the current Video-to-Animation (VTA) integration in the
BlendCap-derived Blender host. VTA supports independent reviewed Characters
from a source video; it does not guarantee correct automatic identity or pose
through contact/occlusion. Final Core 1.0 functional QA is pending, not a feature-freeze
or public/commercial-release claim. Public 1.0 additionally requires commercial,
licensing, security, packaging, installation/update, first-run and support readiness.

## Public component and runtime boundary

Current VTA does not include facial capture/animation. MediaPipe is not an active
runtime dependency. Legacy Face capture, its worker and ARKit application UI are
not exposed. Shared existing-data/body export helpers remain for compatibility.

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

Character membership is Scene-local Blender metadata referencing canonical
Character IDs, not duplicated Characters or a second selection/assignment model.
New Scenes do not inherit the project-wide Character list. Clear/rechoose retains
that Scene's membership for its prior sources; another new Scene does not acquire
it. Legacy Scenes are initialized from source-specific review/repair evidence and
owned rigs. Unreferenced legacy identities in multi-source projects are not
guessed into a Scene. This does not create independent copies of canonical
decisions when two Scenes deliberately reference the same source/Character IDs.

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

Select one or more VTA Characters, set Animation Start/End, optionally use the current-frame setters, then choose
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
Object Mode armature selection also updates the active Character and candidate
highlight in both Preview modes and the VTA timeline. Selected rows preserve the
selected rig set and distinguish its active member; unrelated objects and other
Scenes are not selected or deselected. Animation Cleanup is a collapsible child
of Animation; generation/error feedback remains outside that subsection.

Generation always uses the selected Character set, not merely the active rig.
With no selected Characters it is disabled; the old All Reviewed / Selected Rows
Character-scope dropdown is removed. Start/End remains the frame-range control.
Unselected Characters' Actions/transforms/availability remain untouched. Shared
calibration is still source-wide: when an incompatible old result requires a
camera-calibration update, explicitly select all existing Characters for that
source before accepting migration. Generation never silently expands selection.

ProjectState **0.9** retains the Character/source-bound **Freeze** and **Interpolate**
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
Camera-depth functionality passed manual QA. Invocation now closes only that
viewport's N-panel, and cancel/confirm leave it closed. Generated armature Object
origins remain camera-centered beneath the identity SceneRoot. A depth adjustment
can move that origin behind the camera while the animated body remains in front.
No automatic origin rebasing is performed: that would require validating rest data,
generated/user curves, regeneration and restoration together. A deliberate Blender
3D Cursor pivot near the pelvis is a manual interaction option, not an origin change.
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
Generation workers stamp the existing operation lock with process/request ownership.
Idle polls never delete locks. Explicit commands recover provably dead owners;
Windows also recognizes unheld empty legacy locks using exclusive file handles.
Live, unknown, inaccessible or linked locks stay protected. A crashed worker must
not permanently disable generation after reopen. Abrupt failures include the exit
code and unbuffered/native crash diagnostics, not just the final incidental warning.
The vendor `drop_path` ignored-keyword warning is not itself a generation exception.
Contextual **Go to Log** opens/selects the actual error log. **Advanced → Open
Logs Folder** is available without an error and opens the existing project
companion, the common base of its `analysis` and `reconstruction` logs. It does
not introduce another logging directory; an unsaved/unconfigured project explains
that no VTA logs folder exists instead of opening Documents.
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

### Native Blender Save As

Renaming a VTA `.blend`, or saving it to another folder, now creates an independent
`<new-name>_vta` companion before Blender writes the new file. Ordinary Save is
unchanged. Native Save Copy writes independent destination bindings but restores
the original live session's paths afterward. No replacement Save As UI or deferred
re-save is used, and no background Scene selection is introduced.

The copy retains exact canonical ProjectState bytes, referenced candidate-analysis
revisions (observation IDs cannot safely be regenerated), live reconstruction
reports and their BVH/NPZ dependencies, referenced fixed calibration, and source
movies used by bound VTA Scenes. It rebases movie/background paths, generated-output
paths and source-camera scene-reference paths. Blender itself preserves Scene data,
Characters, animation, cleanup solves, finalized state and recovery Actions.
Historical commands inside immutable reports remain provenance, not live bindings.
Logs, locks/stop/decision/temp markers, unreferenced reconstruction runs, diagnostic
trees, models and research are not copied. Detached authority history is retained
even when only blank owned Scenes remain; unbound historical media without a live
source association is not rediscovered from arbitrary folders.

Existing destination companions are **never merged**. Native Save As may replace
an entire previous VTA copy only when a version-2 ownership receipt verifies the
exact destination `.blend` hash, canonical path and companion project identity.
The previous directory and `.blend` remain recoverable until the native save
finishes; failed writes/rebinding restore the previous pair. Ordinary Save renews
a previously verified receipt after legitimate edits. Unrelated directories,
linked contents, changed/unverifiable `.blend` files and older version-1 copies
remain protected: choose a new filename/folder. A failed fork leaves
the original project untouched. A pre-existing destination `.blend` is restored
from its pre-save snapshot. Otherwise native Blender may still save a visual-only
`.blend` with a persistent VTA error: VTA edits are blocked, with Details/Go to Log
in VTA Review. Reopen the original, or retry explicit Save As to an unused name.
Do not Save As during analysis/generation/cleanup. Windows path preflight rejects
locations current downstream readers cannot safely use. Interrupted native saves
roll back host bindings and only the transaction's unchanged copied companion;
independently changed files are retained for recovery, never scavenged.

Older already-renamed files with missing companions get specific guidance to open
the original working file and Save As again. No automatic search, authority
reconstruction, ambiguous ancestor adoption or general migration is attempted.
Recovering one of those old files without a working original is a separate task.
An OS crash cannot atomically commit a `.blend` and directory together; an orphaned
companion after interruption must be inspected or avoided with a fresh name.
Lifecycle behavior is verified on the current Blender 5.2 host.

### Rotation-only Animation Cleanup

Real-clip manual QA has confirmed roll/pan correction, visually accurate output
and Restore Source Alignment on the obvious-roll case. A separate report of
residual root/global motion on another clip remains open pending its exact saved
before/after evidence; this is not a universal motion-quality guarantee.

After finishing tracking, generation and repair, select one or more Character
rows and open **Animation → Animation Cleanup → Remove Camera Movement**.
Remove starts directly without a confirmation dialog. It is disabled when no
selected generated Character is eligible; a short reason is shown. Current
fixed-initial calibration, ordinary supported rig channels and source-aligned
output are required. File/provenance preflight still completes before a worker
can start. UI polls use observed metadata, not project reloads. Restore Source
Alignment retains its confirmation because it replaces generated Root edits.
All selected Characters use one Scene solve covering their combined generated
range. Unsupported/stale Characters are reported individually without blocking
other eligible Characters. Unselected Characters remain unchanged. Finalization,
component review and Restore Source Alignment remain independent per Character;
Restore operates on the selected rows. Confirm that finalized Characters will no
longer align with the source clip. Cleanup estimates background **pan/tilt/roll only**. It does
not solve camera translation, normalize body size, or correct depth; apparent
size/depth changes remain for manual depth correction.

The source camera and ordinary VTA workflow remain source-aligned. The solve is
stored separately per Scene, keyed to source, candidate analysis and fixed-initial
calibration. All analyzed person boxes are excluded, including suppressed/unassigned
people. Analyze coverage from the source start through the generated animation end
is required. Cleanup requires current **Sample first frame** calibration, supported
generated Root channels, and a rig not shared across Scenes. The full 3D rotation
fit accepts pan/tilt together and now rejects calibrated scale/shear mismatch.
An independent roll fit can still pass using background image similarity, with
feature coverage, inlier consensus, residual and four-region angle-agreement gates.
Image scale/shift are nuisance fit terms only: never applied to animation or used
to estimate camera translation, Character size or physical depth.

Only accepted components are corrected. On roll-only frames, pan/tilt compensation
is omitted, not held at its last value; a fully rejected frame keeps its exact
source-aligned Root animation. The full and roll models keep separate reliable
reference chains and may retry the original source reference (at most four fits
per frame in total). No rotation is guessed, held or
interpolated across the gap. A non-blocking warning lists skipped Blender/source
frames and reasons, plus roll-only frames, in Details. Changes in accepted
components and corrected/uncorrected boundaries can visibly jump;
inspect them during QA. Partial cleanup still finalizes the Character and remains
reversible. If every non-reference frame is rejected, nothing changes and the
Character is not finalized. A later frame without person analysis is skipped
without matching unmasked people or advancing either camera reference; previously
supported frames can still publish. The initial reference still requires analysis.
Missing initial-reference analysis, corrupt inputs, decoder failures,
stale data and cancellation still retain the entire original animation.
The derived version-3 Scene solve records per-frame accepted pan/tilt/roll and
rejection reasons; finalized metadata stores Blender/source review frames and
uncorrected component names. This is geometric acceptance, not a calibrated
probability. Roll is independently validated; pan and tilt are not independently
separated from each other. No ProjectState schema changes. Old solves are not
reused for new cleanup requests; existing source-alignment recovery remains usable.
Cleanup rotates both root orientation and root position around the fixed camera
reference, before user Object Mode corrections. It does not level an already
tilted first frame, freeze legitimate Character locomotion, denoise limbs or solve
camera translation. The initial frame defines relative identity, not world-up.
Flat backgrounds, translation,
moving crowds, blur, cuts and zoom can bias or invalidate rotation estimates;
this is approximate cleanup, not a physically accurate camera solve.

Only generated Root location/rotation channels are baked. Limb animation, user
Object Mode curves/placement, scale, visibility and other Characters are preserved.
Tracking, assignment, generation and repair are blocked for finalized Characters.
Use **Restore Source Alignment** in the same section to restore the saved Root
channels and resume VTA work. Restore replaces later generated Root edits, after
confirmation, but retains later Object Mode and limb edits. Recovery and the solve
survive save/reload. No duplicate rig or Scene is created. Native Blender Undo of
the asynchronous solve is not the recovery contract; explicit Restore is.

This addition does not close existing Public 1.0 debt: cross-Scene Character leakage,
Cancel Analysis partial results, blank-state guidance,
Load Scene ambiguity, or final live compact-error/Details/Go to Log verification.
Move Along Camera Depth closes only its invoking 3D View's sidebar before modal
interaction; confirm/cancel leave it closed. Other editors are unaffected.

### Other supported limits

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

### Retained tracking and pending human keys

The 2026-10-17 milestone records owner acceptance of the core tracking workflow
and working Dope Sheet hints. It is a checkpoint during the Core 1.0 soft freeze /
Public 1.0 QA period, not a Public 1.0 release or tag. Authoritative-key color and
presentation semantics still need a later UX review before 1.0. Clip-specific
camera-cleanup QA/investigation remains; facial-animation investigation is only
prospective next scope and is not included in this checkpoint.

Review edits do not rerun tracking. A new human key takes precedence on its exact
frame; otherwise successful tracking remains until explicitly contradicted,
its exact used candidate is suppressed, or **Update Tracking** replaces it.
Suppressing an unused alternative does not erase the winner or downstream work.
Raw detector evidence is unchanged.

New authoritative keys are **pending**: cyan with a short underline and an explicit
hover label. They are valid human ground truth, not bad or untrusted keys.
Incorporated keys use their ordinary state color without the underline. Only keys
actually used by a successful reconciliation become incorporated; unrelated
Characters, sources and regions remain pending. This receipt state survives reload.

Update Tracking remains selected-Character scoped. After several edits inside an
earlier tracked region containing the playhead, one command reconciles that prior
bounded region, split at the current human keys. It does not union every edited
region in the Scene. With no suitable prior region, the existing nearest-key /
about-five-seconds fallback applies. Consecutive hard keys have no mutable frames
between them. Identical supported assignments retain their existing provenance.

Review assignment, hard-state, suppression, key-management and tracking commands
participate in native Blender Undo/Redo for the current session. Only an opaque
Undo token is stored in the .blend; bounded session snapshots restore canonical
review data atomically. Evidence/media/generated output are not rewound. File
reload, external project writes and other project operations form safety barriers:
Undo never overwrites changed external data. Old evicted snapshots may be
unavailable; the operation reports this rather than fabricating a restore.

### Failure-directed identity review

After explicit **Update Tracking**, a competing-candidate ambiguity or directional
disagreement may show a session-only outlined **triangle with a question mark**
on the affected Character's VTA Dope Sheet row. This is a suggestion, not a human
key, assignment, confidence guarantee or generation input. It appears only in the
VTA Dope Sheet, not the sidebars. Hover for context; left-click to focus the
Character/frame, or right-click to dismiss that suggestion without changing keys.
Human review keys retain click priority. Suggestions are not included in key
selection, box selection or key deletion.

Review identity at the suggested frame and assign only a valid separate
observation. If no suitable observation exists, use **No Valid Candidate** rather
than sharing one normal candidate between Characters. A new authoritative key can
help the next explicit Update Tracking, but does not guarantee continuation.
Well-placed failure keys, not consecutive key runs, are the suggested workflow.

Missing analysis, empty candidate frames, hard boundaries and technical errors do
not create assignment suggestions. Single/duplicate broad boxes alone do not
create this hint; geometry cannot certify separate people. Each update keeps at
most the first forward stop, first backward stop and first directional conflict
per Character, deduplicated by Character/frame. Dismissal lasts until another
explicit update; suggestions clear on project/cache changes, file load or add-on
disable. They remain scoped to their Scene/source and never change tracking
thresholds, candidate ownership, schema or saved project state.

Maintainer manual-QA handoffs use one persistent **QA.blend** and its **QA_vta**
companion, preserving the latest owner-authored edits as functionality evolves.
Follow the [persistent QA workflow](qa_workflow.md); internal diagnostic copies do
not supersede the current owner project or become alphabetized owner handoffs.

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

The development-branch [VTA Mixamo FK V1 guide](vta_retargeting.md) describes
final evaluated motion baking, target requirements and transactional Action
preservation. Owner visual acceptance is required before promotion.

