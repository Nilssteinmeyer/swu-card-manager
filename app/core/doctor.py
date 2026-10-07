"""
Diagnosis module — `python -m app doctor`
Checks: Python, dependencies, database, filesystem, write permissions,
camera, network, data provider, models, GPU, dataset, backups, config.
"""
from __future__ import annotations

import platform
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db.schema import connect, check_integrity, get_db_path
from app.db import repository as repo

log = get_logger("doctor")


class Doctor:
    """System self-diagnosis."""

    def __init__(self, config: AppConfig | None = None):
        self.cfg = config or AppConfig.load()
        self.results: list[dict[str, Any]] = []

    def _check(self, name: str, ok: bool, message: str, details: str = "") -> None:
        status = "OK" if ok else "FAIL"
        self.results.append({
            "name": name,
            "status": status,
            "message": message,
            "details": details,
        })
        symbol = "✓" if ok else "✗"
        print(f"  [{symbol}] {name}: {message}")

    def run_all(self) -> dict[str, Any]:
        """Run all diagnostic checks and return a summary."""
        print("=" * 60)
        print("  SWU Card Manager — System Diagnosis")
        print("=" * 60)

        self._check_python()
        self._check_dependencies()
        self._check_database()
        self._check_filesystem()
        self._check_write_permissions()
        self._check_camera()
        self._check_network()
        self._check_data_provider()
        self._check_gpu()
        self._check_models()
        self._check_dataset()
        self._check_backups()
        self._check_config()
        self._check_tesseract()

        passed = sum(1 for r in self.results if r["status"] == "OK")
        total = len(self.results)
        print("=" * 60)
        print(f"  Result: {passed}/{total} checks passed")
        print("=" * 60)

        return {
            "passed": passed,
            "total": total,
            "all_ok": passed == total,
            "results": self.results,
        }

    def _check_python(self) -> None:
        ver = sys.version
        self._check("Python Runtime", True, f"{platform.python_implementation()} {ver.split()[0]}")

    def _check_dependencies(self) -> None:
        deps = ["cv2", "numpy", "PIL", "yaml", "requests", "rapidfuzz", "imagehash"]
        missing = []
        for dep in deps:
            try:
                __import__(dep)
            except ImportError:
                missing.append(dep)
        ok = not missing
        msg = "All core dependencies present" if ok else f"Missing: {', '.join(missing)}"
        self._check("Dependencies", ok, msg)

    def _check_database(self) -> None:
        db_path = get_db_path()
        if not db_path.exists():
            self._check("Database", False, "Database file not found", str(db_path))
            return
        try:
            ok = check_integrity()
            conn = connect()
            card_count = repo.get_card_count(conn)
            conn.close()
            self._check("Database", ok, f"Integrity OK, {card_count} cards in DB")
        except Exception as e:
            self._check("Database", False, f"Error: {e}")

    def _check_filesystem(self) -> None:
        dirs = ["images_dir", "dataset_dir", "models_dir", "backup_dir", "logs_dir"]
        all_ok = True
        missing = []
        for d in dirs:
            try:
                p = self.cfg.path(d)
                if not p.exists():
                    missing.append(d)
                    all_ok = False
            except Exception:
                missing.append(d)
                all_ok = False
        msg = "All directories exist" if all_ok else f"Missing: {', '.join(missing)}"
        self._check("Filesystem", all_ok, msg)

    def _check_write_permissions(self) -> None:
        test_dirs = [self.cfg.path("images_dir"), self.cfg.path("backup_dir")]
        all_ok = True
        for d in test_dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                test_file = d / ".write_test"
                test_file.write_text("ok")
                test_file.unlink()
            except Exception:
                all_ok = False
        self._check("Write Permissions", all_ok, "All test writes succeeded" if all_ok else "Some directories not writable")

    def _check_camera(self) -> None:
        try:
            import cv2
            cap = cv2.VideoCapture(self.cfg.get("camera.device_index", 0))
            ok = cap.isOpened()
            cap.release()
            self._check("Camera", ok, "Webcam accessible" if ok else "Webcam not accessible")
        except Exception as e:
            self._check("Camera", False, f"Error: {e}")

    def _check_network(self) -> None:
        try:
            import requests
            resp = requests.get("https://api.swu-db.com/sets", timeout=10)
            ok = resp.status_code == 200
            self._check("Network", ok, f"API reachable ({resp.status_code})" if ok else f"API returned {resp.status_code}")
        except Exception as e:
            self._check("Network", False, f"Cannot reach API: {e}")

    def _check_data_provider(self) -> None:
        try:
            from app.providers.swudb_provider import SWUDBProvider
            provider = SWUDBProvider(self.cfg)
            sets = provider.get_sets()
            self._check("Data Provider", True, f"Retrieved {len(sets)} sets")
        except Exception as e:
            self._check("Data Provider", False, f"Error: {e}")

    def _check_gpu(self) -> None:
        try:
            import torch
            cuda = torch.cuda.is_available()
            msg = f"CUDA available: {torch.cuda.get_device_name(0)}" if cuda else "CPU-only (CUDA not available)"
            self._check("GPU/CUDA", True, msg)
        except ImportError:
            self._check("GPU/CUDA", True, "PyTorch not installed (OCR-only mode)")
        except Exception as e:
            self._check("GPU/CUDA", False, f"Error: {e}")

    def _check_models(self) -> None:
        models_dir = self.cfg.path("models_dir")
        models = list(models_dir.glob("*.pt")) + list(models_dir.glob("*.onnx")) if models_dir.exists() else []
        msg = f"{len(models)} models found" if models else "No trained models (using default recognition)"
        self._check("Models", True, msg)

    def _check_dataset(self) -> None:
        dataset_dir = self.cfg.path("dataset_dir")
        count = 0
        if dataset_dir.exists():
            count = sum(1 for _ in dataset_dir.rglob("*.png")) + sum(1 for _ in dataset_dir.rglob("*.jpg"))
        msg = f"{count} samples" if count else "No samples yet"
        self._check("Dataset", True, msg)

    def _check_backups(self) -> None:
        from app.core.backup import BackupManager
        bm = BackupManager(self.cfg)
        backups = bm.list_backups()
        msg = f"{len(backups)} backups available" if backups else "No backups yet"
        self._check("Backups", True, msg)

    def _check_config(self) -> None:
        config_path = self.cfg.root / "config" / "config.yaml"
        ok = config_path.exists()
        self._check("Config", ok, "config.yaml found" if ok else "config.yaml missing")

    def _check_tesseract(self) -> None:
        tesseract_path = Path(self.cfg.get("ocr.tesseract_path", ""))
        ok = tesseract_path.exists()
        self._check("Tesseract OCR", ok, f"Found at {tesseract_path}" if ok else "Tesseract not found")


def run_doctor() -> dict[str, Any]:
    """Entry point for `python -m app doctor`."""
    AppConfig.load()
    from app.core.logging import AppLogger
    AppLogger.setup()
    doctor = Doctor()
    return doctor.run_all()
