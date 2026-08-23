param(
    [string] $GameCopyDir = "reference-local\stage-4-game-copy",
    [string] $DatabaseDirectory = "reference-local",
    [string] $Database = "",
    [string] $RuntimeFile = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"),
    [string] $SteamRoot = "C:\Program Files (x86)\Steam",
    [string] $NativeSaveDir = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\saves"),
    [string] $PairedSaveDir = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-paired-saves"),
    [ValidateSet("enabled", "disabled")]
    [string] $Thinking = "disabled",
    [string] $Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
Set-Location $projectRoot

$gameCopy = (Resolve-Path $GameCopyDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $gameCopy "VA-11 Hall A.exe") -PathType Leaf)) {
    throw "The isolated game copy does not contain VA-11 Hall A.exe: $gameCopy"
}

$databaseRoot = if ([System.IO.Path]::IsPathRooted($DatabaseDirectory)) {
    [System.IO.Path]::GetFullPath($DatabaseDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DatabaseDirectory))
}
[System.IO.Directory]::CreateDirectory($databaseRoot) | Out-Null
$databasePath = if ([string]::IsNullOrWhiteSpace($Database)) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Join-Path $databaseRoot "stage-11-deepseek-real-acceptance-$timestamp.sqlite3"
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
