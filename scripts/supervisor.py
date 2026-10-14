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

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib3
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

# Prod laeuft aus dem eigenen Git-Worktree ../swu_prod (auf dem Release-Tag
# gepinnt - deploy_prod.ps1 aktualisiert ihn). Fehlt der Worktree (erste
# Einrichtung), faellt der Supervisor auf den Haupt-Tree zurueck.
PROD_TREE = PROJECT.parent / "swu_prod"
_prod_home = PROD_TREE if (PROD_TREE / "app" / "web").exists() else PROJECT

# P5 Health-Monitoring: /api/health je Instanz; Ausfall -> Alert
# (E-Mail wenn SMTP konfiguriert - sonst Log). Cooldown 15 min je Instanz.
MONITOR_INTERVAL = 60
ALERT_COOLDOWN = 15 * 60
_last_alert: dict[str, float] = {}
_consecutive_fail: dict[str, int] = {}

INSTANCES = [
    {
        "name": "SWU-Main-8765",
        "env": "test",   # = MEINE Sammlung (Daten + Scanner)
        "host": "0.0.0.0",
        "port": 8765,
        "cwd": str(PROJECT),
        "stop_flag": PROJECT / "config" / ".stop_test",
    },
    {
        "name": "SWU-App-Prod",
        "env": "prod",
        "host": "127.0.0.1",
        "port": 8443,
        "cwd": str(_prod_home),
        "stop_flag": PROJECT / "config" / ".stop_prod",
    },
    {
        "name": "SWU-App-Dev",
        "env": "dev",
        "host": "127.0.0.1",
        "port": 8766,
        "cwd": str(PROJECT),
        "stop_flag": PROJECT / "config" / ".stop_dev",
    },
]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _monitor_only() -> bool:
    """SWU_SUPERVISOR_MODE=monitor: nur beobachten, keine Prozesse starten
    (Modus fuer den Betrieb mit NSSM-Diensten, die selbst neu starten)."""
    return (os.environ.get("SWU_SUPERVISOR_MODE") or "").strip().lower() == "monitor"


def _start(inst: dict) -> None:
    env = os.environ.copy()
    env["SWU_ENV"] = inst["env"]
    # Prod-Worktree: Daten-Root bleibt der HAUPT-Tree (DB/config/ssl/logs)
    if inst.get("cwd") and inst["env"] == "prod" and Path(inst["cwd"]) != PROJECT:
        env["SWU_HOME"] = str(PROJECT)
    env.setdefault("SWU_SKIP_WARMUP", "1")  # Warmup macht die App selbst
    cmd = [str(PYTHON), "-m", "app.web", "--host", inst["host"], "--port", str(inst["port"])]
    try:
        subprocess.Popen(
            cmd,
            cwd=inst.get("cwd", str(PROJECT)),
            env=env,
            stdout=open(LOGS / f"supervisor_{inst['env']}.log", "ab"),
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        log.info("%s gestartet (Port %s, SWU_ENV=%s)", inst["name"], inst["port"], inst["env"])
    except Exception as e:  # noqa: BLE001 - Supervisor darf nie sterben
        log.error("%s Start fehlgeschlagen: %s", inst["name"], e)


def _health_ok(inst: dict) -> tuple[bool, str]:
    """Liveness-Check gegen /api/health (ohne Zertifikats-Pruefung)."""
    import requests

    try:
        r = requests.get(f"https://localhost:{inst['port']}/api/health",
                         verify=False, timeout=8)
        data = r.json() if r.status_code == 200 else {}
        return r.status_code == 200, json.dumps(data, ensure_ascii=False)[:80]
    except Exception as e:  # noqa: BLE001 - Monitor darf nie sterben
        return False, str(e)[:80]


def _send_alert(inst_name: str, detail: str) -> None:
    """E-Mail wenn SMTP konfiguriert (app.auth.mail), sonst nur Log."""
    now = time.time()
    if now - _last_alert.get(inst_name, 0) < ALERT_COOLDOWN:
        return
    _last_alert[inst_name] = now
    log.error("ALERT %s: %s", inst_name, detail)
    try:
        sys.path.insert(0, str(PROJECT))
        os.environ.setdefault("SWU_ENV", "prod")
        from app.auth import mail as _mail

        if _mail.is_configured():
            import yaml

            prod = PROJECT / "config" / "production.yaml"
            cfg = yaml.safe_load(prod.read_text(encoding="utf-8")) or {}
            to = (cfg.get("mail") or {}).get("user", "")
            if to:
                body = (
                    "Instanz " + inst_name + " nicht erreichbar. Detail: " + detail
                    + " (Automatische Meldung des Supervisors, max. 1 Mail / 15 min)"
                )
                _mail.send_email(to, "SWU-Server ALERT: " + inst_name, body)
    except Exception as e:  # noqa: BLE001
        log.warning("Alert-Versand fehlgeschlagen: %s", e)


def _monitor_cycle() -> None:
    """P5: Health je Instanz; 2 aufeinanderfolgende Fails -> Alert."""
    for inst in INSTANCES:
        name = inst["name"]
        try:
            if inst["stop_flag"].exists():
                _consecutive_fail.pop(name, None)
                continue
            ok, detail = _health_ok(inst)
            if ok:
                if _consecutive_fail.pop(name, None):
                    log.info("%s wieder gesund (%s)", name, detail)
                continue
            _consecutive_fail[name] = _consecutive_fail.get(name, 0) + 1
            if _consecutive_fail[name] >= 2:
                _send_alert(name, detail)
        except Exception as e:  # noqa: BLE001
            log.error("Monitor-Fehler %s: %s", name, e)


def main() -> None:
    urllib3.disable_warnings()
    log.info("Supervisor laeuft (Restart-Pruefung 15 s, Health-Monitor 60 s; "
             "Instanzen: %s)",
             ", ".join(f"{i['name']}:{i['port']}" for i in INSTANCES))
    last_monitor = 0.0
    while True:
        for inst in INSTANCES:
            try:
                if inst["stop_flag"].exists():
                    continue  # kontrollierte Down-Phase (Deploy/Wartung)
                if not _port_open(inst["port"]):
                    if _monitor_only():
                        log.warning("%s (Port %s) DOWN - Monitor-Modus, kein "
                                    "Neustart (Dienst/NSSM sollte das tun)",
                                    inst["name"], inst["port"])
                    else:
                        log.warning("%s (Port %s) antwortet nicht -> Neustart",
                                    inst["name"], inst["port"])
                        _start(inst)
            except Exception as e:  # noqa: BLE001
                log.error("Pruef-/Startfehler %s: %s", inst["name"], e)
        if time.time() - last_monitor >= MONITOR_INTERVAL:
            last_monitor = time.time()
            _monitor_cycle()
        time.sleep(15)


if __name__ == "__main__":
    main()
