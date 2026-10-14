"""
Centralised configuration loader.
Reads config/config.yaml from the project root and resolves all paths
to absolute paths. Provides a singleton AppConfig for the whole application.

Umgebungen (Go-Live Infra-Konzept P1):
  SWU_ENV=test  -> config/test.yaml  + data_test/  (Default; lokale Test-App)
  SWU_ENV=prod  -> production.yaml-Pfad-Override + data/  (Produktiv-App)
Die Env-Datei wird zu den Basis-Werten aus config.yaml gemischt:
paths.database wird je Umgebung auf die getrennte DB gelenkt.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# DATEN-Root: normal der Paket-Root; im Prod-Worktree (../swu_prod) per
# Env SWU_HOME auf den Haupt-Tree umgelenkt, damit DB/Bilder/Config/SSL
# dort bleiben, wo sie sind (deploy_prod.ps1 setzt SWU_HOME beim Start).
_PROJECT_ROOT = Path(os.environ.get("SWU_HOME") or Path(__file__).resolve().parent.parent.parent).resolve()
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"


def project_root() -> Path:
    """Return the absolute path to the project root."""
    return _PROJECT_ROOT


def current_env() -> str:
    """Aktuelle Umgebung: 'test' (Default, MEINE Sammlung), 'prod' oder 'dev'.

    Rollen (Go-Live-Anpassung auf Nutzervunsch):
      test = persoenliche Haupt-App auf 8765 (eigene Daten + GPU-Scanner)
      prod  = oeffentliche Instanz auf 8443 hinter Caddy (kein Scan)
      dev   = leere Entwicklungs-Instanz auf 8766 (eigene Dev-DB)
    Default test: ein Start ohne Env-Variable trifft die eigene Sammlung,
    nie die oeffentliche Prod-DB.
    """
    env = (os.environ.get("SWU_ENV") or "test").strip().lower()
    return env if env in ("test", "prod", "dev") else "test"


def _resolve_paths(cfg: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve any 'paths' dict entries to absolute paths."""
    if "paths" in cfg and isinstance(cfg["paths"], dict):
        resolved = {}
        for key, val in cfg["paths"].items():
            p = Path(val)
            if not p.is_absolute():
                p = (_PROJECT_ROOT / p).resolve()
            resolved[key] = str(p)
        cfg["paths"] = resolved
    return cfg


def _env_overlay(cfg: dict[str, Any]) -> dict[str, Any]:
    """Mischt die Umgebungs-Datei ueber die Basis-Config (P1).

    SWU_ENV=test  -> config/test.yaml  (lenkt Pfade nach data_test/)
    SWU_ENV=prod  -> config/production_env.yaml falls vorhanden; sonst
                     bleiben die Basis-Pfade (data/cards.db) - die
                     bestehende Produktiv-Instanz laeuft genau so.
    Nur tiefes Merge der dicts; 'paths' wird komplett ersetzt, damit
    die Env-Datei die volle Kontrolle hat.
    """
    env = current_env()
    env_files = {
        "prod": "production_env.yaml",
        "dev": "development_env.yaml",
        "test": "test.yaml",
    }
    env_path = _PROJECT_ROOT / "config" / env_files[env]
    if not env_path.exists():
        return cfg

    with open(env_path, "r", encoding="utf-8") as f:
        overlay = yaml.safe_load(f) or {}

    def _deep_merge(base: dict, over: dict) -> dict:
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "paths":
                _deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    return _deep_merge(cfg, overlay)


class AppConfig:
    """Singleton-style config holder. Call AppConfig.load() once at startup."""

    _instance: "AppConfig | None" = None

    def __init__(self, data: dict[str, Any]):
        self._data = data
        self.root = _PROJECT_ROOT

    # -- access helpers -------------------------------------------------------
    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Get a nested value via dotted notation: get('database.type')."""
        keys = dotted_key.split(".")
        val: Any = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def path(self, name: str) -> Path:
        """Return a resolved Path object from the paths section."""
        p = self._data.get("paths", {}).get(name)
        if p is None:
            raise KeyError(f"Unknown path key: {name}")
        return Path(p)

    def ensure_dirs(self) -> None:
        """Create all configured directories if they don't exist.
        Paths with a file suffix (e.g. .db) are treated as files;
        only their parent directory is created."""
        for p in self._data.get("paths", {}).values():
            path = Path(p)
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
            else:
                path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AppConfig":
        if cls._instance is not None:
            return cls._instance
        p = Path(path) if path else _CONFIG_PATH
        with open(p, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        raw = _env_overlay(raw or {})
        raw = _resolve_paths(raw)
        cls._instance = cls(raw)
        cls._instance.ensure_dirs()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
