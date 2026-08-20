@echo off
setlocal
cd /d "%~dp0"
title WDG Aircraft Sidecar
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "KEY_FILE=%LOCALAPPDATA%\Canadaverse\WDG-Aircraft-Sidecar\wdgwars_api_key"
if defined DATA_DIR set "KEY_FILE=%DATA_DIR%\wdgwars_api_key"
if defined WDGWARS_API_KEY_FILE set "KEY_FILE=%WDGWARS_API_KEY_FILE%"

if not exist "%KEY_FILE%" (
  echo First-time setup: enter your WDG Wars API key.
  "runtime\python.exe" "app\sidecar.py" --configure
  if errorlevel 1 (
    echo.
    echo Setup did not complete.
    pause
    exit /b 1
  )
  echo.
)

"runtime\python.exe" "app\sidecar.py"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo The sidecar stopped with an error. See the message above.
pause
exit /b %RESULT%
