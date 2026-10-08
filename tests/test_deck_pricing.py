"""Tests for the deck pricing endpoint."""
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
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker', 'Luke Skywalker')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('SOR-010', 'SOR', '010', 'Darth Vader', 'Darth Vader')")
    # owned: 1x Luke
    conn.execute("INSERT INTO collection (card_id, count, household_id) VALUES ('SOR-005', 1, 1)")
    # prices: Luke 4.90, Vader 12.50
    conn.execute("INSERT INTO card_mkm_map (card_id, idProduct) VALUES ('SOR-005', 401005)")
    conn.execute("INSERT INTO card_mkm_map (card_id, idProduct) VALUES ('SOR-010', 401010)")
    conn.execute("INSERT INTO card_prices (idProduct, trend, foil_trend) VALUES (401005, 4.90, 15.50)")
    conn.execute("INSERT INTO card_prices (idProduct, trend, foil_trend) VALUES (401010, 12.50, 38.00)")
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
    yield client


def test_deck_price_with_explicit_ids(app_client):
    deck = "2x Luke Skywalker (SOR-005)\n1 Darth Vader (SOR-010)"
    resp = app_client.post("/api/deck/price", json={"list": deck})
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert len(data["items"]) == 2
    assert data["total_price"] == pytest.approx(2 * 4.90 + 12.50)
    # Luke: own 1 of 2 -> 1 missing; Vader: own 0 of 1 -> 1 missing
    assert data["missing_price"] == pytest.approx(4.90 + 12.50)
    assert data["currency"] == "EUR"
    luke = next(i for i in data["items"] if i["card_id"] == "SOR-005")
    assert luke["owned"] == 1
    assert luke["needed"] == 1


def test_deck_price_fuzzy_name_match(app_client):
    deck = "Luke Skywalker"
    resp = app_client.post("/api/deck/price", json={"list": deck})
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert len(data["items"]) == 1
    assert data["items"][0]["card_id"] == "SOR-005"


def test_deck_price_unknown_line_reported(app_client):
    deck = "Completely Unknown Card Name XYZ"
    resp = app_client.post("/api/deck/price", json={"list": deck})
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["items"] == []
    assert data["unknown_lines"] == [deck]


def test_deck_price_empty_list_rejected(app_client):
    resp = app_client.post("/api/deck/price", json={"list": ""})
    assert resp.status_code == 400
