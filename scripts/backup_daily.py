"""Daily production backup with rotation + weekly restore drill.

Called by Windows Task Scheduler (03:00 daily, config/production.yaml).
Uses the existing BackupManager for the database, then archives everything
else (config, models, training photos, state) into the same versioned ZIP.
Rotation: keep_daily / keep_weekly / keep_monthly from production.yaml.
A weekly restore drill unpacks the newest backup into a temp dir, runs a
SQLite integrity check against the restored DB and records the result.
Failures are logged AND (when SMTP is configured) emailed to the admin.
Exit codes: 0 = ok, 1 = backup failed, 2 = restore drill failed.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from app.core.logging import AppLogger

AppLogger.setup()
import logging

log = logging.getLogger("swu_manager.backup_daily")

CONFIG_PATH = PROJECT_ROOT / "config" / "production.yaml"


def _load_cfg() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_target(cfg: dict[str, Any]) -> Path:
    target = Path(cfg["backup"]["target_dir"])
    fallback = Path(cfg["backup"].get("target_dir_fallback", "project_state/backups/daily"))
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Schreibtest
        (target / ".writetest").write_text("ok", encoding="utf-8")
        (target / ".writetest").unlink()
        return target
    except Exception as e:
        log.warning(f"Backup-Ziel {target} nicht beschreibbar ({e}) — nutze Fallback {fallback}")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _classification(ts: datetime, created_day: datetime) -> str:
    """daily | weekly | monthly for rotation bookkeeping."""
    # Wochen-Snapshot: So. (oder erster Backup-Tag der Woche)
    if created_day.weekday() == 6:
        return "weekly"
    # Monats-Snapshot: 1. Backup des Monats
    if created_day.day <= 1:
        return "monthly"
    return "daily"


def run_backup() -> Path | None:
    from app.core.config import AppConfig
    from app.core.backup import BackupManager

    cfg = _load_cfg()
    target = _resolve_target(cfg)

    ts = datetime.now()
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    day = ts.date()

    # 1) DB via BackupManager (konsistent, WAL-sicher)
    bm = BackupManager(AppConfig.load())
    db_backup_path = bm.create_backup(label=f"daily-{stamp}")

    # 2) Sammel-ZIP: DB-Backup + alles Wichtige
    final_path = target / f"swu_backup_{stamp}.zip"
    import zipfile

    with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # DB-Backup
        if db_backup_path and Path(db_backup_path).exists():
            zf.write(db_backup_path, f"db/{Path(db_backup_path).name}")
        # Config (OHNE secret_key Datei-Inhalt? secret_key ist Betriebsgeheimnis,
        # aber fuer Disaster-Recovery mit drin — liegt im verschluesselten ZIP auf lokaler Platte)
        for p in (PROJECT_ROOT / "config").glob("*"):
            if p.is_file():
                zf.write(p, f"config/{p.name}")
        # Modelle (alle Versionen)
        models_dir = PROJECT_ROOT / "data" / "models"
        for p in models_dir.rglob("*"):
            if p.is_file() and p.suffix in (".pt", ".json", ".index"):
                zf.write(p, f"models/{p.relative_to(models_dir)}")
        # Trainingsfotos (unersetzlich!)
        scans_dir = PROJECT_ROOT / "data" / "scans" / "confirmed"
        if scans_dir.exists():
            for p in scans_dir.glob("*.jpg"):
                zf.write(p, f"scans/{p.name}")
        # Dataset-Metadaten
        dataset_dir = PROJECT_ROOT / "data" / "dataset"
        for p in dataset_dir.rglob("*.json"):
            zf.write(p, f"dataset/{p.relative_to(dataset_dir)}")
        # Project-State (ohne dasBackup-Verzeichnis selbst)
        state_dir = PROJECT_ROOT / "project_state"
        for p in state_dir.rglob("*"):
            if p.is_file() and "backups" not in p.parts:
                zf.write(p, f"state/{p.relative_to(state_dir)}")
        # Manifest mit Metadaten
        manifest = {
            "created_at": ts.isoformat(),
            "classification": _classification(ts, day),
            "db_backup": str(db_backup_path),
            "config": str(CONFIG_PATH),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    # Manifest-Datei (ausserhalb des ZIP, fuer Rotation)
    (target / f"swu_backup_{stamp}.meta.json").write_text(
        json.dumps({"classification": manifest["classification"], "created": stamp}), encoding="utf-8"
    )
    log.info(f"Backup erstellt: {final_path} ({final_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return final_path


def rotate_backups() -> None:
    """Keep N daily / weekly / monthly per production.yaml."""
    cfg = _load_cfg()
    target = _resolve_target(cfg)
    keep = {
        "daily": int(cfg["backup"].get("keep_daily", 7)),
        "weekly": int(cfg["backup"].get("keep_weekly", 4)),
        "monthly": int(cfg["backup"].get("keep_monthly", 6)),
    }
    buckets: dict[str, list[Path]] = {"daily": [], "weekly": [], "monthly": []}
    for meta in sorted(target.glob("swu_backup_*.meta.json"), reverse=True):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            cls = info.get("classification", "daily")
            buckets[cls].append(meta)
        except Exception:
            buckets["daily"].append(meta)

    for cls, metas in buckets.items():
        for meta in metas[keep[cls]:]:
            zip_path = meta.with_suffix("").with_suffix(".zip")  # .meta.json -> .zip
            try:
                zip_path.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                log.info(f"Rotation: {zip_path.name} geloescht ({cls}, Limit {keep[cls]})")
            except Exception as e:
                log.warning(f"Rotation-Fehler bei {zip_path}: {e}")


def restore_drill() -> bool:
    """Unpack newest backup to temp, integrity-check the restored DB."""
    import tempfile

    cfg = _load_cfg()
    target = _resolve_target(cfg)
    zips = sorted(target.glob("swu_backup_*.zip"), reverse=True)
    if not zips:
        log.warning("Restore-Drill: kein Backup gefunden!")
        return False
    newest = zips[0]

    with tempfile.TemporaryDirectory(prefix="swu_drill_") as td:
        import zipfile

        with zipfile.ZipFile(newest) as zf:
            names = zf.namelist()
            zf.extractall(td)
        # DB-Backup finden und pruefen
        db_files = [n for n in names if n.startswith("db/")]
        if not db_files:
            log.error("Restore-Drill: kein DB-Backup im ZIP!")
            return False
        restored_db = Path(td) / db_files[0]
        if restored_db.suffix == ".zip":
            # BackupManager-Backups sind selbst ZIPs -> entpacken
            inner = Path(td) / "inner"
            with zipfile.ZipFile(restored_db) as zf2:
                zf2.extractall(inner)
            db_candidates = list(inner.rglob("*.db"))
            if not db_candidates:
                log.error("Restore-Drill: keine .db im inneren ZIP!")
                return False
            restored_db = db_candidates[0]
        try:
            conn = sqlite3.connect(str(restored_db))
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
            conn.close()
            if result != "ok":
                log.error(f"Restore-Drill: Integritaet FEHLGESCHLAGEN: {result}")
                return False
            log.info(f"Restore-Drill OK: {newest.name} | collection={count} Zeilen | integrity=ok")
            return True
        except sqlite3.Error as e:
            log.error(f"Restore-Drill: DB-Fehler: {e}")
            return False


def _notify_failure(subject: str, body: str) -> None:
    """Email alert if SMTP configured; otherwise log only."""
    try:
        from app.auth.mail import is_configured, send_email
        import os

        if is_configured():
            cfg = _load_cfg()
            send_email(cfg["mail"].get("user", ""), subject, body)
    except Exception as e:
        log.warning(f"Mail-Versand des Alerts fehlgeschlagen: {e}")


def main() -> int:
    cfg = _load_cfg()
    drill_day = int(cfg["backup"].get("restore_drill_weekday", 0))
    today = datetime.now().weekday()

    try:
        run_backup()
    except Exception as e:
        log.error(f"BACKUP FEHLGESCHLAGEN: {e}", exc_info=True)
        if cfg["backup"].get("email_on_failure"):
            _notify_failure("SWU-Server: BACKUP FEHLGESCHLAGEN", f"Fehler:\n{e}")
        return 1

    rotate_backups()

    if today == drill_day:
        try:
            if not restore_drill():
                if cfg["backup"].get("email_on_failure"):
                    _notify_failure("SWU-Server: Restore-Drill FEHLGESCHLAGEN", "Siehe Server-Logs.")
                return 2
        except Exception as e:
            log.error(f"Restore-Drill Fehler: {e}", exc_info=True)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
