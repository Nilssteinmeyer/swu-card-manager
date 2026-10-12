# ============================================================================
#  SWU Card Manager - Gefuehrter Installer (setup.ps1)
#  12 Schritte, resumable (Fortschritt in config/setup_state.json),
#  idempotent (alles mehrfach ausfuehrbar), deutsche Erklaerungen.
# ============================================================================
#requires -version 5.1

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$StateFile  = Join-Path $ProjectDir "config\setup_state.json"
$ProdYaml   = Join-Path $ProjectDir "config\production.yaml"
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# ---------------------------------------------------------------- UI-Helfer
function Write-Step($n, $title) {
    Write-Host ""
    Write-Host ("=====================================================") -ForegroundColor DarkCyan
    Write-Host ("  Schritt $n : $title") -ForegroundColor Cyan
    Write-Host ("=====================================================") -ForegroundColor DarkCyan
}
function Write-Ok($msg)  { Write-Host ("  [OK] ") -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Info($msg){ Write-Host ("  [i]  ") -ForegroundColor Yellow -NoNewline; Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host ("  [!]  ") -ForegroundColor Red -NoNewline; Write-Host $msg -ForegroundColor Red }
function Pause-Key($msg) { Write-Host ""; Write-Host $msg -ForegroundColor DarkGray; Read-Host "  Enter druecken" | Out-Null }
function Ask-Yes($msg) {
    Write-Host ""
    $a = Read-Host ("  $msg [j/n]")
    return ($a -eq "j" -or $a -eq "J" -or $a -eq "y" -or $a -eq "")
}

# ---------------------------------------------------------------- State
function Load-State {
    if (Test-Path $StateFile) {
        try { return Get-Content $StateFile -Raw | ConvertFrom-Json } catch { }
    }
    return @{ steps = @{}; mode = "" }
}
function Save-State($state) {
    $dir = Split-Path -Parent $StateFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $state | ConvertTo-Json -Depth 5 | Set-Content $StateFile -Encoding UTF8
}
function Set-Step($state, $name, $status) {
    if (-not $state.steps) { $state | Add-Member -NotePropertyName steps -NotePropertyValue @{} -Force }
    $state.steps | Add-Member -NotePropertyName $name -NotePropertyValue @{ status = $status; at = (Get-Date -Format "yyyy-MM-dd HH:mm") } -Force
    Save-State $state
}
function Get-Step($state, $name) {
    if ($state.steps -and $state.steps.PSObject.Properties[$name]) { return $state.steps.$name.status }
    return ""
}

# ---------------------------------------------------------------- Elevation
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
# Elevation NUR noetig fuer den INTERNET-Pfad (Dienste, Firewall, NSSM).
# Der LOKAL-Pfad laeuft ohne Admin durch.

# ============================================================================ S1
$state = Load-State
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "     SWU CARD MANAGER - Gefuehrter Installer" -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Dieser Installer richtet deine Karten-Sammlungs-App ein." -ForegroundColor White
Write-Host ""
Write-Host "  Zwei Betriebsmodi:" -ForegroundColor White
Write-Host "    [1] LOKAL    - im Heimnetz (iPhone im WLAN)        ~2 Minuten" -ForegroundColor White
Write-Host "    [2] INTERNET - ueberall erreichbar (DuckDNS,      ~30-45 Minuten" -ForegroundColor White
Write-Host "                  Caddy, echtes Zertifikat, E-Mail)" -ForegroundColor White
Write-Host ""
Write-Host "  Du kannst jederzeit STRG+C druecken - der Installer merkt" -ForegroundColor DarkGray
Write-Host "  sich den Fortschritt und macht beim naechsten Start weiter." -ForegroundColor DarkGray

# ============================================================================ S2
Write-Step 2 "System-Check"
$checks = @(
    @{ n = "Python (.venv)";        ok = (Test-Path $VenvPython) },
    @{ n = "Tesseract OCR";         ok = (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe") },
    @{ n = "Config (production.yaml)"; ok = (Test-Path $ProdYaml) },
    @{ n = "Caddy (tools/caddy.exe)";  ok = (Test-Path (Join-Path $ProjectDir "tools\caddy.exe")) },
    @{ n = "NSSM (tools/nssm.exe)";    ok = (Test-Path (Join-Path $ProjectDir "tools\nssm.exe")) },
    @{ n = "Backup-Task (03:00)";     ok = [bool](Get-ScheduledTask -TaskName "SWU-Backup-Daily" -ErrorAction SilentlyContinue) },
    @{ n = "DuckDNS-Task (5 Min)";    ok = [bool](Get-ScheduledTask -TaskName "SWU-DuckDNS-Update" -ErrorAction SilentlyContinue) },
    @{ n = "Autostart-Task";          ok = [bool](Get-ScheduledTask -TaskName "SWU-Server-Autostart" -ErrorAction SilentlyContinue) }
)
$allOk = $true
foreach ($c in $checks) {
    if ($c.ok) { Write-Ok $c.n } else { Write-Err "$($c.n) FEHLT"; $allOk = $false }
}
if (-not $allOk) {
    Write-Err "Manche Komponenten fehlen. Der INTERNET-Modus braucht alle."
    Write-Info "Ohne Caddy/NSSM laeuft nur LOKAL. installation fortsetzen? [j]"
    $a = Read-Host
    if ($a -ne "j" -and $a -ne "J" -and $a -ne "") { exit 1 }
}

# ============================================================================ S3
Write-Step 3 "Betriebsmodus waehlen"
# RESUME: Wenn der Modus schon gewaelt wurde (z.B. nach Elevation-Neustart),
# direkt zum ersten offenen Schritt springen statt alles nochmal zu fragen.
$resumeMode = ""
if ($state.mode -eq "internet") {
    $pending = @("gmail","gmail_app","duckdns","dns","router","caddy","app_service","mode_switch") |
        Where-Object { (Get-Step $state $_) -ne "done" }
    if ($pending.Count -gt 0) {
        Write-Host ""
        Write-Ok "Fortsetzung erkannt: INTERNET-Modus, offene Schritte: $($pending.Count)"
        Write-Host "  Als naechstes: Schritt '$($pending[0])'" -ForegroundColor DarkGray
        if (Ask-Yes "  Weitermachen?") {
            $resumeMode = "internet"
            # Elevation fuer die restlichen Schritte (falls noch nicht elevated)
            if (-not (Test-Admin)) {
                Write-Info "Admin-Rechte noetig (Dienste, Firewall) - bitte im neuen Fenster zulassen."
                Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ErrorAction SilentlyContinue
                exit 0
            }
        }
    }
}
Write-Host ""
Write-Host "  [1] LOKAL    - iPhone im gleichen WLAN, App bleibt im Heimnetz." -ForegroundColor White
Write-Host "      Gut zum Scannen und Verwalten. Kein Internet-Account noetig." -ForegroundColor DarkGray
Write-Host "  [2] INTERNET - App von ueberall (z.B. https://swu-cardscanner.duckdns.org)" -ForegroundColor White
Write-Host "      Braucht: Gmail-Konto (E-Mail), DuckDNS (Domain), Router-Freigabe." -ForegroundColor DarkGray
Write-Host ""
$mode = $resumeMode
while ($mode -ne "1" -and $mode -ne "2" -and $mode -ne "internet") {
    $mode = Read-Host "  Deine Wahl [1/2] (Standard: 1)"
    if ($mode -eq "") { $mode = "1" }
}
if ($mode -eq "1") {
    $state.mode = "local"
    Set-Step $state "mode" "local"
    Save-State $state
    Write-Ok "LOKAL-Modus gesetzt."
    # Kurzes Abschliessen: LAN-IP ermitteln + Hinweise
    Write-Host ""
    Write-Host "  ==================== FERTIG (LOKAL) ====================" -ForegroundColor Green
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
        Select-Object -First 1).IPAddress
    if (-not $lanIp) { $lanIp = "192.168.178.74" }
    Write-Host ""
    Write-Host "  Am PC:     https://localhost:8765" -ForegroundColor White
    Write-Host "  Am iPhone: https://$lanIp`:8765" -ForegroundColor White
    Write-Host ""
    Write-Host "  Beim ersten iPhone-Besuch: Zertifikatswarnung -> 'Details anzeigen'" -ForegroundColor DarkGray
    Write-Host "  -> 'Einstellungen' -> 'Diese Website besuchen' (einmalig)." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Server starten (falls nicht laeuft): server.ps1 (Doppelklick)" -ForegroundColor DarkGray
    Write-Host "  Backup laeuft automatisch um 03:00 (Task SWU-Backup-Daily)." -ForegroundColor DarkGray
    Pause-Key "Enter zum Beenden"
    exit 0
}
$state.mode = "internet"
Set-Step $state "mode" "internet"
Save-State $state
Write-Ok "INTERNET-Modus gewaehlt - jetzt folgen die Schritte 4 bis 12."

# INTERNET-Pfad braucht Admin (Dienste, Firewall, NSSM): jetzt elevaten
if (-not (Test-Admin)) {
    Write-Info "Der INTERNET-Modus braucht Admin-Rechte (Dienste, Firewall)."
    Write-Info "Es oeffnet sich jetzt ein Fenster mit erhoehten Rechten - bitte zulassen."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ErrorAction SilentlyContinue
    exit 0
}
Write-Ok "Admin-Rechte vorhanden."


# ============================================================================ S4
Write-Step 4 "Gmail-Konto anlegen (E-Mail-Versand der App)"
Write-Host ""
Write-Host "  WARUM: Die App verschickt E-Mails fuer Passwort-Zuruecksetzen und" -ForegroundColor White
Write-Host "         Konto-Verifizierung. Dafuer braucht sie ein eigenes Gmail-Konto." -ForegroundColor White
Write-Host ""
if ((Get-Step $state "gmail") -eq "done") {
    Write-Ok "Bereits konfiguriert - ueberspringe."
} else {
    Write-Host "  WAS DU TUST (ca. 5 Min, Handy griffbereit):" -ForegroundColor Yellow
    Write-Host "   1. Oeffne https://accounts.google.com/signup" -ForegroundColor White
    Write-Host "   2. Nutzername: swuscanner  (wird swuscanner@gmail.com)" -ForegroundColor White
    Write-Host "   3. Passwort: waehle ein STARKES Passwort (notiere es dir!)" -ForegroundColor White
    Write-Host "   4. Google schickt einen SMS-Code an dein Handy -> eingeben" -ForegroundColor White
    Write-Host "   5. Name/Geburtstag: frei waehlbar (z.B. SWU Scanner, 1990)" -ForegroundColor White
    Write-Host ""
    Pause-Key "  Wenn das Konto angelegt ist: Enter hier."
    $yaml = Get-Content $ProdYaml -Raw | ConvertFrom-Yaml
    $yaml.mail.host = "smtp.gmail.com"
    $yaml.mail.port = 587
    $yaml.mail.user = "swuscanner@gmail.com"
    $yaml | ConvertTo-Yaml | Set-Content $ProdYaml -Encoding UTF8
    Set-Step $state "gmail" "done"
    Write-Ok "production.yaml: mail.host=smtp.gmail.com, user=swuscanner@gmail.com"
}

# ============================================================================ S5
Write-Step 5 "Gmail App-Passwort erzeugen (SMTP-Versand)"
Write-Host ""
Write-Host "  WARUM: Google erlaubt keinen Login mit dem normalen Passwort von" -ForegroundColor White
Write-Host "         anderen Programmen. Stattdessen gibt es App-Passwoerter:" -ForegroundColor White
Write-Host "         16-stellige Codes, nur fuer den E-Mail-Versand zustaendig." -ForegroundColor White
Write-Host ""
if ((Get-Step $state "gmail_app") -eq "done") {
    Write-Ok "Bereits konfiguriert - ueberspringe."
} else {
    Write-Host "  WAS DU TUST (ca. 3 Min):" -ForegroundColor Yellow
    Write-Host "   1. Oeffne https://myaccount.google.com (mit swuscanner eingeloggt)" -ForegroundColor White
    Write-Host "   2. Links: Sicherheit -> 2-Faktor-Authentifizierung -> AKTIVIEREN" -ForegroundColor White
    Write-Host "   3. Danach: App-Passwoerter -> Name SWU Card Manager -> Erstellen" -ForegroundColor White
    Write-Host "   4. Es erscheint ein 16-stelliger Code -> gleich hier eingeben" -ForegroundColor White
    Write-Host "      (nirgends speichern, nur hier einfuegen)" -ForegroundColor White
    Write-Host ""
    $retry = $true
    # App-Admin-Zugang fuer den Live-Test (nur im Speicher, wird NIE gespeichert)
    Write-Host ""
    Write-Host "  Fuer den Live-Test brauche ich deinen App-Admin-Login (nur fuer" -ForegroundColor White
    Write-Host "  diesen einen Test im Speicher, wird nicht gespeichert):" -ForegroundColor White
    $adminUser = Read-Host "  App-Benutzername (z.B. nils)"
    $secAdmin = Read-Host "  App-Passwort" -AsSecureString
    $bstrA = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secAdmin)
    $adminPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstrA)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstrA)
    while ($retry) {
        Write-Host "  Gmail-App-Passwort eingeben (Eingabe erscheint nicht am Bildschirm):" -ForegroundColor Yellow
        $sec = Read-Host "  Gmail-App-Passwort" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        [Environment]::SetEnvironmentVariable("SWU_SMTP_PASSWORD", $plain, "Machine")
        Set-Item Env:\SWU_SMTP_PASSWORD $plain
        $plain = $null
        Write-Host ""
        Write-Host "  Teste E-Mail-Versand live (Server muss laufen)..." -ForegroundColor Yellow
        $py = @'
import json, os, urllib.request, ssl, http.cookiejar as cj
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
jar = cj.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=ctx))
try:
    op.open(urllib.request.Request('https://localhost:8765/api/auth/login',
        data=json.dumps({'login': os.environ.get('SWU_TEST_ADMIN', ''),
                         'password': os.environ.get('SWU_TEST_ADMIN_PASS', '')}).encode(),
        headers={'Content-Type': 'application/json'}), timeout=30)
    r = op.open(urllib.request.Request('https://localhost:8765/api/admin/mail/test',
        data=json.dumps({'to': 'swuscanner@gmail.com'}).encode(),
        headers={'Content-Type': 'application/json'}), timeout=90)
    print(r.read().decode())
except Exception as e:
    print(json.dumps({'ok': False, 'error': str(e)}))
'@
        $env:SWU_TEST_ADMIN = $adminUser
        $env:SWU_TEST_ADMIN_PASS = $adminPass
        $mailTest = $py | & $VenvPython - 2>&1
        $env:SWU_TEST_ADMIN_PASS = $null
        if ("$mailTest" -match '"ok":\s*true' -or "$mailTest" -match '"mode":\s*"smtp"') {
            Write-Ok "SMTP-Test ERFOLGREICH - Mail unterwegs an swuscanner@gmail.com"
            Set-Step $state "gmail_app" "done"
            $retry = $false
        } else {
            Write-Err "SMTP-Test fehlgeschlagen. Antwort: $mailTest"
            Write-Info "Haeufige Ursachen: App-Passwort falsch, 2FA nicht aktiv,"
            Write-Info "oder Server muss neu gestartet werden (liest Env erst beim Start)."
            $retry = Ask-Yes "Erneut versuchen?"
        }
    }
    Write-Host ""
    Write-Host "  Checke dein Gmail-Postfach: Testmail von dir selbst angekommen?" -ForegroundColor Yellow
    Pause-Key "  Enter, wenn du die Mail siehst (oder zum Ueberspringen)"
}

# ============================================================================ S6
Write-Step 6 "DuckDNS-Domain anlegen (deine Internet-Adresse)"
Write-Host ""
Write-Host "  WARUM: Damit du die App ueber das Internet erreichst, braucht sie" -ForegroundColor White
Write-Host "         eine feste Adresse. DuckDNS schenkt dir eine gratis Subdomain." -ForegroundColor White
Write-Host ""
if ((Get-Step $state "duckdns") -eq "done") {
    Write-Ok "Bereits konfiguriert - ueberspringe."
} else {
    Write-Host "  WAS DU TUST (ca. 3 Min):" -ForegroundColor Yellow
    Write-Host "   1. Oeffne https://www.duckdns.org -> Login with Google" -ForegroundColor White
    Write-Host "      (mit swuscanner@gmail.com anmelden)" -ForegroundColor White
    Write-Host "   2. Ins Feld add domain: swu-cardscanner -> add domain" -ForegroundColor White
    Write-Host "   3. OBEN steht dein Token (lange Zeichenkette) -> kopieren" -ForegroundColor White
    Write-Host ""
    $retry6 = $true
    while ($retry6) {
        $sec2 = Read-Host "  DuckDNS-Token" -AsSecureString
        $bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec2)
        $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr2)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2)
        $tokenFile = Join-Path $ProjectDir "config\duckdns_token"
        $plain2 | Set-Content $tokenFile -Encoding UTF8 -NoNewline
        $plain2 = $null
        Write-Host ""
        Write-Host "  Teste DuckDNS-Update live..." -ForegroundColor Yellow
        $dd = & $VenvPython (Join-Path $ProjectDir "scripts\duckdns_update.py") 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "DuckDNS OK: swu-cardscanner.duckdns.org zeigt auf deine IP"
            Set-Step $state "duckdns" "done"
            $retry6 = $false
        } else {
            Write-Err "DuckDNS fehlgeschlagen: $dd"
            Write-Info "Token falsch? Subdomain anders geschrieben (genau swu-cardscanner)?"
            $retry6 = Ask-Yes "Erneut versuchen?"
            if (-not $retry6) { Write-Info "Ueberspringe - kann spaeter nachgeholt werden." }
        }
    }
}

# ============================================================================ S7
Write-Step 7 "DNS-Check (zeigt deine Domain auf diesen PC?)"
if ((Get-Step $state "dns") -eq "done") {
    Write-Ok "Bereits geprueft - ueberspringe."
} else {
    $pubIp = ""
    try { $pubIp = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 20).Content.Trim() } catch { }
    $domIp = ""
    try { $domIp = (Resolve-DnsName "swu-cardscanner.duckdns.org" -ErrorAction Stop | Where-Object Type -eq "A" | Select-Object -First 1).IPAddress } catch { }
    Write-Host "  Deine oeffentliche IP: $pubIp" -ForegroundColor White
    Write-Host "  Domain zeigt auf:      $domIp" -ForegroundColor White
    if ($pubIp -and $domIp -and ($pubIp -eq $domIp)) {
        Write-Ok "DNS synchron - die Domain zeigt auf dich."
        Set-Step $state "dns" "done"
    } else {
        Write-Info "Noch nicht synchron. Der DuckDNS-Task aktualisiert alle 5 Minuten."
        $recheck = $true
        while ($recheck) {
            Pause-Key "  Enter = erneut pruefen (oder warten)"
            try { $domIp = (Resolve-DnsName "swu-cardscanner.duckdns.org" -ErrorAction Stop | Where-Object Type -eq "A" | Select-Object -First 1).IPAddress } catch { }
            Write-Host "  Domain zeigt auf: $domIp (soll: $pubIp)" -ForegroundColor White
            if ($pubIp -eq $domIp) { Write-Ok "Jetzt synchron."; Set-Step $state "dns" "done"; $recheck = $false }
            else { $recheck = Ask-Yes "Nochmal pruefen?" }
        }
    }
}

# ============================================================================ S8
Write-Step 8 "Router-Portfreigabe (80 + 443 auf diesen PC)"
Write-Host ""
Write-Host "  WARUM: Damit Anfragen aus dem Internet deinen PC erreichen, muss" -ForegroundColor White
Write-Host "         der Router die Ports 80 und 443 an diesen PC weiterleiten." -ForegroundColor White
Write-Host ""
if ((Get-Step $state "router") -eq "done") {
    Write-Ok "Bereits erledigt - ueberspringe."
} else {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.*" } | Select-Object -First 1).IPAddress
    Write-Host "  WAS DU TUST (ca. 5 Min, im Router-Menue):" -ForegroundColor Yellow
    Write-Host "   1. Oeffne http://fritz.box (oder deine Router-Adresse)" -ForegroundColor White
    Write-Host "   2. Internet -> Freigaben -> Geraet fuer Freigaben hinzufuegen" -ForegroundColor White
    Write-Host "   3. Waehle diesen PC aus (IP: $lanIp)" -ForegroundColor White
    Write-Host "   4. Neue Freigabe -> Port 80 und Port 443 (TCP) -> aktivieren" -ForegroundColor White
    Write-Host "   TIPP: Gib diesem PC im Router eine FESTE IP (sonst kippt die Freigabe)" -ForegroundColor Yellow
    Write-Host ""
    Pause-Key "  Wenn die Freigaben stehen: Enter hier."
    Set-Step $state "router" "done"
    Write-Info "Finale Erreichbarkeit wird nach Schritt 10 per LTE-Test geprueft."
}

# ============================================================================ S9
Write-Step 9 "Caddy-Dienst einrichten (echtes SSL-Zertifikat)"
if ((Get-Step $state "caddy") -eq "done") {
    Write-Ok "Bereits installiert - ueberspringe."
} else {
    $nssm = Join-Path $ProjectDir "tools\nssm.exe"
    $caddy = Join-Path $ProjectDir "tools\caddy.exe"
    $caddyfile = Join-Path $ProjectDir "config\Caddyfile"
    $existing = Get-Service -Name SWU-Caddy -ErrorAction SilentlyContinue
    if ($existing) {
        & $nssm stop SWU-Caddy 2>$null | Out-Null
        & $nssm remove SWU-Caddy confirm 2>$null | Out-Null
        Start-Sleep -Seconds 2
    }
    & $nssm install SWU-Caddy $caddy "run --config `"$caddyfile`"" 2>&1 | Out-Null
    & $nssm set SWU-Caddy AppDirectory $ProjectDir | Out-Null
    & $nssm set SWU-Caddy AppStdout (Join-Path $ProjectDir "logs\caddy.log") | Out-Null
    & $nssm set SWU-Caddy AppStderr (Join-Path $ProjectDir "logs\caddy_err.log") | Out-Null
    & $nssm set SWU-Caddy Start SERVICE_AUTO_START | Out-Null
    & $nssm start SWU-Caddy | Out-Null
    Start-Sleep -Seconds 5
    $svc = Get-Service -Name SWU-Caddy -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Ok "Caddy-Dienst laeuft (SWU-Caddy, Autostart bei Boot)."
        Write-Info "Zertifikat: Caddy holt es beim ersten externen Aufruf (bis 60 s)."
        Set-Step $state "caddy" "done"
    } else {
        Write-Err "Caddy-Dienst startete nicht (Status: $($svc.Status))."
        Write-Info "Haeufigste Ursache: Port 443/80 belegt oder Router-Freigabe fehlt."
        Write-Info "Log: logs\caddy_err.log"
        $fix9 = Ask-Yes "Problem pruefen und erneut starten?"
        if ($fix9) {
            Pause-Key "  Nach Fix: Enter (startet Dienst erneut)"
            & $nssm start SWU-Caddy | Out-Null
            Start-Sleep -Seconds 3
            $svc = Get-Service SWU-Caddy -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -eq "Running") { Write-Ok "Jetzt laeuft er."; Set-Step $state "caddy" "done" }
            else { Write-Err "Immer noch nicht - Installer setzt fort; spaeter erneut starten." }
        }
    }
}

# ============================================================================ S10
Write-Step 10 "App-Dienst einrichten (localhost:8443 hinter Caddy)"
if ((Get-Step $state "app_service") -eq "done") {
    Write-Ok "Bereits installiert - ueberspringe."
} else {
    $nssm = Join-Path $ProjectDir "tools\nssm.exe"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*app.web*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    $existing = Get-Service -Name SWU-App -ErrorAction SilentlyContinue
    if ($existing) {
        & $nssm stop SWU-App 2>$null | Out-Null
        & $nssm remove SWU-App confirm 2>$null | Out-Null
        Start-Sleep -Seconds 2
    }
    & $nssm install SWU-App $VenvPython "-m app.web --host 127.0.0.1 --port 8443" 2>&1 | Out-Null
    & $nssm set SWU-App AppDirectory $ProjectDir | Out-Null
    & $nssm set SWU-App AppStdout (Join-Path $ProjectDir "logs\app_service.log") | Out-Null
    & $nssm set SWU-App AppStderr (Join-Path $ProjectDir "logs\app_service_err.log") | Out-Null
    & $nssm set SWU-App Start SERVICE_AUTO_START | Out-Null
    & $nssm start SWU-App | Out-Null
    Write-Host "  Warte auf App-Start (Engine-Warmup, bis 60 s)..." -ForegroundColor Yellow
    $ok10 = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $l = Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue
        if ($l) { $ok10 = $true; break }
    }
    if ($ok10) {
        Write-Ok "App-Dienst laeuft auf 127.0.0.1:8443 (SWU-App, Autostart bei Boot)."
        Set-Step $state "app_service" "done"
    } else {
        Write-Err "App-Dienst antwortet nicht auf 8443 - siehe logs\app_service_err.log"
        Pause-Key "  Nach Pruefung: Enter (kann spaeter repariert werden)"
    }
}

# ============================================================================ S11
Write-Step 11 "Internet-Modus aktivieren (+ Firewall abschliessen)"
if ((Get-Step $state "mode_switch") -eq "done") {
    Write-Ok "Bereits umgeschaltet - ueberspringe."
} else {
    Write-Host "  Pruefe Voraussetzungen und schalte um..." -ForegroundColor Yellow
    $sw = & $VenvPython (Join-Path $ProjectDir "scripts\switch_mode.py") internet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Umschalten fehlgeschlagen:"
        $sw | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        Pause-Key "  Nach Fehlerbehebung: Installer einfach neu starten (macht weiter)"
        exit 2
    }
    $sw | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    & (Join-Path $ProjectDir "tools\nssm.exe") restart SWU-App 2>$null | Out-Null
    Start-Sleep -Seconds 3
    $fwRules = Get-NetFirewallRule -Direction Inbound -Enabled True -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "*python*" }
    foreach ($r in $fwRules) {
        try {
            Set-NetFirewallRule -DisplayName $r.DisplayName -RemoteAddress 127.0.0.1 -ErrorAction Stop
            Write-Ok "Firewall: '$($r.DisplayName)' nur noch localhost"
        } catch {
            Write-Info "Firewall-Regel '$($r.DisplayName)' nicht anpassbar - manuell pruefen"
        }
    }
    Set-Step $state "mode_switch" "done"
    Write-Ok "Internet-Modus AKTIV: App nur auf localhost, Caddy ist der Eingang."
    Write-Ok "Pentest-Fund M-1 ist damit geschlossen (0.0.0.0-Bindung beendet)."
}

# ============================================================================ S12
Write-Step 12 "Abschluss-Report + Erreichbarkeits-Test"
Write-Host ""
$pubDomain = "https://swu-cardscanner.duckdns.org"
Write-Host "  Zertifikat/Erreichbarkeitstest (erster Aufruf kann 60 s dauern)..." -ForegroundColor Yellow
$certOk = $false
try {
    $req = [Net.HttpWebRequest]::Create("$pubDomain/login")
    $req.Timeout = 60000
    $resp = $req.GetResponse()
    $resp.Close()
    $certOk = $true
} catch [Net.WebException] {
    if ($_.Exception.Response) { $certOk = $true } else { $certOk = $false }
} catch { $certOk = $false }
if ($certOk) {
    Write-Ok "HTTPS erreichbar: $pubDomain"
} else {
    Write-Info "Von innen oft nicht pruefbar (NAT-Loopback). Der finale Test:"
    Write-Host "  >>> iPHONE NEHMEN, WLAN AUS, oeffne $pubDomain" -ForegroundColor Yellow
    Write-Host "      Login-Seite sichtbar = ALLES READY." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  ==================== GO-LIVE CHECKLISTE ====================" -ForegroundColor Green
Write-Host ""
$report = @(
    @{ s = "Gmail (SMTP)";        d = (Get-Step $state "gmail") },
    @{ s = "App-Passwort + Test"; d = (Get-Step $state "gmail_app") },
    @{ s = "DuckDNS-Domain";      d = (Get-Step $state "duckdns") },
    @{ s = "DNS-Sync";            d = (Get-Step $state "dns") },
    @{ s = "Router-Freigabe";     d = (Get-Step $state "router") },
    @{ s = "Caddy-Dienst";        d = (Get-Step $state "caddy") },
    @{ s = "App-Dienst (8443)";   d = (Get-Step $state "app_service") },
    @{ s = "Internet-Modus";      d = (Get-Step $state "mode_switch") }
)
foreach ($r in $report) {
    if ($r.d -eq "done") { Write-Host ("  [X] " + $r.s) -ForegroundColor Green }
    else { Write-Host ("  [ ] " + $r.s + "  (offen)") -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Deine Adresse:  $pubDomain" -ForegroundColor White
Write-Host "  Backup:        automatisch 03:00 (Task SWU-Backup-Daily)" -ForegroundColor DarkGray
Write-Host "  E-Mail:        Reset-Mails gehen ins swuscanner@gmail.com-Postfach" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  WICHTIG:" -ForegroundColor Yellow
Write-Host "  - Nach der Sicherheit-Rotation: neu einloggen (alte Sessions unguelstig)" -ForegroundColor Yellow
Write-Host "  - Empfehlung: GitHub-Repo auf privat stellen (docs/PENTEST_REPORT.md)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Details: docs/OPERATIONS.md  |  Stoeerungen: Abschnitt 7 dort" -ForegroundColor DarkGray
Write-Host ""
Pause-Key "Enter zum Beenden - GLUECKWUNSCH, Go-Live geschafft!"
exit 0
