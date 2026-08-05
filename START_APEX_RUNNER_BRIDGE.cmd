@echo off
setlocal
cd /d "%~dp0"
title APEX Runner Bridge - One Click Ignition

echo Starting the APEX Runner Bridge automated bootstrap...
echo No PEM key will be shown, copied, pasted, or stored manually.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0github-app\start_apex_runner_bridge.ps1"
set "APEX_EXIT=%ERRORLEVEL%"

echo.
if "%APEX_EXIT%"=="0" (
  echo APEX RUNNER BRIDGE COMPLETED SUCCESSFULLY.
) else (
  echo APEX RUNNER BRIDGE FAILED CLOSED. Exit code: %APEX_EXIT%
)

pause
exit /b %APEX_EXIT%
