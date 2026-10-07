"""
SWU-DB.com data provider.
Public REST API: https://api.swu-db.com
  GET /sets               — all sets
  GET /cards/{set}        — all cards in a set
  GET /cards/{set}/{num}  — single card
  GET /cards/{set}/{num}?format=image — card image (redirects to cdn.swu-db.com)

This is a public, free API documented at https://www.swu-db.com/api.
It does not require authentication and has no stated rate-limit beyond
reasonable use. We add a small delay between requests as a courtesy.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

import requests

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("provider")


class CardDataProvider(Protocol):
    """Interface for pluggable card data sources."""

    def get_sets(self) -> list[dict[str, Any]]: ...
    def get_cards(self, set_id: str) -> list[dict[str, Any]]: ...
    def get_card_image(self, card_id: str, set_id: str, number: str, face: str = "front") -> bytes | None: ...


class SWUDBProvider:
    """Data provider using the public swu-db.com API."""

    def __init__(self, config: AppConfig | None = None):
        cfg = config or AppConfig.load()
        dp = cfg.get("data_provider", {})
        self.base_url = dp.get("base_url", "https://api.swu-db.com")
        self.image_base_url = dp.get("image_base_url", "https://cdn.swu-db.com")
        self.delay = dp.get("request_delay", 0.5)
        self.max_retries = dp.get("max_retries", 3)
        self.timeout = dp.get("timeout", 30)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SWU-CardManager/1.0 (local collection tool)"})

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                log.debug(f"GET {url} (attempt {attempt})")
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                time.sleep(self.delay)
                return resp.json()
            except requests.RequestException as e:
                log.warning(f"Request failed (attempt {attempt}): {e}", extra={"event": "request_retry"})
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)

    def get_sets(self) -> list[dict[str, Any]]:
        """Return all available sets."""
        data = self._get("/sets")
        log.info(f"Retrieved {len(data)} sets from API", extra={"event": "sets_fetched"})
        return data

    def get_cards(self, set_id: str) -> list[dict[str, Any]]:
        """Return all cards in a given set (by set code, e.g. 'SOR').
        The API returns {"total_cards": N, "data": [...]}; we extract the data list."""
        data = self._get(f"/cards/{set_id.lower()}")
        # Handle both list and dict-with-data-key response formats
        if isinstance(data, dict) and "data" in data:
            cards = data["data"]
        elif isinstance(data, list):
            cards = data
        else:
            cards = []
        log.info(f"Retrieved {len(cards)} cards for set {set_id}", extra={"event": "cards_fetched", "set_id": set_id})
        return cards

    def get_card(self, set_id: str, number: str) -> dict[str, Any]:
        """Return a single card."""
        return self._get(f"/cards/{set_id.lower()}/{number}")

    def get_card_image(self, card_id: str, set_id: str, number: str, face: str = "front") -> bytes | None:
        """Download a card image. Returns raw bytes or None on failure.
        Uses the CDN URL directly for reliability."""
        # cdn.swu-db.com/images/cards/SOR/010.png
        suffix = "" if face == "front" else "-b"
        url = f"{self.image_base_url}/images/cards/{set_id.upper()}/{number}{suffix}.png"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    time.sleep(self.delay)
                    return resp.content
                log.warning(f"Image download returned {resp.status_code} for {url}")
                return None
            except requests.RequestException as e:
                log.warning(f"Image download failed (attempt {attempt}): {e}")
                if attempt == self.max_retries:
                    return None
                time.sleep(2 ** attempt)
        return None
