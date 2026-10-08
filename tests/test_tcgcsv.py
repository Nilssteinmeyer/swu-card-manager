"""Tests for TCGCSV price integration (parsing, matching, DB roundtrip)."""
from pathlib import Path

import pytest

from app.db.schema import connect, init_database
from app.integrations import tcgcsv
from app.db import repository as repo


def test_normalise_number():
    assert tcgcsv.normalise_number("1/272") == "1"
    assert tcgcsv.normalise_number("545") == "545"
    assert tcgcsv.normalise_number("010") == "10"
    assert tcgcsv.normalise_number("0") == "0"


def test_user_agent_identifies_app():
    assert "SWU-Card-Manager" in tcgcsv.USER_AGENT
    assert tcgcsv.SWU_CATEGORY == 79
    assert tcgcsv.REQUEST_DELAY_S >= 0.5  # politeness


def test_group_to_set_mapping():
    assert tcgcsv._GROUP_TO_SET["homeworlds"] == "HMW"
    assert tcgcsv._GROUP_TO_SET["spark of rebellion"] == "SOR"


@pytest.fixture()
def db(tmp_path):
    conn = connect(Path(tmp_path) / "test.db")
    init_database(conn)
    conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('HMW', 'Homeworlds')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name, name_de) VALUES ('HMW-160', 'HMW', '160', 'Noxious Refinery', 'Giftige Raffinerie')")
    conn.execute("INSERT INTO cards (card_id, set_id, card_number, name) VALUES ('HMW-161', 'HMW', '161', 'Some Other Card')")
    conn.commit()
    yield conn
    conn.close()


def test_price_upsert_and_value(db):
    repo.upsert_tcg_price(db, "HMW-160", {
        "product_id": 719978, "market_normal": 0.25, "market_foil": 1.20,
        "low_normal": 0.10, "low_foil": 0.80, "mid_normal": 0.30, "mid_foil": 1.30,
    })
    db.commit()

    stats = repo.get_tcg_stats(db)
    assert stats["price_rows"] == 1

    # collection: 2x normal + 1x foil -> 2*0.25 + 1*1.20 = 1.70
    db.execute("INSERT INTO collection (card_id, count, is_foil, household_id) VALUES ('HMW-160', 2, 0, 1)")
    db.execute("INSERT INTO collection (card_id, count, is_foil, household_id) VALUES ('HMW-160', 1, 1, 1)")
    db.execute("INSERT INTO collection (card_id, count, is_foil, household_id) VALUES ('HMW-161', 5, 0, 1)")  # unpriced
    db.commit()
    value = repo.get_collection_value_usd(db)
    assert value["total_value"] == pytest.approx(1.70)
    assert value["priced_count"] == 3
    assert value["total_count"] == 8
    assert value["currency"] == "USD"


def test_price_upsert_idempotent(db):
    repo.upsert_tcg_price(db, "HMW-160", {"product_id": 1, "market_normal": 0.5})
    repo.upsert_tcg_price(db, "HMW-160", {"product_id": 1, "market_normal": 0.9})
    db.commit()
    stats = repo.get_tcg_stats(db)
    assert stats["price_rows"] == 1  # no duplicate

    cur = db.execute("SELECT market_normal FROM tcg_prices WHERE card_id='HMW-160'")
    assert cur.fetchone()["market_normal"] == 0.9  # updated


def test_foil_products_map_to_same_card():
    """Foil variants carry their own prices but attach to the same card id."""
    products = [
        tcgcsv.TcgProduct(product_id=100, name="Noxious Refinery", number="160/272", is_foil_variant=False),
        tcgcsv.TcgProduct(product_id=101, name="Noxious Refinery (Hyperspace Foil)", number="545", is_foil_variant=True),
    ]
    cards = [
        {"card_id": "HMW-160", "set_id": "HMW", "card_number": "160", "name": "Noxious Refinery", "name_de": "Giftige Raffinerie"},
    ]
    # The number '545' is a Hyperspace number that should NOT match card 160.
    # The normal product must match by number+name.
    result = tcgcsv.match_products_to_cards(products, cards, "HMW")
    assert "HMW-160" in result
    assert result["HMW-160"]["product_id"] in (100, 101)


def test_wrong_name_blocks_match():
    """Number matches but name does not -> no match (numbering-scheme safety)."""
    products = [
        tcgcsv.TcgProduct(product_id=200, name="Completely Different Card", number="160/272", is_foil_variant=False),
    ]
    cards = [
        {"card_id": "HMW-160", "set_id": "HMW", "card_number": "160", "name": "Noxious Refinery"},
    ]
    result = tcgcsv.match_products_to_cards(products, cards, "HMW")
    assert "HMW-160" not in result
