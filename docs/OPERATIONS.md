# OPERATIONS — SWU Card Manager (Zwei-Umgebungs-Betrieb)

Dieses Dokument beschreibt den Betrieb ab Go-Live v1.0.0: **TEST** (Heimnetz, GPU-Scanner) und **PROD** (Internet hinter Caddy) laufen gleichzeitig auf diesem PC. Der alte Einzel-Server-Betrieb (server.ps1) ist damit abgelöst.

---

## 1. Architektur (Infra-Konzept, umgesetzt in P1–P5)

| | TEST | PROD |
|---|---|---|
| URL | `https://192.168.178.74:8765` (iPhone im WLAN) | `https://swu-cardscanner.duckdns.org` (via Caddy 80/443; Caddy-Dienst siehe §7) |
| Bind | `0.0.0.0:8765` | `127.0.0.1:8443` (nur hinter Caddy) |
| DB | `data_test/cards_test.db` (eigene User/Scans) | `data/cards.db` (Produktivdaten) |
| Scanner | ✔ GPU (CLIP) | ✘ `/api/scan` → 503 „Scannen ist nur in der Test-Umgebung verfügbar" |
| Code | `master` (Entwicklung) | **nur Version-Tags**, eigener Git-Worktree `../swu_prod` |
| Config-Overlay | `config/test.yaml` | `config/production_env.yaml` |
| Env-Flag | `SWU_ENV=test` (Default) | `SWU_ENV=prod` + `SWU_HOME` (Daten-Root = Haupt-Tree) |

**Supervisor** (`scripts/supervisor.py`, Autostart via Startup-Ordner `SWU-Supervisor.cmd`):
- Watchdog: Prüft beide Ports alle 15 s, startet abgestürzte Instanzen automatisch neu (<15 s).
- Health-Monitor (P5): pollt `/api/health` je Instanz alle 60 s; 2 Fails nacheinander → ALERT (Log + E-Mail sobald SMTP konfiguriert; Cooldown 15 min je Instanz). Stop-Flag-Phasen (Deploy/Wartung) zählen nicht.
- Stop-Flags: `config/.stop_test` / `config/.stop_prod` = kontrollierte Down-Phase (Deploy, Restore, Wartung) — Supervisor startet dann NICHT neu.

**Neue User** bekommen bei Registrierung automatisch eigene Sammlung/Haushalt + Standard-Ablageort (on-demand) — nichts weiter nötig.

## 2. Release-Flow (lokal entwickeln → lokal testen → produktiv)

```powershell
# 1. Auf master entwickeln (Feature-Branches wie gehabt); Tests grün
# 2. Version taggen:
git tag -a v1.2.0 -m "Kurzbeschreibung"
git push origin master --tags

# 3. Ausrollen (erst trocken üben):
powershell -File scripts\deploy_prod.ps1 -Tag v1.2.0 -DryRun
powershell -File scripts\deploy_prod.ps1 -Tag v1.2.0
```

Die Pipeline führt automatisch aus: Unit-Gate → E2E-Gate → Tag-Gate (nur Tags!) → **Pre-Deploy-Backup** (`--env prod --pre-deploy`, inkl. D:-Kopie) → Wartungsmodus AN (503 + Retry-After, Admin bleibt frei) → Prod-Worktree `../swu_prod` auf den Tag → Neustart → **Health-Gate** `/api/health/ready` (max 120 s; sonst **AUTO-ROLLBACK** auf den vorherigen Stand) → Wartungsmodus AUS. Letzter Tag landet in `config/.last_deploy`.

Manueller Rollback (nur Code — Daten werden NIE angerührt): `powershell -File scripts\deploy_prod.ps1 -Rollback`
DB-Notfall = Restore (§3), niemals git.

## 3. Backups (automatisch, einstellbar)

Einstellungen je Umgebung: `config/production_env.yaml` bzw. `config/test.yaml` → `backup:` (Zeit, Ziele, keep_daily/weekly/monthly, Drill-Tag, `healthchecks_ping_url` optional).

- **Prod:** täglich 03:00 — Windows-Task `SWU-Backup-Daily` → `scripts/backup_daily.py --env prod` → `C:/SWU-ServerBackups/prod` **+ 3-2-1-Kopie auf `D:/SWU-Backups`** (USB-Stick). Rotation: 7 täglich / 4 wöchentlich / 6 monatlich.
- **Test:** täglich 03:30 — Task `SWU-Backup-Test` → `--env test` → `C:/SWU-ServerBackups/test`.
- **Inhalt je ZIP:** BackupManager-DB-Backup + **VACUUM-INTO-Snapshot** (online-konsistent, DSGVO-bereinigt, verifiziert mit `quick_check` + `foreign_key_check`) + komplette config + alle Modell-Versionen + bestätigte Trainingsfotos + Dataset-Metadaten + State.
- **Restore-Drill:** jeden Sonntag entpackt der Lauf das neueste Backup testweise und prüft Integrität + Zeilenzahl. Manuell: `python scripts/backup_daily.py --env prod` (mit Drill) bzw. `--now` (ohne).
- **Ziel-2-Warnung:** USB-Stick nicht angesteckt → Warnung im Log (`ZIEL-2-WARNUNG`), das Backup liegt dann nur 1-fach — Stick anstecken und Backup wiederholen.
- Sofort-Backup: `python scripts/backup_daily.py --env prod --now`

### Restore (der Ernstfall)

```powershell
python scripts\restore_backup.py --env prod --from "C:/SWU-ServerBackups/prod/swu_backup_YYYYMMDD_HHMMSS.zip"
```
Prüft ZIP-Manifest (env-Konsistenz), verifiziert den Snapshot, setzt das Supervisor-Stop-Flag (kontrollierte Down-Phase), löscht `-wal`/`-shm`, ersetzt die DB, entfernt das Flag. Optional `--with-models` / `--with-config`. Probe-Restore aufs Quartal empfohlen.

## 4. KI-Modelle (nur TEST-Umgebung)

- Versionen in `data_test/models/versions/`; Training über System-Seite (GPU der Test-Instanz, 15–60 min).
- Neues Modell = Kandidat; „Aktivieren" = Promote; Rollback = altes Modell wieder aktivieren.
- Prod hat keinen Scanner und lädt keine GPU-Engine (Kaltstart + RAM gespart).

## 5. Scans / Rate-Limit / Kamera

- Test-App: erster Scan nach Start ~10–30 s Warmup (CLIP+FAISS), dann 0,6–1 s; Rate-Limit 30 Scans/min (429 bei Überschreitung).
- iOS-Kamera braucht HTTPS: Test-App selbstsigniert (Warnung einmalig bestätigen), Prod später Let's Encrypt über Caddy.
- Scan-Einstellungen + Haptik: Einstellungen → Scanner (Test-App).

## 6. E-Mail (SMTP) & Domain

- SMTP: `config/production.yaml → mail:` (host/port/user) **+ Env `SWU_SMTP_PASSWORD`** (Passwort nie in Dateien). Ohne SMTP: Tokens nur im Log; Monitoring-Alert-Mails brauchen SMTP (§7.3).
- Testmail: als Admin `POST /api/admin/mail/test` → `mode: smtp|local`.
- DuckDNS: `python scripts/duckdns_update.py` (Token `config/duckdns_token` oder Env `SWU_DUCKDNS_TOKEN`).
- Router: **nur 80 + 443** freigeben auf diesen PC — 8765/8443 bleiben zu.

## 7. Go-Live-Checkliste (offene Schritte, einmalig)

1. **Caddy-Dienst als Windows-Dienst** — braucht EINMAL Admin (UAC): `powershell -File scripts\setup_services.ps1` (NSSM-Dienste `SWU-App-Test`, `SWU-App-Prod`, `SWU-Caddy` mit Auto-Restart + Log-Rotation). Bis dahin ist **Prod nicht aus dem Internet erreichbar** (nur LAN).
2. **Router-Portfreigabe** 80+443 → dieser PC.
3. **SMTP** konfigurieren (Gmail-App-Passwort) → Alert-Mails + Passwort-Reset online.
4. **DuckDNS-Task** alle 5 min anlegen (öffentliche IP aktuell halten).
5. Erreichbarkeit vom Handy über LTE testen: `https://swu-cardscanner.duckdns.org`.
6. Backup-Tasks prüfen: Aufgabenplanung → `SWU-Backup-Daily` (03:00, `--env prod`) + `SWU-Backup-Test` (03:30).

## 8. Störungen (Schnelldiagnose)

| Symptom | Erst prüfen |
|---|---|
| Beide Apps down | Supervisor läuft? `logs/supervisor.log`; Autostart: Startup-Ordner → `SWU-Supervisor.cmd` |
| Eine App down | Supervisor startet automatisch neu (<15 s); sonst `logs\supervisor_test.log` / `supervisor_prod.log` |
| Alert „nicht erreichbar" | Supervisor-Log; 2× rot trotz Restart → App-Log (`logs/swu_manager.log`) prüfen |
| Deploy schlägt fehl | deploy-Ausgabe: rotes Gate nennt den Grund; nach Health-Fail wurde automatisch zurückgerollt |
| Prod zeigt Wartungsseite | `config/maintenance_on` noch da? Löschen oder Deploy erneut laufen lassen |
| Scans in Prod 503 | **Gewollt** — Scannen nur in der Test-App (GPU) |
| Backup lief nicht | Aufgabenplanung → `SWU-Backup-Daily`/`SWU-Backup-Test` → letzte Laufzeit; `logs/swu_manager.log` |
| „Zu viele Scans" | Rate-Limit 30/min (Test-App) |
| Login geht nicht | Sessions nach SECRET_KEY-Rotation weg → neu einloggen; „Daten weg?" → falscher SWU_ENV |

## 9. Sicherheit — was ist wo

- SECRET_KEY: `config/secret_key` (Haupt-Tree, gitignored) — der Prod-Worktree nutzt denselben (SWU_HOME), sonst wären Sessions + AES-GCM-Nachrichten unlesbar.
- SMTP-Passwort: Env `SWU_SMTP_PASSWORD`; DuckDNS-Token: `config/duckdns_token` (gitignored); MKM-Credentials: DPAPI.
- Prod lauscht nur auf `127.0.0.1:8443` — externer Zugang ausschließlich über Caddy (TLS 1.3, Let's Encrypt, HSTS).
- Betriebs-Flags (`maintenance_on`, `.stop_*`, `.last_deploy`) sind gitignored.
- Pentest-Report: `docs/PENTEST_REPORT.md`.

## 10. Wichtige Pfade/Dateien

| Pfad | Zweck |
|---|---|
| `scripts/supervisor.py` | Watchdog + Health-Monitor für beide Instanzen |
| `scripts/setup_services.ps1` | NSSM-Dienste (einmalig als Admin; Upgrade-Pfad) |
| `scripts/deploy_prod.ps1` | Release-Pipeline mit Auto-Rollback (`-DryRun`, `-Rollback`) |
| `scripts/backup_daily.py` / `restore_backup.py` | Backup / Restore je `--env` |
| `scripts/healthcheck.py` | Health-Poll für Deploy (PS 5.1 kennt kein `-SkipCertificateCheck`) |
| `config/test.yaml` / `config/production_env.yaml` | Umgebungs-Overlays (Pfade, Backup, scan_enabled) |
| `config/.stop_test` / `.stop_prod` / `maintenance_on` | Supervisor-Stop / Wartungsmodus (Datei-Flags) |
| `../swu_prod` | Prod-Code (Git-Worktree, auf Release-Tag gepinnt) |
| `config/.last_deploy` | zuletzt deployter Tag |
