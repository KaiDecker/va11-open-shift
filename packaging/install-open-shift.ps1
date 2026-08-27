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
        (Join-Path $Root "game-patch\manifest.json"),
        (Join-Path $Root "game-patch\apply_mod.csx")
    ) + @(Get-ChildItem -LiteralPath (Join-Path $Root "game-patch\gml") -Filter "*.gml" -File | Select-Object -ExpandProperty FullName)
    $hashText = (($files | Sort-Object | ForEach-Object { Get-Sha256Hex $_ }) -join "`n")
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($hashText)))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

if (-not $SteamGameDir) { $SteamGameDir = Find-SteamGameDir }
if (-not $SteamGameDir) {
    $SteamGameDir = Read-Host "Steam VA-11 HALL-A folder (contains data.win)"
}
if (-not $UtmtCli) { $UtmtCli = Find-UtmtCli }
if (-not $UtmtCli) {
    # This script is also launched hidden by the GUI. Never fall back to
    # Read-Host here: a missing/non-CLI bundle would otherwise appear frozen
    # forever while waiting for input that the player cannot see.
    throw "UndertaleModTool CLI 0.9.1.2 was not found in the package. Re-download a complete player package or pass -UtmtCli explicitly."
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
$patchFingerprint = Get-PatchFingerprint $packageRoot
$packageVersion = "development"
$packageManifest = Join-Path $packageRoot "PACKAGE_MANIFEST.json"
if (Test-Path -LiteralPath $packageManifest -PathType Leaf) {
    try { $packageVersion = [string] ((Get-Content -LiteralPath $packageManifest -Raw | ConvertFrom-Json).package_version) } catch { }
}

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
$alreadyInstalled = $false
if ((Test-Path -LiteralPath $patchRecord -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $copyRoot "data.win") -PathType Leaf)) {
    try {
        $priorPatch = Get-Content -LiteralPath $patchRecord -Raw | ConvertFrom-Json
        $priorStatePath = Join-Path $installRoot "install.json"
        $priorState = if (Test-Path -LiteralPath $priorStatePath -PathType Leaf) { Get-Content -LiteralPath $priorStatePath -Raw | ConvertFrom-Json } else { $null }
        $currentHash = Get-Sha256Hex (Join-Path $copyRoot "data.win")
        $alreadyInstalled = $currentHash -eq ([string] $priorPatch.installed_sha256).ToLowerInvariant() -and
            $null -ne $priorState -and
            ([string] $priorState.patch_fingerprint).ToLowerInvariant() -eq $patchFingerprint
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
prefetch_days = 0
"@
    Write-Utf8NoBom $config $configText
}

$shortcutPath = if ($SkipShortcut) { "" } else { Join-Path ([Environment]::GetFolderPath("Desktop")) "Open Shift.lnk" }
$state = [ordered]@{
    schema_version = 1
    package_version = $packageVersion
    patch_fingerprint = $patchFingerprint
    install_dir = $installRoot
    game_copy_dir = $copyRoot
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
`$arguments = @("launch", "--config", `$state.config, "--db", `$database, "--runtime-file", (Join-Path `$env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"), "--game-cwd", `$state.game_copy_dir, "--game-command", "VA-11 Hall A.exe", "--steam-root", `$state.steam_root, "--steam-app-id", "447530", "--prepare-before-game", "--bridge-command") + @(`$state.bridge_command)
if (`$state.runtime_is_python) { `$env:PYTHONPATH = Join-Path `$root "src"; & `$state.runtime -m open_shift @arguments } else { & `$state.runtime @arguments }
exit `$LASTEXITCODE
"@
Write-Utf8NoBom $launcher $launcherText
if (-not $SkipShortcut) {
    $shortcut = $shortcutPath
    Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $installedGui = Join-Path $installRoot "OpenShiftSetup.exe"
    if (Test-Path -LiteralPath $installedGui -PathType Leaf) {
        $link.TargetPath = $installedGui
        $link.Arguments = ""
        if (Test-Path -LiteralPath $installedIcon -PathType Leaf) {
            $link.IconLocation = "$installedIcon,0"
        } else {
            $link.IconLocation = "$installedGui,0"
        }
    } else {
        $link.TargetPath = "powershell.exe"
        $link.Arguments = "-ExecutionPolicy Bypass -File `"$launcher`""
    }
    $link.WorkingDirectory = $installRoot
    $link.Description = "Start VA-11 HALL-A Open Shift"
    $link.Save()
    $iconRefresh = Join-Path $env:SystemRoot "System32\ie4uinit.exe"
    if (Test-Path -LiteralPath $iconRefresh -PathType Leaf) {
        & $iconRefresh -show
    }
}

Write-Host "Open Shift installed. Start it with: $installRoot\Start-Open-Shift.ps1"
Write-Host "Steam original was not modified; the patched copy is: $copyRoot"
if ($CompletionMarker) {
    $markerPath = [IO.Path]::GetFullPath($CompletionMarker)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $markerPath) | Out-Null
    Write-Utf8NoBom $markerPath ([DateTime]::UtcNow.ToString("o"))
}
exit 0
