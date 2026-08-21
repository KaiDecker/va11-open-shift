param(
    [string] $Version = "0.12.0",
    [string] $Output = (Join-Path (Join-Path $PSScriptRoot "..\work") "open-shift-player-$Version.zip"),
    [string] $Python = "python",
    [Parameter(Mandatory = $true)] [string] $UtmtCliZip
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputPath = [IO.Path]::GetFullPath($Output)
$pyinstaller = Join-Path (Split-Path -Parent $Python) "Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $pyinstaller -PathType Leaf)) {
    $pyinstaller = "pyinstaller"
}
$env:PYTHONPATH = Join-Path $root "src"
$runtimeOut = Join-Path $root "work\OpenShift.exe"
$guiOut = Join-Path $root "work\OpenShiftSetup.exe"
$iconOut = Join-Path $root "work\OpenShift.ico"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "packaging\create-icon.ps1") -Output $iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShift.ico build failed" }
& $Python -m PyInstaller --noconfirm --clean --onefile --name OpenShift (Join-Path $root "packaging\runtime_entry.py") --paths (Join-Path $root "src") --distpath (Split-Path -Parent $runtimeOut) --workpath (Join-Path $root "work\pyinstaller-build") --specpath (Join-Path $root "work\pyinstaller-spec")
if ($LASTEXITCODE -ne 0) { throw "OpenShift.exe build failed" }
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) { $csc = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path -LiteralPath $csc)) { throw "The Windows .NET Framework C# compiler was not found" }
& $csc /nologo /target:winexe /win32icon:$iconOut /reference:System.Windows.Forms.dll /out:$guiOut (Join-Path $root "packaging\gui_launcher.cs")
if ($LASTEXITCODE -ne 0) { throw "OpenShiftSetup.exe build failed" }
& $Python -m open_shift build-mod-package --project-root $root --output $outputPath --version $Version --runtime-exe $runtimeOut --gui-exe $guiOut --icon $iconOut --utmt-cli-zip $UtmtCliZip
if ($LASTEXITCODE -ne 0) { throw "Player release package build failed" }
Write-Host "Player release package: $outputPath"
