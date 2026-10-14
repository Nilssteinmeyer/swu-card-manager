# Legt den Taegl BACKUP-Task fuer die TEST-Umgebung an (03:30, OHNE Admin
# geht nicht per Register-ScheduledTask auf diesem System) - Fallback:
# Der Supervisor prueft die Zeit selbst (Backup-once-per-day-Logik im
# App-Prozess waere Overkill) -> hier: schtasks.exe (Kommandozeilen-Tool,
# funktioniert ohne Admin im Benutzerkontext).
#requires -version 5.1
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Script  = Join-Path $ProjectDir "scripts\backup_daily.py"

# Bestehenden Task entfernen (idempotent)
schtasks /Query /TN "SWU-Backup-Test" 2>$null
if ($LASTEXITCODE -eq 0) {
    schtasks /Delete /TN "SWU-Backup-Test" /F | Out-Null
}

# Taeglich 03:30, TEST-Umgebung
schtasks /Create /TN "SWU-Backup-Test" /TR "`"$Python`" `"$Script`" --env test" /SC DAILY /ST 03:30 /F | Out-Null
Write-Host "[OK] Task 'SWU-Backup-Test' angelegt (taeglich 03:30, --env test)."

schtasks /Query /TN "SWU-Backup-Test" /V /FO LIST | Select-String -Pattern "Task To Run|Next Run|Status"
