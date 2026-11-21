# StudioFree MoCap 1.0 — VirtualBox clean-machine installer validation

Date: 2026-11-20. Branch: `codex/mixamo-retarget-v1`.

## Isolation and inputs

This is an internal engineering test, not a release approval. The dedicated
`StudioFree-Win11-QA` VM uses Oracle VirtualBox 7.2.22, EFI, TPM 2.0, 4 vCPUs,
8 GiB RAM, a 150 GiB dynamic disk, NAT, and no shared folders, clipboard, or
drag-and-drop. It runs headlessly. The owner desktop, installed Blender profile,
entitlement store, and QA project are outside the test.

The Windows input is Microsoft's Windows 11 Enterprise Evaluation 26H2 ISO,
8,225,329,152 bytes, SHA-256
`bc3f24086ebadc94489066b5ad78089e2cf5c3491e90e790bb81a2b199c10e38`,
matching Microsoft's published checksum. The Blender input actually used for
the acceptance test is the official portable Blender 5.2.4 x64 ZIP (405,779,142
bytes, SHA-256
`bbd78f8931dc940444da4ff2983374930f9f5c092ae0e330588e9760bb9d967c`),
matching Blender's published checksum. Neither is stored in Git.

The complete internal E distribution is the previously validated
`StudioFreeMoCap-1.0.0-dev.m5-INTERNAL-TEST-b309afe.zip` (10,579,681,752 bytes,
SHA-256 `4a8f8448b5d6969e70893d80854583151497da68073a90294e66968ebc8c4b86`).
Its payload was built at public commit `b309afe`; prior validation covered its
35,750 payload entries and full archive integrity. That full scan was not
repeated on the host. The signed EXE tested here is a **separate current-source
Inno compile**, because pre-execution sidecar hardening landed after the E ZIP.
It uses the E `package.json` SHA-256
`5c3fe5f9c5bbc7a0fdf7a369c93f4a847f6baa258f818ed0504c26684f3da7e5`
as its embedded identity and is signed with a disposable file-only test
certificate, SHA-1 thumbprint
`7af0e28c1762558666ddf55e42daa9ee04f2afe9`. The signed EXE SHA-256 is
`85cf8654c668de227a865239cb3ecf697d29d9eb46afd09499fcd6e9190efddd`.
The guest replaces the original unsigned E EXE after extracting the archive;
the E ZIP distribution receipt does not authenticate this replacement EXE.
File-only Authenticode and SHA-256 checks are separate evidence. The disposable
signer and key are not trusted by the host or stored in Git. Production
publisher verification still rejects the test identity. This combined test
does **not** prove that the later maintenance implementation is in the E payload.

## VM setup finding

Windows 11 Enterprise Evaluation build 26300.9457 installed and Guest Additions
7.2.22 became available after a setup-phase install. A clean powered-off
Windows baseline snapshot was taken before Blender and StudioFree. The official
portable Blender ZIP was hash-checked in the guest, extracted into
`C:\StudioFreeQA\blender-portable`, and its `blender.exe --version` reported
Blender 5.2.4 LTS. The official MSI failed while writing a guest user registry
key; testing proceeded with the portable ZIP as authorized. That VM test build
temporarily accepted the portable executable path through
`STUDIOFREE_INTERNAL_BLENDER_EXE`. The override was subsequently removed; the
owner-QA build uses the normal Blender picker and default path.

## Execution results

- The guest verified the internal distribution ZIP's SHA-256, extracted it,
  checked `package.json` against the embedded package identity, and checked
  the replacement test-signed Inno EXE's SHA-256 and signer thumbprint.
- The actual Inno EXE launched offline with the portable Blender path. It
  copied the payload into the guest's per-user installation directory.
- At its registry step, Inno reported `RegCreateKeyEx failed; code 5. Access is
  denied` for
  `HKEY_CURRENT_USER\Software\StudioFree\MoCap-InternalTest` / `InstallerPython`.
  In silent mode it aborted and rolled the installation back. The install
  directory no longer existed afterward. This is a guest environment blocker,
  consistent with the earlier official Blender MSI registry error.
- The Inno post-install maintenance command, packaged runtime loading, Blender
  extension registration, and licensing smoke **were not reached**. Thus this
  run does not satisfy the clean-machine installer acceptance criterion.
- The initial PowerShell wrapper printed an empty exit code because Windows
  launched the GUI installer asynchronously; the Inno log and observed
  rollback establish the actual result. The EXE was not run a second time.

## Limits and disposition

The VM has no NVIDIA CUDA device, so it cannot repeat host GPU inference.
The test stopped at the registry permission failure as directed; no additional
VM remediation, MSI troubleshooting, repair, update, or rollback test was run.
Final owner Blender GUI/animation-quality acceptance, production signing,
commercial redistribution permissions, and an independently rebuilt current
full payload remain separate gates.
