"""TCGCSV (TCGplayer) price import — legal, login-free, public data.

TCGCSV (https://tcgcsv.com) republishes TCGplayer's market data as daily
cached JSON. Star Wars: Unlimited is category 79. Structure:

  /tcgplayer/79/groups            -> all SWU sets (groupId per set)
  /tcgplayer/79/<gid>/products   -> products incl. extendedData (Number!)
  /tcgplayer/79/<gid>/prices     -> market prices per productId (USD)

Products and prices join via productId. Cards are matched to our database
by (set mapping, normalized card number) + name verification, mirroring
the MKM importer's safety-first matching (our DB has two numbering
schemes, so a number-only match is unsafe).

Usage guidelines of tcgcsv: identify your application via User-Agent,
be polite with request rates (~1 req/set + delay).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger("swu_manager.tcgcsv")

BASE = "https://tcgcsv.com/tcgplayer"
SWU_CATEGORY = 79
USER_AGENT = "SWU-Card-Manager/1.0 (personal collection manager; github.com/Nilssteinmeyer/swu-card-manager)"
REQUEST_DELAY_S = 0.6  # courtesy delay between set downloads


# --- TCGplayer group name -> our set_id mapping --------------------------------
# TCGplayer names sets like "Homeworlds"; our DB uses codes (HMW, SOR...).
# Matching is done on the normalised display name of our sets table too,
# so unknown new sets still work if the names align.
_GROUP_TO_SET: dict[str, str] = {
    "spark of rebellion": "SOR",
    "shadows of the galaxy": "SHD",
    "twin suns": "TWI",
    "jump to lightspeed": "JTL",
    "legends of the force": "LOF",
    "homeworlds": "HMW",
    "ashes of the empire": "ASH",
    "sector and regional promos": "SEC",
    "icons 2027 edition": "ICONS",
}


def _http_get(url: str) -> dict[str, Any]:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=40)
    resp.raise_for_status()
    return resp.json()


@dataclass
class TcgProduct:
    product_id: int
    name: str
    number: str | None
    is_foil_variant: bool
    market_price: float | None = None
    low_price: float | None = None
    mid_price: float | None = None


def fetch_sets() -> list[dict[str, Any]]:
    """All SWU groups (sets) on TCGplayer."""
    data = _http_get(f"{BASE}/{SWU_CATEGORY}/groups")
    return data.get("results", [])


def fetch_group_products(group_id: int) -> list[TcgProduct]:
    """Products of one set with extendedData parsed (number, foil flag)."""
    data = _http_get(f"{BASE}/{SWU_CATEGORY}/{group_id}/products")
    products: list[TcgProduct] = []
    for p in data.get("results", []):
        eds = {e.get("name"): e.get("value") for e in (p.get("extendedData") or [])}
        number = eds.get("Number")
        if not number:
            continue  # sealed products / accessories have no card number
        name = p.get("name") or ""
        # strip the set prefix "Homeworlds: " if present
        clean = name.split(": ", 1)[-1] if ": " in name else name
        products.append(TcgProduct(
            product_id=p["productId"],
            name=clean,
            number=str(number),
            is_foil_variant="Foil" in name or "(Foil)" in name,
        ))
    return products


def fetch_group_prices(group_id: int) -> dict[int, dict[str, float]]:
    """Market prices of one set keyed by productId."""
    data = _http_get(f"{BASE}/{SWU_CATEGORY}/{group_id}/prices")
    out: dict[int, dict[str, float]] = {}
    for p in data.get("results", []):
        pid = p.get("productId")
        if pid is None:
            continue
        sub = p.get("subTypeName") or "Normal"
        key = "foil" if sub == "Foil" else "normal"
        entry = out.setdefault(pid, {})
        entry[f"market_{key}"] = p.get("marketPrice")
        entry[f"low_{key}"] = p.get("lowPrice")
        entry[f"mid_{key}"] = p.get("midPrice")
    return out


def normalise_number(raw: str) -> str:
    """'1/272' -> '1'; '545' -> '545'."""
    num = raw.split("/", 1)[0].strip()
    try:
        return str(int(num.lstrip("0") or "0"))
    except ValueError:
        return num


def match_products_to_cards(
    products: list[TcgProduct],
    cards: list[dict[str, Any]],
    set_id: str,
    name_threshold: int = 82,
) -> dict[str, dict[str, Any]]:
    """Match TCGplayer products to our card ids.

    Returns {card_id: {product_id, market_normal, market_foil, low_normal,
    low_foil, mid_normal, mid_foil}}.
    Matching key: normalised number within the set + name verification
    (partial ratio against EN and DE names).
    """
    from rapidfuzz import fuzz

    # our cards of this set, indexed by normalised number
    by_number: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        if (card.get("set_id") or "") != set_id:
            continue
        num = card.get("card_number") or ""
        try:
            num_norm = str(int(num.rstrip("F").lstrip("0") or "0"))
        except ValueError:
            num_norm = num.strip()
        by_number.setdefault(num_norm, []).append(card)

    # group products by normalised number; foil variants are matched to the
    # same card as their normal counterpart (Foil prices attach to the card)
    by_num_products: dict[str, list[TcgProduct]] = {}
    for prod in products:
        by_num_products.setdefault(normalise_number(prod.number), []).append(prod)

    result: dict[str, dict[str, Any]] = {}

    def _card_names(card: dict[str, Any]) -> list[str]:
        names = []
        for key in ("name", "name_de"):
            n = (card.get(key) or "").strip()
            if n:
                names.append(n)
        return names

    for num_norm, prods in by_num_products.items():
        candidates = by_number.get(num_norm)
        if not candidates:
            continue
        for card in candidates:
            card_names = _card_names(card)
            for prod in prods:
                if not card_names:
                    continue
                score = max(
                    fuzz.partial_ratio(prod.name.lower(), cn.lower())
                    for cn in card_names
                )
                if score < name_threshold:
                    continue
                entry = result.setdefault(card["card_id"], {
                    "product_id": prod.product_id,
                    "market_normal": None, "market_foil": None,
                    "low_normal": None, "low_foil": None,
                    "mid_normal": None, "mid_foil": None,
                })
                # attach prices by variant type
                # (foil products carry foil prices; normal carry normal)
    return result


def sync_all_sets(cards: list[dict[str, Any]], set_name_lookup: dict[str, str]) -> dict[str, Any]:
    """Fetch products+prices for every TCGplayer SWU set and match.

    set_name_lookup: our set_id -> normalised full_name (for group matching)
    Returns {card_id: price-dict} + stats.
    """
    groups = fetch_sets()
    our_sets_by_norm = {v.lower(): k for k, v in set_name_lookup.items()}
    matched: dict[str, dict[str, Any]] = {}
    stats = {"groups_seen": 0, "groups_matched": 0, "products": 0, "matched_cards": 0}

    from rapidfuzz import fuzz as _fuzz

    for group in groups:
        gname = (group.get("name") or "").strip()
        gnorm = re.sub(r"\s+", " ", gname.lower())
        # direct mapping first
        set_id = _GROUP_TO_SET.get(gnorm)
        if not set_id:
            # try matching against our sets' display names
            for norm_name, sid in our_sets_by_norm.items():
                if norm_name and _fuzz.partial_ratio(gnorm, norm_name) >= 90:
                    set_id = sid
                    break
        stats["groups_seen"] += 1
        if not set_id:
            continue
        stats["groups_matched"] += 1

        group_id = group["groupId"]
        try:
            products = fetch_group_products(group_id)
            prices = fetch_group_prices(group_id)
        except Exception as e:
            log.warning(f"TCGCSV fetch failed for group {gname}: {e}")
            continue
        stats["products"] += len(products)

        # attach prices to products
        for prod in products:
            p = prices.get(prod.product_id, {})
            if prod.is_foil_variant:
                prod.market_price = p.get("market_foil")
                prod.low_price = p.get("low_foil")
            else:
                prod.market_price = p.get("market_normal")
                prod.low_price = p.get("low_normal")

        # match (same-number products: normal first, then foil attaches to card)
        by_number: dict[str, list[TcgProduct]] = {}
        for prod in products:
            by_number.setdefault(normalise_number(prod.number), []).append(prod)

        card_numbers: dict[str, list[dict[str, Any]]] = {}
        for card in cards:
            if (card.get("set_id") or "") != set_id:
                continue
            num = card.get("card_number") or ""
            try:
                nn = str(int(num.rstrip("F").lstrip("0") or "0"))
            except ValueError:
                nn = num.strip()
            card_numbers.setdefault(nn, []).append(card)

        for nn, prods in by_number.items():
            cands = card_numbers.get(nn)
            if not cands:
                continue
            for card in cands:
                names = [(card.get("name") or "").strip(), (card.get("name_de") or "").strip()]
                names = [n for n in names if n]
                if not names:
                    continue
                for prod in prods:
                    score = max(_fuzz.partial_ratio(prod.name.lower(), n.lower()) for n in names)
                    if score < 82:
                        continue
                    entry = matched.setdefault(card["card_id"], {
                        "product_id": prod.product_id,
                        "market_normal": None, "market_foil": None,
                        "low_normal": None, "low_foil": None,
                        "mid_normal": None, "mid_foil": None,
                    })
                    if prod.is_foil_variant:
                        entry["market_foil"] = prod.market_price
                        entry["low_foil"] = prod.low_price
                    else:
                        entry["market_normal"] = prod.market_price
                        entry["low_normal"] = prod.low_price
        time.sleep(REQUEST_DELAY_S)

    stats["matched_cards"] = len(matched)
    return {"prices": matched, "stats": stats}
