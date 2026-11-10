# MoCap canvas and marker follow-up — 2026-11-10

This supersedes the native-marker-exposure approach in the earlier UI regression
pass. Public adapter/UI changes only. No Blender launch, computer control, screen
capture, owner-project write, private-core change or shared input framework change.

## Character row failure: model freshness did not imply visible rows

The installed interactive-path files were read and compared against `b3920d5`;
they matched. The path is: the registered Create operator's deferred foreground
execute → timer/modal → cached backend `create_character` → existing mutation
bridge/Scene membership adoption → forced observation refresh → Scene-filtered
timeline/frame projection → redraw and queued presentation/property timer →
`timeline_state` → `channel_rows` → CHANNELS/WINDOW painting.

The concrete failure reproduced here is at the last step. The model and membership
can already contain the new Character, and `tag_redraw` can already have run, while
the viewport still excludes it. The custom painter slices rows using the old focus
or stable viewport anchor. Expanded Depth children consume slots too. In the
submitted screenshot's approximately 194 logical-pixel WINDOW height, the exact
prior geometry yields three slots: black shirt, its Depth child, and no shirt.
Tall ref exists in the three-Character model but is outside that painted slice.
The three expanded Characters require six slots. This reproduces the screenshot
without stale Scene ownership or a failed Character mutation.

The earlier tests mutated observations/membership and asserted the unsliced
`_timeline.characters`; they mocked queued presentation and did not paint a short
editor after the deferred foreground callback. Their passing result did not prove
the foreground visibility requirement. This pass does not claim that those tests
established live acceptance, nor dismiss the owner's foreground observation.

Create and Rename now explicitly reveal their completed management target in
matching current-Scene MoCap editors if its parent is outside the visible slice.
This adjusts only transient viewport state, never assignment or management target
selection. Already visible targets do not move the viewport. Delete uses the
existing refreshed model and viewport clamping. The existing cache invalidation,
project-wide reply filtering and per-caller cached-backend Scene binding remain.
Other Scenes' viewports are not moved. Owner assignments need no recreation.

## Full canvas and native-backed custom markers

The native-exposure scissor is removed. An opaque fill again covers the whole
WINDOW, including native animation keys and Blender's marker strip. No native
key, Action, marker or animation data is hidden/deleted/rewritten to achieve this.

A custom 28-logical-pixel marker strip is drawn above Blender's 42-pixel native
marker event band. This placement avoids the masked Dopesheet keymap that caused
the first custom strip's click differences and clears the native horizontal
scrollbar. The lower native band is painted over; the scrollbar remains native.
Blender's [Dopesheet event-handler setup](https://raw.githubusercontent.com/blender/blender/main/source/blender/editors/space_action/space_action.cc)
and [marker/mask event polls](https://raw.githubusercontent.com/blender/blender/main/source/blender/windowmanager/intern/wm_event_system.cc)
explain why placing custom controls inside that lower band is unreliable.

There is one authoritative `Scene.timeline_markers` collection. Drawing/hit testing
reads it directly on every redraw, using native Blender-frame View2D X coordinates.
Native edits in another editor appear on subsequent normal redraw; no copied
marker database, synchronization handler, polling framework or persistent cache
was added. Basic triangle/name hit testing and Shift/Ctrl selection use native
marker selection flags. Marker focus is per Scene/workspace/area session state.

- M adds at the current Blender frame; F2 opens a rename dialog; Delete/X removes
  selected markers while marker focus is active. G starts the same preview gesture.
- Left drag previews an integer frame delta locally. Mouse release invokes one
  `REGISTER`/`UNDO` edit operator with native Undo requested. Each selected marker's
  final frame is written once. Escape/right-click cancels without frame writes;
  changed Scene/region/selection/data cancels a stale gesture. Marker locking is
  respected. There is no custom Undo history.
- Review/Depth editing stays separate. Blank painted native-band pixels do not
  fall through this dispatcher's Delete to hidden pose keys; scrollbar input is
  retained. B, Shift-click, horizontal transforms, vertical wheel/trackpad and
  parent/Depth geometry remain shared with the existing row implementation.

## Validation

**40 focused ordinary-Python tests passed**, no failures/errors/skips:

```text
python -S -m unittest tests.test_blendcap_vta_ui_followup tests.test_blendcap_vta_ui_regressions tests.test_blendcap_vta_workspace.ScopeTests tests.test_blendcap_vta_timeline.TimelineProofTests -v
```

The new fixture imports the complete workspace/marker/busy modules with host
sinks, executes actual Create/Manage callback bodies and the real cached backend,
membership adoption, bridge refresh, deferred foreground wrapper, app-timer
presentation, redraw notification and CHANNELS painter. It uses synthetic Scenes
and authority replies. It checks painted names after create/rename/delete, two
Scenes and return, independent targets, and reproduces the previous failure when
only the reveal step is disabled. A real B gesture and Shift-click after wheel
scroll use the shared parent/Depth coordinates. The full WINDOW draw is checked
for opaque bottom coverage and marker labels above the native event band.

Marker tests cover native-collection add/rename/delete/move, normal-editor-style
external changes reflected on redraw, name/icon hits, left-click route, Shift
toggle, M/F2/Delete dispatch, cancelled/locked edits and a local drag preview with
one undo-requesting commit on release. Existing overflow, trackpad, geometry,
stable-anchor, mode-menu and cached-context coverage also passes. Changed Python
files compile and `git diff --check` passes. No full suite was needed.

These tests do **not** execute Blender's native event loop, native history or
native Save/Reopen. Exact live visual behavior, actual registration, Undo/Redo
and Save/Reopen are left for owner QA, per the explicit no-Blender/no-computer-
control instructions. The expected persistence is the ordinary native marker
collection; it has not been independently certified in a running Blender here.

## Deployment and owner provenance

Five adapter files are deployed to the installed extension with prior installed
files backed up under ignored
`checkpoints/dev-preview/vta_ui_followup_20261110/rollback`. The deployment receipt
records the exact commit and hashes; syntax and installed-byte comparisons are
performed without starting Blender. Engine/provider/runtime configuration and
deployment staging are unchanged. No push, tag, release or installer work.

- **Manual QA file:** `C:\Users\dsguf\Desktop\QA.blend`.
- **Based on:** that exact current owner file (saved 2026-11-10, 14:12:32 local)
  and its current `QA_vta` companion. The supplied 16:00 screenshot and read-only
  companion observation data informed the investigation; no .blend was opened
  by the agent and no derived handoff replaced it.
- **Changes made to prepare this copy:** None.
- **Known special owner test modifications:** None.
- **Preservation:** all 430 files (blend plus 429 companion files) hash-identical
  during the final preservation audit. No other owner workflow project was written.

Restart normally to load the updated installed Python modules. The code is ready
for the owner's manual QA; this is not a claim of completed live acceptance.
The ignored proof/test artifacts are synthetic code/JSON evidence, not owner
handoffs or authoritative assignments.
