"""Tests: Registrierung ohne E-Mail (lokaler Modus, zweiter Haushalts-User)."""
import pytest


class TestRegisterNoEmail:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "reg_noemail.db")

        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "reg_noemail.db")
        init_database(conn)
        conn.commit()
        conn.close()

        from app.web.app import create_app
        from app.core.config import AppConfig

        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        app.config["SESSION_COOKIE_SECURE"] = False
        yield app.test_client()

    def test_second_user_registers_without_email(self, app_client):
        """User 1 (Admin) existiert; User 2 legt sich OHNE E-Mail an - muss klappen."""
        r1 = app_client.post("/api/auth/register", json={
            "username": "admin", "email": "admin@example.com", "password": "testpass123",
        })
        assert r1.status_code == 200

        # Ohne E-Mail-Feld komplett
        r2 = app_client.post("/api/auth/register", json={
            "username": "familie", "password": "meinpass123",
        })
        assert r2.status_code == 200, r2.get_data(as_text=True)
        body = r2.get_json()
        assert body.get("ok") is True

        # User ist eingeloggt und hat einen eigenen Haushalt
        me = app_client.get("/api/auth/me").get_json()
        assert me["user"]["username"] == "familie"
        assert me.get("households"), "eigener Haushalt fehlt"
        # Auto-E-Mail ist intern generiert (local.invalid), verification optional
        assert me["user"].get("email", "").endswith("@local.invalid")

    def test_empty_email_string_also_works(self, app_client):
        app_client.post("/api/auth/register", json={"username": "admin2", "email": "a@example.com", "password": "testpass123"})
        r = app_client.post("/api/auth/register", json={
            "username": "leer", "email": "", "password": "meinpass123",
        })
        assert r.status_code == 200, r.get_data(as_text=True)

    def test_username_still_validated(self, app_client):
        r = app_client.post("/api/auth/register", json={"username": "x", "password": "meinpass123"})
        assert r.status_code == 400
        assert "Benutzername" in r.get_json()["error"]

    def test_duplicate_username_still_rejected(self, app_client):
        app_client.post("/api/auth/register", json={"username": "dup", "email": "d@example.com", "password": "testpass123"})
        r = app_client.post("/api/auth/register", json={"username": "dup", "password": "anderes123"})
        assert r.status_code == 409
