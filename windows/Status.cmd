@echo off
setlocal
cd /d "%~dp0"
title WDG Aircraft Sidecar Status
"runtime\python.exe" "app\sidecar.py" --status
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
