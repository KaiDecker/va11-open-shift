param(
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA "OpenShift")
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$root = [IO.Path]::GetFullPath($InstallDir)
if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "Open Shift is not installed at $root" }
$key = Read-Host "Enter your DeepSeek API key (input is hidden)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw "API key cannot be empty" }
    $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [IO.File]::WriteAllBytes((Join-Path $root "api-key.dpapi"), $protected)
    [Array]::Clear($bytes, 0, $bytes.Length)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $plain = $null
}
Write-Host "DeepSeek API key saved with Windows DPAPI for the current user."
