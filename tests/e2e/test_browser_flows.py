"""Playwright E2E tests: full browser flows against a live test server.

Runs the real gevent server as a subprocess on a temp DB (port 8790),
then drives Chromium through the actual UI:
- login/register/reset pages
- dashboard, collection + card detail modal
- manual add modal (search, select, quantity, save)
- wishlist, set completion, settings
- tenant isolation in the browser (second user)

Markers: run with  pytest -m e2e  (excluded from the fast suite)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.db.schema import connect, init_database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = 8790
BASE = f"https://localhost:{PORT}"

pytestmark = pytest.mark.e2e


def _wait_for_port(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def e2e_server(tmp_path_factory):
    """Launch the real server (self-signed SSL) on a temp DB."""
    tmp = tmp_path_factory.mktemp("e2e")
    db_path = tmp / "e2e.db"

    # seed reference data
    conn = connect(db_path)
    init_database(conn)
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('HMW', 'Homeworlds')")
    conn.execute(
        "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity) "
        "VALUES ('SOR-005', 'SOR', '005', 'Luke Skywalker', 'Luke Skywalker', 'Unit', 'Legendary')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity) "
        "VALUES ('HMW-160', 'HMW', '160', 'Noxious Refinery', 'Giftige Raffinerie', 'Upgrade', 'Uncommon')"
    )
    conn.commit()
    conn.close()

    env = dict(os.environ)
    env["SWU_DB_PATH_OVERRIDE"] = str(db_path)
    env["SWU_SKIP_WARMUP"] = "1"  # E2E does not scan; skip the 12s CLIP warmup
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    log_file = tmp / "server.log"
    log_fh = open(log_file, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.web", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        # the app honours SWU_DB_PATH_OVERRIDE for tests (see schema.get_db_path)
        if not _wait_for_port(PORT, timeout=45):
            log_fh.flush()
            log_content = log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise AssertionError("E2E server did not start. Log tail: " + log_content)
        yield BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()


@pytest.fixture()
def page(e2e_server, browser):
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.set_default_timeout(15000)
    yield page
    context.close()


def _register(page, username, email, password):
    page.goto(f"{BASE}/register")
    page.fill("#username-input", username)
    page.fill("#email-input", email)
    page.fill("#password-input", password)
    page.click("#btn-register")
    page.wait_for_selector("#reg-success", state="visible")


def _login(page, login, password):
    page.goto(f"{BASE}/login")
    page.fill("#login-input", login)
    page.fill("#password-input", password)
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


def test_register_login_logout_flow(page):
    _register(page, "e2euser", "e2euser@example.com", "geheim123")
    # registered (first user in this DB -> admin, household created)
    page.goto(f"{BASE}/logout")
    # logout via API + redirect
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/")
    page.wait_for_url(f"{BASE}/login*")
    _login(page, "e2euser", "geheim123")
    # now on dashboard
    assert "Dashboard" in page.content()


def test_unauthenticated_redirect(page):
    page.goto(f"{BASE}/collection")
    page.wait_for_url(f"{BASE}/login*")
    assert "Anmelden" in page.content()


def test_dashboard_shows_account_menu(page):
    _register(page, "dashuser", "dash@example.com", "geheim123")
    page.goto(f"{BASE}/")
    page.wait_for_selector("#account-btn")
    assert "dashuser" in page.inner_text("#account-btn")
    # account dropdown opens
    page.click("#account-btn")
    page.wait_for_selector("#account-dropdown:not(.hidden)")
    assert "Abmelden" in page.inner_text("#account-dropdown")


def test_manual_add_card_flow(page):
    _register(page, "adduser", "add@example.com", "geheim123")
    page.goto(f"{BASE}/collection")
    page.wait_for_selector("#btn-add-manual")
    page.click("#btn-add-manual")
    page.wait_for_selector("#add-modal:not(.hidden)")
    page.fill("#add-search", "Giftige")
    page.click("#btn-add-search")
    page.wait_for_selector("#add-search-results .search-result-row")
    page.click("#add-search-results .search-result-row")
    page.wait_for_selector("#add-selected:not(.hidden)")
    # quantity 2
    page.click("#add-qty-plus")
    assert page.inner_text("#add-qty") == "2"
    page.click("#btn-add-save")
    # collection reloads with the card
    page.wait_for_selector(".collection-item")
    assert "Noxious Refinery" in page.content()


def test_card_detail_modal(page):
    _register(page, "detailuser", "detail@example.com", "geheim123")
    # add a card first via API (faster than UI)
    page.evaluate("""
        fetch('/api/collection/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_id: 'HMW-160', count: 1, language: 'de'})
        })
    """)
    page.goto(f"{BASE}/collection")
    page.wait_for_selector(".collection-item.clickable")
    page.click(".collection-item.clickable")
    page.wait_for_selector("#card-modal:not(.hidden)")
    page.wait_for_selector("#card-modal-body h2")
    assert "Giftige Raffinerie" in page.inner_text("#card-modal-body")
    # close via X
    page.click("#card-modal button", position={"x": 5, "y": 5})
    page.wait_for_selector("#card-modal.hidden", state="attached")


def test_settings_page_saves(page):
    _register(page, "setuser", "set@example.com", "geheim123")
    page.goto(f"{BASE}/settings")
    page.wait_for_selector("#scan_language")
    page.select_option("#scan_language", "en")
    page.click("#btn-save-settings")
    # verify persisted
    me = page.evaluate("fetch('/api/settings').then(r => r.json())")
    assert me["scan_language"] == "en"


def test_wishlist_page(page):
    _register(page, "wishuser", "wish@example.com", "geheim123")
    page.evaluate("""
        fetch('/api/wishlist/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_id: 'SOR-005', count: 1})
        })
    """)
    page.goto(f"{BASE}/wishlist")
    page.wait_for_selector(".collection-item")
    assert "Luke Skywalker" in page.content()


def test_household_sharing_flow(page):
    """Two users share one collection via invite code."""
    _register(page, "owner", "owner@example.com", "geheim123")
    # owner adds a card
    page.evaluate("""
        fetch('/api/collection/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_id: 'HMW-160', count: 3})
        })
    """)
    code = page.evaluate("""
        fetch('/api/auth/household/invite-code').then(r => r.json()).then(d => d.code)
    """)
    assert code

    # second user in a NEW context (fresh browser session)
    context = page.context.browser.new_context(ignore_https_errors=True)
    p2 = context.new_page()
    p2.goto(f"{BASE}/register")
    p2.fill("#username-input", "friend")
    p2.fill("#email-input", "friend@example.com")
    p2.fill("#password-input", "geheim123")
    p2.click("#btn-register")
    p2.wait_for_selector("#reg-success", state="visible")
    # join the owner's household via code
    result = p2.evaluate(f"""
        fetch('/api/auth/household/join', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{code: '{code}'}})
        }}).then(r => r.json())
    """)
    assert result["ok"]
    p2.evaluate("""
        fetch('/api/auth/household/switch', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({household_id: 1})
        })
    """)
    p2.goto(f"{BASE}/collection")
    p2.wait_for_selector(".collection-item")
    assert "Noxious Refinery" in p2.content(), "shared member must see owner's cards"
    context.close()


def test_isolation_in_browser(page):
    """Second user must NOT see the first user's cards."""
    _register(page, "private", "private@example.com", "geheim123")
    page.evaluate("""
        fetch('/api/collection/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_id: 'HMW-160', count: 1})
        })
    """)
    context = page.context.browser.new_context(ignore_https_errors=True)
    p2 = context.new_page()
    p2.goto(f"{BASE}/register")
    p2.fill("#username-input", "stranger")
    p2.fill("#email-input", "stranger@example.com")
    p2.fill("#password-input", "geheim123")
    p2.click("#btn-register")
    p2.wait_for_selector("#reg-success", state="visible")
    p2.goto(f"{BASE}/collection")
    # empty collection view (no items)
    assert p2.locator(".collection-item").count() == 0, "isolation violation!"
    context.close()
