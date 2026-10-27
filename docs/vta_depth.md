# VTA Depth — native Object Location correction

The 3D Viewport **VTA Depth** tool is available only in **Object Mode** when the
authoritative VTA session matches the main Scene and its managed presentation.
Workspace names are not used. A blank/mismatched/non-VTA Scene cannot receive a
depth edit; use the existing explicit Load VTA Scene command to recover a mismatch.
No timer changes the main Scene.

Select one or more generated VTA Characters, choose VTA Depth in the viewport toolbar,
then drag the cyan arrow or centre ring. Release confirms, Shift gives fine
control, Esc/right-click cancels. In a head-on/source-camera view, drag the ring
vertically. Selecting the tool or clicking without a drag does not move/key.
Normal object selection remains available while the tool is selected. The handle
follows the existing synchronized Character/rig selection; deselection hides it.
With several eligible Characters selected, the handle sits at the average of
their visible pelvis handle points. Their generated Object origins can sit at
the source camera, so averaging those origins would hide the handle. All
participants receive the same world-space camera-depth delta, preserving their
relative separation. The active object does not change the group pivot.
VTA Depth uses a small camera ↔ Character toolbar icon. Tool/session entry enables
the invoking window's VTA viewport gizmo/tool visibility once, including saved
viewports that had gizmos disabled. This is local visibility initialization,
not continuous enforcement or a Scene switch. If you subsequently hide gizmos,
the header exposes Show Gizmos / Tool Gizmo, or switch tools and re-enter Depth.
Dragging elsewhere in the viewport is consumed without a transform/key; ordinary
object clicking still selects normally. Only the visible depth handle starts the
constrained modal drag. Explicit native transform shortcuts remain native.

## Exact transform and keying contract

The correction belongs to the generated armature's **Object `location[0:3]`**.
It is **not** the pose `Root`/`Hips`, organizational SceneRoot or camera transform.
Motion is parallel to the source camera's optical forward/back axis, with the
existing parent/matrix-parent-inverse conversion. It does not change scale,
rotation, pose, reconstruction evidence, source camera or ProjectState. Camera
perspective can naturally change apparent size as depth changes.

- **Auto Key on:** release inserts/updates native Object Location keys for every
  participating Character at the
  current Blender frame. All three components are keyed because a camera axis
  can be oblique in parent-local coordinates. Blender's Replace Keys mode only
  updates a frame that already has Object Location keys. No key is added on cancel.
- **Auto Key off:** only the transform changes. In the 3D View use Blender's
  **K → Location** keying menu, or choose the built-in **Location** keying set
  and use **I**. Blender 5.2's default I behavior may otherwise key broader
  transform channels. Use Object Mode and select the intended rig, not a pose bone.
- The gizmo keys Location only, irrespective of a broader active keying set.
  Ordinary Blender manual keying remains unchanged. Default native interpolation
  applies; use native animation editing for holds, timing and curve shaping.
- Locked/constrained/driven/NLA-controlled/unsupported-parent targets are refused
  individually or skipped in a multi-selection; unrelated objects never move.
  The tool also refuses a Character object shared across Scenes, and Auto Key
  refuses a read-only Action or a slot shared by another object. It does not
  silently clone shared animation or replace unrelated channels.

## Seeing and editing native keys

Each Character has a disclosure triangle and one indented **Depth** child row,
expanded by default. Click the triangle to collapse/expand only that Character.
Collapse is session-only Scene/source/editor presentation state: it survives
ordinary scrubbing and Character selection, changes no animation/review data,
and resets on file load or add-on disable. No X/Y/Z channel browser is added.

A diamond on **Character → Depth** means **that Character's
native Object Location has a key on this Blender frame, from any author**.
Multiple X/Y/Z components at the same frame produce one diamond. Ownership uses
Scene/project/source/Character IDs and the actual rig/Action slot, never display
name alone. Each Character has its own markers even when both are keyed at the
same frame. The parent Character row remains exclusively for authority, update/
repair, hints and existing VTA annotations; depth keys are not overlaid there.
The diamond does not mean assignment, confidence, pending state or VTA authorship.
Any one of the three native Location components is enough.
Pose-root, rotation, scale and sibling Action-slot keys do not count. Keys are
read directly on drawing/hover, including fractional frames, without a parallel
metadata list or source-frame offset. Muting a curve does not delete its keys.

Click a Depth diamond to select it and scrub to its exact native Blender frame
(including subframes). Selected diamonds are gold; unselected diamonds are gray.
Shift/Ctrl-click toggles another diamond, including on another Character's Depth
row. Selection is the native Location key selection, not a VTA metadata list.
Click empty space on a Depth row to scrub. Depth-row clicks do not
select review keys, reassign identity, alter repair state or switch Characters.
Select the parent Character row when you want to choose the gizmo's target.

Press **X** or **Delete** after selecting Depth diamonds to remove their native
Location keys. A diamond is one Character-position decision: deletion removes
all XYZ keys at that frame, including any unselected components, and never a
different Character's unselected keys. Remaining native interpolation determines
the resulting position. Undo/Redo restores/reapplies the complete deletion. Native
Graph Editor edits and Action-slot changes are reflected immediately. Rotation,
scale, pose and review/identity keys are not deletion targets.

For timing/curve shaping, hover the timeline and press **Shift F6** for the native Graph
Editor. Select the Character object, expand **Object Transforms / Location**
(or filter for Location), and move/delete keys normally. **Shift F12** returns
to the Dope Sheet; choose its Dope Sheet mode if Blender shows a different mode.
The Graph Editor is not overpainted by VTA. No second editor or editor-history
restoration mechanism is added. The usual Timeline Keying menu is also native.

Native Undo/Redo restores the transform and its keys. The VTA marker is queried
from the restored Action/slot, so external move/delete and Undo/Redo are reflected
without reconstructing VTA metadata. Selection, Scene switches and mode exit
re-evaluate the target rather than retaining a separate remembered Character.

## Status and retained compatibility

The hierarchy and original one-Character gizmo were previously accepted in owner
QA. The new selectable/deletable diamonds and multi-Character drag are verified
with background/native Blender checks; their new live pointer UX needs owner QA.
The older **Move Along Camera Depth** sidebar button/operator is deliberately
retained unchanged. Its axis/parent math is reused, but its modal Enter/click
confirmation, head-on limitation and no-auto-key behavior are legacy-specific.
After acceptance, its redundant button/operator/guide drawing can be retired in
a separate cleanup; shared depth math/preflight helpers should remain.

Per-Scene Character-name uniqueness was not changed: the restriction is in the
private canonical ProjectState identity rules, not an isolated UI check. That
needs its own bounded policy/migration assessment, not a gizmo-side workaround.

## Generation camera options

The Animation generation panel retains **Sample first frame only** (one stable
source calibration) and **Sample every frame** (per-frame FOV estimation) under
the collapsed **Advanced Camera** child panel. Stable focal assumptions can reduce
depth variation; per-frame estimates support zoom but can destabilize apparent
depth/locomotion. The inherited manual-focal property is not an implemented
reviewed-generation mode and is not presented as a working override. No retired
Capture UI or exporter root-filter defaults are restored.

**Fixed Depth is the default for new generation requests.** In Advanced Camera,
leave the **Automatic Depth** checkbox off to hold generated pelvis depth along
the source-camera optical axis. Check it to retain the previous Automatic depth
calculation. Changing this setting never edits an existing animation. With
generated Characters present, a confirmation explains full regeneration and
the possible change in the visible effect of preserved user Depth keys; Cancel
keeps the old setting. Generation remains an explicit separate action.

Fixed preserves generated screen-plane translation and local skeletal pose.
It removes all generated camera-axis travel, including genuine approach/retreat;
author that intentional travel with native Depth keys. It does not fix hand or
other local-pose jitter. Each Character uses its earliest available published
pose as the initial reference, saved with source/Character identity and checksum.
That reference is reused for later bounded and full Fixed updates, even when an
earlier range is subsequently generated. Missing poses stay unavailable.

Depth policy is applied after camera registration/pose repairs and before native
Object offsets. It does not change raw provider evidence, camera motion, FOV
calibration identity or ProjectState. Mode/reference provenance is stored on the
generated rig. Unmarked older output retains Automatic semantics. Switching an
existing Character between Fixed and Automatic requires **whole-source**
generation for the selected Characters; a bounded request refuses safely before
inference rather than mixing policies outside its range. Other Characters remain
untouched. Old animation is not retroactively converted merely by installing an
update or opening a project.

Existing Object Location keys are additive corrections; preserving their exact
values over a changed generated baseline does not necessarily preserve old
visible positions or pair separation. No silent rebasing or Restore Generated
Depth command is added. Native keys and existing transactional safety guards
remain intact during regeneration.
