<#
One entry point for a traceable StudioFree MoCap Windows release build.
The private source is authoritative. This script never reads owner QA files or
the current entitlement, and only writes ignored .build outputs.
#>
param(
    [Parameter(Mandatory = $true)][string]$BuildId,
    [Parameter(Mandatory = $true)][string]$GumroadProductId,
    [Parameter(Mandatory = $true)][string]$Components,
    [Parameter(Mandatory = $true)][string]$FFmpeg4Source,
    [Parameter(Mandatory = $true)][string]$FFmpeg5Source,
    [Parameter(Mandatory = $true)][string]$OpenCV4Archive,
    [Parameter(Mandatory = $true)][string]$OpenCV5Archive,
    [Parameter(Mandatory = $true)][string]$OpenH264Source,
    [string]$Blender = 'C:\Design\Programs\Blender5.2\blender.exe',
    [string]$Python = 'C:\Users\dsguf\AppData\Local\Python\bin\python.exe',
    [string]$NuitkaPython = '',
    [string]$ProbePython = '',
    [string]$Iscc = '',
    [string]$SignToolName = '',
    [string]$SignToolCommand = '',
    [switch]$TestProduct,
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
$public = $PSScriptRoot
$private = Join-Path (Split-Path $public) 'Video-to-Animation'
$disposable = 'u7OwneEbsZJHqF51iv8DwQ=='
if (-not $TestProduct -and $GumroadProductId -eq $disposable) {
    throw 'The disposable Gumroad product is forbidden in a production build. Use -TestProduct for a clearly marked test installer.'
}
if (-not $TestProduct -and $BuildId -match '(test|probe|dev)' ) {
    throw 'Production builds require a non-test build ID.'
}
if ([bool]$SignToolName -ne [bool]$SignToolCommand) {
    throw 'Provide both -SignToolName and -SignToolCommand to enable Inno Setup signing.'
}
if (-not $NuitkaPython) {
    $NuitkaPython = Join-Path $private '.build\nuitka-tooling\Scripts\python.exe'
}
if (-not $Iscc) {
    $Iscc = Join-Path $public '.build\tools\inno-portable\ISCC.exe'
}
$python = (Resolve-Path -LiteralPath $Python).Path
$nuitka = (Resolve-Path -LiteralPath $NuitkaPython).Path
$probePython = if ($ProbePython) { (Resolve-Path -LiteralPath $ProbePython).Path } else { $nuitka }
& $probePython -c 'import cv2, numpy'
if ($LASTEXITCODE -ne 0) { throw 'Compiled worker-flow probes require -ProbePython with cv2 and numpy.' }
$blenderExe = (Resolve-Path -LiteralPath $Blender).Path
$componentsFile = (Resolve-Path -LiteralPath $Components).Path
$ffmpeg4Source = (Resolve-Path -LiteralPath $FFmpeg4Source).Path
$ffmpeg5Source = (Resolve-Path -LiteralPath $FFmpeg5Source).Path
$opencv4Archive = (Resolve-Path -LiteralPath $OpenCV4Archive).Path
$opencv5Archive = (Resolve-Path -LiteralPath $OpenCV5Archive).Path
$openh264Source = (Resolve-Path -LiteralPath $OpenH264Source).Path
$testTemp = New-Item -ItemType Directory -Force -Path (Join-Path $public '.build\test-temp')
$env:TEMP = $testTemp.FullName
$env:TMP = $testTemp.FullName

Push-Location $public
try {
    $productVersion = (& $python -c 'from vta_protocol.runtime import PRODUCT_VERSION; print(PRODUCT_VERSION)').Trim()
    if (-not $TestProduct -and $productVersion -match 'dev') {
        throw 'The canonical product version is still a development version; use -TestProduct or prepare a real release version.'
    }
    & $python -m unittest tests.test_studiofree_release_lifecycle tests.test_vta_packaging_foundation -q
    if ($LASTEXITCODE -ne 0) { throw 'Public source tests failed.' }
} finally { Pop-Location }
Push-Location $private
try {
    & $python -m unittest tests.test_compiled_release_identity tests.test_license_prototype -q
    if ($LASTEXITCODE -ne 0) { throw 'Private source tests failed.' }
    & (Join-Path $private 'tools\build_nuitka_standalone.ps1') -Python $nuitka -BuildId $BuildId -GumroadProductId $GumroadProductId
    if ($LASTEXITCODE -ne 0) { throw 'Private compilation failed.' }
} finally { Pop-Location }

$dist = Join-Path $private ('.build\prototype-' + $BuildId + '\vta_engine.dist')
$stage = Join-Path $public ('.build\release-' + $BuildId)
Push-Location $private
try {
    & $nuitka tools\probe_compiled_engine.py (Join-Path $dist 'vta_engine.exe') $public
    if ($LASTEXITCODE -ne 0) { throw 'Compiled engine probe failed.' }
    & $nuitka tools\probe_compiled_license.py (Join-Path $dist 'vta_engine.exe') $public $GumroadProductId
    if ($LASTEXITCODE -ne 0) { throw 'Compiled licensing probe failed.' }
    & $probePython tools\probe_compiled_flows.py (Join-Path $dist 'vta_engine.exe') $public $probePython $GumroadProductId
    if ($LASTEXITCODE -ne 0) { throw 'Compiled worker-flow probe failed.' }
} finally { Pop-Location }
Push-Location $public
try {
    $releaseArgs = @(
        '--dist', $dist, '--private', $private, '--blender', $blenderExe,
        '--output', $stage, '--components', $componentsFile,
        '--ffmpeg4-source', $ffmpeg4Source, '--ffmpeg5-source', $ffmpeg5Source,
        '--opencv4-archive', $opencv4Archive, '--opencv5-archive', $opencv5Archive,
        '--openh264-source', $openh264Source, '--complete'
    )
    & $python tools\studiofree_release.py @releaseArgs
    if ($LASTEXITCODE -ne 0) { throw 'Release staging failed.' }
    & $python tools\studiofree_release.py --verify (Join-Path $stage 'payload')
    if ($LASTEXITCODE -ne 0) { throw 'Staged package validation failed.' }
    & $python tools\probe_offline_components.py (Join-Path $stage 'payload')
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Python import closure failed.' }
    if ($SkipInstaller) {
        Write-Output "Verified release stage: $stage"
    } elseif (Test-Path -LiteralPath $Iscc) {
        $compiler = (Resolve-Path -LiteralPath $Iscc).Path
        $output = New-Item -ItemType Directory -Force -Path (Join-Path $stage 'installer')
        $innoArgs = @('/DProductVersion=' + $productVersion, '/O' + $output.FullName)
        if ($SignToolName) {
            $innoArgs += '/DSignToolName=' + $SignToolName
            $innoArgs += '/S' + $SignToolName + '=' + $SignToolCommand
        }
        $innoArgs += 'installer\studiofree_mocap.iss'
        & $compiler @innoArgs
        if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
        $installer = Get-ChildItem -LiteralPath $output.FullName -Filter '*.exe' -File |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        $distribution = Join-Path $stage ('StudioFreeMoCap-' + $productVersion + '-' + $BuildId + '.zip')
        & $python tools\package_distribution.py --installer $installer.FullName --payload (Join-Path $stage 'payload') --output $distribution
        if ($LASTEXITCODE -ne 0) { throw 'Offline distribution ZIP failed.' }
        $info = [ordered]@{
            schema = 'studiofree-installer-build'; version = 1
            product_version = $productVersion; build_id = $BuildId
            inno_version = (Get-Item -LiteralPath $compiler).VersionInfo.ProductVersion
            installer_sha256 = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            distribution_sha256 = (Get-FileHash -LiteralPath $distribution -Algorithm SHA256).Hash.ToLowerInvariant()
            signed = [bool]$SignToolName
        }
        $info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'installer-build-info.json') -Encoding utf8
        $signLabel = if ($SignToolName) { 'signed' } else { 'unsigned test' }
        Write-Output "Offline distribution ($signLabel): $distribution"
    } else {
        Write-Warning 'Inno Setup 7 compiler was not found. Verified stage is ready; installer compilation remains pending.'
    }
} finally { Pop-Location }
