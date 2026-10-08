"""Tests for collection export/import (via Flask test client on a temp DB)."""
import csv
import io
import json
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.db.schema import connect, init_database
from app.web.app import create_app


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """App factory with a temp database (config patched to it)."""
    db_path = Path(tmp_path) / "test.db"

    conn = connect(db_path)
    init_database(conn)
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker', 'Luke Skywalker')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('SOR-010', 'SOR', '010', 'Darth Vader', 'Darth Vader')")
    conn.execute("INSERT INTO collection (card_id, count, condition, language, variant, is_foil, household_id) VALUES ('SOR-005', 2, 'NM', 'en', 'Normal', 0, 1)")
    conn.commit()
    conn.close()

    # Patch AppConfig to use the temp DB
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _cfg_with_db(db_path)))
    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": "tester", "email": "tester@example.com", "password": "testpass123",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    yield client, db_path


class _CfgStub:
    """Minimal AppConfig stub that points at the test database."""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    def path(self, key: str) -> Path:
        if key == "database":
            return self._db_path
        # fall back to real config for other paths (images, models...)
        real = AppConfig.__wrapped_load__()
        return real.path(key)

    def get(self, key, default=None):
        return default


def _cfg_with_db(db_path: Path):
    if not hasattr(AppConfig, "__wrapped_load__"):
        AppConfig.__wrapped_load__ = AppConfig.load.__func__ if hasattr(AppConfig.load, "__func__") else AppConfig.load
    return _CfgStub(db_path)


def test_export_csv(app_client):
    client, db_path = app_client
    resp = client.get("/api/collection/export?format=csv")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    text = resp.get_data(as_text=True)
    reader = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    ids = {r["card_id"] for r in reader}
    assert "SOR-005" in ids
    sor5 = next(r for r in reader if r["card_id"] == "SOR-005")
    assert sor5["count"] == "2"


def test_export_json(app_client):
    client, db_path = app_client
    resp = client.get("/api/collection/export?format=json")
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["exported_at"]
    ids = {i["card_id"] for i in data["items"]}
    assert "SOR-005" in ids


def test_import_merge_increases_count(app_client):
    client, db_path = app_client
    csv_content = "card_id;count;condition;language;variant;is_foil\nSOR-005;3;NM;en;Normal;0\n"
    resp = client.post(
        "/api/collection/import",
        data={"file": (io.BytesIO(csv_content.encode()), "import.csv"), "mode": "merge"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    result = json.loads(resp.get_data(as_text=True))
    assert result["imported"] == 1
    assert result["mode"] == "merge"

    conn = connect(db_path)
    cur = conn.execute("SELECT count FROM collection WHERE card_id='SOR-005'")
    assert cur.fetchone()[0] == 5
    conn.close()


def test_import_replace_sets_count(app_client):
    client, db_path = app_client
    csv_content = "card_id;count;condition;language;variant;is_foil\nSOR-005;9;NM;en;Normal;0\n"
    resp = client.post(
        "/api/collection/import",
        data={"file": (io.BytesIO(csv_content.encode()), "import.csv"), "mode": "replace"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    conn = connect(db_path)
    cur = conn.execute("SELECT count FROM collection WHERE card_id='SOR-005'")
    assert cur.fetchone()[0] == 9
    conn.close()


def test_import_unknown_card_reports_error(app_client):
    client, db_path = app_client
    csv_content = "card_id;count\nXXX-999;1\n"
    resp = client.post(
        "/api/collection/import",
        data={"file": (io.BytesIO(csv_content.encode()), "import.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    result = json.loads(resp.get_data(as_text=True))
    assert result["imported"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["card_id"] == "XXX-999"
