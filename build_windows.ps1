<#
  Builds dist\Takeline.exe (and the installer if Inno Setup 6 is installed).

  Usage (PowerShell, from the project folder):
    .\build_windows.ps1                  # build the .exe with the built-in metadata readers
    .\build_windows.ps1 -WithExifTool    # also download and bundle ExifTool
    .\build_windows.ps1 -SkipTests

  Requirements on the BUILD machine only: Python 3.10+ (64-bit) from python.org.
  End users need nothing: the .exe is self-contained.
#>
param([switch]$WithExifTool, [switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = (Get-Command py -ErrorAction SilentlyContinue)
if ($py) { $python = "py"; $pyArgs = @("-3") } else { $python = "python"; $pyArgs = @() }

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    & $python @pyArgs -m venv .venv
}
$venvPy = ".\.venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements-dev.txt --quiet

if (-not $SkipTests) {
    Write-Host "Running tests..." -ForegroundColor Cyan
    $env:QT_QPA_PLATFORM = "offscreen"
    & $venvPy -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }
    Remove-Item Env:QT_QPA_PLATFORM
}

if ($WithExifTool) {
    Write-Host "Downloading ExifTool (build time only)..." -ForegroundColor Cyan
    $ver = (Invoke-WebRequest "https://exiftool.org/ver.txt" -UseBasicParsing).Content.Trim()
    $zip = "build\exiftool.zip"
    New-Item -ItemType Directory -Force build | Out-Null
    Invoke-WebRequest "https://exiftool.org/exiftool-${ver}_64.zip" -OutFile $zip -UseBasicParsing
    Remove-Item -Recurse -Force "build\exiftool_unzip", "vendor\exiftool" -ErrorAction SilentlyContinue
    Expand-Archive $zip "build\exiftool_unzip"
    $dir = Get-ChildItem "build\exiftool_unzip" -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force "vendor\exiftool" | Out-Null
    Copy-Item -Recurse "$($dir.FullName)\*" "vendor\exiftool\"
    Rename-Item "vendor\exiftool\exiftool(-k).exe" "exiftool.exe"
    Write-Host "ExifTool $ver bundled." -ForegroundColor Green
}

if (-not (Test-Path "assets\Takeline.ico")) {
    & $venvPy -c "import sys; sys.path.insert(0,'src'); from PySide6.QtWidgets import QApplication; a=QApplication([]); from takeline.gui.icon import save_ico; save_ico('assets/Takeline.ico')"
}

Write-Host "Building Takeline.exe..." -ForegroundColor Cyan
& $venvPy -m PyInstaller --noconfirm --clean takeline.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Write-Host "Built: dist\Takeline.exe" -ForegroundColor Green

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "installer\Takeline.iss"
    Write-Host "Installer: dist\Takeline-Setup.exe" -ForegroundColor Green
} else {
    Write-Host "Inno Setup 6 not found: skipped the optional installer (the .exe works on its own)." -ForegroundColor Yellow
}
