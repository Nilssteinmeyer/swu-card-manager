# SWU Card Manager — Server-Setup (PowerShell)
# Registriert: Backup-Task (taeglich 03:00) und (spaeter) die Dienste.
# Ausfuehren EINMAL als Administrator.

$ErrorActionPreference = "Stop"
$projectDir = "C:\Users\LLM\Desktop\TCG-Cardscannprojekt\swu_card_manager"
$python = "$projectDir\.venv\Scripts\python.exe"

# --- 1) Taegliches Backup um 03:00 ---
$backupScript = "$projectDir\scripts\backup_daily.py"
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$backupScript`"" -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Daily -At 03:00
# Auch wenn der Laptop im Akku-Betrieb ist? Nein — Server laeuft am Strom.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries:$false -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "SWU-Backup-Daily" -Action $action -Trigger $trigger -Settings $settings -Description "SWU Card Manager: taegliches Backup mit Rotation + woechentlichem Restore-Drill" -Force

Write-Host "Backup-Task registriert: SWU-Backup-Daily (03:00 taeglich)"

# --- 2) DuckDNS-Update (alle 5 min) ---
# Laeuft sofort los; solange config/duckdns_token fehlt, beendet sich das
# Skript mit Exit 2 (Unschaedlich, ins Task-Log). Sobald der Token drin
# liegt, funktioniert es automatisch - keine Neu-Registrierung noetig.
$duckScript = "$projectDir\scripts\duckdns_update.py"
$ddAction = New-ScheduledTaskAction -Execute $python -Argument "`"$duckScript`"" -WorkingDirectory $projectDir
$ddTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$ddSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "SWU-DuckDNS-Update" -Action $ddAction -Trigger $ddTrigger -Settings $ddSettings -Description "SWU Card Manager: haelt swu-cardscanner.duckdns.org auf die aktuelle IP (Token in config/duckdns_token)" -Force
Write-Host "DuckDNS-Task registriert: SWU-DuckDNS-Update (alle 5 min; aktiv sobald Token liegt)"

# --- 3) Server-Autostart beim Boot (Normalbetrieb, kein Admin noetig) ---
$serverPs1 = "$projectDir\server.ps1"
$bootAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$serverPs1`"" -WorkingDirectory $projectDir
$bootTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$bootSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName "SWU-Server-Autostart" -Action $bootAction -Trigger $bootTrigger -Settings $bootSettings -Description "SWU Card Manager: startet den Web-Server beim Anmelden (server.ps1)" -Force
Write-Host "Autostart-Task registriert: SWU-Server-Autostart (beim Anmelden)"

Write-Host "Setup abgeschlossen. Dienste (Caddy/NSSM) folgen in Phase 6 nach dem ersten Boot-Test."
