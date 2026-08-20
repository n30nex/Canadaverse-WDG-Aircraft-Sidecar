@echo off
setlocal
cd /d "%~dp0"
title Configure WDG Aircraft Sidecar
"runtime\python.exe" "app\sidecar.py" --configure
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
