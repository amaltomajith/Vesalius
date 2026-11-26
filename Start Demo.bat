@echo off
setlocal
cd /d "%~dp0"
title Scan pipeline demo
echo.
echo  ==========================================
echo    Scan pipeline demo - starting up
echo  ==========================================
echo.

docker version >nul 2>&1
if errorlevel 1 (
  echo  Docker Desktop is not installed or not running.
  echo.
  echo  1. If it is not installed: get it free from https://www.docker.com/products/docker-desktop
  echo  2. If it is installed: open "Docker Desktop" from the Start menu and wait until
  echo     it says "Engine running" ^(a minute or two^).
  echo  3. Then double-click "Start Demo" again.
  echo.
  pause
  exit /b 1
)

echo  [1/3] Preparing settings ^(first time only^)...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup-demo.ps1"
if errorlevel 1 (
  echo.
  echo  Setup failed. Please take a screenshot of this window and share it.
  pause
  exit /b 1
)

echo.
echo  [2/3] Starting the demo ^(the first time this downloads and builds things,
echo        which can take 5 to 10 minutes. Later starts take under a minute^)...
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo  The demo could not start. The last lines of its log are below.
  docker compose logs --tail 25 api
  echo.
  echo  Please take a screenshot of this window and share it.
  pause
  exit /b 1
)

echo.
echo  [3/3] Waiting for the demo to be ready...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\wait-for-demo.ps1"
if errorlevel 1 (
  echo.
  echo  The demo did not become ready in time. The last lines of its log are below.
  docker compose logs --tail 25 api
  pause
  exit /b 1
)

set /p DEMO_URL=<".demo_url"
echo.
echo  Ready! Opening your browser at %DEMO_URL%
start "" "%DEMO_URL%"
echo.
echo  You can close this window. The demo keeps running in the background.
echo  To stop it, double-click "Stop Demo".
echo.
pause
