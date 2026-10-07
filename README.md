# SWU Card Manager

Lokale Web-Anwendung zur Verwaltung und **kamerabasierten Erkennung** von Star Wars: Unlimited Sammelkarten (Deutsch + Englisch).

## Features

- 📷 **Karten scannen** — Handy-Kamera (Rückkamera via Browser), automatischer Scan mit Bestätigungs-Popup (Ja / Nein / Abbrechen, Foil-Haken, Mengenauswahl +/−)
- 🧠 **CLIP Deep-Learning-Erkennung** — fine-getuntes CLIP ViT-B/32 + FAISS-Vektorindex über 8.800+ Referenzkarten, robust gegen Perspektive, Beleuchtung und Hintergrund
- 🇩🇪 **Zweisprachig** — deutsche + englische Kartennamen (OCR `deu+eng`, Matching gegen beide Sprachen)
- ✨ **Foil & Menge** — beim Bestätigen ankreuzbar bzw. einstellbar
- 📱 **Mobile-First Web-UI** — vom iPhone im WLAN nutzbar (HTTPS mit selbstsigniertem Zertifikat, iOS-Kamera-fähig), haptisches Feedback (Vibration)
- 📊 **Dashboard** — Statistiken ausschließlich über die eigene Sammlung (klar getrennt von Referenz-/Trainingsdaten)
- 🗂️ **Sammlung** — Cardmarket-artige Filterung (Name, Set, Seltenheit, Typ, Aspekt, Trait, Sortierung)
- ⚙️ **Einstellungen** — Scan-Intervall, Auflösung, JPEG-Qualität, Auto-Accept-Schwelle, Kandidaten-Anzahl, Kamera (Rück/Front), Vibrationsmuster, Stabilisierungszeit u.v.m.
- 🎓 **Learning-from-Corrections** — falsche Erkennungen werden als Trainingsdaten gespeichert; bestätigte Fotos landen unter `data/scans/confirmed/` (Löschen einer falsch erkannten Karte entfernt das zugehörige Foto)
- 📦 **Sets** — Import aller 51 Sets inkl. Bilder; neue Sets per Button nachladbar
- 💾 **Backups** — versionierte ZIP-Backups der SQLite-DB
- 🩺 **Selbstdiagnose** — `python -m app doctor` (14 Checks)

## Architektur

```
swu_card_manager/
├── app/
│   ├── core/          # Config, Logging, State/Checkpoint, Backup, Jobs, Doctor
│   ├── db/            # SQLite-Schema, Migrations, Repository-Layer
│   ├── providers/     # SWU-DB-API + offizielle FFG-API (bilingual), Update-Manager
│   ├── vision/        # Kamera, Kartenerkennung, Perspektivkorrektur, OCR (Tesseract deu+eng)
│   ├── recognition/   # CLIP-Engine (clip_engine.py) + klassische Engine (engine.py)
│   ├── ml/            # CLIP-Fine-Tuning (on-the-fly Augmentation), Dataset, Model-Registry
│   ├── web/           # Flask-Backend (REST-API) + HTML/CSS/JS-Frontend
│   └── __main__.py    # CLI (web / doctor / sync / import-all / scan / backup)
├── config/config.yaml
├── data/              # SQLite-DB, Kartenbilder, Modelle (nicht im Repo)
├── tessdata/          # deutsche Tesseract-Sprachdaten
├── tests/             # 59 Unit-/API-Tests
└── start_web.bat      # Windows-Launcher (HTTPS auf 0.0.0.0:8765)
```

## Schnellstart

```bat
:: Windows: Doppelklick auf start_web.bat, oder:
cd swu_card_manager
.venv\Scripts\activate
python -m app web --host 0.0.0.0 --port 8765
```

Dann im Browser öffnen:
- Am PC: `https://localhost:8765`
- Am Handy (gleiches WLAN): `https://<PC-IP>:8765` — Zertifikat-Warnung einmalig bestätigen (iOS: „Details" → „Diese Website besuchen")

## Erkennungs-Pipeline

1. **Preprocessing** — Kartenbereich wird normalisiert (Rotate/Resize auf 750×1050)
2. **OCR** (Tesseract `deu+eng`) — Titel + Kartennummer als Zusatzsignal
3. **CLIP-Embedding** des Kamerabilds (512-dim, L2-normalisiert, GPU via CUDA)
4. **FAISS-Cosine-Suche** über vorberechnete Referenz-Embeddings (Top-K)
5. **Re-Ranking** mit perceptual Hash + OCR-Fuzzy-Match (bilingual)
6. **Cascade-Scoring** — CLIP dominant, kalibriert für echte Handyfotos
7. **Popup** — beste Karte + Alternativ-Kandidaten, Nutzer bestätigt (Ja / Nein / Abbrechen, Foil, Menge)

## Befehle

```bash
python -m app web         # Web-UI starten (HTTPS)
python -m app doctor       # Systemdiagnose
python -m app sync         # Sets synchronisieren
python -m app import-all   # Alle Sets importieren
python -m app backup       # Backup erstellen
```

## Tests

```bash
python -m pytest tests/ --ignore=tests/test_recognition_accuracy.py   # 59 Tests, schnell
python -m pytest tests/test_recognition_accuracy.py                   # Top-1/Top-5-Accuracy (langsamer)
```

## Tech Stack

| Bereich | Technologie |
|---|---|
| Backend | Python 3.11, Flask, flask-cors |
| ML | PyTorch 2.5.1+cu121 (RTX A2000 8GB), open-clip-torch, FAISS |
| CV | OpenCV 5.0, Tesseract 5.4 (`deu+eng`, lokale tessdata) |
| DB | SQLite (WAL-Modus) |
| Frontend | Vanilla HTML/CSS/JS (dunkles Theme, mobile-first) |
| Datenquellen | SWU-DB-API (`api.swu-db.com`), offizielle FFG-API (`admin.starwarsunlimited.com`, bilingual DE/EN) |

## Datenschutz

Alle Bilder und Daten bleiben lokal. Netzwerkzugriffe nur für Kartendaten-Import von öffentlichen APIs. Keine Cloud, keine externen KI-Dienste bei der Erkennung.
