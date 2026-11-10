# MoCap UI regression pass — 2026-11-10

Small public-adapter change for Commands visibility, Character-row refresh,
overflow navigation and the native marker strip. No private/domain/protocol,
generation, camera cleanup, recovery, ownership or Preview J change.

## Changes

- The Dope Sheet header shows MoCap Commands only with MoCap Mode enabled.
  The direct Enter/Exit button remains. Enter/Exit operators remain registered;
  redundant workflow-menu entries are removed. Preview menus retain their
  existing role-based visibility checks.
- Mutation acceptance discards memoized frame/timeline projections and rebuilds
  both from the current backend observation and Scene membership. Project-wide
  replies are still filtered. Character Management and assignment selection
  remain independent; cached backend mutation context is rebound per caller.
- The previous Character-index/focus offset had no custom wheel/trackpad input.
  Native vertical movement could not move these pixel-positioned custom rows.
  Unmodified wheel now moves one displayed row; vertical trackpad deltas
  accumulate to row increments. Predominantly horizontal trackpad motion and
  modified wheel bindings remain native. The native horizontal scrollbar is
  retained, with no custom replacement scrollbar.
- Scroll state is transient and scoped by Scene/project/source/workspace/editor.
  A stable Character ID plus row kind anchors the viewport through reorder,
  rename, create and unrelated deletion. Removing the anchor or reducing content
  below viewport capacity necessarily clamps the position. Selection/redraw
  does not reset an explicit scroll. Depth rows use the same offset as parents;
  a scrolled viewport can start on a Depth child, as ordinary lists do.
- The custom canvas leaves Blender's 42 logical bottom pixels uncovered through
  a restored GPU scissor. Shared geometry reserves this space for rows. Tooltips
  move above the reserved strip. The native strip/scrollbar receives events;
  custom B/A/Alt-A/Delete dispatch passes through there. Custom M/F2/G bindings,
  marker hit testing, marker focus and marker drawing are removed, including
  `blendcap/vta_timeline_markers.py`. Scene markers remain the only marker data.

The layout follows Blender's existing marker margin and drawing order:
[Action editor drawing](https://raw.githubusercontent.com/blender/blender/main/source/blender/editors/space_action/space_action.cc)
and [native marker drawing](https://raw.githubusercontent.com/blender/blender/main/source/blender/editors/animation/anim_markers.cc).
Native selection, naming, movement, deletion and history now belong to Blender's
own marker handlers. No second marker system remains.

## Validation and limits

Final ordinary-Python run: **28 tests passed**, no failures/errors/skips:

```text
python -S -m unittest tests.test_blendcap_vta_ui_regressions tests.test_blendcap_vta_workspace.ScopeTests tests.test_blendcap_vta_timeline.TimelineProofTests -v
```

These execute the actual callback bodies with host sinks, the public presentation
cache and cached engine adapter with synthetic observations, and shared pure
geometry. Coverage includes two Scene membership scopes across create/rename/
delete and return, unchanged assignment target, rebound mutation callers, menu
visibility, native shortcut non-interception, restored clipping, wheel/trackpad,
small trackpad deltas, horizontal pass-through, all 60 synthetic parent/Depth rows
at multiple scales/heights, stable anchors, click/Shift-selection semantics,
frame offsets and box selection. Syntax and whitespace checks also pass.

Before the owner asked to stop Blender, an intermediate 139-test Blender run had
four failures and two errors. Three failures were draw-sink/changed-bottom-margin
expectations, one was the obsolete always-visible Commands expectation; the two
errors were a mocked operator lookup and Windows long temporary paths. Their
fixtures/expectations were corrected, but that Blender suite was **not rerun**.
An earlier run also encountered the sandbox temporary-directory permissions.
These are not reported as a passing final Blender suite.

The owner explicitly stopped Blender and computer control during this task.
The diagnostic Blender process was terminated, and the computer-use session was
reset; the owner confirmed the blue tint cleared. No subsequent Blender launch,
screen capture or desktop automation was used. The preference is recorded in
AGENTS.md. All subsequent checks used an ordinary Python process.

Consequently, final live registration, visual strip exposure/scrollbar clearance,
native left-click/Shift-click, M/F2/G/Delete, Undo/Redo, Save/Reopen and real-event
row selection remain **unverified in the final build**. The implementation delegates
these marker interactions to native Blender, but exact parity and visual absence
of overlap are not claimed as observed results. If View > Show Markers is disabled,
the native visibility setting still applies. An extremely short editor must be
enlarged to fit rows above Blender's native bottom margin.

## Deployment and owner state

Four public adapter files are deployed to the installed extension, and the obsolete
custom marker module is retired. Syntax and exact file hashes are checked without
starting Blender. Prior installed files are saved for rollback in ignored
`checkpoints/dev-preview/vta_ui_regressions_20261110/rollback`; `deployment.json`
records the exact commit and file hashes. Engine, provider, runtime configuration
and deployment staging files are unchanged. No push, tag, release or installer work.
Restart is the normal way to load these updated files; final runtime/visual
acceptance is left to the owner, not certified by background/source checks.

- **Manual QA file:** `C:\Users\dsguf\Desktop\QA.blend`.
- **Based on:** that exact current owner file, saved 2026-11-10 at 14:12:32 local,
  with its current `C:\Users\dsguf\Desktop\QA_vta` companion. It was neither opened
  nor replaced during this pass; no older/derived project was substituted.
- **Changes made to prepare this copy:** None.
- **Known special owner test modifications:** None.
- Preservation: all **426 files** (blend plus 425 companion files) hash-identical
  before/after; receipts are in the ignored checkpoint directory. Other owner
  workflow projects were not modified. No assignment recreation is required.

The internal `DIAGNOSTIC-native-markers.blend` uses 30 synthetic Characters and
two native markers, and patched presentation providers. **This is a stress-test/
diagnostic fixture and is not suitable for normal visual QA.** It is not an
owner-facing handoff and has no authoritative owner assignments.
