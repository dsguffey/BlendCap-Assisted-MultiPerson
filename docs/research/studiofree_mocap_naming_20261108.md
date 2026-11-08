# StudioFree MoCap — tentative installed UI naming

Naming-only update, preserving UI Pass 2 organization. Full product title is **StudioFree MoCap**; short workflow/sidebar term is **MoCap**. Character, Candidate, Tracking, Animation and Depth retain their meanings. No replacement acronym.

The extension manager title, Preferences, Resources version heading and newly opened workspace presets use the full product name. Sidebar tabs, commands, mode/Scene/workspace actions, tooltips, confirmations and public status/recovery/error summaries use the approved short terminology. Resources is last; the single Workspace drawer and existing section order are unchanged. Credits retain BlendCap and other existing acknowledgements.

## Compatibility and limits

- Extension/package ID, operator and class identifiers, modules, property names, schema/protocol values, environment variables, runtime folders and companion conventions remain unchanged. No private implementation was moved/imported into Blender.
- AST comparison against the preceding commit verified identical non-string Python logic in all 30 changed production Python modules, with one explicit display-only exception: assigning the full product name to a newly appended workspace. Native operator IDs and extension manifest identity/version were compared separately and remain identical.
- The bundled workspace lookup **Video to Animation** stays internal so the unchanged binary preset still loads. Existing owner-saved workspace tabs, Scenes, objects, Actions and internal recovery Text datablocks are not renamed. Such saved data names may still display old wording in Blender; this is not a compatibility migration.
- The complete local inventory flags current README branding and four private-engine diagnostic sentences for later review. Private engine code is unchanged. Normal public error summaries are renamed, but rare engine Details/log text can retain VTA wording. No claim of zero remaining VTA strings.
- F3/menu labels use the same existing operators. No new search paths or registration redesign; live F3 discovery remains context-dependent and is not claimed as visually verified.

## Focused evidence

54 focused source checks passed: renamed labels, stable operators, section order, Preferences/Resources/Credits, mode/recovery strings, existing dialog callbacks and workspace behavior. Fresh background Blender registered the adapter, opened the unchanged bundled preset under the new visible name, and drew nine relevant panels against an isolated pre-existing diagnostic QA copy. Owner file paths are blocked from engine calls; the probe allows only read-only project/observation operations and rejects private host imports. Compilation and diff checks passed. Installed verification is recorded in the local deployment evidence.

This is for David's continued manual UI review, not broader QA acceptance. Restart Blender and use the **MoCap** sidebar tab in the same canonical `C:\Users\dsguf\Desktop\QA.blend`. No owner project preparation, saved-name migration or diagnostic modifications are required.

## Separate remaining-occurrence inventory

Local, uncommitted evidence directory: `checkpoints/dev-preview/vta_naming_20261108`.

- `remaining_inventory.md`: owner-readable grouped findings, including each remaining user-facing location/text and proposed replacement.
- `public_remaining.tsv` and `private_remaining.tsv`: one row per literal case-insensitive VTA substring, with path, line, column, current text, category and recommendation.
- `inventory_summary.json`: exact final counts, coverage and excluded binary/undecodable files.

Scope is all tracked and nonignored untracked text/code in both repositories' working trees. Git history, ignored dependencies/caches/generated QA/checkpoints and binary asset internals are excluded. Internal names, historical/developer references and packaging/runtime paths are retained and classified. This is an explicit working-tree inventory, not a fresh security/history or legal audit. Private source excerpts remain in ignored local evidence and must not be committed to the public repository.

Only public adapter/source files are deployed, with prior files and deployment metadata retained for rollback. No owner QA writes, private-code changes, compatibility/schema/runtime migration, UI restructuring, push, tag, release or installer work.

Installed validation caught an unintended runtime lookup-label replacement before handoff. It was restored to the exact VTA/config/runtime.json path; a dedicated regression and comparison of path/schema/operator/environment literals against the pre-naming baseline now verify compatibility. No runtime configuration or owner data was moved.
