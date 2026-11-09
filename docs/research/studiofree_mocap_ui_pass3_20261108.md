# StudioFree MoCap — bounded UI Pass 3

Deployed for continued owner interface review, not broader manual QA acceptance.

## Structure and interactions

Top-level order: **MoCap Setup → Analyze → Character Assignment → Update Tracking → Animation → Overview → Export / Retarget → Resources**.

MoCap Setup starts with **Create MoCap Workspace**, then **Video Source**. Its collapsed drawers are MoCap Scenes, Camera and Playback, and Display. Camera and Playback retains Snap, Match FPS and its final Advanced drawer. Details and the separate top-level Video Source/Display panels are removed. Existing internal panel identifiers are reused where practical; the former Display panel hosts Overview and its display controls are drawn inside Setup.

Create retains the existing `blendcap.open_vta_workspace` operator ID but explicitly appends a new bundled preset on every invocation and assigns it to `context.window.workspace`. Blender supplies unique names. Earlier implementation reused existing tagged workspaces; that idempotency has been replaced only for this explicit Create action, as approved. The separate existing-workspace lookup used by Scene/session logic is preserved. No workspace creation was added to registration/install, and creation does not choose a Scene, source or mode. Bundled workspace lookup and current product branding remain compatible.

Character Assignment retains its current structure, overlay, Repeat, and distinct Occluded / Not Visible / No Valid Candidate actions. Those three states remain preserved but intentionally unresolved for later review. When review data is unavailable, the main Repeat/Apply actions remain visible and disabled. Update Tracking has no persistent Update-needed header or new requirement to reconcile every unresolved interval. Animation keeps its existing generation/progress/cancel path.

The redundant **Move Along Camera Depth** button is removed from Setup. Its operator, helpers and the validated gizmo implementation remain intact. Export / Retarget controls and implementation are unchanged except a tooltip clarification. Resources remains last with Preferences, logs, runtime/version information and Credits. Global command-menu Retry and contextual pending-warning Retry retain the existing recovery operator/path.

## Overview and tooltip limits

Overview is flat and cached/readily available: source filename, source/Scene FPS, current frame, Candidate/Character counts, analysis revision/status, bounded **cached review** source-frame coverage, assignment/suppression mark counts, reconciliation state, selected generation range, selected Character count and cached animation feedback when present.

The selected range is explicitly labeled **Generation selection**, not represented as accepted generated output. Cached review coverage is not represented as full analyzed-source coverage. A complete analyzed range and authoritative accepted-animation range/status are omitted because deriving them would require additional project/output reads or animation scans. No such work is added to redraw. No Update-needed/perfect-tracking warning was introduced.

Assignment/suppression tooltips now describe their action and contextual missing marks/Character, running-operation or recovery condition. Generation uses its existing unavailable-reason helper in the tooltip. Analyze, Update Tracking, Export, Retarget and Retry descriptions explain their function and normal prerequisites; no custom tooltip framework or enable/disable semantics were added. Existing conditional feedback remains outside Overview where needed.

## Focused verification

- **57 focused source tests passed**, zero failures/errors/skips: current hierarchy, source/scene/camera/display placement, recovery, marking, generation tooltip, cached Overview, workspace and existing UI callback behavior.
- Overview was drawn 20 times with refresh/engine acquisition set to fail if called; no IPC, observation rebuild or nested section was used.
- Fresh background Blender registered without creating a workspace. Two native Create calls appended two distinct correctly named preset workspaces without changing the Scene. A permanent regression and real-workspace/proxy-window smoke confirm the operator assigns the exact new workspace to the window.
- **Background Blender does not prove visible tab focus**: its workspace switch is deferred through UI event handling. Production uses the existing native window-assignment API; David's manual UI review must confirm visible focus.
- Fresh installed checks: **12 passed**, zero failures/errors/skips. Installed native registration and all eight relevant panel draw callbacks passed against the existing isolated diagnostic QA copy, with private-import and canonical-companion access guards. Ten existing operator IDs resolved through native RNA. New workspace creation is memory-only and is not saved into owner data.
- Updated stale idempotent-workspace/panel expectations and fake layouts to match this approved interaction. Compilation and diff checks passed. Depth gizmo code, protocol, private core, provider/model execution, domain/persistence semantics and runtime configuration were not changed.

Source/test commit: `c9dce74` (`Align MoCap Setup, workspace creation and Overview with UI Pass 3`). Seven public UI modules were copied to installed and adapter-staging locations with exact prior files/metadata retained in ignored `checkpoints/dev-preview/vta_ui_pass3_20261108/rollback`. Installed evidence and preservation checks are recorded alongside it. No push, tag, release or installer work.

## Owner handoff

Use **C:\Users\dsguf\Desktop\QA.blend** and its existing **QA_vta** companion. Based on the latest owner state at the start of this pass; no owner file preparation, migration, regeneration, reconciliation or diagnostic modification was performed. Internal copies must not replace it.

Save current work as needed, restart Blender, and continue manual UI review in the MoCap sidebar. Check the consolidated setup/overview and that each Create invocation visibly focuses its new tab. This is UI Pass 3 readiness, not product-wide QA acceptance.
