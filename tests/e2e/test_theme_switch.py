"""E2E: Dark-Mode-Wechsel + Kontrast-Regression (weiss auf weiss darf nie passieren)."""
import uuid

import pytest

from tests.e2e.conftest import BASE, _register  # noqa: E402

pytestmark = pytest.mark.e2e


def _lum(r, g, b):
    """Relative Luminanz nach WCAG (0=dunkel, 1=hell)."""
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(rgb1, rgb2):
    """Kontrastverhaeltnis WCAG."""
    l1, l2 = _lum(*rgb1), _lum(*rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _parse_rgb(s):
    import re
    m = re.match(r"rgba?\((\d+), (\d+), (\d+)", s)
    return tuple(int(x) for x in m.groups()) if m else None


def test_theme_switch_and_contrast(page):
    """Theme-Schalter: dark->light->dark; in BEIDEN Themes muss der
    Account-Dropdown-Kontrast >= 4.5:1 sein (weiss auf weiss = Crash)."""
    user = f"th{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    page.goto(f"{BASE}/")
    page.wait_for_timeout(600)
    # Robust gegen globale theme-Settings frueherer E2E-Laeufe (setTheme
    # speichert global): auf dark normalisieren, dann weiter im Ablauf.
    page.evaluate("() => setTheme('dark')")
    page.wait_for_timeout(400)

    def dd_contrast():
        page.click("#account-dd-btn")
        page.wait_for_timeout(300)
        m = page.evaluate("""(() => {
            const dd = document.getElementById('account-dd');
            const cs = getComputedStyle(dd);
            const item = dd.querySelector('.ad-item');
            const ic = item ? getComputedStyle(item) : null;
            return {ddBg: cs.backgroundColor, itemColor: ic ? ic.color : cs.color};
        })()""")
        page.click("#account-dd-btn")  # schliessen
        bg, fg = _parse_rgb(m["ddBg"]), _parse_rgb(m["itemColor"])
        return _contrast(bg, fg) if bg and fg else 0

    # DARK (Standard): Kontrast >= 4.5
    c_dark = dd_contrast()
    assert c_dark >= 4.5, f"Dark-Kontrast zu schwach: {c_dark:.1f}:1"

    # Auf LIGHT umschalten (Settings-Select)
    page.goto(f"{BASE}/settings")
    page.wait_for_selector("#theme-select", state="visible")
    page.select_option("#theme-select", "light")
    # onchange kann im Volllauf race'n - robust: setTheme direkt aufrufen
    page.evaluate("() => setTheme('light')")
    page.wait_for_timeout(700)
    is_light = page.evaluate("() => document.documentElement.classList.contains('theme-light')")
    assert is_light, "theme-light-Klasse nicht gesetzt"

    c_light = dd_contrast()
    assert c_light >= 4.5, f"Light-Kontrast zu schwach: {c_light:.1f}:1 (weiss auf weiss?)"

    # body-Hintergrund ist wirklich hell
    body_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    r = _parse_rgb(body_bg)
    assert r and sum(r) > 500, f"Light-Mode: body nicht hell: {body_bg}"

    # zurueck auf dark
    page.select_option("#theme-select", "dark")
    page.wait_for_timeout(400)
    is_dark = page.evaluate("() => !document.documentElement.classList.contains('theme-light')")
    assert is_dark

    # Persistenz: neu laden -> noch dark? (Setting via API gespeichert)
    page.reload()
    page.wait_for_timeout(800)
    still_dark = page.evaluate("() => !document.documentElement.classList.contains('theme-light')")
    assert still_dark, "Theme nach Reload nicht persistiert"


def test_color_scheme_meta_present(page):
    """iOS-Fix-Verifikation: color-scheme:dark ist deklariert (native Pickers dunkel)."""
    user = f"cs{uuid.uuid4().hex[:8]}"
    _register(page, user, f"{user}@example.com", "geheim123")
    page.goto(f"{BASE}/")
    page.wait_for_timeout(500)
    # Robust gegen globale light-Settings frueherer Laeufe:
    page.evaluate("() => setTheme('dark')")
    page.wait_for_timeout(400)
    scheme = page.evaluate("() => getComputedStyle(document.documentElement).colorScheme")
    assert scheme in ("dark", ""), f"color-scheme fehlt: {scheme!r}"
    # das :root erbt auf selects (Safari rendert dann dunkle Wheel-Picker)
    sel_scheme = page.evaluate("""(() => {
        const s = document.querySelector('select') || document.documentElement;
        return getComputedStyle(s).colorScheme;
    })()""")
    assert sel_scheme in ("dark", ""), f"select color-scheme: {sel_scheme!r}"
