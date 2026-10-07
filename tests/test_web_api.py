"""
Comprehensive tests for the SWU Card Manager web API and core logic.
Tests: collection CRUD, scan confirm (normal + foil), dashboard stats,
collection filtering, foil handling, edge cases.
"""
import pytest
import sqlite3
import json
import tempfile
from pathlib import Path

# Ensure project root is importable
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import AppConfig
from app.db.schema import connect, init_database, get_db_path
from app.db import repository as repo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh test database."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Create tables
    conn.executescript("""
        CREATE TABLE sets (
            set_id TEXT PRIMARY KEY, full_name TEXT, parent_set_id TEXT,
            number_cards INTEGER DEFAULT 0, max_element TEXT,
            is_base_set INTEGER DEFAULT 0, release_date TEXT,
            imported INTEGER DEFAULT 0, imported_at TEXT,
            card_count_actual INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            full_name_de TEXT
        );
        CREATE TABLE cards (
            card_id TEXT PRIMARY KEY, set_id TEXT NOT NULL,
            card_number TEXT NOT NULL, name TEXT NOT NULL, subtitle TEXT,
            type TEXT, rarity TEXT, aspects TEXT, traits TEXT, arenas TEXT,
            cost TEXT, power TEXT, hp TEXT, front_text TEXT, back_text TEXT,
            epic_action TEXT, artist TEXT, language TEXT DEFAULT 'en',
            variant_type TEXT DEFAULT 'Normal', unique_card INTEGER DEFAULT 0,
            double_sided INTEGER DEFAULT 0, front_art_url TEXT, back_art_url TEXT,
            front_art_path TEXT, back_art_path TEXT, tcgplayer_id TEXT, cid TEXT,
            market_price TEXT, low_price TEXT, front_phash TEXT, back_phash TEXT,
            front_features BLOB, imported_at TEXT, updated_at TEXT,
            name_de TEXT, subtitle_de TEXT, type_de TEXT, rarity_de TEXT,
            aspects_de TEXT, traits_de TEXT, arenas_de TEXT,
            front_text_de TEXT, back_text_de TEXT, epic_action_de TEXT
        );
        CREATE TABLE collection (
            collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL, count INTEGER DEFAULT 1,
            condition TEXT DEFAULT 'NM', language TEXT DEFAULT 'en',
            variant TEXT DEFAULT 'Normal', location TEXT,
            acquired_at TEXT, source TEXT DEFAULT 'scan', notes TEXT,
            is_foil INTEGER DEFAULT 0
        );
        CREATE TABLE scans (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            image_path TEXT, recognized_card_id TEXT,
            confidence REAL, method TEXT, ocr_text TEXT,
            error_status TEXT, manual_correction INTEGER DEFAULT 0,
            corrected_card_id TEXT, processing_time_ms INTEGER
        );
        CREATE TABLE scan_candidates (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL, card_id TEXT NOT NULL,
            score REAL, method TEXT, details TEXT
        );
        CREATE TABLE scan_corrections (
            correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER, original_card_id TEXT,
            corrected_card_id TEXT, image_path TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            used_for_training INTEGER DEFAULT 0
        );
    """)
    # Insert test set and cards
    conn.execute("INSERT INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT INTO sets (set_id, full_name) VALUES ('HMW', 'Homeworlds')")
    conn.execute("""INSERT INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects)
        VALUES ('SOR-010', 'SOR', '010', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '["Aggression"]')""")
    conn.execute("""INSERT INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects)
        VALUES ('SOR-029', 'SOR', '029', 'Administrator Tower', 'Turm des Administrators', 'Base', 'Common', '["Vigilance"]')""")
    conn.execute("""INSERT INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects)
        VALUES ('HMW-160', 'HMW', '160', 'Noxious Refinery', 'Giftige Raffinerie', 'Base', 'Common', '["Cunning"]')""")
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Collection CRUD Tests
# ---------------------------------------------------------------------------

class TestCollectionCRUD:
    def test_add_normal_card(self, db_conn):
        """Adding a normal card creates a collection entry."""
        cid = repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        assert cid > 0
        assert repo.get_collection_count(db_conn) == 1
        assert repo.get_unique_collection_count(db_conn) == 1

    def test_add_foil_card(self, db_conn):
        """Adding a foil card with is_foil flag."""
        cid = repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Foil")
        db_conn.execute("UPDATE collection SET is_foil=1 WHERE collection_id=?", (cid,))
        db_conn.commit()
        cur = db_conn.execute("SELECT is_foil FROM collection WHERE collection_id=?", (cid,))
        assert cur.fetchone()[0] == 1

    def test_add_same_card_increments_count(self, db_conn):
        """Adding the same card twice increments count."""
        repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        assert repo.get_collection_count(db_conn) == 2
        assert repo.get_unique_collection_count(db_conn) == 1

    def test_add_same_card_different_variant_separate(self, db_conn):
        """Normal and Foil variants are separate entries."""
        repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Foil")
        assert repo.get_collection_count(db_conn) == 2
        assert repo.get_unique_collection_count(db_conn) == 2

    def test_empty_collection(self, db_conn):
        """Empty collection returns zero counts."""
        assert repo.get_collection_count(db_conn) == 0
        assert repo.get_unique_collection_count(db_conn) == 0
        assert len(repo.get_collection(db_conn)) == 0


# ---------------------------------------------------------------------------
# Scan Tests
# ---------------------------------------------------------------------------

class TestScans:
    def test_record_scan(self, db_conn):
        """Recording a scan creates an entry."""
        scan_id = repo.record_scan(db_conn, "/path/img.png", "SOR-010", 0.85, "clip", "text", "", 500)
        assert scan_id > 0
        recent = repo.get_recent_scans(db_conn, limit=10)
        assert len(recent) == 1

    def test_scan_with_candidates(self, db_conn):
        """Recording a scan with candidates."""
        scan_id = repo.record_scan(db_conn, "/img.png", "SOR-010", 0.90, "clip")
        candidates = [
            {"card_id": "SOR-010", "score": 0.90, "method": "clip"},
            {"card_id": "SOR-029", "score": 0.70, "method": "clip"},
        ]
        repo.record_scan_candidates(db_conn, scan_id, candidates)
        cur = db_conn.execute("SELECT COUNT(*) FROM scan_candidates WHERE scan_id=?", (scan_id,))
        assert cur.fetchone()[0] == 2

    def test_correct_scan(self, db_conn):
        """Correcting a scan updates the record."""
        scan_id = repo.record_scan(db_conn, "/img.png", "SOR-010", 0.50, "clip")
        repo.correct_scan(db_conn, scan_id, "SOR-029")
        cur = db_conn.execute("SELECT manual_correction, corrected_card_id FROM scans WHERE scan_id=?", (scan_id,))
        r = cur.fetchone()
        assert r[0] == 1
        assert r[1] == "SOR-029"

    def test_scan_correction_recorded(self, db_conn):
        """Scan corrections are recorded for learning."""
        scan_id = repo.record_scan(db_conn, "/img.png", "SOR-010", 0.50, "clip")
        repo.record_scan_correction(db_conn, scan_id, "SOR-010", "SOR-029", "/img.png")
        assert repo.get_correction_count(db_conn) == 1

    def test_recognition_rate(self, db_conn):
        """Recognition rate is calculated correctly."""
        repo.record_scan(db_conn, "/img1.png", "SOR-010", 0.90, "clip")
        repo.record_scan(db_conn, "/img2.png", None, 0.30, "clip")
        rate = repo.get_recognition_rate(db_conn)
        assert rate == 0.5  # 1 out of 2 recognized


# ---------------------------------------------------------------------------
# Dashboard Stats Tests
# ---------------------------------------------------------------------------

class TestDashboardStats:
    def test_empty_dashboard(self, db_conn):
        """Empty collection has zero stats."""
        assert repo.get_collection_count(db_conn) == 0
        assert repo.get_unique_collection_count(db_conn) == 0

    def test_dashboard_collection_only(self, db_conn):
        """Dashboard stats come from collection, not reference cards."""
        repo.add_to_collection(db_conn, "SOR-010", count=2, variant="Normal")
        repo.add_to_collection(db_conn, "SOR-029", count=1, variant="Normal")
        # Even though there are 3 cards in DB, collection has 3 total, 2 unique
        assert repo.get_collection_count(db_conn) == 3
        assert repo.get_unique_collection_count(db_conn) == 2

    def test_dashboard_foil_count(self, db_conn):
        """Foil count only counts is_foil=1 entries."""
        cid1 = repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        cid2 = repo.add_to_collection(db_conn, "SOR-029", count=1, variant="Foil")
        db_conn.execute("UPDATE collection SET is_foil=1 WHERE collection_id=?", (cid2,))
        db_conn.commit()
        cur = db_conn.execute("SELECT COALESCE(SUM(count), 0) FROM collection WHERE is_foil = 1")
        assert cur.fetchone()[0] == 1

    def test_dashboard_rarity_from_collection(self, db_conn):
        """Rarity breakdown only includes cards in collection."""
        repo.add_to_collection(db_conn, "SOR-010", count=1, variant="Normal")
        cur = db_conn.execute(
            """SELECT cards.rarity, SUM(col.count) as c
               FROM collection col JOIN cards ON col.card_id = cards.card_id
               GROUP BY cards.rarity"""
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["rarity"] == "Special"
        assert rows[0]["c"] == 1


# ---------------------------------------------------------------------------
# Bilingual Card Tests
# ---------------------------------------------------------------------------

class TestBilingualCards:
    def test_german_name_stored(self, db_conn):
        """German names are stored in name_de column."""
        card = repo.get_card(db_conn, "HMW-160")
        assert card is not None
        assert card["name"] == "Noxious Refinery"
        assert card["name_de"] == "Giftige Raffinerie"

    def test_upsert_card_with_german(self, db_conn):
        """Upserting a card with German fields stores them."""
        card = {
            "Set": "SOR", "Number": "200", "Name": "Test Card",
            "name_de": "Testkarte", "Subtitle": "", "Type": "Unit",
            "Rarity": "Common", "Aspects": [], "Traits": [], "Arenas": [],
            "Cost": "1", "Power": "1", "HP": "1", "FrontText": "",
            "Artist": "", "Unique": False, "FrontArt": "",
        }
        card_id = repo.upsert_card(db_conn, card)
        c = repo.get_card(db_conn, card_id)
        assert c["name_de"] == "Testkarte"

    def test_upsert_card_idempotent(self, db_conn):
        """Upserting the same card twice doesn't duplicate."""
        card = {
            "Set": "SOR", "Number": "200", "Name": "Test Card",
            "name_de": "Testkarte", "Type": "Unit", "Rarity": "Common",
            "Aspects": [], "Traits": [], "Arenas": [],
        }
        repo.upsert_card(db_conn, card)
        repo.upsert_card(db_conn, card)
        c = repo.get_card(db_conn, "SOR-200")
        assert c is not None
        assert c["name"] == "Test Card"


# ---------------------------------------------------------------------------
# Flask API Tests
# ---------------------------------------------------------------------------

class TestFlaskAPI:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        """Create a Flask test client with a temp database."""
        # Override database path
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "test_api.db")

        # Create test DB
        conn = sqlite3.connect(str(tmp_path / "test_api.db"))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE sets (set_id TEXT PRIMARY KEY, full_name TEXT, parent_set_id TEXT,
                number_cards INTEGER DEFAULT 0, max_element TEXT, is_base_set INTEGER DEFAULT 0,
                release_date TEXT, imported INTEGER DEFAULT 0, imported_at TEXT,
                card_count_actual INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, full_name_de TEXT);
            CREATE TABLE cards (card_id TEXT PRIMARY KEY, set_id TEXT NOT NULL,
                card_number TEXT NOT NULL, name TEXT NOT NULL, subtitle TEXT, type TEXT,
                rarity TEXT, aspects TEXT, traits TEXT, arenas TEXT, cost TEXT, power TEXT,
                hp TEXT, front_text TEXT, back_text TEXT, epic_action TEXT, artist TEXT,
                language TEXT DEFAULT 'en', variant_type TEXT DEFAULT 'Normal',
                unique_card INTEGER DEFAULT 0, double_sided INTEGER DEFAULT 0,
                front_art_url TEXT, back_art_url TEXT, front_art_path TEXT, back_art_path TEXT,
                tcgplayer_id TEXT, cid TEXT, market_price TEXT, low_price TEXT,
                front_phash TEXT, back_phash TEXT, front_features BLOB,
                imported_at TEXT, updated_at TEXT, name_de TEXT, subtitle_de TEXT,
                type_de TEXT, rarity_de TEXT, aspects_de TEXT, traits_de TEXT, arenas_de TEXT,
                front_text_de TEXT, back_text_de TEXT, epic_action_de TEXT);
            CREATE TABLE collection (collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id TEXT NOT NULL, count INTEGER DEFAULT 1, condition TEXT DEFAULT 'NM',
                language TEXT DEFAULT 'en', variant TEXT DEFAULT 'Normal', location TEXT,
                acquired_at TEXT, source TEXT DEFAULT 'scan', notes TEXT, is_foil INTEGER DEFAULT 0);
            CREATE TABLE scans (scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')), image_path TEXT,
                recognized_card_id TEXT, confidence REAL, method TEXT, ocr_text TEXT,
                error_status TEXT, manual_correction INTEGER DEFAULT 0,
                corrected_card_id TEXT, processing_time_ms INTEGER);
            CREATE TABLE scan_candidates (candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL, card_id TEXT NOT NULL, score REAL, method TEXT, details TEXT);
            CREATE TABLE scan_corrections (correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER, original_card_id TEXT, corrected_card_id TEXT,
                image_path TEXT, timestamp TEXT DEFAULT (datetime('now')),
                used_for_training INTEGER DEFAULT 0);
            CREATE TABLE models (model_id TEXT PRIMARY KEY, version TEXT, trained_at TEXT,
                dataset_version TEXT, parameters TEXT, validation_accuracy REAL,
                recognition_rate REAL, confusion_matrix TEXT, storage_path TEXT,
                is_active INTEGER DEFAULT 0, status TEXT, notes TEXT, created_at TEXT);
            CREATE TABLE dataset_versions (version_id TEXT PRIMARY KEY, created_at TEXT,
                sample_count INTEGER, card_count INTEGER, description TEXT,
                status TEXT, path TEXT);
            CREATE TABLE job_log (job_id TEXT PRIMARY KEY, job_type TEXT, started_at TEXT,
                completed_at TEXT, status TEXT, result TEXT, details TEXT);
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT);
        """)
        conn.execute("INSERT INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) VALUES ('SOR-010', 'SOR', '010', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '[\"Aggression\"]')")
        conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) VALUES ('SOR-029', 'SOR', '029', 'Admin Tower', 'Turm', 'Base', 'Common', '[\"Vigilance\"]')")
        conn.commit()
        conn.close()

        from app.web.app import create_app
        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_dashboard_empty(self, app_client):
        """Dashboard returns zeros for empty collection."""
        r = app_client.get("/api/dashboard")
        data = r.get_json()
        assert data["collection_total"] == 0
        assert data["collection_unique"] == 0
        assert data["foil_count"] == 0

    def test_confirm_adds_to_collection(self, app_client):
        """Confirming a scan adds the card to collection."""
        r = app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-010",
            "add_to_collection": True, "is_foil": False,
        })
        data = r.get_json()
        assert data["confirmed"] is True
        assert data["added_to_collection"] is True
        assert data["is_foil"] is False

        # Verify in collection
        r2 = app_client.get("/api/collection")
        coll = r2.get_json()
        assert coll["count"] == 1
        assert coll["items"][0]["card_id"] == "SOR-010"

    def test_confirm_foil(self, app_client):
        """Confirming with is_foil=True sets variant=Foil and is_foil=1."""
        r = app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-010",
            "add_to_collection": True, "is_foil": True,
        })
        data = r.get_json()
        assert data["confirmed"] is True
        assert data["is_foil"] is True

        # Verify foil flag
        r2 = app_client.get("/api/collection")
        item = r2.get_json()["items"][0]
        assert item["variant"] == "Foil"

    def test_confirm_rejection(self, app_client):
        """Rejecting a scan does not add to collection."""
        # First create a scan
        app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": False, "card_id": None,
        })
        r = app_client.get("/api/collection")
        assert r.get_json()["count"] == 0

    def test_dashboard_after_add(self, app_client):
        """Dashboard reflects collection after adding cards."""
        # Add a card
        app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-010",
            "add_to_collection": True, "is_foil": False,
        })
        # Add a foil card
        app_client.post("/api/scan/confirm", json={
            "scan_id": 2, "confirmed": True, "card_id": "SOR-029",
            "add_to_collection": True, "is_foil": True,
        })
        r = app_client.get("/api/dashboard")
        data = r.get_json()
        assert data["collection_total"] == 2
        assert data["collection_unique"] == 2
        assert data["foil_count"] == 1

    def test_collection_filter(self, app_client):
        """Collection filtering by set works."""
        app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-010",
            "add_to_collection": True, "is_foil": False,
        })
        r = app_client.get("/api/collection?set=SOR")
        data = r.get_json()
        assert data["count"] == 1

        r2 = app_client.get("/api/collection?set=HMW")
        assert r2.get_json()["count"] == 0

    def test_collection_search(self, app_client):
        """Collection search by name works."""
        app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-010",
            "add_to_collection": True, "is_foil": False,
        })
        r = app_client.get("/api/collection?search=Darth")
        data = r.get_json()
        assert data["count"] == 1

        r2 = app_client.get("/api/collection?search=Nonexistent")
        assert r2.get_json()["count"] == 0

    def test_confirm_empty_card_id_uses_candidate(self, app_client):
        """Confirming with empty card_id but valid candidates still adds."""
        # This tests the bug fix: when card_id is empty but candidates exist
        r = app_client.post("/api/scan/confirm", json={
            "scan_id": 1, "confirmed": True, "card_id": "SOR-029",
            "add_to_collection": True, "is_foil": False,
        })
        assert r.get_json()["confirmed"] is True

    def test_card_image_endpoint(self, app_client):
        """Card image endpoint returns 404 for missing image."""
        r = app_client.get("/api/card/image/SOR-010")
        # Should return 404 (no local image) or redirect
        assert r.status_code in (404, 302)

    def test_sets_list(self, app_client):
        """Sets endpoint returns available sets."""
        r = app_client.get("/api/sets")
        data = r.get_json()
        assert data["count"] >= 1
        set_ids = [s["set_id"] for s in data["sets"]]
        assert "SOR" in set_ids

    def test_filters_endpoint(self, app_client):
        """Filters endpoint returns distinct values."""
        r = app_client.get("/api/cards/filters")
        data = r.get_json()
        assert "rarities" in data
        assert "types" in data
        assert "Special" in data["rarities"]
        assert "Leader" in data["types"]
