param(
    [Parameter(Mandatory = $true)] [string] $Output,
    [string] $Source = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = Join-Path $PSScriptRoot "..\assets\open-shift-icon.svg"
}
if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw "Pixel SVG source not found: $Source"
}

$svg = Get-Content -LiteralPath $Source -Raw
$viewBoxMatch = [regex]::Match($svg, 'viewBox="0 0 (?<width>\d+) (?<height>\d+)"')
if (-not $viewBoxMatch.Success) { throw "SVG viewBox is missing or invalid." }
$pixelWidth = [int]$viewBoxMatch.Groups["width"].Value
$pixelHeight = [int]$viewBoxMatch.Groups["height"].Value

$pixelBitmap = New-Object Drawing.Bitmap($pixelWidth, $pixelHeight, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
$pixelGraphics = [Drawing.Graphics]::FromImage($pixelBitmap)
$pixelGraphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::None
$pixelGraphics.Clear([Drawing.Color]::Transparent)

# The SVG uses one horizontal pixel-run per path command. Rendering those runs
# directly keeps the source vector and the ICO output visually identical.
$pathMatches = [regex]::Matches($svg, '<path\s+fill="#(?<color>[0-9A-Fa-f]{6})"\s+d="(?<data>[^"]+)"\s*/>')
if ($pathMatches.Count -eq 0) { throw "SVG contains no pixel paths." }
foreach ($pathMatch in $pathMatches) {
    $color = [Drawing.ColorTranslator]::FromHtml("#" + $pathMatch.Groups["color"].Value)
    $brush = New-Object Drawing.SolidBrush($color)
    try {
        foreach ($run in ($pathMatch.Groups["data"].Value -split "M")) {
            if ([string]::IsNullOrWhiteSpace($run)) { continue }
            $runMatch = [regex]::Match($run.Trim(), '^(?<x>\d+)\s+(?<y>\d+)\s+h(?<width>\d+)\s+v1\s+h-\d+\s+z')
            if (-not $runMatch.Success) { throw "Unsupported pixel path command: $run" }
            $x = [int]$runMatch.Groups["x"].Value
            $y = [int]$runMatch.Groups["y"].Value
            $width = [int]$runMatch.Groups["width"].Value
            $pixelGraphics.FillRectangle($brush, $x, $y, $width, 1)
        }
    } finally { $brush.Dispose() }
}

$outputPath = [IO.Path]::GetFullPath($Output)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
$finalBitmap = New-Object Drawing.Bitmap(256, 256, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
$finalGraphics = [Drawing.Graphics]::FromImage($finalBitmap)
$finalGraphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::None
$finalGraphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$finalGraphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::Half
$finalGraphics.Clear([Drawing.Color]::Transparent)
$finalGraphics.DrawImage($pixelBitmap, 0, 0, 256, 256)

$pngStream = New-Object IO.MemoryStream
$finalBitmap.Save($pngStream, [Drawing.Imaging.ImageFormat]::Png)
$pngBytes = $pngStream.ToArray()
$pngStream.Dispose()
$stream = [IO.File]::Open($outputPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
$writer = New-Object IO.BinaryWriter($stream)
try {
    # ICO directory with one 256x256 PNG image; zero width/height means 256.
    $writer.Write([UInt16]0); $writer.Write([UInt16]1); $writer.Write([UInt16]1)
    $writer.Write([Byte]0); $writer.Write([Byte]0); $writer.Write([Byte]0); $writer.Write([Byte]0)
    $writer.Write([UInt16]1); $writer.Write([UInt16]32)
    $writer.Write([UInt32]$pngBytes.Length); $writer.Write([UInt32]22)
    $writer.Write($pngBytes)
    $writer.Flush()
} finally { $writer.Dispose(); $stream.Dispose() }

$pixelGraphics.Dispose()
$pixelBitmap.Dispose()
$finalGraphics.Dispose()
$finalBitmap.Dispose()
Write-Host "Open Shift pixel icon: $outputPath"
