# ============================================================================
#  SWU Card Manager - Infra P4: Release-Flow Test -> Prod
#  deploy_prod.ps1 [-Tag v1.2.0] [-DryRun] [-Rollback]
#
#  Pipeline (Infra-Konzept §2.2):
#    1. Gate: pytest Unit + E2E muessen gruen sein
#    2. Gate: Tag vorhanden (Prod deployed NUR Version-Tags, nie master)
#    3. Backup-Gate: backup_daily.py --env prod --pre-deploy (kein Backup,
#       kein Deploy)
#    4. Wartungsmodus AN (config/maintenance_on; 503 + Retry-After)
#    5. Code-Ausrollen: git fetch + checkout <Tag> (+ pip install bei
#       geaenderter requirements.txt)
#    6. Prod-Neustart (Supervisor-Stop-Flag kurz setzen -> Restart)
#    7. Health-Gate: /api/health/ready bis 200 (max 120 s), sonst
#       AUTO-ROLLBACK auf den vorherigen Stand
#    8. Wartungsmodus AUS + Deploy-Report
#
#  -Rollback: manueller Rollback auf den letzten Tag vor dem Deploy.
#  Rollbacks beruehren NIE Daten (nur Code); DB-Notfall = restore_backup.py.
# ============================================================================
#requires -version 5.1
$ErrorActionPreference = "Stop"

param(
    [string]$Tag = "",
    [switch]$DryRun,
    [switch]$Rollback
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$MaintFlag  = Join-Path $ProjectDir "config\maintenance_on"
$StopProd   = Join-Path $ProjectDir "config\.stop_prod"

function Write-Step($msg) { Write-Host "== $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Err($msg)   { Write-Host "  [!!] $msg" -ForegroundColor Red }

Set-Location $ProjectDir

# --- Rollback-Modus ----------------------------------------------------------
if ($Rollback) {
    Write-Step "Rollback: letzter Deploy-Tag"
    $last = Get-Content (Join-Path $ProjectDir "config\.last_deploy") -ErrorAction SilentlyContinue
    if (-not $last) { Write-Err "Kein .last_deploy gefunden - nichts zum Rollback."; exit 1 }
    Write-Host "  Vorheriger Stand: $last"
    if ($DryRun) { Write-Host "  (DryRun: kein Checkout)"; exit 0 }
    New-Item -ItemType File -Path $MaintFlag -Force | Out-Null
    git checkout -f $last 2>&1 | Out-Null
    Remove-Item $StopProd -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 16
    Remove-Item $MaintFlag -Force -ErrorAction SilentlyContinue
    Write-Ok "Rollback auf $last abgeschlossen."
    exit 0
}

# --- Gate 1: Tests ------------------------------------------------------------
Write-Step "Gate 1: Test-Suite (Unit)"
& $VenvPython -m pytest tests/ --ignore=tests/test_recognition_accuracy.py -q --tb=no | Tee-Object -Variable testOut | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Err "Unit-Tests rot - KEIN Deploy."; exit 1 }
Write-Ok "Unit-Tests gruen."

Write-Step "Gate 1b: E2E-Suite"
& $VenvPython -m pytest tests/e2e -m e2e -q --tb=no | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Err "E2E rot - KEIN Deploy."; exit 1 }
Write-Ok "E2E gruen."

# --- Gate 2: Tag --------------------------------------------------------------
Write-Step "Gate 2: Version-Tag"
git fetch --tags 2>&1 | Out-Null
if (-not $Tag) {
    $Tag = git describe --tags --abbrev=0 2>$null
    if (-not $Tag) { Write-Err "Kein Tag angegeben und keiner im Repo - erst 'git tag v1.2.0' setzen."; exit 1 }
    Write-Host "  Neuester Tag: $Tag"
}
$tagOk = git rev-parse -q --verify "refs/tags/$Tag" 2>$null
if (-not $tagOk) { Write-Err "Tag $Tag existiert nicht."; exit 1 }
$status = git status --porcelain
if ($status) { Write-Err "Working tree nicht clean: $status"; exit 1 }
$previous = git describe --tags --abbrev=0 HEAD 2>$null
Write-Ok "Deploye $Tag (vorher: $previous)."

if ($DryRun) { Write-Host "  (DryRun: Gates 3-8 werden NICHT ausgefuehrt)"; exit 0 }

# --- Gate 3: Pre-Deploy-Backup -------------------------------------------------
Write-Step "Gate 3: Pre-Deploy-Backup (Prod)"
& $VenvPython (Join-Path $ProjectDir "scripts\backup_daily.py") --env prod --pre-deploy | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Err "Backup FEHLGESCHLAGEN - KEIN Deploy."; exit 1 }
Write-Ok "Backup erstellt (inkl. D:-Kopie falls Stick angeschlossen)."

# --- Wartungsmodus AN ----------------------------------------------------------
Write-Step "Wartungsmodus AN"
New-Item -ItemType File -Path $MaintFlag -Force | Out-Null
Write-Ok "503 + Retry-After fuer Nicht-Admins."

try {
    # --- 5: Code ausrollen -----------------------------------------------------
    Write-Step "Ausrollen: git checkout $Tag"
    git checkout -f $Tag 2>&1 | Out-Null
    git log -1 --oneline

    # requirements geaendert? -> pip install
    $reqChanged = git diff --name-only $previous $Tag 2>$null | Select-String "requirements.txt"
    if ($reqChanged) {
        Write-Step "requirements.txt geaendert - pip install"
        & $VenvPython -m pip install -r requirements.txt --quiet 2>&1 | Out-Null
    }

    # --- 6: Prod-Neustart -----------------------------------------------------
    Write-Step "Prod-Neustart (Supervisor-Restart)"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*app.web*8443*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 16  # ein Supervisor-Zyklus zum Neustart

    # --- 7: Health-Gate ---------------------------------------------------------
    Write-Step "Health-Gate: /api/health/ready (max 120 s)"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -Uri "https://localhost:8443/api/health/ready" `
                 -UseBasicParsing -SkipCertificateCheck -TimeoutSec 5 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Write-Err "Health nicht gruen - AUTO-ROLLBACK auf $previous"
        git checkout -f $previous 2>&1 | Out-Null
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*app.web*8443*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 16
        Write-Host "  Rollback fertig. Logs pruefen: logs\supervisor_prod.log"
        exit 1
    }
    $envInfo = (Invoke-WebRequest -Uri "https://localhost:8443/api/health/ready" `
        -UseBasicParsing -SkipCertificateCheck -TimeoutSec 5).Content
    Write-Ok "Prod bereit: $envInfo"

    # --- 8: Wartung AUS + Report -------------------------------------------------
    Remove-Item $MaintFlag -Force
    Write-Ok "Wartungsmodus AUS."
    "$Tag" | Set-Content (Join-Path $ProjectDir "config\.last_deploy")
    Write-Host ""
    Write-Host "DEPLOY ERFOLGREICH: $previous -> $Tag" -ForegroundColor Green
} catch {
    Write-Err "Unerwarteter Fehler: $_ - Wartungsmodus bleibt AN (sicherheitshalber)."
    Write-Host "  Nach Fix: Remove-Item $MaintFlag oder erneut deployen."
    exit 1
}
