@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run setup.ps1 first to install the local dependencies.
  pause
  exit /b 1
)
start "Chuddy" /b ".venv\Scripts\pythonw.exe" "run.py"
