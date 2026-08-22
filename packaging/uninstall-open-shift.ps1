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
if (Test-Path -LiteralPath $state.game_copy_dir) {
    Remove-Item -LiteralPath $state.game_copy_dir -Recurse -Force
}
if ($RemoveSaves) {
    $saveRoot = Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-paired-saves"
    if (Test-Path -LiteralPath $saveRoot) { Remove-Item -LiteralPath $saveRoot -Recurse -Force }
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
