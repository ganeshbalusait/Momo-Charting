@echo off
REM Restart the MomX scanner worker. Double-click this file.
REM
REM WHY A .BAT AND NOT THE .PS1 DIRECTLY: this machine's LocalMachine execution
REM policy is RemoteSigned, so right-clicking an unsigned .ps1 and choosing
REM "Run with PowerShell" can refuse or flash-and-close with nothing shown.
REM Matches the existing "Start Trading App.bat" idiom.
REM
REM Safe any time. The scanner runs in its own process on port 3010 and the
REM watchdog respawns it, so stopping it IS the restart. New scanner code only
REM takes effect after this.

title Restart MomX Scanner
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-momx-worker.ps1"

echo.
echo ------------------------------------------------------------
echo Done. Leave the app a couple of minutes to rebuild its boards.
echo ------------------------------------------------------------
echo.
pause
