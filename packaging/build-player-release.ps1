param(
    [string] $Version = "0.19.0-rc.30",
    [string] $Output = "",
    [string] $Python = "python",
    [string] $WebViewSdk = "",
    [Parameter(Mandatory = $true)] [string] $UtmtCliZip
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path (Join-Path $root "work") "open-shift-player-$Version.zip"
}
$outputPath = [IO.Path]::GetFullPath($Output)
function Resolve-PythonExecutable([string] $PythonPath) {
    $command = @(Get-Command -Name $PythonPath -CommandType Application -ErrorAction SilentlyContinue) |
        Where-Object { $_.Source } |
        Select-Object -First 1
    if ($command) {
        return [IO.Path]::GetFullPath([string] $command.Source)
    }
    try {
        return [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path)
    }
    catch {
        throw "Python executable was not found: $PythonPath"
    }
}
$pythonExe = Resolve-PythonExecutable $Python
$pythonDir = Split-Path -Parent $pythonExe
$pyinstaller = Join-Path $pythonDir "Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $pyinstaller -PathType Leaf)) {
    $pyinstaller = "pyinstaller"
}
$env:PYTHONPATH = Join-Path $root "src"
$runtimeOut = Join-Path $root "work\OpenShift.exe"
$guiOut = Join-Path $root "work\OpenShiftSetup.exe"
$iconOut = Join-Path $root "work\OpenShift.ico"
function Find-WebViewSdk {
    $candidates = @(
        $WebViewSdk,
        $env:OPEN_SHIFT_WEBVIEW2_SDK,
        "C:\Program Files (x86)\Windows Kits\10\Windows Performance Toolkit"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($candidate in $candidates) {
        $full = [IO.Path]::GetFullPath($candidate)
        if ((Test-Path -LiteralPath (Join-Path $full "Microsoft.Web.WebView2.Core.dll") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $full "Microsoft.Web.WebView2.WinForms.dll") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $full "WebView2Loader.dll") -PathType Leaf)) {
            return $full
        }
    }
    throw "WebView2 SDK files were not found. Use -WebViewSdk with a directory containing the Core, WinForms, and Loader DLLs."
}
$resolvedWebViewSdk = Find-WebViewSdk
$utmtCheck = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-utmt-check-" + [guid]::NewGuid().ToString("N"))
try {
    Expand-Archive -LiteralPath $UtmtCliZip -DestinationPath $utmtCheck -Force
    if (-not (Get-ChildItem -LiteralPath $utmtCheck -Filter "UndertaleModCli.exe" -Recurse -File -ErrorAction SilentlyContinue)) {
        throw "-UtmtCliZip must contain UndertaleModCli.exe from UTMT_CLI_v0.9.1.2-Windows.zip; a desktop UndertaleModTool archive is not sufficient."
    }
}
finally {
    Remove-Item -LiteralPath $utmtCheck -Recurse -Force -ErrorAction SilentlyContinue
}
$webViewCore = Join-Path $resolvedWebViewSdk "Microsoft.Web.WebView2.Core.dll"
$webViewForms = Join-Path $resolvedWebViewSdk "Microsoft.Web.WebView2.WinForms.dll"
$webViewLoader = Join-Path $resolvedWebViewSdk "WebView2Loader.dll"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "packaging\create-icon.ps1") -Output $iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShift.ico build failed" }
& $pythonExe -m PyInstaller --noconfirm --clean --onefile --name OpenShift (Join-Path $root "packaging\runtime_entry.py") --paths (Join-Path $root "src") --distpath (Split-Path -Parent $runtimeOut) --workpath (Join-Path $root "work\pyinstaller-build") --specpath (Join-Path $root "work\pyinstaller-spec")
if ($LASTEXITCODE -ne 0) { throw "OpenShift.exe build failed" }
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) { throw "The .NET SDK is required to build the WebView2 launcher" }
$publishDir = Join-Path $root "work\webview-publish"
Remove-Item -LiteralPath $publishDir -Recurse -Force -ErrorAction SilentlyContinue
& $dotnet.Source publish (Join-Path $root "packaging\OpenShiftSetup.csproj") --configuration Release --output $publishDir --self-contained true --runtime win-x64 -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:WebViewSdk=$resolvedWebViewSdk -p:ApplicationIcon=$iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShiftSetup.exe WebView2 build failed" }
Copy-Item -LiteralPath (Join-Path $publishDir "OpenShiftSetup.exe") -Destination $guiOut -Force
& $pythonExe -m open_shift build-mod-package --project-root $root --output $outputPath --version $Version --runtime-exe $runtimeOut --gui-exe $guiOut --icon $iconOut --utmt-cli-zip $UtmtCliZip --webview-dll $webViewCore --webview-dll $webViewForms --webview-dll $webViewLoader
if ($LASTEXITCODE -ne 0) { throw "Player release package build failed" }
# Re-open and CRC-check the final archive through Python as an independent
# guard. Do not leave a corrupt package behind if a write was interrupted.
& $pythonExe -m zipfile -t $outputPath
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $outputPath -Force -ErrorAction SilentlyContinue
    throw "Player release package ZIP validation failed; corrupt output was removed: $outputPath"
}
Write-Host "Player release package: $outputPath"
