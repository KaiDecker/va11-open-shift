param(
    [Parameter(Mandatory = $true)] [string] $GameCopyDir,
    [Parameter(Mandatory = $true)] [string] $Database,
    [string] $RuntimeFile = (Join-Path $env:LOCALAPPDATA "VA_11_Hall_A\open-shift-runtime.ini"),
    [string] $Python = "python",
    [string] $ApiKeyEnv = "OPEN_SHIFT_API_KEY"
)

$ErrorActionPreference = "Stop"
if (-not (Get-ChildItem -LiteralPath $GameCopyDir -Filter "VA-11 Hall A.exe" -File -ErrorAction SilentlyContinue)) { throw "GameCopyDir does not contain VA-11 Hall A.exe" }
if (-not [Environment]::GetEnvironmentVariable($ApiKeyEnv, "Process")) { throw "Set $ApiKeyEnv in this PowerShell session before launching" }
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$env:PYTHONPATH = Join-Path $packageRoot "src"
& $Python -m open_shift launch --db $Database --runtime-file $RuntimeFile --game-cwd $GameCopyDir --game-command "VA-11 Hall A.exe" --steam-app-id 447530 --provider-base-url "https://api.deepseek.com" --provider-model "deepseek-v4-flash" --provider-protocol chat_completions --provider-response-format json_object --provider-api-key-env $ApiKeyEnv --provider-thinking disabled
exit $LASTEXITCODE
