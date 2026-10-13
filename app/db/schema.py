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

SCHEMA_VERSION = 5


def get_db_path() -> Path:
    import os

    override = os.environ.get("SWU_DB_PATH_OVERRIDE")
    if override:
        return Path(override)
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
    # --- Auth: users ---------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash   TEXT NOT NULL,
        display_name    TEXT,
        is_platform_admin INTEGER DEFAULT 0,
        email_verified  INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now')),
        last_login_at   TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)
    """,
    # --- Auth: households (collection contexts) -------------------------------
    """
    CREATE TABLE IF NOT EXISTS households (
        household_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        owner_user_id   INTEGER NOT NULL,
        invite_code     TEXT UNIQUE,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (owner_user_id) REFERENCES users(user_id)
    )
    """,
    # --- Auth: household memberships with roles --------------------------------
    """
    CREATE TABLE IF NOT EXISTS household_members (
        member_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        household_id    INTEGER NOT NULL,
        user_id         INTEGER NOT NULL,
        role            TEXT NOT NULL DEFAULT 'editor',  -- owner|editor|viewer
        joined_at       TEXT DEFAULT (datetime('now')),
        UNIQUE (household_id, user_id),
        FOREIGN KEY (household_id) REFERENCES households(household_id),
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    """,
    # --- Auth: sessions (remember-me tokens) ------------------------------------
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        session_token   TEXT PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        created_at      TEXT DEFAULT (datetime('now')),
        expires_at      TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    """,
    # --- Auth: password reset tokens ---------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS password_resets (
        reset_token     TEXT PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        created_at      TEXT DEFAULT (datetime('now')),
        expires_at      TEXT NOT NULL,
        used            INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    """,
    # --- Auth: login rate limiting ------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        attempt_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        username_or_email TEXT NOT NULL,
        ip              TEXT,
        success         INTEGER DEFAULT 0,
        attempted_at    TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts (username_or_email, attempted_at)
    """,
    # --- Auth: email verification tokens -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS email_verifications (
        verify_token    TEXT PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        expires_at      TEXT NOT NULL,
        used            INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
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
    # --- TCGplayer (tcgcsv) prices in USD -------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tcg_prices (
        card_id         TEXT PRIMARY KEY,
        product_id       INTEGER,
        market_normal   REAL,
        market_foil     REAL,
        low_normal       REAL,
        low_foil         REAL,
        mid_normal       REAL,
        mid_foil         REAL,
        updated_at       TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Card -> MKM product mapping (multiple card_ids may map to one product,
    #     e.g. SOR-010 from SWU-DB and SOR-10 from the official API are the same card) ---
    """
    CREATE TABLE IF NOT EXISTS card_mkm_map (
        card_id         TEXT PRIMARY KEY,
        idProduct       INTEGER NOT NULL
    )
    """,
    # --- Undo journal (one row per confirmable scan action) -------------------
    """
    CREATE TABLE IF NOT EXISTS scan_undo (
        undo_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id         INTEGER,
        collection_id   INTEGER,
        card_id         TEXT,
        count_added     INTEGER DEFAULT 1,
        previous_count  INTEGER,
        entry_created   INTEGER DEFAULT 0,
        is_foil         INTEGER DEFAULT 0,
        photo_path      TEXT,
        correction_id   INTEGER,
        dataset_path    TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        undone          INTEGER DEFAULT 0,
        undone_at       TEXT
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
    # --- Ablageorte v5: physische Aufbewahrung (Recherche: ~225 Quellen) ------
    """
    CREATE TABLE IF NOT EXISTS storage_locations (
        location_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        household_id    INTEGER NOT NULL DEFAULT 1,
        name            TEXT NOT NULL,
        typ             TEXT NOT NULL DEFAULT 'box',   -- standard|binder|box|deckbox|sonstige
        sortierung      TEXT DEFAULT 'set',            -- set|aspekt|name (SWU-Community-Standards)
        kapazitaet      INTEGER,                        -- optionale Füllstand-Warnung
        notiz           TEXT,
        is_default      INTEGER DEFAULT 0,              -- Standard-Ort: nie löschbar
        is_active       INTEGER DEFAULT 1,
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decks (
        deck_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        household_id    INTEGER NOT NULL DEFAULT 1,
        name            TEXT NOT NULL,
        format          TEXT DEFAULT 'premier',         -- premier|eternal|twin_suns
        leader_card_id  TEXT,
        base_card_id   TEXT,
        status          TEXT DEFAULT 'geplant',          -- geplant|gebaut
        location_id     INTEGER,                         -- Deck-Box (deckbox-Ort)
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deck_cards (
        deck_card_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id         INTEGER NOT NULL REFERENCES decks(deck_id) ON DELETE CASCADE,
        card_id         TEXT NOT NULL,
        menge           INTEGER NOT NULL DEFAULT 1,
        zone            TEXT NOT NULL DEFAULT 'maindeck', -- maindeck|sideboard
        UNIQUE (deck_id, card_id, zone)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_moves (
        move_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        household_id    INTEGER NOT NULL DEFAULT 1,
        collection_id   INTEGER NOT NULL REFERENCES collection(collection_id) ON DELETE CASCADE,
        from_location_id INTEGER,
        to_location_id  INTEGER,
        reason          TEXT NOT NULL,   -- scan|manuell|deck_build|deck_resolve|korrektur|eingang|abgang
        created_at      TEXT DEFAULT (datetime('now')),
        client_event_id TEXT UNIQUE      -- Idempotenz (Offline-Queue)
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
    # --- Merkliste: Preis-Alarm-Verlauf (Keepa-Muster) -----------------------
    """
    CREATE TABLE IF NOT EXISTS wishlist_alerts (
        alert_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        wishlist_id     INTEGER NOT NULL REFERENCES wishlist(wishlist_id) ON DELETE CASCADE,
        kind            TEXT NOT NULL,          -- 'target_hit' | 'price_drop' | 'digest'
        old_price       REAL,
        new_price       REAL,
        created_at      TEXT DEFAULT (datetime('now')),
        acknowledged    INTEGER DEFAULT 0
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

    # ---- Multi-tenancy (v3): household_id on all collection-scoped tables ----
    # Existing data is backfilled onto the legacy default household (id 1)
    # once the FIRST user registers (see auth.ensure_legacy_household).
    tenant_tables = {
        "collection": ("household_id INTEGER", "user_id INTEGER"),
        "scans": ("household_id INTEGER",),
        "wishlist": ("household_id INTEGER",),
        "scan_undo": ("household_id INTEGER",),
        "settings": ("user_id INTEGER", "household_id INTEGER"),
    }
    for table, col_defs in tenant_tables.items():
        cur.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cur.fetchall()}
        for col_def in col_defs:
            col_name = col_def.split()[0]
            if col_name not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                log.info(f"Migration: added {table}.{col_name}", extra={"event": "db_migration"})

    # ---- Merkliste v4: Varianten + Preisbeobachtung (Wishlist-Redesign) ----
    # cardmarket-Blaupause: pro Want Variante (Foil), Sprache, Menge, Prioritaet,
    # Zielpreis (target_price existiert bereits) + Preis beim Hinzufuegen fuer
    # Delta-Anzeige ("seit gespeichert"). target_reached_at = Anti-Spam (nur
    # der ERSTE Treffer alarmiert, Cardmarket-Muster).
    cur.execute("PRAGMA table_info(wishlist)")
    cols = {row[1] for row in cur.fetchall()}
    for col_def in (
        "is_foil INTEGER DEFAULT 0",
        "language TEXT DEFAULT 'de'",
        "priority INTEGER DEFAULT 2",          # 1=Must-have 2=Normal 3=Nice-to-have
        "price_at_add REAL",
        "added_at_price_src TEXT",            # 'tcg' | 'mkm'
        "target_reached_at TEXT",
    ):
        col_name = col_def.split()[0]
        if col_name not in cols:
            cur.execute(f"ALTER TABLE wishlist ADD COLUMN {col_def}")
            log.info(f"Migration: added wishlist.{col_name}", extra={"event": "db_migration"})

    # ---- Ablageorte v5: location_id auf Sammlungs-Zeilen -------------------
    cur.execute("PRAGMA table_info(collection)")
    cols = {row[1] for row in cur.fetchall()}
    if "location_id" not in cols:
        cur.execute("ALTER TABLE collection ADD COLUMN location_id INTEGER REFERENCES storage_locations(location_id)")
        log.info("Migration: added collection.location_id", extra={"event": "db_migration"})
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_location ON collection (location_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_card_moves_household ON card_moves (household_id)"
    )

    # ---- Ablageorte/Decks v5.1: eigenes Symbol (Emoji) je Ort/Deck ---------
    # Nutzer-Anforderung: 'wenn ich Sammlungen anlege, will ich die Symbole
    # selbst auswählen und ändern können'. icon ist ein einzelnes Emoji-
    # Zeichen (max 8 Bytes); NULL -> UI-Fallback auf typ-basierte Icons.
    cur.execute("PRAGMA table_info(storage_locations)")
    cols = {row[1] for row in cur.fetchall()}
    if "icon" not in cols:
        cur.execute("ALTER TABLE storage_locations ADD COLUMN icon TEXT")
        log.info("Migration: added storage_locations.icon", extra={"event": "db_migration"})
    cur.execute("PRAGMA table_info(decks)")
    cols = {row[1] for row in cur.fetchall()}
    if "icon" not in cols:
        cur.execute("ALTER TABLE decks ADD COLUMN icon TEXT")
        log.info("Migration: added decks.icon", extra={"event": "db_migration"})

    # ---- Verteilungsregeln v5.2: Kopie 1 -> Ordner, 2-4 -> Playset, 5+ Bulk
    # ERP-Slotting-Muster: priorisierte, geordnete Regelkette mit Overflow.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verteilungs_regeln (
            regel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            household_id     INTEGER NOT NULL DEFAULT 1,
            rang            INTEGER NOT NULL DEFAULT 1,
            von_kopie       INTEGER NOT NULL DEFAULT 1,
            bis_kopie       INTEGER,               -- NULL = bis unbeschraenkt (Rest/Overflow)
            ziel_location_id INTEGER REFERENCES storage_locations(location_id),
            bedingung       TEXT,                  -- zukuenftig: 'rarity>=R' o.ae.
            aktiv           INTEGER DEFAULT 1,
            erstellt_am     TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_verteilung_hh ON verteilungs_regeln (household_id, rang)"
    )


    # Standard-Ort als geschützter Seed-Datensatz (Siebel 'Protect Seed Data'):
    # 'Sammlung (Standard)' = Eingangs-Box fuer Unsortiertes (CoolStuffInc-Muster).
    # Pro Haushalt genau einer; bestehende Sammlungs-Zeilen (location_id NULL)
    # werden per Journal-Grund 'eingang' dem Standard-Ort zugeordnet.
    for hh_row in cur.execute("SELECT household_id FROM households").fetchall():
        hh = hh_row[0]
        cur.execute(
            "SELECT 1 FROM storage_locations WHERE household_id = ? AND is_default = 1",
            (hh,),
        )
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO storage_locations (household_id, name, typ, sortierung, is_default)
                   VALUES (?, 'Sammlung (Standard)', 'standard', 'set', 1)""",
                (hh,),
            )
            loc_id = cur.execute(
                "SELECT location_id FROM storage_locations WHERE household_id = ? AND is_default = 1",
                (hh,),
            ).fetchone()[0]
            cur.execute(
                "UPDATE collection SET location_id = ? WHERE household_id = ? AND location_id IS NULL",
                (loc_id, hh),
            )
            log.info(f"Migration: Standard-Ort fuer Haushalt {hh} angelegt (id {loc_id})",
                     extra={"event": "db_migration"})

    # Indexes for tenant-scoped queries
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_household ON collection (household_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_wishlist_household ON wishlist (household_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_scans_household ON scans (household_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_settings_user ON settings (user_id, household_id)"
    )


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
