param(
    [string] $GameCopyDir = "",
    [string] $DatabaseDirectory = "reference-local",
    [string] $Database = "",
    [string] $RuntimeFile = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"),
    [string] $SteamRoot = "C:\Program Files (x86)\Steam",
    [string] $NativeSaveDir = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\saves"),
    [string] $PairedSaveDir = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-paired-saves"),
    [string] $ExpectedPatchedDataWinSha256 = "",
    [ValidateSet("enabled", "disabled")]
    [string] $Thinking = "disabled",
    [string] $Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
Set-Location $projectRoot

function Get-Sha256Hex([string] $Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}
function Read-InstallRecords {
    $root = Join-Path $projectRoot "reference-local"
    foreach ($record in Get-ChildItem -LiteralPath $root -Recurse -Filter "install.json" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "stage-.*-acceptance-build" }) {
        try {
            $value = Get-Content -LiteralPath $record.FullName -Raw | ConvertFrom-Json
            if ($value.installed_data_win -and $value.installed_sha256) {
                [PSCustomObject]@{
                    DataWin = [IO.Path]::GetFullPath([string] $value.installed_data_win)
                    Sha256 = ([string] $value.installed_sha256).ToLowerInvariant()
                    InstalledAt = [datetime] $value.installed_at_utc
                    Record = $record.FullName
                }
            }
        }
        catch { }
    }
}
function Resolve-AcceptanceGameCopy([string] $RequestedPath, [string] $RequestedHash) {
    $records = @(Read-InstallRecords | Sort-Object InstalledAt -Descending)
    $latestRecord = $records | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $path = (Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop).Path
        $hash = if (-not [string]::IsNullOrWhiteSpace($RequestedHash)) { $RequestedHash.ToLowerInvariant() } elseif ($latestRecord) { $latestRecord.Sha256 } else { "" }
        if ([string]::IsNullOrWhiteSpace($hash)) {
            throw "No current install.json hash record was found. Rebuild the current acceptance copy or pass -ExpectedPatchedDataWinSha256 explicitly."
        }
        return [PSCustomObject]@{ Path = $path; ExpectedHash = $hash }
    }
    if (-not $latestRecord) {
        throw "No acceptance install.json record was found. Rebuild the current acceptance copy or pass -GameCopyDir and -ExpectedPatchedDataWinSha256."
    }
    return [PSCustomObject]@{
        Path = [IO.Path]::GetDirectoryName($latestRecord.DataWin)
        ExpectedHash = $latestRecord.Sha256
    }
}

$resolvedCopy = Resolve-AcceptanceGameCopy $GameCopyDir $ExpectedPatchedDataWinSha256
$gameCopy = $resolvedCopy.Path
$expectedPatchedDataWinSha256 = $resolvedCopy.ExpectedHash
$dataWin = Join-Path $gameCopy "data.win"
if (-not (Test-Path -LiteralPath (Join-Path $gameCopy "VA-11 Hall A.exe") -PathType Leaf)) {
    throw "The isolated game copy does not contain VA-11 Hall A.exe: $gameCopy"
}
if (-not (Test-Path -LiteralPath $dataWin -PathType Leaf)) {
    throw "The isolated game copy does not contain data.win: $gameCopy"
}
$actualPatchedDataWinSha256 = Get-Sha256Hex $dataWin
if ($actualPatchedDataWinSha256 -ne $expectedPatchedDataWinSha256) {
    throw "Refusing to start a stale or unsupported game copy.`nPath: $gameCopy`nActual data.win SHA256: $actualPatchedDataWinSha256`nExpected current SHA256: $expectedPatchedDataWinSha256`nRebuild the current acceptance copy or pass the correct -GameCopyDir."
}
Write-Host "Using latest verified acceptance game copy: $gameCopy"
Write-Host "Verified data.win SHA256: $actualPatchedDataWinSha256"

$databaseRoot = if ([System.IO.Path]::IsPathRooted($DatabaseDirectory)) {
    [System.IO.Path]::GetFullPath($DatabaseDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DatabaseDirectory))
}
[System.IO.Directory]::CreateDirectory($databaseRoot) | Out-Null
$databasePath = if ([string]::IsNullOrWhiteSpace($Database)) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Join-Path $databaseRoot "open-shift-acceptance-$timestamp.sqlite3"
}
elseif ([System.IO.Path]::IsPathRooted($Database)) {
    [System.IO.Path]::GetFullPath($Database)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Database))
}
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($databasePath)) | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot "src"

if ([string]::IsNullOrWhiteSpace($env:OPEN_SHIFT_API_KEY)) {
    $secureKey = Read-Host "DeepSeek API Key (input is hidden)" -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:OPEN_SHIFT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}

try {
    Write-Host "Checking the DeepSeek connection..."
    & $Python -m open_shift probe-provider `
        --base-url "https://api.deepseek.com" `
        --model "deepseek-v4-flash" `
        --protocol chat_completions `
        --response-format json_object `
        --thinking $Thinking `
        --timeout 120
    if ($LASTEXITCODE -ne 0) {
        throw "DeepSeek probe failed. The game was not started."
    }

    Write-Host "Preparing the local day skeleton before opening the game."
    Write-Host "Acceptance database: $databasePath"
    & $Python -m open_shift launch `
        --db $databasePath `
        --runtime-file $RuntimeFile `
        --game-cwd $gameCopy `
        --game-command "VA-11 Hall A.exe" `
        --steam-root $SteamRoot `
        --steam-app-id 447530 `
        --native-save-dir $NativeSaveDir `
        --paired-save-dir $PairedSaveDir `
        --provider-base-url "https://api.deepseek.com" `
        --provider-model "deepseek-v4-flash" `
        --provider-protocol chat_completions `
        --provider-response-format json_object `
        --provider-thinking $Thinking `
        --provider-timeout 120 `
        --provider-required `
        --prepare-before-game `
        --prepare-timeout 900
    if ($LASTEXITCODE -ne 0) {
        throw "Open Shift acceptance launch failed. See the diagnostic above."
    }
}
finally {
    Remove-Item Env:OPEN_SHIFT_API_KEY -ErrorAction SilentlyContinue
}
