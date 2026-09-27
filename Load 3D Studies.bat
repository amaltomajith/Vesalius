@echo off
setlocal
cd /d "%~dp0"
title Load the 3D viewer studies
echo.
echo  ==========================================
echo    Load the 3D viewer studies
echo  ==========================================
echo.
echo  Downloads three PUBLIC CT scans (a head, a chest and an abdomen, about 250 MB in all),
echo  checks each one's licence, and runs them through the pipeline in "named structures"
echo  mode. The first time also downloads the segmentation model. Allow 15 to 25 minutes.
echo  Scans that are already loaded are skipped, so it is safe to run again.
echo.
docker version >nul 2>&1
if errorlevel 1 (
  echo  Docker Desktop is not running. Open it, wait for "Engine running", then try again.
  pause
  exit /b 1
)
docker compose ps --status running api 2>nul | findstr /i "api" >nul
if errorlevel 1 (
  echo  The demo is not running. Double-click "Start Demo" first, then try again.
  pause
  exit /b 1
)
docker compose exec -T api python scripts/load_demo_studies.py
if errorlevel 1 (
  echo.
  echo  Not everything loaded. The messages above say why. You can run this again.
  pause
  exit /b 1
)
set DEMO_URL=http://localhost:8000/
if exist ".demo_url" set /p DEMO_URL=<".demo_url"
echo.
echo  Opening your studies at %DEMO_URL%app/studies
start "" "%DEMO_URL%app/studies"
pause
