@echo off
REM Star Wars Unlimited Card Manager — Web UI Launcher (HTTPS for iOS camera)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo.
echo ================================================
echo   SWU Card Manager - Web UI (HTTPS)
echo ================================================
echo.
echo   Handy (iPhone): https://192.168.178.74:8765
echo   PC (Lokal):      https://localhost:8765
echo.
echo   Warnung beim ersten Oeffnen auf dem iPhone:
echo   "Diese Verbindung ist nicht sicher" ->
echo   "Details" -> "Diese Website besuchen" -> bestaetigen
echo.
echo   Druecke Ctrl+C um zu stoppen.
echo ================================================
echo.
python -m app.web --host 0.0.0.0 --port 8765
pause
