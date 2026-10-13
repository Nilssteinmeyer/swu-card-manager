"""API-Tests: Nachrichten-Endpunkte (Rechte, Validierung, Rate-Limit)."""
import pytest

from app.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "msgapi.db")
    from app.db.schema import connect, init_database

    conn = connect(tmp_path / "msgapi.db")
    init_database(conn)
    conn.close()
    from app.core.config import AppConfig

    AppConfig.reset()
    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    client = app.test_client()
    # Admin + 2 User registrieren (erster User = Admin)
    r = client.post("/api/auth/register", json={
        "username": "admin1", "email": "a@x.de", "password": "pw123456"})
    assert r.status_code == 200
    for name in ["user2", "user3"]:
        r = client.post("/api/auth/register", json={
            "username": name, "email": f"{name}@x.de", "password": "pw123456"})
        assert r.status_code == 200
    return client


def _login(client, username):
    r = client.post("/api/auth/login", json={"login": username, "password": "pw123456"})
    assert r.status_code == 200
    return r


class TestMessagesAPI:
    def test_send_roundtrip(self, client):
        _login(client, "user2")
        r = client.post("/api/messages", json={"receiver_id": 3, "body": "Hallo user3!"})
        assert r.status_code == 200, r.get_json()
        mid = r.get_json()["message_id"]
        # user3 sieht die Nachricht
        client.post("/api/auth/logout")
        _login(client, "user3")
        r2 = client.get("/api/messages")
        data = r2.get_json()
        assert data["unread"] == 1
        assert any(m["body"] == "Hallo user3!" for m in data["messages"])
        # Thread oeffnen markiert gelesen
        r3 = client.get("/api/messages/thread/2")
        assert r3.get_json()["messages"][-1]["body"] == "Hallo user3!"
        r4 = client.get("/api/messages/unread_count")
        assert r4.get_json()["unread"] == 0

    def test_send_validation(self, client):
        _login(client, "user2")
        r = client.post("/api/messages", json={"receiver_id": 3, "body": "   "})
        assert r.status_code == 400
        r2 = client.post("/api/messages", json={"body": "kein Empfaenger"})
        assert r2.status_code == 400
        r3 = client.post("/api/messages", json={"receiver_id": 999, "body": "x"})
        assert r3.status_code == 400

    def test_fremde_nicht_lesbar_als_gelesen(self, client):
        _login(client, "user2")
        client.post("/api/messages", json={"receiver_id": 3, "body": "privat"})
        # user2 kann die eigene gesendete NICHT als gelesen markieren
        r = client.post("/api/messages/read", json={"partner_id": 3})
        assert r.get_json()["marked"] == 0
        # user3 sieht weiterhin unread
        client.post("/api/auth/logout")
        _login(client, "user3")
        assert client.get("/api/messages/unread_count").get_json()["unread"] == 1

    def test_soft_delete_isoliert_api(self, client):
        _login(client, "user2")
        mid = client.post("/api/messages", json={"receiver_id": 3, "body": "del"}).get_json()["message_id"]
        client.post("/api/auth/logout")
        _login(client, "user3")
        r = client.delete(f"/api/messages/{mid}")
        assert r.get_json()["deleted"] is True
        # user2 (Sender) sieht die Nachricht noch im Thread
        client.post("/api/auth/logout")
        _login(client, "user2")
        t = client.get("/api/messages/thread/3").get_json()["messages"]
        assert any(m["message_id"] == mid for m in t)

    def test_archiv_roundtrip(self, client):
        _login(client, "user2")
        client.post("/api/messages", json={"receiver_id": 3, "body": "archiv-mich"})
        client.post("/api/auth/logout")
        _login(client, "user3")
        inbox = client.get("/api/messages").get_json()["messages"]
        mid = inbox[0]["message_id"]
        client.post("/api/messages/archive", json={"message_id": mid, "archived": True})
        assert client.get("/api/messages?filter=alle").get_json()["messages"] == []
        assert len(client.get("/api/messages?filter=archiv").get_json()["messages"]) == 1
        client.post("/api/messages/archive", json={"message_id": mid, "archived": False})
        assert len(client.get("/api/messages?filter=alle").get_json()["messages"]) == 1


class TestBroadcastsAPI:
    def test_admin_broadcast_empfaengerisolierung(self, client):
        _login(client, "admin1")
        r = client.post("/api/broadcasts", json={
            "typ": "update", "betreff": "v1.2", "body": "Scanner 30% schneller"})
        assert r.status_code == 200
        bid = r.get_json()["broadcast_id"]
        assert r.get_json()["empfaenger"] == 3
        # Stats (nur Admin)
        s = client.get(f"/api/broadcasts/{bid}/stats").get_json()
        assert s["empfaenger"] == 3 and s["bestaetigt"] == 0
        # user2 ackt -> nur seine Zeile
        client.post("/api/auth/logout")
        _login(client, "user2")
        ack = client.post(f"/api/broadcasts/{bid}/ack")
        assert ack.get_json()["acknowledged"] is True
        assert client.get("/api/messages/unread_count").get_json()["unread"] == 0
        # user3 hat weiter unread
        client.post("/api/auth/logout")
        _login(client, "user3")
        assert client.get("/api/messages/unread_count").get_json()["unread"] == 1
        # user3 kann keine Stats abrufen
        assert client.get(f"/api/broadcasts/{bid}/stats").status_code == 403

    def test_nicht_admin_kann_keinen_broadcast(self, client):
        _login(client, "user2")
        r = client.post("/api/broadcasts", json={"typ": "update", "betreff": "x", "body": "y"})
        assert r.status_code == 403
        assert "Admin" in r.get_json()["error"]

    def test_broadcast_validierung(self, client):
        _login(client, "admin1")
        r = client.post("/api/broadcasts", json={"typ": "spam", "betreff": "x", "body": "y"})
        assert r.status_code == 400

    def test_unauth_401(self, client):
        client.post("/api/auth/logout")
        assert client.get("/api/messages").status_code == 401
        assert client.get("/api/messages/unread_count").status_code == 401
        assert client.post("/api/broadcasts", json={}).status_code == 401
