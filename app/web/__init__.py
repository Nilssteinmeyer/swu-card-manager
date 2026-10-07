"""Web UI package — Flask backend + static HTML/JS/CSS frontend."""
from __future__ import annotations

from app.web.app import create_app

__all__ = ["create_app"]
