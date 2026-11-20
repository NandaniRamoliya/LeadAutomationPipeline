@echo off
REM Starts the pipeline (engine + dashboard). Double-click, or run it from Task Scheduler
REM "At log on" / "At startup" so it keeps running. Keep the PC awake (Power settings: Sleep = Never).
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
:loop
leadpipe start
echo Pipeline stopped. Restarting in 30 seconds... (close this window to stop)
timeout /t 30 /nobreak >nul
goto loop
