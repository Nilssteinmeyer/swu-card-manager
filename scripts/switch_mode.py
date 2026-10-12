"""Betriebsmodus wechseln: local <-> internet.

Der EINZIGE Weg, den Modus zu aendern (nie von Hand in production.yaml).
Prueft vor dem Wechsel auf 'internet' alle Voraussetzungen und bricht mit
klaren deutschen Meldungen + Exit-Code ab, wenn etwas fehlt:

  Exit 0 = umgeschaltet
  Exit 2 = Voraussetzung fehlt (Meldung sagt was + wie)

Aufruf:
  python scripts/switch_mode.py local
  python scripts/switch_mode.py internet
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import urllib.request
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("swu_manager.switch_mode")

PROJECT = Path(__file__).resolve().parents[1]
PROD_YAML = PROJECT / "config" / "production.yaml"
TOKEN_FILE = PROJECT / "config" / "duckdns_token"
CADDY = PROJECT / "tools" / "caddy.exe"

MODES = ("local", "internet")


def _load_cfg() -> dict:
    with open(PROD_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_server_fields(cfg: dict, mode: str) -> None:
    cfg["server"]["deployment_mode"] = mode
    cfg["server"]["app_host"] = "0.0.0.0" if mode == "local" else "127.0.0.1"
    cfg["server"]["app_port"] = 8765 if mode == "local" else 8443
    with open(PROD_YAML, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def _fail(msg: str, hint: str) -> int:
    log.error(msg)
    if hint:
        log.info("  → %s", hint)
    return 2


def _public_ip() -> str | None:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=15) as r:
            return r.read().decode().strip()
    except Exception as e:  # noqa: BLE001
        log.warning("Öffentliche IP nicht ermittelbar: %s", e)
        return None


def _domain_ip(domain: str) -> str | None:
    try:
        return socket.gethostbyname(domain)
    except OSError:
        return None


def check_internet_prereqs(cfg: dict) -> list[str]:
    """Liefert Liste fehlender Voraussetzungen (leer = alles ok)."""
    problems: list[str] = []
    if not TOKEN_FILE.exists() or not TOKEN_FILE.read_text(encoding="utf-8").strip():
        problems.append(
            "DuckDNS-Token fehlt: lege config/duckdns_token an "
            "(Token von https://www.duckdns.org, eine Zeile)"
        )
    mail = cfg.get("mail") or {}
    if not (mail.get("host") or "").strip():
        problems.append(
            "SMTP nicht konfiguriert: trage mail.host/mail.user in "
            "config/production.yaml ein (Installer Schritt 4/5)"
        )
    if not CADDY.exists():
        problems.append("tools/caddy.exe fehlt (Reverse-Proxy fuer den Internet-Modus)")
    domain = (cfg.get("server") or {}).get("public_domain") or ""
    if domain:
        pub = _public_ip()
        dip = _domain_ip(domain)
        if pub and dip and dip != pub:
            problems.append(
                f"{domain} zeigt auf {dip}, aber deine oeffentliche IP ist {pub} "
                "(DuckDNS-Task aktualisiert alle 5 Min — kurz warten und erneut versuchen)"
            )
        if dip is None:
            problems.append(
                f"{domain} loest nicht auf — Subdomain bei duckdns.org angelegt?"
            )
    else:
        problems.append("server.public_domain fehlt in config/production.yaml")
    return problems


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    cfg = _load_cfg()
    current = (cfg.get("server") or {}).get("deployment_mode", "local")

    if mode == current:
        log.info("Modus ist bereits '%s' — nichts zu tun.", mode)
        return 0

    if mode == "internet":
        problems = check_internet_prereqs(cfg)
        if problems:
            for p in problems:
                log.error("FEHLT: %s", p)
            return 2

    _save_server_fields(cfg, mode)
    log.info("Modus umgeschaltet auf '%s'.", mode)
    log.info("Neustart noetig: powershell -File server.ps1 -Restart")
    log.info("                 (bzw. Dienste neu starten, falls NSSM aktiv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
