# OPERATIONS — SWU Card Manager (Server-Betrieb auf diesem PC)

Dieses Dokument beschreibt den Betrieb des SWU Card Managers als Server:
Start, Stop, Neustart, Backups, E-Mail, Domains, Störungen.

---

## 1. Start / Stop / Neustart

| Aktion | Befehl / Weg |
|---|---|
| Server starten | `server.ps1` (Doppelklick) oder `powershell -File server.ps1` |
| Server neu starten | `powershell -File server.ps1 -Restart` |
| Server stoppen | `powershell -File server.ps1 -Stop` oder Task-Manager → python.exe beenden |
| Status prüfen | `https://localhost:8765/api/admin/status` (eingeloggt als Admin) oder Task-Manager (python.exe) |

**Nach dem Start dauert der Engine-Warmup ~10–30 s** (CLIP-Modell + FAISS laden); erst danach sind Scans warm (0,6–1 s).

## 2. KI-Modelle (Versionierung + Training)

- Alle Versionen liegen in `data/models/versions/` (clip_finetuned_v1.pt, …).
- **Neu trainieren:** System-Seite → „🧠 KI-Modelle" → „Neu trainieren". Läuft 15–60 Min auf der GPU, **Server bleibt erreichbar** (Background-Thread). Das neue Modell wird nur **Kandidat** — es ersetzt nichts automatisch.
- **Aktivieren (Promote):** in der Modell-Liste „Aktivieren" klicken. Vorheriges Modell → „retired". **Rollback:** das alte Modell wieder „Aktivieren".
- Metriken (Top-1/Top-5/Δ) werden automatisch auf demselben Test-Split gemessen; ein schlechteres Modell bleibt Kandidat, bis ein Mensch es aktiviert.
- **Empfehlung:** erst trainieren, wenn du 30+ bestätigte Fotos hast; Fotos werden nur bei bestätigtem Scan gespeichert (`data/scans/confirmed/`).

## 2a. Scans: Warm-up und Rate-Limit
- Erster Scan nach Serverstart: ~10–30 s (Engine lädt CLIP+FAISS). Danach: 0,6–1 s.
- Rate-Limit: **30 Scans/Minute** (Schutz vor GPU-Erschöpfung); bei Überschreitung: „Zu viele Scans"-Meldung + 429.
- Die Kamera-Freigabe für Safari/iOS braucht HTTPS. Zertifikatswarnung bei selbstsigniertem Zertifikat: „Details anzeigen → Einstellungen → Besucher dieser Website" bestätigen.
- Haptisches Feedback und Scan-Einstellungen: Einstellungen → Scanner.

## 3. Backups (automatisch)

- **Täglich 03:00** läuft der Windows-Task `SWU-Backup-Daily` → `scripts/backup_daily.py`.
- Inhalt: komplette Datenbank, `config/`, alle Modell-Versionen, bestätigte Trainingsfotos, Dataset-Metadaten.
- Ablage: `C:/SWU-ServerBackups` (konfigurierbar in `config/production.yaml` → `backup:`).
- Rotation: Daily (14) → Weekly (8) → Monthly (12).
- **Restore-Drill:** bei jedem Lauf wird das neueste Backup testweise entpackt und geprüft (SQLite integrity_check + Zeilenzahl). Fehler → Log + Mail an Admin (sobald SMTP konfiguriert).
- Manuell prüfen: System-Seite → Backups → „Diagnose ausführen"; sofort Backup: `python scripts/backup_daily.py --now`

## 4. E-Mail-Versand (SMTP)

- Config: `config/production.yaml` → `mail:` (host/port/user) **+** Env-Variable `SWU_SMTP_PASSWORD` (Passwort liegt NIE in einer Datei).
- Ohne SMTP-Config: Reset-/Verifikations-Tokens landen nur im Log (lokaler Modus).
- Testmail senden: als Admin `POST /api/admin/mail/test` mit `{"to": "deine@mail.de"}` → Antwort zeigt `mode: smtp` (echter Versand) oder `mode: local` (nur Log).
- App-Start liest production.yaml und konfiguriert SMTP automatisch (Log-Zeile `smtp_configured`).

## 5. Domain (DuckDNS)

- `swu-cardscanner.duckdns.org` zeigt auf die öffentliche IP des Routers.
- IP-Update: `python scripts/duckdns_update.py` — Token aus `config/duckdns_token` (gitignored) oder Env `SWU_DUCKDNS_TOKEN`.
- Geplant: Scheduled Task alle 5 Minuten (wird mit dem Token zusammen in Phase 6 eingerichtet).
- Router-Portfreigabe: **nur 80 + 443** auf diesen PC (Caddy); 8765 bleibt zu.

## 6. Caddy (Reverse-Proxy — Go-Live Phase 6)

- `config/Caddyfile`: swu-cardscanner.duckdns.org → `127.0.0.1:8443`, automatische Let's-Encrypt-Zertifikate, HSTS, 15-MB-BODY-Limit, Zugriffs-Log.
- Betrieb als Windows-Dienst via NSSM; Einrichtung in Phase 6.
- App dann mit `app_host: "127.0.0.1"` starten (production.yaml), damit nur Caddy an der Front steht.

## 7. Störungen (Schnelldiagnose)

| Symptom | Erst prüfen |
|---|---|
| iPhone erreicht App nicht | python.exe läuft? `https://localhost:8765` am PC erreichbar? Gleiches WLAN? |
| Erster Scan sehr langsam | Normal (Warmup). Nach 30 s immer noch langsam → `server.ps1 -Restart` |
| „Zu viele Scans" | Rate-Limit; maximal 30 Scans/Minute |
| Login geht nicht | Nach SECRET_KEY-Rotation sind alte Sessions weg → neu einloggen; Reset-Link im lokalen Modus im Log |
| E-Mails kommen nicht an | `POST /api/admin/mail/test` (mode?), `SWU_SMTP_PASSWORD` gesetzt?, Spam-Ordner |
| Backup-Task lief nicht | Aufgabenplanung → `SWU-Backup-Daily` → letzte Laufzeit; `logs/backup.log` |

## 8. Sicherheit — was ist wo

- SECRET_KEY: `config/secret_key` (gitignored, rotiert am 09.10. — Pentest K-1).
- SMTP-Passwort: Env `SWU_SMTP_PASSWORD` (nie in Dateien).
- DuckDNS-Token: `config/duckdns_token` (gitignored).
- MKM-Credentials: DPAPI-verschlüsselt (Windows-Benutzerkontext).
- SSL: selbstsigniert (Übergang), ab Phase 6 Let's Encrypt via Caddy.
- Pentest-Report: `docs/PENTEST_REPORT.md` (K-1/H-1/H-2 gefixt; M-1 = localhost-Binding → Phase 6).
