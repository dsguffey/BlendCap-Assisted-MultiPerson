# Animation editing compatibility — 2026-11-10

Validated for local deployment and owner manual review. No private-core change,
push, release, installer or stabilization work. Deployment/rollback receipts and
owner hashes are in ignored `checkpoints/dev-preview/vta_animation_editing_20261110`.

## Native pose preservation

Camera rotation cleanup now modifies only existing, synchronized integer-frame
Root location/rotation anchors. It does not recreate deleted anchors, densify the
Action, change limb keys, rewrite Object Mode/Depth curves or change interpolation
types. Free/aligned handle ordinates move with their anchor; native automatic
handles remain automatic. The protected Restore Source Alignment mechanism is
unchanged: saved Root channels restore exactly, including missing keys and handle
settings, while later limb/Object Mode edits remain.

Limitations are explicit: time-varying camera compensation between sparse anchors
is approximate. Changing neighboring anchors also changes native interpolation
through an unkeyed or skipped frame. Existing skipped anchors retain their values;
there is no claim that an unkeyed skipped frame retains its evaluated motion.
Root channels with different key-frame sets are refused with an alignment message,
rather than silently inserting keys. Regeneration also refuses subframe pose keys
rather than implicitly rounding them.

## Regeneration choices

Inspection is per selected generated Character, its requested Blender-frame range,
and its native pose-bone transform channels. **Any owned pose channel keyed at a
frame counts as an existing pose for that Character**, whether mocap-generated or
manually edited. Partially keyed poses are therefore protected as a whole. Depth,
Object Mode channels, visibility guards, source cameras, retarget Actions and other
objects do not count as pose-key presence. No attempt is made to classify a key's
authorship. Different Characters may have different keyed frames.

Entirely unkeyed/fully keyed requests retain ordinary Generate behavior. Mixed
requests, including a fully keyed Character selected with an unkeyed one, present:

- **Generate animation in frames with no keys** — leave all existing pose keys,
  manual values and handle settings intact.
- **Replace animation in frames with keys** — replace complete generated poses at
  those frames, preserving entirely unkeyed frames.
- **Replace all animation, generate new animation** — replace generated poses and
  gaps within the requested scope; Object Mode/Depth remains outside that scope.
- **Cancel** — the default. Enter without choosing a generation option starts no
  job. The ordinary dialog Cancel button also does nothing.

The radio-choice dialog explains scope once. Native output application selects
whole pose frames; it never replaces arbitrary individual bone channels according
to their separate missing/keyed status. Interpolation between changed anchors can
change; preserved anchors and gaps are not an exact evaluated curve-segment lock.

The existing one-shot engine, authoritative plan/result acceptance and native
transaction/protected-output restore remain. The public host persists its native
application choice, Character frames and pose signatures in the existing local
job pointer. Explicit recovery retains the choice and refuses later pose edits.
The bounded local pointer reader supports up to 2 MiB; IPC limits/protocol are
unchanged. Ordinary redraw never scans Actions. Shared calibration migration
cannot use a selective preservation choice; it requires explicit full replacement,
rather than silently expanding scope. NLA, drivers, modifiers, constraints,
unsupported channels and skeleton/calibration safeguards remain in force.

## MoCap timeline

A dedicated 28-logical-pixel bottom strip mirrors **Scene timeline markers** using
the existing View2D frame transform. There is no additional marker authority or
domain query. Names and selected triangles are distinct from review/depth keys.
The strip reserves row space; a very short editor may need enlarging to show
Character/Depth rows alongside markers.

- Click / Shift-click in the strip selects/toggles native markers.
- M adds a native marker; F2 renames and G moves selected markers when marker
  focus is active. Delete/X dispatches to native marker deletion in that context.
  Right-click exposes Blender's marker menu.
- In the review canvas, B starts the existing marquee with a crosshair; Shift at
  drag start adds. Escape/right-click cancels without changing selection.
- A selects review keys in WINDOW or Characters in CHANNELS; Alt-A deselects.
  Shift-click retains the existing toggle behavior; empty click deselects.
- Delete/X targets selected review keys through their existing confirmation, or
  native Depth keys when Depth focus is active. It never falls through to hidden
  pose-key deletion. Marker, review and Depth focus remain distinct.
- Preview J Repeat Last Assignment Mark is unchanged. Delegated dialogs/moves
  finish the shortcut dispatcher; only box selection owns its modal lifecycle.

## Validation and handoff

Full public validation passed **936 tests**, zero failures/errors/skips. It was
necessary because durable host-job pointer reading is shared with analysis and
recovery. After the final shortcut handoff/native marker Undo adjustment,
**160 focused tests** passed, zero failures/errors/skips. Counts overlap.
Fresh source and installed registration checks, exact choice labels/Cancel default,
native marker Add/Rename/Move/Delete, marker rendering, native Undo/Redo and
Save/Reopen are covered by the adjacent smoke evidence. Background tests do not
replace owner foreground popup/visual acceptance. No fresh private-suite result
is claimed for this public-only change.

Manual QA remains **C:\Users\dsguf\Desktop\QA.blend**, based on that exact current
owner file and `QA_vta`. Preparation changes: **None**. Special owner modifications:
**None**. All **423 files** (blend plus 422 companion files) remain hash-identical.
Tests use synthetic/temp projects; ignored marker smoke blends are diagnostics,
not replacement owner handoffs. No owner keys or assignments need recreation.

Earlier Scene-context fixes/tests and the three prior visibility R&D reports were
already dirty/untracked. They remain separate from this commit; their deployed
Scene-context code is retained when updating the installed review UI. Only the
inventoried public adapter files are deployed, with exact prior files and metadata
saved for rollback. Engine/provider/model/runtime-configuration files are unchanged.
