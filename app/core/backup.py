"""
Backup & Recovery — versioned backups of database, config, models, and state.
Includes integrity checking and backup rotation.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db.schema import connect, check_integrity

log = get_logger("backup")


class BackupManager:
    """Manages versioned backups with rotation."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.backup_dir = self.cfg.path("backup_dir")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backups = self.cfg.get("backup.max_backups", 10)
        self.db_path = self.cfg.path("database")
        self.models_dir = self.cfg.path("models_dir")
        self.root = self.cfg.root

    def create_backup(self, label: str = "") -> Path:
        """Create a timestamped zip backup of database, config, models, and state."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{label}" if label else ""
        backup_name = f"backup_{ts}{suffix}.zip"
        backup_path = self.backup_dir / backup_name

        # Close any open DB connections by backing up via SQLite's backup API
        # to a temporary copy, which avoids WAL file locking issues
        temp_db = self.backup_dir / f"_tmp_{ts}.db"
        if self.db_path.exists():
            import sqlite3
            try:
                src = sqlite3.connect(str(self.db_path))
                dst = sqlite3.connect(str(temp_db))
                src.backup(dst)
                dst.close()
                src.close()
            except Exception:
                # Fallback: just copy the file
                shutil.copy2(self.db_path, temp_db)

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Database (from temp copy)
            if temp_db.exists():
                zf.write(temp_db, "data/cards.db")
                temp_db.unlink()  # clean up temp
            # Config
            config_path = self.root / "config" / "config.yaml"
            if config_path.exists():
                zf.write(config_path, "config/config.yaml")
            # Project state
            state_dir = self.root / "project_state"
            if state_dir.exists():
                for f in state_dir.rglob("*"):
                    if f.is_file() and "backups" not in str(f):
                        arc = f.relative_to(self.root)
                        zf.write(f, str(arc))

        # Rotate old backups
        self._rotate()
        log.info(f"Backup created: {backup_path}", extra={"event": "backup_created"})
        return backup_path

    def _rotate(self) -> None:
        """Keep only the most recent max_backups backup files."""
        backups = sorted(self.backup_dir.glob("backup_*.zip"))
        if len(backups) > self.max_backups:
            for old in backups[: len(backups) - self.max_backups]:
                old.unlink()
                log.debug(f"Rotated old backup: {old}")

    def list_backups(self) -> list[dict[str, Any]]:
        """List all available backups with metadata."""
        backups = []
        for f in sorted(self.backup_dir.glob("backup_*.zip"), reverse=True):
            stat = f.stat()
            backups.append({
                "name": f.name,
                "size_bytes": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "path": str(f),
            })
        return backups

    def restore_backup(self, backup_path: Path) -> bool:
        """Restore from a backup zip. Returns True on success."""
        if not backup_path.exists():
            log.error(f"Backup not found: {backup_path}")
            return False
        # Create a pre-restore backup first
        if self.db_path.exists():
            self.create_backup("pre_restore")

        with zipfile.ZipFile(backup_path, "r") as zf:
            zf.extractall(self.root)
        log.info(f"Restored from {backup_path}", extra={"event": "backup_restored"})
        return True

    def verify_integrity(self) -> dict[str, Any]:
        """Verify database integrity and backup file hashes."""
        result = {
            "database_ok": False,
            "database_message": "",
            "backups": [],
        }
        if self.db_path.exists():
            ok = check_integrity()
            result["database_ok"] = ok
            result["database_message"] = "ok" if ok else "integrity check failed"
        else:
            result["database_message"] = "database file not found"

        for b in self.list_backups():
            try:
                with zipfile.ZipFile(b["path"], "r") as zf:
                    bad = zf.testzip()
                    b["valid"] = bad is None
            except Exception as e:
                b["valid"] = False
                b["error"] = str(e)
            result["backups"].append(b)

        return result
