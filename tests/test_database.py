"""Database tests — schema, CRUD, collection, scans."""
import pytest
import tempfile
import os
from pathlib import Path

from app.core.config import AppConfig
from app.db.schema import connect, init_database, check_integrity
from app.db import repository as repo


@pytest.fixture
def db_conn(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_database(conn)
    yield conn
    conn.close()


class TestSchema:
    def test_init_database(self, db_conn):
        """Database initialises with all tables."""
        cur = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        assert "cards" in tables
        assert "sets" in tables
        assert "collection" in tables
        assert "scans" in tables
        assert "scan_candidates" in tables
        assert "models" in tables
        assert "dataset_versions" in tables
        assert "job_log" in tables

    def test_integrity_check(self, db_conn):
        assert check_integrity(db_conn) is True


class TestSets:
    def test_upsert_set(self, db_conn):
        set_data = {
            "setId": "SOR",
            "fullName": "Spark of Rebellion",
            "numberCards": 262,
            "maxElement": "262",
            "isBaseSet": True,
            "releaseDate": "3/8/24",
        }
        repo.upsert_set(db_conn, set_data)
        s = repo.get_set(db_conn, "SOR")
        assert s is not None
        assert s["full_name"] == "Spark of Rebellion"
        assert s["number_cards"] == 262

    def test_upsert_set_idempotent(self, db_conn):
        set_data = {"setId": "SOR", "fullName": "Spark of Rebellion", "numberCards": 262}
        repo.upsert_set(db_conn, set_data)
        repo.upsert_set(db_conn, set_data)
        sets = repo.get_all_sets(db_conn)
        assert len(sets) == 1


class TestCards:
    @pytest.fixture
    def setup_set(self, db_conn):
        repo.upsert_set(db_conn, {"setId": "SOR", "fullName": "Spark of Rebellion", "numberCards": 262})

    def test_upsert_card(self, db_conn, setup_set):
        card = {
            "Set": "SOR",
            "Number": "010",
            "Name": "Darth Vader",
            "Subtitle": "Dark Lord of the Sith",
            "Type": "Leader",
            "Rarity": "Special",
            "Aspects": ["Aggression", "Villainy"],
            "Traits": ["FORCE", "IMPERIAL", "SITH"],
            "Arenas": ["Ground"],
            "Cost": "7",
            "Power": "5",
            "HP": "8",
            "FrontText": "Action text here",
            "Artist": "Borja Pindado",
            "Unique": True,
            "FrontArt": "https://cdn.swu-db.com/images/cards/SOR/010.png",
        }
        card_id = repo.upsert_card(db_conn, card)
        assert card_id == "SOR-010"
        c = repo.get_card(db_conn, "SOR-010")
        assert c is not None
        assert c["name"] == "Darth Vader"
        assert c["type"] == "Leader"

    def test_upsert_card_idempotent(self, db_conn, setup_set):
        card = {"Set": "SOR", "Number": "010", "Name": "Darth Vader"}
        repo.upsert_card(db_conn, card)
        repo.upsert_card(db_conn, card)
        assert repo.get_card_count(db_conn) == 1

    def test_get_cards_by_set(self, db_conn, setup_set):
        for i in range(5):
            repo.upsert_card(db_conn, {"Set": "SOR", "Number": f"{i:03d}", "Name": f"Card {i}"})
        cards = repo.get_cards_by_set(db_conn, "SOR")
        assert len(cards) == 5


class TestCollection:
    @pytest.fixture
    def setup_card(self, db_conn):
        repo.upsert_set(db_conn, {"setId": "SOR", "fullName": "Test", "numberCards": 1})
        repo.upsert_card(db_conn, {"Set": "SOR", "Number": "010", "Name": "Test Card"})

    def test_add_to_collection(self, db_conn, setup_card):
        repo.add_to_collection(db_conn, "SOR-010")
        assert repo.get_collection_count(db_conn) == 1
        assert repo.get_unique_collection_count(db_conn) == 1

    def test_add_duplicate_increments(self, db_conn, setup_card):
        repo.add_to_collection(db_conn, "SOR-010", count=1)
        repo.add_to_collection(db_conn, "SOR-010", count=2)
        assert repo.get_collection_count(db_conn) == 3
        assert repo.get_unique_collection_count(db_conn) == 1


class TestScans:
    def test_record_scan(self, db_conn):
        scan_id = repo.record_scan(db_conn, "/path/to/img.png", None, 0.5, "ocr", "text", "", 100)
        assert scan_id > 0
        recent = repo.get_recent_scans(db_conn, limit=10)
        assert len(recent) == 1

    def test_scan_with_candidates(self, db_conn):
        repo.upsert_set(db_conn, {"setId": "SOR", "fullName": "Test", "numberCards": 1})
        repo.upsert_card(db_conn, {"Set": "SOR", "Number": "010", "Name": "Test"})
        scan_id = repo.record_scan(db_conn, "/path/img.png", "SOR-010", 0.85, "multi_signal")
        candidates = [{"card_id": "SOR-010", "score": 0.85, "method": "multi_signal"}]
        repo.record_scan_candidates(db_conn, scan_id, candidates)
        assert repo.get_scan_count_today(db_conn) == 1
