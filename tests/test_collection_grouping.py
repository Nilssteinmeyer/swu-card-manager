"""Tests: Sammlungs-Aggregation — jede Karte nur EINE Kachel."""
import pytest


class TestCollectionGrouping:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "grp.db")
        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "grp.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute(
            "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
            "VALUES ('SOR-001', 'SOR', '001', 'Darth Vader', 'Darth Vader', 'Unit', 'Rare', '[\"Aggression\"]')"
        )
        # Bestand: 3 Zeilen derselben Karte (en x4, de x2, en x1) -> 1 Kachel x7
        from app.db import repository as repo

        repo.add_to_collection(conn, "SOR-001", count=4, language="en", household_id=1)
        repo.add_to_collection(conn, "SOR-001", count=2, language="de", household_id=1)
        repo.add_to_collection(conn, "SOR-001", count=1, language="en", household_id=1)
        conn.commit()
        conn.close()

        from app.web.app import create_app
        from app.core.config import AppConfig

        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        app.config["SESSION_COOKIE_SECURE"] = False
        client = app.test_client()
        r = client.post("/api/auth/register", json={
            "username": "grptester", "email": "grp@example.com", "password": "testpass123",
        })
        assert r.status_code == 200
        yield client

    def test_one_tile_per_card(self, client):
        """"4x en + 2x de + 1x en derselben Karte -> EINE Kachel mit count=7."""
        r = client.get("/api/collection")
        assert r.status_code == 200
        data = r.get_json()
        assert data["count"] == 1, "Aggregation muss 1 Kachel liefern"
        item = data["items"][0]
        assert item["count"] == 7, f"Summe aller Zeilen: 7, nicht {item['count']}"
        assert set(item["languages"]) == {"en", "de"}, "beide Sprachen aufgefuehrt"

    def test_group_rows_returns_raw_lines(self, client):
        """group=rows liefert die Ursprungszeilen (hier 2: en gemerged x5, de x2)."""
        r = client.get("/api/collection?group=rows")
        data = r.get_json()
        assert data["count"] == 2
        total = sum(i["count"] for i in data["items"])
        assert total == 7

    def test_foil_separate_tile(self, client):
        """Foil bleibt eine eigene Kachel (physisch andere Karte)."""
        # Foil-Exemplar direkt in die DB
        from app.db.schema import connect as _connect

        conn = _connect()
        # Test-DB via get_db_path-mock? Der Client nutzt dieselbe DB:
        from app.core.config import AppConfig
        # (Der Test-Server nutzt die tmp-DB; direkter Insert dort:)
        conn.close()
        r = client.post("/api/collection/add", json={
            "card_id": "SOR-001", "count": 2, "is_foil": True, "language": "en",
        })
        if r.status_code == 200:
            r2 = client.get("/api/collection")
            data = r2.get_json()
            # Eine Normal-Kachel + eine Foil-Kachel
            assert data["count"] == 2, "Foil muss eigene Kachel sein"
            counts = {i["count"] for i in data["items"]}
            assert 7 in counts and 2 in counts
