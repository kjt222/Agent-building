param([string]$outPath = "C:\Users\kjt\AppData\Local\Temp\_tmp_obsidian.png")

Add-Type -AssemblyName System.Windows.Forms,System.Drawing

# Full virtual screen capture — Obsidian error dialog will be on screen if open
$screens = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $screens.Width, $screens.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($screens.X, $screens.Y, 0, 0, $bmp.Size)
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved: $outPath  $($screens.Width)x$($screens.Height)"
