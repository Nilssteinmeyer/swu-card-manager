"""Tests: Sammlung umbenennen (Owner-only) + Name im Dashboard."""
import pytest


class TestHouseholdRename:
    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "rename.db")
        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "rename.db")
        init_database(conn)
        conn.commit()
        conn.close()

        from app.web.app import create_app
        from app.core.config import AppConfig

        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        app.config["SESSION_COOKIE_SECURE"] = False
        return app

    def _register(self, app, username, password="testpass123"):
        client = app.test_client()
        r = client.post("/api/auth/register", json={"username": username, "password": password})
        assert r.status_code == 200, r.get_data(as_text=True)
        return client

    def test_owner_renames_household(self, app):
        owner = self._register(app, "renamechef")
        r = owner.post("/api/auth/household/rename", json={"name": "Nils Mega Sammlung"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["ok"] is True

        me = owner.get("/api/auth/me").get_json()
        active = next((h for h in me["households"] if h["household_id"] == me["active_household_id"]), None)
        assert active["name"] == "Nils Mega Sammlung"

    def test_dashboard_contains_household_name(self, app):
        owner = self._register(app, "dashchef")
        owner.post("/api/auth/household/rename", json={"name": "Unsere Familien-Karten"})
        r = owner.get("/api/dashboard")
        assert r.status_code == 200
        assert r.get_json()["household_name"] == "Unsere Familien-Karten"

    def test_rename_requires_owner(self, app):
        owner = self._register(app, "renchef")
        self._register(app, "renmate")
        owner.post("/api/auth/household/invite", json={"username": "renmate", "role": "editor"})

        mate = app.test_client()
        mate.post("/api/auth/login", json={"login": "renmate", "password": "testpass123"})
        me = mate.get("/api/auth/me").get_json()
        hh = next((h for h in me["households"] if h.get("role") != "owner"), None)
        mate.post("/api/auth/household/switch", json={"household_id": hh["household_id"]})
        r = mate.post("/api/auth/household/rename", json={"name": "Hackerversuch"})
        assert r.status_code == 403
        # Name unveraendert
        me2 = mate.get("/api/auth/me").get_json()
        active = next((h for h in me2["households"] if h["household_id"] == me2["active_household_id"]), None)
        assert active["name"] != "Hackerversuch"

    def test_rename_validates_name(self, app):
        owner = self._register(app, "valchef")
        r1 = owner.post("/api/auth/household/rename", json={"name": ""})
        assert r1.status_code == 400
        r2 = owner.post("/api/auth/household/rename", json={"name": "x" * 65})
        assert r2.status_code == 400
