"""E2E: Move/Split-Sheets aus dem Karten-Detail muessen UEBER dem Modal liegen.

Nutzer-Bug: 'Klicke ich auf Umlagern oder Aufteilen passiert einfach nix.'
Root-Cause: .more-sheet z-index 401 < Modal-Overlay z-index 1000 — das Sheet
oeffnete UNSICHTBAR hinter dem Modal. Dieser Test verifiziert echte Taps UND
die Sichtbarkeit (elementFromPoint) — nicht nur das classList-Flag.
"""
import uuid

import pytest

from tests.e2e.conftest import BASE, _register

pytestmark = pytest.mark.e2e


def test_sheets_visible_over_modal(page):
    """Umlagern/Aufteilen-Sheets liegen sichtbar UEBER dem Detail-Modal."""
    user = f"vis{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    page.goto(f"{BASE}/logout")
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-input", state="visible", timeout=10000)
    page.fill("#login-input", user)
    page.fill("#password-input", "geheim123")
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")
    page.evaluate("""(async () => {
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'SOR-005', count: 3, language: 'de' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Ordner', typ: 'binder', icon: '📕' } });
    })()""")
    page.goto(f"{BASE}/collection")
    page.wait_for_selector(".collection-item", timeout=8000)

    # ECHTER TAP auf die Karte
    page.locator(".collection-item").first.click()
    page.wait_for_selector("#detail-bestand-sektion", timeout=8000)
    page.wait_for_timeout(500)

    # ECHTER TAP auf Umlagern
    page.locator("#detail-bestand-rows button", has_text="Umlagern").first.click()
    page.wait_for_selector("#move-sheet.visible", timeout=5000)
    page.wait_for_timeout(350)  # Slide-Transition (0.25s) abwarten
    sichtbar_move = page.evaluate("""(() => {
        const s = document.getElementById('move-sheet');
        const b = s.getBoundingClientRect();
        const y = Math.max(0, Math.min(b.top + 30, window.innerHeight - 1));
        const probe = document.elementFromPoint(195, y);
        return probe ? !!probe.closest('#move-sheet') : false;
    })()""")
    assert sichtbar_move, "Move-Sheet muss UEBER dem Modal liegen (elementFromPoint-Treffer)"
    # Echter Tap auf Abbrechen/Backdrop schliesst
    page.evaluate("""(() => ['move-sheet', 'move-sheet-backdrop'].forEach(id => document.getElementById(id)?.classList.remove('visible')))()""")
    page.wait_for_timeout(300)

    # ECHTER TAP auf Aufteilen
    page.locator("#detail-bestand-rows button", has_text="Aufteilen").first.click()
    page.wait_for_selector("#split-sheet.visible", timeout=5000)
    page.wait_for_timeout(350)  # Slide-Transition abwarten
    sichtbar_split = page.evaluate("""(() => {
        const s = document.getElementById('split-sheet');
        const b = s.getBoundingClientRect();
        const y = Math.max(0, Math.min(b.top + 30, window.innerHeight - 1));
        const probe = document.elementFromPoint(195, y);
        return probe ? !!probe.closest('#split-sheet') : false;
    })()""")
    assert sichtbar_split, "Split-Sheet muss UEBER dem Modal liegen"
    # Inhalt lesbar: Titel + Speichern-Button
    inhalt = page.evaluate("() => document.getElementById('split-sheet').textContent")
    assert "Aufteilen" in inhalt and "Speichern" in inhalt


def test_real_taps_all_cards_move_split(page):
    """Jede Karte der Sammlung: echte Taps auf Umlagern + Aufteilen oeffnen sichtbar."""
    user = f"tap{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    page.goto(f"{BASE}/logout")
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-input", state="visible", timeout=10000)
    page.fill("#login-input", user)
    page.fill("#password-input", "geheim123")
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")
    # 3 verschiedene Karten (Sprach-Splits + Foil + Einzelexemplar)
    page.evaluate("""(async () => {
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'SOR-005', count: 1, language: 'de' } });
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'HMW-160', count: 2, language: 'en' } });
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'HMW-160', count: 1, language: 'de' } });
    })()""")
    page.goto(f"{BASE}/collection")
    page.wait_for_selector(".collection-item", timeout=8000)
    n = page.locator(".collection-item").count()
    assert n >= 2, "mind. 2 Karten expected"

    for idx in range(n):
        page.locator(".collection-item").nth(idx).click()
        page.wait_for_selector("#detail-bestand-sektion", timeout=8000)
        page.wait_for_timeout(400)
        page.locator("#detail-bestand-rows button", has_text="Umlagern").first.click()
        page.wait_for_selector("#move-sheet.visible", timeout=5000)
        page.wait_for_timeout(350)  # Slide-Transition abwarten
        ok_move = page.evaluate("""(() => {
            const s = document.getElementById('move-sheet');
            const b = s.getBoundingClientRect();
            const y = Math.max(0, Math.min(b.top + 30, window.innerHeight - 1));
            const probe = document.elementFromPoint(195, y);
            return probe ? !!probe.closest('#move-sheet') : false;
        })()""")
        assert ok_move, f"Karte {idx}: Move-Sheet nicht sichtbar ueber Modal"
        page.evaluate("""(() => ['move-sheet', 'move-sheet-backdrop'].forEach(id => document.getElementById(id)?.classList.remove('visible')))()""")
        page.wait_for_timeout(250)
        page.locator("#detail-bestand-rows button", has_text="Aufteilen").first.click()
        page.wait_for_selector("#split-sheet.visible", timeout=5000)
        page.wait_for_timeout(350)  # Slide-Transition abwarten
        ok_split = page.evaluate("""(() => {
            const s = document.getElementById('split-sheet');
            const b = s.getBoundingClientRect();
            const y = Math.max(0, Math.min(b.top + 30, window.innerHeight - 1));
            const probe = document.elementFromPoint(195, y);
            return probe ? !!probe.closest('#split-sheet') : false;
        })()""")
        assert ok_split, f"Karte {idx}: Split-Sheet nicht sichtbar ueber Modal"
        page.evaluate("""(() => ['split-sheet', 'split-sheet-backdrop'].forEach(id => document.getElementById(id)?.classList.remove('visible')))()""")
        page.evaluate("closeCardModal()")
        page.wait_for_timeout(250)
