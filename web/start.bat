@echo off
setlocal
rem ===========================================================
rem  MetCom Web launcher: backend (FastAPI) + frontend (Vite)
rem  Keep this file ASCII-only. cmd.exe parses .bat files with
rem  the OEM code page, so non-ASCII text breaks the parsing.
rem ===========================================================

rem Optional override when npm is not already available in PATH.
if defined METCOM_NODE_DIR if exist "%METCOM_NODE_DIR%\npm.cmd" set "PATH=%METCOM_NODE_DIR%;%PATH%"

set "WEB_DIR=%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm not found. Add Node.js to PATH or set METCOM_NODE_DIR.
    pause
    exit /b 1
)

echo Starting MetCom Web ...
start "MetCom Web API" /D "%WEB_DIR%backend" cmd /k start-backend.bat
start "MetCom Web UI"  /D "%WEB_DIR%frontend" cmd /k start-frontend.bat

echo.
echo   API docs : http://127.0.0.1:8000/api/docs
echo   Platform : http://127.0.0.1:5173
echo.
echo Close the two new windows to stop the services.
endlocal
