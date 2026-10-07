"""Cardmarket integration.

Two data paths:

1. **Price Guide / Product Catalogue import (no API account needed)**
   Cardmarket publishes daily price guides and a product catalogue as CSV
   downloads from their website (https://www.cardmarket.com/en/Star-Wars-Unlimited/Data/Price-Guide).
   The user downloads these files in their browser (as a logged-in MKM user)
   and uploads them here. We parse, match against our card database and store
   prices.

2. **MKM API v2.0 (OAuth 1.0a, professional sellers)**
   Optional. If the user has API credentials (App Token/Secret + Access
   Token/Secret from their MKM profile), we can additionally:
   - export wishlists to their MKM wantslist
   - list cards for sale in their MKM stock
   - sync stock changes

   See https://apiv2.cardmarket.com/ws/documentation/API:Auth_Overview

Legal note: the CSV downloads are provided by Cardmarket itself for all its
users. We do NOT scrape the marketplace. The API integration is only used for
the user's own account (dedicated app), which is the officially supported
path.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("swu_manager.cardmarket")

# --- SWU set code mapping: our set_id -> MKM expansion name ----------------
# MKM uses the English expansion name; our DB uses short codes (SOR, SHD...).
# The product catalogue CSV gives us expansion names, so we mainly need the
# reverse mapping for verification.

# Price guide CSV columns (as documented by MKM)
PRICE_COLUMNS = [
    "idProduct",
    "Avg. Sell Price", "Low Price", "Trend Price",
    "German Pro Low", "Suggested Price",
    "Foil Sell", "Foil Low", "Foil Trend",
    "Low Price Ex+", "AVG1", "AVG7", "AVG30",
    "Foil AVG1", "Foil AVG7", "Foil AVG30",
]

# Product catalogue CSV columns (SWU singles)
PRODUCT_COLUMNS = [
    "idProduct", "Name", "Category", "Number", "Rarity",
    "Website", "Expansion", "idExpansion", "idMetaproduct",
    "ProductName_EN", "ProductName_DE", "ProductName_FR", "Reprint",
]


@dataclass
class PriceGuideRow:
    idProduct: int
    trend: float | None = None
    low: float | None = None
    low_ex: float | None = None
    avg_sell: float | None = None
    foil_trend: float | None = None
    foil_low: float | None = None
    foil_sell: float | None = None
    avg30: float | None = None
    raw: dict[str, str] = field(default_factory=dict)


def _to_float(value: str | None) -> float | None:
    """MKM CSVs use dots as decimal separator; empty string -> None."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_price_guide(path_or_bytes: Path | bytes) -> list[PriceGuideRow]:
    """Parse a MKM price guide CSV (possibly gzipped) into rows."""
    raw: bytes
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        raw = Path(path_or_bytes).read_bytes()

    # MKM price guides are distributed gzipped (.csv.gz); handle both.
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows: list[PriceGuideRow] = []
    for r in reader:
        # MKM CSVs sometimes use different column names — be tolerant
        def col(*names: str) -> str | None:
            for n in names:
                for key in r:
                    if key and key.strip().lower() == n.lower():
                        return r[key]
            return None

        pid_raw = col("idProduct", "idproduct", "productid")
        if not pid_raw or not pid_raw.strip():
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        row = PriceGuideRow(
            idProduct=pid,
            trend=_to_float(col("Trend Price", "Trend")),
            low=_to_float(col("Low Price", "Low")),
            low_ex=_to_float(col("Low Price Ex+", "LOWEX")),
            avg_sell=_to_float(col("Avg. Sell Price", "AVG")),
            foil_trend=_to_float(col("Foil Trend", "TRENDFOIL")),
            foil_low=_to_float(col("Foil Low", "LOWFOIL")),
            foil_sell=_to_float(col("Foil Sell", "SELLFOIL")),
            avg30=_to_float(col("AVG30")),
            raw={k: v for k, v in r.items() if k},
        )
        rows.append(row)
    return rows


@dataclass
class MkmProduct:
    idProduct: int
    name: str
    category: str
    number: str
    rarity: str | None
    website: str | None
    expansion: str
    metaproduct_id: int | None
    lang_names: dict[str, str] = field(default_factory=dict)


def parse_product_catalogue(path_or_bytes: Path | bytes) -> list[MkmProduct]:
    """Parse the MKM product catalogue CSV (SWU singles) into products."""
    raw: bytes
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        raw = Path(path_or_bytes).read_bytes()

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    text = raw.decode("utf-8", errors="replace")
    # MKM catalogue files use semicolon delimiter
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    products: list[MkmProduct] = []
    for r in reader:
        def col(*names: str) -> str | None:
            for n in names:
                for key in r:
                    if key and key.strip().lower() == n.lower():
                        return r[key]
            return None

        pid_raw = col("idProduct", "idproduct")
        name = (col("Name", "enName") or "").strip()
        category = (col("Category", "categoryName") or "").strip()
        expansion = (col("Expansion", "expansionName") or "").strip()
        if not pid_raw or not name:
            continue
        if "Single" not in category:
            continue  # skip sealed products / accessories
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        meta_raw = col("idMetaproduct", "idmetaproduct")
        try:
            meta_id = int(meta_raw) if meta_raw else None
        except ValueError:
            meta_id = None
        p = MkmProduct(
            idProduct=pid,
            name=name,
            category=category,
            number=(col("Number") or "").strip(),
            rarity=col("Rarity"),
            website=col("Website", "website"),
            expansion=expansion,
            metaproduct_id=meta_id,
        )
        # localised names if present
        for lang_code, cname in (("EN", "ProductName_EN"), ("DE", "ProductName_DE")):
            v = col(cname)
            if v:
                p.lang_names[lang_code] = v
        products.append(p)
    return products


# --- Set name normalisation for matching -------------------------------------

_SET_NAME_MAP = {
    # our set_id -> MKM expansion name (English)
    "SOR": "Spark of Rebellion",
    "SHD": "Shadows of the Galaxy",
    "TWI": "Twin Suns",
    "JTL": "Jump to Lightspeed",
    "LOF": "Legends of the Force",
    "SEC": "Sector",
    # German market: MKM also lists German-language sets under same expansion
}


def normalise_set_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def match_products_to_cards(
    products: list[MkmProduct],
    cards: list[dict[str, Any]],
    name_threshold: int = 85,
) -> dict[str, int]:
    """Match MKM products to our card ids.

    Returns mapping: card_id -> idProduct.
    Cards carry: card_id ("SOR-010"), set_id ("SOR"), card_number ("010"),
    name (EN), name_de.

    Strategy (set_id, number) lookup + NAME VERIFICATION:
    our cards table contains rows from two data sources whose numbering
    schemes differ (SWU-DB: SOR-010 = Darth Vader; official API: SOR-10 =
    R2-D2). A pure number match would silently map wrong cards. We therefore
    require a fuzzy name similarity >= threshold between the MKM product
    name and the card name before accepting a match.
    """
    from rapidfuzz import fuzz

    # 1) expansion name -> set_id
    exp_to_set: dict[str, str] = {}
    for set_id, exp_name in _SET_NAME_MAP.items():
        exp_to_set[normalise_set_name(exp_name)] = set_id

    # Build product lookup by (set_id, number)
    by_number: dict[tuple[str, str], list[MkmProduct]] = {}
    for p in products:
        set_key = exp_to_set.get(normalise_set_name(p.expansion))
        if set_key and p.number:
            try:
                num_norm = str(int(p.number.lstrip("0") or "0"))
            except ValueError:
                num_norm = p.number.strip()
            by_number.setdefault((set_key, num_norm), []).append(p)

    def _card_names(card: dict[str, Any]) -> list[str]:
        names = []
        for key in ("name", "name_de"):
            n = (card.get(key) or "").strip()
            if n:
                names.append(n)
        return names

    result: dict[str, int] = {}
    unmatched = 0
    for card in cards:
        card_id = card.get("card_id")
        set_id = card.get("set_id") or (card_id.split("-")[0] if card_id else None)
        number = card.get("card_number") or (card_id.split("-")[1] if card_id and "-" in card_id else None)
        if not set_id or not number:
            unmatched += 1
            continue
        try:
            num_norm = str(int(number.lstrip("0") or "0"))
        except ValueError:
            num_norm = number.strip()
        candidates = by_number.get((set_id, num_norm))
        if not candidates:
            unmatched += 1
            continue
        card_names = _card_names(card)
        best_product: MkmProduct | None = None
        best_score = 0
        for prod in candidates:
            if not card_names:
                # no name to verify — accept the only candidate
                best_product = prod
                break
            score = max(
                fuzz.partial_ratio(cn.lower(), prod.name.lower())
                for cn in card_names
            )
            if score > best_score:
                best_score = score
                best_product = prod
        if best_product is not None and (not card_names or best_score >= name_threshold):
            result[card_id] = best_product.idProduct
        else:
            unmatched += 1
    log.info(
        f"MKM match: {len(result)} matched, {unmatched} unmatched of {len(cards)} cards"
    )
    return result
