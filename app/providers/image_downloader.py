"""
Parallel image downloader — downloads all card images efficiently.
Handles Normal, Foil, Hyperspace, and Hyperspace Foil variants.
Uses multiple threads with a configurable delay to respect the CDN.
Idempotent: skips already downloaded images.
"""
from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.schema import connect

log = get_logger("imgdownload")


def download_all_images(
    config: AppConfig | None = None,
    max_workers: int = 8,
    skip_existing: bool = True,
) -> dict[str, int]:
    """Download all card images for all cards in the database.
    Returns a summary dict with counts."""
    cfg = config or AppConfig.load()
    images_dir = cfg.path("images_dir")
    images_dir.mkdir(parents=True, exist_ok=True)

    conn = connect()
    cards = repo.get_all_cards(conn)
    conn.close()

    # Build list of (card_id, set_id, number, front_art_url) for cards without local images
    to_download = []
    already_have = 0
    no_url = 0

    for card in cards:
        front_url = card.get("front_art_url", "")
        front_path = card.get("front_art_path", "")
        if not front_url:
            no_url += 1
            continue
        if skip_existing and front_path and Path(front_path).exists():
            already_have += 1
            continue
        to_download.append((card["card_id"], card["set_id"], card["card_number"], front_url))

    log.info(f"Image download: {len(to_download)} to download, {already_have} already have, {no_url} no URL",
             extra={"event": "imgdownload_start"})

    # CDN base for direct download
    cdn_base = cfg.get("data_provider.image_base_url", "https://cdn.swu-db.com")

    session = requests.Session()
    session.headers.update({"User-Agent": "SWU-CardManager/1.0"})
    delay = cfg.get("data_provider.request_delay", 0.3)
    max_retries = cfg.get("data_provider.max_retries", 3)
    timeout = cfg.get("data_provider.timeout", 30)

    def download_one(card_id: str, set_id: str, number: str, url: str) -> tuple[str, bool]:
        """Download a single card image. Returns (card_id, success)."""
        set_dir = images_dir / set_id.upper()
        set_dir.mkdir(parents=True, exist_ok=True)

        # Determine filename from URL (handles -b suffix and variant suffixes like F, etc.)
        # URL format: https://cdn.swu-db.com/images/cards/SOR/010.png
        # Number in URL may contain suffix like 059F, 324 etc.
        url_filename = url.split("/")[-1]
        local_path = set_dir / url_filename

        if skip_existing and local_path.exists() and local_path.stat().st_size > 1000:
            return card_id, True

        for attempt in range(1, max_retries + 1):
            try:
                resp = session.get(url, timeout=timeout)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    return card_id, True
                elif resp.status_code == 404:
                    # Image doesn't exist (some promo cards may not have images)
                    return card_id, False
            except requests.RequestException:
                if attempt < max_retries:
                    time.sleep(1)
        return card_id, False

    # Parallel download
    downloaded = 0
    failed = 0
    batch_size = 500
    total_batches = (len(to_download) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(to_download), batch_size):
        batch = to_download[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_one, cid, sid, num, url): cid
                for cid, sid, num, url in batch
            }
            for future in as_completed(futures):
                _, success = future.result()
                if success:
                    downloaded += 1
                else:
                    failed += 1

        log.info(f"Batch {batch_num}/{total_batches}: {downloaded} downloaded, {failed} failed",
                 extra={"event": "imgdownload_batch"})

        # Update DB paths every batch
        conn = connect()
        for card_id, set_id, number, url in batch:
            url_filename = url.split("/")[-1]
            local_path = images_dir / set_id.upper() / url_filename
            if local_path.exists() and local_path.stat().st_size > 1000:
                repo.update_card_image_path(conn, card_id, front=str(local_path))
        conn.close()

    summary = {
        "total": len(cards),
        "downloaded": downloaded,
        "already_had": already_have,
        "failed": failed,
        "no_url": no_url,
    }
    log.info(f"Image download complete: {downloaded} new, {already_have} existing, {failed} failed",
             extra={"event": "imgdownload_complete"})
    return summary


if __name__ == "__main__":
    from app.core.logging import AppLogger
    AppConfig.load()
    AppLogger.setup()
    result = download_all_images(max_workers=12)
    print(result)
