# StudioFree MoCap — current checkpoint and Public 1.0 roadmap

Updated **2026-11-20**. This remains development work, not Public 1.0 or a
commercial release. The compiled engine, licensing prototype, portable AI
runtimes, offline internal package, maintenance lifecycle, source-level
security hardening and isolated test signing have passed their stated bounded
checks. Production signing, commercial approval and actual signed-installer
execution on a clean disposable Windows environment remain open. **StudioFree MoCap** is the
tentative public name; **MoCap** is the user-facing shorthand. Name/trademark
clearance remains open. Internal `VTA`/`vta` identifiers intentionally remain.

## Product and architecture

The goal is human-guided recovery of usable, editable skeletal animation from
difficult single-camera real-world footage, especially multi-person, occlusion
and grappling cases. Minimize correction effort rather than optimize only for
maximum autonomous monocular mocap. Facial animation remains outside 1.0.
Generalized retargeting is not the product moat; current supported profiles and
export/retarget workflow are sufficient for the present direction.

**Architecture Cleanup M1–M5 is complete and locally deployed.** The GPL Blender
host and proprietary engine remain separate across a neutral/versioned IPC
boundary. Host-independent domain/tracking/business logic and authoritative
review/tracking/generation decisions stay in the engine. The public UI imports
no private implementation; non-authoritative UI/presentation state may remain
Blender-side. Native output application, manual Depth and target rig integration
remain public. One-shot authoritative control requests, durable jobs, neutral
file-backed provider results and private acceptance remain accepted. Provider
models may stay resident within a job. No persistent authoritative daemon is
planned for 1.0. M5 runtime/package layout remains the accepted foundation;
final installer/distribution readiness is not implied.

Current compatibility: protocol **8**, package layout **1**, ProjectState **0.9**,
development build **1.0.0-dev.m5**. See [engine ownership](vta_engine_boundary.md)
and [runtime/setup foundation](vta_runtime_installation.md).

## Current interface and workflow

An always-visible StudioFree MoCap banner identifies the sidebar. Its workflow
sections remain in this order:

1. **MoCap Setup** — Create MoCap Workspace, Video Source, MoCap Scenes, Camera
   and Playback, Display. Repeated Create produces distinct workspaces and assigns
   the new workspace to the invoking window. Visible interactive tab/sidebar focus
   remains subject to manual review; sidebar opening is best-effort normal RNA.
2. **Analyze** — Whole Source / Frame Range evidence analysis.
3. **Character Management** — Create New Character, Available Characters,
   Rename, Delete. Management selection is independent of assignment, generation,
   tracking, retarget and object/preview selections.
4. **Character Assignment** — assignment-target Character directly above
   Candidate; assignment/suppression marks, then Apply Marked. Assignment marks
   identify the target Character by name. Repeat is a menu/Preview-J convenience,
   not a sidebar button: it creates an ordinary pending mark and advances one
   frame; Apply Marked is still required. Occluded / Not Visible / No Valid
   Candidate remain supported; their final UI/semantic treatment remains open.
   The redundant Clear Assignment sidebar button is removed; equivalent selected
   assignment-review-key deletion and underlying command-menu paths remain.
5. **Update Tracking** — explicit bounded reconciliation when useful; it does
   not silently reconcile every pending region or edit unrelated Characters.
6. **Animation** — Coverage: Whole Source / Frame Range; Start/End appear in
   Frame Range. Selected Character rows still control Character scope. Real job
   phases/progress, cancellation, recovery and transactional output remain.
7. **Overview** — flat passive cached/readily available information; cheap to
   render, no generation-range duplication or nagging about every unresolved
   tracking region.
8. **Export / Retarget** — current evaluated native animation/export and the
   supported target-profile workflow.
9. **Resources** — Preferences, logs/support, runtime/version information, Credits.

Fixed Depth remains the default. Manual depth movement uses the viewport gizmo
and native selectable/deletable Depth keys; its redundant sidebar button is gone.
Remove Camera Movement remains rotation-only. **Restore Source Alignment** is
always visible in Animation Cleanup and disabled when unavailable. Its protected
restore reinstates saved generated Root channels, preserves later Object Mode/
limb edits and leaves the source camera unchanged. It is distinct from Restore
Last Protected Edit for destructive review edits. See [workflow](vta_ui_workflow.md).

The [bounded animation-editing compatibility pass](research/studiofree_mocap_animation_editing_20261110.md)
preserves sparse anchors during rotation cleanup, adds Cancel-default mixed-range
regeneration choices per Character, and exposes native Scene markers plus basic
Blender-like selection in the MoCap timeline. Automated validation is complete;
foreground editing/visual acceptance remains pending. No automatic stabilization
or change to review/tracking authority is included.

## QA and UI development policy

Owner manual review has passed portions of assignment UI, suppression UI and
prior depth-gizmo behavior; layout/polish review is ongoing. This is **not full
post-deployment acceptance**. Clear Selected Review Keys' confirmed-dialog/busy
lifecycle regression and Update Tracking's deferred callback-registration
regression were reproduced, repaired, deployed and regression-tested. Pending
reconciliation now has visible contextual/menu recovery access. Do not infer
whole-build acceptance from individual fixes or automated totals.

UI Pass 4 recorded 906 full public tests plus later focused checks. UI Pass 5
recorded 107 focused source checks and 17 installed checks, with no full-suite
rerun. Those counts describe their respective checkpoints and overlap; they are
not a new combined current-build full-suite claim. Earlier coordinated deployment
recorded 873 public / 537 private checks. Current QA.blend and QA_vta remain the
owner-authored regression fixture; [preservation rules](qa_workflow.md) apply.

**There is no required pre-release “UI freeze.”** UI refinement may continue up
to release. Avoid unnecessary structural churn; validate proportionally to risk;
preserve domain semantics and architecture. Major release/package work should
not casually destabilize the UI. Mostly visual/layout passes should use focused
UI/registration/regression tests. Run broad suites when changes touch shared
lifecycle, authority, persistence, architecture or other broad behavior, rather
than automatically for each layout edit.

## Engineering evidence and remaining Public 1.0 work

| Area | Current evidence | Remaining gate |
| --- | --- | --- |
| Architecture, UI, compilation and licensing | M1–M5, stabilized workflow, source-free compiled workers, Gumroad live prototype and owner-visible activation feedback are implemented and have focused tests/QA. | Final full-workflow and release-candidate Blender GUI acceptance. |
| Portable AI runtime | Separate pinned CPython 3.12 control/body/analysis environments passed relocated import and representative GPU inference checks. | Clean-machine GPU/driver compatibility and final package verification. |
| Offline installer and maintenance | Complete internal-only ZIP (10.58 GB compressed, 15.60 GB payload); actual packaged AI inference and synthetic/disposable CLI install, update, repair, rollback and uninstall checks passed. A [2026-11-20 isolated Blender CLI check](research/studiofree_final_installer_qa_20261120.md) installed the packaged extension, resolved the E compiled runtime and saved/reopened a blank MoCap Scene. | Actual Inno EXE execution, clean-machine transaction and full real-clip Blender workflow tests. The final staged internal EXE is unsigned; separate synthetic test signing does not sign it. |
| Distribution compliance | Component provenance, draft notices and corresponding-source delivery are prepared and internally gated. | Resolve remaining component permissions and owner/legal/commercial approvals before customer distribution. |
| Security and signing | ARAP T2 source hardening and pre-execution sidecar checks passed focused tests; a synthetic Inno EXE passed isolated test-certificate signing and tamper checks. | Production publisher identity/certificate, commercial Inno license and signed clean-machine validation. |
| Documentation | [Maintainer architecture](maintainer_architecture.md), [customer guide](studiofree_customer_guide.md) and [support guide](studiofree_support.md) are drafted. | Owner review of customer wording, support channel, terms and final build-specific requirements. |

The next concrete milestone is **actual test-signed installer execution in a disposable
Windows environment** (none was available in the 2026-11-20 checkpoint), followed by clean-machine Blender 5.2 installation,
activation and an end-to-end project on fresh footage: Create workspace →
choose video → Analyze → manage Characters → assign Candidates → Update
Tracking → Generate Animation → edit/cleanup → Export/Retarget. Use a
disposable project; preserve the owner QA fixture. Then run proportionate
final regression and release acceptance. Do not repeat completed compilation,
licensing or runtime-consolidation research without a concrete defect.

The [approved release strategy](vta_release_strategy.md) predates accepted
prototypes. Final storefront/customer terms, signing identity, distribution
permissions and clean-machine QA remain open. Product name/trademark and final
support channel require owner approval. Historical research reports record
their own checkpoint and are not current release-status assertions.
