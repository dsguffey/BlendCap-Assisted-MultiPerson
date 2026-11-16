# StudioFree MoCap 1.0 ARAP T2 security pass — 2026-11-16

**Disposition: implemented with remaining verification.** This is a focused
source and disposable-fixture pass, not a commercial release approval. The
customer payload remains behind the existing redistribution/owner gate.

## Threat model and boundaries

The public Blender adapter accepts project/video paths and sends one bounded,
versioned JSON request to the separately compiled private engine. The engine
owns project mutation, inference and entitlement checks. The offline ZIP carries
an Inno EXE plus a large external sidecar. The EXE, compiled from release source,
is the publisher trust anchor; `package.json` alone is only a corruption check.
The install/data roots, Blender profile and user projects are separate. Attackers
may supply malformed project/media/IPC data, replace the ZIP sidecar, edit paths
or manifests, or put links in an install tree. A malicious process already
running as the same Windows user can modify per-user application and entitlement
files and is outside the achievable trust boundary of this release.

## Findings and disposition

| Severity | Finding and evidence | Remediation |
|---|---|
| High | The external sidecar could supply `control/python.exe` and its own hash manifest; Inno started that Python before a publisher-anchored check (`installer/studiofree_mocap.iss`, `tools/studiofree_maintenance.py`). | The installer now embeds the expected `package.json` SHA-256 and a verifier script. After copy and **before** starting sidecar code, the script checks that manifest digest and every declared file's size/hash, rejects links and undeclared files. It also runs before uninstaller-side Python. Production builds require Authenticode signing and check the expected publisher certificate thumbprint. |
| Medium | Inno reused a previous app directory and recursively deleted `{app}\payload`; a changed app path or linked ancestor could point deletion outside the intended tree. | `UsePreviousAppDir=no` fixes the target; an embedded pre-install check rejects reparse-point ancestors before `[InstallDelete]`. Same-user race attacks remain outside the practical boundary. |
| Medium | Verification of retained releases allowed arbitrary extra files, including executable Python code, while rollback/repair could subsequently run those releases. | Installed-release verification now rejects unlisted files except preserved `.blend` owner documents; linked members fail. Staged packages remain exact-inventory only. `remove_owned` validates all listed targets before deleting any. |
| Medium | Private engine and worker processes inherited Blender's working directory, permitting avoidable current-directory DLL/module search from a project location. | Public client and private job/provider/camera launchers now set the child working directory to the configured executable's directory. Existing isolated Python flags remain. |
| Low | Maintenance error logs can include bounded local paths or provider diagnostic text useful to support; they contain no keys in the reviewed activation path. | Retain bounded diagnostics. Do not collect or upload logs automatically. |

## Authenticity and release behavior

`build_release.ps1` computes the staged manifest digest and compiles it into the
Inno EXE. The embedded PowerShell verifier is itself part of that EXE. At setup
time the verifier runs from Inno's extracted temporary copy, after external
files are copied but before their Python/runtime code executes. During uninstall
the installed verifier must first match its compiled-in digest, then checks the
sidecar against the compiled-in manifest digest before Python is started.
`tools/studiofree_maintenance.py` independently rechecks the payload and the
private release identity before staging or activation. The signed installer EXE
and the verified publisher certificate are the **production** trust anchor;
neither the ZIP's own receipt nor its hash manifest authenticates the publisher.

Internal-validation installers can compile unsigned, are marked INTERNAL TEST,
and use separate AppId/install/data roots. A production build and the direct
customer ZIP packager require a signing tool, a 40-hex expected publisher
certificate thumbprint, successful Inno signing and a locally trusted
Authenticode result. The product owner must obtain
the commercial signing certificate/process (and the separately identified Inno
commercial license), record its expected thumbprint in release instructions,
and confirm timestamping and signature validation on a clean Windows machine.
No test certificate is represented as commercial publisher identity.

The deliberate full-installer update can select a signed older release; explicit
rollback still verifies the retained release's bytes before activation. There
is no monotonic release counter or online updater. A policy that forbids all
older signed releases would conflict with the accepted rollback design; this
remains a product/release policy decision, not an observed bypass of the current
publisher boundary. Per-user tampering after installation is not prevented by
Authenticode; recovery uses a known-good signed installer/repair.

## Evidence and limits

- Disposable sidecar tests: authentic tiny package accepted; changed manifest,
  changed executable, undeclared code, traversal, and linked member rejected.
  Reparse-link tests skip where sandbox privileges deny link creation.
- Public protocol/client and lifecycle focused suites: 46 tests passed (six
  skips); private jobs/license/external-runtime suites: 43 passed. The most
  recent focused sidecar/lifecycle rerun passed 25 tests (four skips). These include
  malformed/oversized JSON, request and response checks, unlicensed gate,
  synthetic update/rollback/repair/uninstall and DPAPI fixtures.
- Inno 7.1.1 compiled the internal-test script with the embedded verifier and
  manifest anchor. No installer EXE was executed in this pass. No owner Blender,
  profile, entitlement, `QA.blend` or `QA_vta` was touched.
- The 15.6 GB payload was not copied, rebuilt or rehashed for this small source
  correction. One new full-payload installer validation is warranted later
  because pre-execution authentication changes the install transaction. Do it
  once alongside signed clean-machine release QA, not repeatedly now.
- Third-party code, license and commercial-release restrictions remain governed
  by the separate compliance records; this pass did not change those decisions.

## Remaining release work

No unresolved critical/high *source finding* is identified in this bounded
pass. Production certificate/expected thumbprint, a commercially licensed Inno
compiler, one signed real-payload installer/update/uninstall exercise, and clean
machine verification remain release gates. They are not covered by synthetic
source tests. Customer distribution remains prohibited until those and the
separate compliance/owner gates are satisfied.
