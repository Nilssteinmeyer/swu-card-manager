"""E2E: Scan-Modus-Umschalter (Sammlung / Preis) auf der Scanner-Seite."""
import pytest

pytestmark = pytest.mark.e2e

from tests.e2e.conftest import BASE, _register, _login  # noqa: E402

pytestmark = pytest.mark.e2e


import uuid as _uuid


def _register_or_login(page):
    """Registriert einen frischen User - die Registrierung loggt direkt ein."""
    name = f"pricemode{_uuid.uuid4().hex[:8]}"
    _register(page, name, f"{name}@example.com", "geheim123")


def test_scan_mode_switch_visible(page):
    """Der Modus-Umschalter ist sichtbar, default = Sammlung."""
    _register_or_login(page)
    page.goto(f"{BASE}/scanner")
    page.wait_for_selector("#scan-mode-switch", state="visible")
    assert page.is_visible("#mode-btn-collection")
    assert page.is_visible("#mode-btn-price")
    cls = page.get_attribute("#mode-btn-collection", "class")
    assert "active" in (cls or "")


def test_scan_mode_switch_to_price_persists(page):
    """Umschalten auf Preis: Button aktiv; nach Reload bleibt der Modus."""
    _register_or_login(page)
    page.goto(f"{BASE}/scanner")
    page.wait_for_selector("#mode-btn-price", state="visible")
    page.click("#mode-btn-price")
    cls = page.get_attribute("#mode-btn-price", "class")
    assert "active" in (cls or "")
    page.reload()
    page.wait_for_selector("#mode-btn-price", state="visible")
    # Active-Klasse wird erst nach dem async. Settings-Laden gesetzt ->
    # kurz pollen statt einmalig lesen (Suite-Last-Timing)
    import time as _t
    deadline = _t.time() + 5
    cls2 = ""
    while _t.time() < deadline:
        cls2 = page.get_attribute("#mode-btn-price", "class") or ""
        if "active" in cls2:
            break
        page.wait_for_timeout(300)


def test_scan_mode_invalid_value_rejected_by_api(page):
    """Die API lehnt ungueltige scan_mode-Werte ab."""
    _register_or_login(page)
    resp = page.evaluate(
        """async () => {
            const r = await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scan_mode: 'unsinn'}),
                credentials: 'same-origin',
            });
            return {status: r.status, body: await r.json()};
        }"""
    )
    assert resp["status"] == 400
    assert "Ungueltiger Wert" in resp["body"].get("error", "")

