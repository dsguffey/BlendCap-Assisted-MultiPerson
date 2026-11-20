# StudioFree MoCap maintainer map

This describes the Public 1.0 development architecture as of 2026-11-20. It
explains ownership and failure boundaries; the linked source remains the
precise contract. The private checkout maintains its own `docs/maintainer_engine.md`.

## The five different things called a “Scene” or “project”

| Thing | Authority and lifetime |
| --- | --- |
| `.blend` file | Blender owns file save/open. An adjacent `_vta` companion holds private canonical project state and evidence. Neither licensing nor installer lifecycle lives in a `.blend`. |
| Blender Scene | Blender owns scene, strips, rigs, Actions, animation, markers, and its custom properties. A Scene is a host view of one MoCap source, not a second project database. |
| StudioFree project | Private `project_state.json` in the companion owns source, Character identity, review, tracking, and accepted-generation records. Several Blender Scenes can reference one project. |
| StudioFree Scene membership | `vta_scene_characters` is a host-side visibility/binding record for which project Character IDs appear in a particular Blender Scene. It does not create a second Character registry. |
| MoCap Mode | Workspace presentation and interaction state. Entering it does not load a `.blend`, create a project, or change the project's authority. |

`blendcap/vta_scene_session.py:load` is the explicit host Scene binding
transaction used by **Load MoCap Scene** (`BLENDCAP_OT_vta_load_scene` in
`blendcap/vta_review_ui.py`), and by entry to MoCap Mode for a previously
remembered Scene. It validates ownership, source movie, project/source
identity, workspace layout and active operation state before changing the
window Scene. It then configures the dedicated workspace, binds missing Scene
Character membership, and refreshes review if analysis exists. A failure
restores the prior host context. `enter` calls `load`; `exit_mode` only turns
off presentation and clears review state.

On `.blend` open, `blendcap/vta_scene_reconcile.py:file_opened` schedules a
one-shot reconciliation. `vta_review_ui._reset_scene_presentation` drops
Python-only caches; it does not choose a Scene. The observer timer notices
context changes and refreshes presentation, but must not bind a Scene or
rewrite camera/editor ownership. An ordinary Blender Scene switch therefore
does not silently become a StudioFree Load MoCap Scene action.

The reconciliation planner is the Blender-independent
`blendcap_vta_scene_reconcile.py:plan`; the host adapter gathers surviving
Scenes, membership history, adoption receipts and native Character IDs, then
uses guarded private `review-management` mutations for confidently orphaned
Characters. Unknown schema, ambiguous ownership, pending work, and protected
native data preserve identities. Deleted-Scene cleanup runs on file open and
explicit load; no continuous destructive timer is intended. Names are never
used as identity or as deletion proof. `vta_character_bindings.py` records
accepted host adoption, and `vta_scene_characters.py` filters Scene display.

## Public/private command boundary

`blendcap_vta_engine.py:EngineClient` starts one private process per request;
the private engine emits a ready handshake, reads one bounded stdin JSON
request to EOF, and emits one bounded JSON response. The neutral `vta_protocol`
copy exists in both repositories under CC0 with byte-hash parity; protocol
changes require matched adapter and engine deployment. Its control envelope
is 16 KiB. Larger review/job/motion results use fixed companion-root artifact
locations, opaque IDs, size limits and SHA-256 checks; a request never supplies
an arbitrary executable/provider path. Public code does not import private
domain or provider implementation. The engine does not import Blender or GPL
adapter modules (`vta_engine.py:HostImportGuard`).

`blendcap_vta_runtime.py` reads the setup-owned `runtime.json`; compiled
deployments resolve `engine/vta_engine.exe` from that configuration. A saved
legacy Core preference cannot redirect a compiled release. `vta_protocol`
validates operations, identities and bounded replies, while
`blendcap_vta_engine_review.py` converts neutral private review artifacts into
host presentation. Keep native Blender objects out of the wire and the private
state model. Do not add a parallel direct Python import as a shortcut.

## Review, timeline, and native data

`vta_review_ui.refresh` and `blendcap_vta_review.py:present_review` turn
private observations into UI rows. UI selection, marks, viewport scroll and
cached overlays are transient. Review keys, assignments and tracking authority
come from the private project; `vta_review_undo.py` coordinates Blender's
native Undo with private receipts rather than exposing general engine Undo.
`vta_project_save.py` prepares the companion and validates save transitions;
preserve the owner-authored `.blend` plus `_vta` pair when handling QA data.

The MoCap Dope Sheet canvas is in `blendcap/vta_workspace.py` with shared row
geometry in `blendcap_vta_workspace.py`. One fractional `_row_scroll` anchor
positions Character labels, keys, empty-key rows and Depth children together.
The left full-height MoCap elevator, wheel over names, and vertical MMB pan
adjust that same anchor. Horizontal MMB delegates to Blender `view2d.pan`,
keeping keys, playhead and markers on Blender's time projection. The native
right Dope Sheet scrollbar still belongs to Blender and must not be treated
as a second MoCap row scrollbar. See
`docs/research/vta_dopesheet_navigation_20261110.md` for the input-routing
reason. Selection/hit testing must use the same clipped row geometry as draw.

Scene `timeline_markers` are the only marker data. The bridge in
`blendcap/vta_timeline_markers.py` draws and routes gestures because the custom
canvas covers the native marker region; it must not store duplicate markers.
Blender owns marker save/Undo and marker fields. Depth child keys are native
rig location data (`vta_depth_keys.py`); neither those nor generated Actions
are reconstructed from review-row presentation.

## Generation and recovery

`blendcap_vta_jobs.py` and `blendcap_vta_generation_ipc.py` prepare and observe
durable private jobs. The private engine validates source/cache/revision and
emits a bounded output plan; the public adapter applies validated results to
Blender rigs/Actions through `vta_generation.py`. An accepted engine artifact
is separate from native application acknowledgement and from saving a `.blend`.
Do not claim private result acceptance proves the Action was saved. Protected
Restore coordinates previously accepted results; native edits and Blender
Undo remain Blender-owned.

## Packaging, signing and security assumptions

`build_release.ps1` is the release entry point: matched committed public and
private source, tests, Nuitka build/probes, offline component staging, manifest
and compliance checks, Inno compilation, then one offline ZIP. Pinned portable
control/body/analysis runtimes and models are immutable staged inputs; release
source and component inventories are not inferred from a developer venv.
`tools/studiofree_release.py` records exact source commits and file hashes.
`tools/studiofree_compliance.py` enforces separately cleared distribution
evidence. `tools/package_distribution.py` requires a trusted production
publisher signature and exact certificate thumbprint.

The Inno EXE embeds the expected `package.json` SHA-256 and its verifier. It
copies the external sidecar, then authenticates manifest and every declared
member **before** starting sidecar Python; uninstall authenticates again.
`tools/studiofree_maintenance.py` manages immutable versioned releases,
setup-owned runtime/current pointers, repair, rollback and uninstall. A
failed stage or extension switch should retain the previous active release.
Entitlement in DPAPI, logs and owner project files remain outside versioned
releases and survive uninstall. The internal-test AppId/install/data roots are
separate. See `docs/studiofree_installer_lifecycle.md`,
`docs/research/studiofree_arap_t2_security_20261116.md` and
`docs/research/studiofree_test_signed_installer_20261120.md`.

The compiled private engine enforces licensing before protected operations;
public UI status/key entry is only an adapter. Test certificates, internal
component inventories and a disposable Gumroad Product ID do not satisfy
commercial release approval. Signature trust authenticates the publisher of
the EXE, while the EXE's embedded digest authenticates the external payload.
The ZIP receipt and a bare hash manifest are integrity records, not publisher
identity. Native same-user tampering after installation and a malicious OS are
outside this design's guarantee; repair requires a known-good installer.

## Change checklist

When changing the protocol, update both CC0 copies and parity tests. When
changing Character ownership, examine project state, Scene membership,
binding receipts, native IDs and reconciliation before removing data. When
changing timeline geometry, keep draw, clipping, hit testing and all scroll
inputs on one row model. When changing accepted generation, distinguish
private authority from native Action application and Save. When changing
release contents, update source/component inventories, notices, compliance
and authentication tests before creating a customer package.
