# Enterprise-Konzept: Ablageorte & Decks — Physische Sammlungsverwaltung
**Stand:** 13.10.2026 · **Status:** Zur Freigabe · **Basis:** ~225 Quellen (73 physische Aufbewahrung · 60 Deck-Verwaltung · 92 UX/DB/Inventur)
**Recherche-Reports:** `~/deck_research_verwaltung.md`, `~/swu-inventar-tiefenrecherche.md`, `scratch/tcg_research/ABLAGEORTE_RECHERCHE.md`

---

## 1. Warum dieses Feature (Markt-Validierung)

- **Card Kingdom (Profi-Retailer):** „Fool's Demise eine Stunde in Boxen gesucht" — der Kernschmerz in einem Satz
- **COMC (Millionenkarten-Warehouse):** Physisches Chaos + digitale Ort-Datenbank = funktionierendes Geschäftsmodell — unsere App ist „COMC für zu Hause"
- **CardCastle-LGS-Blog:** Chaos-Bins funktionieren NUR mit Software-Tracking — genau das liefert der Scan
- **Marktlücke bestätigt:** KEIN SWU-Tool (auch nicht der Wettbewerber Datapad) hat physische Orte; selbst Dragon Shield (#1-Scanner-App, 18 Mio. Scans/Monat) hat nur flache Listen — und migriert gerade Richtung freie Ordner („Sorted"-App). Das „Wo liegt meine Karte"-Feature existiert vereinzelt im Pokémon-Bereich als Premium-Verkaufsargument (TCG Collector: „map physical binder slots")
- **Moxfields dokumentierte Lücke:** Nutzer fragen seit Jahren „ist die Karte FREI, nicht nur owned?" (#1027 „1 available", #1539 „physisch unmöglich, alle Decks zu bauen") — wir beantworten das mit `verfügbar = besitzt − eingebaut`
- **Deck-Ablageort-Rückgabe („Return to Origin") existiert als Muster bei Sortly (Inventur-App)** — im TCG-Bereich bietet es niemand

## 2. Das Datenmodell (Synthese aus 5 Blaupausen)

Kern-Erkenntnis aus StackOverflow-F1, PokéCollector-F2, Silvane, ManaBox, Snipe-IT: **Zwei-Ebenen-Trennung**

```
┌────────────────────────────────────────────────────────────────┐
│ EBENE 1: PHYSISCH (exklusiv — Karte liegt an GENAU EINEM Ort)  │
├────────────────────────────────────────────────────────────────┤
│ storage_locations                                             │
│   location_id, household_id, name, typ, sortierung,           │
│   is_default (0/1), is_active, kapazitaet, notiz               │
│   typ ∈ {standard, binder, box, deckbox, sonstige}             │
│   → Deck erzeugt automatisch einen Ort vom Typ 'deckbox'       │
│                                                                │
│ collection (bestehende Tabelle wird erweitert)                 │
│   + location_id FK → storage_locations (ON DELETE RESTRICT)   │
│   (statt: alle Kopien in Zeilen mit Menge — Mengen-Split        │
│    übernehmen wir NICHT: siehe "Mengen-Problem" unten)         │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ EBENE 2: LOGISCH (frei — Karte kann in N Deck-Listen stehen)   │
├────────────────────────────────────────────────────────────────┤
│ decks                                                          │
│   deck_id, household_id, name, format, leader, base,          │
│   status ∈ {geplant, gebaut}, created_at                      │
│                                                                │
│ deck_cards (Deckliste/Bauplan, Katalog-Ref + Menge)           │
│   deck_id FK, card_id, menge, zone ∈ {maindeck, sideboard}     │
│   UNIQUE(deck_id, card_id, zone)                              │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ VERBINDUNG: BewEGUNGS-JOURNAL (Sortly "Return to Origin")     │
├────────────────────────────────────────────────────────────────┘
│ card_moves                                                     │
│   move_id, collection_id FK, household_id,                    │
│   from_location_id, to_location_id,                            │
│   reason ∈ {scan, manuell, deck_build, deck_resolve,          │
│             korrektur, eingang, abgang},                      │
│   created_at, client_event_id (Idempotenz)                    │
└────────────────────────────────────────────────────────────────┘
```

**Die Regeln (aus den Quellen abgeleitet):**
1. **Kopie an genau EINEM Ort** — „Karte in 2 Decks" ist physisch unmöglich; die Antwort der Spotify-Analogie: Deck-LISTE ist N:M frei, der ORT ist exklusiv. Die App zeigt Konflikte als Warnung („auch in Deck B gelistet")
2. **Deck = Bauplan + physischer Ort.** Beim „Bauen" (Karte per Scan zuweisen) wandert die Kopie an die Deck-Box; die Deckliste bleibt davon unberührt (PokéCollector: Planned vs. Real)
3. **Deck auflösen = Return to Origin:** `to = from` des jüngsten `deck_build`-Moves (Sortly-Muster); ohne Origin → Standard-Ort
4. **`verfügbar = besitzt − in Decks eingebaut`** — die zentrale Kennzahl (Silvane, Moxfield-Wunsch #1027), angezeigt in Sammlung und Deck-Builder
5. **Lösch-Schutz:** `ON DELETE RESTRICT` + API-Check; UI zeigt präventiv ausgegrauten Button + Kartenanzahl + Link zur Kartei (Dynamics-365-Goldstandard; Windows-Dialog = Negativbeispiel)
6. **Standard-Ort ist ein geschützter Seed-Datensatz** — kein Lösch-Button, nur Umbenennen erlaubt (Siebel „Protect Seed Data"); Bedeutung: „Eingangs-Box" für unsortierte Karten (CoolStuffInc-Empfehlung)

**Mengen-Problem (ehrlich benannt):** Die bestehende Sammlung speichert `card_id + variante = eine Zeile mit count`. Eine Karte mit 7 Kopien, davon 3 im Deck und 4 im Ordner, braucht eine Aufteilung. **Lösung:** Beim ersten „Umbuchen" wird die Zeile automatisch in zwei Zeilen aufgeteilt (gleiche Karte/Variante, unterschiedliche `location_id`) — für den Nutzer unsichtbar, die Sammlungs-Ansicht aggregiert weiterhin nach Karte. Das Journal macht jede Aufteilung rückholbar.

## 3. Ablageorte — UX-Konzept

**Anlage & Verwaltung** (eigener Bereich, erreichbar über Sammlung → „Orte"):
- Name frei wählbar („Rotes Binder", „Bulkbox SOR"), **Typ** (Ordner/Binder/Box/Sonstiges) mit Icon, optionale Kapazität (Füllstand %, MizouTCG-Muster), optionale Notiz
- Umbenennen jederzeit; **Löschen nur wenn leer** — Button ausgegraut + „3 Karten liegen noch darin" + Button „Karten umziehen" (Apple-Muster: präventiv statt Fehler zur Laufzeit)
- Standard-Ort „Sammlung (Standard)" ist immer da, nicht löschbar
- **Sortierung pro Ort konfigurierbar** (die Recherche ergab 2 konkurrierende SWU-Standards): nach Set+Nummer (Collector) oder Aspekt>Kosten (Deck-Bauer) — als Default pro Typ: Binder=Set, Box=Aspekt

**Scan-Zuweisung** (der Kern-Workflow):
- Scan-Ergebnis zeigt neu: **„📁 Ablageort: [Standard ▾]"** — Bottom-Sheet-Auswahl mit Suche (NN/g: Bottom Sheet statt Dropdown ab >5 Optionen; Karten-Bild bleibt im Hintergrund sichtbar)
- **Ziel-Banner:** Gewählter Ort bleibt persistent sichtbar über allen weiteren Scans (ShipHero: „erst den Ort scannen, dann die Karten") — Bulk-Modus: Ort einmal wählen → 50 Karten scannen, alle landen dort
- **Undo statt Bestätigungsdialog** (Apple/Material/NN/g einstimmig): Toast „→ Bulkbox SOR ✓ · Undo" — kein Dialog, nächster Scan läuft sofort weiter
- QR-Codes für Orte als optionaler Ausdruck („physical shelf and its database record are directly linked" — PartsBox-Muster) — Phase 2

**Sammlungs-Ansicht:** Jede Karte zeigt den Ort als Chip („📁 Bulkbox SOR · 3×"); Filter „Wo? Ort X"; Karten-Detail listet alle Fundorte mit Mengen (Silvane „Where is this card?")

## 4. Decks — UX-Konzept

**Anlage:** Name („Cad Bane Gelb"), Format (Premier/Eternal/Twin Suns), optional Leader/Base — reine Metadaten, die Deckliste ist anfangs leer. Ein Tap „Deck anlegen" → automatisch gehört eine **Deck-Box als Ablageort** dazu (ManaBox: Deck als registriertes physisches Objekt).

**Karten ins Deck (2 Wege):**
1. **Per Scan:** Scan-Ergebnis → statt Ablageort das aktive Deck wählen („🃏 → Deck ‚Cad Bane Gelb'") — die gescannte Karte wandert an die Deck-Box, der `deck_build`-Move merkt sich den vorherigen Ort
2. **Aus der Sammlung:** Karte öffnen → „In Deck X" — bei mehreren Kopien: freie zuerst (verfügbar-Mathe), sonst Warnung „alle 3 Kopien stecken bereits in Decks"

**Deck-Detail:** Deckliste (Bauplan) mit habe/fehlt-Spalte (osu!-Muster: „Collection is followed by a list of missing maps"), `50/50`-Zähler + Sideboard `x/10` (SWU-Regeln: max 3 Kopien global über Main+Side), Karten-Grid mit Fundort-Chip, Summe „Deck-Wert" und „noch zu kaufen: X €" (fehlt × Preis). **Buttons:** „Deck auflösen" (Rückgabe) · „Deck umbenennen" · „Deck löschen" — und die 100%-Zuweisungs-Anzeige („48/50 Karten liegen in der Deck-Box").

**Deck auflösen** (Return to Origin): Ein Bestätigungs-Dialog mit konkreter Liste („52 Karten kehren zurück: 31 → Rotes Binder, 12 → Bulkbox SOR, 9 → Standard") — dann Bulk-Umbuchung via Journal. **Deck löschen vs. auflösen sind zwei getrennte Operationen** (Spotify-Lektion: Nutzer verwechseln das — „Löschen" entfernt nur die Liste und warnt, wenn noch Karten in der Deck-Box liegen; „Auflösen" räumt physisch zurück und löscht danach das Deck).

**Scanner-Anbindung:** Über dem Kamera-Feed ein Deck-Kontext-Chip („🃏 Ziel: Deck ‚Cad Bane Gelb'") — antippen wechselt zwischen „Sammlungs-Modus" (Ort wählen) und „Deck-Modus" (aktives Deck). Scan im Deck-Modus: Karte → Deck-Box + Deckliste-Eintrag falls noch nicht drin.

**Platz in der Navigation:** Decks als eigener Bereich im „Mehr"-Menü + Dashboard-Kachel („3 Decks · 2 gebaut"); Ablageorte wohnen in der Sammlung (Filter-Chip + Verwaltungs-Eintrag) — die Tab-Bar bleibt 5-slottig.

## 5. Offene Punkte (Empfehlungen)

1. **Sideboard:** Im Datenmodell vorbereitet (`zone`), UI Phase 2 — SWU-regelkonform (max 10, global 3er-Limit)
2. **Decklisten-Import** (SWUDB-JSON / offizieller Code / Limitless-Text): Phase 2 — erzeugt den Bauplan, nicht die physische Belegung
3. **QR-Etiketten** für Orte: Phase 2 (Ausdruck „Box 3" auf die Box kleben, QR scannen setzt Ziel)
4. **Sealed-Produkte** (Displays/Booster-Boxen als eigene Objekte mit Ort): Phase 3 — TCG Vault HQ zeigt das Feld, SWU-Displays sind real

## 6. Umsetzungsplan (5 Phasen, jede grün getestet + 1 Commit)

| Phase | Inhalt | Tests |
|---|---|---|
| **1 — Fundament** | DB v5: `storage_locations`, `decks`, `deck_cards`, `card_moves`; `collection.location_id`; Standard-Ort-Seed; Mengen-Split-Logik; Repository-Funktionen (CRUD Orte/Decks, Move mit Journal, verfügbar-Mathe) | Migration, CRUD, Journal, Mengen-Split, Lösch-Schutz |
| **2 — Orte-UI + Scan** | Orte-Verwaltung (Sammlung), Orts-Chips in Sammlung + Detail „Wo liegt die Karte?", Scan-Zuweisung mit Bottom-Sheet + Ziel-Banner + Undo-Toast, Batch-Modus | E2E: Ort anlegen/umbenennen/löschen(belegt→Block), Scan-Zuweisung, Undo |
| **3 — Decks-UI** | Decks-Bereich, Deck-Detail (habe/fehlt, Zähler), Karte aus Sammlung ins Deck, Scanner-Deck-Modus, Scan-Zuweisung an Deck-Box | E2E: Deck anlegen/befüllen/scannen, verfügbar-Mathe, Duplikat-Schutz |
| **4 — Auflösen & Löschen** | „Deck auflösen" mit Rückgabe-Dialog + Bulk-Journal, „Deck löschen" mit Warnung bei belegter Deck-Box, Orte-Löschen mit Umziehen-Flow | E2E: Auflösen (Origin-Kette), Löschen-Blockaden, Kaskaden |
| **5 — Feinschliff** | Dashboard-Kacheln (Decks, Orte), CSV-Export mit Ort-Spalte, Validierung (50/10/3er), Performance (Journal-Index) | Suite gesamt + Live-Verifikation |

**Nicht Teil** (Roadmap): Seiten-/Slot-Genauigkeit im Binder („Seite 4, Fach 7" — TCG-Collector-Feature), Preis-Alarme auf Deck-Wert, Playtesting, Karten verkaufen/verschenken mit Ort-Abgang.
