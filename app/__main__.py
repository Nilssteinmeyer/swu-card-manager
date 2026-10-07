"""
Entry point for `python -m app`.
Supports subcommands:
  python -m app            — start the GUI
  python -m app doctor     — run system diagnosis
  python -m app sync       — sync card data from provider
  python -m app import-all — import all missing sets
  python -m app scan       — capture and recognise a single card
  python -m app backup     — create a backup
"""
from __future__ import annotations

import sys

from app.core.config import AppConfig


def main():
    AppConfig.load()
    from app.core.logging import AppLogger
    AppLogger.setup()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "gui"

    if cmd == "doctor":
        from app.core.doctor import run_doctor
        run_doctor()
    elif cmd == "sync":
        from app.providers.update_manager import UpdateManager
        um = UpdateManager()
        um.sync_sets()
    elif cmd == "import-all":
        from app.providers.update_manager import UpdateManager
        um = UpdateManager()
        um.full_sync(download_images=True)
    elif cmd == "scan":
        from app.vision.camera import CameraManager
        from app.recognition.engine import RecognitionEngine
        from app.db.schema import connect
        cam = CameraManager()
        engine = RecognitionEngine()
        frame = cam.capture_stable()
        if frame is not None:
            conn = connect()
            result = engine.recognize(frame, conn=conn)
            conn.close()
            print(f"Card: {result.card_name} | Confidence: {result.confidence:.1%} | Time: {result.processing_time_ms}ms")
            for c in result.candidates[:5]:
                print(f"  -> {c['name']} ({c['set_id']}-{c['card_number']}) score={c['score']:.2f}")
        cam.close()
    elif cmd == "backup":
        from app.core.backup import BackupManager
        bm = BackupManager()
        path = bm.create_backup("cli")
        print(f"Backup created: {path}")
    elif cmd == "gui" or cmd == "start":
        from app.ui.main_window import SWUApp
        app = SWUApp()
        app.protocol("WM_DELETE_WINDOW", app.on_closing)
        app.mainloop()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m app [gui|doctor|sync|import-all|scan|backup]")
        sys.exit(1)


if __name__ == "__main__":
    main()
