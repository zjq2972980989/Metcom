@echo off
setlocal
rem ===========================================================
rem  MetCom Web launcher: backend (FastAPI) + frontend (Vite)
rem  Keep this file ASCII-only. cmd.exe parses .bat files with
rem  the OEM code page, so non-ASCII text breaks the parsing.
rem ===========================================================

set "NODE_DIR=C:\Users\29729\nodejs"
if exist "%NODE_DIR%\npm.cmd" set "PATH=%NODE_DIR%;%PATH%"

set "WEB_DIR=%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm not found. Expected at "%NODE_DIR%".
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