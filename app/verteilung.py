"""Verteilungsregeln: Kopien auf Ablageorte verteilen (ERP-Slotting-Muster).

Regelwerk (priorisierte Kette, first-match je Kopie):
  Rang 1: Kopie 1   -> Ordner (Display)
  Rang 2: Kopie 2-4 -> Playsets
  Rang 3: Kopie 5+  -> Bulk (Overflow, bis_kopie NULL)

Jede KOPIE (1., 2., 3. …) durchlaeuft die Regeln in Rang-Reihenfolge;
die erste passende Regel gewinnt (Firewall/Paket-Filter first-match).
Fehlt eine Regel (Luecke) oder ist keine aktiv, faellt die Kopie auf den
Standard-Ort zurueck (nie NULL - Karton geht nie verloren).
"""
from __future__ import annotations

import sqlite3

from app.storage import get_default_location


# ---------------------------------------------------------------------------
# Regel-CRUD
# ---------------------------------------------------------------------------

def list_regeln(conn: sqlite3.Connection, household_id: int) -> list[dict]:
    cur = conn.execute(
        """SELECT r.*, sl.name AS ziel_name, sl.icon AS ziel_icon, sl.typ AS ziel_typ
           FROM verteilungs_regeln r
           LEFT JOIN storage_locations sl ON sl.location_id = r.ziel_location_id
           WHERE r.household_id = ? AND r.aktiv = 1
           ORDER BY r.rang, r.von_kopie""",
        (household_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def validate_regel_set(regeln: list[dict]) -> str | None:
    """Prueft ein Regelsortiment: Rueckgabe = Fehlermeldung oder None.

    - von_kopie >= 1
    - bis_kopie NULL oder >= von_kopie
    - genau EINE Rest-Regel (bis NULL) erlaubt, sie muss den hoechsten
      von_kopie-Wert haben (sonst wuerden spaetere Regeln nie greifen)
    - keine identischen (von, bis)-Bereiche
    """
    if not regeln:
        return None
    rest = [r for r in regeln if r.get("bis_kopie") is None]
    if len(rest) > 1:
        return "Nur eine Regel darf 'bis unbeschränkt' (Rest) sein"
    for r in regeln:
        von, bis = r.get("von_kopie"), r.get("bis_kopie")
        if not von or von < 1:
            return "Start-Kopie muss >= 1 sein"
        if bis is not None and bis < von:
            return "Ende-Kopie muss >= Start-Kopie sein"
    bereiche = [(r["von_kopie"], r["bis_kopie"]) for r in regeln]
    if len(bereiche) != len(set(bereiche)):
        return "Doppelte Bereichs-Regel"
    if rest:
        r = rest[0]
        if any(x["von_kopie"] > r["von_kopie"] for x in regeln if x is not r):
            return "Die Rest-Regel muss alle späteren Kopien abdecken (höchster Start)"
    return None


def save_regeln(conn: sqlite3.Connection, household_id: int,
                regeln: list[dict]) -> None:
    """Ersetzt das Regelsortiment des Haushalts transaktional."""
    fehler = validate_regel_set(regeln)
    if fehler:
        raise ValueError(fehler)
    cur = conn.execute(
        "SELECT COUNT(*) FROM verteilungs_regeln WHERE household_id = ?", (household_id,)
    )
    if cur.fetchone()[0] == 0 and not regeln:
        return
    conn.execute("DELETE FROM verteilungs_regeln WHERE household_id = ?", (household_id,))
    for i, r in enumerate(regeln, start=1):
        conn.execute(
            """INSERT INTO verteilungs_regeln
               (household_id, rang, von_kopie, bis_kopie, ziel_location_id, bedingung, aktiv)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (household_id, i, r["von_kopie"], r.get("bis_kopie"),
             r["ziel_location_id"], r.get("bedingung")),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Regel-Engine (reine Funktion -> deterministisch testbar)
# ---------------------------------------------------------------------------

def verteile(menge: int, regeln: list[dict]) -> list[tuple[int, int]]:
    """Verteilt 'menge' Kopien nach Regelwerk.

    Returns [(ziel_location_id, anzahl)] in Regel-Reihenfolge.
    Fehlende Ziel-Orte oder Regelluecken -> Standard-Ort-Handling im Caller.
    """
    if menge <= 0:
        return []
    out: list[tuple[int, int]] = []
    rest = menge
    for r in sorted(regeln, key=lambda x: x.get("rang", 999)):
        if rest <= 0:
            break
        von = max(1, r.get("von_kopie", 1))
        bis = r.get("bis_kopie")
        # Kapazitaet dieser Regel im Kontext der Gesamtmenge:
        # Kopie 1..1 -> 1 Platz; 2..4 -> 3 Plaetze; 5..None -> alle restlichen
        platz = (bis - von + 1) if bis is not None else rest
        # Wie viele Kopien liegen schon vor dieser Regel (kumulative Plaetze)?
        vorher = sum(
            ((x.get("bis_kopie") or von + rest) - x.get("von_kopie", 1) + 1)
            for x in regeln if x.get("rang", 999) < r.get("rang", 999)
        )
        # Kopien, die in diese Regel fallen: von..min(bis, menge) minus bereits verbrauchte
        start = max(von, vorher + 1)
        if start > menge:
            continue
        # anzahl = Plaetze in [start .. min(bis or menge, menge)]
        ende = bis if bis is not None else menge
        anzahl = max(0, min(ende, menge) - start + 1)
        if anzahl > 0:
            out.append((r["ziel_location_id"], min(anzahl, rest)))
            rest -= min(anzahl, rest)
    return out


def verteile_card(conn: sqlite3.Connection, household_id: int, menge: int) -> list[tuple[int, int]]:
    """Verteilt 'menge' neue Kopien nach dem gespeicherten Regelwerk des Haushalts.

    Zielt ein Regel-Ziel nicht (mehr) auf den Haushalt, faellen diese Kopien
    auf den Standard-Ort zurueck.
    """
    regeln = list_regeln(conn, household_id)
    if not regeln:
        default = get_default_location(conn, household_id)
        return [(default["location_id"], menge)] if default and menge > 0 else []
    valid_locs = {
        r["location_id"] for r in conn.execute(
            "SELECT location_id FROM storage_locations WHERE household_id = ? AND is_active = 1",
            (household_id,),
        ).fetchall()
    }
    result: list[tuple[int, int]] = []
    default = get_default_location(conn, household_id)
    for ziel, anzahl in verteile(menge, regeln):
        if ziel in valid_locs:
            result.append((ziel, anzahl))
        elif default:
            result.append((default["location_id"], anzahl))
    # Fehlmenge (Regelluecke) -> Standard-Ort
    verteilt = sum(a for _, a in result)
    if verteilt < menge and default:
        result.append((default["location_id"], menge - verteilt))
    return result


# ---------------------------------------------------------------------------
# Teilmengen-Split (Bestandszeilen auf Orte aufteilen, Journal-basiert)
# ---------------------------------------------------------------------------

def split_collection_rows(conn: sqlite3.Connection, collection_ids: list[int],
                          abgaben: list[tuple[int, int]],
                          reason: str = "split", household_id: int = 1,
                          user_id: int | None = None) -> int:
    """Bucht Teilmengen mehrerer Bestandszeilen (ein Ablageort, eine Karte)
    auf Ziel-Orte um.

    collection_ids: ALLE Zeilen der Ort-Gruppe (z.B. EN x4 + DE x1 + EN x2).
    abgaben: [(ziel_location_id, anzahl)] — Summe muss <= Summe der Zeilen
    sein. Verbrauch FIFO ueber die Zeilen: eine Abgabe kann Zeile 1 ganz und
    Zeile 2 teilweise verbrauchen. Jede Bewegung erhaelt einen card_moves-
    Journal-Eintrag. Ziel-Zeilen erben die Attribute der Quell-Zeile
    (Sprache, Zustand, Variante, Foil) und werden gemergt, falls identisch
    existierend.
    Returns: Gesamtzahl bewegter Exemplare.
    """
    from datetime import datetime
    import uuid as _uuid

    if not collection_ids or not abgaben:
        raise ValueError("Nichts zu verteilen")
    rows = []
    for cid in collection_ids:
        row = conn.execute(
            "SELECT * FROM collection WHERE collection_id = ? AND household_id = ?",
            (cid, household_id),
        ).fetchone()
        if row:
            rows.append(dict(row))
    if not rows:
        raise ValueError("Bestandszeile nicht gefunden")
    total = sum(r["count"] for r in rows)
    sum_abgaben = sum(a for _, a in abgaben)
    if sum_abgaben <= 0:
        raise ValueError("Nichts zu verteilen")
    if sum_abgaben > total:
        raise ValueError(f"Verteilung ({sum_abgaben}) übersteigt Bestand ({total})")

    quelle_loc = rows[0]["location_id"]
    bewegt = 0
    stapel = [dict(r) for r in rows]

    def _buchung(quell_row, ziel_loc, anzahl):
        ziel = conn.execute(
            """SELECT collection_id, count FROM collection
               WHERE card_id=? AND condition=? AND language=? AND variant=? AND is_foil=?
                 AND location_id=? AND household_id=?""",
            (quell_row["card_id"], quell_row["condition"], quell_row["language"],
             quell_row["variant"], quell_row["is_foil"], ziel_loc, household_id),
        ).fetchone()
        if ziel:
            conn.execute("UPDATE collection SET count = count + ? WHERE collection_id = ?",
                         (anzahl, ziel["collection_id"]))
        else:
            conn.execute(
                """INSERT INTO collection (card_id, count, condition, language, variant,
                                           is_foil, location, source, household_id, user_id, location_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (quell_row["card_id"], anzahl, quell_row["condition"], quell_row["language"],
                 quell_row["variant"], quell_row["is_foil"], quell_row["location"],
                 quell_row["source"], household_id, user_id, ziel_loc),
            )
        conn.execute("UPDATE collection SET count = count - ? WHERE collection_id = ?",
                     (anzahl, quell_row["collection_id"]))
        conn.execute(
            """INSERT INTO card_moves (collection_id, from_location_id, to_location_id,
                                       reason, household_id, client_event_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (quell_row["collection_id"], quelle_loc, ziel_loc, reason,
             household_id, str(_uuid.uuid4())),
        )
        return anzahl

    for ziel_loc, soll in abgaben:
        if soll <= 0 or ziel_loc == quelle_loc:
            continue
        offen = soll
        while offen > 0 and stapel:
            r = stapel[0]
            nehmen = min(offen, r["count"])
            if nehmen > 0:
                bewegt += _buchung(r, ziel_loc, nehmen)
                r["count"] -= nehmen
                offen -= nehmen
            if r["count"] <= 0:
                conn.execute("DELETE FROM collection WHERE collection_id = ? AND count <= 0",
                             (r["collection_id"],))
                stapel.pop(0)
    conn.commit()
    return bewegt


# Abwaertskompatibilitaet: Einzelzeilen-API
def split_collection_row(conn, collection_id, abgaben, reason="split",
                         household_id=1, user_id=None) -> int:
    return split_collection_rows(conn, [collection_id], abgaben, reason=reason,
                                 household_id=household_id, user_id=user_id)

