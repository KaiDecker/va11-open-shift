param(
    [Parameter(Mandatory = $true)] [string] $SteamGameDir,
    [Parameter(Mandatory = $true)] [string] $GameCopyDir,
    [Parameter(Mandatory = $true)] [string] $UtmtCli,
    [string] $BackupDir = (Join-Path $PSScriptRoot "backups"),
    [string] $Record = (Join-Path $BackupDir "install.json"),
    [string] $Runtime = "python",
    [bool] $RuntimeIsPython = $true,
    [switch] $KeepBuildFiles
)

$ErrorActionPreference = "Stop"
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$steamDir = (Resolve-Path $SteamGameDir).Path
$utmt = (Resolve-Path $UtmtCli).Path
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

if (-not (Test-Path -LiteralPath $original -PathType Leaf)) { throw "SteamGameDir does not contain data.win" }
if (-not (Test-Path -LiteralPath $utmt -PathType Leaf)) { throw "UtmtCli was not found" }
if ([IO.Path]::GetFullPath($steamDir) -eq [IO.Path]::GetFullPath($destinationRoot)) { throw "GameCopyDir must differ from SteamGameDir" }

$env:PYTHONPATH = Join-Path $packageRoot "src"
Invoke-OpenShift @("validate-patch-target", "--data-win", $original, "--manifest", (Join-Path $packageRoot "game-patch\manifest.json"))

New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
Get-ChildItem -LiteralPath $steamDir -Force | Where-Object { $_.Name -ne "data.win" } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $destinationRoot -Recurse -Force
}
Copy-Item -LiteralPath $original -Destination $destination -Force
$buildDir = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
try {
    $input = Join-Path $buildDir "original.data.win"
    $patched = Join-Path $buildDir "patched.data.win"
    Copy-Item -LiteralPath $original -Destination $input
    & $utmt load $input -s (Join-Path $packageRoot "game-patch\apply_mod.csx") -o $patched -v
    if ($LASTEXITCODE -ne 0) { throw "UTMT patching failed with code $LASTEXITCODE" }
    Invoke-OpenShift @("verify-patch-output", "--original-data-win", $original, "--patched-data-win", $patched, "--manifest", (Join-Path $packageRoot "game-patch\manifest.json"), "--gml-source-dir", (Join-Path $packageRoot "game-patch\gml"))
    $env:PYTHONPATH = Join-Path $packageRoot "src"
    Invoke-OpenShift @("install-patch", "--original-data-win", $original, "--patched-data-win", $patched, "--destination-data-win", $destination, "--backup-dir", $BackupDir, "--record", $Record, "--manifest", (Join-Path $packageRoot "game-patch\manifest.json"))
} finally {
    if (-not $KeepBuildFiles) { Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue }
}
Write-Host "Open Shift isolated copy installed at $destinationRoot"
