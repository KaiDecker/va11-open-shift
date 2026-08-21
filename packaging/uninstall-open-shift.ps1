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
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Open Shift.lnk"
if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
Remove-Item -LiteralPath $root -Recurse -Force
Write-Host "Open Shift was uninstalled. The Steam installation was not modified."
