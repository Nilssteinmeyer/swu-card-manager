"""Shared test fixtures: authenticated Flask test clients with isolated DBs."""
from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.db.schema import connect, init_database
from app.web.app import create_app


class _CfgStub:
    """Minimal AppConfig stub pointing at a test database."""

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


def _seed_reference_data(conn):
    """Minimal reference data so collection-scoped tests work."""
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute(
        "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de) "
        "VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker', 'Luke Skywalker')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de) "
        "VALUES ('SOR-010', 'SOR', '010', 'Darth Vader', 'Darth Vader')"
    )
    conn.commit()


def make_app(tmp_path, monkeypatch):
    """Create an app on a temp DB and return (app, db_path)."""
    db_path = Path(tmp_path) / "test.db"
    conn = connect(db_path)
    init_database(conn)
    _seed_reference_data(conn)
    conn.close()

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _CfgStub(db_path)))
    app = create_app()
    app.config["TESTING"] = True
    # Tests run over plain HTTP cookies
    app.config["SESSION_COOKIE_SECURE"] = False
    return app, db_path


def register_and_login(app, username="testuser", password="testpass123", email=None):
    """Register a user (first user = platform admin + legacy household 1)
    and return a logged-in test client."""
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": username,
        "email": email or f"{username}@example.com",
        "password": password,
    })
    assert resp.status_code == 200, f"register failed: {resp.get_data(as_text=True)}"
    # session cookie is set automatically by the client
    return client


def register_second_user(app, username="user2", password="testpass123"):
    """Register a second user (gets their own household)."""
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": username,
        "email": f"{username}@example.com",
        "password": password,
    })
    assert resp.status_code == 200
    return client


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    """App + one logged-in user owning household 1 (legacy data)."""
    app, db_path = make_app(tmp_path, monkeypatch)
    client = register_and_login(app)
    yield client, db_path, app


@pytest.fixture()
def two_users(tmp_path, monkeypatch):
    """App + two users: user1 owns household 1, user2 has their own household."""
    app, db_path = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")
    yield client1, client2, db_path, app
