"""Tests: Merkliste v4 — Varianten, Duplikat-Merge, Zielpreis, Bulk, Alerts, Isolation."""
import pytest


class TestWishlistV4:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "wl_v4.db")

        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "wl_v4.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        for cid, name in [("SOR-001", "Darth Vader"), ("SOR-002", "Luke Skywalker"), ("SOR-003", "Wookiee Guerilla")]:
            conn.execute(
                "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
                f"VALUES ('{cid}', 'SOR', '{cid[-3:]}', '{name}', '{name}', 'Unit', 'Rare', '[\"Aggression\"]')"
            )
        # TCGplayer-Preise (USD) + ein Cardmarket-Preis fuer SOR-003
        conn.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, market_foil, low_normal, low_foil, updated_at) "
            "VALUES ('SOR-001', 999, 0.50, 2.50, 0.40, 2.00, '2026-10-13')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, market_foil, low_normal, low_foil, updated_at) "
            "VALUES ('SOR-002', 998, 1.20, NULL, 1.00, NULL, '2026-10-13')"
        )
        conn.execute("INSERT OR IGNORE INTO card_mkm_map (card_id, idProduct) VALUES ('SOR-003', 777)")
        conn.execute(
            "INSERT OR REPLACE INTO card_prices (idProduct, trend, foil_trend, low, updated_at) "
            "VALUES (777, 3.20, 8.50, 3.00, '2026-10-13')"
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
            "username": "wltester", "email": "wl@example.com", "password": "testpass123",
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        yield client

    # ---- Add mit Varianten ----
    def test_add_with_foil_and_language(self, app_client):
        r = app_client.post("/api/wishlist/add", json={
            "card_id": "SOR-001", "count": 2, "is_foil": True, "language": "en", "priority": 1,
        })
        assert r.status_code == 200, r.get_data(as_text=True)
        data = r.get_json()
        assert data["added"] is True
        r2 = app_client.get("/api/wishlist")
        item = r2.get_json()["items"][0]
        assert item["is_foil"] == 1
        assert item["language"] == "en"
        assert item["priority"] == 1
        # Foil-Preis aus TCGplayer (USD), price_at_add gesnapshottet
        assert item["price"] == 2.50
        assert item["currency"] == "USD"
        assert item["price_source"] == "tcgplayer"
        assert item["price_at_add"] == 2.50

    def test_add_invalid_language_falls_back(self, app_client):
        r = app_client.post("/api/wishlist/add", json={"card_id": "SOR-001", "language": "fr"})
        assert r.status_code == 200
        r2 = app_client.get("/api/wishlist")
        assert r2.get_json()["items"][0]["language"] == "de"

    # ---- Duplikat-Merge ----
    def test_duplicate_merges_count(self, app_client):
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001", "count": 1})
        r = app_client.post("/api/wishlist/add", json={"card_id": "SOR-001", "count": 2})
        data = r.get_json()
        assert data["merged"] is True
        assert data["count"] == 3
        # Andere Variante (Foil) bleibt getrennt
        r2 = app_client.post("/api/wishlist/add", json={"card_id": "SOR-001", "count": 1, "is_foil": True})
        assert r2.get_json().get("merged") is None or r2.get_json()["added"] is True
        r3 = app_client.get("/api/wishlist")
        assert r3.get_json()["count"] == 2, "Foil-Eintrag muss separat existieren"

    # ---- Dual-Source-Preise ----
    def test_mkm_price_preferred(self, app_client):
        """SOR-003 hat MKM (EUR) + keinen TCG-Preis -> EUR/Quelle cardmarket."""
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-003"})
        item = app_client.get("/api/wishlist").get_json()["items"][0]
        assert item["price"] == 3.20
        assert item["currency"] == "EUR"
        assert item["price_source"] == "cardmarket"

    def test_tcg_fallback_for_foil_without_foil_price(self, app_client):
        """SOR-002 Foil hat keinen market_foil -> Fallback auf Normal-Preis."""
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-002", "is_foil": True})
        item = app_client.get("/api/wishlist").get_json()["items"][0]
        assert item["price"] == 1.20, "Foil ohne Foil-Preis faellt auf Normal-Preis zurueck"

    # ---- Update + Zielpreis ----
    def test_update_target_price_and_status(self, app_client):
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001", "count": 1})
        wl_id = app_client.get("/api/wishlist").get_json()["items"][0]["wishlist_id"]
        # Zielpreis unter aktuellem Preis (0.50 USD)
        r = app_client.post("/api/wishlist/update", json={"wishlist_id": wl_id, "target_price": 0.40})
        assert r.status_code == 200 and r.get_json()["updated"] is True
        item = app_client.get("/api/wishlist").get_json()["items"][0]
        assert item["target_status"] == "above_target"
        assert item["target_distance_pct"] == 25.0
        # Zielpreis senken -> under_target
        app_client.post("/api/wishlist/update", json={"wishlist_id": wl_id, "target_price": 0.60})
        item = app_client.get("/api/wishlist").get_json()["items"][0]
        assert item["target_status"] == "under_target"

    def test_update_count_priority_notes(self, app_client):
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001"})
        wl_id = app_client.get("/api/wishlist").get_json()["items"][0]["wishlist_id"]
        app_client.post("/api/wishlist/update", json={
            "wishlist_id": wl_id, "count": 5, "priority": 1, "notes": "nur deutsch",
        })
        item = app_client.get("/api/wishlist").get_json()["items"][0]
        assert item["count"] == 5
        assert item["priority"] == 1
        assert item["notes"] == "nur deutsch"

    def test_update_household_isolation(self, app_client):
        """Fremde wishlist_id -> 404."""
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001"})
        r = app_client.post("/api/wishlist/update", json={"wishlist_id": 99999, "count": 2})
        assert r.status_code == 404

    # ---- Summary ----
    def test_summary(self, app_client):
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001"})       # 0.50 USD
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-003"})       # 3.20 EUR
        s = app_client.get("/api/wishlist/summary").get_json()
        assert s["count"] == 2
        assert s["total_usd"] == 0.50
        assert s["total_eur"] == 3.20
        assert s["priced_count"] == 2
        assert s["unpriced_count"] == 0

    # ---- Bulk (Deck-Bruecke) ----
    def test_deck_missing_bulk(self, app_client):
        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001"})
        r = app_client.post("/api/wishlist/deck-missing", json={"card_ids": ["SOR-001", "SOR-002", "SOR-003"]})
        data = r.get_json()
        assert data["added"] == 2, "SOR-001 existiert bereits -> 2 neu"
        assert data["skipped"] == 1
        assert app_client.get("/api/wishlist").get_json()["count"] == 3

    # ---- Alerts ----
    def test_alerts_flow(self, app_client):
        from app.db.schema import connect as _connect

        app_client.post("/api/wishlist/add", json={"card_id": "SOR-001"})
        wl_id = app_client.get("/api/wishlist").get_json()["items"][0]["wishlist_id"]
        conn = _connect()
        # Da get_db_path monkeypatched ist, müssen wir denselben Pfad nutzen
        conn.close()

        alerts = app_client.get("/api/wishlist/alerts").get_json()
        assert alerts["alerts"] == []
        assert alerts["unacknowledged"] == 0

    # ---- Ungueltige Eingaben ----
    def test_add_unknown_card_404(self, app_client):
        r = app_client.post("/api/wishlist/add", json={"card_id": "XXX-999"})
        assert r.status_code == 404

    def test_add_missing_card_id_400(self, app_client):
        r = app_client.post("/api/wishlist/add", json={"count": 1})
        assert r.status_code == 400

    def test_deck_missing_requires_list(self, app_client):
        r = app_client.post("/api/wishlist/deck-missing", json={"card_ids": "SOR-001"})
        assert r.status_code == 400
