"""Supervisor: haelt beide SWU-Instanzen am Laufen (Infra P2).

Ohne-Admin-Watchdog (NSSM-Dienste brauchen UAC; dieses Skript liefert
bis dahin dieselben Garantien):
- prueft alle 15 s, ob die Ports 8765 (TEST) und 8443 (PROD) antworten
- startet eine abgestuerzte Instanz automatisch neu (unbeschraenkt,
  natuerliches Backoff durch Pruefintervall)
- Stop-Flags (config/.stop_test bzw. .stop_prod) erlauben kontrollierte
  Down-Phasen (Deploy/Wartung), ohne dass der Supervisor dazwischenfunkt
- Logs nach logs/supervisor.log (Appending)

Autostart: Windows-Aufgabe 'SWU-Supervisor' (Task Scheduler, je Benutzer,
keine Admin-Rechte) startet dieses Skript bei jeder Anmeldung.
Upgrade-Pfad: scripts/setup_services.ps1 (NSSM-Dienste, einmalig als Admin).
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

LOGS = PROJECT / "logs"
LOGS.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "supervisor.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("swu.supervisor")

PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"

INSTANCES = [
    {
        "name": "SWU-App-Test",
        "env": "test",
        "host": "0.0.0.0",
        "port": 8765,
        "stop_flag": PROJECT / "config" / ".stop_test",
    },
    {
        "name": "SWU-App-Prod",
        "env": "prod",
        "host": "127.0.0.1",
        "port": 8443,
        "stop_flag": PROJECT / "config" / ".stop_prod",
    },
]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _start(inst: dict) -> None:
    env = os.environ.copy()
    env["SWU_ENV"] = inst["env"]
    env.setdefault("SWU_SKIP_WARMUP", "1")  # Warmup macht die App selbst
    cmd = [str(PYTHON), "-m", "app.web", "--host", inst["host"], "--port", str(inst["port"])]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT),
            env=env,
            stdout=open(LOGS / f"supervisor_{inst['env']}.log", "ab"),
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        log.info("%s gestartet (Port %s, SWU_ENV=%s)", inst["name"], inst["port"], inst["env"])
    except Exception as e:  # noqa: BLE001 - Supervisor darf nie sterben
        log.error("%s Start fehlgeschlagen: %s", inst["name"], e)


def main() -> None:
    log.info("Supervisor laeuft (Pruefintervall 15 s, Instanzen: %s)",
             ", ".join(f"{i['name']}:{i['port']}" for i in INSTANCES))
    while True:
        for inst in INSTANCES:
            try:
                if inst["stop_flag"].exists():
                    continue  # kontrollierte Down-Phase (Deploy/Wartung)
                if not _port_open(inst["port"]):
                    log.warning("%s (Port %s) antwortet nicht -> Neustart",
                                inst["name"], inst["port"])
                    _start(inst)
            except Exception as e:  # noqa: BLE001
                log.error("Pruef-/Startfehler %s: %s", inst["name"], e)
        time.sleep(15)


if __name__ == "__main__":
    main()
