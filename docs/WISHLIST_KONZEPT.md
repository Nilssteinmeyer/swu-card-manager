# Konzept: Merkliste (Wishlist) — Enterprise-Redesign
**Stand:** 13.10.2026 · **Status:** Zur Freigabe durch Nutzer · **Basis:** 147 Quellen (65 TCG-Industrie · 42 UX-Best-Practices · 40 SWU/Preisquellen)

---

## 1. Ist-Analyse — warum der „Wunsch"-Reiter nervt

| # | Problem | Nachweis |
|---|---|---|
| 1 | **Drei unverbundene Features** auf einer Seite: Karten-Suche+Merkliste, MKM-Export (admin-only, wirft Fehler ohne MKM-Konto), Deck-Preisrechner | wishlist.html |
| 2 | **Preise fehlen komplett** im Merklisten-Grid: Query joint nur `card_prices` (Cardmarket = 0 Zeilen in DB); die 10.495 TCGplayer-Preise werden ignoriert — derselbe Bug wie früher in der Sammlung | repository.get_wishlist |
| 3 | **„Preise anzeigen" existiert dort nicht** — der Umschalter sitzt nur in der Sammlung. Ein Klick auf der Wunsch-Seite kann logischerweise nichts bewirken | kein Element in wishlist.html |
| 4 | **USD wird als EUR angezeigt**: TCGplayer-Preise sind USD (`get_collection_value_usd`), die UI rendert `currency: "EUR"` — falsche Währung, kein Quellen-Label | collection.html L288ff |
| 5 | Keine Badges (Rarity/Foil), keine Sortierung, kein Filter, kein Mengen-Editor, kein Zielpreis — die DB-Felder `target_price` + `notes` existieren, sind aber nirgends angebunden | schema.py L384 |
| 6 | Kein Ein-Tap-Add: Scanner-Ergebnis („falsche Karte") hat keinen „Merken"-Button; Karten-Detail in Sammlung hat keinen; nur die Suche auf der Wunsch-Seite selbst | scanner.js, collection.html |
| 7 | Whishlist-Schema kennt keine Variante: kein `is_foil`, keine Sprache — unmöglich, gezielt eine Foil zu suchen | PRAGMA wishlist |

## 2. Recherche-Kern (147 Quellen — Auswahl der entscheidenden Befunde)

**Industrie-Top-10-Muster** (TCGplayer, Moxfield, Archidekt, Deckbox, Dragon Shield, ManaBox, CollX, Mugi TCG, OP.TCG, MTGGoldfish …):
1. Merkliste = **eigenes First-Class-Objekt** (eigener Tab, eigene Tabelle) — niemals Deck-Missbrauch (Moxfield-/Archidekt-Fehler)
2. **Ein-Tap-Add überall**: Kartendetail, Scanner-Ergebnis, Deck („fehlende Karten → Merkliste in Bulk" — Deckbox), Set-Browsing
3. **Varianten-Genauigkeit**: Printing/Foil/Sprache pro Eintrag; Metadaten hängen am Printing, nicht an der Karte (Moxfield-Lektion)
4. **Zielpreis + Alert pro Eintrag** (Mugi: fallend+steigend, Marktplatz wählbar)
5. Zielpreis-Treffer als **leiser visueller Zustand** (grüne Glocke, Grand Line Binder) — kein Alert-Spam (Cardmarket: nur erster Treffer)
6. **Preistransparenz**: Quelle neben jedem Preis (OP.TCG), mehrere Quellen umschaltbar (Archidekt/ManaBox)
7. **Gesamtsumme live mitgerechnet** (Archidekt „Est cost") + „Deck-Kosten minus eigener Sammlung"
8. Teilbarkeit (Share-Links; bei uns: Haushalt-Modell deckt das ab)
9. **Set-Vervollständigung als Merklisten-Generator** (Bulk-Add pro Set)
10. Export: CSV + MKM-Wants-kompatibel (Ökosystem baut darauf auf)

**UX-Autoritäten** (NN/g, Baymard, Apple HIG, Discogs, Steam, Keepa, Vinted):
- „Wishlist" wirkt geschenk-lastig — **„Merkliste"/„Gesucht"** testet besser (NN/g)
- Feste Zeilenstruktur: Thumbnail | Name+Set | Varianten-Chip | Preis+Trend | Ziel-Status (NN/g List-Anatomy) — verhindert Pogo-Sticking
- **Zwei-Alarm-Modell**: Zielpreis-Treffer = Act-now (Push), Preis-Bewegung = Digest (Steam/Vinted/Discogs: 1 Sammel-Mail/Tag)
- **Alert-Budget**: Tageslimit gegen Alert-Müdigkeit bei volatilen Preisen (Vinted), gesendete Alerts bleiben einsehbar (Keepa „Recent Notifications")
- Empty State ist Design-Fläche (Scanner-/Suchvorschlag), kein „leer"-Wort (vocab.design)
- Preis-Delta auf der Karte („+12 % seit gespeichert") — Ehrlichkeit in der Liste
- Grid als Default (Bild-Produkte), kompakte Liste als Toggle für Preisvergleiche
- Swipe nur als Beschleuniger, nie einzige Weg (NN/g Macy's-Fall)

**SWU-spezifisch** (offizielle FFG-Artikel, swu-db, swu.fan, Cardmarket/TCGplayer/tcgcsv-Dokus):
- **4 Basis-Prints pro Karte**: Standard, Foil, Hyperspace, Hyperspace-Foil — jede mit eigenem Preis; Premium (Showcase/Prestige/Serialized) haben eigene Collector-Nummern. **Ab Set „A Lawless Time" (LAW) entfällt Standard-Foil** — Variant-Logik muss set-abhängig sein
- **Cardmarket-API ist für Neukunden geschlossen** — CSV-Import bleibt der einzige saubere Weg; das MKM-Wantslist-Datenmodell ist die Blaupause: Menge, Sprache, Min-Zustand, Foil, Zielpreis (FROM/SELL/±Semantik), Mail-Alarm
- **tcgcsv-Schema-Korrektur** (live verifiziert): Es gibt KEINE `market_normal`/`market_foil`-Spalten im Originalformat — eine Zeile pro `productId × subTypeName` (Normal/Foil); Hyperspace = eigene productId. Parser muss pivotieren. Täglicher Update ~20:00 UTC
- **Marktlücke bestätigt**: Kein einziges SWU-Tool hat heute eine echte Merkliste mit Zielpreis-Alerts (TCGplayer-App hat keine nativen Alerts; Dragon Shield ohne SWU; TCGAlert/TCGPriceAlert generisch)

## 3. Zielbild

**Der Reiter wird vom „Wunsch"-Konglomerat zum Beschaffungs-Cockpit** (Discogs-Prinzip):
> *Was fehlt mir, was kostet es, wo gibt es ein Schnäppchen — auf einen Blick, mit Alarm, wenn der Preis fällt.*

### 3.1 Benennung & Navigation
- Tab heißt künftig **„Merkliste"** (Icon ❤️ statt ⭐) — NN/g: „Wunschliste" wirkt geschenk-lastig; „Merkliste" ist der Sammler-Begriff
- Unter-Reiter innerhalb der Seite: **Meine Karten** | **Deck-Preisrechner** | (Admin: **Export**)

### 3.2 Datenmodell (SQLite-Migration, rückwärtskompatibel)

```sql
-- Erweiterung wishlist (bestehende Zeilen bleiben gültig):
ALTER TABLE wishlist ADD COLUMN is_foil        INTEGER DEFAULT 0;   -- Variante gesucht
ALTER TABLE wishlist ADD language            TEXT DEFAULT 'de';    -- 'de'|'en'|'any'
ALTER TABLE wishlist ADD priority            INTEGER DEFAULT 2;    -- 1=Must-have 2=Normal 3=Nice-to-have
ALTER TABLE wishlist ADD price_at_add        REAL;                 -- Preis beim Hinzufügen (Delta-Anzeige)
ALTER TABLE wishlist ADD added_at_price_src  TEXT;                 -- 'tcg'|'mkm'
-- target_price (REAL) und notes (TEXT) existieren bereits -> werden angebunden
ALTER TABLE wishlist ADD COLUMN target_reached_at TEXT;             -- erster Treffer (Anti-Spam, Cardmarket-Muster)

-- Preis-Alarm-Verlauf (Keepa-Muster: gesendete Alerts bleiben sichtbar):
CREATE TABLE IF NOT EXISTS wishlist_alerts (
    alert_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    wishlist_id  INTEGER NOT NULL REFERENCES wishlist(wishlist_id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,          -- 'target_hit' | 'price_drop' | 'digest'
    old_price    REAL, new_price REAL,
    created_at   TEXT DEFAULT (datetime('now')),
    acknowledged INTEGER DEFAULT 0
);
```

- Preise weiterhin aus `tcg_prices` (USD, Pivot-Handling im Sync korrigieren) + `card_prices` (MKM, EUR) — **mit Währungs- und Quellen-Label**
- Zielpreis-Semantik wie Cardmarket: absolut ODER relativ („−10 % vom Marktpreis")

### 3.3 API-Design (neu/erweitert, alle mit Haushalts- + can_edit-Guard)

| Endpunkt | Methode | Zweck |
|---|---|---|
| `/api/wishlist` | GET | Liste + Preise (dual-source), Zielpreis-Status, Summen, Sortierung/Filter server-seitig |
| `/api/wishlist/add` | POST | + `is_foil`, `language`, `target_price`, `notes`, `priority`; erfasst `price_at_add` |
| `/api/wishlist/update` | POST | Menge, Zielpreis, Notiz, Priorität einzeln änderbar (Edit-Sheet) |
| `/api/wishlist/remove` | POST | wie gehabt (+ Undo-Journal wie Sammlung) |
| `/api/wishlist/summary` | GET | Gesamtsumme, „unter Zielpreis"-Count, Preis-Bewegung 24h/7d |
| `/api/wishlist/alerts` | GET/POST | Alert-Verlauf lesen/quittieren |
| `/api/wishlist/deck-missing` | POST | Deckliste → fehlende Karten **in Bulk** auf die Merkliste (Deckbox-Muster) |
| `/api/deck/price` | POST | bleibt, Antwort um „auf Merkliste fehlend"-Flag ergänzt |

### 3.4 UI-Konzept (iPhone-first)

**Kopf der Seite — Statistik-Leiste** (wie Dashboard):
`12 Karten · Gesamt $14,32 · 2 unter Zielpreis · 1 Warnung`

**Grid-Ansicht (Default)** — identische Karten-Kacheln wie Sammlung:
- Badge oben links (Rarity C/U/R/S/L), **Foil-Badge** oben rechts wenn `is_foil` gesucht
- Preis unten: `$0.35 · TCGplayer` (Quelle IMMER, OP.TCG-Muster) bzw. `0,35 € · Cardmarket`
- **Zielpreis-Chip**: grün „✓ unter Ziel ($0.30)" / grau „Ziel: $0.25" / orange „−12 % zum Ziel"
- Preis-Delta klein unter dem Preis: „↓ 8 % seit gespeichert"
- Tap → **Karten-Detail-Sheet** (wie Sammlung) + Merklisten-Aktionen: Menge ±, Zielpreis setzen, Notiz, Priorität, Entfernen (mit Undo)

**Listen-Ansicht (Toggle)** — kompakte Zeilen für Preisvergleiche (NN/g List-Anatomy):
`Thumb | Name+Set | Foil/Sprache-Chip | Preis + Quelle | Ziel-Status` — Zeilen identisch formatiert, keine Detailseite nötig

**Steuerung**:
- Suchfeld wie gehabt (Autocomplete statt Enter-only)
- Sortierung: Zuletzt hinzugefügt · **Preis aufsteigend** (Discogs „Lowest first") · Zielpreis-Distanz · Priorität · Set
- Filter: Set, Foil ja/nein, Sprache, Priorität, „nur unter Zielpreis", „nur mit Preis"
- Ansicht: ☰ Liste / ▦ Grid

**Deck-Preisrechner** (eigener Unter-Reiter, wie gehabt, aber mit Brücke):
- Nach Berechnung: Button **„Fehlende N Karten auf Merkliste"** (Bulk-Add, Deckbox-Muster)
- Antwort zeigt: Deck-Kosten · **abzüglich bereits im Besitz** (existiert) · abzüglich bereits auf Merkliste

**Export** (Admin, eigenes Panel statt freistehendem Button):
- MKM-Wantslist-Export (wie gehabt, jetzt mit Foil/Sprache/Zielpreis angereichert)
- **CSV-Export der Merkliste** (Card, Menge, Foil, Sprache, Zielpreis, aktueller Preis, Quelle — CollX/MTGGoldfish-Muster)

**Empty State** (vocab.design): Droid-Icon + „Noch nichts gemerkt" + 3 Wege: „🔍 Karte suchen" · „📷 Falsch gescannt? Nach dem Scan auf ‚Merken' tippen" · „📦 Fehlende Karten eines Sets importieren"

**Ein-Tap-Add überall (neue Integrationen):**
1. **Scanner**: Im Overlay unter „✗ Falsche Karte" → neue Option „❤️ Zur Merkliste" (falsch erkannte/unerwünschte Karte ohne Umweg merken; kein Foto, kein Sammlungs-Eintrag)
2. **Sammlung → Karten-Detail**: Button „❤️ Merken" (wenn Karte nochmal gewünscht) bzw. „✓ auf Merkliste"
3. **Sets → Set-Detail**: „Fehlende → Merkliste" existiert schon; zusätzlich Checkbox „nur Foil fehlt mir"
4. **Dashboard**: Kachel „Merkliste: N Karten, M unter Zielpreis"

### 3.5 Preis-Beobachtung & Alarme (das Differenzierungsmerkmal — Marktlücke lt. Recherche)

- **Zielpreis pro Eintrag** (absolut `$ 0,25` oder relativ `−10 % vom Marktpreis`)
- **Prüf-Job**: läuft nach dem täglichen Preis-Sync (tcgcsv ~20:00 UTC) einmal täglich über alle Einträge
- **Zwei-Alarm-Modell**:
  - *Act-now*: Zielpreis erreicht → E-Mail (SMTP vorhanden) + In-App-Badge; **nur der erste Treffer** (Anti-Spam, Cardmarket) — Reset bei erneutem Überschreiten um >20 %
  - *Digest*: 1 Sammel-Mail/Tag „3 deiner Karten sind gesunken" (Steam/Vinted-Budget: max. relevante Meldungen, Rest im Verlauf)
- **Alert-Verlauf** in der App einsehbar + quittierbar (Keepa)
- Konfiguration in Einstellungen: Master-Switch, Kanäle, „nur erste Treffer"

### 3.6 Währungs-Ehrlichkeit (Bugfix mit eingereichtet)
- USD-Preise künftig als `$` gerendert, MKM als `€` — **mit Quellen-Label** an jedem Preis (Sammlung, Merkliste, Scanner, Dashboard, Deck-Rechner)
- Dashboard „Sammlungswert": Quelle + Währung im Stat-Label statt falschem €
- Optional später: Tageskurs-Umwandlung (nicht Teil dieses Redesigns — Label zuerst)

## 4. Umsetzungsplan (4 Commits, jeder grün getestet)

| Phase | Inhalt | Tests |
|---|---|---|
| **1 — Fundament** | DB-Migration (Varianten, priority, price_at_add, alerts-Tabelle), `/api/wishlist` dual-source mit Währungs-/Quellen-Label + Zielpreis-Status + Summen, tcgcsv-Pivot-Korrektur, USD/EUR-Bugfix in Sammlung/Dashboard/Scanner | Migration-Test, API-Tests (Preise, Zielpreis-Logik, Guards), Sync-Test |
| **2 — UI Merkliste** | Neue wishlist.html: Unter-Reiter, Grid/Liste, Badges, Sortierung, Filter, Detail-Sheet mit Menge/Ziel/Notiz/Priorität, Empty State, Statistik-Kopf | E2E: Add/Sort/Filter/Edit/Remove+Undo, Grid/List-Toggle |
| **3 — Integrationen** | Scanner „Zur Merkliste", Sammlung-Detail „Merken", Deck-Rechner „Fehlende → Merkliste (Bulk)", Sets-Foil-Checkbox, Dashboard-Kachel, CSV-Export, MKM-Export angereichert | E2E je Integration + Export-Tests |
| **4 — Alarme** | Zielpreis-Job nach Preis-Sync, Alert-Verlauf-API + UI, E-Mail (Digest + Act-now), Einstellungen, Anti-Spam-Reset-Logik | Job-Tests (Treffer/Reset/Digest-Batching), Mail-Test mit Mock |

**Nicht Teil dieses Redesigns** (bewusst): historische Preis-Charts, Multi-Quellen-Blending (Guardian-TCG-Muster), Trading/Trade-Matching zwischen Nutzern, mehrere parallele Merklisten — gute Roadmap-Items nach Nutzer-Feedback.

## 5. Offene Entscheidungen (bitte kurz beantworten)

1. **Tab-Name**: „Merkliste" ❤️ (Empfehlung der Recherche) — oder lieber „Gesucht"/„Wunschliste" behalten?
2. **Alarm-Kanal**: E-Mail (SMTP ist konfiguriert) + In-App — okay als Phase 4, oder erstmal nur In-App?
3. **Deck-Preisrechner**: in der Merkliste als Unter-Reiter (Empfehlung, mit Bulk-Brücke) — oder ganz woanders hin?
4. **Freigabe Phase 1–4 komplett**, oder erst 1–2 und dann Feedback?
