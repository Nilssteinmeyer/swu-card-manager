# Verteilungsregeln — Recherche & Design (2026-10-13)

Drei parallele Tiefenrecherchen, **199 Quellen gesamt**:

| Stream | Quellen | Fokus |
|---|---|---|
| TCG-Apps & Sammler-Praxis | 50 | Playsets, Bulk, physische Organisation (Moxfield, Silvane, ManaBox, Reddit r/mtg, r/OnePieceTCG, r/starwarsunlimited, r/Lorcana) |
| ERP-/Logistik-Regelwerke | 81 | SAP EWM Slotting, Oracle WMS Rules, Odoo Putaway, D365/BC Location Directives, Shopify Order Routing, Siebel Assignment, Firewall-First-Match, DMN |
| UI/UX-Splitting & Regel-Editor-Tests | 68 | Splitwise/YNAB/KMyMoney, iOS HIG, PoE/WoW/EVE-Stacks, SwiftUI onMove, Tailscale ACL-Tests, Undo/Command-Pattern, ISTQB/PBT |

## Kern-Ergebnisse

1. **Alleinstellungsmerkmal bestätigt:** Kein Consumer-TCG-Tracker verteilt Exemplare regelbasiert auf physische Orte. Moxfield-Requests wie „Split multiples to binders" sind seit 2–3 Jahren offen (10–35 Upvotes); ManaBox hat als einziger einen manuellen Partial-Move. Unsere Regel-Engine ist ein Unikat im Consumer-Bereich — automatische Verteilung existiert nur als Händler-Hardware (TCGplayer Roca Sifter, SortSwift).
2. **SWU-Playset = 3** (offizielle Deck-Regeln; MTG = 4). Bandengrenzen sind daher bewusst frei konfigurierbar — „Kopien 2–4 → Playsets" ist Nutzer-Wahl, kein Hardcode.
3. **Regel-Semantik = First-Match-Liste** überall bestätigt: iptables-Chain, D365 Sequence Numbers, BC Put-away Template (letzte Zeile = Default/„Find Floating Bin"), Oracle Rules Engine, DMN-Hit-Policy FIRST. Overflow-Modell: `bis_kopie NULL` (offenes Intervall statt Pseudo-9999).
4. **Bulk ist Triage, kein Filing:** „Bulk will keinen Binder" (ManaArchive) — Bulk-Ziele sind Box-Typ-Orte; ab ~6 Kopien einer Karte gehört sie in eine Box (TCG Foundry).
5. **Split-UX (Splitwise/YNAB/KMyMoney/Actual/Venmo):** Rest ≠ 0 als weicher Zustand mit Auto-Distribute-Button; **Über-Zuteilung sofort hart clampsen** (EVE: Drag-Preview wird rot); Stepper nie ohne sichtbaren Wert (HIG); 44px-Targets; bei ungespeicherten Änderungen darf Swipe-to-Close nichts unbemerkt verwerfen (HIG Sheets).
6. **Undo:** Eine komplette Verteilung = EIN Composite-Journal-Schritt (nie pro Zeile — sonst bricht die Summen-Invariante); kompensierende Gegenbuchung statt Journal-DELETE.
7. **Tests:** Decision-Table-Testing + Boundary Value Analysis an allen Bandengrenzen (ISTQB), Property-based Testing (Hypothesis-Invarianten: Σ = Gesamtmenge, keine Negativmenge, Determinismus), Gap/Overlap-Warnungen (IBM-ODM), Tailscale-Muster: erwartete Verteilungen als versionierte Tests.

## Umgesetzt (Schema v5.2, Commits f2b4567 ff.)

- `verteilungs_regeln` (rang, von_kopie, bis_kopie NULL = Rest, ziel_location_id, `bedingung`-Reserve für Preis/Seltenheit)
- `verteile_card`: First-Match-Kette; Fallbacks: Ziel gelöscht → Standard-Ort, Regellücke → Standard-Ort — **nie Kopienverlust**
- Validierung: keine Überlappungen/Doppelten, genau eine Rest-Regel (höchster Start), von ≥ 1
- ✂️ Aufteilen-Sheet: Stepper + **↧ „Rest hierher"** + Hart-Clamp auf unverteilt­en Rest + Live-Summe (grün/orange/rot)
- Regel-Editor: Banden-Liste mit **↑/↓-Reorder** (Reihenfolge = Auswertungsreihenfolge) + Live-Preview-Chips
- Scan-Auto-Verteilung (Setting `auto_verteilung`, nur ohne explizite Scanner-Ortswahl)
- Tests: Entscheidungstabelle (1/2/4/5/8/20), Fallbacks, Split-Attribute/Journal/Haushalts-Isolation, E2E mit Swipe, ↧, Clamp, Reorder

## Backlog (aus Recherche, noch nicht umgesetzt)

- **Playset-Fortschritts-Chip „3/3"** je Karte (Arena-Debatte: Aggregation pro Variante vs. über alle Varianten — Toggle nötig)
- **Preiskriterium je Bande:** „Kopie 5+ UND < 1 € → Bulk; ≥ 1 € → Trade-Box" (Roca-Sifter-Threshold-Muster; `bedingung`-Spalte vorbereitet)
- **„Für Tausch"-Flag** je Bestandszeile (Deckbox-SUBSET-Modell, seit 2008 bewährt) + Trade-Export für Events
- **Kapazitäts-/Voll-Warnung** je Ort (EWM/BC-Muster) + „Bulk-Exit-Potential" (Wert-Summe unter Schwellenwert je Box)
- **„Bestand neu verteilen"-Trockenlauf:** Tabelle Karte/Menge/von/nach + EIN Undo für alles (Silvane-Live-Summary als Gate)
- Property-based Testing (Hypothesis) für die Engine-Invarianten
