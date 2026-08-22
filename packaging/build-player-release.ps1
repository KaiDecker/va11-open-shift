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
$webViewSdk = "C:\Program Files (x86)\Windows Kits\10\Windows Performance Toolkit"
$webViewCore = Join-Path $webViewSdk "Microsoft.Web.WebView2.Core.dll"
$webViewForms = Join-Path $webViewSdk "Microsoft.Web.WebView2.WinForms.dll"
$webViewLoader = Join-Path $webViewSdk "WebView2Loader.dll"
foreach ($webViewFile in @($webViewCore, $webViewForms, $webViewLoader)) {
    if (-not (Test-Path -LiteralPath $webViewFile -PathType Leaf)) { throw "WebView2 SDK file was not found: $webViewFile" }
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "packaging\create-icon.ps1") -Output $iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShift.ico build failed" }
& $Python -m PyInstaller --noconfirm --clean --onefile --name OpenShift (Join-Path $root "packaging\runtime_entry.py") --paths (Join-Path $root "src") --distpath (Split-Path -Parent $runtimeOut) --workpath (Join-Path $root "work\pyinstaller-build") --specpath (Join-Path $root "work\pyinstaller-spec")
if ($LASTEXITCODE -ne 0) { throw "OpenShift.exe build failed" }
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) { throw "The .NET SDK is required to build the WebView2 launcher" }
$publishDir = Join-Path $root "work\webview-publish"
Remove-Item -LiteralPath $publishDir -Recurse -Force -ErrorAction SilentlyContinue
& $dotnet.Source publish (Join-Path $root "packaging\OpenShiftSetup.csproj") --configuration Release --output $publishDir --self-contained true --runtime win-x64 -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:WebViewSdk=$webViewSdk -p:ApplicationIcon=$iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShiftSetup.exe WebView2 build failed" }
Copy-Item -LiteralPath (Join-Path $publishDir "OpenShiftSetup.exe") -Destination $guiOut -Force
& $Python -m open_shift build-mod-package --project-root $root --output $outputPath --version $Version --runtime-exe $runtimeOut --gui-exe $guiOut --icon $iconOut --utmt-cli-zip $UtmtCliZip --webview-dll $webViewCore --webview-dll $webViewForms --webview-dll $webViewLoader
if ($LASTEXITCODE -ne 0) { throw "Player release package build failed" }
Write-Host "Player release package: $outputPath"
