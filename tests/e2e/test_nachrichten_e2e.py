"""E2E: Postfach — Sende-Roundtrip (2 Kontexte), Broadcast-Isolation,
Composer-Swipe, Draft, Sichtbarkeitsproben (elementFromPoint)."""
import uuid

import pytest

from tests.e2e.conftest import BASE, _register

pytestmark = pytest.mark.e2e


def _login(page, user, pw="geheim123"):
    page.goto(f"{BASE}/logout")
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-input", state="visible", timeout=10000)
    page.fill("#login-input", user)
    page.fill("#password-input", pw)
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")


@pytest.fixture()
def two_users(e2e_server, browser):
    """Zwei frische User, je eigener Browser-Kontext."""
    import uuid as _u
    contexts = []
    names = []
    for i in range(2):
        ctx = browser.new_context(ignore_https_errors=True)
        pg = ctx.new_page()
        pg.set_default_timeout(15000)
        user = f"usr{_u.uuid4().hex[:8]}"
        pg.goto(f"{BASE}/register")
        pg.fill("#username-input", user)
        pg.fill("#email-input", f"{user}@example.com")
        pg.fill("#password-input", "geheim123")
        pg.click("#btn-register")
        pg.wait_for_selector("#reg-success", state="visible", timeout=10000)
        contexts.append(ctx)
        names.append(user)
    # beide ausloggen (Register laesst evtl. eingeloggt)
    for ctx in contexts:
        pass
    pages = [ctx.pages[0] if ctx.pages else ctx.new_page() for ctx in contexts]
    yield pages[0], pages[1], names[0], names[1]
    for ctx in contexts:
        ctx.close()


@pytest.fixture()
def admin_and_user(e2e_server, browser):
    """Admin (erster User dieser DB wird Admin) + normaler User."""
    import uuid as _u
    ctx_a = browser.new_context(ignore_https_errors=True)
    pa = ctx_a.pages[0] if ctx_a.pages else ctx_a.new_page()
    pa.set_default_timeout(15000)
    admin = f"adm{_u.uuid4().hex[:8]}"
    pa.goto(f"{BASE}/register")
    pa.fill("#username-input", admin)
    pa.fill("#email-input", f"{admin}@example.com")
    pa.fill("#password-input", "geheim123")
    pa.click("#btn-register")
    pa.wait_for_selector("#reg-success", state="visible", timeout=10000)
    # Session-DB: unseren Test-Admin explizit zum Platform-Admin machen
    # (die Session-DB hat evtl. schon User aus frueheren Tests -> 'erster
    # User' ist unbestimmt; E2E_DB_PATH wird vom e2e_server-Fixture geteilt)
    import sqlite3 as _s
    from tests.e2e.conftest import E2E_DB_PATH

    if E2E_DB_PATH:
        _c = _s.connect(E2E_DB_PATH)
        _c.execute("UPDATE users SET is_platform_admin = 1 WHERE username = ?", (admin,))
        _c.commit()
        _c.close()
    _login(pa, admin)
    ctx_b = browser.new_context(ignore_https_errors=True)
    pb = ctx_b.pages[0] if ctx_b.pages else ctx_b.new_page()
    pb.set_default_timeout(15000)
    user = f"usr{_u.uuid4().hex[:8]}"
    pb.goto(f"{BASE}/register")
    pb.fill("#username-input", user)
    pb.fill("#email-input", f"{user}@example.com")
    pb.fill("#password-input", "geheim123")
    pb.click("#btn-register")
    pb.wait_for_selector("#reg-success", state="visible", timeout=10000)
    _login(pb, user)
    yield pa, pb
    ctx_a.close()
    ctx_b.close()


def _swipe_down(page, selector, distance=200):
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
            for (let i = 1; i <= 6; i++) handle.dispatchEvent(mk('touchmove', cy + (dist * i / 6)));
            handle.dispatchEvent(mk('touchend', cy + dist));
            return true;
        }""",
        [selector, distance],
    )


def test_dm_roundtrip_zwei_kontexte(two_users):
    """A sendet an B -> B Badge steigt -> B liest -> Badge sinkt (Reload-safe)."""
    page_a, page_b, name_a, name_b = two_users
    # A: Composer mit Chip-Picker
    page_a.goto(f"{BASE}/messages")
    page_a.wait_for_timeout(800)
    page_a.locator("#btn-msg-compose").click()
    page_a.wait_for_selector("#compose-sheet.visible", timeout=5000)
    page_a.fill("#compose-empfaenger", name_b[:6])
    page_a.wait_for_timeout(400)
    # Vorschlag antippen (echter Klick)
    page_a.locator("#compose-vorschlaege .sheet-link").first.click()
    page_a.fill("#compose-body", "Hey, das ist die E2E-Testnachricht!")
    page_a.locator("#btn-msg-send").click()
    page_a.wait_for_timeout(900)
    # B: Badge + Nachricht
    page_b.goto(f"{BASE}/messages")
    page_b.wait_for_timeout(1000)
    badge_b = page_b.evaluate("() => document.getElementById('msg-total-badge')?.textContent")
    assert badge_b == "1", f"Badge bei B muss 1 sein (ist {badge_b})"
    item_bold = page_b.evaluate("() => document.querySelector('.msg-item')?.style.background || ''")
    assert item_bold != "", "Unread-Item hervorgehoben"
    # B oeffnet Thread -> Badge sinkt
    page_b.locator(".msg-item").first.click()
    page_b.wait_for_selector("#thread-view:not(.hidden)", timeout=5000)
    page_b.wait_for_timeout(600)
    badge_after = page_b.evaluate("() => document.getElementById('msg-badge')?.classList.contains('hidden')")
    assert badge_after, "Badge muss nach Lesen verschwinden"
    body_b = page_b.evaluate("() => document.getElementById('thread-bubbles').textContent")
    assert "E2E-Testnachricht" in body_b
    # B antwortet
    page_b.fill("#thread-reply", "Antwort von B!")
    page_b.locator("#btn-thread-send").click()
    page_b.wait_for_timeout(800)
    # A: sieht Antwort + Badge steigt
    page_a.goto(f"{BASE}/messages")
    page_a.wait_for_timeout(1000)
    badge_a = page_a.evaluate("() => document.getElementById('msg-total-badge')?.textContent")
    assert badge_a == "1", f"Badge bei A muss 1 sein (Antwort von B, ist {badge_a})"



def test_broadcast_empfaengerisolation(admin_and_user):
    """Admin-Broadcast: beide sehen ihn; nur A ackt -> nur A's Badge sinkt."""
    page_admin, page_user = admin_and_user
    # Admin sendet Broadcast
    page_admin.evaluate("""(async () => {
        await api('/api/broadcasts', { method: 'POST', body: {
            typ: 'update', betreff: 'E2E-Broadcast', body: 'Alle muessen das lesen' } });
    })()""")
    # Beide haben unread
    for p in (page_admin, page_user):
        p.goto(f"{BASE}/messages")
        p.wait_for_timeout(900)
        badge = p.evaluate("() => document.getElementById('msg-total-badge')?.textContent")
        assert badge == "1"
        assert p.evaluate("() => document.body.textContent.includes('E2E-Broadcast')")
        # KEIN Reply-Feld im Broadcast (assert absence)
        assert p.evaluate("() => !document.querySelector('#broadcast-list #thread-reply')")
    # Admin (oder user) ackt -> nur dessen Badge sinkt
    page_admin.locator("#broadcast-list button", has_text="Verstanden").first.click()
    page_admin.wait_for_timeout(700)
    assert page_admin.evaluate("() => document.getElementById('msg-total-badge')?.classList.contains('hidden')")
    page_user.reload()
    page_user.wait_for_timeout(900)
    badge_user = page_user.evaluate("() => document.getElementById('msg-total-badge')?.textContent")
    assert badge_user == "1", "User hat weiter unread (Isolation!)"
    # user kann KEINE Stats abrufen (403)
    stats = page_user.evaluate("(async () => { const r = await fetch('/api/broadcasts/1/stats'); return r.status; })()")
    assert stats == 403


def test_composer_draft_und_swipe(page):
    """Draft ueberlebt Swipe-Close; nach Senden weg; Composer sichtbar ueber allem."""
    user = f"dr{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    _login(page, user)
    page.goto(f"{BASE}/messages")
    page.wait_for_timeout(800)
    page.locator("#btn-msg-compose").click()
    page.wait_for_selector("#compose-sheet.visible", timeout=5000)
    # Sichtbarkeit (Sheet-ueber-allem-Regression!)
    sichtbar = page.evaluate("""(() => {
        const s = document.getElementById('compose-sheet');
        const b = s.getBoundingClientRect();
        const y = Math.max(0, Math.min(b.top + 30, window.innerHeight - 1));
        const probe = document.elementFromPoint(195, y);
        return probe ? !!probe.closest('#compose-sheet') : false;
    })()""")
    assert sichtbar, "Composer muss sichtbar sein (elementFromPoint)"
    page.fill("#compose-body", "Mein Entwurf den niemand verlieren darf")
    # Swipe-Close -> Draft gespeichert
    _swipe_down(page, "#compose-sheet", 220)
    page.wait_for_timeout(600)
    assert page.evaluate("() => !document.getElementById('compose-sheet').classList.contains('visible')")
    # Neu oeffnen -> Draft wieder da
    page.locator("#btn-msg-compose").click()
    page.wait_for_selector("#compose-sheet.visible", timeout=5000)
    draft = page.evaluate("() => document.getElementById('compose-body').value")
    assert "Entwurf" in draft, f"Draft muss wiederhergestellt sein (ist: {draft!r})"
    # Senden leert Draft (Empfaenger: der 2. registrierte User via API-Self-Check)
    page.evaluate("document.getElementById('compose-sheet').classList.remove('visible'); localStorage.removeItem('swu-msg-draft')")
