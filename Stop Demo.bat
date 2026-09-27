@echo off
setlocal
cd /d "%~dp0"
title Stop the scan pipeline demo
echo.
echo  Stopping the demo. Your results are kept, and they will be there next time.
echo.
docker compose down
echo.
echo  Stopped. To start again, double-click "Start Demo".
echo.
pause
