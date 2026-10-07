"""
Repository layer — CRUD operations for cards, sets, collection, scans.
All operations are transactional and use parameterised queries.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.schema import connect

log = get_logger("repository")


# ---------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------

def upsert_set(conn: sqlite3.Connection, set_data: dict[str, Any]) -> None:
    """Insert or update a set. Idempotent."""
    conn.execute(
        """
        INSERT INTO sets (set_id, full_name, parent_set_id, number_cards, max_element,
                          is_base_set, release_date)
        VALUES (:set_id, :full_name, :parent_set_id, :number_cards, :max_element,
                :is_base_set, :release_date)
        ON CONFLICT(set_id) DO UPDATE SET
            full_name=excluded.full_name,
            parent_set_id=excluded.parent_set_id,
            number_cards=excluded.number_cards,
            max_element=excluded.max_element,
            is_base_set=excluded.is_base_set,
            release_date=excluded.release_date,
            updated_at=datetime('now')
        """,
        {
            "set_id": set_data["setId"],
            "full_name": set_data["fullName"],
            "parent_set_id": set_data.get("parentSetId"),
            "number_cards": set_data.get("numberCards", 0),
            "max_element": set_data.get("maxElement", ""),
            "is_base_set": 1 if set_data.get("isBaseSet") else 0,
            "release_date": set_data.get("releaseDate", ""),
        },
    )
    conn.commit()


def get_all_sets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM sets ORDER BY set_id")
    return [dict(r) for r in cur.fetchall()]


def get_set(conn: sqlite3.Connection, set_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM sets WHERE set_id = ?", (set_id,))
    r = cur.fetchone()
    return dict(r) if r else None


def mark_set_imported(conn: sqlite3.Connection, set_id: str, card_count: int) -> None:
    conn.execute(
        "UPDATE sets SET imported=1, imported_at=?, card_count_actual=?, updated_at=datetime('now') WHERE set_id=?",
        (datetime.now().isoformat(timespec="seconds"), card_count, set_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def upsert_card(conn: sqlite3.Connection, card: dict[str, Any]) -> str:
    """Insert or update a card. Returns the card_id.
    Supports bilingual data: name_de, subtitle_de, type_de, etc."""
    card_id = f"{card['Set']}-{card['Number']}"
    conn.execute(
        """
        INSERT INTO cards (
            card_id, set_id, card_number, name, subtitle, type, rarity,
            aspects, traits, arenas, cost, power, hp,
            front_text, back_text, epic_action, artist,
            unique_card, double_sided, front_art_url, back_art_url,
            tcgplayer_id, cid, market_price, low_price, variant_type,
            name_de, subtitle_de, type_de, rarity_de,
            aspects_de, traits_de, arenas_de,
            front_text_de, back_text_de, epic_action_de
        ) VALUES (
            :card_id, :set_id, :card_number, :name, :subtitle, :type, :rarity,
            :aspects, :traits, :arenas, :cost, :power, :hp,
            :front_text, :back_text, :epic_action, :artist,
            :unique_card, :double_sided, :front_art_url, :back_art_url,
            :tcgplayer_id, :cid, :market_price, :low_price, :variant_type,
            :name_de, :subtitle_de, :type_de, :rarity_de,
            :aspects_de, :traits_de, :arenas_de,
            :front_text_de, :back_text_de, :epic_action_de
        )
        ON CONFLICT(card_id) DO UPDATE SET
            name=excluded.name, subtitle=excluded.subtitle, type=excluded.type,
            rarity=excluded.rarity, aspects=excluded.aspects, traits=excluded.traits,
            arenas=excluded.arenas, cost=excluded.cost, power=excluded.power, hp=excluded.hp,
            front_text=excluded.front_text, back_text=excluded.back_text,
            epic_action=excluded.epic_action, artist=excluded.artist,
            front_art_url=excluded.front_art_url, back_art_url=excluded.back_art_url,
            market_price=excluded.market_price, low_price=excluded.low_price,
            variant_type=excluded.variant_type,
            name_de=COALESCE(excluded.name_de, name_de),
            subtitle_de=COALESCE(excluded.subtitle_de, subtitle_de),
            type_de=COALESCE(excluded.type_de, type_de),
            rarity_de=COALESCE(excluded.rarity_de, rarity_de),
            aspects_de=COALESCE(excluded.aspects_de, aspects_de),
            traits_de=COALESCE(excluded.traits_de, traits_de),
            arenas_de=COALESCE(excluded.arenas_de, arenas_de),
            front_text_de=COALESCE(excluded.front_text_de, front_text_de),
            back_text_de=COALESCE(excluded.back_text_de, back_text_de),
            epic_action_de=COALESCE(excluded.epic_action_de, epic_action_de),
            updated_at=datetime('now')
        """,
        {
            "card_id": card_id,
            "set_id": card["Set"],
            "card_number": card["Number"],
            "name": card.get("Name", ""),
            "subtitle": card.get("Subtitle", ""),
            "type": card.get("Type", ""),
            "rarity": card.get("Rarity", ""),
            "aspects": json.dumps(card.get("Aspects", [])),
            "traits": json.dumps(card.get("Traits", [])),
            "arenas": json.dumps(card.get("Arenas", [])),
            "cost": card.get("Cost", ""),
            "power": card.get("Power", ""),
            "hp": card.get("HP", ""),
            "front_text": card.get("FrontText", ""),
            "back_text": card.get("BackText", ""),
            "epic_action": card.get("EpicAction", ""),
            "artist": card.get("Artist", ""),
            "unique_card": 1 if card.get("Unique") else 0,
            "double_sided": 1 if card.get("DoubleSided") else 0,
            "front_art_url": card.get("FrontArt", ""),
            "back_art_url": card.get("BackArt", ""),
            "tcgplayer_id": card.get("tcgplayerId", ""),
            "cid": card.get("cid", ""),
            "market_price": card.get("MarketPrice", ""),
            "low_price": card.get("LowPrice", ""),
            "variant_type": card.get("VariantType", "Normal"),
            "name_de": card.get("name_de", ""),
            "subtitle_de": card.get("subtitle_de", ""),
            "type_de": card.get("type_de", ""),
            "rarity_de": card.get("rarity_de", ""),
            "aspects_de": json.dumps(card.get("aspects_de", [])),
            "traits_de": json.dumps(card.get("traits_de", [])),
            "arenas_de": json.dumps(card.get("arenas_de", [])),
            "front_text_de": card.get("front_text_de", ""),
            "back_text_de": card.get("back_text_de", ""),
            "epic_action_de": card.get("epic_action_de", ""),
        },
    )
    conn.commit()
    return card_id


def get_card(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,))
    r = cur.fetchone()
    return dict(r) if r else None


def get_cards_by_set(conn: sqlite3.Connection, set_id: str) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM cards WHERE set_id = ? ORDER BY card_number", (set_id,))
    return [dict(r) for r in cur.fetchall()]


def get_all_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM cards ORDER BY set_id, card_number")
    return [dict(r) for r in cur.fetchall()]


def get_card_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM cards")
    return cur.fetchone()[0]


def update_card_phash(conn: sqlite3.Connection, card_id: str, front_phash: str, back_phash: str | None = None) -> None:
    conn.execute(
        "UPDATE cards SET front_phash=?, back_phash=?, updated_at=datetime('now') WHERE card_id=?",
        (front_phash, back_phash, card_id),
    )
    conn.commit()


def update_card_image_path(conn: sqlite3.Connection, card_id: str, front: str | None = None, back: str | None = None) -> None:
    if front:
        conn.execute("UPDATE cards SET front_art_path=?, updated_at=datetime('now') WHERE card_id=?", (front, card_id))
    if back:
        conn.execute("UPDATE cards SET back_art_path=?, updated_at=datetime('now') WHERE card_id=?", (back, card_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def add_to_collection(
    conn: sqlite3.Connection,
    card_id: str,
    count: int = 1,
    condition: str = "NM",
    language: str = "en",
    variant: str = "Normal",
    location: str | None = None,
    source: str = "scan",
    return_details: bool = False,
) -> int | dict[str, Any]:
    """Add a card to the collection. If an identical entry exists, increment count.

    With return_details=True, returns a dict with undo information:
    {collection_id, previous_count, entry_created}.
    """
    cur = conn.execute(
        """SELECT collection_id, count FROM collection
           WHERE card_id=? AND condition=? AND language=? AND variant=?""",
        (card_id, condition, language, variant),
    )
    row = cur.fetchone()
    if row:
        previous_count = row["count"]
        new_count = previous_count + count
        conn.execute(
            "UPDATE collection SET count=?, acquired_at=? WHERE collection_id=?",
            (new_count, datetime.now().isoformat(timespec="seconds"), row["collection_id"]),
        )
        conn.commit()
        if return_details:
            return {
                "collection_id": row["collection_id"],
                "previous_count": previous_count,
                "entry_created": 0,
            }
        return row["collection_id"]
    cur = conn.execute(
        """INSERT INTO collection (card_id, count, condition, language, variant, location, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (card_id, count, condition, language, variant, location, source),
    )
    conn.commit()
    if return_details:
        return {
            "collection_id": cur.lastrowid,
            "previous_count": 0,
            "entry_created": 1,
        }
    return cur.lastrowid


def record_scan_undo(
    conn: sqlite3.Connection,
    scan_id: int,
    collection_id: int,
    card_id: str,
    count_added: int,
    previous_count: int,
    entry_created: bool,
    is_foil: bool,
    photo_path: str | None,
    correction_id: int | None,
    dataset_path: str | None,
) -> int:
    """Journal a scan-confirm action so it can be undone."""
    cur = conn.execute(
        """INSERT INTO scan_undo (scan_id, collection_id, card_id, count_added,
               previous_count, entry_created, is_foil, photo_path, correction_id, dataset_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (scan_id, collection_id, card_id, count_added, previous_count,
         1 if entry_created else 0, 1 if is_foil else 0,
         photo_path, correction_id, dataset_path),
    )
    conn.commit()
    return cur.lastrowid


def get_last_undoable(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Most recent confirm action that has not been undone yet."""
    cur = conn.execute(
        "SELECT * FROM scan_undo WHERE undone = 0 ORDER BY undo_id DESC LIMIT 1"
    )
    row = cur.fetchone()
    return dict(row) if row else None


def undo_last_scan_action(conn: sqlite3.Connection) -> dict[str, Any]:
    """Undo the most recent not-yet-undone confirm action.

    - restores the collection count (or removes the entry if it was newly created)
    - deletes the training photo taken for this scan
    - removes the correction learning record (scan_corrections row + dataset sample)
    Returns a result dict describing what was undone.
    """
    row = get_last_undoable(conn)
    if row is None:
        return {"undone": False, "error": "Keine rückgängig zu machende Aktion"}

    undo_id = row["undo_id"]
    collection_id = row["collection_id"]
    details: list[str] = []

    # 1) Collection rollback
    cur = conn.execute("SELECT 1 FROM collection WHERE collection_id=?", (collection_id,))
    if cur.fetchone():
        if row["entry_created"]:
            conn.execute("DELETE FROM collection WHERE collection_id=?", (collection_id,))
            details.append("Sammlungseintrag entfernt")
        else:
            conn.execute(
                "UPDATE collection SET count=? WHERE collection_id=?",
                (row["previous_count"], collection_id),
            )
            details.append(f"Anzahl zurück auf {row['previous_count']} gesetzt")

    # 2) Delete the training photo (data quality!)
    if row["photo_path"]:
        try:
            Path(row["photo_path"]).unlink(missing_ok=True)
            details.append("Trainingsfoto gelöscht")
        except Exception as e:
            log.warning(f"Could not delete training photo {row['photo_path']}: {e}")

    # 3) Remove the correction learning record
    if row["correction_id"]:
        conn.execute("DELETE FROM scan_corrections WHERE correction_id=?", (row["correction_id"],))
        details.append("Korrektur-Lerneintrag entfernt")

    # 4) Delete dataset sample
    if row["dataset_path"]:
        try:
            Path(row["dataset_path"]).unlink(missing_ok=True)
            details.append("Dataset-Sample gelöscht")
        except Exception as e:
            log.warning(f"Could not delete dataset sample {row['dataset_path']}: {e}")

    # 5) Mark the scan as undone
    conn.execute("UPDATE scans SET error_status='undone' WHERE scan_id=?", (row["scan_id"],))
    conn.execute(
        "UPDATE scan_undo SET undone=1, undone_at=datetime('now') WHERE undo_id=?",
        (undo_id,),
    )
    conn.commit()
    return {
        "undone": True,
        "card_id": row["card_id"],
        "details": details,
        "undone_at": row["created_at"],
    }


def get_collection(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT c.*, cards.name, cards.subtitle, cards.set_id, cards.card_number,
                  cards.rarity, cards.type, cards.front_art_path
           FROM collection c JOIN cards ON c.card_id = cards.card_id
           ORDER BY cards.name"""
    )
    return [dict(r) for r in cur.fetchall()]


def get_collection_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COALESCE(SUM(count), 0) FROM collection")
    return cur.fetchone()[0]


def get_unique_collection_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM collection")
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------

def record_scan(
    conn: sqlite3.Connection,
    image_path: str | None,
    recognized_card_id: str | None,
    confidence: float,
    method: str,
    ocr_text: str = "",
    error_status: str = "",
    processing_time_ms: int = 0,
) -> int:
    cur = conn.execute(
        """INSERT INTO scans (image_path, recognized_card_id, confidence, method,
                              ocr_text, error_status, processing_time_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (image_path, recognized_card_id, confidence, method, ocr_text, error_status, processing_time_ms),
    )
    conn.commit()
    return cur.lastrowid


def record_scan_candidates(conn: sqlite3.Connection, scan_id: int, candidates: list[dict[str, Any]]) -> None:
    for c in candidates:
        conn.execute(
            """INSERT INTO scan_candidates (scan_id, card_id, score, method, details)
               VALUES (?, ?, ?, ?, ?)""",
            (scan_id, c["card_id"], c.get("score", 0), c.get("method", ""), json.dumps(c.get("details", {}))),
        )
    conn.commit()


def correct_scan(conn: sqlite3.Connection, scan_id: int, corrected_card_id: str) -> None:
    conn.execute(
        "UPDATE scans SET manual_correction=1, corrected_card_id=? WHERE scan_id=?",
        (corrected_card_id, scan_id),
    )
    conn.commit()


def record_scan_correction(
    conn: sqlite3.Connection,
    scan_id: int,
    original_card_id: str,
    corrected_card_id: str,
    image_path: str | None = None,
) -> int:
    """Record a scan correction for learning. Returns the correction_id."""
    cur = conn.execute(
        """INSERT INTO scan_corrections (scan_id, original_card_id, corrected_card_id, image_path)
           VALUES (?, ?, ?, ?)""",
        (scan_id, original_card_id, corrected_card_id, image_path),
    )
    conn.commit()
    return cur.lastrowid


def get_scan_corrections(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Get recent scan corrections for training."""
    cur = conn.execute(
        """SELECT sc.*, c.name as corrected_name, c.name_de as corrected_name_de
           FROM scan_corrections sc
           LEFT JOIN cards c ON sc.corrected_card_id = c.card_id
           ORDER BY sc.timestamp DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_correction_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM scan_corrections")
    return cur.fetchone()[0]


def get_recent_scans(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT s.*, cards.name, cards.subtitle FROM scans s
           LEFT JOIN cards ON s.recognized_card_id = cards.card_id
           ORDER BY s.timestamp DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_scan_count_today(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM scans WHERE date(timestamp) = date('now')"
    )
    return cur.fetchone()[0]


def get_recognition_rate(conn: sqlite3.Connection) -> float:
    """Fraction of scans with recognized_card_id not null."""
    cur = conn.execute("SELECT COUNT(*) FROM scans WHERE recognized_card_id IS NOT NULL")
    recognized = cur.fetchone()[0]
    total_cur = conn.execute("SELECT COUNT(*) FROM scans")
    total = total_cur.fetchone()[0]
    return recognized / total if total > 0 else 0.0


# --- Cardmarket prices -------------------------------------------------------


def upsert_price(conn: sqlite3.Connection, price: dict[str, Any]) -> None:
    """Insert or update one price row (keyed by idProduct)."""
    conn.execute(
        """INSERT INTO card_prices (idProduct, trend, low, low_ex, avg_sell,
               foil_trend, foil_low, foil_sell, avg30, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(idProduct) DO UPDATE SET
               trend=excluded.trend, low=excluded.low, low_ex=excluded.low_ex,
               avg_sell=excluded.avg_sell, foil_trend=excluded.foil_trend,
               foil_low=excluded.foil_low, foil_sell=excluded.foil_sell,
               avg30=excluded.avg30, updated_at=datetime('now')""",
        (
            price["idProduct"], price.get("trend"), price.get("low"),
            price.get("low_ex"), price.get("avg_sell"),
            price.get("foil_trend"), price.get("foil_low"),
            price.get("foil_sell"), price.get("avg30"),
        ),
    )


def get_price_for_card(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
    """Get MKM price for a card via the card_mkm_map mapping."""
    cur = conn.execute(
        """SELECT p.* FROM card_prices p
           JOIN card_mkm_map m ON p.idProduct = m.idProduct
           WHERE m.card_id = ?""",
        (card_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_card_mkm_map(conn: sqlite3.Connection, card_id: str, idProduct: int) -> None:
    """Map a card_id to an MKM product id.

    Multiple card_ids can point to the same product (SOR-010 and SOR-10 are
    the same physical card; both are imported into our cards table).
    """
    conn.execute(
        """INSERT INTO card_mkm_map (card_id, idProduct) VALUES (?, ?)
           ON CONFLICT(card_id) DO UPDATE SET idProduct=excluded.idProduct""",
        (card_id, idProduct),
    )


def upsert_mkm_product(conn: sqlite3.Connection, product: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO mkm_products (idProduct, name, number, rarity, expansion,
               website, idMetaproduct, card_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(idProduct) DO UPDATE SET
               name=excluded.name, number=excluded.number, rarity=excluded.rarity,
               expansion=excluded.expansion, website=excluded.website,
               idMetaproduct=excluded.idMetaproduct, card_id=excluded.card_id""",
        (
            product["idProduct"], product.get("name"), product.get("number"),
            product.get("rarity"), product.get("expansion"),
            product.get("website"), product.get("idMetaproduct"),
            product.get("card_id"),
        ),
    )


def get_price_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Import status + freshness of the price table."""
    cur = conn.execute("SELECT COUNT(*) FROM card_prices")
    price_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM card_mkm_map")
    mapped = cur.fetchone()[0]
    cur = conn.execute("SELECT MAX(updated_at) FROM card_prices")
    last_update = cur.fetchone()[0]
    return {
        "price_rows": price_count,
        "mapped_cards": mapped,
        "last_update": last_update,
    }


def get_collection_value(conn: sqlite3.Connection) -> dict[str, Any]:
    """Total market value of the collection (foil-aware).

    Value model:
    - non-foil copy  -> TREND
    - foil copy      -> FOIL_TREND (fallback TREND)
    Cards without MKM price are counted as unpriced.
    """
    cur = conn.execute(
        """SELECT
               SUM(CASE WHEN c.is_foil = 1 AND pr.foil_trend IS NOT NULL
                        THEN pr.foil_trend * c.count
                        WHEN pr.trend IS NOT NULL THEN pr.trend * c.count
                        ELSE 0 END) AS total_value,
               SUM(CASE WHEN (c.is_foil = 1 AND pr.foil_trend IS NOT NULL)
                             OR (c.is_foil = 0 AND pr.trend IS NOT NULL)
                        THEN c.count ELSE 0 END) AS priced_count,
               COUNT(c.collection_id) AS entry_count,
               SUM(c.count) AS total_count
           FROM collection c
           LEFT JOIN card_mkm_map m ON c.card_id = m.card_id
           LEFT JOIN card_prices pr ON m.idProduct = pr.idProduct""",
    )
    row = cur.fetchone()
    if not row or row["total_count"] is None:
        return {
            "total_value": 0.0,
            "priced_count": 0,
            "total_count": 0,
            "entry_count": 0,
            "currency": "EUR",
        }
    return {
        "total_value": round(row["total_value"] or 0.0, 2),
        "priced_count": row["priced_count"] or 0,
        "total_count": row["total_count"],
        "entry_count": row["entry_count"] or 0,
        "currency": "EUR",
    }


# --- Wishlist -----------------------------------------------------------------


def add_to_wishlist(
    conn: sqlite3.Connection,
    card_id: str,
    count: int = 1,
    target_price: float | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO wishlist (card_id, count, target_price, notes)
           VALUES (?, ?, ?, ?)""",
        (card_id, count, target_price, notes),
    )
    return cur.lastrowid


def remove_from_wishlist(conn: sqlite3.Connection, wishlist_id: int) -> bool:
    cur = conn.execute("DELETE FROM wishlist WHERE wishlist_id = ?", (wishlist_id,))
    return cur.rowcount > 0


def get_wishlist(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT w.*, cards.name, cards.subtitle, cards.set_id, cards.card_number,
                  cards.rarity, cards.front_art_path,
                  m.idProduct, p.trend AS price_trend
           FROM wishlist w
           JOIN cards ON w.card_id = cards.card_id
           LEFT JOIN card_mkm_map m ON m.card_id = w.card_id
           LEFT JOIN card_prices p ON p.idProduct = m.idProduct
           ORDER BY w.created_at DESC"""
    )
    return [dict(r) for r in cur.fetchall()]


# --- Set completion -------------------------------------------------------------


def get_set_completion(conn: sqlite3.Connection, set_id: str) -> dict[str, Any]:
    """Completion stats for one set: owned/total, missing cards list.

    'Unique cards' ignores variants (foil/hyperspace duplicates share the
    same base number); we count distinct normalised card numbers.
    """
    # All cards of the set: normalise number (strip leading zeros / F suffix)
    cur = conn.execute(
        "SELECT card_id, card_number, name, name_de, rarity, front_art_path FROM cards WHERE set_id = ?",
        (set_id,),
    )
    all_cards = [dict(r) for r in cur.fetchall()]

    # Owned distinct numbers
    cur = conn.execute(
        "SELECT DISTINCT card_number FROM collection c JOIN cards ON c.card_id = cards.card_id WHERE cards.set_id = ?",
        (set_id,),
    )
    owned_numbers = {r["card_number"] for r in cur.fetchall()}

    # Index by normalised number (first non-foil, non-variant card wins)
    by_number: dict[str, dict[str, Any]] = {}
    for card in all_cards:
        num = card["card_number"]
        try:
            num_norm = str(int(num.rstrip("F").lstrip("0") or "0"))
        except ValueError:
            num_norm = num
        if num_norm not in by_number or (card["card_id"].endswith("F") is False and by_number[num_norm]["card_id"].endswith("F")):
            by_number[num_norm] = card

    total_unique = len(by_number)
    owned_unique = sum(1 for n, c in by_number.items() if c["card_number"] in owned_numbers)
    missing = [
        c for n, c in by_number.items()
        if c["card_number"] not in owned_numbers
    ]
    # sort by number
    def _num_key(c: dict[str, Any]) -> tuple[int, str]:
        try:
            return (int(c["card_number"].rstrip("F").lstrip("0") or "0"), c["card_number"])
        except ValueError:
            return (999999, c["card_number"])
    missing.sort(key=_num_key)

    return {
        "set_id": set_id,
        "total_unique": total_unique,
        "owned_unique": owned_unique,
        "percent": round(100.0 * owned_unique / total_unique, 1) if total_unique else 0.0,
        "missing": missing[:200],
        "missing_count": len(missing),
    }


def get_all_set_completion(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Completion summary for every set (without the missing list)."""
    cur = conn.execute("SELECT set_id FROM sets ORDER BY set_id")
    results = []
    for r in cur.fetchall():
        set_id = r["set_id"]
        stats = get_set_completion(conn, set_id)
        stats.pop("missing", None)
        results.append(stats)
    return results
