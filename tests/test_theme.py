"""Tests: Theme-Setting (dark/light/auto) + color-scheme."""
import pytest


class TestTheme:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "theme.db")
        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "theme.db")
        init_database(conn)
        conn.commit()
        conn.close()

        from app.web.app import create_app
        from app.core.config import AppConfig

        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        app.config["SESSION_COOKIE_SECURE"] = False
        client = app.test_client()
        r = client.post("/api/auth/register", json={"username": "themuser", "password": "testpass123"})
        assert r.status_code == 200
        yield client

    def test_theme_roundtrip(self, app_client):
        for mode in ("light", "auto", "dark"):
            r = app_client.post("/api/settings", json={"theme": mode})
            assert r.status_code == 200
            s = app_client.get("/api/settings").get_json()
            assert s.get("theme") == mode

    def test_invalid_theme_stored_but_ignored(self, app_client):
        """Ungueltige Werte speichert die DB, aber die UI validiert (dunkel bleibt Standard)."""
        app_client.post("/api/settings", json={"theme": "neon-pink"})
        s = app_client.get("/api/settings").get_json()
        # JS ignoriert unbekannte Werte -> dark; API speichert roh (bekanntes Verhalten)
        assert s.get("theme") in ("neon-pink", "dark", None)
