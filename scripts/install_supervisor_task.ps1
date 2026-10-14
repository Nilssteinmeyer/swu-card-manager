# Autostart des Supervisors ueber den Startup-Ordner (OHNE Admin;
# Register-ScheduledTask wird ohne Admin auf diesem System verweigert).
# Legt eine .cmd in shell:startup ab, die den Supervisor bei Anmeldung startet.
# Upgrade-Pfad: scripts/setup_services.ps1 (NSSM-Dienste, einmalig als Admin).
#requires -version 5.1
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python  = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$Script  = Join-Path $ProjectDir "scripts\supervisor.py"
$Startup = [Environment]::GetFolderPath("Startup")
$CmdPath = Join-Path $Startup "SWU-Supervisor.cmd"

$cmd = "@echo off`r`nrem SWU Card Manager Supervisor-Autostart (Startup-Ordner)`r`nif exist `"$Python`" start `"`" /min `"$Python`" `"$Script`"`r`n"
Set-Content -Path $CmdPath -Value $cmd -Encoding ASCII

Write-Host "[OK] Autostart eingerichtet: $CmdPath"
Write-Host (Get-Content $CmdPath -Raw)
