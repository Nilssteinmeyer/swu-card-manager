"""
Centralised configuration loader.
Reads config/config.yaml from the project root and resolves all paths
to absolute paths. Provides a singleton AppConfig for the whole application.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"


def project_root() -> Path:
    """Return the absolute path to the project root."""
    return _PROJECT_ROOT


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
