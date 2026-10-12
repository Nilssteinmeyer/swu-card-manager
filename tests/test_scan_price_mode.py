"""Tests fuer den Scan-Modus 'Preis' (Scannen fuer Sammlung vs. Preis)."""
import base64
import io

import pytest


def _test_image() -> str:
    """Valides 1x1-PNG als data-URL (der /api/scan dekodiert das Bild VOR dem Engine-Aufruf)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (128, 128, 128)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class TestScanPriceMode:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        """Authenticated Flask test client on a temp database."""
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "test_price_mode.db")

        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "test_price_mode.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute(
            "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
            "VALUES ('SOR-001', 'SOR', '001', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '[\"Aggression\"]')"
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

    # ------------------------------------------------------------------
    # API: /api/card/<id> liefert Preis (Basis fuer den Preis-Modus)
    # ------------------------------------------------------------------
    def test_card_detail_contains_price(self, app_client):
        r = app_client.get("/api/card/SOR-001")
        assert r.status_code == 200
        data = r.get_json()
        assert "price" in data

    # ------------------------------------------------------------------
    # Preis-Modus: Bestaetigen fuegt NICHT zur Sammlung hinzu
    # ------------------------------------------------------------------
    def test_scan_confirm_price_mode_no_collection_add(self, app_client, monkeypatch):
        from app.web import app as app_mod

        class FakeEngine:
            def recognize(self, image_bytes, **kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(
                    card_id="SOR-001",
                    card_name="Darth Vader",
                    confidence=0.9,
                    method="clip",
                    ocr_text="",
                    processing_time_ms=10,
                    candidates=[{
                        "card_id": "SOR-001", "name": "Darth Vader",
                        "subtitle": "", "set_id": "SOR", "card_number": "001",
                        "score": 0.9,
                    }],
                )

        monkeypatch.setattr(app_mod, "_get_engine", lambda app: FakeEngine())

        r = app_client.post("/api/scan", json={"image": _test_image()})
        assert r.status_code == 200, r.get_data(as_text=True)
        scan = r.get_json()
        assert scan.get("scan_id")

        r2 = app_client.post("/api/scan/confirm", json={
            "scan_id": scan["scan_id"],
            "confirmed": True,
            "card_id": "SOR-001",
            "add_to_collection": False,   # <- Preis-Modus
            "is_foil": False,
            "quantity": 1,
            "language": "de",
            "photo": None,
        })
        assert r2.status_code == 200, r2.get_data(as_text=True)
        body = r2.get_json()
        assert body.get("added_to_collection") is False

        r3 = app_client.get("/api/collection?sort=name&dir=asc")
        items = r3.get_json().get("items", [])
        assert not any(i["card_id"] == "SOR-001" for i in items)

    # ------------------------------------------------------------------
    # Sammlungs-Modus: Bestaetigen fuegt hinzu (Regressionsschutz)
    # ------------------------------------------------------------------
    def test_scan_confirm_collection_mode_still_works(self, app_client, monkeypatch):
        from app.web import app as app_mod

        class FakeEngine:
            def recognize(self, image_bytes, **kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(
                    card_id="SOR-001",
                    card_name="Darth Vader",
                    confidence=0.8,
                    method="clip",
                    ocr_text="",
                    processing_time_ms=10,
                    candidates=[{
                        "card_id": "SOR-001", "name": "Darth Vader",
                        "subtitle": "", "set_id": "SOR", "card_number": "001",
                        "score": 0.8,
                    }],
                )

        monkeypatch.setattr(app_mod, "_get_engine", lambda app: FakeEngine())
        r = app_client.post("/api/scan", json={"image": _test_image()})
        scan = r.get_json()
        r2 = app_client.post("/api/scan/confirm", json={
            "scan_id": scan["scan_id"],
            "confirmed": True,
            "card_id": "SOR-001",
            "add_to_collection": True,    # <- Sammlungs-Modus
            "is_foil": False,
            "quantity": 2,
            "language": "de",
            "photo": None,
        })
        assert r2.status_code == 200, r2.get_data(as_text=True)
        assert r2.get_json().get("added_to_collection") is True

        r3 = app_client.get("/api/collection?sort=name&dir=asc")
        items = r3.get_json().get("items", [])
        match = [i for i in items if i["card_id"] == "SOR-001"]
        assert match and match[0]["count"] == 2

    # ------------------------------------------------------------------
    # Settings: scan_mode ist setzbar und wird validiert
    # ------------------------------------------------------------------
    def test_settings_scan_mode_roundtrip(self, app_client):
        r = app_client.post("/api/settings", json={"scan_mode": "price"})
        assert r.status_code == 200, r.get_data(as_text=True)
        r2 = app_client.get("/api/settings")
        assert r2.get_json().get("scan_mode") == "price"

        # Ungueltiger Modus: API akzeptiert nur collection|price
        r3 = app_client.post("/api/settings", json={"scan_mode": "unsinn"})
        assert r3.status_code in (200, 400)
        r4 = app_client.get("/api/settings")
        assert r4.get_json().get("scan_mode") in ("collection", "price", None)
