<#
Embedded in the installer EXE. Run after external files are copied and before
starting any executable or script from the sidecar. The manifest digest is
compiled into the installer; a digest carried only by the ZIP is not trusted.
#>
param(
    [string]$Payload,
    [ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedManifestSha256,
    [string]$InstallTarget
)
$ErrorActionPreference = 'Stop'

function Fail([string]$Message) { throw "StudioFree package authentication: $Message" }
if ($InstallTarget) {
    $target = [System.IO.Path]::GetFullPath($InstallTarget).TrimEnd('\')
    $current = $target
    while ($current) {
        if ([System.IO.File]::Exists($current) -or [System.IO.Directory]::Exists($current)) {
            if (([System.IO.File]::GetAttributes($current) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Fail 'installation target has a linked ancestor'
            }
        }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
    if ([System.IO.Directory]::Exists($target)) {
        $pending = New-Object 'System.Collections.Generic.Stack[string]'
        $pending.Push($target)
        while ($pending.Count -gt 0) {
            foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($pending.Pop())) {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    Fail 'installation target contains a linked entry'
                }
                if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                    $pending.Push($entry)
                }
            }
        }
    }
    exit 0
}
if (-not $Payload -or -not $ExpectedManifestSha256) { Fail 'payload and installer digest are required' }
function Sha256([string]$Path) {
    $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'Read')
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

$root = [System.IO.Path]::GetFullPath($Payload).TrimEnd('\')
if (-not [System.IO.Directory]::Exists($root)) { Fail 'payload directory is missing' }
if (([System.IO.File]::GetAttributes($root) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail 'payload directory is linked'
}
$manifestPath = [System.IO.Path]::Combine($root, 'package.json')
if (-not [System.IO.File]::Exists($manifestPath)) { Fail 'manifest is missing' }
if (([System.IO.File]::GetAttributes($manifestPath) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail 'manifest is linked'
}
if ((Sha256 $manifestPath) -ne $ExpectedManifestSha256.ToLowerInvariant()) {
    Fail 'manifest does not match this installer'
}
if (([System.IO.FileInfo]$manifestPath).Length -gt 32000000) { Fail 'manifest is oversized' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema -ne 'studiofree-package' -or $manifest.version -ne 1 -or
    $null -eq $manifest.files -or $manifest.files.Count -lt 1 -or $manifest.files.Count -gt 100000) {
    Fail 'manifest structure is invalid'
}
$expected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
foreach ($row in $manifest.files) {
    if ($row.path -isnot [string] -or $row.path.Length -gt 512 -or
        $row.path -notmatch '^[^\\/:*?"<>|]+(?:/[^\\/:*?"<>|]+)*$' -or
        $row.path.Split('/') -contains '..' -or $row.path.Split('/') -contains '.' -or
        $row.path -ceq 'package.json' -or $row.bytes -isnot [long] -and $row.bytes -isnot [int] -or
        $row.bytes -lt 0 -or $row.sha256 -cnotmatch '^[0-9a-f]{64}$') {
        Fail 'manifest contains an unsafe member'
    }
    if (-not $expected.Add($row.path)) { Fail 'manifest has duplicate members' }
    $path = $root
    foreach ($part in $row.path.Split('/')) {
        if ($part.EndsWith(' ') -or $part.EndsWith('.') -or
            $part -match '^(?i:(?:con|prn|aux|nul|com[1-9]|lpt[1-9]))(?:\.|$)') {
            Fail 'manifest contains a Windows-ambiguous member'
        }
        $path = [System.IO.Path]::Combine($path, $part)
        if (([System.IO.File]::GetAttributes($path) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail 'payload contains a linked member'
        }
    }
    if (-not [System.IO.File]::Exists($path)) { Fail 'declared member is missing' }
    if (([System.IO.FileInfo]$path).Length -ne $row.bytes -or (Sha256 $path) -ne $row.sha256) {
        Fail 'declared member has changed'
    }
}

$pending = New-Object 'System.Collections.Generic.Stack[string]'
$pending.Push($root)
while ($pending.Count -gt 0) {
    $directory = $pending.Pop()
    foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
        $attributes = [System.IO.File]::GetAttributes($entry)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail 'payload contains a linked entry'
        }
        if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
            $pending.Push($entry)
        } else {
            $relative = $entry.Substring($root.Length + 1).Replace('\', '/')
            if ($relative -cne 'package.json' -and -not $expected.Contains($relative)) {
                Fail 'payload contains an undeclared file'
            }
        }
    }
}
