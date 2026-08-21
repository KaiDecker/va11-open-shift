param(
    [Parameter(Mandatory = $true)] [string] $Output
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

function New-RoundedPath([Drawing.RectangleF] $rectangle, [float] $radius) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = $radius * 2
    $path.AddArc($rectangle.X, $rectangle.Y, $diameter, $diameter, 180, 90)
    $path.AddArc($rectangle.Right - $diameter, $rectangle.Y, $diameter, $diameter, 270, 90)
    $path.AddArc($rectangle.Right - $diameter, $rectangle.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($rectangle.X, $rectangle.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

$size = 256
$bitmap = New-Object Drawing.Bitmap($size, $size, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$graphics.Clear([Drawing.Color]::Transparent)

$outerPath = New-RoundedPath (New-Object Drawing.RectangleF(10, 10, 236, 236)) 48
$outerBrush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(255, 19, 34, 53))
$outerPen = New-Object Drawing.Pen([Drawing.Color]::FromArgb(255, 102, 224, 210), 7)
$graphics.FillPath($outerBrush, $outerPath)
$graphics.DrawPath($outerPen, $outerPath)

# The open door is the central mark; the coral arrow signals a shift into play.
$doorBrush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(255, 102, 224, 210))
$doorPoints = [Drawing.PointF[]]@(
    (New-Object Drawing.PointF(74, 70)), (New-Object Drawing.PointF(145, 53)),
    (New-Object Drawing.PointF(145, 203)), (New-Object Drawing.PointF(74, 186))
)
$graphics.FillPolygon($doorBrush, $doorPoints)

$cutoutBrush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(255, 19, 34, 53))
$cutoutPoints = [Drawing.PointF[]]@(
    (New-Object Drawing.PointF(105, 86)), (New-Object Drawing.PointF(130, 80)),
    (New-Object Drawing.PointF(130, 176)), (New-Object Drawing.PointF(105, 170))
)
$graphics.FillPolygon($cutoutBrush, $cutoutPoints)

$arrowPen = New-Object Drawing.Pen([Drawing.Color]::FromArgb(255, 255, 126, 104), 17)
$arrowPen.StartCap = [Drawing.Drawing2D.LineCap]::Round
$arrowPen.EndCap = [Drawing.Drawing2D.LineCap]::Round
$arrowPen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
$graphics.DrawLine($arrowPen, 87, 128, 184, 128)
$graphics.DrawLine($arrowPen, 153, 98, 184, 128)
$graphics.DrawLine($arrowPen, 153, 158, 184, 128)

$dotBrush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(255, 255, 206, 111))
$graphics.FillEllipse($dotBrush, 179, 64, 17, 17)
$graphics.FillEllipse($dotBrush, 52, 198, 13, 13)

$outputPath = [IO.Path]::GetFullPath($Output)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
$pngStream = New-Object IO.MemoryStream
$bitmap.Save($pngStream, [Drawing.Imaging.ImageFormat]::Png)
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
$graphics.Dispose()
$outerBrush.Dispose(); $outerPen.Dispose(); $outerPath.Dispose()
$doorBrush.Dispose(); $cutoutBrush.Dispose(); $arrowPen.Dispose(); $dotBrush.Dispose()
$bitmap.Dispose()
Write-Host "Open Shift icon: $outputPath"
