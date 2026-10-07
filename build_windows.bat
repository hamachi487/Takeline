@echo off
rem Double-click to build dist\Takeline.exe
rem Add  -WithExifTool  to also bundle ExifTool, e.g.  build_windows.bat -WithExifTool
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %*
echo.
pause
