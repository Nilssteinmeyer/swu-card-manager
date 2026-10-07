"""Backup and recovery tests."""
import pytest
from pathlib import Path

from app.core.config import AppConfig
from app.core.backup import BackupManager
from app.db.schema import connect, init_database
from app.db import repository as repo


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """Set up test environment with tmp paths."""
    AppConfig.reset()
    # Create a test config
    cfg = AppConfig.load()
    # Override paths to tmp
    cfg._data["paths"]["database"] = str(tmp_path / "test.db")
    cfg._data["paths"]["backup_dir"] = str(tmp_path / "backups")
    cfg._data["paths"]["images_dir"] = str(tmp_path / "images")
    cfg._data["paths"]["logs_dir"] = str(tmp_path / "logs")
    cfg._data["paths"]["dataset_dir"] = str(tmp_path / "dataset")
    cfg._data["paths"]["models_dir"] = str(tmp_path / "models")
    cfg.ensure_dirs()
    yield cfg


class TestBackup:
    def test_create_backup(self, setup_env):
        # Create a database first
        conn = connect(Path(setup_env.path("database")))
        init_database(conn)
        conn.close()

        bm = BackupManager(setup_env)
        backup_path = bm.create_backup("test")
        assert backup_path.exists()
        assert backup_path.suffix == ".zip"

    def test_list_backups(self, setup_env):
        conn = connect(Path(setup_env.path("database")))
        init_database(conn)
        conn.close()

        bm = BackupManager(setup_env)
        bm.create_backup("test1")
        bm.create_backup("test2")
        backups = bm.list_backups()
        assert len(backups) == 2

    def test_verify_integrity(self, setup_env):
        conn = connect(Path(setup_env.path("database")))
        init_database(conn)
        conn.close()

        bm = BackupManager(setup_env)
        bm.create_backup("test")
        result = bm.verify_integrity()
        assert result["database_ok"] is True
        assert len(result["backups"]) == 1
        assert result["backups"][0]["valid"] is True

    def test_backup_rotation(self, setup_env):
        conn = connect(Path(setup_env.path("database")))
        init_database(conn)
        conn.close()

        bm = BackupManager(setup_env)
        bm.max_backups = 3
        for i in range(5):
            bm.create_backup(f"test_{i}")
        backups = bm.list_backups()
        assert len(backups) <= 3
