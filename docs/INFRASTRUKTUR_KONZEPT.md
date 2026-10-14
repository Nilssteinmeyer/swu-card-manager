# INFRASTRUKTUR-KONZEPT — SWU Card Manager
## Test- + Produktiv-Umgebung für den Go-Live

**Status:** Konzept zur Freigabe (noch NICHT implementiert)
**Datum:** 14. Oktober 2026 · **Quellen:** 334 (Deployment 132 / Backup 93 / Release-Flow 54 / Stabilität 55, Rohdaten in `scratch/infra-research/`)
**Recherchiert & 3× selbst-reviewed gegen:** Caddy-/Flask-/SQLite-/NSSM-/restic-/litestream-Doku, Hetzner/Netcup-Preislisten, Home-Assistant/Paperless/Immich-Production-Setups, GitHub-Flow-Doku, Uptime-Kuma/healthchecks.io

---

## 0. Zielbild (dein Auftrag, wörtlich)

| Umgebung | Zweck | Erreichbarkeit | Besonderheit |
|---|---|---|---|
| **TEST (lokal)** | Entwicklung + Test neuer Features | Heimnetz wie bisher (`https://192.168.178.74:8765`, iPhone im WLAN) | **GPU-Scanner** (CLIP auf RTX A2000), Scans + Training + eigene DB |
| **PROD (online)** | Produktivbetrieb für Familie/später Freunde | `https://swu-cardscanner.duckdns.org` (Internet, Let's Encrypt) | **Kein Scannen** (kein GPU-Server); Kartenverwaltung, Preise, Decks, Nachrichten, Backups |

**Release-Flow:** lokal entwickeln → Test-Umgebung grün (Unit + E2E + Smoke) → `deploy_prod.ps1` → produktiv. Fehler im Produktivbetrieb → lokal reproduzieren → fixen → neu ausrollen. **Niemals direkt auf Prod entwickeln.**

### Die zentrale Architektur-Entscheidung (aus der Recherche — 2 Optionen, Empfehlung: A)

Die Deployment-Recherche (132 Quellen) sieht zwei tragfähige Wege. Beide sind quellenbelegt; die Entscheidung ist eine Kosten-/Privacy-Frage:

**Option A — Alles auf deinem PC (EMPFOHLEN):**
- Der PC läuft ohnehin 24/7 als GPU-Host (CLIP-Scan-Engine, Kaltstart-Verhalten, Rate-Limit sind darauf optimiert).
- Alle Daten (DB 12 MB + Bilder 2,6 GB + Modelle 600 MB) bleiben zu 100 % auf deiner Hardware → **privacy-first bleibt gewahrt** (deine Grundanforderung seit Projektbeginn).
- Der bestehende Caddy-Modus (Port 80/443, Let's Encrypt, NSSM, DuckDNS) wird zur PROD-Umgebung ausgebaut statt neu zu bauen.
- Nachteile: Heim-IP exponiert (über Caddy kontrolliert), PC-Neustarts = kurze Prod-Downtime, Windows-Patching betrifft Prod.

**Option B — VPS für PROD (~4-8 €/Monat):**
- Kleiner Linux-VPS (Hetzner CX22 ~4-8 €/Monat / netcup VPS Lite ~5 €/Monat), PROD als Docker-Compose-Stack: Flask hinter Gunicorn (gevent-Worker, Flask-Doku-Empfehlung für Produktion), SQLite WAL + Litestream-Streaming-Backup, Caddy mit Auto-TLS auf der DuckDNS-Domain (DuckDNS-API zeigt auf die VPS-IP).
- Vorteile: Heim-IP nicht exponiert, 24/7 unabhängig vom Wohn-PC, gelerntes Linux-Production-Muster (Immich/Paperless-Style); Deploy via GitHub Actions (pytest-Gate + SSH + `docker compose pull/up -d`).
- Nachteile: Deine Daten (Sammlung, Fotos der Familie) liegen auf fremder Hardware — widerspricht Privacy-first; zweites System mit eigenem Patch-/Sicherheitsaufwand; Monatskosten.
- **Empfehlung: Option A** — Privacy-first ist deine Basisanforderung, der PC läuft ohnehin, und der Caddy-Modus existiert bereits. Option B bleibt als dokumentierter Migrationspfad (gleiche App, gleiche Config-Maschinerie, nur anderes Hosting).

```
                    ┌── Heimnetz (TEST) ─────────────────┐   ┌── Internet (PROD) ───────────────┐
iPhone/Familie LAN ─→│ Flask TEST  0.0.0.0:8765 (SSL)     │   │ Caddy :80/:443 (Let's Encrypt)   │
                    │  • eigene DB cards_test.db          │   │  └→ Flask PROD 127.0.0.1:8443    │
                    │  • GPU-Scanner AN                   │   │     • eigene DB cards_prod.db    │
                    │  • Quelle: git master              │   │     • Scanner deaktiviert (503) │
                    └─────────────────────────────────────┘   │     • Quelle: git Tag vX.Y      │
                                                              └──────────────────────────────────┘
```

---

## 1. Umgebungs-Trennung: zwei Instanzen, ein Code-Stand

Heute existiert EIN `deployment_mode: local | internet` (nur Umschalten). Neu: **beide Modi laufen GLEICHZEITIG**, mit getrennten Daten.

### 1.1 Konfiguration (erweiterter Ist-Zustand)

`config/production.yaml` (PROD) und NEU `config/test.yaml` (TEST) — dieselbe Struktur:

| Feld | TEST | PROD | Warum |
|---|---|---|---|
| `deployment_mode` | `local` | `internet` | bestehende Semantik |
| `app_host/port` | `0.0.0.0:8765` | `127.0.0.1:8443` | Test direkt im WLAN; Prod nur hinter Caddy (Pentest M-1 bleibt geschlossen) |
| `data_dir` | `data_test/` | `data/` (wie heute) | **getrennte DB/Bilder/Scans** — Test-Scans verschmutzen nie Prod-Daten |
| `db_name` | `cards_test.db` | `cards_prod.db` | Migrationen laufen getrennt |
| `scan_enabled` | `true` | `false` | Prod antwortet auf `/api/scan` mit 503 + klarem deutschen Hinweis „Scannen nur in der Test-Umgebung" |
| `models_dir` | `data_test/models` | `data/models` (schreibgeschützt, nur lesend) | Training nur auf Test |
| `public_url` | `https://192.168.178.74:8765` | `https://swu-cardscanner.duckdns.org` | für Links in Mails/Postfach |
| `backup.*` | eigenes Profil | eigenes Profil | §3 |

Umgebungs-Flag: `SWU_ENV=test|prod` (Env-Var, nie Datei-Inhalt erraten). `app/core/config.py` wählt damit `config/test.yaml` bzw. `production.yaml` + `data_dir`. Default ohne Flag: `test` (Schutz: versehentliches Starten aus dem Explorer startet nie die Prod-DB).

### 1.2 Zwei Windows-Dienste (NSSM, Ist-Zustand ausgebaut)

| Dienst | Start | Befehl | Restart-Politik |
|---|---|---|---|
| `SWU-App-Test` | AUTO | `python -m app.web --host 0.0.0.0 --port 8765` | Restart nach Crash (AppRestartDelay 5 s, Throttling an) |
| `SWU-App-Prod` | AUTO (delayed) | `python -m app.web --host 127.0.0.1 --port 8443` | dito + AppExit=Restart, AppRotateFiles (Log-Rotation 50 MB) |
| `SWU-Caddy` | AUTO | `tools/caddy.exe run --config config/Caddyfile` | bestehend, bleibt |

Beide Dienste laufen unter demselben Windows-Konto (keine neuen Rechte). NSSM-Parameter nach Recherche-Empfehlung („Stabilität", Befund 1): AppStdout/AppStderr mit Rotation, `AppRotateBytes=50MB`, Restart-Throttling aktiv.

### 1.3 Scanner in PROD sauber abschalten (nicht verstecken)

- `/api/scan` → HTTP **503** + JSON `{"error": "Scannen ist nur in der Test-Umgebung verfügbar"}` (Recherche-Muster Maintenance-Modus; klare Antwort statt Timeout).
- Kamera-UI im Scanner-Tab zeigt im Prod-Modus Info-Karte „Hier scannen → Test-App im WLAN" (verhindert Verwirrung bei Familie/Freunden).
- Rate-Limit + GPU-Engine laden **gar nicht erst** (Kaltstart weg, RAM gespart).

---

## 2. Release-Flow: Test → Prod (mit Qualitätsschranken)

### 2.1 Git-Workflow (GitHub Flow light — Recherche-Empfehlung für Ein-Personen-Teams)

```
feature/xyz  ──→  master (immer deployable)  ──→  Tag v1.2.0  ──→  Prod-Deploy
   lokal           Test-Umgebung läuft auf master      ↑ nur Tags gehen nach Prod
```

- Kurze Feature-Branches (wie bisher), Fixes direkt auf master erlaubt.
- **Neu: Version-Tags** (`v1.2.0`) als Prod-Pfeiler: Prod deployed IMMER einen Tag, nie einen fliegenden master-Stand.
- Prod kennt den Tag (`/api/system` → `app_version`, auch sichtbar im Admin-Bereich).

### 2.2 Deploy-Skript `scripts/deploy_prod.ps1` (das Herzstück)

Vollautomatisch, abbrechbar, mit Rollback:

1. **Gate 1 — Tests:** `pytest tests/` (Unit) + `pytest tests/e2e -m e2e` (laufen gegen Test-Server). Rot → STOP.
2. **Gate 2 — Tag:** `git tag vOLD..vNEW` vorhanden? Working tree clean? Sonst STOP.
3. **Backup vor allem:** `backup_daily.py --env prod --pre-deploy` (erzwingt Backup VOR jedem Deploy — Recherche-Regel „Migrationen nur mit Vorher-Backup").
4. **Wartungsmodus AN:** `maintenance on` → Caddy zeigt 503-Seite „Kurzes Update, gleich wieder da" (mit Retry-After; Recherche: NIE 200 mit Wartungstext).
5. **Update:** `git fetch + checkout <tag>`; `pip install -r requirements.txt` (nur bei Änderung); DB-Migration läuft automatisch beim App-Start (Schema-System, additive Migrationskette v1→v6 wie bisher — Recherche bestätigt handgeschriebene additive Migrationen für SQLite als vertretbar; Flask-Migrate/Alembic erst bei häufiger werdenden Schema-Änderungen).
6. **Dienst-Restart** `SWU-App-Prod` (NSSM restart).
7. **Health-Gate:** Poll `https://localhost:8443/api/health/ready` bis 200 (max. 90 s; Engine-Warmup berücksichtigt). Fail → **Auto-Rollback**: Checkout vorheriger Tag, Dienst-Restart, Backup bleibt unangetastet.
8. **Wartungsmodus AUS**, Deploy-Report (Alt-Tag → Neu-Tag, Dauer, Health-Ergebnis) ins Log + als Admin-System-Nachricht ins Postfach.
9. Optional `--dry-run` (zeigt Pipeline ohne Änderung) und `--rollback` (manueller Rollback zum letzten Tag).

### 2.3 Datengetrenntheit beim Deploy

Prod deployt **nur Code** (`git checkout tag`) — Daten (DB, Bilder, Modelle, Scans) bleiben unberührt in ihren Verzeichnissen. Config-Dateien (`production.yaml`, `secret_key`) sind Teil der Umgebung, nicht des Releases. **Ein Rollback berührt niemals Daten.**

---

## 3. Backups — automatisiert und einstellbar (dein Wortlaut)

### 3.1 Was die Recherche ergab (93 Quellen, Kern)

- **SQLite niemals per File-Copy während Betrieb** sichern → `VACUUM INTO` / Backup-API (konsistent, online-sicher, zusätzlich kompakt + bereinigt = DSGVO-freundlich, gelöschte Zeilen verschwinden aus dem Backup).
- **3-2-1-Regel** (3 Kopien, 2 Medien, 1 extern) — für SQLite-Apps der etablierte Standard; die Bestandteile DB + Bilder + Modelle als EIN logisches Set sichern.
- **GFS-Rotation** (Grandfather-Father-Son: daily/weekly/monthly) mit konfigurierbarer Retention — Muster bei Paperless-ngx, Immich, Home Assistant.
- **Restore-Drill ist Pflicht** — ein ungetestetes Backup ist kein Backup (existiert schon: wöchentlicher Drill wird auf beide Umgebungen erweitert).

### 3.2 Das Ziel-Design (erweitertes backup_daily.py, einstellbar in YAML)

```yaml
# config/production.yaml (Prod) — Test analog in test.yaml
backup:
  schedule_time: "03:00"            # täglich (Task Scheduler)
  target_dir: "C:/SWU-ServerBackups/prod"     # Ziel 1 (Zweit-Platte/Ordner)
  target_dir_external: "E:/SWU-Backups"        # Ziel 2 (USB/NAS) — 3-2-1
  target_dir_fallback: "project_state/backups/prod"
  keep_daily: 7
  keep_weekly: 4
  keep_monthly: 6
  include: [db, config, models, confirmed_scans]   # einstellbar
  verify: true                     # PRAGMA quick_check auf jede DB im Backup
  restore_drill_weekday: 0         # Sonntag-Drill
  email_on_failure: true
  pre_deploy_backup: true          # Gateway im deploy_prod.ps1
```

- **Pro Lauf:** `VACUUM INTO` → `quick_check` + `foreign_key_check` auf der Snapshot-Datei → ZIP (DB + config + Modelle + bestätigte Scans) → GFS-Rotation auf **beiden Zielen** → Ergebnis-Log.
- **Kein Backup = kein Deploy** (Gate im deploy_prod.ps1).
- **Restore-Drill** wöchentlich (wie heute) + **halbju­hrlicher manueller Voll-Restore** in ein Temp-Verzeichnis mit App-Start gegen die restaurierte DB (Smoke: Login + Sammlung laden) — dokumentiert in OPERATIONS.md.
- **Upgrade-Pfad restic (optional, P3+):** Die Backup-Recherche empfiehlt als zweite Stufe restic (nativ Windows, VSS via `--use-fs-snapshot`, Dedup, Verschlüsselung, GFS-Retention `--keep-daily/-weekly/-monthly`, `check --read-data-subset`-Verifikation) für DB-Snapshot + Bilder + Modelle + Scans als EIN System. Phase 3 liefert zuerst die ZIP-Pipeline (bewährt, einfach wiederherstellbar); restic ist der dokumentierte Ausbau, sobald die Datenmenge wächst oder ein NAS angeschlossen ist. Borg scheidet aus (keine nativen Windows-Binaries).
- **Litestream (optional, später):** Streaming-Replikation der DB mit Sekunden-RPO als Windows-Service — nur die DB, Ergänzung kein Ersatz; bewusst nicht in P3 (RPO von 24 h reicht für die Familien-App, Restore-Einfachheit zuerst).
- **Einstellbar ohne Code:** alles obige per YAML; Windows-Task `SWU-Backup-Daily` bleibt, bekommt `--env prod`; NEU `SWU-Backup-Test` mit `--env test` (eigene Rotation, eigenes Ziel).
- **Externes Ziel (3-2-1):** USB-Platte/Netzlaufwerk als Ziel 2; wenn nicht angeschlossen → Warnung im Log + Admin-Postfach (kein stiller Ausfall). Optionaler Cloud-Sync bleibt bewusst weg (privacy-first) — nur das YAML-Feld existiert für später.

### 3.3 Restore (der Ernstfall)

`scripts/restore_backup.ps1 -Env prod -From "C:/SWU-ServerBackups/prod/swu_backup_2026-10-14_0300.zip"`:
1. Prod-Dienst stoppen → 2. DB/Bilder/Config aus ZIP zurück → 3. Dienst starten → 4. Health-Check → 5. Report. (Dokumentiert + einmal pro Quartal geübt.)

---

## 4. Stabilität & Monitoring (55 Quellen-Kern)

| Baustein | Entscheidung | Quelle-Kategorie |
|---|---|--- Tags/NSSM-Watchdog
| Health-Endpoints | `/api/health` (Liveness: immer 200 wenn Prozess lebt, keine DB) + `/api/health/ready` (Readiness: `SELECT 1` + Schema-Version + ggf. Engine-Status) — **neu zu implementieren** | Flask-Production-Muster
| Monitoring | **Uptime-Kuma** (self-hosted, De-facto-Standard) pollt `/api/health` alle 60 s auf BEIDE Umgebungen; Alert per E-Mail (SMTP vorhanden) — später Telegram optional | Uptime-Kuma-Doku/Community
| Backup-Job-Monitoring | healthchecks.io-Ping (oder Uptime-Kuma-Push) am Ende jedes Backup-Laufs — „Backup STILL" wird alarmiert, nicht nur „Backup tot" | healthchecks.io-Muster
| Fehler-Alerts | 500er-Flask-Handler + Backup-Fehlschlag → Admin-Postfach (System-Nachricht) + E-Mail; Cooldown 15 min/Fehlerklasse | Stabilität-Recherche Befund 5
| Log-Rotation | TimedRotatingFileHandler (midnight, 30 behalten) in der App + NSSM-AppRotateFiles 50 MB als zweite Linie | Logging-Best-Practice
| Wartungsmodus | Maintenance-Flag als **Datei** `config/maintenance_on` (nicht DB, damit Migrationen laufen) → before_request-Hook: alle Routes 503 + `Retry-After: 600` + statische deutsche Info-Seite; `/api/health` + Admin-User ausgenommen; **Auto-Off nach Zeitfenster** als Fail-Safe gegen vergessene Flags (Muster: Laravel `artisan down`, django-maintenance-mode) | Stabilität-Recherche Befund 5 |
| SQLite-Betrieb | WAL bleibt, `PRAGMA busy_timeout=5000` auf jeder Verbindung, `synchronous=NORMAL` — 2-10 Nutzer weit unter jeder Grenze (offizielle Doku: <100 k Hits/Tag „fine") | SQLite-WAL-Doku |
| Graceful Shutdown | gevent `spawn=Pool(200)` als Concurrency-Cap + SIGTERM/SIGINT-Handler → `server.stop(timeout=30)` — damit NSSM-Stop/Deploy keine laufenden Requests zerreißen (pywsgi hat das NICHT eingebaut) | gevent-Doku/Stabilität Befund |
| TLS-Hardening | HSTS `max-age=63072000` (includeSubDomains erst nach Prüfung), TLS-1.3-Profil, Caddy-Admin-API auf localhost:2019 begrenzen; bei Zertifikats-Problemen LE-Staging-Environment gegen Rate-Limits | OWASP/MDN/Caddy-Doku |

**Was bewusst NICHT kommt** (Overkill für 1 App / 2-10 Nutzer, lt. Recherche): Prometheus/Grafana/Loki-Stack, Sentry self-hosted, Kubernetes, Ansible, Watchtower für die App selbst, GitFlow-Environment-Branches.

---

## 5. Umsetzungsplan (nach deiner Freigabe)

| Phase | Inhalt | Aufwand | Test |
|---|---|---|---|
| **P1 — Fundament** | `SWU_ENV`-Config-Trennung (`app/core/config.py`, `config/test.yaml`, `data_test/`); Health-Endpoints `/api/health` + `/api/health/ready`; `scan_enabled`-Flag + 503-Pfad; gevent Pool(200) + SIGTERM-Graceful-Shutdown | ~½ Tag | Unit + Live-Check beider Modi |
| **P2 — Prod-Dienst** | `SWU-App-Test` + `SWU-App-Prod` als NSSM-Dienste (Umbau von setup.ps1, resumable); beide gleichzeitig; Prod-DB aus Backup-Import (Startbestand) | ~½ Tag | Dienst-Crash-Test (kill → Auto-Restart), Health-Grün |
| **P3 — Backup-Ausbau** | backup_daily.py: `--env`, VACUUM INTO + verify, 2 Ziele, YAML-Retention, Backup-Task Test+Prod, healthchecks-Ping, restore_backup.ps1 | ~1 Tag | Restore-Drill Prod + Test, Ziel-2-Ausfall-Simulation |
| **P4 — Release-Flow** | deploy_prod.ps1 (Gates, Tag-Checkout, Maintenance, Health-Poll, Auto-Rollback, Report); Wartungsmodus (503-Seite, Flag-Datei + Auto-Off); Version-Tags + `/api/system`-Anzeige | ~1 Tag | Dry-Run + echter Deploy v1.0.0 + absichtlicher Rollback-Test |
| **P5 — Monitoring** | Uptime-Kuma (Docker/EXE) mit 2 Monitoren; Alert-Mail; Backup-Ping-Alarm | ~½ Tag | Alert-Test (Server killen → Mail) |
| **P6 — Doku & Go-Live** | OPERATIONS.md neu (2 Umgebungen, Deploy, Restore, Wartung); Go-Live-Checkliste; erstes echtes Prod-Release | ~½ Tag | End-to-End-Walkthrough |

**Gesamt: ~4 Tage Fokusarbeit.** Reihenfolge ist bewusst so, dass P1-P2 allein schon einen sicheren Parallelbetrieb ergeben.

### Offene Punkte für dich (vor P1)

1. **Hosting-Entscheidung:** Option A (alles auf deinem PC, meine Empfehlung) oder Option B (VPS ~4-8 €/Monat für PROD)?
2. **Prod-Startdaten:** Soll die Prod-DB mit deinem aktuellen Sammlungsstand starten (Kopie aus dem heutigen Backup) oder leer?
3. **Ziel 2 für Backups:** USB-Platte vorhanden? Netzlaufwerk? (Für 3-2-1; sonst konfiguriere ich es mit Platzhalter + Warnung.)
4. **Uptime-Kuma als Docker oder EXE** — hast du Docker für Windows aktiviert, oder soll ich die EXE-Variante einrichten?
5. **Domain:** bleibt `swu-cardscanner.duckdns.org` für Prod?

---

## 6. Review-Protokoll (3 Durchgänge, wie befohlen)

- **Review 1 (Vollständigkeit gegen den Auftrag):** Alle 4 Anforderungen geprüft — Test+Prod getrennt ✓; lokal entwickeln/testen, dann Prod-Rollout ✓; Stabilität (Watchdog/Health/Monitoring/503-Wartung) ✓; automatisierte, einstellbare Backups (YAML-Rotation, 2 Ziele, Verify, Drill, Restore-Skript) ✓. Lücke gefunden & geschlossen: Scanner in Prod war ursprünglich nur „ausgeblendet" — jetzt sauber 503 + UI-Hinweis (Kap. 1.3).
- **Review 2 (Widerspruchsfreiheit + Machbarkeit auf DIESEM System):** Ports (8765 Test / 8443+80+443 Caddy) kollisionsfrei ✓; NSSM-Dienste ohne Admin-Rechte-Erweiterung ✓; GPU-Bindung nur Test ✓; Config-Default = test schützt vor versehentlichem Prod-Zugriff ✓; Schema-Migrationen laufen je Umgebung getrennt (Additiv-Kette v1→v6 bewährt) ✓; Backup-Vor-Deploy-Gate deckt auch manuelle Migrationen ab ✓. Korrektur aus Review: Ursprünglich „Prod deployt master" — verschärft zu „Prod deployt NUR Tags" (nachvollziehbarkeit + Rollback-Pfeiler).
- **Review 3 (Vergleich mit gängigen Lösungen, Quellen-Abgleich):** Hosting-Empfehlung gegen alle 132 Deployment-Quellen geprüft — die Recherche empfiehlt mehrheitlich einen kleinen VPS für die Online-Instanz; abgewogen gegen Privacy-first + vorhandenen Caddy-Modus + 24/7-GPU-Host bleibt Option A (eigener PC) die Empfehlung, Option B (VPS + Docker + Gunicorn + Litestream, ~4-8 €/Monat) ist als gleichwertige Alternative mit vollem Migrationspfad dokumentiert ✓; Backup-Stack = kanonische SQLite-Empfehlung (VACUUM INTO/Backup-API) + GFS + 3-2-1 + Restore-Drill wie bei Paperless/Immich; restic als dokumentierter Upgrade-Pfad aufgenommen ✓; Release-Flow = GitHub-Flow-light + Tag-Deploy + Health-Gate + Auto-Rollback = Üblichkeitsmaßstab kleiner Web-Apps (GHA+SSH-Variante für Option B notiert) ✓; Monitoring = Uptime-Kuma + healthchecks-Ping (De-facto-Standard für 1-2 Instanzen) ✓; bewusste Nicht-Ziele gegen alle 4 Recherche-Berichte abgeglichen (kein K8s/GitFlow/Loki/Sentry/Ansible/Watchtower — Overkill begründet) ✓. Preisdifferenz VPS geprüft (Hetzner CX22 ~3,79-8,49 €, netcup VPS Lite ~4,92 € nach Preiserhöhung Sept. 2026, DO ab 4 $) und in Option B dokumentiert.
- **Review 3b (Nach-Agenten-Delta):** Die vollständigen 4 Agenten-Ergebnisse (nach Konzept-Commit eingetroffen) wurden delta-geprüft und eingearbeitet: Hosting-Option B mit ehrlicher Vor-/Nachteil-Tabelle; gevent Pool(200) + SIGTERM-Graceful-Shutdown (Stabilität-Befund, fehlte); Maintenance-Flag als Datei + Auto-Off-Fail-Safe; HSTS 63072000 + LE-Staging-Detail; restic- + Litestream-Upgrade-Pfade (Backup-Befunde); Gunicorn-Hinweis für Option B (Flask-Doku: Dev-Server nie produktiv). Keine Widersprüche zum Kern-Design — alle Deltas waren Präzisierungen.

---

**Freigabe?** Dann lege ich mit P1 los (Config-Trennung + Health-Endpoints + Scan-Flag — das Fundament, auf dem alles andere aufbaut).
