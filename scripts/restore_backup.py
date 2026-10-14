"""Restore eines SWU-Backups (Infra P3 - der Ernstfall).

Aufruf:
  python scripts/restore_backup.py --env prod --from "C:/SWU-ServerBackups/prod/swu_backup_...zip" [--yes]

Ablauf:
  1. ZIP validieren (manifest.json env konsistent, VACUUM-Snapshot verify)
  2. Supervisor-Stop-Flag setzen (Instanz bleibt kontrolliert down)
  3. DB durch den verify-geprueften VACUUM-Snapshot ersetzen
     (-wal/-shm loeschen, Recherche-Regel nach DB-Restore)
  4. Modelle/Config optional mit restaurieren (--with-models/--with-config)
  5. Freigabe + Health-Check-Hinweis

Der Supervisor erkennt das Stop-Flag und haelt die Instanz herunter,
bis das Flag entfernt wird (kontrollierte Down-Phase).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os


def _set_env(env: str) -> None:
    from app.core.config import AppConfig

    os.environ["SWU_ENV"] = env
    AppConfig.reset()


def main() -> int:
    parser = argparse.ArgumentParser(description="SWU Restore (Infra P3)")
    parser.add_argument("--env", choices=["test", "prod", "dev"], required=True)
    parser.add_argument("--from", dest="source", required=True, help="Pfad zum Backup-ZIP")
    parser.add_argument("--yes", action="store_true", help="Keine Rueckfrage (Skript/Deploy)")
    parser.add_argument("--with-models", action="store_true", help="Modelle mit restaurieren")
    parser.add_argument("--with-config", action="store_true", help="Config mit restaurieren")
    args = parser.parse_args()

    _set_env(args.env)

    src = Path(args.source)
    if not src.exists():
        print(f"FEHLER: Backup nicht gefunden: {src}")
        return 1

    _set_env(args.env)
    from app.core.config import AppConfig

    db_target = AppConfig.load().path("database")
    stop_flag = PROJECT_ROOT / "config" / f".stop_{args.env}"

    # 1) ZIP inspizieren
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except KeyError:
            print("FEHLER: manifest.json fehlt im ZIP - kein SWU-Backup?")
            return 1
        snap_name = next((n for n in names if n.startswith("db/snapshot_")), None)
        if not snap_name:
            print("FEHLER: kein VACUUM-Snapshot im ZIP (altes Backup-Format?)")
            return 1

    if manifest.get("env") and manifest["env"] != args.env:
        print(f"FEHLER: Backup ist env={manifest['env']}, Ziel ist {args.env}")
        return 1

    print(f"Restore: {src.name}")
    print(f"  Umgebung: {args.env} | Ziel-DB: {db_target}")
    print(f"  Snapshot im ZIP: {snap_name} | erstellt: {manifest.get('created_at', '?')}")

    if not args.yes:
        antwort = input("  WIRKLICH restaurieren? DB wird ersetzt! [ja/nein] ")
        if antwort.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Abgebrochen.")
            return 1

    # 2) Supervisor-Stop (kontrollierte Down-Phase)
    stop_flag.write_text("restore", encoding="utf-8")
    print("  Stop-Flag gesetzt (Supervisor haelt Instanz unten).")
    import time

    time.sleep(16)  # ein Supervisor-Zyklus

    # 3) Snapshot extrahieren + verifizieren + einsetzen
    import tempfile

    with tempfile.TemporaryDirectory(prefix="swu_restore_") as td:
        with zipfile.ZipFile(src) as zf:
            zf.extract(snap_name, td)
        snap = Path(td) / snap_name
        chk = sqlite3.connect(str(snap))
        quick = chk.execute("PRAGMA quick_check").fetchone()[0]
        n_cards = chk.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        n_coll = chk.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
        chk.close()
        if quick != "ok":
            print(f"FEHLER: Snapshot-Verify im Restore: {quick} - NICHT eingesetzt!")
            stop_flag.unlink(missing_ok=True)
            return 1
        # wal/shm der Zieldatenbank loeschen (Recherche-Regel)
        for suffix in ("-wal", "-shm"):
            Path(str(db_target) + suffix).unlink(missing_ok=True)
        db_target.parent.mkdir(parents=True, exist_ok=True)
        snap.replace(db_target)
        print(f"  DB restauriert: {n_cards} Karten, {n_coll} Sammlungszeilen (quick_check ok)")

    # 4) Optional Modelle/Config
    if args.with_models:
        with zipfile.ZipFile(src) as zf:
            for n in names:
                if n.startswith("models/"):
                    dest = data_dir / n
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    zf.extract(n, data_dir.parent)
        print("  Modelle restauriert.")
    if args.with_config:
        with zipfile.ZipFile(src) as zf:
            for n in names:
                if n.startswith("config/"):
                    zf.extract(n, PROJECT_ROOT)
        print("  Config restauriert (secret_key mit drin - Disaster-Recovery-Konzept).")

    # 5) Freigabe
    stop_flag.unlink(missing_ok=True)
    print("  Stop-Flag entfernt - Supervisor startet die Instanz neu.")
    print("Restore abgeschlossen. Health pruefen: /api/health/ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
