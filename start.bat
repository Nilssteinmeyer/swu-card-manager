@echo off
REM Star Wars Unlimited Card Manager — Windows Launcher
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m app gui
pause
