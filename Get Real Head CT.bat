@echo off
setlocal
cd /d "%~dp0"
title Get a real public head CT
echo.
echo  ==========================================
echo    Get a real public head CT
echo  ==========================================
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
echo  Downloading (about 40 MB) and picking the main image series...
docker compose exec -T api python -m pipeline.demo.real_sample /tmp/head_ct.zip
if errorlevel 1 (
  echo.
  echo  That did not work. The message above says why.
  pause
  exit /b 1
)
if not exist real_scans mkdir real_scans
docker compose cp api:/tmp/head_ct.zip "real_scans\public-head-ct.zip"
if errorlevel 1 (
  echo  Could not copy the file out. Please take a screenshot of this window and share it.
  pause
  exit /b 1
)
echo.
echo  Done. Your file is:
echo    %cd%\real_scans\public-head-ct.zip
echo.
echo  Next: open the demo page, open "Or use your own scan", tick the box,
echo  choose "A head (the face will be removed)", pick the file above, and press the button.
echo.
start "" explorer "%cd%\real_scans"
pause
