# StudioFree MoCap owner-laptop internal installation QA

This is an **internal test package**, not a commercial release. It reuses the
previously verified 35,750-file E payload. Only the Blender adapter's engine
working-directory fix, the maintenance helper's path-hardening changes, their
manifest/source records, and the Inno EXE were refreshed. No private engine,
model or Python runtime was rebuilt. The Inno source retains its normal Blender
5.2 executable picker and per-user workflow. The temporary VirtualBox Blender
path override from commit `1d61ce3` was removed before this EXE was compiled.

## Package identity

- File: `.build/internal-validation/owner-qa/StudioFreeMoCap-1.0.0-dev.m5-OWNER-QA-CURRENT-INTERNAL-TEST.zip`
- Size: **10,579,515,374 bytes**
- SHA-256: `9a4cc00146444e0d80795edc1a30f5df2519287f3b53d4b390d7157a4126daa3`
- Installer EXE SHA-256 inside ZIP:
  `bbe2e4ed92cc325b1ab446ff25564cba57c4f903c88064c3193af2c21e7ed839`
- Payload `package.json` SHA-256 embedded in the EXE:
  `958999f5cc821896acb9f07c8501d10fa74346601634cf3fd988538f8081baa2`

The new ZIP passed a complete CRC extraction check, unique-entry/set checks,
unchanged payload entry size/CRC comparison to the verified E ZIP, hashes of
all refreshed entries, public corresponding-source comparison, and embedded
EXE and manifest SHA-256 checks. The test-signed EXE passed file-only Authenticode
verification against the exact disposable test certificate. The certificate is
self-signed, untrusted by Windows, **not timestamped**, and expires November
27, 2026. Its SHA-1 thumbprint is
`7AF0E28C1762558666DDF55E42DAA9EE04F2AFE9`. It is not a production
publisher identity. Do not import it into the
Windows trust store or disable security protections. If Windows blocks the EXE,
record the exact message and stop. The production publisher gate rejects this
test-signed EXE. This package retains the E private release/build ID and is for
a **clean internal-test installation**, not an update or repair of an older E
installation with that ID.

## Laptop installation

Use a separate Windows x64 laptop with Blender **5.2.x** already installed.
Close Blender. Keep the ZIP and extracted files together; the EXE reads its
`payload` directory beside it. No separate Python, AI dependency or model
installation is required.

In PowerShell, set `$zip` to the copied ZIP's local path, then:

```powershell
(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
$work = Join-Path $env:USERPROFILE 'Desktop\StudioFree-QA-Install'
New-Item -ItemType Directory -Path $work -Force | Out-Null
tar -xf $zip -C $work
$env:STUDIOFREE_INTERNAL_VALIDATION = '1'
$env:BLENDER_USER_CONFIG = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.2\config'
$env:BLENDER_USER_EXTENSIONS = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.2\extensions'
& (Join-Path $work 'StudioFreeMoCap-1.0.0-dev.m5-INTERNAL-TEST.exe') "/LOG=$work\studiofree-inno.log"
```

Compare the printed hash with the package identity above. The environment
variables are process-local **internal-test guards** and point Blender at its
ordinary user directories. They do not select a Blender executable. In the
normal installer wizard, select the installed Blender 5.2 `blender.exe` if the
default path is wrong. Do not elevate to administrator just to work around a
registry error. The installer uses separate `VTA-InternalTest` program/data
roots and will refuse an unmanaged existing `blendcap` extension.

## Basic success checks and logs

After the installer reports success, these should all be `True` in the same
PowerShell window:

```powershell
Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\VTA-InternalTest\install-record.json')
Test-Path (Join-Path $env:LOCALAPPDATA 'VTA-InternalTest\config\runtime.json')
Test-Path (Join-Path $env:BLENDER_USER_EXTENSIONS 'user_default\blendcap\blender_manifest.toml')
Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\VTA-InternalTest\releases\1.0.0-dev.m5-offline-internal-20261115-e\engine\vta_engine.exe')
```

To open the installed test extension in Blender, first run
`$env:VTA_RUNTIME_CONFIG = Join-Path $env:LOCALAPPDATA 'VTA-InternalTest\config\runtime.json'`, then start the
selected Blender executable **from that PowerShell window**. A normal Start
menu launch does not inherit this test-only runtime location. Checking the
StudioFree panel and `Resources → Licensing` status is sufficient for the
initial smoke; a full footage/animation workflow is separate QA. Keep the
license key private.

If setup fails, retain `$work\studiofree-inno.log` and
`$env:LOCALAPPDATA\VTA-InternalTest\logs\maintenance.jsonl` if present. Record
the on-screen error and whether the four basic checks above passed. Do not send
license keys, entitlement files or owner projects with the logs.

The VirtualBox attempt reached Inno's registry step and rolled back with access
denied for `HKEY_CURRENT_USER\Software\StudioFree\MoCap-InternalTest`; that guest
failure does not establish laptop success or a StudioFree defect. No further VM
troubleshooting is part of this handoff.
