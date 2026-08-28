param(
    [Parameter(Mandatory = $true)] [string] $SteamGameDir,
    [Parameter(Mandatory = $true)] [string] $GameCopyDir,
    [Parameter(Mandatory = $true)] [string] $DataDelta,
    [string] $BackupDir = (Join-Path $PSScriptRoot "backups"),
    [string] $Record = (Join-Path $BackupDir "install.json"),
    [string] $Runtime = "python",
    [bool] $RuntimeIsPython = $true,
    [string] $InstanceId = ([guid]::NewGuid().ToString("N")),
    [switch] $KeepBuildFiles
)

$ErrorActionPreference = "Stop"
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$steamDir = (Resolve-Path $SteamGameDir).Path
$dataDeltaPath = (Resolve-Path $DataDelta).Path
$original = Join-Path $steamDir "data.win"
$destinationRoot = [IO.Path]::GetFullPath($GameCopyDir)
$destination = Join-Path $destinationRoot "data.win"

function Invoke-OpenShift([string[]] $Arguments) {
    if ($RuntimeIsPython) {
        & $Runtime -m open_shift @Arguments
    } else {
        & $Runtime @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "Open Shift command failed with code $LASTEXITCODE" }
}

function Get-Sha256Hex([string] $Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}

function Test-ReparsePoint([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Remove-InstanceItem([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # Never recurse through a junction/symlink while replacing an instance.
    if (Test-ReparsePoint $Path) {
        Remove-Item -LiteralPath $Path -Force
    } else {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Clear-InstanceRoot([string] $Root) {
    if (-not (Test-Path -LiteralPath $Root)) {
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        return
    }
    if (Test-ReparsePoint $Root) {
        throw "GameCopyDir must be a real directory, not a junction or symbolic link: $Root"
    }
    Get-ChildItem -LiteralPath $Root -Force | ForEach-Object {
        Remove-InstanceItem $_.FullName
    }
}

function New-InstanceLink([string] $Source, [string] $Destination, [ValidateSet("file", "directory")] [string] $Kind) {
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Steam game is missing required $Kind for the isolated instance: $Source"
    }
    Remove-InstanceItem $Destination
    $linkType = ""
    try {
        if ($Kind -eq "directory") {
            # Junctions work on supported Windows installations without requiring
            # Developer Mode and keep the large original resource directories out
            # of every instance.
            New-Item -ItemType Junction -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
            $linkType = "junction"
        } else {
            # Hard links avoid requiring Developer Mode when both paths share
            # an NTFS volume. Cross-volume installs use a symbolic link. If
            # both link mechanisms are unavailable, copy this one immutable
            # executable/DLL only; never copy the whole Steam directory.
            try {
                New-Item -ItemType HardLink -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
                $linkType = "hard_link"
            } catch {
                try {
                    New-Item -ItemType SymbolicLink -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
                    $linkType = "symbolic_link"
                } catch {
                    Copy-Item -LiteralPath $Source -Destination $Destination -Force -ErrorAction Stop
                    $linkType = "copied_file"
                }
            }
        }
    } catch {
        throw "Could not prepare the required $Kind file '$Destination' from '$Source'. Link creation and the safe single-file copy both failed; refusing to copy the full Steam game. $($_.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $Destination)) {
        throw "The required $Kind link was not created: $Destination"
    }
    return $linkType
}

function Write-LinkManifest([string] $Path, [string] $Root, [string] $SteamRoot, [string] $Id, [object[]] $Links) {
    $manifest = [ordered]@{
        schema_version = 1
        instance_id = $Id
        game_copy_dir = [IO.Path]::GetFullPath($Root)
        steam_game_dir = [IO.Path]::GetFullPath($SteamRoot)
        data_win = "patched-copy"
        links = @($Links)
    }
    [IO.File]::WriteAllText($Path, ($manifest | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
}

if (-not (Test-Path -LiteralPath $original -PathType Leaf)) { throw "SteamGameDir does not contain data.win" }
if (-not (Test-Path -LiteralPath $dataDeltaPath -PathType Leaf)) { throw "data.win delta was not found" }
if ([IO.Path]::GetFullPath($steamDir) -eq [IO.Path]::GetFullPath($destinationRoot)) { throw "GameCopyDir must differ from SteamGameDir" }

$env:PYTHONPATH = Join-Path $packageRoot "src"
Invoke-OpenShift @("validate-patch-target", "--data-win", $original, "--manifest", (Join-Path $packageRoot "game-patch\manifest.json"))

$buildDir = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
try {
    $patched = Join-Path $buildDir "patched.data.win"
    Invoke-OpenShift @("apply-data-delta", "--original-data-win", $original, "--delta", $dataDeltaPath, "--output", $patched)
    # The delta application already verifies both input and output hashes.
    # Player packages intentionally omit the development GML tree; the full
    # source-tree verification remains a build-time check.
    # Do not replace an instance until the new patch has compiled and verified.
    # The game executable and immutable resources are linked back to Steam; only
    # data.win is a physical, patched instance copy. Never fall back to copying
    # the entire Steam directory when link creation is unavailable.
    New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
    Clear-InstanceRoot $destinationRoot
    Copy-Item -LiteralPath $original -Destination $destination -Force
    $links = New-Object Collections.ArrayList
    $linkedFiles = @("VA-11 Hall A.exe", "D3DX9_43.dll", "GMFile.dll", "GMIni.dll", "GMResource.dll", "GMXML.dll", "steam_api.dll")
    foreach ($name in $linkedFiles) {
        $source = Join-Path $steamDir $name
        $target = Join-Path $destinationRoot $name
        $linkType = New-InstanceLink $source $target "file"
        [void] $links.Add([ordered]@{ path = $name; source = $source; type = $linkType })
    }
    $linkedDirectories = @("answer", "config", "scripts", "sounds")
    foreach ($name in $linkedDirectories) {
        $source = Join-Path $steamDir $name
        $target = Join-Path $destinationRoot $name
        $linkType = New-InstanceLink $source $target "directory"
        [void] $links.Add([ordered]@{ path = $name; source = $source; type = $linkType })
    }
    # The game may update this file; keep it instance-local instead of linking a
    # writable file into the user's Steam installation.
    $options = Join-Path $steamDir "options.ini"
    if (Test-Path -LiteralPath $options -PathType Leaf) {
        Copy-Item -LiteralPath $options -Destination (Join-Path $destinationRoot "options.ini") -Force
    }
    $linkManifest = Join-Path $destinationRoot "open-shift-links.json"
    Write-LinkManifest $linkManifest $destinationRoot $steamDir $InstanceId @($links)
    $env:PYTHONPATH = Join-Path $packageRoot "src"
    $reuseVerifiedPatch = $false
    if ((Test-Path -LiteralPath $Record -PathType Leaf) -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
        try {
            $priorRecord = Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json
            $destinationHash = Get-Sha256Hex $destination
            $patchedHash = Get-Sha256Hex $patched
            $reuseVerifiedPatch = $destinationHash -eq $patchedHash -and $destinationHash -eq ([string] $priorRecord.installed_sha256).ToLowerInvariant()
        } catch { $reuseVerifiedPatch = $false }
    }
    if ($reuseVerifiedPatch) {
        Write-Host "Existing isolated data.win already matches the newly verified patch output."
    } else {
        Invoke-OpenShift @("install-patch", "--original-data-win", $original, "--patched-data-win", $patched, "--destination-data-win", $destination, "--backup-dir", $BackupDir, "--record", $Record, "--manifest", (Join-Path $packageRoot "game-patch\manifest.json"))
    }
} finally {
    if (-not $KeepBuildFiles) { Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue }
}
Write-Host "Open Shift isolated copy installed at $destinationRoot"
