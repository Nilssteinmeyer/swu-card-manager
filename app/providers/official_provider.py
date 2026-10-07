"""
Official Star Wars Unlimited data provider.
API: https://admin.starwarsunlimited.com/api
  GET /cards?pagination[pageSize]=100&populate=...&locale=de|en
  Single endpoint returning paginated cards; sets are extracted from the
  expansion relation on each card.

The API is a public Strapi backend. It does not require authentication.
We add a 0.3s delay between requests as a courtesy and to be a good citizen.

Bilingual: every set/card is fetched in both German (de) and English (en).
The English representation is the canonical record (matches the existing
SWUDBProvider contract: Set codes like "SOR" are always English). German
names are merged into the same record so the repository can store them
alongside the English data.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

import requests

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("provider.official")


# ---------------------------------------------------------------------------
# Interface (mirrors app.providers.swudb_provider.CardDataProvider)
# ---------------------------------------------------------------------------

class CardDataProvider(Protocol):
    """Interface for pluggable card data sources."""

    def get_sets(self) -> list[dict[str, Any]]: ...
    def get_cards(self, set_id: str) -> list[dict[str, Any]]: ...
    def get_card_image(self, card_id: str, set_id: str, number: str, face: str = "front") -> bytes | None: ...


# ---------------------------------------------------------------------------
# Helpers — navigate Strapi relation payloads defensively
# ---------------------------------------------------------------------------

def _rel_names(relation: Any) -> list[str]:
    """Extract the 'name' attribute from every entry of a to-many relation.

    Strapi wraps to-many relations as {"data": [ {id, attributes: {name, ...}}, ... ]}.
    Returns an empty list when the relation is absent or malformed.
    """
    if not relation or not isinstance(relation, dict):
        return []
    data = relation.get("data")
    if isinstance(data, list):
        return [
            (d.get("attributes") or {}).get("name", "")
            for d in data
            if isinstance(d, dict)
        ]
    # Single to-one relation masquerading as a list field — treat uniformly.
    if isinstance(data, dict):
        return [(data.get("attributes") or {}).get("name", "")]
    return []


def _rel_attr(relation: Any, key: str, default: Any = "") -> Any:
    """Extract a single attribute from a to-one relation.

    Strapi wraps to-one relations as {"data": {id, attributes: {...}}}.
    Returns the default when absent.
    """
    if not relation or not isinstance(relation, dict):
        return default
    data = relation.get("data")
    if isinstance(data, dict):
        return (data.get("attributes") or {}).get(key, default)
    return default


def _image_url(art_relation: Any, preferred_format: str = "card") -> str:
    """Extract the best image URL from an artFront/artBack relation.

    Prefers the 'card' format (400px, good for display + matching),
    falls back to the original 'url', then to the 'thumbnail' format.
    Returns "" when no usable URL exists.
    """
    data = (art_relation or {}).get("data") if isinstance(art_relation, dict) else None
    if not data:
        return ""
    attrs = data.get("attributes") or {}
    formats = attrs.get("formats") or {}
    # Prefer the 'card' format, then the original, then thumbnail, then xsmall.
    for fmt in (preferred_format, "thumbnail", "xsmall", "xxsmall", "xxxsmall"):
        if fmt in formats and isinstance(formats[fmt], dict):
            url = formats[fmt].get("url")
            if url:
                return url
    # Fall back to the original upload URL (highest resolution).
    return attrs.get("url", "") or ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

POPULATE_FIELDS = (
    "aspects,traits,type,rarity,arenas,expansion,variantTypes,"
    "artFront,artBack,variants,variantOf"
)
"""Comma-separated list of relations to populate in every card request."""

DEFAULT_PAGE_SIZE = 100  # API maximum
DEFAULT_REQUEST_DELAY = 0.3  # seconds between requests
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30


class OfficialProvider:
    """Data provider using the official admin.starwarsunlimited.com API.

    Implements the CardDataProvider protocol (get_sets / get_cards /
    get_card_image) and additionally fetches every set in both German and
    English, merging the German names into the English records under a
    'name_de' key so callers can persist both languages.

    Fallback: when the official API is unreachable for a given set, the
    caller may fall back to SWUDBProvider (see app.providers.swudb_provider).
    """

    def __init__(self, config: AppConfig | None = None):
        cfg = config or AppConfig.load()
        dp = cfg.get("data_provider", {}) or {}
        self.base_url = dp.get("official_base_url", "https://admin.starwarsunlimited.com/api")
        self.page_size = int(dp.get("official_page_size", DEFAULT_PAGE_SIZE))
        self.delay = float(dp.get("request_delay", DEFAULT_REQUEST_DELAY))
        self.max_retries = int(dp.get("max_retries", DEFAULT_MAX_RETRIES))
        self.timeout = int(dp.get("timeout", DEFAULT_TIMEOUT))
        self.locales = tuple(dp.get("official_locales", ["en", "de"]))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "SWU-CardManager/1.0 (local collection tool)",
            "Accept": "application/json",
        })

    # -- low-level HTTP --------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> Any:
        """GET a JSON endpoint with retries and courteous delay."""
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                log.debug(f"GET {url} params={params} (attempt {attempt})")
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                time.sleep(self.delay)
                return resp.json()
            except requests.RequestException as e:
                log.warning(
                    f"Official API request failed (attempt {attempt}/{self.max_retries}): {e}",
                    extra={"event": "request_retry", "url": url},
                )
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)

    # -- pagination ------------------------------------------------------------

    def _fetch_all_cards(self, locale: str, set_code: str | None = None) -> list[dict[str, Any]]:
        """Fetch every card for a locale, optionally filtered to one set code.

        Walks all pagination pages (100 cards per page) until exhausted.
        Returns the raw Strapi card objects ({"id", "attributes": {...}}).
        """
        all_cards: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {
                "pagination[pageSize]": self.page_size,
                "pagination[page]": page,
                "locale": locale,
                "populate": POPULATE_FIELDS,
            }
            data = self._get("/cards", params=params)
            page_cards = data.get("data") or []
            all_cards.extend(page_cards)

            meta = data.get("meta", {}).get("pagination", {})
            page_count = int(meta.get("pageCount", 0))
            total = int(meta.get("total", 0))
            log.debug(
                f"Official API page {page}/{page_count} ({len(page_cards)} cards, "
                f"{len(all_cards)}/{total} total) locale={locale}"
            )
            if not page_cards or page >= page_count:
                break
            page += 1
        return all_cards

    # -- mapping (official API → repository schema) ----------------------------

    def _map_card(self, raw: dict[str, Any], locale: str) -> dict[str, Any]:
        """Map a Strapi card object to the repository's card dict shape.

        The repository (app.db.repository.upsert_card) expects keys:
          Set, Number, Name, Subtitle, Type, Rarity, Aspects, Traits,
          Arenas, Cost, Power, HP, FrontText, BackText, EpicAction,
          Artist, Unique, DoubleSided, FrontArt, BackArt, tcgplayerId,
          cid, MarketPrice, LowPrice, VariantType
        We add 'name_de'/'subtitle_de' for the German translation when the
        primary locale is English so both languages can be persisted.
        """
        a = raw.get("attributes") or {}
        expansion_code = _rel_attr(a.get("expansion"), "code")
        card_number = str(a.get("cardNumber", "")).strip()
        front_art = _image_url(a.get("artFront"))
        back_art = _image_url(a.get("artBack"))

        card = {
            "Set": expansion_code,
            "Number": card_number,
            "Name": a.get("title", "") or "",
            "Subtitle": a.get("subtitle", "") or "",
            "Type": _rel_attr(a.get("type"), "name"),
            "Rarity": _rel_attr(a.get("rarity"), "name"),
            "Aspects": _rel_names(a.get("aspects")),
            "Traits": _rel_names(a.get("traits")),
            "Arenas": _rel_names(a.get("arenas")),
            "Cost": a.get("cost") if a.get("cost") is not None else "",
            "Power": a.get("power") if a.get("power") is not None else "",
            "HP": a.get("hp") if a.get("hp") is not None else "",
            "FrontText": a.get("text", "") or "",
            "BackText": a.get("deployBox", "") or "",
            "EpicAction": a.get("epicAction", "") or "",
            "Artist": a.get("artist", "") or "",
            "Unique": bool(a.get("unique")),
            "DoubleSided": bool(a.get("artBackHorizontal")),
            "FrontArt": front_art,
            "BackArt": back_art,
            "tcgplayerId": "",
            "cid": str(a.get("cardId", "") or ""),
            "MarketPrice": "",
            "LowPrice": "",
            "VariantType": _rel_names(a.get("variantTypes"))[0] if _rel_names(a.get("variantTypes")) else "Normal",
            "locale": locale,
        }
        return card

    def _merge_locale(self, base: dict[str, Any], de_card: dict[str, Any] | None) -> dict[str, Any]:
        """Merge the German name/subtitle into an English card record."""
        if de_card is not None:
            base["name_de"] = de_card.get("Name", "")
            base["subtitle_de"] = de_card.get("Subtitle", "")
            base["type_de"] = de_card.get("Type", "")
            base["rarity_de"] = de_card.get("Rarity", "")
            base["aspects_de"] = de_card.get("Aspects", [])
            base["traits_de"] = de_card.get("Traits", [])
            base["arenas_de"] = de_card.get("Arenas", [])
        else:
            base["name_de"] = ""
            base["subtitle_de"] = ""
            base["type_de"] = ""
            base["rarity_de"] = ""
            base["aspects_de"] = []
            base["traits_de"] = []
            base["arenas_de"] = []
        return base

    # -- public API (CardDataProvider protocol) --------------------------------

    def get_sets(self) -> list[dict[str, Any]]:
        """Return all available sets, extracted from card expansion data.

        We page through cards and collect unique expansions. This avoids
        a separate /expansions endpoint (which requires admin auth on this
        Strapi instance) and gives us exactly the sets that have cards.
        Both locales are fetched; the English name is canonical and the
        German name is merged under 'fullName_de'.
        """
        # English is canonical for set codes.
        en_cards = self._fetch_all_cards("en")

        sets_by_code: dict[str, dict[str, Any]] = {}
        for raw in en_cards:
            a = raw.get("attributes") or {}
            exp = (a.get("expansion") or {}).get("data")
            if not exp:
                continue
            attrs = exp.get("attributes") or {}
            code = attrs.get("code")
            if not code or code in sets_by_code:
                continue
            sets_by_code[code] = {
                "setId": code,
                "fullName": attrs.get("name", code),
                "parentSetId": None,
                "numberCards": 0,
                "maxElement": "",
                "isBaseSet": False,
                "releaseDate": (attrs.get("publishedAt") or "")[:10],
            }

        # Count cards per set from the English pull.
        for raw in en_cards:
            a = raw.get("attributes") or {}
            code = _rel_attr(a.get("expansion"), "code")
            if code and code in sets_by_code:
                sets_by_code[code]["numberCards"] += 1

        # Fetch German names for each set via a German card pull.
        de_name_by_code: dict[str, str] = {}
        if "de" in self.locales:
            try:
                de_cards = self._fetch_all_cards("de")
                for raw in de_cards:
                    a = raw.get("attributes") or {}
                    code = _rel_attr(a.get("expansion"), "code")
                    name = _rel_attr(a.get("expansion"), "name")
                    if code and code not in de_name_by_code and name:
                        de_name_by_code[code] = name
            except Exception as e:
                log.warning(f"Failed to fetch German set names: {e}",
                            extra={"event": "de_sets_fetch_fail"})

        sets = list(sets_by_code.values())
        for s in sets:
            s["fullName_de"] = de_name_by_code.get(s["setId"], "")

        log.info(f"Retrieved {len(sets)} sets from official API",
                 extra={"event": "sets_fetched", "count": len(sets)})
        return sets

    def get_cards(self, set_id: str, locale: str = "en") -> list[dict[str, Any]]:
        """Return all cards in a given set (by set code, e.g. 'SOR').

        By default returns English-canonical cards with German names merged
        under the *_de keys. Pass locale='de' to receive German-canonical
        cards (German primary names, English merged under *_en keys is NOT
        done here — callers wanting both should use the default 'en' path).
        """
        set_code = set_id.upper()
        # Use the Strapi filters param to page only through this set's cards
        # instead of the whole ~10k catalogue.
        en_cards_raw = self._fetch_filtered_cards("en", set_code)

        de_by_number: dict[str, dict[str, Any]] = {}
        if locale == "en" and "de" in self.locales:
            try:
                de_cards_raw = self._fetch_filtered_cards("de", set_code)
                for raw in de_cards_raw:
                    c = self._map_card(raw, "de")
                    de_by_number[c["Number"]] = c
            except Exception as e:
                log.warning(f"Failed to fetch German cards for set {set_code}: {e}",
                            extra={"event": "de_cards_fetch_fail", "set_id": set_code})

        cards: list[dict[str, Any]] = []
        for raw in en_cards_raw:
            c = self._map_card(raw, "en")
            de = de_by_number.get(c["Number"])
            self._merge_locale(c, de)
            cards.append(c)

        log.info(f"Retrieved {len(cards)} cards for set {set_code} (locale={locale})",
                 extra={"event": "cards_fetched", "set_id": set_code, "count": len(cards)})
        return cards

    def _fetch_filtered_cards(self, locale: str, set_code: str) -> list[dict[str, Any]]:
        """Fetch all cards for one set code in one locale using Strapi filters.

        Uses filters[expansion][code][$eq]=<CODE> so we only page through
        the cards of that set rather than the whole catalogue.
        """
        all_cards: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {
                "pagination[pageSize]": self.page_size,
                "pagination[page]": page,
                "locale": locale,
                "populate": POPULATE_FIELDS,
                "filters[expansion][code][$eq]": set_code,
            }
            data = self._get("/cards", params=params)
            page_cards = data.get("data") or []
            all_cards.extend(page_cards)
            meta = data.get("meta", {}).get("pagination", {})
            page_count = int(meta.get("pageCount", 0))
            if not page_cards or page >= page_count:
                break
            page += 1
        return all_cards

    def get_card_image(self, card_id: str, set_id: str, number: str, face: str = "front") -> bytes | None:
        """Download a card image by re-fetching the single card record.

        The official API does not expose a stable CDN URL pattern keyed on
        set+number alone (URLs contain a hash), so we look up the card via
        filters and read its artFront/artBack relation URL, then download
        the bytes. Returns raw bytes or None on failure.
        """
        set_code = set_id.upper()
        art_field = "artFront" if face == "front" else "artBack"
        params = {
            "pagination[pageSize]": 1,
            "locale": "en",
            "populate": POPULATE_FIELDS,
            "filters[expansion][code][$eq]": set_code,
            "filters[cardNumber][$eq]": str(number),
        }
        try:
            data = self._get("/cards", params=params)
        except requests.RequestException as e:
            log.warning(f"Card lookup for image failed ({set_code}-{number}): {e}")
            return None

        results = data.get("data") or []
        if not results:
            log.warning(f"No card found for {set_code}-{number} when fetching image")
            return None
        a = (results[0].get("attributes") or {})
        url = _image_url(a.get(art_field))
        if not url:
            log.warning(f"No image URL for {set_code}-{number} face={face}")
            return None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    time.sleep(self.delay)
                    return resp.content
                log.warning(f"Image download returned {resp.status_code} for {url}")
                return None
            except requests.RequestException as e:
                log.warning(f"Image download failed (attempt {attempt}) for {url}: {e}")
                if attempt == self.max_retries:
                    return None
                time.sleep(2 ** attempt)
        return None
