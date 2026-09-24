@echo off
setlocal
set "ROOT=%~dp0"

echo ================================================
echo   Novel Writer - Start Frontend and Backend
echo ================================================
echo.
echo Starting PostgreSQL, backend API, and frontend...
echo.

rem Normal launch is idempotent: keep healthy services and only start missing ones.
rem Use "启动小说写作系统.cmd /restart" only when an explicit safe restart is needed.
set "START_MODE="
if /I "%~1"=="/restart" set "START_MODE=-Restart"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start.ps1" %START_MODE%
if errorlevel 1 (
    echo.
    echo [ERROR] Startup failed.
    echo Check logs\backend.stderr.log and logs\frontend.stderr.log.
    echo Make sure Docker, Python, and pnpm are installed and running.
    pause
    exit /b 1
)

rem Wait briefly for Vite, then open the workbench and API docs.
timeout /t 2 /nobreak >nul
start "Novel Writer Workbench" "http://127.0.0.1:5173"
start "Novel Writer API Docs" "http://127.0.0.1:8000/docs"

echo.
echo [OK] Frontend: http://127.0.0.1:5173
echo [OK] Backend:  http://127.0.0.1:8000/docs
echo.
echo Services are running in the background.
echo To stop them, terminate the PIDs recorded in .runtime\backend.pid and .runtime\frontend.pid.
pause
endlocal
