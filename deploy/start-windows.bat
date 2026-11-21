@echo off
REM Easiest way to run 24/7 on Windows: open a terminal in this folder and run ONCE:
REM     .venv\Scripts\activate
REM     leadpipe service install
REM It then starts with your PC, runs hidden in the background, and restarts itself if it stops.
REM
REM This file is the manual alternative: double-click to run in a window (closing the window stops it).
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
:loop
leadpipe start
echo Pipeline stopped. Restarting in 30 seconds... (close this window to stop)
timeout /t 30 /nobreak >nul
goto loop
