@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-tvbt.ps1"
if errorlevel 1 (
  echo.
  echo TVBT failed to start. Review the error above and bin\runtime logs.
  pause
)
