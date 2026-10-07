# TDL — Technical Decision Log

Entscheidungsprotokoll des SWU Card Manager. Format: Kontext → Entscheidung → Konsequenzen.

---

## TDL-001 · Python 3.11 statt 3.14

**Kontext:** System-Python war 3.14.7; viele CV/ML-Pakete haben dafür keine Wheels.
**Entscheidung:** Eigenes venv mit Python 3.11.15 (via `uv`), isoliert vom System.
**Konsequenzen:** Breiteste Wheel-Verfügbarkeit (OpenCV, PyTorch, FAISS). Projekt nutzt `.venv/` — kein globaler Eingriff.

## TDL-002 · SQLite mit WAL als Datenbank

**Kontext:** Lokale Single-User-Anwendung, keine Server-Abhängigkeit gewünscht.
**Entscheidung:** SQLite, WAL-Modus, `busy_timeout=5000`, Foreign-Keys an.
**Konsequenzen:** Crash-safe, keine Installation nötig. Backup über SQLite-Backup-API (TDL-010).

## TDL-003 · Zwei Datenquellen (SWU-DB + offizielle FFG-API)

**Kontext:** SWU-DB liefert Kartennummern mit führenden Nullen (`029`), nur englische Namen, aber stabile CDN-Bild-URLs. Offizielle FFG-API (`admin.starwarsunlimited.com`) liefert `locale=de`/`en`, aber andere Nummern-Formate (`29`).
**Entscheidung:** Beide Provider hinter dem `CardDataProvider`-Interface. SWU-DB als Basis-Import (Bilder), FFG-API als bilingualer Name-Lieferant.
**Konsequenzen:** Beide APIs sind öffentlich ohne Auth; 0.3–0.5s Delay als Courtesy. Nummern-Normalisierung nötig (TDL-007). Provider austauschbar.

## TDL-004 · Klassische CV (SIFT+FLANN+RANSAC) ersetzt durch CLIP-Embeddings

**Kontext:** ORB/SIFT-Feature-Matching versagte bei echten Handyfotos (Hintergrund, Beleuchtung, Perspektive): 10 Scans, 0 Treffer. Recherche professioneller TCG-Scanner-Apps (TCGplayer/Roca, CardTrove, eigenes CLIP-fine-tuning-Projekt) zeigte: Deep-Learning-Embeddings sind der Standard.
**Entscheidung:** CLIP ViT-B/32 (OpenAI-Weights) als visueller Encoder, 512-dim Embeddings, FAISS IndexFlatIP (Cosine via L2-Norm + Inner Product). Klassische Engine bleibt als Fallback im Repo (`app/recognition/engine.py`).
**Konsequenzen:** FAISS-Index (8.822 Karten) einmalig vorberechnet (~2 Min. auf GPU), Scan dann ~500–1500ms. Referenz-Embeddings müssen bei Modellwechsel neu gebaut werden (`build_faiss_index(force=True)`).

## TDL-005 · CLIP-Fine-Tuning mit on-the-fly Augmentation

**Kontext:** Vortrainiertes CLIP verwechselte ähnliche Karten (Domain-Gap: saubene Referenz vs. Handyfoto).
**Entscheidung:** Fein-Tuning des visual encoders (Text-Encoder eingefroren) mit InfoNCE-Verlust auf Paaren (Original, augmentiert). Augmentation zur Laufzeit auf der GPU/CPU pro Batch: Perspektiv-Warp, Rotation ±5°, HSV-Jitter, Gauß-Rauschen, Blur, JPEG-Artefakte.
**Konsequenzen:** Kein langsamer Datensatz-Download nötig; Training 5 Epochen à ~4 Min auf RTX A2000. Loss 3.78 → 0.05. Kein Hängen mehr bei `num_workers=0` auf Windows.

## TDL-006 · Flask-Web-UI statt Desktop-GUI

**Kontext:** Nutzer will vom iPhone scannen; CustomTkinter-Desktop-GUI erreiche das Handy nicht.
**Entscheidung:** Flask-Backend (`app/web/`), statisches HTML/CSS/JS-Frontend, HTTPS mit selbstsigniertem Zertifikat auf `0.0.0.0:8765`. Kamera läuft **im Browser** via `getUserMedia` (`facingMode: environment`) — das Handy ist die Kamera, kein Server-Zugriff auf Webcams nötig.
**Konsequenzen:** iOS-Safari erzwingt HTTPS für `getUserMedia` → selbstsigniertes Zertifikat mit SAN für die LAN-IP (TDL-008). Desktop-GUI (`app/ui/`) bleibt existiert, ist aber nicht der Hauptpfad.

## TDL-007 · Kartennummer-Normalisierung

**Kontext:** SWU-DB: `SOR-029` (Nummer `029`), FFG-API: `SOR-29` (Nummer `29`) — Join der deutschen Namen schlug fehl (510/10k Karten statt 96%).
**Entscheidung:** Normalisierung beim Lookup: `.rstrip('F').lstrip('0') or '0'` als Join-Schlüssel.
**Konsequenzen:** 10.716/11.084 Karten (96%) haben deutsche Namen; Rest sind Tokens/OP-Promos ohne offizielle DE-Übersetzung. Foil-Varianten erben den Namen der Normal-Variante.

## TDL-008 · Selbstsigniertes TLS-Zertifikat mit SAN

**Kontext:** iOS blockiert `getUserMedia` auf plain HTTP; Installieren nach `C:\Program Files` scheiterte an Rechten.
**Entscheidung:** Zertifikat (10 Jahre, SANs: Hostname, localhost, LAN-IP 192.168.178.74) via Python `cryptography` erzeugt, liegt in `ssl/`. Flask läuft mit `ssl_context=(cert, key)`.
**Konsequenzen:** iPhone: einmalige Zertifikats-Warnung bestätigen. SAN muss bei IP-Wechsel neu erzeugt werden.

## TDL-009 · Scan-Ablauf: Popup-Zwang + Einfrieren der Kamera

**Kontext:** Nutzer forderte: Karte legen → erkennen → Popup „Ist das die richtige Karte?" → bestätigen → Zyklus von vorn. Erste Motion-Detection-Implementierung blockierte dauerhaft (Rauschen > Threshold).
**Entscheidung:** Keine Motion-Gate-Logik mehr. Zustandsmaschine `null → ready (Stabilisierungs-Wartezeit) → scanned → confirm → zurück`. `video.pause()` während Scan + Popup, `video.play()` nach Bestätigung/Abbruch.
**Konsequenzen:** Deterministischer Ablauf; ein Scan pro aufgelegter Karte. „Abbrechen" schließt Popup ohne Speichern/Löschen.

## TDL-010 · Backup über SQLite-Backup-API

**Kontext:** ZIP-Backup der DB scheiterte an WAL-File-Locks (`PermissionError`), `UPDATE ... ORDER BY ... LIMIT` ist in SQLite ungültig.
**Entscheidung:** Backups erzeugen eine Konsistenzkopie via `sqlite3.Connection.backup()`; Foil-Flag wird per `SELECT MAX(collection_id)` + normalem `UPDATE` gesetzt.
**Konsequenzen:** Backups funktionieren auch bei offenen Verbindungen; Rotation behält max. N ZIPs.

## TDL-011 · Dashboard zeigt nur Sammlungsdaten

**Kontext:** Dashboard zeigte Referenzdaten (10k+ Karten aus der Kartei) — für den Nutzer nicht von seiner Sammlung unterscheidbar.
**Entscheidung:** Alle Dashboard-Statistiken (Gesamt, Unique, Foil, Seltenheits-/Typ-/Set-Breakdowns) werden ausschließlich aus `collection JOIN cards` berechnet.
**Konsequenzen:** Leere Sammlung = leeres Dashboard. Kartei bleibt intern für Erkennung sichtbar, taucht aber in keiner Sammlungs-Ansicht auf.

## TDL-012 · Trainingsdaten-Handling: bestätigte Fotos + gezieltes Löschen

**Kontext:** Fotos sollen nur für bestätigte Scans als Trainingsdaten dienen; bei Fehl-Erkennung müssen Karte UND zugehöriges Foto verschwinden.
**Entscheidung:** Foto wird erst beim `confirmed=true` gespeichert (`data/scans/confirmed/CARDID_TIMESTAMP.jpg`). Ablehnen löscht das Scan-Bild. `POST /api/collection/delete` mit `reason:"false_recognition"` löscht **nur das jüngste** Foto dieser Karte (nicht alle).
**Konsequenzen:** Saubere Trainingsdaten; Korrekturen früherer Scans bleiben erhalten.

## TDL-013 · Einstellungen in SQLite statt config-Datei

**Kontext:** Nutzer will Scan-Prozess individuell anpassbar (Intervall, Auflösung, Schwellwerte, Vibration...). Erst gab es zwei konkurrierende Settings-Systeme (JSON-Datei + DB), was Flask-Routen kollidieren ließ (`View function mapping is overwriting`).
**Entscheidung:** Ein einziges System: `settings(key, value)`-Tabelle in SQLite + `/api/settings` (GET/POST/reset). Frontend lädt Settings beim Scanner-Start und wendet sie an.
**Konsequenzen:** Persistente, zur Laufzeit änderbare Präferenzen (20+ Optionen) ohne Neustart des Browsers; ein Neustart des Servers ist nicht nötig, der Scanner lädt per AJAX.

## TDL-014 · Cache-Busting für statische Assets

**Kontext:** iOS-Safari cache-aggressiv CSS/JS (`304`), Änderungen kamen beim Nutzer nicht an.
**Entscheidung:** Versionsquery an allen Asset-URLs (`style.css?v=N`, `scanner.js?v=N`), bei jeder relevanten Änderung hochgezählt.
**Konsequenzen:** Zuverlässiges Update nach Tab-neu-öffnen; Versionsnummern manuell im Template pflegen.

## TDL-015 · OCR bilingual (deu+eng) mit lokaler tessdata

**Kontext:** Deutsche Karten („Giftige Raffinerie") sollten erkennbar sein; `deu.traineddata` ließ sich nicht nach `C:\Program Files` kopieren (Rechte).
**Entscheidung:** Sprachdaten im Projekt (`tessdata/`), Tesseract mit `--tessdata-dir` konfiguriert, OCR `deu+eng`, Matching gegen `name` UND `name_de` (Fuzzy).
**Konsequenzen:** Kein Admin-Rechte nötig; deutsche Titel erhöhen die Genauigkeit als Zusatzsignal neben CLIP.

## TDL-016 · CPU-Torch → CUDA-Torch (cu121)

**Kontext:** Eingebautes Torch war CPU-only (kein `c10.dll`-Load / kein CUDA), Fine-Tuning und CLIP-Inferenz wären zu langsam geworden.
**Entscheidung:** `torch 2.5.1+cu121` + `torchvision 0.20.1` vom PyTorch-CUDA-Index; MSVC Redistributable nachinstalliert.
**Konsequenzen:** CLIP läuft auf der RTX A2000 8GB (fp16-fähig), Fine-Tuning ~13min/5 Epochen, Scan ~0.5–1.5s.

## TDL-017 · Teststrategie

**Kontext:** Kernfunktionen (Sammlung, Scans, Bestätigung, Foil, Dashboard, Filter) sollen gegen Regressionen gesichert werden.
**Entscheidung:** `tests/test_web_api.py`: 28 Tests (DB-CRUD, Scan-/Confirm-Flow, Foil-Handling, Dashboard-Stats, Flask-Endpunkte inkl. Cache-Reset). Insgesamt 59 Unit-/Integration-Tests; Genauigkeitsmessung separat (`test_recognition_accuracy.py`, Top-1 ≥ 99.5% auf Referenzbildern).
**Konsequenzen:** Schnelle Suite (<3s) läuft bei jedem Fix; langsamer Accuracy-Test on-demand.

---

*Fortgeführt bei jeder wesentlichen Architektur-Änderung.*
