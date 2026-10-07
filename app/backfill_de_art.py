"""Backfill German card image URLs from the official SWU API.

The official API (admin.starwarsunlimited.com) serves per-locale card art:
locale=de returns e.g. card_SWH_01de_005_Luke_Skywalker_Leader_<hash>.png.
This script walks all cards via paging and stores the German art URL in
cards.front_art_url_de.

Usage: python -m app.backfill_de_art   (idempotent, skips filled rows)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import requests

from app.core.config import AppConfig
from app.core.logging import AppLogger
from app.db.schema import connect

AppConfig.load()
AppLogger.setup()

API = "https://admin.starwarsunlimited.com/api/cards"
POPULATE = "expansion,artFront"


def _extract_art_url(card: dict) -> str | None:
    art = (card.get("attributes") or {}).get("artFront") or {}
    data = art.get("data") or {}
    attrs = data.get("attributes") or {}
    fmts = attrs.get("formats") or {}
    card_fmt = fmts.get("card") or {}
    return card_fmt.get("url") or attrs.get("url")


def main() -> None:
    conn = connect()
    try:
        page = 1
        filled = 0
        seen = 0
        while True:
            resp = requests.get(
                API,
                params={
                    "pagination[pageSize]": 100,
                    "pagination[page]": page,
                    "locale": "de",
                    "populate": POPULATE,
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            cards = payload.get("data") or []
            if not cards:
                break
            meta = (payload.get("meta") or {}).get("pagination") or {}
            total_pages = meta.get("pageCount", page)

            for card in cards:
                attrs = card.get("attributes") or {}
                exp = attrs.get("expansion") or {}
                exp_code = (exp.get("data") or {}).get("attributes", {}).get("code") if isinstance(exp, dict) else None
                if not exp_code:
                    continue
                num = attrs.get("cardNumber")
                if num is None:
                    continue
                url = _extract_art_url(card)
                if not url:
                    continue
                seen += 1
                # match by (set code, number-normalised) across both id variants
                try:
                    num_norm = str(int(str(num).lstrip("0") or "0"))
                except ValueError:
                    num_norm = str(num)
                cur = conn.execute(
                    """SELECT card_id FROM cards
                       WHERE set_id = ?
                         AND (card_number = ? OR card_number = ? OR card_number = ?)""",
                    (exp_code, str(num), str(num).lstrip("0") or "0", f"{int(num):03d}"),
                )
                rows = cur.fetchall()
                for row in rows:
                    conn.execute(
                        "UPDATE cards SET front_art_url_de = ? WHERE card_id = ? AND (front_art_url_de IS NULL OR front_art_url_de = '')",
                        (url, row["card_id"]),
                    )
                    filled += 1
            conn.commit()
            print(f"page {page}/{total_pages}: seen={seen} updated_rows={filled}", flush=True)
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.15)  # courtesy delay

        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM cards WHERE front_art_url_de IS NOT NULL AND front_art_url_de != ''")
        print(f"DONE: {cur.fetchone()[0]} cards now have a German art URL")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
