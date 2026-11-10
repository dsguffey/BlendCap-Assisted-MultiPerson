# MoCap manual QA follow-up — 2026-11-10

The owner supplied screenshots at 17:24 and 17:25. `test2` is visibly painted immediately after creation, confirming the previous create-row change in the live build. No Blender process or computer control was used for this pass.

## Existing names outside the Scene

Read-only inspection of `Desktop/QA_vta/project_state.json` shows project identities `tall ref` (`character-fc8aae1811ef432690fb9e711814b1ff`), `test` (`character-b9376e7571724ed7854ada3f30ce5f99`) and `test2` (`character-7bb9b1ccfc8048a8ac267fd286790143`). The current engine observation for source `source-594d54c7550c45e8ac96388f1ac317eb` lists `tall ref` among source members, but not `test` or `test2`. A host binding associates `tall ref` with Scene owner `7bc54af063bd4c2daa8d36253ec4c9b5`. The screenshots show only black shirt/no shirt in the current Scene before `test2` was created, then `test2` appears. The exact saved Blender Scene membership cannot be decoded without opening the `.blend`; the screenshot and source-level filtering establish that the Scene view excludes the older identities.

The project registry reserves display names project-wide. This is correct: silently creating a second `tall ref` or `test` would produce duplicate semantic identities. The access problem is real for any project with Scene membership excluding an existing project identity, whether caused by historical QA reconciliation or deliberate Scene separation. The current Character Management selector enumerates only the Scene-filtered frame view, so the name was reserved with no user-facing reuse path.

The Character Management panel now offers **Use Existing Project Character** when the cached canonical project observation contains identities outside current Scene membership. The user must explicitly choose one. The operator verifies a fresh project view, adds that identity to the current Scene's existing membership field, refreshes the view and selects it in Management. It does not create another identity or change the assignment target. Creating a reserved name now explains this recovery path. No automatic migration or cleanup touches other Scenes.

## Row scrolling and marker style

The custom rows have their own `_row_scroll` offset. Blender's native vertical elevator moves native View2D, which cannot guarantee the custom painted rows follow it; prior tests did not assert return to row zero. A visible custom elevator in the CHANNELS region now writes the same clamped row offset as wheel and trackpad input. It sits immediately left of Blender's native edge strip. Both controls use the same total count including expanded Depth children and the same visible capacity. The recent Create/Rename reveal and Delete anchor behavior remain in the shared layout path.

The custom marker draw routine no longer paints a dedicated gray rectangle or separator line. Marker glyphs and labels remain in the fixed bottom lane over the ordinary canvas background; hit bounds and native Scene marker routing are unchanged.

## Validation and provenance

52 focused ordinary-Python tests pass, including painted Create/Rename/Delete, 30-Character overflow, wheel down/up to both limits, custom elevator down/up, off-Scene explicit identity reuse without duplicate creation, Depth geometry, selection after scroll, and marker authority/interaction. Native Blender event routing, visual appearance, Undo/Redo and Save/Reopen remain for owner manual QA.

Owner source is `C:\Users\dsguf\Desktop\QA.blend` with its current `QA_vta` companion. Preparation changes: none. Special diagnostic modifications: none. Read-only inspection covered project state, one engine observation, one host binding, and supplied screenshots. The 435 current owner files were hash-recorded before deployment and verified afterward. No owner file was saved or replaced. The installed code has a separate exact-file rollback; no push, tag, release or installer work was done.
