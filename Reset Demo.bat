@echo off
setlocal
cd /d "%~dp0"
title Reset the scan pipeline demo
echo.
echo  This DELETES every scan and result the demo has stored, and starts fresh.
echo  ^(It does not delete your files or the program itself.^)
echo.
set /p CONFIRM=  Type YES and press Enter to continue:
if /i not "%CONFIRM%"=="YES" (
  echo.
  echo  Cancelled. Nothing was deleted.
  pause
  exit /b 0
)
echo.
docker compose down --volumes
echo.
echo  Done. Double-click "Start Demo" to begin again.
echo.
pause
