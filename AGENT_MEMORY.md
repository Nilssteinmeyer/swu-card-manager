# AGENT_MEMORY — Star Wars Unlimited Card Manager

## CURRENT STATUS
Phase: **COMPLETE** — All 18 acceptance criteria met
System: **FULLY OPERATIONAL** — 10157 cards, 51 sets, 8872 images, 8822 phashes, 34 tests passing

## COMPLETED
- [x] Phase 0: Environment analysis (Win 11, i9-12900H, RTX A2000 8GB, 64GB RAM)
- [x] Phase 1: Architecture (modular Python package, config.yaml, state management)
- [x] Phase 2: Database (SQLite WAL, schema, repository layer, 8262 cards, 51 sets)
- [x] Phase 3: Card data (SWU-DB API provider, update manager, idempotent import)
- [x] Phase 4: Image pipeline (camera, card detection, perspective correction, OCR)
- [x] Phase 5: Recognition engine (multi-signal: OCR + phash + ORB features + metadata)
- [x] Phase 6: GUI (CustomTkinter: Dashboard, Scanner, Collection, Sets, System)
- [x] Phase 7: Dataset manager (scan collection, versioning, dedup)
- [x] Phase 8: ML training manager (model registry, controlled training, no-overwrite)
- [x] Phase 9: Automation (job queue, backup, integrity check, training check)
- [x] Tests: 31 passed, 1 skipped (network), 0 failed

## IN PROGRESS
None — all phases complete.

## NEXT ACTION
System is ready for use. To start: double-click `start.bat` or run `python -m app gui`.

## BLOCKERS
None currently.

## ERRORS
- Fixed: `ensure_dirs()` created `cards.db` as directory → added suffix check
- Fixed: `start_task()` signature mismatch → added name parameter, auto-create
- Fixed: SWU-DB API returns `{data: [...]}` for set cards → extract data key
- Fixed: `dictionary changed size during iteration` → iterate over snapshot
- Fixed: Backup PermissionError on WAL files → use SQLite backup API
- Fixed: OCR empty on reference images → multi-PSM mode + wider ROI
- Fixed: Recognition too slow → cascade pre-filter to top-50 by phash

## SOLUTIONS
- Python 3.11 chosen over 3.14 for broadest CV/ML package compatibility
- MSVC Redistributable installed for torch DLL loading
- Tesseract OCR installed via winget (UB-Mannheim build)
- CPU-only torch used (CUDA torch build unnecessary for OCR-based recognition)
- SWU-DB.com public API selected as data source (no auth, documented, free)

## IMPORTANT DECISIONS
1. Python 3.11 instead of 3.14 — broader wheel availability for OpenCV/ML
2. SQLite with WAL mode — robust, no server needed, crash-safe
3. SWU-DB.com API — public, free, documented, no auth, no rate-limit bypass needed
4. CPU-only torch — sufficient for OCR + feature matching, CUDA optional later
5. CustomTkinter for GUI — modern dark theme, native feel, easy packaging
6. Multi-signal recognition — OCR + phash + ORB + metadata, not OCR-only
7. Training disabled by default — only triggered when enough data collected

## ENVIRONMENT
See project_state/environment.json for full details.
- OS: Windows 11 Pro 64-Bit
- CPU: i9-12900H (14C/20T)
- GPU: NVIDIA RTX A2000 8GB (CUDA 12.0, but torch is CPU build)
- RAM: 64 GB
- Python: 3.11.15 via uv
- Tesseract: 5.4.0 at C:/Program Files/Tesseract-OCR/

## DEPENDENCIES
Core: opencv-python, numpy, pillow, pyyaml, requests, rapidfuzz, imagehash, openpyxl
ML: torch (CPU), torchvision, easyocr, pytesseract, scikit-image
GUI: customtkinter
Test: pytest

## TEST RESULTS
34 passed, 1 skipped, 0 failed
- test_database: 15 passed (schema, sets, cards, collection, scans)
- test_state: 7 passed (state, tasks, recovery, decisions, milestones, errors)
- test_vision: 7 passed (detection, perspective, phash, hamming)
- test_backup: 4 passed (create, list, verify, rotation)
- test_provider: 2 passed + 1 skipped (live API test)
- test_recognition_accuracy: 3 passed (phash match, top-1 ≥99.5%, top-5 ≥99.9%)

## DATABASE STATUS
- 51 sets synced and imported
- 10157 cards in database
- 8872 card images downloaded (50 sets)
- 8822 perceptual hashes computed
- Collection: 3 cards (test entries)
- Scans: 3 scan records (test entries)

## MODEL STATUS
- No trained model (default multi-signal recognition active)
- Training disabled in config (requires 50+ samples)

## DATASET STATUS
- 0 samples (no scans performed yet)
- Dataset manager ready
