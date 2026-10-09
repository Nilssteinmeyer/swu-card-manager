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
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOn Batteries:$false -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "SWU-Backup-Daily" -Action $action -Trigger $trigger -Settings $settings -Description "SWU Card Manager: taegliches Backup mit Rotation + woechentlichem Restore-Drill" -Force

Write-Host "Backup-Task registriert: SWU-Backup-Daily (03:00 taeglich)"

# --- 2) DuckDNS-Update (alle 10 min, Fallback falls Fritzbox-DDNS versagt) ---
# Wird in Go-Live Phase 6 mit dem echten Token konfiguriert; Task erstmal vorbereiten
Write-Host "Setup abgeschlossen. Dienste (NSSM) folgen in Phase 6 nach dem ersten Boot-Test."
