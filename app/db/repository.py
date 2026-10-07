"""
Repository layer — CRUD operations for cards, sets, collection, scans.
All operations are transactional and use parameterised queries.
"""
from __future__ import annotations

import json
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
) -> int:
    """Add a card to the collection. If an identical entry exists, increment count."""
    cur = conn.execute(
        """SELECT collection_id, count FROM collection
           WHERE card_id=? AND condition=? AND language=? AND variant=?""",
        (card_id, condition, language, variant),
    )
    row = cur.fetchone()
    if row:
        new_count = row["count"] + count
        conn.execute(
            "UPDATE collection SET count=?, acquired_at=? WHERE collection_id=?",
            (new_count, datetime.now().isoformat(timespec="seconds"), row["collection_id"]),
        )
        conn.commit()
        return row["collection_id"]
    cur = conn.execute(
        """INSERT INTO collection (card_id, count, condition, language, variant, location, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (card_id, count, condition, language, variant, location, source),
    )
    conn.commit()
    return cur.lastrowid


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
