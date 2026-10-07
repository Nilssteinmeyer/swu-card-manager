"""
Update Manager — detects new sets, imports cards, downloads images,
and keeps the local database in sync with the data provider.
Fully idempotent: running twice produces no duplicates.
Transactional: interrupted imports are detected and can be resumed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.core.state import start_task, complete_task, fail_task, update_state
from app.db import repository as repo
from app.db.schema import connect
from app.providers.swudb_provider import SWUDBProvider

log = get_logger("update")


class UpdateManager:
    """Manages card data updates from the data provider."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.provider = SWUDBProvider(self.cfg)
        self.images_dir = self.cfg.path("images_dir")
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def sync_sets(self) -> dict[str, Any]:
        """Fetch all sets from the provider and upsert into DB.
        Returns a summary of new / updated sets."""
        task_id = f"sync_sets_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        start_task(task_id, "Sync sets from provider")
        try:
            sets = self.provider.get_sets()
            conn = connect()
            new_sets = []
            updated_sets = []
            for s in sets:
                existing = repo.get_set(conn, s["setId"])
                repo.upsert_set(conn, s)
                if existing is None:
                    new_sets.append(s["setId"])
                else:
                    updated_sets.append(s["setId"])
            conn.close()
            complete_task(task_id, {"new": len(new_sets), "updated": len(updated_sets)})
            log.info(f"Sets synced: {len(new_sets)} new, {len(updated_sets)} updated",
                     extra={"event": "sets_synced"})
            return {"new_sets": new_sets, "updated_sets": updated_sets, "total": len(sets)}
        except Exception as e:
            fail_task(task_id, str(e))
            log.error(f"Set sync failed: {e}", extra={"event": "sync_fail"})
            raise

    def import_set(self, set_id: str, download_images: bool = True) -> dict[str, Any]:
        """Import all cards for a given set. Idempotent.
        Downloads images if download_images is True."""
        task_id = f"import_set_{set_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        start_task(task_id, f"Import set {set_id}")
        try:
            cards = self.provider.get_cards(set_id)
            conn = connect()

            # Ensure the set exists in DB
            s = repo.get_set(conn, set_id)
            if s is None:
                # Fetch and insert set metadata
                all_sets = self.provider.get_sets()
                for st in all_sets:
                    if st["setId"].upper() == set_id.upper():
                        repo.upsert_set(conn, st)
                        break

            imported_count = 0
            image_count = 0
            image_failures = 0

            for card in cards:
                card_id = repo.upsert_card(conn, card)
                imported_count += 1

                if download_images and card.get("FrontArt"):
                    img_path = self._download_card_image(
                        conn, card_id, set_id, card["Number"], face="front"
                    )
                    if img_path:
                        image_count += 1
                    else:
                        image_failures += 1

                    # Download back art for double-sided cards
                    if card.get("DoubleSided") and card.get("BackArt"):
                        self._download_card_image(
                            conn, card_id, set_id, card["Number"], face="back"
                        )

            repo.mark_set_imported(conn, set_id, imported_count)
            conn.close()

            # Update state
            state = update_state()
            imported = state.get("data_imported_sets", [])
            if set_id.upper() not in imported:
                imported.append(set_id.upper())
            update_state(data_imported_sets=imported)

            complete_task(task_id, {
                "cards": imported_count,
                "images": image_count,
                "image_failures": image_failures,
            })
            log.info(f"Set {set_id} imported: {imported_count} cards, {image_count} images",
                     extra={"event": "set_imported", "set_id": set_id})
            return {
                "set_id": set_id,
                "cards_imported": imported_count,
                "images_downloaded": image_count,
                "image_failures": image_failures,
            }
        except Exception as e:
            fail_task(task_id, str(e))
            log.error(f"Set import failed for {set_id}: {e}", extra={"event": "import_fail", "set_id": set_id})
            raise

    def _download_card_image(
        self, conn: sqlite3.Connection, card_id: str, set_id: str, number: str, face: str = "front"
    ) -> Path | None:
        """Download and save a card image. Returns the local path or None."""
        set_dir = self.images_dir / set_id.upper()
        set_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if face == "front" else "-b"
        filename = f"{number}{suffix}.png"
        local_path = set_dir / filename

        # Skip if already downloaded
        if local_path.exists() and local_path.stat().st_size > 1000:
            repo.update_card_image_path(conn, card_id, front=str(local_path) if face == "front" else None,
                                        back=str(local_path) if face == "back" else None)
            return local_path

        img_data = self.provider.get_card_image(card_id, set_id, number, face)
        if img_data is None:
            return None

        with open(local_path, "wb") as f:
            f.write(img_data)

        if face == "front":
            repo.update_card_image_path(conn, card_id, front=str(local_path))
        else:
            repo.update_card_image_path(conn, card_id, back=str(local_path))
        return local_path

    def check_for_new_sets(self) -> list[str]:
        """Compare provider sets with DB and return set IDs not yet imported."""
        all_sets = self.provider.get_sets()
        conn = connect()
        db_sets = {s["set_id"] for s in repo.get_all_sets(conn)}
        conn.close()
        new = [s["setId"] for s in all_sets if s["setId"] not in db_sets]
        if new:
            log.info(f"Found {len(new)} new sets: {new}", extra={"event": "new_sets_detected"})
        return new

    def import_all_missing_sets(self, download_images: bool = True) -> list[dict[str, Any]]:
        """Import all sets that exist in the provider but not yet in the DB."""
        # First sync set metadata
        self.sync_sets()
        # Then find unimported sets
        conn = connect()
        all_db_sets = repo.get_all_sets(conn)
        conn.close()
        unimported = [s for s in all_db_sets if not s.get("imported")]
        results = []
        for s in unimported:
            try:
                result = self.import_set(s["set_id"], download_images=download_images)
                results.append(result)
            except Exception as e:
                log.error(f"Failed to import set {s['set_id']}: {e}", extra={"event": "import_fail", "set_id": s["set_id"]})
                results.append({"set_id": s["set_id"], "error": str(e)})
        return results

    def full_sync(self, download_images: bool = True) -> dict[str, Any]:
        """Sync set metadata, then import all missing sets."""
        log.info("Starting full sync", extra={"event": "full_sync_start"})
        self.sync_sets()
        results = self.import_all_missing_sets(download_images=download_images)
        total_cards = sum(r.get("cards_imported", 0) for r in results if "error" not in r)
        log.info(f"Full sync complete: {len(results)} sets, {total_cards} cards",
                 extra={"event": "full_sync_complete"})
        return {"sets_processed": len(results), "total_cards": total_cards, "results": results}
