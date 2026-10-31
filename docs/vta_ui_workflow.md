# VTA UI pass 1 — development workflow

VTA is the provisional product label. This source checkpoint is not an installer
or release, and must be deployed with its matching engine before owner UI checks.

Open Workspace uses the VTA review workspace. Preferences contains persistent
workspace-shortcut visibility and runtime/diagnostics guidance; Scene source,
review, depth and generation choices remain in the project workflow. Runtime
installation and repair belong to setup, not a Preferences dependency wizard.

## Mark, then apply

Select a candidate to inspect it, then choose its intended mark:

- **Mark for Assignment** uses a keyframe symbol and `[A]` preview label. Marks
  span frames; a new assignment mark replaces the previous one on that frame.
  Choose the Character, then use **Assign Marked** to apply the whole batch.
- **Mark for Suppression** uses a hidden-eye symbol and `[S]` preview label.
  Several candidates can be marked on one frame. Use **Suppress Marked** to
  exclude those candidates from automatic continuation.
- **Clear Marks** changes no saved review decisions. Marks are temporary, scoped
  to the current Scene/source and cleared on file load or addon disable.

A candidate cannot have both contradictory marks. Marks do not assign identity
until the action succeeds. Existing decisions require replacement confirmation;
conflicts preserve the marks and abort the entire batch. Successful actions clear
the marks they handled. One-item and multiple-item batches use the same action.
Requests are bounded; apply or clear the current batch before adding more marks.

Restore Candidate remains available for suppressed evidence. Repeat Last
Assignment remains a fast existing gesture that also advances one frame.

## Generate, refine, retarget, export

Generation uses the selected Character rows and the chosen frame range. Preparing,
model loading, reconstruction, validation and application show actual job phases.
A progress bar appears when real completed/total counts exist; Cancel and explicit
recovery remain available in their supported states. Explicit guarded edits show
initial Working feedback before control I/O; this does not make native editing
fully asynchronous.

Fixed Depth remains the default. Automatic Depth and first/every-frame FOV stay
under Advanced Camera. Native Depth editing, repairs, camera cleanup and Restore
retain their existing safeguards. Retargeting is a secondary collapsed panel;
supported profiles and finger behavior are unchanged.

Export BVH writes the selected generated VTA Character's current evaluated
animation over the Scene range, including manual Depth and cleanup. It preserves
source animation by baking an isolated temporary skeleton. Static uniform object
scale is supported; animated/nonuniform scale is refused rather than silently
lost. This exports animation/skeleton data, not meshes, materials or arbitrary
control rigs. Legacy BVH library/import/rebake controls are outside the workflow.

Advanced → Open Logs Folder and contextual Details/Go to Log remain. Credits at
the bottom retain BlendCap attribution, source/license links and upstream support.
Permanent naming, visual refinements, final packaging and release QA remain later
work; see the canonical project status and private pass-1 audit for evidence.
