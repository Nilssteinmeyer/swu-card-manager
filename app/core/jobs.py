"""
Job Queue — background task execution with state tracking.
Supports: data sync, integrity checks, backup, training triggers,
and maintenance. Idempotent and crash-resumable.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.core.state import load_current_state, update_state
from app.db.schema import connect, check_integrity

log = get_logger("jobs")


class JobQueue:
    """Simple in-memory job queue with persistence via job_log table."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self._jobs: dict[str, Callable] = {}
        self._register_default_jobs()

    def register(self, name: str, func: Callable) -> None:
        self._jobs[name] = func

    def _register_default_jobs(self) -> None:
        from app.providers.update_manager import UpdateManager
        from app.core.backup import BackupManager
        from app.ml.training import TrainingManager

        def job_sync_data():
            um = UpdateManager(self.cfg)
            return um.sync_sets()

        def job_check_integrity():
            return check_integrity()

        def job_backup():
            bm = BackupManager(self.cfg)
            return str(bm.create_backup("scheduled"))

        def job_training_check():
            tm = TrainingManager(self.cfg)
            should, reason = tm.should_train()
            if should:
                return tm.train()
            return {"trained": False, "reason": reason}

        self.register("sync_data", job_sync_data)
        self.register("check_integrity", job_check_integrity)
        self.register("backup", job_backup)
        self.register("training_check", job_training_check)

    def run_job(self, name: str) -> dict[str, Any]:
        """Run a single named job with logging and error handling."""
        job_id = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        conn = connect()
        conn.execute(
            "INSERT INTO job_log (job_id, job_type, started_at, status) VALUES (?, ?, ?, 'running')",
            (job_id, name, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

        try:
            func = self._jobs.get(name)
            if func is None:
                raise ValueError(f"Unknown job: {name}")
            result = func()
            conn.execute(
                "UPDATE job_log SET completed_at = ?, status = 'completed', result = ? WHERE job_id = ?",
                (datetime.now().isoformat(timespec="seconds"),
                 json.dumps(result, default=str) if result else "", job_id),
            )
            conn.commit()
            log.info(f"Job {name} completed", extra={"event": "job_done", "task_id": job_id})
            return {"job": name, "status": "completed", "result": result}
        except Exception as e:
            conn.execute(
                "UPDATE job_log SET completed_at = ?, status = 'failed', result = ? WHERE job_id = ?",
                (datetime.now().isoformat(timespec="seconds"), str(e), job_id),
            )
            conn.commit()
            log.error(f"Job {name} failed: {e}", extra={"event": "job_fail", "task_id": job_id})
            return {"job": name, "status": "failed", "error": str(e)}
        finally:
            conn.close()

    def run_maintenance_cycle(self) -> list[dict[str, Any]]:
        """Run a full maintenance cycle: integrity, sync, backup, training check."""
        results = []
        for job_name in ["check_integrity", "sync_data", "backup", "training_check"]:
            results.append(self.run_job(job_name))
        update_state(last_maintenance=datetime.now().isoformat(timespec="seconds"))
        return results

    def get_job_history(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = connect()
        cur = conn.execute(
            "SELECT * FROM job_log ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
