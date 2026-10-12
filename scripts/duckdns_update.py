"""DuckDNS IP-Update — hält swu-cardscanner.duckdns.org auf die aktuelle IP.

Aufruf: python scripts/duckdns_update.py
Liest den Token aus der Umgebungsvariablen SWU_DUCKDNS_TOKEN oder aus
config/duckdns_token (gitignored, NUR auf diesem Rechner). Ohne Token: Exit 2
mit Anleitung. Wird vom geplanten Scheduled Task alle 5 Minuten laufen.
"""
from __future__ import annotations

import logging
import os
import sys
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("swu_manager.duckdns")

DOMAIN = "swu-cardscanner"
TOKEN_FILE = "config/duckdns_token"


def get_token() -> str | None:
    token = os.environ.get("SWU_DUCKDNS_TOKEN", "").strip()
    if not token:
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                token = f.read().strip()
        except FileNotFoundError:
            return None
    return token or None


def main() -> int:
    token = get_token()
    if not token:
        log.error(
            "Kein DuckDNS-Token gefunden. Lege config/duckdns_token an "
            "(Token von https://www.duckdns.org, NUR eine Zeile) oder setze "
            "die Umgebungsvariable SWU_DUCKDNS_TOKEN."
        )
        return 2

    url = f"https://www.duckdns.org/update?domains={DOMAIN}&token={token}&ip="
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            body = r.read().decode().strip()
        if body == "OK":
            log.info("DuckDNS-Update erfolgreich: %s.duckdns.org", DOMAIN)
            return 0
        if body.startswith("KO"):
            log.error("DuckDNS lehnte ab (falscher Token?): %s", body)
            return 3
        log.error("Unerwartete Antwort: %s", body)
        return 4
    except Exception as e:
        log.error("DuckDNS-Update fehlgeschlagen: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
