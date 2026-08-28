param(
    [string] $SteamGameDir,
    [string] $InstallDir = "",
    [string] $GameCopyDir = "",
    [string] $ApiKeyEnv = "OPEN_SHIFT_API_KEY",
    [string] $CompletionMarker,
    [switch] $SkipShortcut,
    [switch] $SkipCredential
)

$ErrorActionPreference = "Stop"
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packageManifest = Join-Path $packageRoot "PACKAGE_MANIFEST.json"
$packageVersion = "development"
if (Test-Path -LiteralPath $packageManifest -PathType Leaf) {
    try { $packageVersion = [string] ((Get-Content -LiteralPath $packageManifest -Raw | ConvertFrom-Json).package_version) } catch { }
}
if ([string]::IsNullOrWhiteSpace($packageVersion)) { $packageVersion = "development" }
$safeVersion = [regex]::Replace($packageVersion, '[^A-Za-z0-9._-]', '-').Trim('-','.')
if ([string]::IsNullOrWhiteSpace($safeVersion)) { $safeVersion = "development" }
$existingStatePath = Join-Path $packageRoot "install.json"
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $reuseInstalledRoot = $false
    if (Test-Path -LiteralPath $existingStatePath -PathType Leaf) {
        try { $reuseInstalledRoot = ([string] ((Get-Content -LiteralPath $existingStatePath -Raw | ConvertFrom-Json).package_version) -eq $packageVersion) } catch { }
    }
    $InstallDir = if ($reuseInstalledRoot) { $packageRoot } else { Join-Path $env:LOCALAPPDATA ("OpenShift-" + $safeVersion) }
}
if ([string]::IsNullOrWhiteSpace($GameCopyDir)) { $GameCopyDir = Join-Path $InstallDir "game" }
if ($CompletionMarker) {
    Remove-Item -LiteralPath $CompletionMarker -Force -ErrorAction SilentlyContinue
}

function Write-Utf8NoBom([string] $Path, [string] $Content) {
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function Get-Sha256Hex([string] $Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}

function Add-SteamRoot([Collections.Generic.HashSet[string]] $Roots, [string] $Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return }
    try { [void] $Roots.Add([IO.Path]::GetFullPath($Candidate.Trim().TrimEnd('\'))) } catch { }
}

function Get-SteamRoots {
    $roots = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    Add-SteamRoot $roots (Join-Path ${env:ProgramFiles(x86)} "Steam")
    Add-SteamRoot $roots (Join-Path $env:ProgramFiles "Steam")
    Add-SteamRoot $roots "C:\Steam"
    foreach ($registryPath in @("HKCU:\Software\Valve\Steam", "HKLM:\Software\WOW6432Node\Valve\Steam")) {
        try {
            $steam = Get-ItemProperty -LiteralPath $registryPath -ErrorAction Stop
            Add-SteamRoot $roots ([string] $(if ($steam.SteamPath) { $steam.SteamPath } else { $steam.InstallPath }))
        } catch { }
    }
    foreach ($steamRoot in @($roots)) {
        $vdf = Join-Path $steamRoot "steamapps\libraryfolders.vdf"
        if (-not (Test-Path -LiteralPath $vdf -PathType Leaf)) { continue }
        try {
            $content = Get-Content -LiteralPath $vdf -Raw
            foreach ($match in [regex]::Matches($content, '"path"\s+"([^"]+)"')) {
                Add-SteamRoot $roots $match.Groups[1].Value.Replace('\\', '\')
            }
            foreach ($match in [regex]::Matches($content, '"\d+"\s+"([^"]+)"')) {
                Add-SteamRoot $roots $match.Groups[1].Value.Replace('\\', '\')
            }
        } catch { }
    }
    return @($roots)
}

function Find-SteamGameDir {
    foreach ($steamRoot in (Get-SteamRoots)) {
        $candidate = Join-Path $steamRoot "steamapps\common\VA-11 HALL-A"
        if (Test-Path -LiteralPath (Join-Path $candidate "data.win") -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Find-SteamRoot {
    $candidates = @(Get-SteamRoots) + @((Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $SteamGameDir))))
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath (Join-Path $candidate "Steam2.dll") -PathType Leaf) -or
            (Test-Path -LiteralPath (Join-Path $candidate "steam.exe") -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw "Steam root containing Steam2.dll was not found"
}

function Get-PatchFingerprint([string] $Root) {
    $files = @(
        (Join-Path $Root "game-patch\manifest.json")
    )
    $delta = Join-Path $Root "patch\data-win.delta"
    if (Test-Path -LiteralPath $delta -PathType Leaf) { $files += $delta }
    $hashText = (($files | Sort-Object | ForEach-Object { Get-Sha256Hex $_ }) -join "`n")
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($hashText)))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

if (-not $SteamGameDir) { $SteamGameDir = Find-SteamGameDir }
if (-not $SteamGameDir) {
    $SteamGameDir = Read-Host "Steam VA-11 HALL-A folder (contains data.win)"
}
if (-not (Test-Path -LiteralPath $SteamGameDir -PathType Container)) { throw "Steam game folder was not found: $SteamGameDir" }
$dataDelta = Join-Path $packageRoot "patch\data-win.delta"
if (-not (Test-Path -LiteralPath $dataDelta -PathType Leaf)) { throw "The player package is missing patch\data-win.delta" }
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
$patchFingerprint = Get-PatchFingerprint $packageRoot
$installRoot = [IO.Path]::GetFullPath($InstallDir)
$copyRoot = [IO.Path]::GetFullPath($GameCopyDir)
if ($copyRoot -eq [IO.Path]::GetFullPath($SteamGameDir)) { throw "GameCopyDir must differ from the Steam game folder" }
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
$sameRoot = $packageRoot.TrimEnd('\') -eq $installRoot.TrimEnd('\')
if (-not $sameRoot) {
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
    foreach ($webViewName in @("WebView2Loader.dll")) {
        $webViewPath = Join-Path $packageRoot $webViewName
        if (Test-Path -LiteralPath $webViewPath -PathType Leaf) {
            Copy-Item -LiteralPath $webViewPath -Destination (Join-Path $installRoot $webViewName) -Force
        }
    }
    $bundledManifest = Join-Path $packageRoot "game-patch\manifest.json"
    New-Item -ItemType Directory -Force -Path (Join-Path $installRoot "game-patch") | Out-Null
    Copy-Item -LiteralPath $bundledManifest -Destination (Join-Path $installRoot "game-patch\manifest.json") -Force
    Copy-Item -LiteralPath (Join-Path $packageRoot "packaging") -Destination $installRoot -Recurse -Force
    if (Test-Path -LiteralPath (Join-Path $packageRoot "assets")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "assets") -Destination $installRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath (Join-Path $packageRoot "patch")) {
        Copy-Item -LiteralPath (Join-Path $packageRoot "patch") -Destination $installRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $packageManifest -PathType Leaf) {
        Copy-Item -LiteralPath $packageManifest -Destination $installRoot -Force
    }
}
if (-not $runtimeIsPython) {
    $runtimePath = Join-Path $installRoot "OpenShift.exe"
}
$installedIcon = Join-Path $installRoot ("OpenShift-" + $patchFingerprint.Substring(0, 12) + ".ico")
$bundledIcon = Join-Path $packageRoot "OpenShift.ico"
if ((Test-Path -LiteralPath $bundledIcon -PathType Leaf) -and
    ([IO.Path]::GetFullPath($bundledIcon) -ne [IO.Path]::GetFullPath($installedIcon))) {
    Copy-Item -LiteralPath $bundledIcon -Destination $installedIcon -Force
}

$env:PYTHONPATH = Join-Path $installRoot "src"
if ($runtimeIsPython) {
    & $runtimePath -m open_shift validate-patch-target --data-win (Join-Path $SteamGameDir "data.win") --manifest (Join-Path $installRoot "game-patch\manifest.json")
} else {
    & $runtimePath validate-patch-target --data-win (Join-Path $SteamGameDir "data.win") --manifest (Join-Path $installRoot "game-patch\manifest.json")
}
if ($LASTEXITCODE -ne 0) { throw "Steam data.win did not match a supported original hash" }

$patchRecord = Join-Path $installRoot "backups\install.json"
$linkManifest = Join-Path $copyRoot "open-shift-links.json"
$instanceId = [guid]::NewGuid().ToString("N")
$alreadyInstalled = $false
if ((Test-Path -LiteralPath $patchRecord -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $copyRoot "data.win") -PathType Leaf)) {
    try {
        $priorPatch = Get-Content -LiteralPath $patchRecord -Raw | ConvertFrom-Json
        $priorStatePath = Join-Path $installRoot "install.json"
        $priorState = if (Test-Path -LiteralPath $priorStatePath -PathType Leaf) { Get-Content -LiteralPath $priorStatePath -Raw | ConvertFrom-Json } else { $null }
        $currentHash = Get-Sha256Hex (Join-Path $copyRoot "data.win")
        $linksReady = $false
        if (Test-Path -LiteralPath $linkManifest -PathType Leaf) {
            try {
                $linkState = Get-Content -LiteralPath $linkManifest -Raw | ConvertFrom-Json
                $linksReady = ([string] $linkState.data_win -eq "patched-copy") -and
                    ([string] $linkState.steam_game_dir).TrimEnd('\') -ieq ([IO.Path]::GetFullPath($SteamGameDir)).TrimEnd('\') -and
                    @($linkState.links).Count -gt 0 -and
                    (@($linkState.links) | ForEach-Object { Test-Path -LiteralPath (Join-Path $copyRoot ([string] $_.path)) }).Count -eq @($linkState.links).Count
                if ($linksReady) { $instanceId = [string] $linkState.instance_id }
            } catch { $linksReady = $false }
        }
        $alreadyInstalled = $currentHash -eq ([string] $priorPatch.installed_sha256).ToLowerInvariant() -and
            $null -ne $priorState -and
            ([string] $priorState.patch_fingerprint).ToLowerInvariant() -eq $patchFingerprint -and
            ([string] $priorState.game_copy_mode) -eq "patched_data_win_plus_steam_links" -and
            $linksReady
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
        -DataDelta (Join-Path $installRoot "patch\data-win.delta") `
        -BackupDir (Join-Path $installRoot "backups") `
        -Record $patchRecord `
        -Runtime $runtimePath `
        -RuntimeIsPython $runtimeIsPython `
        -InstanceId $instanceId
    if ($LASTEXITCODE -ne 0) { throw "Open Shift isolated copy installation failed" }
}

$linkEntries = @()
if (Test-Path -LiteralPath $linkManifest -PathType Leaf) {
    try {
        $linkDocument = Get-Content -LiteralPath $linkManifest -Raw | ConvertFrom-Json
        $linkEntries = @($linkDocument.links)
        if ([string]::IsNullOrWhiteSpace($instanceId) -and $linkDocument.instance_id) {
            $instanceId = [string] $linkDocument.instance_id
        }
    } catch {
        throw "The isolated instance link manifest was invalid: $linkManifest"
    }
}
if ($linkEntries.Count -eq 0) { throw "The isolated instance was created without Steam resource links: $copyRoot" }

$configDir = $installRoot
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
prefetch_days = 0
"@
    Write-Utf8NoBom $config $configText
}

# Keep the legacy switch for compatibility, but new installs never create a
# desktop shortcut. Launchers are started from the installation directory.
$shortcutPath = ""
$state = [ordered]@{
    schema_version = 1
    instance_id = $instanceId
    package_version = $packageVersion
    patch_fingerprint = $patchFingerprint
    install_dir = $installRoot
    game_copy_dir = $copyRoot
    game_copy_mode = "patched_data_win_plus_steam_links"
    link_manifest = $linkManifest
    linked_entries = $linkEntries
    steam_game_dir = [IO.Path]::GetFullPath($SteamGameDir)
    steam_root = $steamRoot
    config = $config
    database = (Join-Path $installRoot "open-shift.sqlite3")
    api_key_env = $ApiKeyEnv
    runtime = $runtimePath
    runtime_is_python = $runtimeIsPython
    bridge_command = if ($runtimeIsPython) { @($runtimePath, "-m", "open_shift", "serve-bridge") } else { @($runtimePath, "serve-bridge") }
    shortcut_path = $shortcutPath
    installed_at_utc = [DateTime]::UtcNow.ToString("o")
}
Write-Utf8NoBom (Join-Path $installRoot "install.json") ($state | ConvertTo-Json)

if (-not $SkipCredential) {
    & (Join-Path $installRoot "packaging\configure-api-key.ps1") -InstallDir $installRoot
}

$launcher = Join-Path $installRoot "Start-Open-Shift.ps1"
$launcherText = @"
`$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
`$root = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$state = Get-Content -LiteralPath (Join-Path `$root "install.json") -Raw | ConvertFrom-Json
`$secretFile = Join-Path `$root "api-key.dpapi"
if (-not (Test-Path -LiteralPath `$secretFile)) { throw "API key is not configured. Run packaging\configure-api-key.ps1." }
`$env:OPEN_SHIFT_TIMING_LOG = Join-Path `$root "timing.log"
`$env:OPEN_SHIFT_DIALOGUE_LOG = Join-Path `$root "dialogue.log"
`$protected = [IO.File]::ReadAllBytes(`$secretFile)
`$bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(`$protected, `$null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
try { Set-Item -Path "Env:$ApiKeyEnv" -Value ([Text.Encoding]::UTF8.GetString(`$bytes)) } finally { [Array]::Clear(`$bytes, 0, `$bytes.Length) }
`$database = if (`$state.database) { [IO.Path]::GetFullPath([string] `$state.database) } else { Join-Path `$root "open-shift.sqlite3" }
`$arguments = @("launch", "--config", `$state.config, "--db", `$database, "--runtime-file", (Join-Path `$env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"), "--paired-save-dir", (Join-Path `$root "paired-saves"), "--game-cwd", `$state.game_copy_dir, "--game-command", "VA-11 Hall A.exe", "--steam-root", `$state.steam_root, "--steam-app-id", "447530", "--prepare-before-game", "--bridge-command") + @(`$state.bridge_command)
if (`$state.runtime_is_python) { `$env:PYTHONPATH = Join-Path `$root "src"; & `$state.runtime -m open_shift @arguments } else { & `$state.runtime @arguments }
exit `$LASTEXITCODE
"@
Write-Utf8NoBom $launcher $launcherText
Write-Host "Open Shift installed. Start it with: $installRoot\Start-Open-Shift.ps1"
Write-Host "Steam original was not modified; the patched copy is: $copyRoot"
if ($CompletionMarker) {
    $markerPath = [IO.Path]::GetFullPath($CompletionMarker)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $markerPath) | Out-Null
    Write-Utf8NoBom $markerPath ([DateTime]::UtcNow.ToString("o"))
}
exit 0
