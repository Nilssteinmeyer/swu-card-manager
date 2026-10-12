"""Tests fuer deployment_mode + switch_mode.py (Task A1/A2)."""
import sys
from pathlib import Path

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

import switch_mode  # noqa: E402


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """Lenkt PROJECT-Konstanten auf ein Temp-Verzeichnis."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    prod = cfg_dir / "production.yaml"
    prod.write_text(
        "server:\n"
        "  deployment_mode: \"local\"\n"
        "  app_host: \"0.0.0.0\"\n"
        "  app_port: 8765\n"
        "  public_domain: \"example.duckdns.org\"\n"
        "mail:\n"
        "  host: \"\"\n"
        "  port: 587\n"
        "  user: \"\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(switch_mode, "PROD_YAML", prod)
    monkeypatch.setattr(switch_mode, "TOKEN_FILE", cfg_dir / "duckdns_token")
    monkeypatch.setattr(switch_mode, "CADDY", tmp_path / "tools" / "caddy.exe")
    monkeypatch.setattr(switch_mode, "_public_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(switch_mode, "_domain_ip", lambda d: "1.2.3.4")
    return prod


def _read(prod: Path) -> dict:
    return yaml.safe_load(prod.read_text(encoding="utf-8"))


# --- A1: echte production.yaml hat den Schalter ---
def test_production_yaml_has_deployment_mode():
    cfg = yaml.safe_load(
        (PROJECT / "config" / "production.yaml").read_text(encoding="utf-8")
    )
    assert cfg["server"]["deployment_mode"] in ("local", "internet")
    assert cfg["server"]["app_host"] in ("0.0.0.0", "127.0.0.1")
    assert isinstance(cfg["server"]["app_port"], int)


# --- A2: Wechsel local -> internet ohne Voraussetzungen scheitert ---
def test_switch_to_internet_without_prereqs_fails(temp_env):
    cfg = switch_mode._load_cfg()
    problems = switch_mode.check_internet_prereqs(cfg)
    assert any("DuckDNS-Token" in p for p in problems)
    assert any("SMTP" in p for p in problems)
    assert any("caddy.exe" in p for p in problems)


# --- A2: mit (fake) Voraussetzungen klappt der Wechsel ---
def test_switch_to_internet_with_prereqs(temp_env):
    switch_mode.TOKEN_FILE.write_text("faketoken123", encoding="utf-8")
    tools = switch_mode.CADDY.parent
    tools.mkdir(parents=True, exist_ok=True)
    switch_mode.CADDY.write_bytes(b"MZ")
    cfg = switch_mode._load_cfg()
    cfg["mail"]["host"] = "smtp.gmail.com"
    cfg["mail"]["user"] = "swuscanner@gmail.com"
    with open(temp_env, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)

    problems = switch_mode.check_internet_prereqs(cfg)
    assert problems == [], f"unerwartete Probleme: {problems}"

    switch_mode._save_server_fields(cfg, "internet")
    after = _read(temp_env)["server"]
    assert after["deployment_mode"] == "internet"
    assert after["app_host"] == "127.0.0.1"
    assert after["app_port"] == 8443


# --- A2: zurueck auf local ---
def test_switch_back_to_local(temp_env):
    cfg = switch_mode._load_cfg()
    switch_mode._save_server_fields(cfg, "local")
    after = _read(temp_env)["server"]
    assert after["deployment_mode"] == "local"
    assert after["app_host"] == "0.0.0.0"
    assert after["app_port"] == 8765


# --- A2: DNS-Abweichung wird als Problem gemeldet ---
def test_dns_mismatch_reported(temp_env, monkeypatch):
    monkeypatch.setattr(switch_mode, "_domain_ip", lambda d: "9.9.9.9")
    cfg = switch_mode._load_cfg()
    problems = switch_mode.check_internet_prereqs(cfg)
    assert any("zeigt auf 9.9.9.9" in p for p in problems)
