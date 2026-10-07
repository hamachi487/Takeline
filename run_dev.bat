@echo off
rem Run Takeline from source (after build_windows.bat has created .venv)
set PYTHONPATH=%~dp0src
"%~dp0.venv\Scripts\python.exe" -m takeline %*
