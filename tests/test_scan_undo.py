"""Tests for scan undo: journaling + rollback incl. training photo cleanup."""
import json
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.db.schema import connect, init_database
from app.db import repository as repo
from app.web.app import create_app


class _CfgStub:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        if not hasattr(AppConfig, "__wrapped_load__"):
            AppConfig.__wrapped_load__ = AppConfig.load

    def path(self, key: str) -> Path:
        if key == "database":
            return self._db_path
        try:
            return AppConfig.__wrapped_load__().path(key)
        except Exception:
            return Path("data")

    def get(self, key, default=None):
        return default


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_path = Path(tmp_path) / "test.db"
    conn = connect(db_path)
    init_database(conn)
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _CfgStub(db_path)))
    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": "tester", "email": "tester@example.com", "password": "testpass123",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    yield client, db_path


def test_undo_new_entry_removes_it(app_client):
    client, db_path = app_client
    # Confirm a new card (collection empty -> entry created)
    resp = client.post("/api/scan/confirm", json={
        "scan_id": 1, "confirmed": True, "card_id": "SOR-005",
        "quantity": 2, "language": "de",
    })
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["undo_id"] is not None

    conn = connect(db_path)
    assert repo.get_collection_count(conn) == 2
    conn.close()

    # Undo
    resp = client.post("/api/scan/undo", json={})
    assert resp.status_code == 200
    result = json.loads(resp.get_data(as_text=True))
    assert result["undone"] is True
    assert result["card_id"] == "SOR-005"

    conn = connect(db_path)
    assert repo.get_collection_count(conn) == 0
    conn.close()


def test_undo_increment_restores_previous_count(app_client):
    client, db_path = app_client
    conn = connect(db_path)
    # Existing entry with 3 copies
    conn.execute("INSERT INTO collection (card_id, count, condition, language, variant, household_id) VALUES ('SOR-005', 3, 'NM', 'de', 'Normal', 1)")
    conn.commit()
    conn.close()

    # Confirm adds 2 -> count 5
    client.post("/api/scan/confirm", json={
        "scan_id": 1, "confirmed": True, "card_id": "SOR-005",
        "quantity": 2, "language": "de",
    })
    conn = connect(db_path)
    cur = conn.execute("SELECT count FROM collection WHERE card_id='SOR-005'")
    assert cur.fetchone()[0] == 5
    conn.close()

    # Undo -> back to 3
    resp = client.post("/api/scan/undo", json={})
    assert resp.status_code == 200
    conn = connect(db_path)
    cur = conn.execute("SELECT count FROM collection WHERE card_id='SOR-005'")
    assert cur.fetchone()[0] == 3
    conn.close()


def test_undo_deletes_training_photo(app_client, tmp_path):
    client, db_path = app_client
    # Fake a training photo on disk
    photo = tmp_path / "SOR-005_20260101_000000.jpg"
    photo.write_bytes(b"fake-jpg-data")

    # Build a 1x1 red PNG as base64 photo payload
    import base64
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(200, 30, 30)).save(buf, format="JPEG")
    photo_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    resp = client.post("/api/scan/confirm", json={
        "scan_id": 1, "confirmed": True, "card_id": "SOR-005",
        "quantity": 1, "language": "de", "photo": photo_b64,
    })
    data = json.loads(resp.get_data(as_text=True))
    assert data["photo_saved"] is True

    # The saved photo must exist now
    conn = connect(db_path)
    row = repo.get_last_undoable(conn)
    saved_path = Path(row["photo_path"])
    conn.close()
    assert saved_path.exists()

    # Undo -> photo must be gone
    client.post("/api/scan/undo", json={})
    assert not saved_path.exists()


def test_undo_only_once(app_client):
    client, db_path = app_client
    client.post("/api/scan/confirm", json={
        "scan_id": 1, "confirmed": True, "card_id": "SOR-005", "quantity": 1,
    })
    # First undo OK
    resp = client.post("/api/scan/undo", json={})
    assert resp.status_code == 200
    assert json.loads(resp.get_data(as_text=True))["undone"] is True
    # Second undo -> 404 (nothing left)
    resp = client.post("/api/scan/undo", json={})
    assert resp.status_code == 404


def test_undo_preview(app_client):
    client, db_path = app_client
    resp = client.get("/api/scan/undo/preview")
    assert json.loads(resp.get_data(as_text=True))["available"] is False

    client.post("/api/scan/confirm", json={
        "scan_id": 7, "confirmed": True, "card_id": "SOR-005", "quantity": 3,
    })
    resp = client.get("/api/scan/undo/preview")
    data = json.loads(resp.get_data(as_text=True))
    assert data["available"] is True
    assert data["card_id"] == "SOR-005"
    assert data["count"] == 3
