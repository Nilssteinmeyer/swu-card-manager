# Star Wars Unlimited Card Manager — Final Report

## 1. Installierte Software

| Komponente | Version | Zweck |
|---|---|---|
| Python | 3.11.15 (via uv) | Laufzeitumgebung |
| OpenCV | 5.0.0 | Bildverarbeitung, Karten detektion, Feature Matching |
| NumPy | 2.4.6 | Numerische Operationen |
| Pillow | 12.3.0 | Bildkonvertierung für GUI |
| PyYAML | 6.0.3 | Konfiguration |
| Requests | 2.34.2 | HTTP API Client |
| RapidFuzz | 3.14.6 | Fuzzy String Matching (OCR → DB) |
| ImageHash | 4.3.2 | Perceptual Hashing |
| PyTesseract | 0.3.13 | OCR Python Wrapper |
| Tesseract OCR | 5.4.0 | Texterkennung ( externe Binary ) |
| PyTorch | 2.14.1 (CPU) | ML Backend (für EasyOCR, optionales Training) |
| EasyOCR | — | OCR Alternative (optional) |
| CustomTkinter | 6.0.0 | Desktop GUI |
| OpenPyXL | 3.1.5 | Excel Export (optional) |
| pytest | 9.1.1 | Testing |
| MSVC Redist | 14.51 | C++ Runtime (für Torch DLLs) |
| Git | 2.53.0 | Versionskontrolle (optional) |

## 2. Hardware

| Komponente | Wert |
|---|---|
| Betriebssystem | Windows 11 Pro 64-Bit (10.0.26200) |
| CPU | Intel Core i9-12900H (14 Kerne / 20 Threads) |
| RAM | 64 GB |
| GPU | NVIDIA RTX A2000 8GB (CUDA 12.0 fähig) |
| GPU 2 | Intel Iris Xe Graphics |
| freier Speicher | 808 GB |
| Kamera | Integrated Webcam |
| Tesseract | C:/Program Files/Tesseract-OCR/tesseract.exe |

## 3. Datenquelle

**Primäre Datenquelle:** SWU-DB.com öffentliche REST-API
- Base URL: `https://api.swu-db.com`
- Bild CDN: `https://cdn.swu-db.com`
- Authentifizierung: keine erforderlich
- Dokumentation: https://www.swu-db.com/api
- Rate-Limiting: nicht dokumentiert, wir verwenden 0.5s Verzögerung als Höflichkeit
- Endpunkte:
  - `GET /sets` — alle Sets
  - `GET /cards/{set}` — alle Karten eines Sets
  - `GET /cards/{set}/{number}` — Einzelkarte
  - Bilder: `https://cdn.swu-db.com/images/cards/{SET}/{NUMBER}.png`

Die Datenquelle ist austauschbar über das `CardDataProvider` Interface.

## 4. Datenbankstruktur

SQLite mit WAL-Modus. Tabellen:

- **sets** — Set-Metadaten (set_id, name, release_date, imported status)
- **cards** — alle Karten (card_id, name, set, number, type, rarity, aspects, traits, cost, power, hp, text, artist, image paths, perceptual hash, features)
- **collection** — Sammlung (card_id, count, condition, language, variant, location)
- **scans** — Scan-Historie (timestamp, image_path, recognized_card_id, confidence, method, ocr_text, error_status, manual_correction)
- **scan_candidates** — Alternative Kandidaten pro Scan
- **models** — ML-Modell-Registry (version, dataset, validation, accuracy, active status)
- **dataset_versions** — Dataset-Versionierung
- **job_log** — Hintergrund-Job-Historie
- **schema_migrations** — Migrations-Tracking

## 5. Recognition Engine

Multi-Signal Architektur mit gewichteter Kombination:

| Signal | Gewicht | Methode |
|---|---|---|
| OCR Title Matching | 40% | Tesseract OCR + RapidFuzz fuzzy matching |
| Perceptual Hash | 25% | dHash (16x16) + Hamming-Distanz |
| Feature Matching | 25% | ORB Features + BFMatcher |
| Metadata (Kartennummer) | 10% | Exact match der Kartennummer |

- Auto-Accept Threshold: 85%
- Kandidaten-Threshold: 50%
- Bei niedriger Konfidenz: Kandidaten anzeigen, Benutzer bestätigen

## 6. Testergebnisse

```
31 passed, 1 skipped, 0 failed
```

Getestet:
- Datenbankschema, CRUD-Operationen, Idempotenz
- Collection-Management (hinzufügen, duplikate erkennen)
- Scan-Aufzeichnung mit Kandidaten
- State-Management (Tasks, Recovery, Crash-Erkennung)
- Karten Detektion (Perspektivkorrektur, ROI-Extraktion)
- Perceptual Hash (identische/verschiedene Bilder)
- Backup-Erstellung, -Rotation, -Integritätsprüfung
- Data Provider (API-Initialisierung, Kartenimport)

## 7. Bekannte Einschränkungen

1. **Torch ist CPU-only**: Die installierte PyTorch-Version unterstützt kein CUDA. Für reine OCR/Feature-basierte Erkennung ausreichend. Für potenzielles Deep-Learning-Training könnte eine CUDA-Version installiert werden.

2. **Bild-Download dauert**: Das Herunterladen aller Kartenbilder (~8000+ Karten) benötigt Zeit aufgrund der Rate-Limiting-Verzögerung. Der Download läuft im Hintergrund und ist idempotent.

3. **OCR-Genauigkeit**: Bei ungünstigen Lichtverhältnissen oder Schräglagen kann die OCR-Genauigkeit sinken. Die Multi-Signal-Architektur kompensiert dies durch phash und Feature-Matching.

4. **Kein echtes ML-Modell**: Das Training-System ist implementiert aber deaktiviert (requires 50+ samples). Die Standard-Recognition nutzt OCR + phash + ORB.

5. **Keine EXE-Verpackung**: PyInstaller könnte für eine echte .exe verwendet werden, wurde aber nicht implementiert um Kompatibilitätsprobleme zu vermeiden. `start.bat` ist der Launcher.

## 8. Startanleitung

### Schnellstart
```
Doppelklick auf: start.bat
```

### Kommandozeile
```bash
cd C:\Users\LLM\Desktop\TCG-Cardscannprojekt\swu_card_manager

# GUI starten
.venv\Scripts\activate
python -m app gui

# Systemdiagnose
python -m app doctor

# Sets synchronisieren
python -m app sync

# Alle fehlenden Sets importieren
python -m app import-all

# Backup erstellen
python -m app backup
```

## 9. Backup-Anleitung

Backups werden automatisch in `project_state/backups/` erstellt.
- Automatisches Backup: alle 24 Stunden (konfigurierbar)
- Manuell: `python -m app backup` oder GUI → System → Backup erstellen
- Rotation: max. 10 Backups (konfigurierbar in config.yaml)
- Restore: `BackupManager.restore_backup(path)`

## 10. Recovery-Anleitung

Bei Absturz oder Unterbrechung:
1. Das System erkennt RUNNING-Tasks beim Neustart automatisch
2. Diese werden als FAILED markiert und können neu gestartet werden
3. Alle Operationen sind idempotent — kein Datenverlust bei Wiederholung
4. Backups ermöglichen Wiederherstellung bei Datenbankbeschädigung

```bash
# System nach Absturz prüfen
python -m app doctor

# Backup wiederherstellen
python -c "from app.core.backup import BackupManager; bm = BackupManager(); bm.restore_backup('project_state/backups/backup_XXXXXX.zip')"
```

## 11. Wartungsanleitung

- **Log-Dateien**: `logs/swu_manager.log` (rotierend, max 5MB x 5)
- **Datenbank-Integrität**: `python -m app doctor` prüft automatisch
- **Konfiguration**: `config/config.yaml` — alle Einstellungen zentral
- **Neue Sets**: System erkennt und importiert automatisch neue Sets
- **Training**: Aktivieren in config.yaml → `training.enabled: true`

## 12. Zukünftige Verbesserungen

1. **CUDA-Support**: Installation von `torch` mit CUDA für GPU-beschleunigtes Training
2. **Mobile Kamera-Unterstützung**: IP-Kamera-URL für Smartphone als Scanner
3. **Erweiterte OCR-Modelle**: EasyOCR oder Tesseract-Training für SWU-Kartentext
4. **Deep-Learning-Modell**: CNN für Kartenklassifikation bei ausreichend Trainingsdaten
5. **Preisverfolgung**: MarketPrice-Daten aus der API für Sammlungswert
6. **Export**: Excel/CSV-Export der Sammlung
7. **Deck-Builder**: Integration mit SWU-DB Deck-Builder
8. **Multiplayer-Trade**: Karten-Tausch-Verwaltung
