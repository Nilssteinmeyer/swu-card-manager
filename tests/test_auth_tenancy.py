"""Auth & multi-tenancy tests: registration, login, isolation, roles, reset."""
import json
from pathlib import Path

import pytest

from app.db.schema import connect

from tests.auth_helpers import make_app, register_and_login, register_second_user


# ---------------------------------------------------------------------------
# Registration & login
# ---------------------------------------------------------------------------


def test_register_first_user_is_admin(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": "admin", "email": "admin@example.com", "password": "geheim123",
    })
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["platform_admin"] is True

    conn = connect(db_path)
    cur = conn.execute("SELECT is_platform_admin FROM users WHERE username='admin'")
    assert cur.fetchone()["is_platform_admin"] == 1
    conn.close()


def test_register_second_user_not_admin(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")
    resp = client2.get("/api/auth/me")
    me = json.loads(resp.get_data(as_text=True))
    assert me["user"]["is_platform_admin"] is False


def test_register_validation(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    # short password
    resp = client.post("/api/auth/register", json={"username": "abc", "email": "a@b.de", "password": "short"})
    assert resp.status_code == 400
    # invalid email
    resp = client.post("/api/auth/register", json={"username": "abc", "email": "notanemail", "password": "geheim123"})
    assert resp.status_code == 400
    # invalid username
    resp = client.post("/api/auth/register", json={"username": "x!", "email": "a@b.de", "password": "geheim123"})
    assert resp.status_code == 400


def test_register_duplicate_rejected(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "user1")
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": "user1", "email": "other@example.com", "password": "geheim123",
    })
    assert resp.status_code == 409


def test_login_success_and_logout(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester")
    # fresh client must authenticate
    client = app.test_client()
    resp = client.get("/api/auth/me")
    me = json.loads(resp.get_data(as_text=True))
    assert me["user"] is None

    resp = client.post("/api/auth/login", json={"login": "tester", "password": "testpass123"})
    assert resp.status_code == 200
    resp = client.get("/api/auth/me")
    me = json.loads(resp.get_data(as_text=True))
    assert me["user"]["username"] == "tester"

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    resp = client.get("/api/auth/me")
    me = json.loads(resp.get_data(as_text=True))
    assert me["user"] is None


def test_login_with_email(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester", email="tester@example.com")
    client = app.test_client()
    resp = client.post("/api/auth/login", json={"login": "tester@example.com", "password": "testpass123"})
    assert resp.status_code == 200


def test_login_wrong_password(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester")
    client = app.test_client()
    resp = client.post("/api/auth/login", json={"login": "tester", "password": "wrongpass999"})
    assert resp.status_code == 401


def test_login_rate_limit(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester")
    client = app.test_client()
    for _ in range(5):
        client.post("/api/auth/login", json={"login": "tester", "password": "wrongpass999"})
    resp = client.post("/api/auth/login", json={"login": "tester", "password": "testpass123"})
    assert resp.status_code == 429


def test_password_hash_is_argon2(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester")
    conn = connect(db_path)
    cur = conn.execute("SELECT password_hash FROM users WHERE username='tester'")
    h = cur.fetchone()["password_hash"]
    conn.close()
    assert h.startswith("$argon2")


# ---------------------------------------------------------------------------
# Route protection
# ---------------------------------------------------------------------------


def test_api_requires_login(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401
    resp = client.get("/api/collection")
    assert resp.status_code == 401
    resp = client.post("/api/scan/undo", json={})
    assert resp.status_code == 401


def test_pages_redirect_to_login(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_public_pages_accessible(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    for path in ("/login", "/register", "/reset"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} should be public"


# ---------------------------------------------------------------------------
# Tenant isolation (THE critical suite)
# ---------------------------------------------------------------------------


def test_user2_cannot_see_user1_collection(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")

    # user1 adds a card via manual add
    resp = client1.post("/api/collection/add", json={"card_id": "SOR-005", "count": 3})
    assert resp.status_code == 200

    # user2 logs in, has their own household
    client2 = register_second_user(app, "user2")
    resp = client2.get("/api/collection?sort=name&dir=asc")
    data = json.loads(resp.get_data(as_text=True))
    ids = {i["card_id"] for i in data["items"]}
    assert "SOR-005" not in ids, "ISOLATION VIOLATION: user2 sees user1 data!"

    # dashboard must not count user1 cards
    resp = client2.get("/api/dashboard")
    dash = json.loads(resp.get_data(as_text=True))
    assert dash["collection_total"] == 0


def test_isolation_on_undo_preview(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    # user1 scan+confirm flows produce an undoable action (simulate via insert)
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO scan_undo (scan_id, collection_id, card_id, count_added, previous_count, entry_created, household_id) "
        "VALUES (999, 999, 'SOR-005', 1, 0, 1, 1)"
    )
    conn.commit()
    conn.close()

    # user2 must NOT see user1's undoable action
    resp = client2.get("/api/scan/undo/preview")
    data = json.loads(resp.get_data(as_text=True))
    assert data["available"] is False, "ISOLATION VIOLATION in undo preview!"

    # user1 sees it
    resp = client1.get("/api/scan/undo/preview")
    data = json.loads(resp.get_data(as_text=True))
    assert data["available"] is True


def test_isolation_on_wishlist(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    resp = client1.post("/api/wishlist/add", json={"card_id": "SOR-005", "count": 1})
    assert resp.status_code == 200

    resp = client2.get("/api/wishlist")
    data = json.loads(resp.get_data(as_text=True))
    assert data["count"] == 0, "ISOLATION VIOLATION: user2 sees user1 wishlist!"

    resp = client1.get("/api/wishlist")
    data = json.loads(resp.get_data(as_text=True))
    assert data["count"] == 1


def test_isolation_on_search_owned_flag(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    client1.post("/api/collection/add", json={"card_id": "SOR-005", "count": 1})

    resp = client2.get("/api/cards/search?q=Luke&limit=5")
    data = json.loads(resp.get_data(as_text=True))
    luke = next(i for i in data["items"] if i["card_id"] == "SOR-005")
    assert luke["owned"] is False, "ISOLATION VIOLATION: owned flag leaks!"

    resp = client1.get("/api/cards/search?q=Luke&limit=5")
    data = json.loads(resp.get_data(as_text=True))
    luke = next(i for i in data["items"] if i["card_id"] == "SOR-005")
    assert luke["owned"] is True


def test_isolation_on_completion(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    client1.post("/api/collection/add", json={"card_id": "SOR-005", "count": 1})

    resp = client2.get("/api/sets/SOR/completion")
    data = json.loads(resp.get_data(as_text=True))
    assert data["owned_unique"] == 0, "ISOLATION VIOLATION in set completion!"

    resp = client1.get("/api/sets/SOR/completion")
    data = json.loads(resp.get_data(as_text=True))
    assert data["owned_unique"] >= 1


def test_isolation_on_export(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    client1.post("/api/collection/add", json={"card_id": "SOR-005", "count": 2})

    resp = client2.get("/api/collection/export?format=json")
    data = json.loads(resp.get_data(as_text=True))
    assert data["items"] == [], "ISOLATION VIOLATION in export!"


# ---------------------------------------------------------------------------
# Household sharing
# ---------------------------------------------------------------------------


def test_household_invite_and_join(tmp_path, monkeypatch):
    app, db_path = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    # owner gets the invite code
    resp = client1.get("/api/auth/household/invite-code")
    data = json.loads(resp.get_data(as_text=True))
    assert resp.status_code == 200
    code = data["code"]

    # user2 joins user1's household
    resp = client2.post("/api/auth/household/join", json={"code": code})
    assert resp.status_code == 200

    # user2 switches to household 1 and now sees the shared collection
    resp = client2.post("/api/auth/household/switch", json={"household_id": 1})
    assert resp.status_code == 200

    resp = client2.get("/api/auth/me")
    me = json.loads(resp.get_data(as_text=True))
    assert me["active_household_id"] == 1


def test_shared_household_sees_same_collection(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")

    client1.post("/api/collection/add", json={"card_id": "SOR-005", "count": 3})

    # join + switch
    code = json.loads(client1.get("/api/auth/household/invite-code").get_data(as_text=True))["code"]
    client2.post("/api/auth/household/join", json={"code": code})
    client2.post("/api/auth/household/switch", json={"household_id": 1})

    resp = client2.get("/api/collection?sort=name&dir=asc")
    data = json.loads(resp.get_data(as_text=True))
    ids = {i["card_id"] for i in data["items"]}
    assert "SOR-005" in ids, "Shared household member should see the collection!"


def test_invite_code_only_for_owner(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client1 = register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")
    # user2 joins household 1 as editor, then switches to it
    code = json.loads(client1.get("/api/auth/household/invite-code").get_data(as_text=True))["code"]
    client2.post("/api/auth/household/join", json={"code": code})
    client2.post("/api/auth/household/switch", json={"household_id": 1})
    # as non-owner in household 1 the invite code must be denied
    resp = client2.get("/api/auth/household/invite-code")
    assert resp.status_code == 403


def test_join_invalid_code(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")
    resp = client2.post("/api/auth/household/join", json={"code": "doesnotexist"})
    assert resp.status_code == 400


def test_switch_to_foreign_household_forbidden(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "user1")
    client2 = register_second_user(app, "user2")
    # user2's own household is 2; trying to activate 1 without membership
    resp = client2.post("/api/auth/household/switch", json={"household_id": 1})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def test_password_reset_flow(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester", email="tester@example.com")

    # request reset (local mode returns the token)
    client = app.test_client()
    resp = client.post("/api/auth/reset", json={"email": "tester@example.com"})
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    token = data.get("reset_token_local")
    assert token, "local mode should return the token"

    # set new password
    resp = client.post("/api/auth/reset/confirm", json={"token": token, "password": "neuespass456"})
    assert resp.status_code == 200

    # old sessions dead, old password rejected, new works
    resp = client.post("/api/auth/login", json={"login": "tester", "password": "testpass123"})
    assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"login": "tester", "password": "neuespass456"})
    assert resp.status_code == 200


def test_reset_token_single_use(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    register_and_login(app, "tester", email="tester@example.com")
    client = app.test_client()
    token = json.loads(client.post("/api/auth/reset", json={"email": "tester@example.com"}).get_data(as_text=True))["reset_token_local"]
    # use it once
    client.post("/api/auth/reset/confirm", json={"token": token, "password": "erstes1234"})
    # second use must fail
    resp = client.post("/api/auth/reset/confirm", json={"token": token, "password": "zweites1234"})
    assert resp.status_code == 400


def test_reset_unknown_email_is_generic(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.post("/api/auth/reset", json={"email": "unknown@example.com"})
    # must NOT reveal that the account does not exist
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert "reset_token_local" not in data


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


def test_email_verification_flow(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.post("/api/auth/register", json={
        "username": "tester", "email": "tester@example.com", "password": "geheim123",
    })
    data = json.loads(resp.get_data(as_text=True))
    token = data["verify_token_local"]
    assert token

    resp = client.get(f"/verify-email?token={token}")
    assert resp.status_code == 200

    client.post("/api/auth/login", json={"login": "tester", "password": "geheim123"})
    me = json.loads(client.get("/api/auth/me").get_data(as_text=True))
    assert me["user"]["email_verified"] is True


def test_email_verification_bad_token(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.get("/verify-email?token=invalidtoken123")
    assert resp.status_code == 200
    assert b"ung\xc3\xbcltig" in resp.get_data() or "ungültig" in resp.get_data(as_text=True)
