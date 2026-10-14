# ============================================================================ #
#  SWU Card Manager - Infra P2: Beide Umgebungen als Windows-Dienste (NSSM)     #
#  SWU-App-Test : SWU_ENV=test  -> 0.0.0.0:8765 (Heimnetz, GPU-Scanner)        #
#  SWU-App-Prod : SWU_ENV=prod  -> 127.0.0.1:8443 (hinter Caddy, kein Scan)    #
#  SWU-Caddy    : Reverse-Proxy, Let's Encrypt auf der DuckDNS-Domain         #
#  Idempotent: mehrfach ausfuehrbar; Dienste werden neu eingerichtet.          #
#  AUFRUF: powershell -File scripts/setup_services.ps1 [-Remove]               #
# ============================================================================ #
#requires -version 5.1
param(
    [switch]$Remove
)
$ErrorActionPreference = "Continue"

# Diagnose-Log (elevated Konsole ist unsichtbar)
$LogsDir0 = Join-Path (Split-Path -Parent $PSScriptRoot) "logs"
if (-not (Test-Path $LogsDir0)) { New-Item -ItemType Directory -Path $LogsDir0 | Out-Null }
Start-Transcript -Path (Join-Path $LogsDir0 "setup_services.log") -Append | Out-Null

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Nssm       = Join-Path $ProjectDir "tools\nssm.exe"
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogsDir    = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }

# --- NSSM-Wrapper: stderr- und $ErrorActionPreference-sicher -----------------
# NSSM schreibt Hinweise (z.B. remove eines nicht existierenden Dienstes) auf
# stderr; unter Stop + 2>&1 waere das ein PowerShell-Fehler. Der Wrapper laesst
# NSSM selbst reden und liefert den Exit-Code zurueck.
function Invoke-Nssm {
    param([string[]]$NssmArgs)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Nssm
    $psi.Arguments = ($NssmArgs | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } }) -join ' '
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($out.Trim()) { Write-Host "    nssm> $($out.Trim())" -ForegroundColor DarkGray }
    return $p.ExitCode
}

function Remove-AllServices {
    foreach ($name in @("SWU-Main-8765", "SWU-App-Prod", "SWU-App-Dev", "SWU-Caddy")) {
        [void](Invoke-Nssm @("stop", $name))
        [void](Invoke-Nssm @("remove", $name, "confirm"))
    }
    Write-Host "  Dienste entfernt (falls vorhanden)."
}

if ($Remove) {
    Remove-AllServices
    Stop-Transcript | Out-Null
    exit 0
}

function Install-SwuService {
    param([string]$Name, [string]$Env, [string]$BindHost, [int]$Port,
          [string]$WorkDir = "", [string]$SwuHome = "")
    Write-Host ""
    Write-Host "== Dienst $Name (SWU_ENV=$Env, ${BindHost}:${Port}) ==" -ForegroundColor Cyan
    [void](Invoke-Nssm @("stop", $Name))
    [void](Invoke-Nssm @("remove", $Name, "confirm"))
    Start-Sleep -Seconds 1

    if (-not $WorkDir) { $WorkDir = $ProjectDir }
    $appArgs = "-m app.web --host $BindHost --port $Port"
    $rc = Invoke-Nssm @("install", $Name, $VenvPython, $appArgs)
    if ($rc -ne 0) { Write-Host "  [!] install rc=$rc" -ForegroundColor Red }

    [void](Invoke-Nssm @("set", $Name, "AppDirectory", $WorkDir))
    if ($SwuHome) {
        [void](Invoke-Nssm @("set", $Name, "AppEnvironmentExtra", "SWU_ENV=$Env", "SWU_HOME=$SwuHome"))
    } else {
        [void](Invoke-Nssm @("set", $Name, "AppEnvironmentExtra", "SWU_ENV=$Env"))
    }
    [void](Invoke-Nssm @("set", $Name, "AppStdout", (Join-Path $LogsDir "$($Name.ToLower()).log")))
    [void](Invoke-Nssm @("set", $Name, "AppStderr", (Join-Path $LogsDir "$($Name.ToLower())_err.log")))
    # Recherche-Empfehlung (Stabilitaet): Restart bei Crash + Throttling + Log-Rotation
    [void](Invoke-Nssm @("set", $Name, "AppExit", "Default", "Restart"))
    [void](Invoke-Nssm @("set", $Name, "AppRestartDelay", "5000"))
    [void](Invoke-Nssm @("set", $Name, "AppRotateFiles", "1"))
    [void](Invoke-Nssm @("set", $Name, "AppRotateBytes", "52428800"))
    [void](Invoke-Nssm @("set", $Name, "Start", "SERVICE_AUTO_START"))

    [void](Invoke-Nssm @("start", $Name))
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Host "  [OK] $Name laeuft (Autostart, Restart bei Crash)." -ForegroundColor Green
    } else {
        Write-Host "  [!] $Name Status: '$($svc.Status)' - Log: logs\$($Name.ToLower())_err.log" -ForegroundColor Red
    }
}

# --- Installieren ------------------------------------------------------------ #
$ProdTree = Join-Path (Split-Path -Parent $ProjectDir) "swu_prod"
Install-SwuService -Name "SWU-Main-8765" -Env "test" -BindHost "0.0.0.0" -Port 8765
Install-SwuService -Name "SWU-App-Prod" -Env "prod" -BindHost "127.0.0.1" -Port 8443 -WorkDir $ProdTree -SwuHome $ProjectDir
Install-SwuService -Name "SWU-App-Dev" -Env "dev" -BindHost "127.0.0.1" -Port 8766

# Caddy-Dienst: Let's Encrypt auf swu-cardscanner.duckdns.org -> 127.0.0.1:8443
Write-Host ""
Write-Host "== Dienst SWU-Caddy (Reverse-Proxy, Let's Encrypt) ==" -ForegroundColor Cyan
$Caddy = Join-Path $ProjectDir "tools\caddy.exe"
$Caddyfile = Join-Path $ProjectDir "config\Caddyfile"
[void](Invoke-Nssm @("stop", "SWU-Caddy"))
[void](Invoke-Nssm @("remove", "SWU-Caddy", "confirm"))
Start-Sleep -Seconds 1
$rcCaddy = Invoke-Nssm @("install", "SWU-Caddy", $Caddy, "run --config `"$Caddyfile`"")
if ($rcCaddy -ne 0) { Write-Host "  [!] caddy install rc=$rcCaddy" -ForegroundColor Red }
[void](Invoke-Nssm @("set", "SWU-Caddy", "AppDirectory", $ProjectDir))
[void](Invoke-Nssm @("set", "SWU-Caddy", "AppStdout", (Join-Path $LogsDir "caddy.log")))
[void](Invoke-Nssm @("set", "SWU-Caddy", "AppStderr", (Join-Path $LogsDir "caddy_err.log")))
[void](Invoke-Nssm @("set", "SWU-Caddy", "AppExit", "Default", "Restart"))
[void](Invoke-Nssm @("set", "SWU-Caddy", "AppRestartDelay", "5000"))
[void](Invoke-Nssm @("set", "SWU-Caddy", "Start", "SERVICE_AUTO_START"))
[void](Invoke-Nssm @("start", "SWU-Caddy"))
Start-Sleep -Seconds 3
$svcCaddy = Get-Service -Name SWU-Caddy -ErrorAction SilentlyContinue
if ($svcCaddy -and $svcCaddy.Status -eq "Running") {
    Write-Host "  [OK] SWU-Caddy laeuft (holt beim ersten externen Aufruf das Let's-Encrypt-Zertifikat, bis 60 s)." -ForegroundColor Green
} else {
    Write-Host "  [!] SWU-Caddy Status: '$($svcCaddy.Status)' - Log: logs\caddy_err.log" -ForegroundColor Red
    Write-Host "      Haeufigste Ursachen: DuckDNS nicht aktuell, Port 80/443 belegt." -ForegroundColor Yellow
}

# --- Verifikation: Health-Check beider Instanzen ------------------------------ #
# Vorher die alten Supervisor-Prozesse auf den Ports 8765/8443 beenden,
# sonst antworten die ALTEN Instanzen statt der neuen Dienste!
Write-Host ""
Write-Host "== Alte Supervisor-Prozesse beenden (Dienste uebernehmen) ==" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*app.web*' } |
    ForEach-Object {
        Write-Host "  Beende PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*supervisor.py*' } |
    ForEach-Object {
        Write-Host "  Beende Supervisor PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 2

Write-Host ""
Write-Host "== Health-Verifikation (bis 90 s) ==" -ForegroundColor Cyan
$ok = $true
foreach ($check in @(
    @{ port = 8765; url = "https://localhost:8765/api/health"; expect = "test" },
    @{ port = 8443; url = "https://localhost:8443/api/health"; expect = "prod" },
    @{ port = 8766; url = "https://localhost:8766/api/health"; expect = "dev" }
)) {
    $ready = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        & $VenvPython (Join-Path $ProjectDir "scripts\healthcheck.py") $check.url 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    }
    if ($ready) {
        Write-Host "  [OK] Port $($check.port): /api/health env=$($check.expect)" -ForegroundColor Green
    } else {
        Write-Host "  [!] Port $($check.port): Health nicht gruen!" -ForegroundColor Red
        $ok = $false
    }
}
Write-Host ""
if ($ok) { Write-Host "ERFOLG: Alle 3 Dienste installiert, beide Apps antworten." -ForegroundColor Green }
else     { Write-Host "FEHLER - Logs pruefen (logs\swu-app-*.log, logs\setup_services.log)." -ForegroundColor Red }

Stop-Transcript | Out-Null
