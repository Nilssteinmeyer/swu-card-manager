"""
Database schema definition and migration system for SWU Card Manager.
Uses SQLite with WAL mode for robustness.

Tables:
  cards           — all known cards from all sets
  sets            — set metadata
  collection      — user's owned cards (card_id + count + condition + language + variant)
  scans           — every scan event, for audit and ML improvement
  scan_candidates — alternative candidates for each scan
  models          — ML model registry
  dataset_versions — dataset version tracking
  job_log         — background job execution log
  schema_migrations — track which migrations have been applied
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("db")

SCHEMA_VERSION = 2


def get_db_path() -> Path:
    cfg = AppConfig.load()
    return cfg.path("database")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with pragmas for robustness."""
    p = db_path or get_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

DDL_STATEMENTS = [
    # --- Sets ----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS sets (
        set_id          TEXT PRIMARY KEY,
        full_name       TEXT NOT NULL,
        parent_set_id   TEXT,
        number_cards    INTEGER DEFAULT 0,
        max_element     TEXT,
        is_base_set     INTEGER DEFAULT 0,
        release_date    TEXT,
        imported        INTEGER DEFAULT 0,
        imported_at     TEXT,
        card_count_actual INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Cards ---------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS cards (
        card_id         TEXT PRIMARY KEY,   -- e.g. "SOR-010"
        set_id          TEXT NOT NULL,
        card_number     TEXT NOT NULL,
        name            TEXT NOT NULL,
        subtitle        TEXT,
        type            TEXT,
        rarity          TEXT,
        aspects         TEXT,   -- JSON array
        traits          TEXT,   -- JSON array
        arenas          TEXT,   -- JSON array
        cost            TEXT,
        power           TEXT,
        hp              TEXT,
        front_text      TEXT,
        back_text        TEXT,
        epic_action     TEXT,
        artist          TEXT,
        language        TEXT DEFAULT 'en',
        variant_type    TEXT DEFAULT 'Normal',
        unique_card     INTEGER DEFAULT 0,
        double_sided    INTEGER DEFAULT 0,
        front_art_url   TEXT,
        back_art_url     TEXT,
        front_art_path   TEXT,
        back_art_path    TEXT,
        tcgplayer_id     TEXT,
        cid             TEXT,
        market_price     TEXT,
        low_price        TEXT,
        front_phash      TEXT,   -- perceptual hash of front art
        back_phash       TEXT,
        front_features   BLOB,   -- serialised OpenCV features (SIFT/ORB)
        imported_at      TEXT DEFAULT (datetime('now')),
        updated_at       TEXT DEFAULT (datetime('now')),
        -- Bilingual support (German translations)
        name_de          TEXT,
        subtitle_de      TEXT,
        type_de          TEXT,
        rarity_de        TEXT,
        aspects_de       TEXT,   -- JSON array
        traits_de        TEXT,   -- JSON array
        arenas_de        TEXT,   -- JSON array
        front_text_de    TEXT,
        back_text_de     TEXT,
        epic_action_de   TEXT,
        FOREIGN KEY (set_id) REFERENCES sets(set_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)",
    "CREATE INDEX IF NOT EXISTS idx_cards_number ON cards(card_number)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_set_number ON cards(set_id, card_number)",
    # --- Collection ----------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS collection (
        collection_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id         TEXT NOT NULL,
        count           INTEGER DEFAULT 1,
        condition       TEXT DEFAULT 'NM',
        language        TEXT DEFAULT 'en',
        variant         TEXT DEFAULT 'Normal',
        location        TEXT,
        acquired_at     TEXT DEFAULT (datetime('now')),
        source          TEXT DEFAULT 'scan',
        notes           TEXT,
        FOREIGN KEY (card_id) REFERENCES cards(card_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_collection_card ON collection(card_id)",
    # --- Scans ---------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS scans (
        scan_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp      TEXT DEFAULT (datetime('now')),
        image_path      TEXT,
        recognized_card_id TEXT,
        confidence      REAL,
        method          TEXT,
        ocr_text        TEXT,
        error_status    TEXT,
        manual_correction INTEGER DEFAULT 0,
        corrected_card_id TEXT,
        processing_time_ms INTEGER,
        FOREIGN KEY (recognized_card_id) REFERENCES cards(card_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_candidates (
        candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id         INTEGER NOT NULL,
        card_id         TEXT NOT NULL,
        score           REAL,
        method          TEXT,
        details         TEXT,   -- JSON
        FOREIGN KEY (scan_id) REFERENCES scans(scan_id),
        FOREIGN KEY (card_id) REFERENCES cards(card_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scans_time ON scans(timestamp)",
    # --- Model registry ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS models (
        model_id        TEXT PRIMARY KEY,
        version         TEXT NOT NULL,
        trained_at      TEXT,
        dataset_version TEXT,
        parameters      TEXT,   -- JSON
        validation_accuracy REAL,
        recognition_rate REAL,
        confusion_matrix TEXT,  -- JSON
        storage_path    TEXT,
        is_active       INTEGER DEFAULT 0,
        status          TEXT DEFAULT 'created',  -- created/trained/validated/active/discarded
        notes           TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Dataset versions ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS dataset_versions (
        version_id      TEXT PRIMARY KEY,
        created_at      TEXT DEFAULT (datetime('now')),
        sample_count    INTEGER DEFAULT 0,
        card_count      INTEGER DEFAULT 0,
        description     TEXT,
        status          TEXT DEFAULT 'building',
        path            TEXT
    )
    """,
    # --- Job log -------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS job_log (
        job_id          TEXT PRIMARY KEY,
        job_type        TEXT NOT NULL,
        started_at      TEXT,
        completed_at    TEXT,
        status          TEXT,
        result          TEXT,
        details         TEXT
    )
    """,
    # --- Settings (user preferences, overridable at runtime) -----------------
    """
    CREATE TABLE IF NOT EXISTS settings (
        key             TEXT PRIMARY KEY,
        value           TEXT,
        updated_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Cardmarket prices (imported from MKM price guide CSV) ---------------
    """
    CREATE TABLE IF NOT EXISTS card_prices (
        idProduct       INTEGER PRIMARY KEY,
        trend           REAL,
        low             REAL,
        low_ex          REAL,
        avg_sell        REAL,
        foil_trend      REAL,
        foil_low        REAL,
        foil_sell       REAL,
        avg30           REAL,
        updated_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mkm_products (
        idProduct       INTEGER PRIMARY KEY,
        name            TEXT,
        number          TEXT,
        rarity          TEXT,
        expansion       TEXT,
        website         TEXT,
        idMetaproduct   INTEGER,
        card_id         TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mkm_products_card ON mkm_products (card_id)
    """,
    # --- Card -> MKM product mapping (multiple card_ids may map to one product,
    #     e.g. SOR-010 from SWU-DB and SOR-10 from the official API are the same card) ---
    """
    CREATE TABLE IF NOT EXISTS card_mkm_map (
        card_id         TEXT PRIMARY KEY,
        idProduct       INTEGER NOT NULL
    )
    """,
    # --- Local wishlist (cards the user wants to acquire) --------------------
    """
    CREATE TABLE IF NOT EXISTS wishlist (
        wishlist_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id         TEXT NOT NULL,
        count           INTEGER DEFAULT 1,
        target_price    REAL,
        notes           TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (card_id) REFERENCES cards(card_id)
    )
    """,
    # --- Schema migrations --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version         INTEGER PRIMARY KEY,
        applied_at      TEXT DEFAULT (datetime('now')),
        description     TEXT
    )
    """,
    # --- Scan corrections (learning from user corrections) -------------------
    """
    CREATE TABLE IF NOT EXISTS scan_corrections (
        correction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id         INTEGER,
        original_card_id TEXT,
        corrected_card_id TEXT,
        image_path      TEXT,
        timestamp       TEXT DEFAULT (datetime('now')),
        used_for_training INTEGER DEFAULT 0,
        FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
    )
    """,
]


def init_database(conn: sqlite3.Connection | None = None) -> bool:
    """Create all tables if they don't exist and record schema version."""
    own_conn = conn is None
    if own_conn:
        conn = connect()
    try:
        cur = conn.cursor()
        for ddl in DDL_STATEMENTS:
            cur.execute(ddl)
        # --- migrations for pre-existing databases --------------------------------
        _migrate(cur)
        # Record schema version
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
            (SCHEMA_VERSION, "Initial schema + phash/features columns"),
        )
        conn.commit()
        log.info("Database initialised", extra={"event": "db_init", "set_id": ""})
        return True
    except Exception as e:
        log.error(f"Database init failed: {e}", extra={"event": "db_error"})
        return False
    finally:
        if own_conn:
            conn.close()


def _migrate(cur: sqlite3.Cursor) -> None:
    """Idempotent column migrations for databases created by older versions."""
    # collection.is_foil (added manually during development; make it official)
    cur.execute("PRAGMA table_info(collection)")
    cols = {row[1] for row in cur.fetchall()}
    if "is_foil" not in cols:
        cur.execute("ALTER TABLE collection ADD COLUMN is_foil INTEGER DEFAULT 0")
        log.info("Migration: added collection.is_foil", extra={"event": "db_migration"})

    # cards.front_art_url_de (German card art from the official API CDN)
    cur.execute("PRAGMA table_info(cards)")
    cols = {row[1] for row in cur.fetchall()}
    if "front_art_url_de" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN front_art_url_de TEXT")
        log.info("Migration: added cards.front_art_url_de", extra={"event": "db_migration"})


def check_integrity(conn: sqlite3.Connection | None = None) -> bool:
    """Run PRAGMA integrity_check."""
    own_conn = conn is None
    if own_conn:
        conn = connect()
    try:
        cur = conn.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        ok = result == "ok"
        if not ok:
            log.error(f"Database integrity check failed: {result}", extra={"event": "db_integrity_fail"})
        return ok
    finally:
        if own_conn:
            conn.close()
