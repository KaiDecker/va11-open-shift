param(
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA "OpenShift"),
    [switch] $RemoveSaves,
    [int] $WaitForProcessId = 0
)

$ErrorActionPreference = "Stop"
if ($WaitForProcessId -gt 0) {
    Wait-Process -Id $WaitForProcessId -Timeout 30 -ErrorAction SilentlyContinue
}
$root = (Resolve-Path -LiteralPath $InstallDir).Path
$stateFile = Join-Path $root "install.json"
if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) { throw "Open Shift install record was not found" }
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$gameCopy = [IO.Path]::GetFullPath([string] $state.game_copy_dir)
$steamGame = if ($state.steam_game_dir) { [IO.Path]::GetFullPath([string] $state.steam_game_dir) } else { "" }
if ($steamGame -and $gameCopy.TrimEnd('\') -ieq $steamGame.TrimEnd('\')) {
    throw "Refusing to remove the Steam game directory as an Open Shift instance"
}
$record = Join-Path $root "backups\install.json"
if (Test-Path -LiteralPath $record) {
    if ($state.runtime_is_python) {
        $env:PYTHONPATH = Join-Path $root "src"
        & $state.runtime -m open_shift uninstall-patch --record $record
    } else {
        & $state.runtime uninstall-patch --record $record
    }
    if ($LASTEXITCODE -ne 0) { throw "Patch uninstall failed; refusing to remove the installation" }
}
if (Test-Path -LiteralPath $gameCopy) {
    $gameCopyItem = Get-Item -LiteralPath $gameCopy -Force
    if (($gameCopyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a junction or symbolic link registered as the game instance: $gameCopy"
    }
    # Remove links one at a time so Remove-Item never traverses a junction into
    # the user's Steam installation. Only the instance directory is removed.
    Get-ChildItem -LiteralPath $gameCopy -Force | ForEach-Object {
        if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Remove-Item -LiteralPath $_.FullName -Force
        } else {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
    }
    Remove-Item -LiteralPath $gameCopy -Force
}
if ($RemoveSaves) {
    foreach ($saveRoot in @((Join-Path $root "paired-saves"))) {
        if (Test-Path -LiteralPath $saveRoot) { Remove-Item -LiteralPath $saveRoot -Recurse -Force }
    }
}
$shortcut = if ($state.PSObject.Properties.Name -contains "shortcut_path") {
    [string] $state.shortcut_path
} else {
    Join-Path ([Environment]::GetFolderPath("Desktop")) "Open Shift.lnk"
}
if (-not [string]::IsNullOrWhiteSpace($shortcut) -and (Test-Path -LiteralPath $shortcut -PathType Leaf)) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $expectedGui = Join-Path $root "OpenShiftSetup.exe"
    $expectedLauncher = Join-Path $root "Start-Open-Shift.ps1"
    $ownsShortcut = ([string] $link.TargetPath) -ieq $expectedGui -or
        (([IO.Path]::GetFileName([string] $link.TargetPath)) -ieq "powershell.exe" -and ([string] $link.Arguments).Contains($expectedLauncher))
    if ($ownsShortcut) {
        Remove-Item -LiteralPath $shortcut -Force
    } else {
        Write-Warning "Desktop shortcut was not owned by this installation and was preserved: $shortcut"
    }
}
Remove-Item -LiteralPath $root -Recurse -Force
Write-Host "Open Shift was uninstalled. The Steam installation was not modified."
