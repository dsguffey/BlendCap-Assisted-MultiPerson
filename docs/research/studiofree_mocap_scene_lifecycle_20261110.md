# StudioFree MoCap Scene lifecycle cleanup — 2026-11-10

The 1.0 reconciliation is a one-shot pass, not a Scene monitor. The shared host
routine is `blendcap/vta_scene_reconcile.py:reconcile`. Its host-independent
classification is `blendcap_vta_scene_reconcile.py:plan`. The former is scheduled
once after an existing `.blend` file opens (also after add-on registration into
an already-open file), and called inside `vta_scene_session.load` after Scene and
workspace validation, before presentation refresh. Enter MoCap Mode uses that
same `load` path. No Blender Scene is recreated or selected as a substitute.

The routine first asks the private engine to validate the companion project and
return a checkpoint, then reads the exact checkpointed project document. It
compares all surviving Blender Scenes and their current/history membership,
project-wide Character references, native ID tags and bounded host-binding
receipts. Missing Scene membership, a pending recovery, unknown project fields
and ambiguous owner tags block automatic Character deletion. Canonical project
deletion uses the engine's existing guarded `review-management` mutation, with
the observed revision. Stale receipts for absent, unambiguous Scene owners and
invalid membership IDs are removed only after any canonical mutations succeed.
Deleted Scenes' own membership properties disappear with their Blender Scene;
there is no separate Scene registry to edit. The Load chooser already enumerates
`bpy.data.scenes`, and `load` now reports a missing/stale target explicitly.

The Character Management cross-Scene reuse control was removed. Names belonging
to still-referenced Characters remain reserved project-wide, with an error that
explains they belong elsewhere. No automatic Character transfer is performed.

## QA provenance

The owner source is `C:\Users\dsguf\Desktop\QA.blend` and its current `QA_vta`
companion. Preparation changes: none. Special diagnostic modifications: none.
Read-only inspection of the companion shows `tall ref` and `test` both have
creation/adoption receipts for Scene owner
`7bc54af063bd4c2daa8d36253ec4c9b5` and source
`source-594d54c7550c45e8ac96388f1ac317eb`. `tall ref` additionally has a
user-authoritative identity keyframe at frame 0. A read-only zstd stream scan of
the saved `.blend` finds that Scene owner and four Scene membership records, but
neither current Character ID. This is useful evidence, not a complete native
state evaluation. The saved `.blend` is from 14:12 while the companion project
was updated at 17:25, so unsaved foreground Scene/native edits cannot be ruled
out. Neither identity was manually deleted; no owner file was edited. Their
fate on a later file-open pass depends on actual surviving references: ambiguity
preserves them.

## Validation boundary

62 focused ordinary-Python tests passed for the new planner/runtime fixture,
missing Scene and selector behavior, file-open scheduling, explicit load ordering,
painted Create/Rename/Delete, scrolling, marker and timeline paths. Five existing
isolated-engine process tests could not initialize their own fixtures in this
environment because `-I` resolves an unrelated installed `tests` package ahead
of the private repository's namespace tests. A separate disposable direct
engine probe could not pass the sandbox's child-PID identity check. Neither
failure exercised the new reconciliation code.
No Blender process or computer control was used. Native startup/registration,
Undo/Redo and Save/Reopen remain owner manual QA.
