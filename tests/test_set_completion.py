"""Tests for set completion tracking."""
from pathlib import Path

import pytest

from app.db.schema import connect, init_database
from app.db import repository as repo


@pytest.fixture()
def db(tmp_path):
    conn = connect(Path(tmp_path) / "test.db")
    init_database(conn)
    # Set SOR with 5 unique cards
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    for card_id, num, name in [
        ("SOR-001", "001", "Director Krennic"),
        ("SOR-002", "002", "Iden Versio"),
        ("SOR-003", "003", "Chewbacca"),
        ("SOR-004", "004", "Chirrut Îmwe"),
        ("SOR-005", "005", "Luke Skywalker"),
    ]:
        conn.execute(
            "INSERT INTO cards (card_id, set_id, card_number, name) VALUES (?, 'SOR', ?, ?)",
            (card_id, num, name),
        )
    conn.commit()
    yield conn
    conn.close()


def test_completion_empty_collection(db):
    stats = repo.get_set_completion(db, "SOR")
    assert stats["total_unique"] == 5
    assert stats["owned_unique"] == 0
    assert stats["percent"] == 0.0
    assert stats["missing_count"] == 5


def test_completion_with_owned_cards(db):
    # Own 2 of 5 cards
    db.execute("INSERT INTO collection (card_id, count, is_foil, household_id) VALUES ('SOR-001', 1, 0, 1)")
    db.execute("INSERT INTO collection (card_id, count, is_foil, household_id) VALUES ('SOR-002', 1, 1, 1)")
    db.commit()
    stats = repo.get_set_completion(db, "SOR")
    assert stats["owned_unique"] == 2
    assert stats["percent"] == 40.0
    assert stats["missing_count"] == 3
    missing_ids = {m["card_id"] for m in stats["missing"]}
    assert "SOR-001" not in missing_ids
    assert "SOR-002" not in missing_ids
    assert "SOR-003" in missing_ids


def test_completion_full_set(db):
    for card_id in ("SOR-001", "SOR-002", "SOR-003", "SOR-004", "SOR-005"):
        db.execute("INSERT INTO collection (card_id, count, household_id) VALUES (?, 1, 1)", (card_id,))
    db.commit()
    stats = repo.get_set_completion(db, "SOR")
    assert stats["percent"] == 100.0
    assert stats["missing_count"] == 0
    assert stats["missing"] == []


def test_completion_all_sets_summary(db):
    db.execute("INSERT INTO collection (card_id, count, household_id) VALUES ('SOR-001', 1, 1)")
    db.commit()
    results = repo.get_all_set_completion(db)
    sor = next((s for s in results if s["set_id"] == "SOR"), None)
    assert sor is not None
    assert sor["owned_unique"] == 1
    assert "missing" not in sor  # summary has no missing list
