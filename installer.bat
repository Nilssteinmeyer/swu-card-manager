@echo off
REM ============================================================
REM  SWU Card Manager - Gefuehrter Installer
REM  Fuehrt durch die komplette Einrichtung:
REM    [1] LOKAL   - Heimnetz (iPhone im WLAN, ~2 Minuten)
REM    [2] INTERNET- Erreichbar ueber DuckDNS+Caddy (~30-45 Min)
REM  Abbruch jederzeit moeglich - der Installer merkt sich
REM  den Fortschritt und macht beim naechsten Start weiter.
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "installer\setup.ps1"
pause
