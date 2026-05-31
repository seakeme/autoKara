# generate_icon.ps1 — Convert knm.png to multi-resolution .ico
# Usage: powershell -File generate_icon.ps1

Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = "Stop"

$srcPath = Join-Path $PSScriptRoot "..\knm.png"
$outPath = Join-Path $PSScriptRoot "app.ico"

if (-not (Test-Path $srcPath)) {
    Write-Host "ERROR: knm.png not found at $srcPath"
    exit 1
}

$src = [System.Drawing.Image]::FromFile((Resolve-Path $srcPath))

# Generate multiple resolutions for taskbar, desktop, explorer
$sizes = @(256, 128, 96, 64, 48, 40, 32, 24, 16)
$entries = @()

foreach ($size in $sizes) {
    $w = [int]$size
    $h = [int]$size

    $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode  = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode      = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $g.PixelOffsetMode    = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.DrawImage($src, 0, 0, $w, $h)
    $g.Dispose()

    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Bmp)
    $raw = $ms.ToArray()
    $ms.Close()

    # Strip 14-byte BMP file header, keep DIB (info header + pixels)
    $dibLen = $raw.Length - 14
    $dib = New-Object byte[] $dibLen
    [Array]::Copy($raw, 14, $dib, 0, $dibLen)

    # ICO expects DIB height to be doubled (XOR mask + AND mask).
    # BITMAPINFOHEADER byte offset 8 = biHeight (Int32).
    $hVal = [BitConverter]::GetBytes([int32]($h * 2))
    [Array]::Copy($hVal, 0, $dib, 8, 4)

    # AND mask (1bpp transparency, all zeros for 32bpp ARGB).
    # Row stride = ((w + 31) / 32) * 4 bytes, rounded up.
    $andRow = [Math]::Ceiling($w / 32.0) * 4
    $andSize = [int]($andRow * $h)
    $andMask = New-Object byte[] $andSize

    # Final DIB for ICO: header + XOR pixels + AND mask
    $icoDib = New-Object byte[] ($dibLen + $andSize)
    [Array]::Copy($dib, 0, $icoDib, 0, $dibLen)
    [Array]::Copy($andMask, 0, $icoDib, $dibLen, $andSize)

    if ($w -ge 256) { $wByte = 0 } else { $wByte = [byte]$w }
    if ($h -ge 256) { $hByte = 0 } else { $hByte = [byte]$h }

    $entries += @{
        w       = $wByte
        h       = $hByte
        dibSize = $icoDib.Length
        dib     = $icoDib
        bmp     = $bmp
    }
}

# Write .ico file
$fs = [System.IO.File]::Open($outPath, 'Create')
$bw = New-Object System.IO.BinaryWriter($fs)

# ICO header: reserved(2) + type(2) + count(2)
$bw.Write([UInt16]0)
$bw.Write([UInt16]1)
$bw.Write([UInt16]$entries.Count)

$dataOffset = 6 + 16 * $entries.Count

# Directory entries
foreach ($e in $entries) {
    $bw.Write([Byte]$e.w)
    $bw.Write([Byte]$e.h)
    $bw.Write([Byte]0)                  # palette
    $bw.Write([Byte]0)                  # reserved
    $bw.Write([UInt16]1)                # color planes
    $bw.Write([UInt16]32)               # bpp
    $bw.Write([UInt32]$e.dibSize)
    $bw.Write([UInt32]$dataOffset)
    $dataOffset += $e.dibSize
}

# Image data
foreach ($e in $entries) {
    $bw.Write($e.dib)
}

$bw.Close()
$fs.Close()

$src.Dispose()
foreach ($e in $entries) { $e.bmp.Dispose() }

Write-Host "      multi-res icon generated ($($sizes -join ', ') px)"
