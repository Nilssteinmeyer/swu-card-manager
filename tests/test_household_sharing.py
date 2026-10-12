"""Tests: Sammlungs-Sharing (Beitreten per Code UI, Einladen per Name, Rollen)."""
import pytest


class TestHouseholdSharing:
    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "sharing.db")
        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "sharing.db")
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
        return client  # gleicher Client bleibt eingeloggt

    # ------------------------------------------------------------------
    # 1) Beitreten per Code (Endpoint existierte, UI fehlte)
    # ------------------------------------------------------------------
    def test_join_by_code(self, app):
        owner = self._register(app, "chef")
        code_resp = owner.get("/api/auth/household/invite-code")
        code = code_resp.get_json()["code"]

        guest = self._register(app, "gast")
        r = guest.post("/api/auth/household/join", json={"code": code})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["ok"] is True

        me = guest.get("/api/auth/me").get_json()
        ids = [h["household_id"] for h in me["households"]]
        assert ids, "Gast ist nicht in der Sammlung gelistet"

    # ------------------------------------------------------------------
    # 2) Einladen per USERNAME (neu): Owner laedt 'gast2' direkt
    # ------------------------------------------------------------------
    def test_invite_by_username(self, app):
        owner = self._register(app, "chef2")
        self._register(app, "gast2")  # zweiter User existiert

        # Owner laedt gast2 per Name mit Rolle 'viewer'
        r = owner.post("/api/auth/household/invite", json={"username": "gast2", "role": "viewer"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["ok"] is True

        # gast2 ist jetzt Mitglied
        guest = app.test_client()
        guest.post("/api/auth/login", json={"login": "gast2", "password": "testpass123"})
        me = guest.get("/api/auth/me").get_json()
        assert len(me["households"]) >= 1

    def test_invite_unknown_username_fails(self, app):
        owner = self._register(app, "chef3")
        r = owner.post("/api/auth/household/invite", json={"username": "niemandxyz", "role": "viewer"})
        assert r.status_code == 404
        assert "nicht gefunden" in r.get_json()["error"]

    def test_only_owner_can_invite(self, app):
        owner = self._register(app, "chef4")
        self._register(app, "editoruser")
        owner.post("/api/auth/household/invite", json={"username": "editoruser", "role": "editor"})

        editor = app.test_client()
        editor.post("/api/auth/login", json={"login": "editoruser", "password": "testpass123"})
        # In den Owner-Haushalt wechseln: der, in dem der Editor NICHT owner ist
        me = editor.get("/api/auth/me").get_json()
        owner_hh = next(h for h in me["households"] if h.get("role") != "owner")
        editor.post("/api/auth/household/switch", json={"household_id": owner_hh["household_id"]})
        self._register(app, "victimuser")
        # Editor versucht einzuladen -> verboten
        r = editor.post("/api/auth/household/invite", json={"username": "victimuser", "role": "viewer"})
        assert r.status_code == 403

    def test_invite_invalid_role_rejected(self, app):
        owner = self._register(app, "chef5")
        self._register(app, "gast5")
        r = owner.post("/api/auth/household/invite", json={"username": "gast5", "role": "superadmin"})
        assert r.status_code == 400

    # ------------------------------------------------------------------
    # 3) Mitgliederliste + Rollen aendern + entfernen
    # ------------------------------------------------------------------
    def test_members_list_and_role_change(self, app):
        owner = self._register(app, "chef6")
        self._register(app, "mmm6")
        owner.post("/api/auth/household/invite", json={"username": "mmm6", "role": "viewer"})

        members = owner.get("/api/auth/household/members").get_json()["members"]
        names = [m["username"] for m in members]
        assert "mmm6" in names
        m6 = next(m for m in members if m["username"] == "mmm6")
        assert m6["role"] == "viewer"

        # Rolle aendern: viewer -> editor
        r = owner.post("/api/auth/household/members/role", json={"username": "mmm6", "role": "editor"})
        assert r.status_code == 200
        members2 = owner.get("/api/auth/household/members").get_json()["members"]
        m6b = next(m for m in members2 if m["username"] == "mmm6")
        assert m6b["role"] == "editor"

        # Nur Owner darf Rollen aendern
        victim_c = app.test_client()
        victim_c.post("/api/auth/login", json={"login": "mmm6", "password": "testpass123"})
        r2 = victim_c.post("/api/auth/household/members/role", json={"username": "chef6", "role": "viewer"})
        assert r2.status_code == 403

    def test_remove_member(self, app):
        owner = self._register(app, "chef7")
        self._register(app, "mmm7")
        owner.post("/api/auth/household/invite", json={"username": "mmm7", "role": "editor"})
        r = owner.post("/api/auth/household/members/remove", json={"username": "mmm7"})
        assert r.status_code == 200
        members = owner.get("/api/auth/household/members").get_json()["members"]
        assert "mmm7" not in [m["username"] for m in members]

    # ------------------------------------------------------------------
    # 4) Rollen wirken: viewer darf NICHT in die Sammlung schreiben
    # ------------------------------------------------------------------
    def test_viewer_cannot_add_cards(self, app):
        owner = self._register(app, "chef8")
        self._register(app, "watcher")
        owner.post("/api/auth/household/invite", json={"username": "watcher", "role": "viewer"})

        watcher = app.test_client()
        watcher.post("/api/auth/login", json={"login": "watcher", "password": "testpass123"})
        # In den Owner-Haushalt wechseln (watcher hat keinen eigenen? -> Owners household)
        me = watcher.get("/api/auth/me").get_json()
        hh_ids = [h["household_id"] for h in me["households"]]
        assert hh_ids, "watcher sollte Mitglied sein"
        watcher.post("/api/auth/household/switch", json={"household_id": hh_ids[0]})
        r = watcher.post("/api/collection/add", json={"card_id": "SOR-001", "count": 1})
        assert r.status_code == 403, "viewer darf keine Karten hinzufuegen"
