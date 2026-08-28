param(
    [string] $Version = "0.24.0-preview.5",
    [string] $Output = "",
    [string] $Python = "python",
    [string] $WebViewSdk = "",
    [Parameter(Mandatory = $true)] [string] $UtmtCliZip,
    [Parameter(Mandatory = $true)] [string] $OriginalDataWin
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
$deltaOut = Join-Path $root "work\data-win.delta"
function Find-WebViewSdkNative {
    $version = "1.0.3485.44"
    $expectedPackageSha256 = "bc09150b179246ac90189649b13be8e6b11b3ac200e817e18df106e1f3cf489e"
    $candidates = @($WebViewSdk, $env:OPEN_SHIFT_WEBVIEW2_SDK) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($candidate in $candidates) {
        $full = [IO.Path]::GetFullPath($candidate)
        $include = Join-Path $full "build\native\include"
        $native = Join-Path $full "build\native"
        if (-not (Test-Path -LiteralPath (Join-Path $include "WebView2.h") -PathType Leaf)) {
            $include = Join-Path $full "include"
            $native = $full
        }
        $arch = Join-Path $native "x64"
        $loader = Join-Path $arch "WebView2Loader.dll"
        $lib = Join-Path $arch "WebView2LoaderStatic.lib"
        if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) { $lib = Join-Path $arch "WebView2Loader.dll.lib" }
        if ((Test-Path -LiteralPath (Join-Path $include "WebView2.h") -PathType Leaf) -and (Test-Path -LiteralPath $loader -PathType Leaf) -and (Test-Path -LiteralPath $lib -PathType Leaf)) {
            return [pscustomobject]@{ Include = $include; Loader = $loader; Library = $lib }
        }
    }
    $downloadRoot = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-webview2-" + $version)
    $package = Join-Path $downloadRoot ("Microsoft.Web.WebView2." + $version + ".nupkg")
    $expanded = Join-Path $downloadRoot "expanded"
    if (-not (Test-Path -LiteralPath (Join-Path $expanded "build\native\include\WebView2.h") -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
        if (-not (Test-Path -LiteralPath $package -PathType Leaf)) {
            Invoke-WebRequest -Uri ("https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/" + $version) -OutFile $package
        }
        Expand-Archive -LiteralPath $package -DestinationPath $expanded -Force
    }
    if (Test-Path -LiteralPath $package -PathType Leaf) {
        $actualPackageSha256 = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualPackageSha256 -ne $expectedPackageSha256) { throw "Native WebView2 SDK SHA-256 did not match the pinned package." }
    }
    $arch = Join-Path $expanded "build\native\x64"
    $lib = Join-Path $arch "WebView2LoaderStatic.lib"
    if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) { $lib = Join-Path $arch "WebView2Loader.dll.lib" }
    if (-not (Test-Path -LiteralPath (Join-Path $expanded "build\native\include\WebView2.h") -PathType Leaf) -or -not (Test-Path -LiteralPath $lib -PathType Leaf)) { throw "Native WebView2 SDK could not be prepared." }
    return [pscustomobject]@{ Include = Join-Path $expanded "build\native\include"; Loader = Join-Path $arch "WebView2Loader.dll"; Library = $lib }
}
$resolvedWebViewSdk = Find-WebViewSdkNative
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
$webViewLoader = $resolvedWebViewSdk.Loader
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "packaging\create-icon.ps1") -Output $iconOut
if ($LASTEXITCODE -ne 0) { throw "OpenShift.ico build failed" }
& $pythonExe -m PyInstaller --noconfirm --clean --onefile --name OpenShift (Join-Path $root "packaging\runtime_entry.py") --paths (Join-Path $root "src") --distpath (Split-Path -Parent $runtimeOut) --workpath (Join-Path $root "work\pyinstaller-build") --specpath (Join-Path $root "work\pyinstaller-spec")
if ($LASTEXITCODE -ne 0) { throw "OpenShift.exe build failed" }
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw "Visual Studio 2022 with the C++ workload is required to build the native launcher." }
$vsInstall = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1).Trim()
if ([string]::IsNullOrWhiteSpace($vsInstall)) { throw "Visual Studio C++ build tools were not found." }
$devCmd = Join-Path $vsInstall "Common7\Tools\VsDevCmd.bat"
$nativeSource = Join-Path $root "packaging\native\OpenShiftSetup.cpp"
$nativeBuild = Join-Path $root "work\native-launcher"
New-Item -ItemType Directory -Force -Path $nativeBuild | Out-Null
$compile = "call `"$devCmd`" -arch=x64 && cl /nologo /std:c++17 /EHsc /O2 /MT /utf-8 /DUNICODE /D_UNICODE /I `"$($resolvedWebViewSdk.Include)`" /Fo`"$nativeBuild\OpenShiftSetup.obj`" `"$nativeSource`" `"$($resolvedWebViewSdk.Library)`" /link /SUBSYSTEM:WINDOWS /OUT:`"$guiOut`" /PDB:`"$nativeBuild\OpenShiftSetup.pdb`""
& cmd.exe /d /s /c $compile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $guiOut -PathType Leaf)) { throw "Native OpenShiftSetup.exe build failed" }
$deltaBuildDir = Join-Path ([IO.Path]::GetTempPath()) ("open-shift-release-delta-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $deltaBuildDir | Out-Null
try {
    $originalPath = (Resolve-Path -LiteralPath $OriginalDataWin -ErrorAction Stop).Path
    $utmtExtracted = Join-Path $deltaBuildDir "utmt"
    Expand-Archive -LiteralPath $UtmtCliZip -DestinationPath $utmtExtracted -Force
    $utmt = (Get-ChildItem -LiteralPath $utmtExtracted -Filter "UndertaleModCli.exe" -Recurse -File | Select-Object -First 1).FullName
    if (-not $utmt) { throw "UTMT CLI executable was not found in $UtmtCliZip" }
    $deltaInput = Join-Path $deltaBuildDir "original.data.win"
    $patchedInput = Join-Path $deltaBuildDir "patched.data.win"
    Copy-Item -LiteralPath $originalPath -Destination $deltaInput -Force
    & $utmt load $deltaInput -s (Join-Path $root "game-patch\apply_mod.csx") -o $patchedInput -v
    if ($LASTEXITCODE -ne 0) { throw "UTMT patching failed with code $LASTEXITCODE" }
    & $pythonExe -m open_shift verify-patch-output --original-data-win $originalPath --patched-data-win $patchedInput --manifest (Join-Path $root "game-patch\manifest.json") --gml-source-dir (Join-Path $root "game-patch\gml")
    if ($LASTEXITCODE -ne 0) { throw "Patched data.win verification failed" }
    & $pythonExe -m open_shift build-data-delta --original-data-win $originalPath --patched-data-win $patchedInput --output $deltaOut
    if ($LASTEXITCODE -ne 0) { throw "data.win delta build failed" }
}
finally {
    Remove-Item -LiteralPath $deltaBuildDir -Recurse -Force -ErrorAction SilentlyContinue
}
& $pythonExe -m open_shift build-mod-package --project-root $root --output $outputPath --version $Version --runtime-exe $runtimeOut --gui-exe $guiOut --icon $iconOut --data-delta $deltaOut --webview-dll $webViewLoader
if ($LASTEXITCODE -ne 0) { throw "Player release package build failed" }
# Re-open and CRC-check the final archive through Python as an independent
# guard. Do not leave a corrupt package behind if a write was interrupted.
& $pythonExe -m zipfile -t $outputPath
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $outputPath -Force -ErrorAction SilentlyContinue
    throw "Player release package ZIP validation failed; corrupt output was removed: $outputPath"
}
Write-Host "Player release package: $outputPath"
