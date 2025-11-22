# PowerShell packaging helper for Windows
# Usage: run this from project root (g:\project\py_video) in PowerShell (run as admin if needed)

param(
    [string]$VlcInstallPath = $env:PY_VIDEO_LIBVLC,
    [string]$PythonExe = "python",
    [string]$EntryScript = "player_qml.py",
    [string]$AppName = "py_video_player"
)

function Write-Log($msg){ Write-Host "[build] $msg" }

if (-not $VlcInstallPath) {
    # common default paths
    $candidates = @(
        "C:\Program Files\VideoLAN\VLC",
        "C:\Program Files (x86)\VideoLAN\VLC"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { $VlcInstallPath = $p; break }
    }
}

if (-not $VlcInstallPath -or -not (Test-Path $VlcInstallPath)){
    Write-Error "VLC install path not found. Install VLC or provide path via -VlcInstallPath or set PY_VIDEO_LIBVLC environment variable."; exit 1
}

Write-Log "Using VLC path: $VlcInstallPath"

# Ensure PyInstaller is installed
Write-Log "Installing PyInstaller (if missing)"
& $PythonExe -m pip install pyinstaller | Out-Null

# Clean previous build
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build, .\dist, .\${AppName}.spec

# Run PyInstaller in --onedir mode (recommended for bundling VLC files)
$addData = "qml;qml"
Write-Log "Running PyInstaller (onedir)"
& $PythonExe -m PyInstaller --noconfirm --onedir --name $AppName --add-data $addData $EntryScript
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed (exit $LASTEXITCODE)"; exit $LASTEXITCODE }

$distDir = Join-Path -Path (Get-Location) -ChildPath "dist\$AppName"
if (-not (Test-Path $distDir)) { Write-Error "dist output not found: $distDir"; exit 1 }

# Copy VLC files: dlls and plugins
$targetVlcDir = Join-Path $distDir "vlc"
Write-Log "Copying VLC files to: $targetVlcDir"
New-Item -ItemType Directory -Force -Path $targetVlcDir | Out-Null

# Copy libvlc dlls (libvlc.dll, libvlccore.dll) and the plugins folder
Get-ChildItem -Path $VlcInstallPath -Filter "libvlc*.dll" -File -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName -Destination $targetVlcDir -Force }
Get-ChildItem -Path $VlcInstallPath -Filter "libvlccore*.dll" -File -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName -Destination $targetVlcDir -Force }

# copy plugins folder
$pluginsSrc = Join-Path $VlcInstallPath 'plugins'
if (Test-Path $pluginsSrc) {
    Write-Log "Copying VLC plugins (this may take a while)"
    robocopy $pluginsSrc (Join-Path $targetVlcDir 'plugins') /E /NFL /NDL /NJH /NJS > $null
}

# Create a small launcher batch that sets PATH to include the embedded vlc folder
$launcher = @"
@echo off
setlocal
set APPDIR=%~dp0
set PATH=%APPDIR%vlc;%PATH%
"@
$launcher += "start "" "%APPDIR%\$AppName.exe"
$launcherPath = Join-Path $distDir "run.bat"
Set-Content -Path $launcherPath -Value $launcher -Encoding ASCII

Write-Log "Packaging complete. Distribution folder: $distDir"
Write-Log "Run the app using: $distDir\run.bat"

exit 0
