"""MKM (Cardmarket) API v2.0 client — OAuth 1.0a "Dedicated App".

The user creates a Dedicated App in their MKM profile (professional sellers
only, manual approval by MKM) and gets:
  - App Token / App Secret  (identifies the app)
  - Access Token / Access Token Secret  (identifies the user, unlimited lifetime)

All requests are signed with HMAC-SHA1 per RFC 5849. Base URL since Jan 2026:
  https://apiv2.cardmarket.com/ws/v2.0/output.json/...

Docs: https://apiv2.cardmarket.com/ws/documentation/API:Auth_Overview
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("swu_manager.mkm_api")

API_BASE = "https://apiv2.cardmarket.com/ws/v2.0/output.json"

# Windows DPAPI credential store (user-bound, machine-local, no extra deps)
_CRED_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "mkm_credentials.bin"


def _dpapi_blob():
    import ctypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return DATA_BLOB, ctypes


def dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt bytes with Windows DPAPI (user-scoped)."""
    DATA_BLOB, ctypes = _dpapi_blob()
    src = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    dst = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src), "SWU-MKM", None, None, None, 0, ctypes.byref(dst)
    )
    if not ok:
        raise OSError("DPAPI CryptProtectData failed")
    return ctypes.string_at(dst.pbData, dst.cbData)


def dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt bytes previously encrypted with dpapi_encrypt."""
    DATA_BLOB, ctypes = _dpapi_blob()
    src = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    dst = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)
    )
    if not ok:
        raise OSError("DPAPI CryptUnprotectData failed")
    return ctypes.string_at(dst.pbData, dst.cbData)


def save_credentials(
    app_token: str,
    app_secret: str,
    access_token: str,
    access_secret: str,
) -> None:
    payload = json.dumps(
        {
            "app_token": app_token,
            "app_secret": app_secret,
            "access_token": access_token,
            "access_secret": access_secret,
        }
    ).encode("utf-8")
    _CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CRED_FILE.write_bytes(dpapi_encrypt(payload))
    log.info("MKM credentials saved (DPAPI-encrypted)", extra={"event": "mkm_creds_saved"})


def load_credentials() -> dict[str, str] | None:
    """Load DPAPI-encrypted MKM credentials. Returns None if not present."""
    if not _CRED_FILE.exists():
        return None
    try:
        payload = dpapi_decrypt(_CRED_FILE.read_bytes())
        return json.loads(payload.decode("utf-8"))
    except Exception as e:
        log.error(f"Could not decrypt MKM credentials: {e}", extra={"event": "mkm_creds_error"})
        return None


def delete_credentials() -> None:
    if _CRED_FILE.exists():
        _CRED_FILE.unlink()
        log.info("MKM credentials deleted", extra={"event": "mkm_creds_deleted"})


def credentials_exist() -> bool:
    return _CRED_FILE.exists()


# --- OAuth 1.0a signing -------------------------------------------------------


def _percent_encode(s: str) -> str:
    # RFC 3986 unreserved: - _ . ~ kept, everything else percent-encoded
    return urllib.parse.quote(str(s), safe="-_.~")


def _sign_request(
    method: str,
    url: str,
    params: dict[str, str],
    creds: dict[str, str],
) -> str:
    """Build the OAuth 1.0a Authorization header for a request."""
    oauth_params = {
        "oauth_consumer_key": creds["app_token"],
        "oauth_token": creds["access_token"],
        "oauth_nonce": secrets.token_hex(8),
        "oauth_timestamp": str(int(time.time())),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }

    # Signature base string: METHOD&base_url&normalized_params
    all_params = {**oauth_params, **params}
    sorted_items = sorted(
        (_percent_encode(k), _percent_encode(v)) for k, v in all_params.items()
    )
    param_str = "&".join(f"{k}={v}" for k, v in sorted_items)
    base_url = url.split("?")[0]
    base_string = "&".join(
        [
            method.upper(),
            _percent_encode(base_url),
            _percent_encode(param_str),
        ]
    )

    signing_key = "&".join(
        [_percent_encode(creds["app_secret"]), _percent_encode(creds["access_secret"])]
    )
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()

    oauth_params["oauth_signature"] = signature
    auth_header = "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"' for k, v in oauth_params.items()
    )
    return auth_header


def mkm_request(
    method: str,
    path: str,
    creds: dict[str, str],
    body: str | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """Signed request against the MKM API.

    Returns (status_code, parsed_json_or_text).
    `path` is relative to API_BASE without leading slash.
    """
    url = f"{API_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    auth = _sign_request(method, url, params or {}, creds)
    req = urllib.request.Request(url, method=method.upper())
    req.add_header("Authorization", auth)
    if body is not None:
        req.data = body.encode("utf-8")
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(data)
            except json.JSONDecodeError:
                return resp.status, data
    except urllib.error.HTTPError as e:
        data = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(data)
        except json.JSONDecodeError:
            return e.code, data


# --- High-level operations ------------------------------------------------------


def test_account(creds: dict[str, str]) -> dict[str, Any]:
    """GET /account — verifies the credentials and returns account info."""
    status, data = mkm_request("GET", "account", creds)
    if status == 200 and isinstance(data, dict):
        account = data.get("account", {})
        return {
            "ok": True,
            "username": account.get("username"),
            "user_type": account.get("userType") or account.get("type"),
            "country": (account.get("address") or {}).get("country") if isinstance(account.get("address"), dict) else None,
        }
    return {"ok": False, "status": status, "error": data if isinstance(data, str) else json.dumps(data)[:300]}


def get_wantslists(creds: dict[str, str]) -> dict[str, Any]:
    """GET /wantslist — all wantslists of the user."""
    status, data = mkm_request("GET", "wantslist", creds)
    return {"status": status, "data": data}


def get_games(creds: dict[str, str]) -> list[dict[str, Any]]:
    """GET /games — all games MKM supports (to find the SWU game id)."""
    status, data = mkm_request("GET", "games", creds)
    if status == 200 and isinstance(data, dict):
        return data.get("game", []) or data.get("games", [])
    return []


def find_swu_game_id(creds: dict[str, str]) -> int | None:
    """Find the game id for 'Star Wars Unlimited' via /games."""
    for g in get_games(creds):
        name = (g.get("name") or "").lower()
        if "star wars" in name and "unlimited" in name:
            return int(g.get("idGame"))
    return None


def create_wantslist(creds: dict[str, str], name: str, game_id: int | None = None) -> dict[str, Any]:
    """POST /wantslist — create a wantslist.

    game_id: MKM's internal game id for Star Wars: Unlimited (looked up via
    /games when not provided).
    """
    if game_id is None:
        game_id = find_swu_game_id(creds)
        if game_id is None:
            return {"status": 404, "data": {"error": "Star Wars: Unlimited nicht in MKM games gefunden"}}
    body = json.dumps({"wantslist": {"name": name, "idGame": game_id}})
    status, data = mkm_request("POST", "wantslist", creds, body=body)
    return {"status": status, "data": data}


def add_item_to_wantslist(
    creds: dict[str, str],
    wantslist_id: int,
    product_id: int,
    count: int = 1,
    min_condition: str = "NM",
    wish_price: float | None = None,
    mail_alert: bool = False,
) -> dict[str, Any]:
    """PUT /wantslist/:id — add a product item to a wantslist.

    Uses the XML-style action format via JSON body as documented by MKM.
    """
    item: dict[str, Any] = {
        "idProduct": product_id,
        "count": count,
        "minCondition": min_condition,
        "mailAlert": mail_alert,
    }
    if wish_price is not None:
        item["wishPrice"] = wish_price
    body = json.dumps({"action": "addItem", "product": item})
    status, data = mkm_request("PUT", f"wantslist/{wantslist_id}", creds, body=body)
    return {"status": status, "data": data}


def get_stock(creds: dict[str, str], start: int = 0) -> dict[str, Any]:
    """GET /stock?start=N — the user's stock (100 articles per page)."""
    status, data = mkm_request("GET", "stock", creds, params={"start": str(start)})
    return {"status": status, "data": data}


def add_article_to_stock(
    creds: dict[str, str],
    product_id: int,
    count: int = 1,
    price: float = 0.10,
    condition: str = "NM",
    language_id: int = 1,
    is_foil: bool = False,
    comments: str = "",
) -> dict[str, Any]:
    """POST /stock — list an article for sale.

    language_id: 1=English, 3=German.
    condition: MT/NM/EX/GD/LP/PL/PO.
    """
    article = {
        "idProduct": product_id,
        "count": count,
        "price": price,
        "condition": condition,
        "idLanguage": language_id,
        "comments": comments,
    }
    if is_foil:
        article["isFoil"] = True
    body = json.dumps({"article": article})
    status, data = mkm_request("POST", "stock", creds, body=body)
    return {"status": status, "data": data}


def delete_article_from_stock(creds: dict[str, str], article_id: int, count: int = 1) -> dict[str, Any]:
    """DELETE /stock — remove articles from stock."""
    body = json.dumps({"article": {"idArticle": article_id, "count": count}})
    status, data = mkm_request("DELETE", "stock", creds, body=body)
    return {"status": status, "data": data}
