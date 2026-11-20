# StudioFree MoCap test-signed installer checkpoint — 2026-11-20

## Scope and trust boundary

This checkpoint validates the signing and pre-execution authentication design
with a **disposable internal-test identity**. It does not approve customer
distribution or establish Windows publisher reputation. The test certificates
and private keys were generated under ignored `.build/test-signing/`, outside
Git and the distribution ZIP. The PFX files and password file were deleted
after validation; public PEMs and signed synthetic EXEs remain ignored test
artifacts. Neither certificate was installed in `CurrentUser\Root`,
`CurrentUser\My`, or `LocalMachine\Root` (all checked after the probe).

The local fixture used two 3072-bit RSA, SHA-256, code-signing self-signed
certificates, with subjects explicitly marked `DISPOSABLE TEST SIGNER` and
`NOT FOR RELEASE`. They expire on 2026-11-27. The expected certificate's
SHA-1 thumbprint was `779EBEC19545B5763CBAC733677C6EDEFC3A851F`; the
unexpected certificate's was `CC93CEE5563D8551B9C1E4F6C6282B30323D5A81`.
These values identify the local test only and must never become production
publisher approvals. Never attach test keys to an issue or release.

The test used Microsoft Windows SDK BuildTools 10.0.28000.2957 SignTool
(`bin/10.0.28000.0/x64/signtool.exe`) from the official NuGet package,
Inno Setup 7.1.1, and `osslsigncode` 2.15. The latter's official Windows
release ZIP had SHA-256
`95c661bcaf35bdbf11e56da1d2801c1dd7f5bc67ac406b2103981faf9487bb30`.
These tools are local test dependencies, not shipping payload components.

## Results

| Check | Result |
| --- | --- |
| Small Inno internal-test EXE signed through its `SignTool` interface | Passed. The release-script JSON/subprocess argument transport was also exercised with this fixture. |
| Full Authenticode verification with a file-only test root and expected leaf SHA-1 | Passed with `osslsigncode verify -CAfile <test-root.pem> -require-leaf-hash sha1:<expected-thumbprint> -in <installer.exe>`. No Windows trust-store import. |
| Unexpected signer | A second Inno EXE signed by the other test certificate passed under its own file-only root and failed under the expected root/thumbprint. |
| Altered installer | A byte-modified signed EXE failed isolated verification. |
| Altered sidecar manifest, altered executable, missing executable, undeclared executable, path escape | Rejected by the embedded-verifier fixture tests. The manifest digest in the EXE is the authority. |
| Linked files and target path | The focused tests exist, but four link-related cases skipped because this sandbox could not create the required links. No acceptance is claimed for those cases in this run. |
| Pre-execution ordering | Source inspection of `installer/studiofree_mocap.iss`: `CurStepChanged(ssPostInstall)` runs embedded `verify_sidecar.ps1` and raises on failure **before** `control/python.exe`; `InitializeUninstall` authenticates the installed verifier and payload **before** its Python maintenance call. Actual EXE execution was not performed. |
| Customer publisher gate | `tools/package_distribution.py` rejected the test-signed EXE even when passed its real test thumbprint, because normal Windows Authenticode status is not `Valid` for the untrusted test root. Both production checks also explicitly reject a self-signed publisher leaf, even if someone later trusts this test root locally. |
| Trusted publisher control | The same packager verifier accepted an OS-trusted Microsoft-signed executable with its actual thumbprint and rejected a different thumbprint. It launches Windows PowerShell without the inherited PowerShell 7 module path, which otherwise broke Security-module autoload in this build environment. |
| Customer compliance gate | Focused release/compliance tests passed; an uncleared component or missing compliance bundle still cannot become a customer package. |

The focused security, compliance, and lifecycle suite passed **30 tests with
four skips**. The compiled synthetic installer hash after the release-script
argument-transport fix was
`3f71712e9432967db0b0c5bba5d1d722941bd13616e52f891197191994883d46`.
The entire 15.6 GB payload was not rebuilt, copied, or rehashed for this
fixture. The test EXE was **not executed**: no existing disposable Windows VM
was found, and an isolated directory on the owner's account is insufficient.
The owner's Blender profile, license state, and QA files were untouched.

## Release-script correction and production path

`build_release.ps1` now passes Inno's documented `--signtool` option through
Python `subprocess` using a short-lived JSON argument file. Windows PowerShell
had split the quoted sign command into multiple compiler arguments. The
argument file is removed in a `finally` block. The same script accepts
`-TestTrustPem` plus `-TestSignatureVerifier` only for a signed
`-TestProduct -InternalValidation` build with a 40-hex expected test
thumbprint. That path checks the signer's reported thumbprint and verifies the
complete Authenticode signature against a file-only root. It does not put the
test certificate into Windows trust stores.

The **production** path retains `Get-AuthenticodeSignature.Status -eq Valid`
plus exact expected publisher thumbprint, and now explicitly rejects a
self-signed publisher leaf. The customer packager independently repeats those
requirements. The test-only PEM options
are rejected for a production build. The full `build_release.ps1` flow was not
rerun because it would rebuild and verify the complete runtime; the small
Inno compiler/signing invocation and isolated verifier were exercised directly.

## Remaining release work

Acquire the production publisher certificate and signing service/process;
record and securely approve its exact certificate thumbprint; timestamp the
installer; use a commercially licensed Inno compiler; verify the signed
full-payload installer and uninstall/update/repair behavior in a disposable
Windows environment with Blender 5.2.x. Confirm genuine Windows trusted
Authenticode status and expected publisher on a clean machine. The separate
redistribution/compliance approvals remain required. No release gate was
lowered by this test.

Sources: [Microsoft SDK BuildTools](https://www.nuget.org/packages/Microsoft.Windows.SDK.BuildTools),
[Inno SignTool documentation](https://jrsoftware.org/inno-setup/documentation/topic_setup_signtool.htm),
[osslsigncode verification options](https://github.com/mtrojnar/osslsigncode/blob/master/osslsigncode.md).
