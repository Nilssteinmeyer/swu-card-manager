# MASTERPLAN — SWU Card Manager: Zwei-Modus-Deployment + geführter Installer

> **Für Hermes:** Mit subagent-driven-development umsetzen, Task für Task. Plan-Modus aktiv — dieser Plan wird in diesem Turn NICHT ausgeführt.

**Ziel:** Der SWU Card Manager bekommt zwei saubere, getrennte Betriebsmodi (LOKAL im Heimnetz / INTERNET über DuckDNS+Caddy) und einen **geführten .bat-Installer**, der jeden offenen Punkt interaktiv, mit deutschen Erklärungen, Schritt für Schritt abarbeitet — abbruchsfest, wieder aufnehmbar (resumable), idempotent.

**Architektur:** Ein neuer, zentraler `deployment_mode`-Schalter in `config/production.yaml` (`local` | `internet`) steuert alles: Bind-Host, Ports, Firewall, Tasks, Mail, Domain-Checks. Ein **einziges** PowerShell-Installer-Skript (`installer/setup.ps1`, gestartet via `installer.bat`) führt durch Prüfaufgaben + interaktive Eingaben; der Fortschritt wird in `config/setup_state.json` persistiert, sodass jeder Durchlauf exakt dort weitermacht, wo er aufhörte. Alles bereits Gebaute (Caddy v2.11.7 in `tools/`, NSSM 2.24 in `tools/`, 3 Scheduled Tasks, SMTP-Verkabelung, DuckDNS-Updater) wird wiederverwendet — nichts doppelt bauen.

**Tech Stack:** Windows Batch (nur Launcher), PowerShell 5.1 (Installer-Logik), Python 3.11 (Checks + Modus-Wechsel), YAML-Config, Caddy 2.11.7, NSSM 2.24, Windows-Aufgabenplanung, DuckDNS-API, Gmail SMTP.

---

## Teil A — Grundlage: Zwei saubere Betriebsmodi

### Warum zwei Modi?
Die App läuft heute mit `--host 0.0.0.0 --port 8765` (LAN-Modus, direkt vom iPhone erreichbar). Für den Internetbetrieb gilt: App nur noch auf `127.0.0.1:8443`, Caddy als einziger Eingang auf 443. Diese zwei Zustände müssen **explizit, prüfbar und umschaltbar** werden — ohne manuelles YAML-Editieren.

### Task A1: `deployment_mode` in production.yaml + Config-Loader

**Ziel:** EIN Schalter steuert den Betriebsmodus.

**Dateien:**
- Modify: `config/production.yaml` (Sektion `server:`)
- Modify: `server.ps1` (liest künftig `app_host` UND `app_port`; Port ist aktuell hardcoded 8765)
- Create: `tests/test_config_modes.py`

**Umsetzung:**
```yaml
# config/production.yaml — Sektion server:
server:
  deployment_mode: "local"     # "local" (Heimnetz, Standard) | "internet" (Caddy+DuckDNS)
  app_host: "0.0.0.0"         # local: 0.0.0.0 | internet: 127.0.0.1
  app_port: 8765               # local: 8765 | internet: 8443
  public_domain: "swu-cardscanner.duckdns.org"
```

`server.ps1`: `$port` aus `$yaml.server.app_port` lesen (Fallback 8765), `$bindHost` wie vorhanden.

**Verifikation:** `python -c "import yaml; print(yaml.safe_load(open('config/production.yaml'))['server']['deployment_mode'])"` → `local`. `server.ps1` startet weiterhin auf 8765.

**Commit:** `feat(config): deployment_mode switch for local/internet operation`

### Task A2: Modus-Wechsel als geprüfter Einzelschritt (`scripts/switch_mode.py`)

**Ziel:** Umschalten nur über dieses Skript (nie manuell YAML editieren), mit Abhängigkeitsprüfung und klaren deutschen Fehlertexten.

**Dateien:**
- Create: `scripts/switch_mode.py`
- Modify: `tests/test_config_modes.py` (erweitern)

**Logik:**
```python
def switch_to(mode):
    if mode == "internet":
        require(config/duckdns_token existiert)        # sonst Exit 2 + Anleitung
        require(mail.host in production.yaml gesetzt)  # SMTP für Passwort-Reset
        require(tools/caddy.exe existiert)
        require(Domain löst auf die öffentliche IP auf)  # nslookup + api.ipify.org
    if mode == "local":
        pass  # Internet-Dienste stoppt der Installer beim Wechsel zurück (S11 invers)
    # production.yaml schreiben (YAML round-trip, Kommentare erhalten wo möglich):
    server.deployment_mode = mode
    server.app_host = "0.0.0.0" if mode == "local" else "127.0.0.1"
    server.app_port = 8765 if mode == "local" else 8443
    print(Neustart-Hinweis: "server.ps1 -Restart" bzw. Dienst-Neustart)
```

**Verifikation:** Unit-Test mit Temp-YAML: `switch local→internet` ohne DuckDNS-Token → Exit 2 mit Meldung; mit Fake-Token → YAML korrekt geändert (mode/host/port).

**Commit:** `feat(deploy): switch_mode.py — geprüfter Wechsel local<->internet`

### Task A3: `.gitignore`-Schutz
`config/setup_state.json` ergänzen (Installer-Fortschritt, keine Secrets, aber maschinenspezifisch). `config/duckdns_token` ist bereits geschützt — prüfen.

**Commit:** `chore: ignore installer state file`

---

## Teil B — Der geführte Installer (`installer.bat` → `installer/setup.ps1`)

### Design-Prinzipien
1. **Resumable:** Fortschritt in `config/setup_state.json` (`{"steps": {"gmail": "done", "duckdns": "pending", ...}}`, nur Statuse + Zeitstempel, niemals Werte/Secrets). Neustart des Installers = Weiter am letzten offenen Punkt; Installer zeigt „Schritt X von N erledigt" und fragt „Weiter bei Schritt X?".
2. **Idempotent:** Jeder Schritt prüft zuerst den Ist-Zustand („Gmail schon konfiguriert?" → überspringen mit grünem Hinweis). Kein Schritt zerstört Bestehendes; zweimaliges Ausführen = gleiches Ergebnis, keine doppelten Tasks/Dienste.
3. **Deutsch mit Erklärung:** Jeder Schritt erklärt WARUM → WAS → Aktion/Eingabe → Verifikation mit Häkchen. Kein Fachjargon.
4. **Farbige Terminal-UI:** grün = OK, gelb = Hinweis, rot = Fehler mit Lösungsweg (`Write-Host -ForegroundColor`, PS 5.1, kein Modul-Bedarf).
5. **Bestätigungspflicht:** Schritte, die Extern-Konten/Netzwerk ändern, fragen explizit `j`.
6. **Abbruch-sicher:** STRG+C verliert nichts — State wird nach jedem Schritt gespeichert.
7. **Keine Passwörter im Klartext:** SMTP-App-Passwort per `Read-Host -AsSecureString` → `[Environment]::SetEnvironmentVariable('SWU_SMTP_PASSWORD', $plain, 'Machine')` + Prozess-Env. Nie in Datei, nie im Log, nie im State.
8. **Admin-Rechte:** Installer prüft und elevatet sich selbst (`Start-Process -Verb RunAs`) — nötig für Tasks/NSSM/Firewall. Ohne Admin läuft er im Nur-Prüfen-Modus und sagt, was fehlt.

### Installer-Struktur — 12 Schritte

```
installer.bat  → powershell installer\setup.ps1 (elevated, deutsches UI)
   │
   ├─ S1  Willkommen + Überblick (12 Schritte, Zeitaufwand: LOKAL ~2 Min / INTERNET ~30-45 Min)
   ├─ S2  System-Check (Python 3.11, .venv, Tesseract, CUDA optional, freier Disk,
   │      Scheduled Tasks SWU-*, Tools caddy.exe/nssm.exe) — alles da → lauter Grüne
   ├─ S3  Modus-Wahl: [1] LOKAL (Standard, fertig nach S3) | [2] INTERNET (→ S4-S12)
   │
   ├─ S4  Gmail-Konto: erklärt Anmeldung (accounts.google.com/signup →
   │      swuscanner@gmail.com → SMS-Code); Nutzer macht es im Browser, Installer
   │      wartet („Enter, wenn fertig"); danach trägt er mail.host/port/user in
   │      production.yaml ein (smtp.gmail.com / 587 / swuscanner@gmail.com)
   ├─ S5  Gmail App-Passwort: erklärt 2FA + App-Passwort-Erzeugung (Sicherheit →
   │      2FA → App-Passwörter → „SWU Card Manager"); SecureString-Eingabe →
   │      Machine-Env SWU_SMTP_PASSWORD; Live-Test: POST /api/admin/mail/test
   │      → Mail im Gmail-Postfach bestätigen lassen; Fehlerdiagnose bei Block
   ├─ S6  DuckDNS: erklärt duckdns.org (Google-Login, gratis); Nutzer legt
   │      Subdomain „swu-cardscanner" an, kopiert Token; SecureString-Eingabe →
   │      config/duckdns_token; Live-Test: python scripts/duckdns_update.py → „OK"
   ├─ S7  DNS-Check: nslookup swu-cardscanner.duckdns.org == öffentliche IP
   │      (api.ipify.org)? Abweichend → DuckDNS-Task läuft 5-Minütlich → warten,
   │      Taste zum Wiederholen
   ├─ S8  Router-Portfreigabe: erklärt fritz.box → Internet → Freigaben →
   │      Ports 80+443 → diesen PC (192.168.178.74); Nutzer macht es; Verifikation:
   │      LTE-iPhone-Test (WLAN aus) → Login-Seite sichtbar? (NAT-Loopback macht
   │      interne Tests unzuverlässig — der LTE-Test ist die Wahrheit)
   ├─ S9  Caddy-Dienst: nssm install SWU-Caddy (tools/caddy.exe run --config
   │      config/Caddyfile); Start; Zertifikats-Check: curl https://DOMAIN ohne
   │      -k → Let's Encrypt gültig (kein Selbstsigner mehr)
   ├─ S10 App-Dienst: nssm install SWU-App (.venv python -m app.web --host
   │      127.0.0.1 --port 8443); Health-Check localhost:8443 → 302/200
   ├─ S11 Internet-Modus aktivieren: python scripts/switch_mode.py internet
   │      → Dienste neu starten; Firewall-Audit: python.exe-Allow-Regeln auf
   │      localhost beschränken, nur 80/443 bleibt offen → Pentest-Fund M-1
   │      geschlossen
   └─ S12 Abschluss-Report: Schritt-Tabelle mit Status+Zeitstempel, Test-URL,
          Backup-Task-Status, erste Login-Erinnerung (SECRET_KEY-Rotation → alte
          Sessions weg), Hinweis „Repo public → Empfehlung: privat stellen",
          Verweis auf docs/OPERATIONS.md
```

### Task B1: `installer.bat` (Launcher)
Minimal und robust:
```bat
@echo off
REM SWU Card Manager — Gefuehrter Installer (lokal/internet)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "installer\setup.ps1"
pause
```
Alle Logik in setup.ps1; der Launcher startet nur (Elevation übernimmt setup.ps1 selbst).

**Commit:** `feat(installer): guided installer launcher`

### Task B2: `installer/setup.ps1` — Grundgerüst + S1–S3
**Datei:** Create `installer/setup.ps1`
- `#requires -version 5.1`; Elevation-Check + Selbst-Elevation mit Argument-Durchreiche
- UI-Helfer: `Write-Step/Write-Ok/Write-Warn/Write-Err` (Farben + Trennlinien)
- State: `Load-State`/`Save-State` (`config/setup_state.json`)
- S1 Willkommen (Überblick, Zeitaufwand je Modus)
- S2 System-Check (Python, .venv, Tesseract, Tasks, Disk, Tools) — je Zeile grün/gelb
- S3 Modus-Wahl (Menü; Auswahl persistiert; LOKAL → Sprung zu S12-Kurzabschluss)

**Verifikation:** Doppelklick `installer.bat` → deutsches farbiges UI, State-Datei wird angelegt, Modus wählbar.

**Commit:** `feat(installer): setup.ps1 framework + steps 1-3`

### Task B3: S4–S5 Gmail + SMTP (interaktiv)
Der Installer kann Google-Konten nicht selbst anlegen (Captcha, SMS) — er erklärt und wartet:
- S4: Anleitung in 6 Klicks → „Ich warte hier. Enter, wenn das Konto steht." → danach YAML-Eintrag (host/port/user) + Validierung (YAML parst, mail.host != leer)
- S5: 2FA + App-Passwort-Anleitung → SecureString → Machine-Env → SMTP-Live-Test über den laufenden Server (`/api/admin/mail/test`, Admin-Login nils) → „Checke dein Gmail-Postfach — Mail da? [j/n]"
- Fehlerdiagnose: App-Passwort falsch? 2FA nicht aktiv? Google blockt? → klare deutsche Meldungen + Wiederholungsschleife

**Verifikation:** production.yaml mail-Sektion gefüllt; Env gesetzt; Testmail angekommen.

**Commit:** `feat(installer): gmail + app password steps with live SMTP test`

### Task B4: S6–S7 DuckDNS (interaktiv)
- S6: duckdns.org-Erklärung (Google-Login, gratis, Subdomain) → Nutzer legt `swu-cardscanner` an, kopiert Token → SecureString → `config/duckdns_token` → Live-Test `duckdns_update.py` → „OK" grün
- S7: `nslookup`-Abgleich mit api.ipify.org-IP; DuckDNS-Task (alle 5 Min) holt nach → Wiederholungsschleife bis grün

**Verifikation:** `nslookup swu-cardscanner.duckdns.org` == öffentliche IP.

**Commit:** `feat(installer): duckdns steps with token file + dns check`

### Task B5: S8 Router-Portfreigabe (semi-automatisch)
- Erklärt: fritz.box → Internet → Freigaben → Ports **80+443** → PC (192.168.178.74, im Router als feste IP vergeben lassen)
- Warte-Prompt: „Enter, wenn die Freigabe steht."
- Verifikation: **LTE-iPhone-Test** (WLAN aus → https://swu-cardscanner.duckdns.org → Login-Seite?). Interne Port-Checks sind wegen NAT-Loopback unzuverlässig — deshalb ist der LTE-Test die maßgebliche Verifikation. Falls der Test vor S9/S10 noch nichts zeigt (Caddy läuft ja noch nicht), bleibt S8 auf „needs-recheck" und wird nach S10 final bestätigt (Installer merkt sich das).

**Verifikation:** iPhone im LTE: Login-Seite über die Domain erreichbar (nach S9/S10).

**Commit:** `feat(installer): router port forwarding step with LTE verification`

### Task B6: S9–S10 Dienste (Caddy + App via NSSM)
- **Caddy-Dienst:** `tools/nssm.exe install SWU-Caddy "…\tools\caddy.exe" "run --config …\config\Caddyfile --adapter caddyfile"`; AppStdout/Err → `logs/`; `Start = SERVICE_AUTO_START`; `nssm start SWU-Caddy`. Zertifikats-Verifikation: `curl https://swu-cardscanner.duckdns.org` **ohne** `-k` → gültiges Let's-Encrypt-Zertifikat.
- **App-Dienst:** `nssm install SWU-App "…\.venv\Scripts\python.exe" "-m app.web --host 127.0.0.1 --port 8443"`; WorkingDirectory = Projekt; Health-Check `Test-NetConnection 127.0.0.1 -Port 8443`.
- **Koexistenz-Regel (fixiert):** INTERNET-Modus = App als NSSM-Dienst `SWU-App` (Boot ohne Login, Crash-Restart). LOKAL-Modus = bleibt beim Logon-Task `SWU-Server-Autostart` (kein Admin, Desktop-Kontext für CUDA). `server.ps1` erkennt per Port-Check laufende Instanzen → keine Doppel-Instanz. Der Installer deaktiviert den Logon-Task NICHT (server.ps1 beendet sich friedlich, wenn der Dienst läuft).
- Env-Anforderung: `SWU_SMTP_PASSWORD` ist Machine-Env → auch für den SYSTEM-Dienst lesbar.

**Verifikation:** `nssm status SWU-Caddy`/`SWU-App` = `SERVICE_RUNNING`; `curl` ohne `-k` erfolgreich; Reboot-Test (einmalig am Ende, mit Nutzer-Bestätigung): beide Dienste starten ohne Login.

**Commit:** `feat(installer): NSSM services for Caddy and app (internet mode)`

### Task B7: S11–S12 Modus-Wechsel + Abschluss
- S11: `python scripts/switch_mode.py internet` (Voraussetzungen werden dort geprüft) → Dienste neu starten → Firewall-Audit: bestehende „python.exe Allow"-Inbound-Regeln auf Localhost/8443 beschränken; nur 80/443 bleibt extern offen → **Pentest M-1 geschlossen**
- S12: Abschluss-Report (Schritt-Tabelle, Test-URL, Backup-Status, Login-Hinweis wegen SECRET_KEY-Rotation, Repo-privat-Empfehlung, OPERATIONS.md-Verweis) + finaler Verifikations-Block: Admin-Login von außen (LTE), Dashboard 200, ein Scan vom iPhone über die Domain

**Verifikation:** `GET https://swu-cardscanner.duckdns.org/api/dashboard` ohne Session → 302 auf Login; Scan über die Domain funktioniert.

**Commit:** `feat(installer): mode switch + final report; pentest M-1 closed`

---

## Teil C — Dokumentation

### Task C1: README-Aufteilung
`README.md` bekommt zwei klare Installations-Abschnitte:
1. **LOKAL (Heimnetz):** `installer.bat` → Modus LOKAL → fertig (~2 Min). Alternativ `server.ps1` direkt. iPhone: `https://192.168.178.74:8765` (Zertifikatswarnung einmalig akzeptieren — Übergangszustand).
2. **INTERNET:** `installer.bat` → Modus INTERNET → 12 geführte Schritte (~30–45 Min, Gmail/DuckDNS/Router inklusive) → erreichbar unter `https://swu-cardscanner.duckdns.org` mit echtem Zertifikat.

### Task C2: GoLive_Accounts.md (Desktop) aktualisieren
Nach Abschluss Status-Spalten pflegen (⏳→✅) bzw. Hinweis, dass der Installer die Checkliste jetzt abdeckt.

---

## Teil D — Testkonzept

| # | Test | Was geprüft wird |
|---|---|---|
| D1 | Installer-Smoke LOKAL | S1–S3 komplett durchklicken → UI, State-Datei, System-Checks, Kurzabschluss mit LAN-URL |
| D2 | Installer-Smoke INTERNET | Der echte Go-Live-Lauf mit echten Gmail/DuckDNS/Router-Inputs — der Installer IST der Go-Live |
| D3 | Resumable | Mitten im Lauf STRG+C → Neustart → „Weiter bei Schritt X?" springt korrekt |
| D4 | Idempotenz | Zweiter kompletter Lauf → keine doppelten Tasks/Dienste, keine Fehler, gleicher Endzustand |
| D5 | Unit-Tests | `pytest tests/ --ignore=tests/test_recognition_accuracy.py` weiterhin 141 passed (switch_mode-Tests kommen dazu) |
| D6 | E2E | 9 Browser-E2E grün nach Modus-Wechsel (Login, Dashboard, Scanner über die neue Basis-URL) |
| D7 | Reboot-Test | Einmalig am Go-Live-Tag mit Nutzer-Bestätigung: Dienste starten ohne Login, Domain erreichbar |

---

## Risiken & Trade-offs

| Risiko | Bewertung | Gegenmaßnahme |
|---|---|---|
| `SWU_SMTP_PASSWORD` als Machine-Env (für SYSTEM-Dienst nötig) | Akzeptiert | Nur so liest der Dienst die Variable; alternativ DPAPI+Dienst-User = deutlich komplexer. Env ist nur für Prozesse dieser Maschine lesbar. |
| NAT-Loopback (Domain von innen aufrufen) funktioniert je nach Router nicht | Mittel | Von außen: Domain (LTE-Test). Von innen: LOKAL-URL bzw. localhost. In OPERATIONS.md dokumentiert. |
| Google blockt SMTP-Login | Mittel | App-Passwort ist der offizielle Weg (2FA Pflicht); Installer S5 hat Diagnose + Wiederholungsschleife |
| Zwei Start-Wege (Logon-Task + NSSM-Dienst) | Niedrig | server.ps1 Port-Check verhindert Doppel-Instanzen; Koexistenz-Regel in B6 fixiert |
| Domain veraltet bei IP-Wechsel | Niedrig | DuckDNS-Task alle 5 Min + Router-DDNS als erste Linie |
| Repo öffentlich (Pentest N-1) | Niedrig | Empfehlung „privat" im S12-Report; Secrets liegen bereits außerhalb des Repos |
| State-Datei | Kein Risiko | `setup_state.json` enthält nur Statuse + Zeitstempel, niemals Werte/Secrets |

## Im Plan entschieden (keine offenen Fragen mehr)
- Caddy intern → App auf `127.0.0.1:8443` (Caddyfile steht bereits so). ✔
- LOKAL = Logon-Task + Port 8765; INTERNET = NSSM-Dienste + Port 8443. ✔
- Externe Verifikation ohne Drittanbieter-Website: LTE-iPhone-Test (S8, S12). ✔
- UPnP-Fallback für Router-Freigabe: **bewusst verworfen** — manuelle Freigabe ist sicherer und einmalig; UPnP wäre eine unnötige Angriffsfläche. |

---

## Was bereits existiert und NICHT neu gebaut wird

| Baustein | Status |
|---|---|
| Caddy v2.11.7 + validierte Caddyfile | ✅ `tools/caddy.exe`, `config/Caddyfile` |
| NSSM 2.24 | ✅ `tools/nssm.exe` |
| 3 Scheduled Tasks (Backup 03:00 / DuckDNS 5-Min / Autostart-Logon) | ✅ registriert, Ready |
| SMTP-Verkabelung + Test-Endpunkt | ✅ `app.py` + `/api/admin/mail/test` |
| DuckDNS-Updater | ✅ `scripts/duckdns_update.py` (Exit-2-Pfad getestet) |
| production.yaml (app_host/app_port/public_domain) | ✅ wird nur um `deployment_mode` erweitert |
| Pentest (1 kritisch + 2 hoch gefixt; M-1 → durch S11) | ✅ `docs/PENTEST_REPORT.md` |

---

## Umsetzungsreihenfolge

```
A1 → A2 → A3 → B1 → B2 → B3 → B4 → B5 → B6 → B7 → C1 → C2 → D1..D7
```

A zuerst (der Installer ruft `switch_mode.py` in S11). Dann der Installer von innen nach außen (Framework → Gmail → DuckDNS → Router → Dienste → Umschalten). C/D begleiten.

**Geschätzter Gesamtaufwand:** ~3–5 Stunden Umsetzung + Tests. Der spätere LOKAL-Durchlauf: ~2 Min. Der INTERNET-Durchlauf (dein Go-Live): ~30–45 Min, davon ~15 Min deine Interaktion (Gmail, DuckDNS, Router, LTE-Tests).
