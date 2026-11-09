# StudioFree MoCap — current UI workflow

Updated 2026-11-09; UI Pass 5 is locally deployed. This is not full owner manual
acceptance or Public 1.0. StudioFree MoCap is tentative; MoCap is shorthand and
internal VTA identifiers are retained. See [current status and roadmap](current_status.md).

## Sidebar order and setup

The product title appears above nine workflow sections: **MoCap Setup → Analyze →
Character Management → Character Assignment → Update Tracking → Animation →
Overview → Export / Retarget → Resources**.

MoCap Setup contains Create MoCap Workspace, Video Source, MoCap Scenes, Camera
and Playback, and Display. Create may append multiple distinct workspaces and
assigns the new one to the invoking window. Visible interactive tab/sidebar focus
still needs owner review where applicable; opening the sidebar is best-effort.
Preferences belongs in Resources. There is no separate Details section in Setup;
contextual operation Details/Go to Log remain.

## Manage separately from assigning

Character Management contains Create New Character, Available Characters, Rename
and Delete. Its session-only selection does not change assignment/Candidate,
marks, generation/tracking, retarget or native object/preview selections. Newly
created Characters become selected there for management, not automatically for
assignment. Rename/Delete use the existing protected command paths.

Character Assignment places the assignment-target Character selector immediately
above Candidate. Select Candidates across frames, mark them, then apply:

- **Mark for Assignment** uses a distinct keyframe symbol and Character-name
  preview label. One assignment Candidate per frame; several frames may be marked.
  Choose the target Character and use **Assign Marked** for the batch.
- **Mark for Suppression** uses a distinct suppression symbol/label; several
  Candidates on one frame are allowed. **Suppress Marked** excludes those exact
  observations from automatic continuation; raw evidence is retained.
- **Clear Marks** changes no saved review decisions. Marks are temporary and
  Scene/source-scoped, cleared on load/disable. Successful application clears
  handled marks; conflicts/failure retain them and require the existing consent.

Repeat Last Assignment Mark remains in MoCap command menus and Preview J, not the
sidebar. With a valid current Candidate and the previous applied Character as the
assignment target, it creates an ordinary pending mark and advances one frame.
**Apply Marked remains required**; Repeat does not commit authority. Restore
Candidate remains for suppressed evidence. Occluded / Not Visible / No Valid
Candidate remain supported pending later UI/semantic review.

Clear Assignment's redundant sidebar button is removed. Deleting the relevant
selected assignment review key provides the same authoritative removal and
protected Restore policy. The Clear Assignment command/operator is retained in
menus and internal callers. Pending Candidate marks are separate user intent,
not saved assignments. Recovery warnings and Retry Reconciliation remain visible
when needed; unavailable edits do not bypass recovery.

## Generate, refine, export

Analyze and Animation both offer **Coverage: Whole Source / Frame Range**.
Animation defaults to Whole Source; Frame Range exposes the existing inclusive
Blender Start/End fields. Coverage chooses frames, while selected Character rows
choose Characters. There is one Generate Animation command. Real job phases,
progress where counts exist, Cancel and explicit recovery remain.

Fixed Depth remains default. Automatic Depth and FIRST/EVERY camera options stay
under Advanced camera controls. Use the viewport Depth gizmo and native Depth
keys for manual travel. Repairs and rotation-only camera cleanup retain safety
checks. **Restore Source Alignment stays visible but disabled when unavailable**;
it restores saved generated Root curves while retaining later Object Mode/limb
edits and leaving the source camera unchanged. It is distinct from the review
command **Restore Last Protected Edit**.

Overview is a flat, cheap passive information surface: no generation-range
summary and no demand to fix every unresolved tracking region. Export / Retarget
retains the current supported profiles. BVH exports current evaluated native
animation including Depth/cleanup over the Scene range through an isolated
skeleton, preserving source Actions; meshes/materials/arbitrary control rigs are
not included. Existing nonuniform/animated-scale refusals remain.

Resources remains last: Preferences, logs/support, runtime/version information,
Credits and upstream attribution. Credits do not replace legal notices.

## Review policy and next milestone

There is no required pre-release UI freeze. Continue proportionate focused
UI/registration/regression checks; broaden validation for lifecycle, authority,
persistence, architecture or other broad behavior changes. Avoid structural churn.
Next is a new clip/project through the complete workflow; Desktop QA.blend/QA_vta
remain the preserved regression fixture, not evidence of full build acceptance.
