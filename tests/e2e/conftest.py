"""Shared E2E fixtures: live test server + browser page.

Both are provided here so every e2e test module gets them without importing
from test_browser_flows (module-scoped fixtures are not importable across
modules for pytest discovery).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.db.schema import connect, init_database

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    """Launch the real server (self-signed SSL) on a temp DB."""
    tmp = tmp_path_factory.mktemp("e2e")
    db_path = tmp / "e2e.db"

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
    env["SWU_SKIP_WARMUP"] = "1"
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
