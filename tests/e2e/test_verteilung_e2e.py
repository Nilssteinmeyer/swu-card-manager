"""E2E: Aufteilen (Split) + Verteilungsregeln + Orts-Ansicht aggregiert."""
import uuid

import pytest

from tests.e2e.conftest import BASE, _register

pytestmark = pytest.mark.e2e


def _fresh_user(page, prefix):
    user = f"{prefix}{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    page.goto(f"{BASE}/logout")
    page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-input", state="visible", timeout=10000)
    page.fill("#login-input", user)
    page.fill("#password-input", "geheim123")
    page.click("#btn-login")
    page.wait_for_url(f"{BASE}/")


def test_ort_ansicht_aggregiert(page):
    """Bugfix-E2E: 8 Exemplare in 4 Zeilen -> Orts-Ansicht zeigt EINEN Eintrag ×8."""
    _fresh_user(page, "agg")
    # 4 getrennte Bestandszeilen derselben Karte im Standard-Ort
    page.evaluate("""(async () => {
        for (let i = 0; i < 4; i++) {
            await api('/api/collection/add', { method: 'POST', body: { card_id: 'SOR-005', count: [4,1,2,1][i], language: i === 1 ? 'de' : 'en' } });
        }
    })()""")
    result = page.evaluate("""(async () => {
        const locs = await api('/api/locations');
        const std = locs.locations.find(l => l.is_default);
        const cards = await api(`/api/locations/${std.location_id}/cards`);
        return { items: cards.count, total: cards.items.reduce((s, i) => s + i.count, 0) };
    })()""")
    assert result["items"] == 1, "8 Exemplare in 4 Zeilen -> 1 aggregierter Eintrag"
    assert result["total"] == 8


def test_split_flow_im_detail(page):
    """✂️ Aufteilen: 8 Exemplare -> Ordner 1 / Playsets 3 / Bulk 4."""
    _fresh_user(page, "spt")
    page.evaluate("""(async () => {
        await api('/api/collection/add', { method: 'POST', body: { card_id: 'SOR-005', count: 8, language: 'de' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Ordner', typ: 'binder', icon: '📕' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Playsets', typ: 'binder', icon: '🎯' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Bulk', typ: 'box', icon: '🗑️' } });
    })()""")
    page.goto(f"{BASE}/collection")
    page.wait_for_selector(".collection-item", timeout=8000)
    page.click(".collection-item")
    page.wait_for_selector("#detail-bestand-sektion", timeout=8000)
    page.wait_for_timeout(600)

    # Split-Sheet oeffnen
    page.evaluate("[...document.querySelectorAll('#detail-bestand-rows button')].find(b => b.textContent.includes('Aufteilen')).click()")
    page.wait_for_selector("#split-sheet.visible", timeout=5000)
    # Regel-Vorschlag (ohne Regeln: alles bei Ort 1). Wir setzen per Stepper:
    state = page.evaluate("""(() => {
        const rows = [...document.querySelectorAll('#split-sheet .items, #split-sheet .flex')];
        return SPLIT_STATE ? { total: SPLIT_STATE.total, orte: SPLIT_STATE.orte.map(o => o.name) } : null;
    })()""")
    assert state, "SPLIT_STATE muss initialisiert sein"
    orte_ids = page.evaluate("() => SPLIT_STATE.orte.map(o => o.location_id)")
    namen = page.evaluate("() => SPLIT_STATE.orte.map(o => o.name)")

    # Ordner: 1, Playsets: 3, Bulk: 4 — Stepper per JS-API (Touch-genau)
    ziel = {n: i for i, n in enumerate(namen)}
    def set_menge(name, menge):
        lid = orte_ids[ziel[name]]
        page.evaluate(f"""(([]).length, SPLIT_STATE.verteilt[{lid}] = {menge},
            document.getElementById('split-val-' + {lid}).textContent = {menge}, 0)""")
    set_menge("Ordner", 1)
    set_menge("Playsets", 3)
    set_menge("Bulk", 4)
    # Andere Orte (Standard) auf 0
    for n, lid in zip(namen, orte_ids):
        if n not in ("Ordner", "Playsets", "Bulk"):
            set_menge(n, 0)
    page.evaluate("updateSplitSumme()")
    summe_text = page.evaluate("() => document.getElementById('split-summe').textContent")
    assert "Alle 8" in summe_text, f"Summen-Zeile muss grün sein: {summe_text}"

    # Speichern
    page.evaluate("""window.__splitResp = null;
        const _origFetch = window.fetch;
        window.fetch = async function(url, opts) {
            const r = await _origFetch.apply(this, arguments);
            if (String(url).includes('/api/collection/split')) {
                const clone = r.clone();
                clone.json().then(j => { window.__splitResp = { status: r.status, body: j }; }).catch(() => { window.__splitResp = { status: r.status }; });
            }
            return r;
        };""")
    page.evaluate("saveSplit()")
    page.wait_for_timeout(1200)
    resp = page.evaluate("() => window.__splitResp")
    print("DEBUG split resp:", resp)

    verifikation = page.evaluate("""(async () => {
        const locs = await api('/api/locations');
        const byName = {};
        for (const l of locs.locations) byName[l.name] = l.karten_count;
        return byName;
    })()""")
    assert verifikation["Ordner"] == 1, f"Ordner muss 1 haben: {verifikation}"
    assert verifikation["Playsets"] == 3
    assert verifikation["Bulk"] == 4


def test_regel_editor_und_preview(page):
    """Einstellungen: Beispiel-Regeln anlegen -> Preview zeigt 1/3/4."""
    _fresh_user(page, "rgl")
    page.evaluate("""(async () => {
        await api('/api/locations', { method: 'POST', body: { name: 'Ordner', typ: 'binder', icon: '📕' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Playsets', typ: 'binder', icon: '🎯' } });
        await api('/api/locations', { method: 'POST', body: { name: 'Bulk', typ: 'box', icon: '🗑️' } });
    })()""")
    page.goto(f"{BASE}/settings")
    page.wait_for_selector("#verteilung-regeln-liste", timeout=8000)
    page.wait_for_timeout(500)
    # Beispiel anlegen
    page.evaluate("beispielRegeln()")
    page.wait_for_timeout(300)
    regeln = page.evaluate("() => VT_REGELN.length")
    assert regeln == 3, "Beispiel-Regelsatz = 3 Regeln"
    # Speichern
    page.evaluate("saveRegeln()")
    page.wait_for_timeout(800)
    # Preview bei 8 Karten
    page.fill("#vt-preview-menge", "8")
    page.evaluate("renderPreview()")
    page.wait_for_timeout(400)
    preview = page.evaluate("() => document.getElementById('vt-preview-out').textContent")
    assert "×1" in preview and "×3" in preview and "×4" in preview, f"Preview: {preview}"

    # API verifizieren
    api_result = page.evaluate("""(async () => {
        const p = await api('/api/verteilung/preview', { method: 'POST', body: { menge: 8 } });
        return p.preview.map(x => x.name + ':' + x.menge).join(', ');
    })()""")
    assert "Ordner:1" in api_result and "Playsets:3" in api_result and "Bulk:4" in api_result
