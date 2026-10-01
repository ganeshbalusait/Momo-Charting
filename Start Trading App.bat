@echo off
REM Double-click this to start the backend and open the trading dashboard
REM in its own app window. Safe to run when the backend is already up.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1"
if errorlevel 1 pause
