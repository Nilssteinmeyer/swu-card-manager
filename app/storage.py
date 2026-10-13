"""Phase 1 Fundament: Repository-Funktionen für Ablageorte + Decks + Journal.

Blaupausen: Sortly 'Return to Origin', Silvane available-Mathe,
PokéCollector Planned-vs-Real, ManaBox Deck-als-Ort.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from app.db import repository as repo


# ---------------------------------------------------------------------------
# Ablageorte (storage_locations)
# ---------------------------------------------------------------------------

def _clean_icon(icon: str | None) -> str | None:
    """Genau EIN Emoji (max 3 Codepoints incl. Variation-Selector/ZWJ)."""
    if not icon:
        return None
    icon = icon.strip()
    if not icon or len(icon) > 3:  # 1 Graphem: Emoji + ggf. VS16/FE0F oder ZWJ
        return None
    return icon


def get_default_location(conn: sqlite3.Connection, household_id: int) -> dict:
    """Standard-Ort des Haushalts - legt ihn on-demand an, falls fehlend.

    Migration seedet nur Haushalte, die zum Zeitpunkt des DB-Imports
    existierten; jede spaetere Registrierung braucht ihren eigenen
    Standard-Ort (Nutzer-Anforderung: IMMER ein Standard-Ablageort).
    """
    row = conn.execute(
        "SELECT * FROM storage_locations WHERE household_id = ? AND is_default = 1",
        (household_id,),
    ).fetchone()
    if row:
        return dict(row)
    cur = conn.execute(
        """INSERT INTO storage_locations (household_id, name, typ, sortierung, is_default)
           VALUES (?, 'Sammlung (Standard)', 'standard', 'set', 1)""",
        (household_id,),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM storage_locations WHERE location_id = ?", (cur.lastrowid,),
    ).fetchone())


def list_locations(conn: sqlite3.Connection, household_id: int) -> list[dict]:
    cur = conn.execute(
        """SELECT l.*, (SELECT COALESCE(SUM(c.count), 0) FROM collection c
                        WHERE c.location_id = l.location_id) AS karten_count
           FROM storage_locations l
           WHERE l.household_id = ? AND l.is_active = 1
           ORDER BY l.is_default DESC, l.name COLLATE NOCASE""",
        (household_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def create_location(conn: sqlite3.Connection, name: str, typ: str = "box",
                    household_id: int = 1, sortierung: str = "set",
                    kapazitaet: int | None = None, notiz: str | None = None,
                    icon: str | None = None) -> int:
    if typ not in ("standard", "binder", "box", "deckbox", "sonstige"):
        typ = "sonstige"
    if sortierung not in ("set", "aspekt", "name"):
        sortierung = "set"
    cur = conn.execute(
        """INSERT INTO storage_locations (household_id, name, typ, sortierung, kapazitaet, notiz, icon)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (household_id, name.strip(), typ, sortierung, kapazitaet, notiz, _clean_icon(icon)),
    )
    conn.commit()
    return cur.lastrowid


def rename_location(conn: sqlite3.Connection, location_id: int, name: str,
                    household_id: int = 1, icon: str | None = None) -> bool:
    """Umbenennen inkl. Standard-Ort erlaubt; Household-Isolation.

    icon: None = unverändert; '' = zurücksetzen (typ-Fallback); 'x' = setzen.
    """
    icon_clean = _clean_icon(icon)
    if icon is not None:
        cur = conn.execute(
            "UPDATE storage_locations SET name = ?, icon = ? WHERE location_id = ? AND household_id = ?",
            (name.strip(), icon_clean, location_id, household_id),
        )
    else:
        cur = conn.execute(
            "UPDATE storage_locations SET name = ? WHERE location_id = ? AND household_id = ?",
            (name.strip(), location_id, household_id),
        )
    conn.commit()
    return cur.rowcount > 0


def location_card_count(conn: sqlite3.Connection, location_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM collection WHERE location_id = ?",
        (location_id,),
    ).fetchone()
    return int(row[0])


def delete_location(conn: sqlite3.Connection, location_id: int,
                    household_id: int = 1) -> tuple[bool, str]:
    """Lösch-Schutz: Standard-Ort nie; belegte Orte nur nach Umbuchung.

    Returns (ok, grund) — grund erklärt die Blockade für die UI
    (Apple-Muster: präventiv + konkreter Grund statt Laufzeit-Fehler).
    """
    row = conn.execute(
        "SELECT is_default FROM storage_locations WHERE location_id = ? AND household_id = ?",
        (location_id, household_id),
    ).fetchone()
    if not row:
        return False, "Ort nicht gefunden"
    if row["is_default"]:
        return False, "Der Standard-Ort kann nicht gelöscht werden"
    count = location_card_count(conn, location_id)
    if count > 0:
        return False, f"{count} Karten liegen noch in diesem Ablageort"
    # Referenz-Check auch im Journal (Snipe-IT-Lektion: Logs hielten Sperre aufrecht)
    journal = conn.execute(
        "SELECT COUNT(*) FROM card_moves WHERE (from_location_id = ? OR to_location_id = ?)",
        (location_id, location_id),
    ).fetchone()[0]
    # Journal-Einträge blockieren NICHT (Historie bleibt gueltig, Ort wird
    # inactive statt geloescht -> Soft-Delete)
    conn.execute(
        "UPDATE storage_locations SET is_active = 0 WHERE location_id = ?",
        (location_id,),
    )
    conn.commit()
    return True, "gelöscht"


def move_cards(conn: sqlite3.Connection, collection_ids: list[int], to_location_id: int,
               reason: str = "manuell", household_id: int = 1,
               client_event_id: str | None = None) -> int:
    """Karten (Bestandszeilen) an einen Ort umbuchen — mit Journal-Eintrag je Zeile.

    Kein Bestätigungs-Dialog nötig: reversibel via Journal (NN/g Undo statt Confirm).
    Idempotenz: client_event_id verhindert Offline-Queue-Doppelbuchungen.
    """
    moved = 0
    for cid in collection_ids:
        row = conn.execute(
            "SELECT location_id, household_id FROM collection WHERE collection_id = ?",
            (cid,),
        ).fetchone()
        if not row or row["household_id"] != household_id:
            continue
        from_loc = row["location_id"]
        if from_loc == to_location_id:
            continue
        conn.execute(
            "UPDATE collection SET location_id = ? WHERE collection_id = ?",
            (to_location_id, cid),
        )
        conn.execute(
            """INSERT INTO card_moves (household_id, collection_id, from_location_id,
                                        to_location_id, reason, client_event_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (household_id, cid, from_loc, to_location_id, reason,
             client_event_id or str(uuid.uuid4())),
        )
        moved += 1
    conn.commit()
    return moved


def split_collection_entry(conn: sqlite3.Connection, collection_id: int,
                           count_to_new: int, to_location_id: int,
                           reason: str = "manuell", household_id: int = 1) -> int | None:
    """Mengen-Problem: Zeile mit count=7 aufteilen (3 an neuen Ort).

    Erzeugt eine neue Zeile mit count_to_new am Ziel-Ort und reduziert die
    Ursprungs-Zeile. Rückgabe: neue collection_id (für Undo), None bei Fehler.
    """
    row = conn.execute(
        "SELECT * FROM collection WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    if not row or row["household_id"] != household_id:
        return None
    if count_to_new <= 0 or count_to_new >= row["count"]:
        return None
    new_count = row["count"] - count_to_new
    conn.execute(
        "UPDATE collection SET count = ? WHERE collection_id = ?",
        (new_count, collection_id),
    )
    cur = conn.execute(
        """INSERT INTO collection (card_id, count, condition, language, variant,
                                    location, source, notes, is_foil, household_id, user_id, location_id)
           SELECT card_id, ?, condition, language, variant, location, source,
                  notes, is_foil, household_id, user_id, ?
           FROM collection WHERE collection_id = ?""",
        (count_to_new, to_location_id, collection_id),
    )
    new_id = cur.lastrowid
    conn.execute(
        """INSERT INTO card_moves (household_id, collection_id, from_location_id,
                                    to_location_id, reason)
           VALUES (?, ?, ?, ?, ?)""",
        (household_id, new_id, row["location_id"], to_location_id, reason),
    )
    conn.commit()
    return new_id


# ---------------------------------------------------------------------------
# Decks (Bauplan + physische Deck-Box)
# ---------------------------------------------------------------------------

def create_deck(conn: sqlite3.Connection, name: str, household_id: int = 1,
                format: str = "premier", leader_card_id: str | None = None,
                base_card_id: str | None = None, icon: str | None = None) -> int:
    """Deck anlegen: automatisch mit Deck-Box als Ablageort (ManaBox-Muster:
    Deck = registriertes physisches Objekt)."""
    icon_clean = _clean_icon(icon)
    cur = conn.execute(
        """INSERT INTO decks (household_id, name, format, leader_card_id, base_card_id, icon)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (household_id, name.strip(), format, leader_card_id, base_card_id, icon_clean),
    )
    deck_id = cur.lastrowid
    loc = conn.execute(
        """INSERT INTO storage_locations (household_id, name, typ, sortierung, icon)
           VALUES (?, ?, 'deckbox', 'aspekt', ?)""",
        (household_id, f"Deckbox: {name.strip()}", icon_clean),
    ).lastrowid
    conn.execute("UPDATE decks SET location_id = ? WHERE deck_id = ?", (loc, deck_id))
    conn.commit()
    return deck_id


def get_deck(conn: sqlite3.Connection, deck_id: int, household_id: int = 1) -> dict | None:
    row = conn.execute(
        "SELECT * FROM decks WHERE deck_id = ? AND household_id = ?",
        (deck_id, household_id),
    ).fetchone()
    return dict(row) if row else None


def list_decks(conn: sqlite3.Connection, household_id: int) -> list[dict]:
    cur = conn.execute(
        """SELECT d.*,
                  (SELECT COALESCE(SUM(menge), 0) FROM deck_cards
                    WHERE deck_id = d.deck_id AND zone = 'maindeck') AS maindeck_count,
                  (SELECT COALESCE(SUM(menge), 0) FROM deck_cards
                    WHERE deck_id = d.deck_id AND zone = 'sideboard') AS sideboard_count,
                  (SELECT name FROM storage_locations WHERE location_id = d.location_id) AS box_name
           FROM decks d WHERE d.household_id = ?
           ORDER BY d.created_at DESC""",
        (household_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def rename_deck(conn: sqlite3.Connection, deck_id: int, name: str,
                household_id: int = 1, icon: str | None = None) -> bool:
    """Umbenennen (Deck + Deck-Box); icon None=unveraendert, ''=reset, 'x'=setzen."""
    icon_clean = _clean_icon(icon)
    if icon is not None:
        cur = conn.execute(
            "UPDATE decks SET name = ?, icon = ? WHERE deck_id = ? AND household_id = ?",
            (name.strip(), icon_clean, deck_id, household_id),
        )
        conn.execute(
            """UPDATE storage_locations SET name = 'Deckbox: ' || ?, icon = ?
               WHERE location_id = (SELECT location_id FROM decks WHERE deck_id = ?)""",
            (name.strip(), icon_clean, deck_id),
        )
    else:
        cur = conn.execute(
            "UPDATE decks SET name = ? WHERE deck_id = ? AND household_id = ?",
            (name.strip(), deck_id, household_id),
        )
        # Deck-Box heisst mit um
        conn.execute(
            """UPDATE storage_locations SET name = 'Deckbox: ' || ?
               WHERE location_id = (SELECT location_id FROM decks WHERE deck_id = ?)""",
            (name.strip(), deck_id),
        )
    conn.commit()
    return cur.rowcount > 0


def add_card_to_deck_list(conn: sqlite3.Connection, deck_id: int, card_id: str,
                          menge: int = 1, zone: str = "maindeck") -> bool:
    """Deckliste (Bauplan) erweitern — berührt KEINE physischen Kopien.

    SWU-Regeln (Fifth-Trooper/Regelwerk): max 3 Kopien derselben Karte
    global ueber Maindeck+Sideboard kombiniert; Sideboard max 10 Karten.
    """
    if zone not in ("maindeck", "sideboard"):
        zone = "maindeck"
    # Global-Limit: bestehende Menge in BEIDEN Zonen + neue <= 3
    row = conn.execute(
        "SELECT COALESCE(SUM(menge), 0) AS m FROM deck_cards WHERE deck_id = ? AND card_id = ?",
        (deck_id, card_id),
    ).fetchone()
    if int(row["m"]) + menge > 3:
        raise ValueError(f"Maximal 3 Kopien von dieser Karte erlaubt (global Main+Sideboard)")
    if zone == "sideboard":
        sb = conn.execute(
            "SELECT COALESCE(SUM(menge), 0) AS m FROM deck_cards WHERE deck_id = ? AND zone = 'sideboard'",
            (deck_id,),
        ).fetchone()
        if int(sb["m"]) + menge > 10:
            raise ValueError("Sideboard darf maximal 10 Karten enthalten")
    cur = conn.execute(
        """INSERT INTO deck_cards (deck_id, card_id, menge, zone)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(deck_id, card_id, zone)
           DO UPDATE SET menge = menge + excluded.menge""",
        (deck_id, card_id, menge, zone),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_card_from_deck_list(conn: sqlite3.Connection, deck_id: int, card_id: str,
                               zone: str = "maindeck") -> bool:
    cur = conn.execute(
        "DELETE FROM deck_cards WHERE deck_id = ? AND card_id = ? AND zone = ?",
        (deck_id, card_id, zone),
    )
    conn.commit()
    return cur.rowcount > 0


def get_deck_list(conn: sqlite3.Connection, deck_id: int) -> list[dict]:
    """Deckliste mit habe/verfügbar/fehlt (osu!-Muster: Liste + Missing-Anhang)."""
    cur = conn.execute(
        """SELECT dc.*, cards.name, cards.name_de, cards.set_id, cards.card_number,
                  cards.rarity, cards.front_art_path,
                  (SELECT COALESCE(SUM(count), 0) FROM collection
                    WHERE card_id = dc.card_id) AS owned_total
           FROM deck_cards dc
           JOIN cards ON cards.card_id = dc.card_id
           WHERE dc.deck_id = ?
           ORDER BY dc.zone, cards.set_id, cards.card_number""",
        (deck_id,),
    )
    items = []
    for r in cur.fetchall():
        it = dict(r)
        # Physisch in DIESEM Deck (Deck-Box) via location der Bestandszeilen
        deck_loc = conn.execute(
            "SELECT location_id FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        in_box = conn.execute(
            """SELECT COALESCE(SUM(count), 0) FROM collection
               WHERE card_id = ? AND location_id = ?""",
            (it["card_id"], deck_loc["location_id"] if deck_loc else -1),
        ).fetchone()[0]
        it["in_deck_box"] = int(in_box)
        it["fehlt"] = max(0, it["menge"] - it["in_deck_box"])
        items.append(it)
    return items


def card_in_decks(conn: sqlite3.Connection, card_id: str, household_id: int = 1) -> list[dict]:
    """In welchen Decks steckt die Karte (physisch, via Deck-Boxen)?"""
    cur = conn.execute(
        """SELECT d.deck_id, d.name, SUM(c.count) AS menge_dort
           FROM collection c
           JOIN decks d ON c.location_id = d.location_id
           WHERE c.card_id = ? AND d.household_id = ?
           GROUP BY d.deck_id""",
        (card_id, household_id),
    )
    return [dict(r) for r in cur.fetchall()]


def available_count(conn: sqlite3.Connection, card_id: str, household_id: int = 1) -> dict:
    """Silvane-Mathe: verfügbar = besitzt − in Deck-Boxen (Moxfield-Wunsch #1027)."""
    owned = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM collection WHERE card_id = ? AND household_id = ?",
        (card_id, household_id),
    ).fetchone()[0]
    in_decks = sum(d["menge_dort"] for d in card_in_decks(conn, card_id, household_id))
    return {"owned": int(owned), "in_decks": int(in_decks), "available": int(owned) - int(in_decks)}


def assign_card_to_deck(conn: sqlite3.Connection, card_id: str, deck_id: int,
                        menge: int = 1, household_id: int = 1,
                        client_event_id: str | None = None) -> dict:
    """Karte per Scan/Auswahl ins Deck: Kopie an die Deck-Box umbuchen.

    Deckliste wird ergänzt, falls die Karte noch nicht drin ist (Bauplan
    folgt dem physischen Bau). Rückgabe für Undo/Toast.
    """
    deck = get_deck(conn, deck_id, household_id)
    if not deck or not deck.get("location_id"):
        return {"ok": False, "error": "Deck nicht gefunden"}
    # Freie Kopien zuerst: Zeilen, die NICHT in einer Deck-Box liegen
    rows = conn.execute(
        """SELECT c.* FROM collection c
           LEFT JOIN decks d ON c.location_id = d.location_id
           WHERE c.card_id = ? AND c.household_id = ? AND d.deck_id IS NULL
           ORDER BY c.count DESC""",
        (card_id, household_id),
    ).fetchall()
    frei = sum(r["count"] for r in rows)
    if frei < menge:
        return {"ok": False, "error": "keine_freien",
                "verfuegbar": frei, "benoetigt": menge}
    # Umbuchen (mit Split, wenn Zeile mehr hat als gebraucht)
    rest = menge
    moved_ids = []
    for r in rows:
        if rest <= 0:
            break
        if r["count"] <= rest:
            conn.execute(
                "UPDATE collection SET location_id = ? WHERE collection_id = ?",
                (deck["location_id"], r["collection_id"]),
            )
            conn.execute(
                """INSERT INTO card_moves (household_id, collection_id, from_location_id,
                                            to_location_id, reason, client_event_id)
                   VALUES (?, ?, ?, ?, 'deck_build', ?)""",
                (household_id, r["collection_id"], r["location_id"], deck["location_id"],
                 client_event_id or str(uuid.uuid4())),
            )
            moved_ids.append((r["collection_id"], r["count"]))
            rest -= r["count"]
        else:
            new_id = split_collection_entry(
                conn, r["collection_id"], rest, deck["location_id"],
                reason="deck_build", household_id=household_id)
            if new_id:
                moved_ids.append((new_id, rest))
            rest = 0
    # Deckliste ergänzen (Bauplan folgt dem physischen Bau)
    row = conn.execute(
        "SELECT menge FROM deck_cards WHERE deck_id = ? AND card_id = ? AND zone = 'maindeck'",
        (deck_id, card_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE deck_cards SET menge = menge + ? WHERE deck_id = ? AND card_id = ? AND zone = 'maindeck'",
            (menge, deck_id, card_id),
        )
    else:
        conn.execute(
            "INSERT INTO deck_cards (deck_id, card_id, menge, zone) VALUES (?, ?, ?, 'maindeck')",
            (deck_id, card_id, menge),
        )
    conn.commit()
    return {"ok": True, "deck": deck["name"], "moved": menge}


def resolve_deck(conn: sqlite3.Connection, deck_id: int, household_id: int = 1) -> dict:
    """Deck auflösen: Karten zurück an ihren Ursprungs-Ort (Sortly 'Return to Origin').

    Ziel = from_location_id des jüngsten deck_build-Moves pro Karte;
    ohne Origin -> Standard-Ort. Deckliste bleibt als Bauplan erhalten?
    Nein — Nutzer-Wunsch: Deck wird aufgelöst; wir behalten die Liste bis
    'löschen' separat aufgerufen wird (zwei getrennte Operationen).
    """
    deck = get_deck(conn, deck_id, household_id)
    if not deck:
        return {"ok": False, "error": "Deck nicht gefunden"}
    default_loc = get_default_location(conn, household_id)
    default_id = default_loc["location_id"] if default_loc else None
    rows = conn.execute(
        "SELECT collection_id, count FROM collection WHERE location_id = ?",
        (deck["location_id"],),
    ).fetchall()
    zurueck = {}
    for r in rows:
        cid = r["collection_id"]
        menge = int(r["count"])
        # Juengster Move dieser Zeile: Origin suchen
        origin = conn.execute(
            """SELECT from_location_id FROM card_moves
               WHERE collection_id = ? AND reason = 'deck_build'
               ORDER BY move_id DESC LIMIT 1""",
            (cid,),
        ).fetchone()
        target = origin["from_location_id"] if origin and origin["from_location_id"] else default_id
        conn.execute(
            "UPDATE collection SET location_id = ? WHERE collection_id = ?",
            (target, cid),
        )
        conn.execute(
            """INSERT INTO card_moves (household_id, collection_id, from_location_id,
                                        to_location_id, reason)
               VALUES (?, ?, ?, ?, 'deck_resolve')""",
            (household_id, cid, deck["location_id"], target),
        )
        zurueck[target] = zurueck.get(target, 0) + menge
    conn.commit()
    # Zusammenfassung fuer den Bestaetigungs-Toast
    namen = {}
    for loc_id in zurueck:
        n = conn.execute(
            "SELECT name FROM storage_locations WHERE location_id = ?", (loc_id,)
        ).fetchone()
        namen[loc_id] = n["name"] if n else "?"
    summary = [{"ort": namen[k], "karten": v} for k, v in zurueck.items()]
    return {"ok": True, "karten": sum(zurueck.values()), "verteilt": summary}


def delete_deck(conn: sqlite3.Connection, deck_id: int, household_id: int = 1) -> tuple[bool, str | dict]:
    """Deck löschen — NUR die Liste. Blockiert, wenn Karten in der Deck-Box liegen
    (Spotify-Lektion: löschen != auflösen sind getrennte Operationen)."""
    deck = get_deck(conn, deck_id, household_id)
    if not deck:
        return False, "Deck nicht gefunden"
    count = location_card_count(conn, deck["location_id"])
    if count > 0:
        return False, {"karten_in_box": count,
                       "hinweis": f"{count} Karten liegen noch in der Deck-Box — erst auflösen"}
    conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
    conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
    conn.execute(
        "UPDATE storage_locations SET is_active = 0 WHERE location_id = ?",
        (deck["location_id"],),
    )
    conn.commit()
    return True, "gelöscht"
