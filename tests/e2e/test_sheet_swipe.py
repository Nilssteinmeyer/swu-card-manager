"""E2E: Bottom-Sheets per Runterziehen schließen + Karten-Detail-Ablageort.

Nutzer-Kritik: 'ich kann nicht runtersliden um das fenster wieder zu
schließen. testest du das nicht oder was?' — genau das deckt dieser Test ab.
"""
import uuid

import pytest

from tests.e2e.conftest import BASE, _register

pytestmark = pytest.mark.e2e


def _login_page(page, user, pw):
    # Session sauber beenden (nach _register ist man bereits eingeloggt,
    # /login redirectet dann -> kein #login-input)
    page.goto(f"{BASE}/logout")
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-input", state="visible", timeout=10000)
    page.fill("#login-input", user)
    page.fill("#password-input", pw)
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")


def _swipe_down(page, selector, distance=180):
    """iOS-Swipe am Sheet-Handle: touchstart oben, touchmove nach unten."""
    page.evaluate(
        """([sel, dist]) => {
            const sheet = document.querySelector(sel);
            const handle = sheet.querySelector('.sheet-handle') || sheet;
            const r = handle.getBoundingClientRect();
            const cx = r.left + r.width / 2;
            const cy = r.top + Math.min(r.height / 2, 30);
            const mk = (type, y) => new TouchEvent(type, {
                bubbles: true, cancelable: true,
                touches: type === 'touchend' ? [] : [new Touch({identifier: 1, target: handle, clientX: cx, clientY: y})],
                changedTouches: [new Touch({identifier: 1, target: handle, clientX: cx, clientY: y})],
            });
            handle.dispatchEvent(mk('touchstart', cy));
            const steps = 6;
            for (let i = 1; i <= steps; i++) {
                handle.dispatchEvent(mk('touchmove', cy + (dist * i / steps)));
            }
            handle.dispatchEvent(mk('touchend', cy + dist));
            return true;
        }""",
        [selector, distance],
    )


def test_loc_manage_sheet_swipe_to_close(page):
    """"Ablageorte verwalten"-Sheet: Swipe-Down schließt es."""
    user = f"sw{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    _login_page(page, user, "geheim123")
    page.goto(f"{BASE}/collection")
    page.wait_for_timeout(1500)
    # Werkzeuge aufklappen + Sheet oeffnen
    page.evaluate("toggleSection('tools-body', 'tools-arrow')")
    page.wait_for_timeout(300)
    page.evaluate("document.getElementById('btn-manage-locations').click()")
    page.wait_for_selector("#loc-manage-sheet.visible", timeout=5000)
    assert page.evaluate("() => document.getElementById('loc-manage-sheet').classList.contains('visible')")

    # iOS-Swipe am Handle nach unten -> Sheet schliesst
    _swipe_down(page, "#loc-manage-sheet", 180)
    page.wait_for_timeout(400)
    closed = page.evaluate("() => !document.getElementById('loc-manage-sheet').classList.contains('visible')")
    assert closed, "Sheet muss per Swipe-Down am Handle schließen (iOS-Konvention)"


def test_card_detail_shows_location_and_moves(page):
    """Karten-Detail: zeigt Ablageort, 'Umlagern' bucht auf anderen Ort um."""
    user = f"mv{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    _login_page(page, user, "geheim123")
    # Karte in die Sammlung + zweiten Ort anlegen
    page.evaluate("""(async () => {
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'SOR-005', count: 2, language: 'de' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Rotes Binder', typ: 'binder', icon: '🔴' } });
    })()""")
    page.goto(f"{BASE}/collection")
    page.wait_for_timeout(1500)

    # Karte antippen -> Detail-Modal
    page.wait_for_selector(".collection-item", timeout=8000)
    page.click(".collection-item")
    page.wait_for_selector("#detail-bestand-sektion", timeout=8000)
    page.wait_for_timeout(600)  # bestand-Rows rendern async nach api/card
    sektion = page.evaluate("""(() => ({
        sichtbar: document.getElementById('detail-bestand-rows').textContent.includes('Sammlung (Standard)'),
        rows: document.querySelectorAll('#detail-bestand-rows > div').length,
    }))()""")
    if not sektion["sichtbar"]:
        dbg = page.evaluate("() => ({rows: document.getElementById('detail-bestand-rows').innerHTML.slice(0, 300), api: window.__lastCard ? window.__lastCard.bestand : 'n/a'})")
        print("DEBUG rows:", dbg)
    assert sektion["sichtbar"], "Ablageort 'Sammlung (Standard)' muss im Detail stehen"
    assert sektion["rows"] >= 1

    # Umlagern: Ziel 'Rotes Binder' im Bottom-Sheet waehlen
    page.evaluate("[...document.querySelectorAll('#detail-bestand-rows button')][0].click()")
    page.wait_for_selector("#move-sheet.visible", timeout=5000)
    # Ziel-Button (Rotes Binder) anklicken
    clicked = page.evaluate("""(() => {
        const btns = [...document.querySelectorAll('#move-sheet .sheet-link')];
        const ziel = btns.find(b => b.textContent.includes('Rotes Binder'));
        if (ziel) { ziel.click(); return true; }
        return false;
    })()""")
    assert clicked, "Ziel-Ort im Move-Sheet muss klickbar sein"
    page.wait_for_timeout(800)

    # Verifikation via API: Karte liegt jetzt im 'Roten Binder'
    ort = page.evaluate("""(async () => {
        const d = await api('/api/locations');
        const binder = d.locations.find(l => l.name === 'Rotes Binder');
        return binder ? binder.karten_count : -1;
    })()""")
    assert ort == 2, f"Nach Umlagerung müssen 2 Karten im 'Roten Binder' liegen (ist: {ort})"

    # Modal bleibt nutzbar: Swipe-Down am move-sheet hat es bereits geschlossen
    # (doMoveBestandRow schliesst) - Karte erneut oeffnen zeigt neuen Ort
    page.evaluate("closeCardModal()")
    page.wait_for_timeout(300)
    page.click(".collection-item")
    page.wait_for_selector("#detail-bestand-sektion", timeout=8000)
    neu = page.evaluate("() => document.getElementById('detail-bestand-rows').textContent.includes('Rotes Binder')")
    assert neu, "Nach Umlagerung muss der neue Ort im Karten-Detail stehen"


def test_icon_picker_swipe_to_close(page):
    """Icon-Picker (dynamisches Sheet) schließt per Swipe-Down."""
    user = f"ip{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    _login_page(page, user, "geheim123")
    page.goto(f"{BASE}/collection")
    page.wait_for_timeout(1200)
    page.evaluate("toggleSection('tools-body', 'tools-arrow')")
    page.wait_for_timeout(300)
    page.evaluate("document.getElementById('btn-manage-locations').click()")
    page.wait_for_selector("#new-loc-icon-btn", state="attached", timeout=5000)
    page.evaluate("document.getElementById('new-loc-icon-btn').click()")
    page.wait_for_selector("#icon-picker-sheet.visible", timeout=5000)

    _swipe_down(page, "#icon-picker-sheet", 200)
    page.wait_for_timeout(400)
    closed = page.evaluate("() => !document.getElementById('icon-picker-sheet').classList.contains('visible')")
    assert closed, "Icon-Picker muss per Swipe-Down schließen"
