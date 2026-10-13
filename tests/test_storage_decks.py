"""Tests: Ablageorte & Decks — Fundament (Phase 1)."""
import pytest

from app.db import repository as repo
from app.db.schema import connect as _connect, init_database
from app import storage as st


class TestStorageLocations:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "st.db")
        conn = _connect(tmp_path / "st.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute(
            "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
            "VALUES ('SOR-001', 'SOR', '001', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '[\"Aggression\"]')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, password_hash, is_platform_admin, email_verified) "
            "VALUES (1, 'stowner', 'st@example.com', 'x', 0, 1)"
        )
        conn.execute("INSERT OR IGNORE INTO households (household_id, name, owner_user_id) VALUES (1, 'H', 1)")
        # Standard-Ort ggf. anlegen (init ohne Household-Sektion legt keinen an)
        if not st.get_default_location(conn, 1):
            conn.execute(
                "INSERT INTO storage_locations (household_id, name, typ, is_default) VALUES (1, 'Sammlung (Standard)', 'standard', 1)"
            )
            conn.commit()
        yield conn
        conn.close()

    def test_default_location_exists_and_protected(self, db):
        d = st.get_default_location(db, 1)
        assert d and d["is_default"] == 1
        ok, grund = st.delete_location(db, d["location_id"], household_id=1)
        assert ok is False
        assert "Standard-Ort" in grund

    def test_crud_location(self, db):
        loc = st.create_location(db, "Rotes Binder", typ="binder", household_id=1)
        assert loc > 0
        locs = st.list_locations(db, 1)
        names = [l["name"] for l in locs]
        assert "Rotes Binder" in names
        # Umbenennen
        assert st.rename_location(db, loc, "Blaues Binder", household_id=1) is True
        assert st.rename_location(db, loc, "Blaues Binder", household_id=99) is False  # Isolation
        # Leerer Ort loeschbar
        ok, grund = st.delete_location(db, loc, household_id=1)
        assert ok is True

    def test_delete_blocked_when_cards_inside(self, db):
        d = st.get_default_location(db, 1)
        loc = st.create_location(db, "Bulkbox", typ="box", household_id=1)
        cid = repo.add_to_collection(db, "SOR-001", count=3, household_id=1)
        st.move_cards(db, [cid], loc, reason="manuell", household_id=1)
        ok, grund = st.delete_location(db, loc, household_id=1)
        assert ok is False
        assert "3 Karten" in grund

    def test_move_cards_with_journal(self, db):
        d = st.get_default_location(db, 1)
        loc = st.create_location(db, "Ordner A", typ="binder", household_id=1)
        cid = repo.add_to_collection(db, "SOR-001", count=2, household_id=1)
        moved = st.move_cards(db, [cid], loc, reason="scan", household_id=1)
        assert moved == 1
        row = db.execute("SELECT location_id FROM collection WHERE collection_id = ?", (cid,)).fetchone()
        assert row["location_id"] == loc
        mv = db.execute("SELECT * FROM card_moves WHERE collection_id = ?", (cid,)).fetchone()
        assert mv["from_location_id"] == d["location_id"]
        assert mv["to_location_id"] == loc
        assert mv["reason"] == "scan"
        # Idempotenz: gleiche client_event_id kein Doppel-Move
        moved2 = st.move_cards(db, [cid], d["location_id"], reason="manuell",
                               household_id=1, client_event_id="evt-1")
        assert moved2 == 1
        try:
            st.move_cards(db, [cid], loc, reason="manuell", household_id=1, client_event_id="evt-1")
            pytest.fail("Doppel-Event sollte UNIQUE verletzen")
        except Exception:
            pass  # UNIQUE verhindert Doppelbuchung

    def test_split_collection_entry(self, db):
        """Mengen-Problem: 7 Kopien -> 3 an neuen Ort, 4 bleiben."""
        d = st.get_default_location(db, 1)
        loc = st.create_location(db, "Deckbox Test", typ="deckbox", household_id=1)
        cid = repo.add_to_collection(db, "SOR-001", count=7, household_id=1)
        new_id = st.split_collection_entry(db, cid, 3, loc, household_id=1)
        assert new_id is not None
        rows = db.execute(
            "SELECT location_id, count FROM collection WHERE card_id = 'SOR-001' AND household_id = 1"
        ).fetchall()
        by_loc = {r["location_id"]: r["count"] for r in rows}
        assert by_loc[d["location_id"]] == 4
        assert by_loc[loc] == 3

    def test_household_isolation(self, db):
        loc = st.create_location(db, "Nur HH1", typ="box", household_id=1)
        # HH 99 sieht den Ort nicht
        rows = st.list_locations(db, 99)
        assert all(l["location_id"] != loc for l in rows)


class TestDecks:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "dk.db")
        conn = _connect(tmp_path / "dk.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        for cid, nm in [("SOR-001", "Vader"), ("SOR-002", "Luke"), ("SOR-003", "Wookiee")]:
            conn.execute(
                "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
                f"VALUES ('{cid}', 'SOR', '{cid[-3:]}', '{nm}', '{nm}', 'Unit', 'Rare', '[\"Aggression\"]')"
            )
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, password_hash, is_platform_admin, email_verified) "
            "VALUES (1, 'stowner', 'st@example.com', 'x', 0, 1)"
        )
        conn.execute("INSERT OR IGNORE INTO households (household_id, name, owner_user_id) VALUES (1, 'H', 1)")
        if not st.get_default_location(conn, 1):
            conn.execute(
                "INSERT INTO storage_locations (household_id, name, typ, is_default) VALUES (1, 'Sammlung (Standard)', 'standard', 1)"
            )
            conn.commit()
        yield conn
        conn.close()

    def test_create_deck_makes_deckbox(self, db):
        did = st.create_deck(db, "Cad Bane Gelb", household_id=1)
        deck = st.get_deck(db, did, 1)
        assert deck["name"] == "Cad Bane Gelb"
        assert deck["location_id"] is not None
        box = db.execute("SELECT * FROM storage_locations WHERE location_id = ?", (deck["location_id"],)).fetchone()
        assert box["typ"] == "deckbox"
        assert box["name"] == "Deckbox: Cad Bane Gelb"

    def test_rename_deck_renames_box(self, db):
        did = st.create_deck(db, "Alt", household_id=1)
        st.rename_deck(db, did, "Cad Bane Gelb", household_id=1)
        deck = st.get_deck(db, did, 1)
        box = db.execute("SELECT name FROM storage_locations WHERE location_id = ?", (deck["location_id"],)).fetchone()
        assert box["name"] == "Deckbox: Cad Bane Gelb"

    def test_assign_and_availability(self, db):
        """Kern: verfügbar = besitzt − in Decks (Silvane-Mathe)."""
        repo.add_to_collection(db, "SOR-001", count=3, household_id=1)
        did = st.create_deck(db, "Testdeck", household_id=1)
        av = st.available_count(db, "SOR-001", 1)
        assert av == {"owned": 3, "in_decks": 0, "available": 3}
        r = st.assign_card_to_deck(db, "SOR-001", did, menge=2, household_id=1)
        assert r["ok"] is True
        av2 = st.available_count(db, "SOR-001", 1)
        assert av2 == {"owned": 3, "in_decks": 2, "available": 1}
        # Deckliste wurde ergänzt
        dl = st.get_deck_list(db, did)
        assert any(i["card_id"] == "SOR-001" and i["menge"] == 2 for i in dl)

    def test_assign_blocks_when_no_free_copies(self, db):
        repo.add_to_collection(db, "SOR-002", count=1, household_id=1)
        did = st.create_deck(db, "D2", household_id=1)
        r = st.assign_card_to_deck(db, "SOR-002", did, menge=2, household_id=1)
        assert r["ok"] is False
        assert r["error"] == "keine_freien"
        assert r["verfuegbar"] == 1

    def test_resolve_deck_returns_to_origin(self, db):
        """Return to Origin: Karten kehren an ihren Ursprungs-Ort zurueck."""
        d = st.get_default_location(db, 1)
        binder = st.create_location(db, "Rotes Binder", typ="binder", household_id=1)
        # 2 Kopien liegen im Binder
        cid = repo.add_to_collection(db, "SOR-003", count=2, household_id=1)
        st.move_cards(db, [cid], binder, reason="manuell", household_id=1)
        did = st.create_deck(db, "D3", household_id=1)
        r = st.assign_card_to_deck(db, "SOR-003", did, menge=2, household_id=1)
        assert r["ok"] is True
        # Alle in der Deck-Box?
        deck = st.get_deck(db, did, 1)
        in_box = db.execute(
            "SELECT COALESCE(SUM(count),0) FROM collection WHERE location_id = ?", (deck["location_id"],)
        ).fetchone()[0]
        assert in_box == 2
        # Aufloesen -> zurueck zum Binder (Origin), nicht zum Standard!
        res = st.resolve_deck(db, did, household_id=1)
        assert res["ok"] is True and res["karten"] == 2
        back = db.execute(
            "SELECT location_id, count FROM collection WHERE card_id = 'SOR-003'"
        ).fetchall()
        assert any(r2["location_id"] == binder and r2["count"] == 2 for r2 in back)
        # Journal: 1 deck_resolve-Eintrag pro BESTANDSZEILE (2 Kopien = 1 Zeile)
        mv = db.execute("SELECT COUNT(*) FROM card_moves WHERE reason = 'deck_resolve'").fetchone()[0]
        assert mv == 1

    def test_delete_deck_blocked_with_cards_in_box(self, db):
        repo.add_to_collection(db, "SOR-001", count=1, household_id=1)
        did = st.create_deck(db, "D4", household_id=1)
        st.assign_card_to_deck(db, "SOR-001", did, menge=1, household_id=1)
        ok, info = st.delete_deck(db, did, household_id=1)
        assert ok is False
        assert info["karten_in_box"] == 1
        # Nach Aufloesen loeschbar
        st.resolve_deck(db, did, household_id=1)
        ok2, info2 = st.delete_deck(db, did, household_id=1)
        assert ok2 is True

    def test_card_in_decks_query(self, db):
        repo.add_to_collection(db, "SOR-001", count=2, household_id=1)
        d1 = st.create_deck(db, "Deck A", household_id=1)
        d2 = st.create_deck(db, "Deck B", household_id=1)
        st.assign_card_to_deck(db, "SOR-001", d1, menge=1, household_id=1)
        st.assign_card_to_deck(db, "SOR-001", d2, menge=1, household_id=1)
        in_decks = st.card_in_decks(db, "SOR-001", 1)
        names = {d["name"] for d in in_decks}
        assert names == {"Deck A", "Deck B"}
