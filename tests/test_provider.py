"""Data provider and import tests."""
import pytest
from app.core.config import AppConfig
from app.db.schema import connect, init_database
from app.db import repository as repo
from app.providers.swudb_provider import SWUDBProvider


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_database(conn)
    yield conn
    conn.close()


class TestProvider:
    def test_provider_init(self):
        provider = SWUDBProvider()
        assert provider.base_url == "https://api.swu-db.com"

    @pytest.mark.skipif(
        pytest.importorskip("requests"),
        reason="Network test — requires internet"
    )
    def test_get_sets_live(self):
        """Live test against the real API."""
        provider = SWUDBProvider()
        try:
            sets = provider.get_sets()
            assert len(sets) > 0
            assert any(s["setId"] == "SOR" for s in sets)
        except Exception:
            pytest.skip("Network unavailable")


class TestImport:
    def test_upsert_set_and_card(self, db_conn):
        # Simulate importing from API
        set_data = {
            "setId": "SOR",
            "fullName": "Spark of Rebellion",
            "numberCards": 262,
            "maxElement": "262",
            "isBaseSet": True,
            "releaseDate": "3/8/24",
        }
        repo.upsert_set(db_conn, set_data)
        assert repo.get_set(db_conn, "SOR") is not None

        card_data = {
            "Set": "SOR", "Number": "010", "Name": "Darth Vader",
            "Subtitle": "Dark Lord", "Type": "Leader", "Rarity": "Special",
            "Aspects": ["Aggression"], "Traits": ["SITH"],
            "Arenas": ["Ground"], "Cost": "7", "Power": "5", "HP": "8",
            "FrontText": "text", "Artist": "Artist", "Unique": True,
            "FrontArt": "https://example.com/img.png",
        }
        card_id = repo.upsert_card(db_conn, card_data)
        assert card_id == "SOR-010"
        c = repo.get_card(db_conn, "SOR-010")
        assert c["name"] == "Darth Vader"
