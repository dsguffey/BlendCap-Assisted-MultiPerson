# Character viewport root cause and bottom markers — 2026-11-10

Historical investigation preceded production edits. No Blender launch, computer
control or screen capture was used. The supplied owner screenshot was reference
material. This report supersedes the operation-time reveal approach in `a1ac211`.

## Historical evidence

The last reproducibly good pre-marker implementation is **`9c9115f`**, the parent
of the first slot-accounting regression. Its row geometry is the same as the
deployed checkpoint **`ea4a6b2`**, and originated in the accepted Depth baseline
`d16d497`. This is a source/history result, not a claim of newly running those
versions in the owner's foreground process.

For the supplied layout's approximately 194 logical-pixel WINDOW height, three
Characters and their expanded Depth children yield this exact historical slice:

| Commit | Reserved bottom | Painted slots | Third Character parent visible |
| --- | ---: | ---: | --- |
| `ea4a6b2`, `9c9115f` | 0 | 5 | Yes |
| **`8bb48cb`** | **28** | **4** | **No** |
| `b3920d5` | 42 | 3 | No |
| `a1ac211` | 70 | 2 | No |

The concrete initiating change is `8bb48cb`'s replacement of
`(height - top_padding) // row_height` with
`(height - top_padding - 28 * ui_scale) // row_height`. Expanded Depth rows were
already in the flattened row list. The added bottom reservation silently removed
the final parent from a previously working visible slice. The subsequent 42- and
70-pixel reservations made the loss worse. `a1ac211`'s two-slot result matches
the screenshot's black-shirt/Depth-only canvas.

The Create path historically selected the new assignment Character through
`7ce5ece`. `5eda92f` deliberately removed that coupling to implement management
selection independence. It did **not** change row capacity: historical comparison
still shows five slots and the third parent. This selection separation is retained;
it is not rolled back or treated as the first slot regression.

Creation, Scene membership adoption, cached backend reuse, filtered timeline/frame
projection, queued presentation timer and redraw were compared across history.
The current problem is not repaired by another cache clear. A fresh model and
`tag_redraw` do not guarantee inclusion in the flattened, sliced painted rows.
`a1ac211` added a best-effort operation-time reveal but left that guarantee outside
the layout. Its reveal is discarded if no rows fit at that moment, and is absent
when a model is subsequently adopted without reaching that final callback.

Before changing production code, three new paint-level tests **failed on
`a1ac211`** despite the new/renamed Character being in `_timeline.characters`:
unusable operation-time geometry followed by usable paint geometry; model adoption
without a second operator callback; and a rename moving its parent out of view.
The earlier model-only tests omitted slicing. The follow-up foreground-shaped
tests supplied usable geometry and a successful final reveal callback, so they
also missed these paths. Logs and the six-version historical proof are in ignored
`checkpoints/dev-preview/vta_root_cause_20261110`.

The exact native callback exit taken in the owner's live process was not captured.
The slot regression, screenshot-consistent slice and missing renderer invariant
are demonstrated independently; they are not presented as live runtime tracing.

## Correction in the shared viewport path

The operation-time `reveal_character` function and Create/Rename calls are removed.
The shared metrics/layout path now reconciles the current Scene/source/editor's
ID/name projection before **both painting and hit testing**. New or renamed IDs
are revealed only if outside the painted slice. A pending reveal survives zero
available slots and is consumed when usable geometry arrives. The first draw can
also honor an existing management target without changing either selection.

The existing stable row anchor is retained for ordinary redraw and wheel input.
Deleted anchors are rebased to the actual nearby valid row, preventing an old
numeric fallback from causing a later jump. Scene/source/workspace/area scope is
the same as the existing Depth/scroll presentation scope. This is disposable
Python UI state, cleared on file load; it changes no project or Scene authority.
Assignment, management and generation selections are never written by reconciliation.
The current Scene-context fix, membership filtering and cached mutation caller
rebinding are preserved. No alternative Character mutation path was introduced.

## Bottom marker lane and input

The fixed custom marker lane now occupies **16–44 logical pixels** from the WINDOW
bottom: 16 pixels for Blender's horizontal scrollbar, then a 28-pixel marker lane.
Character/Depth rows reserve this same 44-pixel total. The former extra blank
42-pixel native-event reservation is gone. Full opaque canvas coverage remains;
native animation keys and native marker drawing are not exposed or damaged.

Blender excludes its native marker band from the normal Dopesheet event map when
native markers exist. Rather than moving the custom lane upward to avoid that
poll, the same MoCap-scoped handlers are also bound in Blender's **Markers** map.
The Dopesheet route still handles an empty native collection or hidden native
marker display. Outside MoCap Mode the operator polls fail, retaining ordinary
Blender behavior. This adds no generic input framework or native handler patches.

Single-click selection, F2/M/G/Delete/X and the existing local drag preview remain.
Double-click explicitly routes through the same rename dialog as F2; it is not
removed. G/direct drag still write the final native marker frames once on release
through the existing undoable operator. `Scene.timeline_markers` remains the only
marker collection, read on every redraw. There is no new marker cache, persistence
format, synchronization engine or Undo history.

## Validation and deployment

**50 focused ordinary-Python tests pass**, no failures/errors/skips:

```text
python -S -m unittest tests.test_blendcap_vta_viewport_root_cause tests.test_blendcap_vta_ui_followup tests.test_blendcap_vta_ui_regressions tests.test_blendcap_vta_workspace.ScopeTests tests.test_blendcap_vta_timeline.TimelineProofTests -v
```

Coverage includes the historical 5/4/3/2-slot regression, all three initially failing
paint contracts, actual callback/timer/redraw/paint bodies for Create/Rename/Delete,
two Scenes and return, pending layout, sensible delete clamping, stable redraw,
30-Character wheel traversal with expanded Depth, Shift-click/B after scrolling,
bottom marker bounds at several scales, full native-region coverage, single/double
click rename, F2/M/G/Delete/X bindings, local preview/single commit, and native-data
round trips. A fixture initially added multiple synthetic memberships using an
outdated observation; it was corrected to publish the complete synthetic set.
No production mutation or membership code was changed for that fixture error.

Python syntax and whitespace checks pass. No shared lifecycle implementation,
tracking/review semantics, sparse cleanup, selective generation, Preview J or
Commands visibility logic changes. A full suite was not needed. Actual native
registration, event-loop dispatch, rendering, Undo/Redo and Save/Reopen remain
owner manual QA; no native Blender validation is claimed after the stop request.

Four changed adapter files are deployed, with exact prior installed files backed
up under the ignored task `rollback` directory. `deployment.json` records the
commit and installed hashes. Engine/provider/runtime configuration and deployment
staging remain unchanged. No push, tag, release or installer work.

## Owner provenance

- **Manual QA file:** `C:\Users\dsguf\Desktop\QA.blend`.
- **Based on:** that exact current owner file and its current `QA_vta` companion;
  read-only source/history/receipt inspection, with no .blend opened or replaced.
- **Changes made to prepare this copy:** None.
- **Known special owner test modifications:** None.
- **Preservation:** 433 files (blend plus 432 companion files) hash-identical in
  the final audit. No owner workflow project was written. No assignments need
  recreation. Synthetic proof/test artifacts are internal code/JSON evidence.

Save current unsaved work before a normal restart to load the installed update.
The build is ready for continued manual QA, not certified as live accepted.
