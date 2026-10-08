"""Tests for manual card add (/api/collection/add) + card search by number."""
import json
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.db.schema import connect, init_database
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
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('HMW', 'Homeworlds')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('HMW-160', 'HMW', '160', 'Noxious Refinery', 'Giftige Raffinerie')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('HMW-161', 'HMW', '161', 'Other Card', 'Andere Karte')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _CfgStub(db_path)))
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, db_path


def test_add_card_manual(app_client):
    client, db_path = app_client
    resp = client.post("/api/collection/add", json={
        "card_id": "HMW-160", "count": 2, "is_foil": False, "language": "de",
    })
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["added"] is True
    assert data["count"] == 2
    assert data["language"] == "de"

    conn = connect(db_path)
    cur = conn.execute("SELECT count, language, variant, is_foil, source FROM collection WHERE card_id='HMW-160'")
    row = cur.fetchone()
    assert row["count"] == 2
    assert row["language"] == "de"
    assert row["source"] == "manual"
    conn.close()


def test_add_card_foil(app_client):
    client, db_path = app_client
    resp = client.post("/api/collection/add", json={
        "card_id": "HMW-160", "count": 1, "is_foil": True, "language": "en",
    })
    assert resp.status_code == 200
    conn = connect(db_path)
    cur = conn.execute("SELECT variant, is_foil, language FROM collection WHERE card_id='HMW-160'")
    row = cur.fetchone()
    assert row["variant"] == "Foil"
    assert row["is_foil"] == 1
    assert row["language"] == "en"
    conn.close()


def test_add_card_unknown_rejected(app_client):
    client, _ = app_client
    resp = client.post("/api/collection/add", json={"card_id": "XXX-999"})
    assert resp.status_code == 404


def test_add_card_merge_same_variant(app_client):
    client, db_path = app_client
    client.post("/api/collection/add", json={"card_id": "HMW-160", "count": 2, "language": "de"})
    client.post("/api/collection/add", json={"card_id": "HMW-160", "count": 3, "language": "de"})
    conn = connect(db_path)
    cur = conn.execute("SELECT count FROM collection WHERE card_id='HMW-160'")
    assert cur.fetchone()["count"] == 5
    conn.close()


def test_search_by_number(app_client):
    client, _ = app_client
    # Search by full card id
    resp = client.get("/api/cards/search?q=HMW-160")
    data = json.loads(resp.get_data(as_text=True))
    assert data["count"] >= 1
    assert any(i["card_id"] == "HMW-160" for i in data["items"])

    # Search by plain number
    resp = client.get("/api/cards/search?q=160")
    data = json.loads(resp.get_data(as_text=True))
    assert any(i["card_id"] == "HMW-160" for i in data["items"])
