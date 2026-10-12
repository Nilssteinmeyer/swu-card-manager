# MASTERPLAN — SWU Card Manager: Complete UI Redesign (PC / iPhone / Android)

> **Status:** PLAN ONLY — nicht ausführen ohne Freigabe. Erstellungsdatum: 2026-10-12.
> Grundlage: IST-Analyse (Pixel-vermessen aus 15 echten Screenshots) + Apple HIG + Material Design 3 + WCAG + PWA-Navigation-Patterns.

---

## 1. IST-ANALYSE (vermessen, nicht geraten)

### 1.1 Der gemeldete Bug — Ursache gefunden (2 unabhängige Ursachen!)

**Bug A — Account-Menü unsichtbar (KRITISCH, trifft JEDE Breite):**
Die `.navbar` hat `overflow-x: auto`. Der Browser berechnet daraus `overflow-y: auto` (CSS-Regel: wenn eine Achse `auto`/`scroll` ist, wird die andere nie `visible`). Das Account-Dropdown (`.account-dropdown { position:absolute; top: calc(100% + 8px) }`) öffnet 8px UNTER dem 47px-Clip-Bereich der Navbar → **vollständig geclippt, unsichtbar**. „Abmelden", „Sammlung teilen", „Sammlung wechseln" sind faktisch nicht bedienbar — auf Handy UND PC.

**Bug B — Navbar nicht vollständig erreichbar (Mobile-spezifisch):**
Eine Zeile enthält: Brand (128px) + Account-Btn (102px, **mittig!**) + 7 Nav-Items (à 38,7px ≈ 271px) ≈ **541px Bedarf bei 390px Viewport → 151px Overflow**. Die Navbar scrollt horizontal (`overflow-x: auto`), aber es gibt KEINEN visuellen Hinweis (iOS blendet Scrollbalken aus; kein Fade, Pfeil, Punkt). Nur 3 von 7 Tabs sind unscrolled sichtbar, der 3. am Rand abgeschnitten.

**Bug B2 — Entweder-Oder-Konflikt (neu vermessen):** Beim Swipe zu den hinteren Tabs scrollt der Account-Button links aus dem Bild ("nils" bei x=0 abgeschnitten) — **Account-Funktionen und vollständige Navigation sind nie gleichzeitig erreichbar.** Auf /collection ist zusätzlich der AKTIVE Tab nur als 26px-Sliver am rechten Rand sichtbar: Der Nutzer weiß weder wo er ist, noch wie er zum Account kommt.

### 1.2 Messwerte (390×844 iPhone-Viewport, DPR 3)

| Element | Ist-Zustand | Problem |
|---|---|---|
| Navbar-Zeile | y=11–38px, Höhe ~27px (bei 47px Container) | Unter 44px Touch-Minimum (HIG) |
| Account-Btn | **MITTIG** (x=169–271, ~102px), 2-zeiliger Textumbruch; Dropdown 0 sichtbare Pixel | Nicht rechts platziert! Swarmt beim Scrollen links raus → nie gleichzeitig mit allen Tabs erreichbar (Bug B2) |
| 7 Nav-Tabs | je ~38,7px ≈ 271px, Cluster ab x=286 | Nur 3 sichtbar, 3. (Sammlung 🗂️) am Rand abgeschnitten; Wishlist/Sets/Einstellungen/System = 0px sichtbar |
| Scan-Modus-Segment | 263×35px (x=14–277), rechts daneben **113px ungenutzt** | 35px < 44px Touch-Minimum |
| Sammlung: Filterwand | ≥10 Felder, y=145–802+ (**~660px**) vor der 1. Karte | Erster Screen zeigt keine einzige Karte |
| Dashboard Stat-Grid | 7 Karten à 178px in 2 Spalten → 4 Reihen, **4. Reihe halbleer**; Labels 8–10px | Unruhiges Raster, winzige Labels |
| Kamera-Start-Button / Undo-Btn | 151×37px / 33–35px hoch | Beide < 44px Touch-Minimum |
| Undo-Button (Scanner) | 33–35px hoch | < 44px Touch-Ziel |
| Scan-Modus-Schalter | 263×35px Segment (x=14–277) | Funktioniert; 35px knapp unterm Minimum |
| `viewport-fit=cover` | fehlt in ALLEN 12 Templates | Safe-Area (Home-Indicator) unbeachtet |
| `env(safe-area-inset-*)` | 0 Vorkommen im gesamten CSS | Tab-Bottom-Bar würde in den Indikator ragen |
| Templates | 12 Dateien, jede mit **kopierter Navbar** | Kein Jinja-Base-Template → Änderungen 12× |
| CSS | 1.124 Zeilen, GitHub-Palette (#58a6ff-Blau) | Kein Rollen-System, keine Plattform-Schicht |
| Account-Menü-Inhalt | Dropdown (kaputt), mobile unerreichbar | Sämtliche Account-Aktionen blockiert |

### 1.3 Bewährung der aktuellen Pages (Mobile, je Seite)
- **Login:** OK, aber generisch, kein Star-Wars-Charakter
- **Dashboard:** 7 Stat-Karten (Grid 2-spaltig), Tabellen mit Scroll — funktioniert, wirkt aber wie Desktop-verkleinert
- **Scanner:** Kamera + Modus-Schalter OK; Ergebnis-Overlay ist funktional, aber eng (Buttons 2-zeilig, Cancel klein)
- **Sammlung:** Filter-Ein-/Ausklappen gut; Karten-Grid auf 390px = 2 Spalten à ~180px OK
- **Einstellungen/System:** Desktop-Formulare, auf Mobile viel Scroll, kleine Schalter

---

## 2. STYLE-GUIDE-ANALYSE (Recherche-Grundlage)

### 2.1 Apple Human Interface Guidelines (iOS) — Kernpunkte
- **Tab Bar am unteren Rand**: „Use the appropriate number of tabs... it's generally easier to navigate among fewer tabs. **Avoid overflow tabs**." Ein „Mehr"-Tab ist die iOS-Konvention, wenn > 5 Sections — aber: „limit scenarios where this happens"
- **Touch-Ziele: 44×44pt Minimum** (Recommended; absolutes Minimum 28×28pt), ~12pt Padding zwischen Zielen
- **Tab-Labels**: einzeilig, kurze Wörter; Icons über Label (compact); gefüllte Icons (SF Symbols Stil)
- **Liquid Glass (iOS 26-Ästhetik)**: schwebende, halbtransparente Bars über dem Inhalt
- **Tab Bar nicht verstecken** (außer bei Modals); aktiver Tab farblich klar
- **Bottom-Position = Daumenzone** — primäre Navigation gehört dorthin

### 2.2 Material Design 3 (Android) — Kernpunkte
- **Breakpoints**: compact (<600dp) = **Bottom Navigation Bar** (3–5 Ziele); medium/expanded = **Navigation Rail** (seitlich); „different components performing the same function across breakpoints"
- **Touch-Ziele: 48×48dp Minimum**
- **Color-Roles-System**: primary/on-primary, primaryContainer, surface/surfaceContainer 1–4, outline — Kontrast automatisch parebar; Dark Theme als Basis
- **Navigation Bar M3**: Pill-Indikator hinter aktivem Item, Icon+Label, Ripple-Feedback
- **FAB (Floating Action Button)** für die prominenteste Aktion — hier: **Scanner** (das ist die Kernaktion der App!)
- **Tonal elevation**: Flächen über Farbe statt nur Schatten

### 2.3 WCAG 2.5.5/2.5.8 + UX-Research
- **44×44 CSS-px** für Touch-Ziele (Enhanced), 24px absolutes Minimum (AA)
- Hamburger-Menüs senken Discovery um 20–50% (NNGroup) → **Bottom-Tab-Bar statt Hamburger bei 3–5 Hauptbereichen** (PWA-Standard; „logged-in environments" profitieren am meisten)
- Statische responsives Verhalten: „Navigation should not occur as a side-effect of window size changes" — ein Navigationssystem, drei Darstellungen

### 2.4 Star Wars Design-Sprache (für Charakter der App)
- **Palette „An Old Hope" / Film-Referenzen**: tiefes Space-Schwarz (`#0a0e14` ist bereits gut), **Imperial-Yellow `#FFE81F`** (klassisches SW-Logo-Gelb) als Akzent statt GitHub-Blau, Cyan für Scanner/HUD-Elemente, Rot nur destruktiv (Löschen/Errors)
- **HUD-Ästhetik**: Scanner-Seite als „Ziel-Erfassung" (Targeting-Computer-Frame um die Karte, Scanline-Effekt beim Erkennen)
- Weniger ist mehr: SW-Motive subtil (Ränder, Scan-Frame, Empty States), kein Theme-Park — die Kartenbilder sind bunt genug
- **Typografie**: System-Font-Stack bleibt (Performance, Lesbarkeit), für Headlines optional „Orbitron"/„Aurebesh-artige" Akzente nur in Logos/Banner

---

## 3. ZIEL-ARCHITEKTUR

### 3.1 Geräte-Erkennung (3 Wege, kombiniert)

**Server-seitig (User-Agent → Jinja-Variable):** Flask-Helper `detect_platform()` → `ios | android | desktop`, injiziert `<html class="platform-ios">` in Base-Template. Regeln: iPhone/iPad Safari → ios; Android-Chrome → android; sonst desktop. UA-Games (Desktop-Mode) werden clientseitig korrigiert.

**Client-seitig (Verfeinerung):** `common.js` prüft `navigator.userAgentData?.platform` / `CSS.supports(-webkit-touch-callout)` + Viewport-Breite und überschreibt die Klasse bei Widerspruch (Tablet im Desktop-Modus etc.).

**CSS-Schichten:**
```css
/* Sichtbarkeit: EINE Navigation, drei Darstellungen */
.nav-desktop { display: none; }
.platform-desktop .nav-desktop { display: flex; }   /* Topbar + Labels */
.tabbar { display: none; }
.platform-ios .tabbar, .platform-android .tabbar { display: flex; }
/* Plattform-Feinschliff: */
.platform-ios .tab-item { /* HIG: gefüllte Icons, 10px Label */ }
.platform-android .tab-item { /* M3: Pill-Indikator, Ripple */ }
```
**Regel:** Kein „iOS auf Android nachbauen" — beide folgen ihren Konventionen, teilen aber Struktur (gleiches Jinja-Partial, gleiche Routen, gleiche ARIA-Rollen).

### 3.2 Neue Navigations-IA (Information Architecture)

**5 Hauptbereiche statt 7+Items in einer Zeile:**

| Slot | Bereich | Enthält |
|---|---|---|
| 1 | 📊 Dashboard | Start, Statistiken |
| 2 | 🗂️ Sammlung | Karten, Filter, Detail, Manuell hinzufügen |
| 3 | **📷 Scanner** | ZENTRUM, hervorgehoben (FAB-artig auf Android) — Kernaktion |
| 4 | ⭐ Wunschliste | Wishlist, MKM-Export |
| 5 | ⚙️ Mehr | Sets, Einstellungen, System (admin), **Account (Profil, Abmelden, Sammlung teilen/wechseln)** |

**Bug A + B lösen sich dadurch strukturell:** Account-Menuumzieht in „Mehr"-Sheet (Bottom Sheet auf Mobile, Dropdown auf Desktop aus NAChBAR-Container MIT `overflow: visible`), die Top-Navbar wird auf Mobile durch eine schlanke Kopfzeile (Logo + Haushalt) ersetzt.

**Darstellung je Plattform:**
- **iPhone:** Bottom Tab Bar (5 Tabs, HIG: Icon über Label, gefüllt, Safe-Area-padds, Scanner-Tab mittig hervorgehoben, Liquid-Glass-artige Transparenz `backdrop-filter`)
- **Android:** M3 Navigation Bar (Pill auf aktivem Item, Ripple, 48dp, Scanner ggf. als abschnittsloser FAB über der Bar schwebend — M3-konform)
- **PC:** Obere Navbar (wie jetzt, aber Overflow-Bug gefixt: `overflow: visible`, Account-Dropdown funktionsfähig) + ALLE Items mit Labels, kein Scrollen nötig; Alternativ Seiten-Rail ab ≥1440px

### 3.3 Template-Architektur (Refactor)

- **NEU: `base.html`** (Jinja-Vererbung): `<head>` (inkl. `viewport-fit=cover`, manifest, theme-color), Topbar, Tabbar, Toast-Container, `common.js`-Einbindung. Alle 12 Seiten erben → **Navbar-Wartung an EINEM Ort**.
- `partials/_nav_desktop.html`, `partials/_tabbar.html`, `partials/_more_sheet.html` (Bottom Sheet: Account + admin-Links)
- **NEU: `platform.css`** (Geräte-Schicht, ~200 Zeilen) + refactortes `style.css` (Design-Tokens nach M3-Rollen):

```css
:root {
  /* Star Wars Palette */
  --bg: #0a0e14;                    /* Deep Space */
  --surface: #10151f;               /* Card */
  --surface-2: #161d2b;             /* Container */
  --surface-3: #1c2536;             /* Elevated */
  --outline: #2a3446;
  --on-surface: #e8ecf4;
  --on-surface-variant: #9aa4b2;
  --primary: #FFE81F;               /* Imperial Yellow */
  --on-primary: #1a1600;
  --primary-container: #3d3505;
  --on-primary-container: #FFE81F;
  --secondary: #4DD8FF;             /* Scanner-HUD Cyan */
  --error: #ff5a52;
  --success: #3fd98a;
  /* Metriken */
  --touch-min: 48px;                /* M3 */
  --tabbar-h: 64px;
  --radius-lg: 16px; --radius-md: 12px; --radius-sm: 8px;
  --safe-bottom: env(safe-area-inset-bottom, 0px);
  --safe-top: env(safe-area-inset-top, 0px);
}
```
Farb-Kontrast wird je Rolle gegen WCAG AA geprüft (Gelb auf Schwarz: ~18:1 ✓; on-primary dunkel auf Gelb ✓).

---

## 4. SEITEN-REDESIGN (je Element hinterfragt)

### 4.1 Login / Register / Reset (alle Plattformen)
- Vollflächiger Hintergrund: generiertes **Star-Wars-Artwork** (Sternenfeld + sublimes Lichtschwert-Silhouette, dunkel) mit `prefers-reduced-motion`-statischem Fallback
- Karte (Glass-Morphismus, max. 400px) zentriert; Inputs 48px hoch, Labels darüber (kein Placeholder-only); Passwort-Stärke-Inline
- Fehler nie nur farblich (Icon + Text); Button voll breit (Touch 48px)
- **Desktop:** Zweispaltig — links Artwork/Branding, rechts Formular

### 4.2 Dashboard
- **Mobile:** Hero-Kachel „Sammlungswert" (2 Daten: EUR Cardmarket + USD TCGplayer) groß, darunter 2-spaltige Mini-Stats; Tabellen (Seltenheit/Typ/Set) werden zu **horizontalen Chips-Reihen** mit Zählern (keine Scroll-Tabellen mehr)
- Pull-to-Refresh-Pattern (JS, `overscroll-behavior`)
- **Desktop:** 4-spaltiges Stat-Grid, Tabellen bleiben, plus „Letzte Scans"-Liste rechts (aus scan-Tabelle, nur eigene)

### 4.3 Scanner (Herzstück)
- Kopfzeile mobil: nur Haushalt-Anzeige (Topbar entfällt), Tabbar unten bleibt
- **Scan-Modus-Schalter** (Sammlung/Preis): als **segmentierte Kontrolle DIREKT über der Kamera**, 44px hoch, beide Modi mit Icon; bleibt persistiert
- **Kamera-Frame als HUD**: Ecken des Targeting-Computers (gelbe Klammern, Cyan-Scanline beim Scannen; `reduced-motion` → statisch), Zoom-/Torch-Steuerung bleibt (iOS-Pattern bereits gut)
- Ergebnis-Overlay: Karte 40% oben (Bild groß), darunter Name/Set/Konfidenz; Buttons stapeln mobil vertikal (je 48px): „✓ Richtig", „✗ Falsch", „↩ Weiter"; Preis-Panel wie gebaut, auf Mobile als eigener Abschnitt mit großem Trend-Preis
- Bulk-Session-Counter als schwebender Chip oben rechts
- **Desktop:** Kamera links (60%), rechte Spalte: letzte Scans-Feed + Session-Statistik live

### 4.4 Sammlung
- Mobile: Sticky Mini-Header mit Suchfeld + „Filter"-Chip (öffnet Bottom-Sheet mit ALLEN Filtern, 48px-Reihen, Apply/Reset)
- Karten-Grid: 2 Spalten mobil / 3 (768px) / 5 (Desktop); Karten mit Preis-Badge (falls importiert) + Foil-Icon; Skeleton-Loading
- Detail-Modal: mobil **Bottom-Sheet vollhöhen** (Drag-Handle), Desktop zentriertes Modal (800px) mit 2 Spalten (Bild | Daten+Preis)
- Manuell hinzufügen: gleicher Sheet-Flow

### 4.5 Wunschliste
- Mobil: Liste mit Swipe-Aktionen (nach links = löschen, mit Undo-Toast), MKM-Export-Button im Header („Mehr"-Menü auf Mobile)
- Desktop: Tabelle + Massen-Auswahl

### 4.6 Sets
- Fortschrittsbalken bleiben (gut!), aber mobil: kompakte Set-Zeilen mit radialem Progress-Ring statt langer Balken+Tabelle
- Fehlende-Karten-Modal → Bottom-Sheet mobil
- MKM-Import (Admin): Desktop-Formular, mobil in „Mehr" verlinkt mit Hinweis „am PC empfohlen"

### 4.7 Einstellungen
- **Mobil: Gruppen-Liste** (Scanner / Konto / Darstellung / Admin) mit Unterseiten-Feel (Accordion statt 397-Zeilen-Scroll)
- Custom-Switches (M3-Stil, 48er Reihe), Slider mit großer Grifffläche
- Desktop: Zweispaltig (Navigations-Sektionen links, Inhalte rechts)

### 4.8 System (Admin)
- Nur im „Mehr" verankert; mobil als Karten-Stapel (Logs voll breit, Buttons 48px), Desktop wie jetzt + Fehler-Banner statt roten Textzeilen
- KI-Modelle: Status-Pills (aktiv/kandidat) statt Tabelle auf mobil

### 4.9 „Mehr"-Sheet + Account (ersetzt das kaputte Dropdown)
- Mobile: Bottom Sheet mit: Profil-Kopf (Name, Rolle, Haushalt), Sammlung wechseln (falls mehrere), Sammlung teilen (Owner), Sets, Einstellungen, System (admin), **Abmelden (rot, letztes)**
- Desktop: bestehendes Dropdown am fixen Account-Button — **Bug-A-Fix: Navbar-Container `overflow: visible`, Dropdown-Position aus Scroll-Container herausgelöst**
- Einladungscode: echtes Modal mit Copy-Button + QR (statt Toast)

### 4.10 Leere Zustände (Neu)
- Je Seite ein SW-illustrierter Empty State (generierte Grafiken): Sammlung leer → „Deine Sammlung ist noch leer wie der Weltraum" + Scan-CTA; Wishlist leer → Droid-Suche-Motiv; Fehler → „Rebellen-Transmitter offline"

### 4.11 Toasts & Feedback
- Einheitliche Toasts oben (Safe-Area), 48px-Ziel beim „Rückgängig"-Action-Toast; Haptik bleibt (iOS `navigator.vibrate` unverändert)

---

## 5. STAR-WARS-ASSETS (generieren — Teil der Umsetzung)

Per ComfyUI (lokal installiert, RTX A2000) — Liste der Assets:
1. `login_hero.png` (1920×1080 + mobile-Crop 1080×1920): Sternenfeld, unten rechts Lichtschwert-/Silhouetten-Schemen, sehr dunkel, gelber Akzent — Prompt: „dark star wars inspired starfield, subtle lightsaber silhouette, deep space black, imperial yellow accent glow, cinematic, minimal, no text"
2. `empty_collection.png` (600×600): freundlicher Droid vor leerem Regal, Flat-Illustration
3. `empty_wishlist.png` (600×600): Such-Droid mit Holokarte
4. `error_state.png` (600×600): R2-artiger Droid mit Antennen-Geste
5. `scanner_hud_frame.svg` (Vektor, von Hand gebaut — keine KI): Targeting-Ecken
6. Logo-Rework `icon-512.png`: Karten-Symbol + Imperial-Gelb (ersetzt 🃏-Emoji langfristig)
Lizenz-Sauberkeit: **keine geschützten Figuren/Por_traits direkt** — nur Silhouetten-artige, generische Droiden/Motive (Fan-Art-Stil, lokal genutzt).

---

## 6. UMSETZUNGSPLAN (Task-Reihenfolge, TDD wo testbar)

### Phase 1 — Fundament & Bugfixes (Voraussetzung für alles)
1. **T1: base.html + Jinja-Vererbung** — alle 12 Seiten migrieren (Navbar/Tabbar-Partials), `viewport-fit=cover` überall. Smoke: alle Seiten 200, E2E-Suite grün.
2. **T2: Bug A fix** — Account-Dropdown-Container `overflow: visible` (Desktop-Navbar) + Dropdown-Popover aus Container gelöst; E2E-Test: Dropdown öffnen, „Abmelden" klickbar (390px UND 1920px).
3. **T3: Design-Tokens-Rewrite** — `:root` auf M3-Rollen (Palette Gelb/Cyan), alle `var()`-Namen migriert (sed-artig via Tests: CSS parst, keine `var(--…)`-Dangling-Refs — Build-Check via kleinem Python-Linter).
4. **T4: Plattform-Erkennung** — Flask `detect_platform()` + `<html class>`; Unit-Tests mit UA-Strings (iPhone Safari, Pixel Chrome, Windows, iPad); Client-Korrektur in `common.js`.

### Phase 2 — Navigation
5. **T5: Bottom Tab Bar** (iOS-Darstellung: HIG-Konventionen; Android: M3-Pill) + „Mehr"-Bottom-Sheet + Account-Inhalt dorthin. E2E: Tab-Wechsel auf 390px iPhone-Viewport, „Abmelden" über Sheet erreichbar (der konkrete reported Bug ist damit in der echten Bedienung verifiziert).
6. **T6: Desktop-Navbar-Feinschliff** — Labels, Hover, aktive Zustände, Account-Dropdown final; E2E auf 1920px.
7. **T7: Safe-Area + Touch-Audit** — `env(safe-area-*)` überall wo unten fixiert; alle interaktiven Elemente ≥44px (iOS) / 48px (Android); Playwright-Mess-Test: `getBoundingClientRect()`-Checks auf Tab-Items, Undo-Button, Modus-Schalter.

### Phase 3 — Seiten-Redesign (je Seite: Template + CSS + E2E-Viewport-Test)
8. **T8 Login/Register/Reset** (Desktop zweispaltig, Hero)
9. **T9 Dashboard** (Mobile-Stat-Umbau, Chips statt Tabellen)
10. **T10 Scanner** (HUD-Frame, Overlay-Stapel, Modus-Schalter 44px)
11. **T11 Sammlung** (Filter-Sheet, Grid, Detail-Bottom-Sheet)
12. **T12 Wunschliste + Sets**
13. **T13 Einstellungen + System** (Gruppen-Listen mobil)

### Phase 4 — Assets & Feinschliff
14. **T14 ComfyUI-Asset-Generierung** (Liste §5) + Einbau (Login-Hero, Empty States, Scanner-Frame)
15. **T15 Plattform-Feinschliff** — Ripple (Android), `-webkit-tap-highlight`, Momentum-Scroll, `backdrop-filter`-Fallback (Safari alt), `prefers-reduced-motion` überall
16. **T16 Ende-zu-Ende-Abnahme** — Playwright: JEDE Seite × 3 Viewports (390×844 iOS, 412×915 Android, 1920×1080 Desktop): Screenshot + Basis-Checks (keine horizontale Scrollbar, Tabbar sichtbar, Dropdown/Sheet bedienbar); Suite: 150 Unit + neue E2E grün.

### Test-Strategie
- **E2E-Viewport-Matrix** (neu): `test_ui_platforms.py` — parametrisiert über 3 Viewports × Kernflows (Login → Tab-Wechsel → Account-Sheet → Scanner-Modus)
- Touch-Target-Messung via JS-Injection (`getBoundingClientRect`) als Assertion
- Keine visuelle Regression-Infrastruktur (Overkill lokal) — aber Screenshot-Dumps pro Viewport in `tests/e2e/screenshots/` für manuellen Vergleich

### Risiken
| Risiko | Gegenmaßnahme |
|---|---|
| Backbone-Refactor (base.html) bricht bestehende Tests | E2E nach jedem Template-Migrationsschritt; 12 Seiten in 3 Batches |
| Apple-Gelb auf Schwarz flimmert bei feinen Linien | Gelb nur für Flächen ≥ Text/Fills, Linien/Outlines bleiben neutral |
| UA-Erkennung unscharf (iPadOS 13+ tarnt sich als Desktop) | Client-Korrektur (Touch-Check) als Rückfalllinie |
| ComfyUI-Assets brauchen Zeit/GPU | Phase 4.1, blockiert nichts davor; Fallback: erste Version ohne Hero, Slot-für-später |
| bestehende Scanner-Logik (v15) nicht regressieren | scanner.js wird NICHT neu geschrieben — nur Overlay-DOM/CSS angefasst, Logik bleibt; E2E Preis/Sammlungs-Modus weiterhin grün |

**Geschätzter Aufwand:** Phase 1+2 ≈ 3–4 h, Phase 3 ≈ 5–7 h, Phase 4 ≈ 2–3 h → gesamt ~10–14 h inkl. Tests.

---

## 7. WAS NICHT VERÄNDERT WIRD
- Backend/API: kein Endpunkt ändert sich (reiner Frontend-Redesign + `detect_platform`-Helper)
- scanner.js-Erkennungslogik, Preis-Modus, Undo, Haptik, Settings-Keys
- Alle 150 Unit/API-Tests + bestehenden E2E (werden als Regressionsnetz genutzt)
- Dunkles Theme als Basis (nur Palette/Rollen wechseln: Blau → Imperial-Gelb/Cyan)
