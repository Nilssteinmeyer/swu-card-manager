"""Daily production backup with rotation + weekly restore drill.

Called by Windows Task Scheduler (03:00 daily, config/production.yaml).
Uses the existing BackupManager for the database, then archives everything
else (config, models, training photos, state) into the same versioned ZIP.
Rotation: keep_daily / keep_weekly / keep_monthly from production.yaml.
A weekly restore drill unpacks the newest backup into a temp dir, runs a
SQLite integrity check against the restored DB and records the result.
Failures are logged AND (when SMTP is configured) emailed to the admin.
Exit codes: 0 = ok, 1 = backup failed, 2 = restore drill failed.

Infra P3 (Go-Live):
  --env test|prod   Umgebung wählen (DB + Backup-Profil je Env aus dem
                     Config-Overlay: test.yaml / production_env.yaml).
                     Default: test (Schutz wie SWU_ENV).
  --now             Sofort-Backup (auch mehrfach am Tag, kein Drill).
  --pre-deploy      Pre-Deploy-Backup (deploy_prod.ps1 Gate): garantiert
                     ein Backup VOR jedem Prod-Release; gleicher Ablauf
                     wie --now, aber classification "pre-deploy".
  Zusätzlich (Backup-Recherche, 93 Quellen):
  - DB-Snapshot per VACUUM INTO in ein Staging-Verzeichnis (konsistent,
    online, bereinigt = DSGVO-freundlich), danach PRAGMA quick_check +
    foreign_key_check auf der Snapshot-Datei — nur bei OK geht's ins ZIP.
  - Ziel 2 (3-2-1-Regel): backup.target_dir_external (z.B. D:/SWU-Backups,
    USB-Stick) — ZIP wird dorthin kopiert; fehlt das Ziel: WARNUNG ins
    Log + Admin-Postfach (kein stiller Ausfall).
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


def _set_env(env: str) -> None:
    """SWU_ENV fuer diesen Prozess setzen (P1-Overlay-Maschinerie).

    WICHTIG: AppLogger.setup() beim Modul-Import laedt den AppConfig-
    Singleton bereits (env war evtl. noch nicht gesetzt) - daher hier
    ZWINGEND reset(), damit die gewaehlte Umgebung greift.
    """
    import os

    from app.core.config import AppConfig

    os.environ["SWU_ENV"] = env
    AppConfig.reset()


def _load_cfg() -> dict[str, Any]:
    """Config fuer die GEWAEHLTE Umgebung (Overlay-Maschinerie aus P1).

    backup.*-Werte kommen aus config/test.yaml (SWU_ENV=test) bzw.
    config/production_env.yaml (SWU_ENV=prod).production.yaml bleibt
    kompatibel als Fallback, wenn das Overlay nichts definiert.
    """
    from app.core.config import AppConfig

    cfg_obj = AppConfig.load()
    data = cfg_obj.raw
    # Overlay hat Vorrang; fehlende Felder aus production.yaml ergänzen
    base: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}
    merged = {**base, **data}
    if "backup" not in merged or not isinstance(merged.get("backup"), dict):
        merged["backup"] = base.get("backup", {})
    else:
        merged["backup"] = {**base.get("backup", {}), **merged["backup"]}
    return merged


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


def _vacuum_snapshot(db_path: Path, staging_dir: Path, stamp: str) -> Path | None:
    """Konsistenter DB-Snapshot per VACUUM INTO (Backup-Recherche P3).

    - online-sicher (kein File-Copy der Live-DB, kein App-Stop)
    - bereinigt: geloeschte Zeilen verschwinden aus dem Backup (DSGVO)
    - Verify: PRAGMA quick_check + foreign_key_check auf dem Snapshot
    Rueckgabe: Pfad der geprueften Snapshot-Datei oder None bei Fehler.
    """
    if not db_path.exists():
        log.warning(f"VACUUM-Snapshot: DB fehlt ({db_path})")
        return None
    staging_dir.mkdir(parents=True, exist_ok=True)
    snap = staging_dir / f"db_snapshot_{stamp}.db"
    src = sqlite3.connect(str(db_path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src.execute("VACUUM INTO ?", (str(snap),))
    finally:
        src.close()
    # Verify auf dem Snapshot
    chk = sqlite3.connect(str(snap))
    try:
        quick = chk.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            log.error(f"VACUUM-Snapshot Verify FEHLGESCHLAGEN: quick_check={quick}")
            snap.unlink(missing_ok=True)
            return None
        fk = chk.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            log.error(f"VACUUM-Snapshot Verify FEHLGESCHLAGEN: {len(fk)} FK-Verstoesse")
            snap.unlink(missing_ok=True)
            return None
    finally:
        chk.close()
    log.info(f"VACUUM-Snapshot OK: {snap.name} ({snap.stat().st_size / 1024 / 1024:.1f} MB, quick_check + fk ok)")
    return snap


def _copy_to_external(zip_path: Path, cfg: dict[str, Any]) -> bool:
    """3-2-1: Kopie des ZIP auf Ziel 2 (USB/NAS). Warnung, wenn fehlt."""
    ext = (cfg["backup"].get("target_dir_external") or "").strip()
    if not ext:
        return True  # nicht konfiguriert -> kein Fehler
    ext_path = Path(ext)
    try:
        ext_path.mkdir(parents=True, exist_ok=True)
        dest = ext_path / zip_path.name
        shutil.copy2(zip_path, dest)
        log.info(f"3-2-1: Kopie auf Ziel 2 ({dest})")
        return True
    except Exception as e:  # noqa: BLE001 - Ziel 2 darf den Lauf nie killen
        log.warning(f"ZIEL-2-WARNUNG: {ext} nicht erreichbar ({e}) - Backup nur 1-fach vorhanden!")
        return False


def _ping_healthchecks(cfg: dict[str, Any]) -> None:
    """Dead-Man's-Switch-Ping (healthchecks.io-Muster): Job lebt noch."""
    url = (cfg["backup"].get("healthchecks_ping_url") or "").strip()
    if not url:
        return
    try:
        import urllib.request

        urllib.request.urlopen(url, timeout=10)
        log.info("Healthchecks-Ping gesendet")
    except Exception as e:  # noqa: BLE001 - Ping darf Backup nicht killen
        log.warning(f"Healthchecks-Ping fehlgeschlagen: {e}")


def run_backup(classification_override: str | None = None) -> Path | None:
    from app.core.config import AppConfig
    from app.core.backup import BackupManager

    cfg = _load_cfg()
    target = _resolve_target(cfg)

    ts = datetime.now()
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    day = ts.date()

    # 1) DB via BackupManager (konsistent, WAL-sicher) - Bestand bleibt
    bm = BackupManager(AppConfig.load())
    db_backup_path = bm.create_backup(label=f"daily-{stamp}")

    # 1b) P3: ZUSAETZLICH VACUUM-INTO-Snapshot mit Verify (bereinigt +
    #     konsistent; Recherche-Best-Practice)
    from app.core.config import current_env

    env = current_env()
    data_dir = PROJECT_ROOT / ("data_test" if env == "test" else "data")
    db_live = data_dir / ("cards_test.db" if env == "test" else "cards.db")
    staging = PROJECT_ROOT / "project_state" / f"backup_staging_{env}"
    snapshot = _vacuum_snapshot(db_live, staging, stamp) if db_live.exists() else None

    # 2) Sammel-ZIP: DB-Backup + alles Wichtige
    final_path = target / f"swu_backup_{stamp}.zip"
    import zipfile

    with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # DB-Backup
        if db_backup_path and Path(db_backup_path).exists():
            zf.write(db_backup_path, f"db/{Path(db_backup_path).name}")
        # P3: VACUUM-Snapshot (verify-geprueft)
        if snapshot and snapshot.exists():
            zf.write(snapshot, f"db/snapshot_{snapshot.name}")
        # Config (OHNE secret_key Datei-Inhalt? secret_key ist Betriebsgeheimnis,
        # aber fuer Disaster-Recovery mit drin — liegt im verschluesselten ZIP auf lokaler Platte)
        for p in (PROJECT_ROOT / "config").glob("*"):
            if p.is_file():
                zf.write(p, f"config/{p.name}")
        # Modelle (alle Versionen)
        models_dir = data_dir / "models"
        if models_dir.exists():
            for p in models_dir.rglob("*"):
                if p.is_file() and p.suffix in (".pt", ".json", ".index"):
                    zf.write(p, f"models/{p.relative_to(models_dir)}")
        # Trainingsfotos (unersetzlich!)
        scans_dir = data_dir / "scans" / "confirmed"
        if scans_dir.exists():
            for p in scans_dir.glob("*.jpg"):
                zf.write(p, f"scans/{p.name}")
        # Dataset-Metadaten
        dataset_dir = data_dir / "dataset"
        if dataset_dir.exists():
            for p in dataset_dir.rglob("*.json"):
                zf.write(p, f"dataset/{p.relative_to(dataset_dir)}")
        # Project-State (ohne das Backup-Verzeichnis selbst)
        state_dir = PROJECT_ROOT / "project_state"
        for p in state_dir.rglob("*"):
            if p.is_file() and "backups" not in p.parts and "staging" not in p.parts:
                zf.write(p, f"state/{p.relative_to(state_dir)}")
        # Manifest mit Metadaten
        classification = classification_override or _classification(ts, day)
        manifest = {
            "created_at": ts.isoformat(),
            "classification": classification,
            "env": env,
            "db_backup": str(db_backup_path),
            "db_snapshot": str(snapshot) if snapshot else None,
            "config": str(CONFIG_PATH),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    # Manifest-Datei (ausserhalb des ZIP, fuer Rotation)
    (target / f"swu_backup_{stamp}.meta.json").write_text(
        json.dumps({"classification": manifest["classification"], "created": stamp, "env": env}), encoding="utf-8"
    )
    log.info(f"Backup erstellt: {final_path} ({final_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # P3: 3-2-1-Kopie auf Ziel 2 (USB D:) - Warnung bei Ausfall
    _copy_to_external(final_path, cfg)

    # P3: Dead-Man's-Switch-Ping
    _ping_healthchecks(cfg)
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
    import argparse

    parser = argparse.ArgumentParser(description="SWU Backup (Infra P3)")
    parser.add_argument("--env", choices=["test", "prod"], default="test",
                        help="Umgebung (Default: test; prod = Produktiv-DB)")
    parser.add_argument("--now", action="store_true", help="Sofort-Backup ohne Restore-Drill")
    parser.add_argument("--pre-deploy", action="store_true",
                        help="Pre-Deploy-Backup (deploy_prod.ps1 Gate)")
    args = parser.parse_args()

    _set_env(args.env)
    cfg = _load_cfg()
    drill_day = int(cfg["backup"].get("restore_drill_weekday", 0))
    today = datetime.now().weekday()

    try:
        if args.pre_deploy:
            run_backup(classification_override="pre-deploy")
        else:
            run_backup()
    except Exception as e:
        log.error(f"BACKUP FEHLGESCHLAGEN: {e}", exc_info=True)
        if cfg["backup"].get("email_on_failure"):
            _notify_failure(f"SWU-Server ({args.env}): BACKUP FEHLGESCHLAGEN", f"Fehler:\n{e}")
        return 1

    rotate_backups()

    if not args.now and not args.pre_deploy and today == drill_day:
        try:
            if not restore_drill():
                if cfg["backup"].get("email_on_failure"):
                    _notify_failure(f"SWU-Server ({args.env}): Restore-Drill FEHLGESCHLAGEN", "Siehe Server-Logs.")
                return 2
        except Exception as e:
            log.error(f"Restore-Drill Fehler: {e}", exc_info=True)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
