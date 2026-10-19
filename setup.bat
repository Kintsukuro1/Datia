@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set RC=%ERRORLEVEL%
pause
exit /b %RC%
