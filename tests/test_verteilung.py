"""Tests: Verteilungsregeln-Engine + Teilmengen-Split."""
import sqlite3

import pytest

from app.db.schema import init_database
from app import verteilung as vt
from app.db import repository as repo
from app.storage import get_default_location, create_location


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "vt.db")
    c = sqlite3.connect(tmp_path / "vt.db")
    c.row_factory = sqlite3.Row
    init_database(c)
    c.execute("INSERT OR IGNORE INTO households (household_id, name, owner_user_id) VALUES (1, 'HH', 1)")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def orte(conn):
    """Drei Orte: Ordner (Display), Playsets, Bulk."""
    folder = create_location(conn, "Ordner", typ="binder", household_id=1, icon="📕")
    playset = create_location(conn, "Playsets", typ="binder", household_id=1, icon="🎯")
    bulk = create_location(conn, "Bulk", typ="box", household_id=1, icon="🗑️")
    return {"ordner": folder, "playset": playset, "bulk": bulk}


def _regeln(orte):
    return [
        {"von_kopie": 1, "bis_kopie": 1, "ziel_location_id": orte["ordner"]},
        {"von_kopie": 2, "bis_kopie": 4, "ziel_location_id": orte["playset"]},
        {"von_kopie": 5, "bis_kopie": None, "ziel_location_id": orte["bulk"]},
    ]


class TestRegelEngine:
    """Entscheidungstabelle: menge -> erwartete Verteilung."""

    @pytest.mark.parametrize("menge,erwartung", [
        (1, [("ordner", 1)]),
        (2, [("ordner", 1), ("playset", 1)]),
        (4, [("ordner", 1), ("playset", 3)]),
        (5, [("ordner", 1), ("playset", 3), ("bulk", 1)]),
        (8, [("ordner", 1), ("playset", 3), ("bulk", 4)]),
        (20, [("ordner", 1), ("playset", 3), ("bulk", 16)]),
    ])
    def test_standard_muster(self, conn, orte, menge, erwartung):
        """Das Nutzer-Muster: 1 Ordner, 2-4 Playset, Rest Bulk."""
        vt.save_regeln(conn, 1, _regeln(orte))
        result = vt.verteile_card(conn, 1, menge)
        namen = {orte["ordner"]: "ordner", orte["playset"]: "playset", orte["bulk"]: "bulk"}
        # zu Map mergen
        got = {}
        for ziel, anzahl in result:
            key = namen.get(ziel, "default")
            got[key] = got.get(key, 0) + anzahl
        exp = {}
        for name, anzahl in erwartung:
            exp[name] = exp.get(name, 0) + anzahl
        assert got == exp, f"menge={menge}: {got} != {exp}"
        assert sum(got.values()) == menge, "Keine Kopie darf verloren gehen"

    def test_null_menge(self, conn, orte):
        vt.save_regeln(conn, 1, _regeln(orte))
        assert vt.verteile_card(conn, 1, 0) == []

    def test_keine_regeln_fallback_standard(self, conn):
        """Ohne Regelwerk -> alles in den Standard-Ort."""
        result = vt.verteile_card(conn, 1, 5)
        d = get_default_location(conn, 1)
        assert result == [(d["location_id"], 5)]

    def test_regelluecke_fallback_standard(self, conn, orte):
        """Luecke (nur Kopie 1 geregelt) -> Rest faellt auf Standard."""
        vt.save_regeln(conn, 1, [
            {"von_kopie": 1, "bis_kopie": 1, "ziel_location_id": orte["ordner"]},
        ])
        result = vt.verteile_card(conn, 1, 6)
        d = get_default_location(conn, 1)
        assert sum(a for _, a in result) == 6
        assert (orte["ordner"], 1) in result
        assert (d["location_id"], 5) in result

    def test_ziel_geloescht_fallback(self, conn, orte):
        """Regel-Ziel existiert nicht (mehr) -> Standard-Ort."""
        vt.save_regeln(conn, 1, _regeln(orte))
        conn.execute("UPDATE storage_locations SET is_active = 0 WHERE location_id = ?",
                     (orte["bulk"],))
        conn.commit()
        result = vt.verteile_card(conn, 1, 8)
        d = get_default_location(conn, 1)
        assert sum(a for _, a in result) == 8  # nichts verloren


class TestRegelValidierung:
    def test_zwei_rest_regeln_verboten(self, conn, orte):
        regeln = _regeln(orte) + [{"von_kopie": 99, "bis_kopie": None, "ziel_location_id": orte["bulk"]}]
        with pytest.raises(ValueError, match="Rest"):
            vt.save_regeln(conn, 1, regeln)

    def test_bereich_invertiert(self, conn, orte):
        with pytest.raises(ValueError, match="Ende-Kopie"):
            vt.save_regeln(conn, 1, [
                {"von_kopie": 5, "bis_kopie": 2, "ziel_location_id": orte["bulk"]},
            ])

    def test_doppelte_bereiche(self, conn, orte):
        with pytest.raises(ValueError, match="Doppelte"):
            vt.save_regeln(conn, 1, [
                {"von_kopie": 1, "bis_kopie": 3, "ziel_location_id": orte["ordner"]},
                {"von_kopie": 1, "bis_kopie": 3, "ziel_location_id": orte["playset"]},
            ])

    def test_von_null_verboten(self, conn, orte):
        with pytest.raises(ValueError, match="Start-Kopie"):
            vt.save_regeln(conn, 1, [
                {"von_kopie": 0, "bis_kopie": 2, "ziel_location_id": orte["ordner"]},
            ])


class TestSplit:
    def test_split_8_auf_drei_orte(self, conn, orte):
        cid = repo.add_to_collection(conn, "SOR-005", count=8, language="de", household_id=1)
        bewegt = vt.split_collection_row(conn, cid, [
            (orte["ordner"], 1), (orte["playset"], 3), (orte["bulk"], 4),
        ], household_id=1)
        assert bewegt == 8
        zeilen = conn.execute(
            "SELECT location_id, count FROM collection WHERE card_id='SOR-005' AND household_id=1"
        ).fetchall()
        vert = {r["location_id"]: r["count"] for r in zeilen}
        assert vert[orte["ordner"]] == 1
        assert vert[orte["playset"]] == 3
        assert vert[orte["bulk"]] == 4
        # Quelle (Standard) ist leer und weg
        std = get_default_location(conn, 1)
        assert std["location_id"] not in vert

    def test_split_teilmenge_quelle_bleibt(self, conn, orte):
        cid = repo.add_to_collection(conn, "SOR-005", count=8, language="de", household_id=1)
        bewegt = vt.split_collection_row(conn, cid, [(orte["ordner"], 3)], household_id=1)
        assert bewegt == 3
        std = get_default_location(conn, 1)
        rest = conn.execute(
            "SELECT count FROM collection WHERE collection_id = ?", (cid,)
        ).fetchone()
        assert rest["count"] == 5

    def test_split_ueberbestand_verboten(self, conn, orte):
        cid = repo.add_to_collection(conn, "SOR-005", count=4, language="de", household_id=1)
        with pytest.raises(ValueError, match="übersteigt"):
            vt.split_collection_row(conn, cid, [(orte["ordner"], 5)], household_id=1)

    def test_split_journal_eintraege(self, conn, orte):
        cid = repo.add_to_collection(conn, "SOR-005", count=8, language="de", household_id=1)
        vt.split_collection_row(conn, cid, [
            (orte["ordner"], 1), (orte["playset"], 3),
        ], household_id=1)
        moves = conn.execute(
            "SELECT COUNT(*) FROM card_moves WHERE collection_id = ?", (cid,)
        ).fetchone()[0]
        assert moves == 2, "Je Ziel-Ort ein Journal-Eintrag"

    def test_split_attribute_bleiben(self, conn, orte):
        """Sprache/Zustand/Foil der Quelle wandern mit."""
        cid = repo.add_to_collection(conn, "SOR-005", count=2, language="en",
                                      variant="AltArt", household_id=1)
        conn.execute("UPDATE collection SET is_foil=1 WHERE collection_id=?", (cid,))
        conn.commit()
        vt.split_collection_row(conn, cid, [(orte["ordner"], 2)], household_id=1)
        ziel = conn.execute(
            "SELECT * FROM collection WHERE location_id=? AND card_id='SOR-005'",
            (orte["ordner"],),
        ).fetchone()
        assert ziel["language"] == "en"
        assert ziel["variant"] == "AltArt"
        assert ziel["is_foil"] == 1

    def test_split_haushalt_isolation(self, conn, orte):
        """Fremde Zeile (anderer Haushalt) ist nicht split-bar."""
        conn.execute("INSERT INTO households (household_id, name, owner_user_id) VALUES (2, 'HH2', 9)")
        cid2 = repo.add_to_collection(conn, "SOR-005", count=4, household_id=2)
        with pytest.raises(ValueError, match="nicht gefunden"):
            vt.split_collection_row(conn, cid2, [(orte["ordner"], 1)], household_id=1)
