param(
    [string] $SteamGameDir,
    [string] $UtmtCli,
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA "OpenShift"),
    [string] $GameCopyDir = (Join-Path $env:LOCALAPPDATA "OpenShift\game"),
    [string] $ApiKeyEnv = "OPEN_SHIFT_API_KEY",
    [string] $CompletionMarker,
    [switch] $SkipShortcut,
    [switch] $SkipCredential
)

$ErrorActionPreference = "Stop"
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($CompletionMarker) {
    Remove-Item -LiteralPath $CompletionMarker -Force -ErrorAction SilentlyContinue
}

function Write-Utf8NoBom([string] $Path, [string] $Content) {
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function Find-SteamGameDir {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common\VA-11 HALL-A"),
        (Join-Path $env:ProgramFiles "Steam\steamapps\common\VA-11 HALL-A"),
        "C:\Steam\steamapps\common\VA-11 HALL-A"
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "data.win") -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Find-UtmtCli {
    $bundled = Join-Path $packageRoot "tools\utmt\UndertaleModCli.exe"
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { return $bundled }
    $bundledZip = Join-Path $packageRoot "tools\utmt\UndertaleModCli.zip"
    if (Test-Path -LiteralPath $bundledZip -PathType Leaf) {
        $expanded = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-utmt-" + [guid]::NewGuid().ToString("N"))
        Expand-Archive -LiteralPath $bundledZip -DestinationPath $expanded -Force
        $cli = Get-ChildItem -LiteralPath $expanded -Filter "UndertaleModCli.exe" -Recurse -File | Select-Object -First 1
        if ($cli) { return $cli.FullName }
    }
    $commands = Get-Command utmt-cli, UndertaleModTool.CLI, UndertaleModTool -ErrorAction SilentlyContinue
    if ($commands) { return $commands[0].Source }
    return $null
}

function Find-SteamRoot {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Steam"),
        (Join-Path $env:ProgramFiles "Steam"),
        (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $SteamGameDir)))
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "Steam2.dll") -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw "Steam root containing Steam2.dll was not found"
}

if (-not $SteamGameDir) { $SteamGameDir = Find-SteamGameDir }
if (-not $SteamGameDir) {
    $SteamGameDir = Read-Host "Steam VA-11 HALL-A folder (contains data.win)"
}
if (-not $UtmtCli) { $UtmtCli = Find-UtmtCli }
if (-not $UtmtCli) {
    $UtmtCli = Read-Host "Path to UndertaleModTool CLI 0.9.1.2"
}
if (-not (Test-Path -LiteralPath $SteamGameDir -PathType Container)) { throw "Steam game folder was not found: $SteamGameDir" }
if (-not (Test-Path -LiteralPath $UtmtCli -PathType Leaf)) { throw "UTMT CLI was not found: $UtmtCli" }
$bundledRuntime = Join-Path $packageRoot "OpenShift.exe"
if (Test-Path -LiteralPath $bundledRuntime -PathType Leaf) {
    $runtimePath = $bundledRuntime
    $runtimeIsPython = $false
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw "OpenShift.exe or Python 3.11+ was not found" }
    $runtimePath = $pythonCommand.Source
    $runtimeIsPython = $true
}
$steamRoot = Find-SteamRoot

$installRoot = [IO.Path]::GetFullPath($InstallDir)
$copyRoot = [IO.Path]::GetFullPath($GameCopyDir)
if ($copyRoot -eq [IO.Path]::GetFullPath($SteamGameDir)) { throw "GameCopyDir must differ from the Steam game folder" }
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
$sameRoot = $packageRoot.TrimEnd('\') -eq $installRoot.TrimEnd('\')
if (-not $sameRoot) {
    if (Test-Path -LiteralPath (Join-Path $packageRoot "src")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "src") -Destination $installRoot -Recurse -Force
    }
    if (-not $runtimeIsPython) {
        Copy-Item -LiteralPath $bundledRuntime -Destination (Join-Path $installRoot "OpenShift.exe") -Force
    }
    $bundledGui = Join-Path $packageRoot "OpenShiftSetup.exe"
    if (Test-Path -LiteralPath $bundledGui -PathType Leaf) {
        Copy-Item -LiteralPath $bundledGui -Destination (Join-Path $installRoot "OpenShiftSetup.exe") -Force
    }
    $bundledIcon = Join-Path $packageRoot "OpenShift.ico"
    if (Test-Path -LiteralPath $bundledIcon -PathType Leaf) {
        Copy-Item -LiteralPath $bundledIcon -Destination (Join-Path $installRoot "OpenShift.ico") -Force
    }
    foreach ($webViewName in @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")) {
        $webViewPath = Join-Path $packageRoot $webViewName
        if (Test-Path -LiteralPath $webViewPath -PathType Leaf) {
            Copy-Item -LiteralPath $webViewPath -Destination (Join-Path $installRoot $webViewName) -Force
        }
    }
    Copy-Item -LiteralPath (Join-Path $packageRoot "game-patch") -Destination $installRoot -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $packageRoot "packaging") -Destination $installRoot -Recurse -Force
    if (Test-Path -LiteralPath (Join-Path $packageRoot "assets")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "assets") -Destination $installRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath (Join-Path $packageRoot "tools")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "tools") -Destination $installRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath (Join-Path $packageRoot "pyproject.toml")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "pyproject.toml") -Destination $installRoot -Force
    }
}
if (-not $runtimeIsPython) {
    $runtimePath = Join-Path $installRoot "OpenShift.exe"
}

$env:PYTHONPATH = Join-Path $installRoot "src"
if ($runtimeIsPython) {
    & $runtimePath -m open_shift validate-patch-target --data-win (Join-Path $SteamGameDir "data.win") --manifest (Join-Path $installRoot "game-patch\manifest.json")
} else {
    & $runtimePath validate-patch-target --data-win (Join-Path $SteamGameDir "data.win") --manifest (Join-Path $installRoot "game-patch\manifest.json")
}
if ($LASTEXITCODE -ne 0) { throw "Steam data.win did not match a supported original hash" }

$patchRecord = Join-Path $installRoot "backups\install.json"
$alreadyInstalled = $false
if ((Test-Path -LiteralPath $patchRecord -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $copyRoot "data.win") -PathType Leaf)) {
    try {
        $priorPatch = Get-Content -LiteralPath $patchRecord -Raw | ConvertFrom-Json
        $currentHash = (Get-FileHash -LiteralPath (Join-Path $copyRoot "data.win") -Algorithm SHA256).Hash.ToLowerInvariant()
        $alreadyInstalled = $currentHash -eq ([string] $priorPatch.installed_sha256).ToLowerInvariant()
    } catch {
        $alreadyInstalled = $false
    }
}
if ($alreadyInstalled) {
    Write-Host "Existing verified Open Shift patch found; continuing setup without rebuilding data.win."
} else {
    & (Join-Path $installRoot "packaging\install-isolated-copy.ps1") `
        -SteamGameDir $SteamGameDir `
        -GameCopyDir $copyRoot `
        -UtmtCli $UtmtCli `
        -BackupDir (Join-Path $installRoot "backups") `
        -Record $patchRecord `
        -Runtime $runtimePath `
        -RuntimeIsPython $runtimeIsPython
    if ($LASTEXITCODE -ne 0) { throw "Open Shift isolated copy installation failed" }
}

$configDir = Join-Path $env:LOCALAPPDATA "VA_11_Hall_A"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$config = Join-Path $configDir "open-shift.toml"
if (-not (Test-Path -LiteralPath $config)) {
    $configText = @"
[provider]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
protocol = "chat_completions"
response_format = "json_object"
api_key_env = "$ApiKeyEnv"
timeout_seconds = 30
max_calls = 100000
thinking = "disabled"

[world]
prefetch_days = 1
"@
    Write-Utf8NoBom $config $configText
}

$state = [ordered]@{
    schema_version = 1
    install_dir = $installRoot
    game_copy_dir = $copyRoot
    steam_game_dir = [IO.Path]::GetFullPath($SteamGameDir)
    steam_root = $steamRoot
    config = $config
    api_key_env = $ApiKeyEnv
    runtime = $runtimePath
    runtime_is_python = $runtimeIsPython
    bridge_command = if ($runtimeIsPython) { @($runtimePath, "-m", "open_shift", "serve-bridge") } else { @($runtimePath, "serve-bridge") }
    installed_at_utc = [DateTime]::UtcNow.ToString("o")
}
Write-Utf8NoBom (Join-Path $installRoot "install.json") ($state | ConvertTo-Json)

if (-not $SkipCredential) {
    & (Join-Path $installRoot "packaging\configure-api-key.ps1") -InstallDir $installRoot
}

if (-not $SkipShortcut) {
    $launcher = Join-Path $installRoot "Start-Open-Shift.ps1"
    $launcherText = @"
`$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
`$root = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$state = Get-Content -LiteralPath (Join-Path `$root "install.json") -Raw | ConvertFrom-Json
`$secretFile = Join-Path `$root "api-key.dpapi"
if (-not (Test-Path -LiteralPath `$secretFile)) { throw "API key is not configured. Run packaging\configure-api-key.ps1." }
`$protected = [IO.File]::ReadAllBytes(`$secretFile)
`$bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(`$protected, `$null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
try { Set-Item -Path "Env:$ApiKeyEnv" -Value ([Text.Encoding]::UTF8.GetString(`$bytes)) } finally { [Array]::Clear(`$bytes, 0, `$bytes.Length) }
`$arguments = @("launch", "--config", `$state.config, "--db", (Join-Path `$env:LOCALAPPDATA "VA_11_Hall_A\open-shift.sqlite3"), "--runtime-file", (Join-Path `$env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"), "--game-cwd", `$state.game_copy_dir, "--game-command", "VA-11 Hall A.exe", "--steam-root", `$state.steam_root, "--steam-app-id", "447530", "--provider-required", "--prepare-before-game", "--bridge-command") + @(`$state.bridge_command)
if (`$state.runtime_is_python) { `$env:PYTHONPATH = Join-Path `$root "src"; & `$state.runtime -m open_shift @arguments } else { & `$state.runtime @arguments }
exit `$LASTEXITCODE
"@
    Write-Utf8NoBom $launcher $launcherText
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcut = Join-Path $desktop "Open Shift.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $installedGui = Join-Path $installRoot "OpenShiftSetup.exe"
    if (Test-Path -LiteralPath $installedGui -PathType Leaf) {
        $link.TargetPath = $installedGui
        $link.Arguments = ""
        $link.IconLocation = "$installedGui,0"
    } else {
        $link.TargetPath = "powershell.exe"
        $link.Arguments = "-ExecutionPolicy Bypass -File `"$launcher`""
    }
    $link.WorkingDirectory = $installRoot
    $link.Description = "Start VA-11 HALL-A Open Shift"
    $link.Save()
}

Write-Host "Open Shift installed. Start it with: $installRoot\Start-Open-Shift.ps1"
Write-Host "Steam original was not modified; the patched copy is: $copyRoot"
if ($CompletionMarker) {
    $markerPath = [IO.Path]::GetFullPath($CompletionMarker)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $markerPath) | Out-Null
    Write-Utf8NoBom $markerPath ([DateTime]::UtcNow.ToString("o"))
}
exit 0
