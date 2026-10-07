"""Data provider package — pluggable card data sources."""
from __future__ import annotations

from app.providers.official_provider import OfficialProvider
from app.providers.swudb_provider import SWUDBProvider

__all__ = ["OfficialProvider", "SWUDBProvider"]
