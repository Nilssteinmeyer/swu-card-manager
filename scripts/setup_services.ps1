# ============================================================================
#  SWU Card Manager - Infra P2: Beide Umgebungen als Windows-Dienste (NSSM)
#  SWU-App-Test : SWU_ENV=test  -> 0.0.0.0:8765 (Heimnetz, GPU-Scanner)
#  SWU-App-Prod : SWU_ENV=prod  -> 127.0.0.1:8443 (hinter Caddy, kein Scan)
#  Idempotent: mehrfach ausfuehrbar; Dienste werden neu eingerichtet.
#  AUFRUF: powershell -File scripts/setup_services.ps1 [-Remove]
# ============================================================================
#requires -version 5.1
$ErrorActionPreference = "Stop"

param(
    [switch]$Remove
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Nssm       = Join-Path $ProjectDir "tools\nssm.exe"
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogsDir    = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }

function Remove-AllServices {
    foreach ($name in @("SWU-App-Test", "SWU-App-Prod")) {
        & $Nssm stop $name 2>$null | Out-Null
        & $Nssm remove $name confirm 2>$null | Out-Null
    }
    Write-Host "  Dienste entfernt (falls vorhanden)."
}

if ($Remove) {
    Remove-AllServices
    exit 0
}

function Install-SwuService {
    param([string]$Name, [string]$Env, [string]$Host_, [int]$Port, [string]$Extra = "")
    Write-Host ""
    Write-Host "== Dienst $Name (SWU_ENV=$Env, $Host_`:$Port) ==" -ForegroundColor Cyan
    & $Nssm stop $Name 2>$null | Out-Null
    & $Nssm remove $Name confirm 2>$null | Out-Null
    Start-Sleep -Seconds 1

    $args_ = "-m app.web --host $Host_ --port $Port $Extra"
    & $Nssm install $Name $VenvPython $args_ 2>&1 | Out-Null
    & $Nssm set $Name AppDirectory $ProjectDir | Out-Null
    & $Nssm set $Name AppStdout (Join-Path $LogsDir "$($Name.ToLower()).log") | Out-Null
    & $Nssm set $Name AppStderr (Join-Path $LogsDir "$($Name.ToLower())_err.log") | Out-Null
    # Recherche-Empfehlung (Stabilitaet): Restart bei Crash + Throttling + Log-Rotation
    & $Nssm set $Name AppExit Default Restart | Out-Null
    & $Nssm set $Name AppRestartDelay 5000 | Out-Null
    & $Nssm set $Name AppRotateFiles 1 | Out-Null
    & $Nssm set $Name AppRotateBytes 52428800 | Out-Null
    & $Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    # Umgebungsvariable (P1) NUR fuer diesen Dienst
    & $Nssm set $Name AppEnvironmentExtra "SWU_ENV=$Env" | Out-Null

    & $Nssm start $Name | Out-Null
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Host "  [OK] $Name laeuft (Autostart, Restart bei Crash)." -ForegroundColor Green
    } else {
        Write-Host "  [!] $Name Status: $($svc.Status) - Log: logs\$($Name.ToLower())_err.log" -ForegroundColor Red
    }
}

# --- Installieren ------------------------------------------------------------
Install-SwuService -Name "SWU-App-Test" -Env "test" -Host_ "0.0.0.0" -Port 8765
Install-SwuService -Name "SWU-App-Prod" -Env "prod" -Host_ "127.0.0.1" -Port 8443

# --- Verifikation: Health-Check beider Instanzen ------------------------------
Write-Host ""
Write-Host "== Health-Verifikation (bis 60 s) ==" -ForegroundColor Cyan
$ok = $true
foreach ($check in @(
    @{ port = 8765; url = "https://localhost:8765/api/health"; expect = "test" },
    @{ port = 8443; url = "https://localhost:8443/api/health"; expect = "prod" }
)) {
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri $check.url -UseBasicParsing -SkipCertificateCheck -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200 -and $resp.Content -match $check.expect) { $ready = $true; break }
        } catch { }
    }
    if ($ready) {
        Write-Host "  [OK] Port $($check.port): /api/health env=$($check.expect)" -ForegroundColor Green
    } else {
        Write-Host "  [!] Port $($check.port): Health nicht gruen!" -ForegroundColor Red
        $ok = $false
    }
}
Write-Host ""
if ($ok) { Write-Host "P2: Beide Dienste laufen und antworten." -ForegroundColor Green }
else     { Write-Host "P2: Fehler - Logs pruefen (logs\swu-app-*.log)." -ForegroundColor Red }
