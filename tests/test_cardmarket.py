"""Tests for the Cardmarket price import (CSV parsing + matching + DB)."""
import io
import gzip
import sqlite3

import pytest

from app.db.schema import init_database, connect
from app.integrations import cardmarket as mkm


PRICE_CSV = """idProduct;Avg. Sell Price;Low Price;Trend Price;German Pro Low;Suggested Price;Foil Sell;Foil Low;Foil Trend;Low Price Ex+;AVG1;AVG7;AVG30;Foil AVG1;Foil AVG7;Foil AVG30
401001;0.15;0.05;0.12;0.08;0.13;0.35;0.20;0.30;0.06;0.11;0.12;0.13;0.31;0.29;0.30
401002;2.50;1.80;2.20;2.00;2.30;8.00;5.50;7.20;1.90;2.10;2.20;2.25;7.10;7.20;7.15
401003;15.00;9.00;12.50;11.00;13.00;45.00;30.00;38.00;10.00;12.00;12.50;12.80;37.50;38.00;38.20
"""

PRODUCT_CSV = """idProduct;Name;Category;Number;Rarity;Website;Expansion;idExpansion;idMetaproduct
401001;Luke Skywalker, Faithful Friend;SWU Single;010;Legendary;/en/SWU/Products/Singles/Spark-of-Rebellion/Luke-Skywalker-Faithful-Friend;Spark of Rebellion;1;500001
401002;Darth Vader, Dark Lord of the Sith;SWU Single;029;Legendary;/en/SWU/Products/Singles/Spark-of-Rebellion/Darth-Vader-Dark-Lord;Spark of Rebellion;1;500002
401003;Grand Inquisitor, Power of the Dark Side;SWU Single;077;Legendary;/en/SWU/Products/Singles/Shadows-of-the-Galaxy/Grand-Inquisitor;Shadows of the Galaxy;2;500003
401004;Sealed Booster Box;SWU Box;;;/en/SWU/Products/Sealed/Booster-Box;Spark of Rebellion;1;
"""


@pytest.fixture()
def db(tmp_path):
    from pathlib import Path

    conn = connect(Path(tmp_path) / "test.db")
    init_database(conn)
    yield conn
    conn.close()


def test_parse_price_guide_plain():
    rows = mkm.parse_price_guide(PRICE_CSV.encode())
    assert len(rows) == 3
    assert rows[0].idProduct == 401001
    assert rows[0].trend == 0.12
    assert rows[1].foil_trend == 7.20
    assert rows[2].avg30 == 12.80


def test_parse_price_guide_gzipped():
    gz = gzip.compress(PRICE_CSV.encode())
    rows = mkm.parse_price_guide(gz)
    assert len(rows) == 3
    assert rows[0].idProduct == 401001


def test_parse_price_guide_empty():
    rows = mkm.parse_price_guide(b"")
    assert rows == []


def test_parse_product_catalogue():
    products = mkm.parse_product_catalogue(PRODUCT_CSV.encode())
    # Sealed box must be filtered out (category without "Single")
    assert len(products) == 3
    assert products[0].name == "Luke Skywalker, Faithful Friend"
    assert products[0].expansion == "Spark of Rebellion"
    assert products[2].number == "077"


def test_match_products_to_cards(db):
    # Insert sample cards (as our DB schema expects)
    cards = [
        {"card_id": "SOR-010", "set_id": "SOR", "card_number": "010", "name": "Luke Skywalker"},
        {"card_id": "SOR-029", "set_id": "SOR", "card_number": "029", "name": "Darth Vader"},
        {"card_id": "SHD-077", "set_id": "SHD", "card_number": "077", "name": "Grand Inquisitor"},
    ]
    products = mkm.parse_product_catalogue(PRODUCT_CSV.encode())
    mapping = mkm.match_products_to_cards(products, cards)
    assert mapping["SOR-010"] == 401001
    assert mapping["SOR-029"] == 401002
    assert mapping["SHD-077"] == 401003


def test_number_normalisation():
    cards = [
        {"card_id": "SOR-010", "set_id": "SOR", "card_number": "010", "name": "X"},
        {"card_id": "SOR-5", "set_id": "SOR", "card_number": "5", "name": "Y"},
    ]
    csv = """idProduct;Name;Category;Number;Expansion
900001;X;SWU Single;10;Spark of Rebellion
900002;Y;SWU Single;005;Spark of Rebellion
"""
    products = mkm.parse_product_catalogue(csv.encode())
    mapping = mkm.match_products_to_cards(products, cards)
    assert mapping["SOR-010"] == 900001
    assert mapping["SOR-5"] == 900002


def test_name_verification_blocks_wrong_number_match():
    """SWU-DB SOR-010 is 'Darth Vader' but official API SOR-10 is 'R2-D2'.
    A product for number 10 with a Vader name must NOT map to SOR-10 (R2-D2)."""
    cards = [
        {"card_id": "SOR-010", "set_id": "SOR", "card_number": "010", "name": "Darth Vader"},
        {"card_id": "SOR-10", "set_id": "SOR", "card_number": "10", "name": "R2-D2"},
    ]
    csv = """idProduct;Name;Category;Number;Expansion
910001;Darth Vader, Dark Lord of the Sith;SWU Single;10;Spark of Rebellion
"""
    products = mkm.parse_product_catalogue(csv.encode())
    mapping = mkm.match_products_to_cards(products, cards)
    # Vader product maps to SOR-010 (name matches), NOT to SOR-10 (R2-D2)
    assert mapping.get("SOR-010") == 910001
    assert "SOR-10" not in mapping


def test_name_verification_uses_german_name():
    cards = [
        {"card_id": "SHD-077", "set_id": "SHD", "card_number": "077", "name": "Grand Inquisitor", "name_de": "Großinquisitor"},
    ]
    csv = """idProduct;Name;Category;Number;Expansion
920001;Großinquisitor, Macht der Dunklen Seite;SWU Single;077;Shadows of the Galaxy
"""
    products = mkm.parse_product_catalogue(csv.encode())
    mapping = mkm.match_products_to_cards(products, cards)
    assert mapping.get("SHD-077") == 920001


def test_price_roundtrip_db(db):
    from app.db import repository as repo

    rows = mkm.parse_price_guide(PRICE_CSV.encode())
    for row in rows:
        repo.upsert_price(db, {
            "idProduct": row.idProduct,
            "trend": row.trend,
            "low": row.low,
            "low_ex": row.low_ex,
            "avg_sell": row.avg_sell,
            "foil_trend": row.foil_trend,
            "foil_low": row.foil_low,
            "foil_sell": row.foil_sell,
            "avg30": row.avg30,
        })
    db.commit()

    stats = repo.get_price_stats(db)
    assert stats["price_rows"] == 3


def test_collection_value_foil_aware(db):
    from app.db import repository as repo

    # Setup: set + cards first (FK), then products mapping, then collection
    db.execute(
        "INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')"
    )
    db.execute(
        "INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('SOR-010', 'SOR', '010', 'Luke')"
    )
    db.execute(
        "INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('SOR-029', 'SOR', '029', 'Vader')"
    )
    # Both id variants of the same card map to one product
    repo.upsert_card_mkm_map(db, "SOR-010", 401001)
    repo.upsert_card_mkm_map(db, "SOR-10", 401001)
    repo.upsert_card_mkm_map(db, "SOR-029", 401002)
    # Prices: Luke trend 0.12 / foil 0.30; Vader trend 2.20 / foil 7.20
    for pid, trend, foil in ((401001, 0.12, 0.30), (401002, 2.20, 7.20)):
        repo.upsert_price(db, {"idProduct": pid, "trend": trend, "foil_trend": foil})
    # Collection: 2x Luke normal, 1x Vader foil, 1x Vader normal
    db.execute("INSERT INTO collection (card_id, count, condition, language, variant, is_foil) VALUES ('SOR-010', 2, 'NM', 'en', 'Normal', 0)")
    db.execute("INSERT INTO collection (card_id, count, condition, language, variant, is_foil) VALUES ('SOR-029', 1, 'NM', 'en', 'Normal', 1)")
    db.execute("INSERT INTO collection (card_id, count, condition, language, variant, is_foil) VALUES ('SOR-029', 1, 'NM', 'en', 'Normal', 0)")
    db.commit()

    value = repo.get_collection_value(db)
    # 2*0.12 + 1*7.20 (foil) + 1*2.20 (normal) = 0.24 + 7.20 + 2.20 = 9.64
    assert value["total_value"] == pytest.approx(9.64)
    assert value["priced_count"] == 4
    assert value["total_count"] == 4
    assert value["entry_count"] == 3

    # Price lookup works for BOTH id variants
    p1 = repo.get_price_for_card(db, "SOR-010")
    p2 = repo.get_price_for_card(db, "SOR-10")
    assert p1 is not None and p1["trend"] == 0.12
    assert p2 is not None and p2["trend"] == 0.12
