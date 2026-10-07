"""
ML Training Manager — controlled training with validation.
A new model must outperform the current active model by a threshold
before it is activated. A worse model is never auto-activated.

Model Registry — stores model metadata in the database.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db.schema import connect
from app.ml.dataset import DatasetManager

log = get_logger("training")


class ModelRegistry:
    """Manages ML model metadata and activation in the database."""

    @staticmethod
    def register_model(
        conn: sqlite3.Connection,
        model_id: str,
        version: str,
        dataset_version: str,
        parameters: dict,
        storage_path: str,
    ) -> None:
        conn.execute(
            """INSERT INTO models (model_id, version, trained_at, dataset_version,
                                   parameters, storage_path, status)
               VALUES (?, ?, ?, ?, ?, ?, 'created')""",
            (model_id, version, datetime.now().isoformat(timespec="seconds"),
             dataset_version, json.dumps(parameters), storage_path),
        )
        conn.commit()

    @staticmethod
    def get_active_model(conn: sqlite3.Connection) -> dict[str, Any] | None:
        cur = conn.execute("SELECT * FROM models WHERE is_active = 1 LIMIT 1")
        r = cur.fetchone()
        return dict(r) if r else None

    @staticmethod
    def activate_model(conn: sqlite3.Connection, model_id: str) -> None:
        conn.execute("UPDATE models SET is_active = 0")
        conn.execute("UPDATE models SET is_active = 1, status = 'active' WHERE model_id = ?", (model_id,))
        conn.commit()
        log.info(f"Model {model_id} activated", extra={"event": "model_activated"})

    @staticmethod
    def discard_model(conn: sqlite3.Connection, model_id: str) -> None:
        conn.execute("UPDATE models SET status = 'discarded' WHERE model_id = ?", (model_id,))
        conn.commit()
        log.info(f"Model {model_id} discarded", extra={"event": "model_discarded"})

    @staticmethod
    def update_validation(
        conn: sqlite3.Connection, model_id: str,
        accuracy: float, recognition_rate: float, confusion_matrix: dict | None = None,
    ) -> None:
        conn.execute(
            """UPDATE models SET validation_accuracy = ?, recognition_rate = ?,
               confusion_matrix = ?, status = 'validated' WHERE model_id = ?""",
            (accuracy, recognition_rate,
             json.dumps(confusion_matrix) if confusion_matrix else None, model_id),
        )
        conn.commit()

    @staticmethod
    def list_models(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        cur = conn.execute("SELECT * FROM models ORDER BY trained_at DESC")
        return [dict(r) for r in cur.fetchall()]


class TrainingManager:
    """Orchestrates the controlled training process."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.dataset = DatasetManager(self.cfg)
        self.models_dir = self.cfg.path("models_dir")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.improvement_threshold = self.cfg.get("training.improvement_threshold", 0.03)

    def should_train(self) -> tuple[bool, str]:
        return self.dataset.should_trigger_training()

    def train(self) -> dict[str, Any]:
        """Run a training cycle. This is a controlled process:
        1. Check if training is warranted
        2. Prepare dataset
        3. Train (placeholder for actual ML)
        4. Validate
        5. Compare with active model
        6. Activate only if better
        """
        should, reason = self.should_train()
        if not should:
            log.info(f"Training skipped: {reason}", extra={"event": "training_skip"})
            return {"trained": False, "reason": reason}

        log.info("Starting training cycle", extra={"event": "training_start"})

        # Create dataset version
        ds_version = self.dataset.create_version("Training run")

        # Placeholder: actual model training would go here
        # For now, we record a mock training result
        model_id = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        model_path = self.models_dir / f"{model_id}.json"

        conn = connect()
        ModelRegistry.register_model(
            conn, model_id, "0.1.0", ds_version,
            {"algorithm": "placeholder", "threshold": self.improvement_threshold},
            str(model_path),
        )

        # Validate (mock)
        mock_accuracy = 0.85
        mock_recognition_rate = 0.82

        ModelRegistry.update_validation(conn, model_id, mock_accuracy, mock_recognition_rate)

        # Compare with current active model
        active = ModelRegistry.get_active_model(conn)
        if active is None:
            # No active model — activate this one
            ModelRegistry.activate_model(conn, model_id)
            conn.close()
            return {"trained": True, "model_id": model_id, "activated": True, "reason": "First model"}
        else:
            old_rate = active.get("recognition_rate") or 0
            if mock_recognition_rate > old_rate + self.improvement_threshold:
                ModelRegistry.activate_model(conn, model_id)
                conn.close()
                return {"trained": True, "model_id": model_id, "activated": True,
                        "improvement": mock_recognition_rate - old_rate}
            else:
                ModelRegistry.discard_model(conn, model_id)
                conn.close()
                return {"trained": True, "model_id": model_id, "activated": False,
                        "reason": f"Did not improve by {self.improvement_threshold}"}
