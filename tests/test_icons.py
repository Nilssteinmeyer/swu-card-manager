"""Tests: eigene Symbole (Emoji-Icons) für Ablageorte + Decks."""
import sqlite3

import pytest

from app.db import schema as db_schema
from app.db.schema import init_database
from app import storage as st


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "icons.db")
    c = sqlite3.connect(tmp_path / "icons.db")
    c.row_factory = sqlite3.Row
    init_database(c)
    yield c
    c.close()


class TestLocationIcons:
    def test_create_with_icon(self, conn):
        loc_id = st.create_location(conn, "Rotes Binder", typ="binder",
                                    household_id=1, icon="🔴")
        row = conn.execute("SELECT icon, name FROM storage_locations WHERE location_id = ?",
                           (loc_id,)).fetchone()
        assert row["icon"] == "🔴"

    def test_icon_sanitized(self, conn):
        # Zu lang / leer -> None (UI zeigt typ-Fallback)
        loc_id = st.create_location(conn, "Box", typ="box", household_id=1,
                                    icon="🔥🔥🔥🔥🔥")
        row = conn.execute("SELECT icon FROM storage_locations WHERE location_id = ?",
                           (loc_id,)).fetchone()
        assert row["icon"] is None
        loc2 = st.create_location(conn, "Box2", typ="box", household_id=1, icon="   ")
        row2 = conn.execute("SELECT icon FROM storage_locations WHERE location_id = ?",
                            (loc2,)).fetchone()
        assert row2["icon"] is None

    def test_change_icon_via_rename(self, conn):
        loc_id = st.create_location(conn, "Bulkbox", typ="box", household_id=1)
        ok = st.rename_location(conn, loc_id, "Bulkbox SOR", household_id=1, icon="🧊")
        assert ok
        row = conn.execute("SELECT icon FROM storage_locations WHERE location_id = ?",
                           (loc_id,)).fetchone()
        assert row["icon"] == "🧊"

    def test_reset_icon_empty_string(self, conn):
        loc_id = st.create_location(conn, "Test", typ="box", household_id=1, icon="⚡")
        st.rename_location(conn, loc_id, "Test", household_id=1, icon="")
        row = conn.execute("SELECT icon FROM storage_locations WHERE location_id = ?",
                           (loc_id,)).fetchone()
        assert row["icon"] is None  # '' -> Fallback

    def test_rename_without_icon_keeps_it(self, conn):
        loc_id = st.create_location(conn, "Test", typ="box", household_id=1, icon="⚡")
        st.rename_location(conn, loc_id, "Test neu", household_id=1)  # icon=None
        row = conn.execute("SELECT icon, name FROM storage_locations WHERE location_id = ?",
                           (loc_id,)).fetchone()
        assert row["icon"] == "⚡"
        assert row["name"] == "Test neu"


class TestDeckIcons:
    def test_deck_icon_propagates_to_box(self, conn):
        deck_id = st.create_deck(conn, "Cad Bane Gelb", household_id=1, icon="🟡")
        deck = st.get_deck(conn, deck_id, 1)
        assert deck["icon"] == "🟡"
        box = conn.execute("SELECT icon, typ FROM storage_locations WHERE location_id = ?",
                           (deck["location_id"],)).fetchone()
        assert box["icon"] == "🟡"  # Deck-Box uebernimmt das Deck-Symbol
        assert box["typ"] == "deckbox"

    def test_deck_rename_updates_icon(self, conn):
        deck_id = st.create_deck(conn, "Testdeck", household_id=1)
        st.rename_deck(conn, deck_id, "Testdeck v2", household_id=1, icon="⚔️")
        deck = st.get_deck(conn, deck_id, 1)
        assert deck["icon"] == "⚔️"
        box = conn.execute("SELECT icon FROM storage_locations WHERE location_id = ?",
                           (deck["location_id"],)).fetchone()
        assert box["icon"] == "⚔️"

    def test_deck_rename_without_icon_keeps(self, conn):
        deck_id = st.create_deck(conn, "Icons Deck", household_id=1, icon="🐉")
        st.rename_deck(conn, deck_id, "Icons Deck v2", household_id=1)
        deck = st.get_deck(conn, deck_id, 1)
        assert deck["icon"] == "🐉"
        assert deck["name"] == "Icons Deck v2"
