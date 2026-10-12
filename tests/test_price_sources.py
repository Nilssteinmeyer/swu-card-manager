"""Tests: Preis-Modus zeigt beide Quellen (Cardmarket + TCGplayer)."""
import pytest


class TestPriceSources:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "price_src.db")

        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "price_src.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute(
            "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
            "VALUES ('SOR-001', 'SOR', '001', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '[\"Aggression\"]')"
        )
        # TCGplayer-Preis fuer SOR-001 (KEIN Cardmarket-Preis -> Realitaet dieser DB)
        conn.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, market_foil, low_normal, low_foil, updated_at) "
            "VALUES ('SOR-001', 999, 12.34, 45.67, 10.0, 40.0, '2026-10-12')"
        )
        conn.commit()
        conn.close()

        from app.web.app import create_app
        from app.core.config import AppConfig

        AppConfig.reset()
        app = create_app()
        app.config["TESTING"] = True
        app.config["SESSION_COOKIE_SECURE"] = False
        client = app.test_client()
        resp = client.post("/api/auth/register", json={
            "username": "tester", "email": "tester@example.com", "password": "testpass123",
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        yield client

    def test_card_detail_has_tcg_price(self, app_client):
        """ohne Cardmarket-Import muss TCGplayer als Quelle erscheinen."""
        r = app_client.get("/api/card/SOR-001")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("price") is None, "kein MKM-Preis in dieser DB erwartet"
        tcg = data.get("price_tcg")
        assert tcg, "price_tcg fehlt im Detail-Endpoint"
        assert tcg["market_normal"] == 12.34
        assert tcg["market_foil"] == 45.67
        assert tcg["low_normal"] == 10.0

    def test_price_endpoint_prefers_mkm(self, app_client):
        """Der /price-Endpoint liefert MKM oder TCG als Fallback."""
        r = app_client.get("/api/card/SOR-001/price")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("source") in ("cardmarket", "tcgplayer")
        assert data.get("source") == "tcgplayer", "ohne MKM-Daten ist TCG die Quelle"
        assert data["trend"] == 12.34
        assert data["foil_trend"] == 45.67

    def test_price_endpoint_404_when_nothing(self, app_client):
        r = app_client.get("/api/card/SOR-999/price")
        assert r.status_code == 404
