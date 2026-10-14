"""Infra P1: Umgebungs-Trennung (SWU_ENV), Health-Endpoints, Scan-503."""
from __future__ import annotations

import importlib

import pytest

from app.core import config as cfg_mod


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """AppConfig-Singleton vor/nach jedem Test zuruecksetzen."""
    cfg_mod.AppConfig.reset()
    yield
    cfg_mod.AppConfig.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test-Client mit eigener DB (Health/Scan brauchen keine Auth)."""
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "infra.db")
    from app.db.schema import connect, init_database

    conn = connect(tmp_path / "infra.db")
    init_database(conn)
    conn.close()
    cfg_mod.AppConfig.reset()
    from app.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    r = c.post("/api/auth/register", json={
        "username": "admininfra", "email": "a@x.de", "password": "pw123456"})
    assert r.status_code == 200
    r = c.post("/api/auth/login", json={"login": "admininfra", "password": "pw123456"})
    assert r.status_code == 200
    return c


def test_default_env_ist_test(monkeypatch):
    monkeypatch.delenv("SWU_ENV", raising=False)
    assert cfg_mod.current_env() == "test"


def test_env_prod_erkannt(monkeypatch):
    monkeypatch.setenv("SWU_ENV", "PROD")
    assert cfg_mod.current_env() == "prod"


def test_env_ungueltig_faellt_auf_test(monkeypatch):
    monkeypatch.setenv("SWU_ENV", "xyz")
    assert cfg_mod.current_env() == "test"


def test_test_env_lenkt_pfade_nach_data_test(monkeypatch, tmp_path):
    """SWU_ENV=test -> Overlay config/test.yaml -> data_test/cards_test.db."""
    monkeypatch.delenv("SWU_ENV", raising=False)
    cfg = cfg_mod.AppConfig.load()
    db = cfg.path("database")
    assert "data_test" in str(db)
    assert "cards_test.db" in db.name


def test_prod_env_lenkt_pfade_nach_data(monkeypatch):
    """SWU_ENV=prod -> Overlay production_env.yaml -> data/cards.db."""
    monkeypatch.setenv("SWU_ENV", "prod")
    cfg = cfg_mod.AppConfig.load()
    db = cfg.path("database")
    assert str(db).endswith("data\\cards.db") or str(db).endswith("data/cards.db")
    assert "data_test" not in str(db)


def test_prod_env_scan_disabled(monkeypatch):
    monkeypatch.setenv("SWU_ENV", "prod")
    cfg = cfg_mod.AppConfig.load()
    assert cfg.get("server.scan_enabled") is False
    assert cfg.get("server.deployment_mode") == "internet"


def test_test_env_scan_enabled(monkeypatch):
    monkeypatch.delenv("SWU_ENV", raising=False)
    cfg = cfg_mod.AppConfig.load()
    assert cfg.get("server.scan_enabled") is True


def test_backup_ziele_je_env(monkeypatch):
    """Prod hat Ziel 2 (D: USB), Test nicht; Rotation konfigurierbar."""
    monkeypatch.setenv("SWU_ENV", "prod")
    cfg = cfg_mod.AppConfig.load()
    assert cfg.get("backup.target_dir_external") == "D:/SWU-Backups"
    assert cfg.get("backup.keep_daily") == 7


def test_health_endpoints_ohne_auth(client):
    """Liveness/Readiness antworten ohne Login (Monitor-Ping)."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    assert r.get_json()["env"] == "test"

    r2 = client.get("/api/health/ready")
    assert r2.status_code == 200
    j = r2.get_json()
    assert j["status"] == "ready"
    assert j["db"] == "ok"
    assert isinstance(j["schema_version"], int)


def test_scan_enabled_aktion_in_test(client):
    """In der Test-Umgebung ist /api/scan aktiviert (kein 503-Wirtschaftlichkeits-Gate)."""
    r = client.post("/api/scan", json={})
    assert r.status_code != 503  # 400 (kein Bild) o.k. - Hauptsache nicht 503


def test_scan_503_wenn_deaktiviert(monkeypatch, client):
    """scan_enabled=false (Prod) -> 503 + deutscher Hinweis."""
    from app.web.app import create_app

    cfg = client.application.config["_SWU_CFG"]
    cfg._data["server"] = {"scan_enabled": False}
    r = client.post("/api/scan", json={})
    assert r.status_code == 503
    assert "Test-Umgebung" in r.get_json()["error"]


def test_api_system_zeigt_env(client):
    """/api/system liefert env + scan_enabled (Admin-Deployment-Sicht)."""
    r = client.get("/api/system")
    j = r.get_json()
    assert j["env"] == "test"
    assert j["scan_enabled"] is True


# ---- Infra P4: Wartungsmodus (503, Health-Ausnahme, Admin-Freiheit) ----

def test_maintenance_503_fuer_nutzer(client):
    from pathlib import Path
    from app.core.config import AppConfig

    root = AppConfig.load().root
    flag = root / "config" / "maintenance_on"
    flag.write_text("deploy", encoding="utf-8")
    # Nicht-Admin registrieren + einloggen (Admin darf in Wartung weiterarbeiten)
    client.post("/api/auth/register", json={
        "username": "familiemitglied", "email": "f@x.de", "password": "pw123456"})
    client.post("/api/auth/login", json={"login": "familiemitglied", "password": "pw123456"})
    try:
        r = client.get("/api/collection")
        assert r.status_code == 503
        assert "Wartungsarbeiten" in r.get_json()["error"]
        assert r.headers.get("Retry-After") == "600"
    finally:
        flag.unlink(missing_ok=True)


def test_maintenance_health_durchlaessig(client):
    from app.core.config import AppConfig

    root = AppConfig.load().root
    flag = root / "config" / "maintenance_on"
    flag.write_text("deploy", encoding="utf-8")
    try:
        r = client.get("/api/health")
        assert r.status_code == 200  # Monitor-Ping darf nicht blockiert werden
        r2 = client.get("/api/health/ready")
        assert r2.status_code == 200
    finally:
        flag.unlink(missing_ok=True)


def test_maintenance_seite_fuer_browser(tmp_path, monkeypatch):
    """HTML-Anfragen bekommen die Wartungsseite mit 503 (nie 200)."""
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "m.db")
    from app.db.schema import connect, init_database

    conn = connect(tmp_path / "m.db")
    init_database(conn)
    conn.close()
    from app.core.config import AppConfig

    AppConfig.reset()
    from app.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    flag = AppConfig.load().root / "config" / "maintenance_on"
    flag.write_text("deploy", encoding="utf-8")
    try:
        r = c.get("/collection")  # nicht eingeloggt -> Seite, kein API-JSON
        assert r.status_code == 503
        assert b"Wartungsarbeiten" in r.data
    finally:
        flag.unlink(missing_ok=True)
