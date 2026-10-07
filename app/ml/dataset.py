"""
Dataset Manager — collects validated scans, manages dataset versions,
labels, deduplication, and quality checks. Feeds into the ML training pipeline.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.schema import connect

log = get_logger("dataset")


class DatasetManager:
    """Manages training data collection and versioning."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.dataset_dir = self.cfg.path("dataset_dir")
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def add_scan_sample(
        self,
        image: np.ndarray,
        card_id: str,
        confidence: float,
        source: str = "scan",
    ) -> str | None:
        """Add a scan image to the dataset. Returns the sample path or None."""
        if image is None or image.size == 0:
            return None

        # Deduplicate by hash
        img_hash = hashlib.md5(image.tobytes()).hexdigest()
        card_dir = self.dataset_dir / card_id.replace("/", "_")
        card_dir.mkdir(parents=True, exist_ok=True)

        # Check for existing duplicate
        existing = list(card_dir.glob("*.png"))
        for f in existing:
            try:
                existing_img = cv2.imread(str(f))
                if existing_img is not None:
                    existing_hash = hashlib.md5(existing_img.tobytes()).hexdigest()
                    if existing_hash == img_hash:
                        log.debug(f"Duplicate scan skipped for {card_id}")
                        return str(f)
            except Exception:
                continue

        # Save new sample
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ts}_{confidence:.2f}.png"
        filepath = card_dir / filename
        cv2.imwrite(str(filepath), image)

        # Save metadata
        meta = {
            "card_id": card_id,
            "confidence": confidence,
            "source": source,
            "timestamp": datetime.now().isoformat(),
            "image_hash": img_hash,
        }
        meta_path = filepath.with_suffix(".json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        return str(filepath)

    def get_sample_count(self) -> int:
        """Total number of samples in the dataset."""
        if not self.dataset_dir.exists():
            return 0
        return sum(1 for _ in self.dataset_dir.rglob("*.png"))

    def get_samples_per_card(self) -> dict[str, int]:
        """Count of samples per card_id."""
        counts = {}
        if not self.dataset_dir.exists():
            return counts
        for d in self.dataset_dir.iterdir():
            if d.is_dir():
                count = sum(1 for _ in d.glob("*.png"))
                if count > 0:
                    counts[d.name.replace("_", "/")] = count
        return counts

    def create_version(self, description: str = "") -> str:
        """Create a versioned snapshot of the current dataset.
        Records metadata in the database."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_id = f"ds_{ts}"
        sample_count = self.get_sample_count()
        card_count = len(self.get_samples_per_card())

        conn = connect()
        conn.execute(
            "INSERT INTO dataset_versions (version_id, sample_count, card_count, description, status, path) VALUES (?, ?, ?, ?, 'created', ?)",
            (version_id, sample_count, card_count, description, str(self.dataset_dir)),
        )
        conn.commit()
        conn.close()

        log.info(f"Dataset version {version_id} created: {sample_count} samples, {card_count} cards",
                 extra={"event": "dataset_version"})
        return version_id

    def should_trigger_training(self) -> tuple[bool, str]:
        """Check if enough new data warrants a training run."""
        min_samples = self.cfg.get("training.min_samples_per_card", 5)
        min_total = self.cfg.get("training.min_total_samples", 50)
        enabled = self.cfg.get("training.enabled", False)

        if not enabled:
            return False, "Training disabled in config"

        total = self.get_sample_count()
        if total < min_total:
            return False, f"Only {total} samples (need {min_total})"

        per_card = self.get_samples_per_card()
        cards_with_enough = sum(1 for c in per_card.values() if c >= min_samples)
        if cards_with_enough < 10:
            return False, f"Only {cards_with_enough} cards with >={min_samples} samples"

        return True, f"Ready: {total} samples across {len(per_card)} cards"

    def collect_from_scans(self, limit: int = 100) -> int:
        """Collect validated scans from the database into the dataset.
        Uses scans with recognized_card_id and confidence above threshold."""
        conn = connect()
        cur = conn.execute(
            """SELECT s.image_path, s.recognized_card_id, s.confidence
               FROM scans s
               WHERE s.recognized_card_id IS NOT NULL
                 AND s.confidence > 0.7
                 AND s.image_path IS NOT NULL
               ORDER BY s.timestamp DESC LIMIT ?""",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()

        added = 0
        for row in rows:
            img_path = Path(row["image_path"])
            if not img_path.exists():
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            self.add_scan_sample(img, row["recognized_card_id"], row["confidence"])
            added += 1

        log.info(f"Collected {added} samples from scan history", extra={"event": "dataset_collect"})
        return added

    def cleanup_bad_samples(self) -> int:
        """Remove corrupted or empty image files from the dataset."""
        removed = 0
        if not self.dataset_dir.exists():
            return removed
        for f in self.dataset_dir.rglob("*.png"):
            try:
                img = cv2.imread(str(f))
                if img is None or img.size == 0:
                    f.unlink()
                    removed += 1
            except Exception:
                f.unlink()
                removed += 1
        if removed:
            log.info(f"Cleaned up {removed} bad samples", extra={"event": "dataset_cleanup"})
        return removed
