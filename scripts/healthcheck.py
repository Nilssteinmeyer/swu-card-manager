"""Health-Check-Helfer fuer deploy_prod.ps1 (Infra P4).

PowerShell 5.1 hat KEIN -SkipCertificateCheck (erst PS 7) - Invoke-
WebRequest gegen das selbstsignierte Zertifikat schlaegt immer fehl.
Dieser Helfer nutzt requests mit verify=False und ist von der
PowerShell-Version unabhaengig.

Aufruf:  python scripts/healthcheck.py <url>
Exit 0  = HTTP 200 (bereit) · Exit 1 = nicht bereit / Fehler
"""
from __future__ import annotations

import sys

import requests
import urllib3

urllib3.disable_warnings()

url = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443/api/health/ready"
try:
    r = requests.get(url, verify=False, timeout=5)
    print(r.text[:200])
    sys.exit(0 if r.status_code == 200 else 1)
except Exception as e:  # noqa: BLE001 - Healthcheck meldet jeden Fehler mit Exit 1
    print(f"healthcheck: {e}")
    sys.exit(1)
