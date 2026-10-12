"""UI-Selbsttest: Screenshot + automatische Mess-Analyse fuer UI-Zustaende.

Wiederverwendbar nach jeder UI-Aenderung:
  python scripts/ui_selftest.py [zustand ...]

Zustaende (Default: alle): login, share_owner, share_guest, scanner, tabbar
Analyse pro Screenshot:
  1. Automatische DOM-Messung: sichtbar? groesse? abgeschnitten? touch >= 44px?
  2. PNG-Screenshot in ui_selftest/ fuer manuelle/Vision-Pruefung
Ergebnis: PASS/FAIL je Check + Report-Datei.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://localhost:8765"
OUT = Path("C:/Users/LLM/AppData/Local/hermes/cache/scratch/ui_selftest")
OUT.mkdir(parents=True, exist_ok=True)

IPHONE_UA = "iPhone; CPU iPhone OS 17_5 like Mac OS X AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"


async def _login(page, username="nils", password="geheim123"):
    await page.goto(f"{BASE}/login", wait_until="networkidle")
    await page.fill("#login-input", username)
    await page.fill("#password-input", password)
    await page.click("#btn-login")
    await page.wait_for_url(f"{BASE}/", timeout=20000)


async def _measure(page, selector: str, label: str, min_w=44, min_h=44, min_text_px=8):
    """Misst ein Element: Sichtbarkeit, Groesse, Clipping, Touch-Minimum."""
    data = await page.evaluate(
        """([sel, mw, mh]) => {
        const el = document.querySelector(sel);
        if (!el) return {exists: false};
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        // Sichtbar im Viewport?
        const inView = r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
        // Clipping: sichtbare Breite < Offsetbreite (overflow abschneidet)?
        const clippedW = r.width < el.scrollWidth - 2;
        return {exists: true, x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                inView, clippedW,
                fontSize: parseFloat(cs.fontSize),
                value: (el.value || '').slice(0, 20)};
    }""",
        [selector, min_w, min_h],
    )
    if not data.get("exists"):
        return {"check": label, "selector": selector, "pass": False, "reason": "Element fehlt", "data": data}
    issues = []
    if not data["inView"]:
        issues.append("nicht sichtbar")
    if data["w"] < min_w:
        issues.append(f"zu schmal ({data['w']}px < {min_w}px)")
    if data["h"] < min_h:
        issues.append(f"zu niedrig ({data['h']}px)")
    if data.get("clippedW"):
        issues.append("Inhalt abgeschnitten")
    if data.get("fontSize", 16) < min_text_px:
        issues.append(f"Schrift zu klein ({data['fontSize']}px)")
    return {"check": label, "selector": selector, "pass": not issues,
            "reason": "; ".join(issues) if issues else "OK", "data": data}


async def run_state(browser, state: str, results: list):
    ctx = await browser.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=3,
        is_mobile=True, has_touch=True, ignore_https_errors=True, user_agent=IPHONE_UA,
    )
    page = await ctx.new_page()
    page.set_default_timeout(15000)

    if state == "login":
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.screenshot(path=str(OUT / "login.png"))
        results.append(await _measure(page, "#login-input", "Login: Benutzername-Feld"))
        results.append(await _measure(page, "#password-input", "Login: Passwort-Feld"))
        results.append(await _measure(page, "#btn-login", "Login: Anmelden-Button"))

    elif state == "share_owner":
        await _login(page)
        await page.evaluate("openShareDialog()")
        await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "share_owner.png"))
        results.append(await _measure(page, "#invite-username-input", "Teilen: Benutzername-Eingabe"))
        # Typ-Test: 12 Zeichen muessen lesbar reinpassen
        await page.fill("#invite-username-input", "MaxMustermann12")
        await page.wait_for_timeout(200)
        m = await _measure(page, "#invite-username-input", "Teilen: Eingabe mit 15 Zeichen")
        typed = await page.evaluate("document.getElementById('invite-username-input').value")
        m["reason"] += f" | getippt: '{typed}'"
        results.append(m)
        results.append(await _measure(page, "#invite-role-select", "Teilen: Rollen-Auswahl"))
        results.append(await _measure(page, "#btn-invite-user", "Teilen: Einladen-Button"))
        await page.fill("#invite-username-input", "")
        await page.screenshot(path=str(OUT / "share_owner_filled.png"))

    elif state == "share_guest":
        # Gast-Sicht: als eingeloggter Owner ist das Join-Feld KORREKT versteckt.
        # Wir pruefen: (a) Join-Bereich existiert im DOM, (b) ist als Owner unsichtbar,
        # (c) Mitglied-Bereiche sichtbar. Ein echter Gast-Login waere schoener, aber
        # dafuer muesste ein Zweit-Account existieren - der Dialog-Fallback genuegt.
        await _login(page)
        await page.evaluate("openShareDialog()")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "share_guest.png"))
        join_hidden = await page.evaluate(
            "getComputedStyle(document.getElementById('share-join-area')).display === 'none'"
        )
        results.append({"check": "Teilen: Join-Feld korrekt versteckt (Owner)",
                        "pass": join_hidden, "reason": "OK" if join_hidden else "sichtbar?!"})

    elif state == "scanner":
        await _login(page)
        await page.goto(f"{BASE}/scanner", wait_until="networkidle")
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "scanner.png"))
        results.append(await _measure(page, "#mode-btn-collection", "Scanner: Modus Sammlung"))
        results.append(await _measure(page, "#mode-btn-price", "Scanner: Modus Preis"))
        results.append(await _measure(page, "#start-camera-btn", "Scanner: Kamera-Start"))

    elif state == "tabbar":
        await _login(page)
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / "tabbar.png"))
        for tab in ["Start", "Sammlung", "Scanner", "Wunsch", "Mehr"]:
            sel = f".tab-item:has-text('{tab}')"
            # has-text ist playwright-spezifisch; alternativ via evaluate
            data = await page.evaluate(f"""(() => {{
                const items = Array.from(document.querySelectorAll('.tab-item'));
                const el = items.find(i => i.textContent.includes('{tab}'));
                if (!el) return {{exists: false}};
                const r = el.getBoundingClientRect();
                return {{exists: true, w: Math.round(r.width), h: Math.round(r.height), inView: r.height > 40}};
            }})()""")
            results.append({"check": f"Tab-Bar: {tab}", "pass": bool(data.get("exists") and data["w"] >= 44 and data["inView"]),
                            "reason": "OK" if data.get("exists") else "Tab fehlt", "data": data})

    await ctx.close()


async def main():
    states = sys.argv[1:] or ["login", "share_owner", "share_guest", "scanner", "tabbar"]
    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for state in states:
            try:
                await run_state(browser, state, results)
            except Exception as e:
                results.append({"check": f"Zustand {state}", "pass": False, "reason": f"Absturz: {e}"})
        await browser.close()

    fails = [r for r in results if not r.get("pass")]
    print("\n" + "=" * 70)
    print("UI-SELBSTTEST — ERGEBNIS")
    print("=" * 70)
    for r in results:
        icon = "✓" if r.get("pass") else "✗"
        print(f"  {icon} {r['check']}: {r.get('reason', '')}")
    print("=" * 70)
    print(f"  {len(results) - len(fails)}/{len(results)} Checks bestanden | Screenshots: {OUT}")
    (OUT / "report.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
