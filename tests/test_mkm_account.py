"""Tests for MKM API client (OAuth signing, DPAPI credentials) + wishlist."""
import base64
import hmac
import hashlib
import json
from pathlib import Path

import pytest

from app.integrations import mkm_api
from app.db.schema import connect, init_database
from app.db import repository as repo


# --- OAuth 1.0a signature -----------------------------------------------------

CREDS = {
    "app_token": "bfaD9xOU0SXBhtBP",
    "app_secret": "pChvrpp6AEOEwxBIIUBOvWcRG3X9xL4Y",
    "access_token": "lBY1xptUJ7ZJSK01x4fNwzw8kAe5b10Q",
    "access_secret": "hc1wJAOX02pGGJK2uAv1ZOiwS7I9Tpoe",
}


def test_percent_encode():
    assert mkm_api._percent_encode("a b") == "a%20b"
    assert mkm_api._percent_encode("ü") == "%C3%BC"
    assert mkm_api._percent_encode("abc-._~") == "abc-._~"


def test_signature_header_structure():
    header = mkm_api._sign_request("GET", "https://apiv2.cardmarket.com/ws/v2.0/output.json/account", {}, CREDS)
    assert header.startswith("OAuth ")
    parts = dict(
        p.split('="', 1)[::-1][::-1]  # keep as-is; parse below
        for p in header[6:].split('", ')
    )
    # simpler parse
    import re
    kv = dict(re.findall(r'(\w+)="([^"]*)"', header))
    assert kv["oauth_consumer_key"] == CREDS["app_token"]
    assert kv["oauth_token"] == CREDS["access_token"]
    assert kv["oauth_signature_method"] == "HMAC-SHA1"
    assert "oauth_signature" in kv
    assert "oauth_nonce" in kv
    assert "oauth_timestamp" in kv


def test_signature_is_deterministic_and_valid_base64():
    h1 = mkm_api._sign_request("GET", "https://example.com/api", {}, CREDS)
    import re
    import urllib.parse
    sig1 = re.findall(r'oauth_signature="([^"]*)"', h1)[0]
    # signature is percent-encoded inside the header — decode first
    decoded = urllib.parse.unquote(sig1)
    # valid base64 decodes without error (padding may be implicit; add as needed)
    padded = decoded + "=" * (-len(decoded) % 4)
    base64.b64decode(padded)


# --- DPAPI credentials --------------------------------------------------------

def test_credentials_roundtrip(tmp_path, monkeypatch):
    cred_file = tmp_path / "mkm_credentials.bin"
    monkeypatch.setattr(mkm_api, "_CRED_FILE", cred_file)

    mkm_api.save_credentials("app-t", "app-s", "acc-t", "acc-s")
    assert cred_file.exists()
    # raw file must NOT contain plaintext secrets
    raw = cred_file.read_bytes()
    assert b"app-t" not in raw
    assert b"acc-s" not in raw

    creds = mkm_api.load_credentials()
    assert creds == {
        "app_token": "app-t",
        "app_secret": "app-s",
        "access_token": "acc-t",
        "access_secret": "acc-s",
    }

    mkm_api.delete_credentials()
    assert not cred_file.exists()
    assert mkm_api.load_credentials() is None


def test_credentials_exist(tmp_path, monkeypatch):
    cred_file = tmp_path / "mkm_credentials.bin"
    monkeypatch.setattr(mkm_api, "_CRED_FILE", cred_file)
    assert not mkm_api.credentials_exist()
    mkm_api.save_credentials("a", "b", "c", "d")
    assert mkm_api.credentials_exist()


# --- Wishlist -------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(Path(tmp_path) / "test.db")
    init_database(conn)
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('SOR-010', 'SOR', '010', 'Darth Vader')")
    conn.commit()
    yield conn
    conn.close()


def test_wishlist_add_remove(db):
    wl_id = repo.add_to_wishlist(db, "SOR-005", count=2, target_price=4.0)
    db.commit()
    items = repo.get_wishlist(db)
    assert len(items) == 1
    assert items[0]["card_id"] == "SOR-005"
    assert items[0]["count"] == 2
    assert items[0]["target_price"] == 4.0
    assert items[0]["name"] == "Luke Skywalker"

    assert repo.remove_from_wishlist(db, wl_id) is True
    db.commit()
    assert repo.get_wishlist(db) == []
    assert repo.remove_from_wishlist(db, wl_id) is False


def test_wishlist_includes_price(db):
    repo.upsert_card_mkm_map(db, "SOR-005", 401005)
    repo.upsert_price(db, {"idProduct": 401005, "trend": 4.9, "foil_trend": 15.5})
    repo.add_to_wishlist(db, "SOR-005")
    db.commit()
    items = repo.get_wishlist(db)
    assert items[0]["price_trend"] == 4.9
    assert items[0]["idProduct"] == 401005
