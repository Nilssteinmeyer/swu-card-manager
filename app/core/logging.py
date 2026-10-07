"""
Structured logging for the entire application.
Provides a singleton logger that writes to both console and a rotating file.
Includes structured fields: timestamp, component, severity, event, message,
optionally task_id, card_id, set_id, exception.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import AppConfig

_LOGGER_NAME = "swu_manager"


class StructuredFormatter(logging.Formatter):
    """Custom formatter that produces semi-structured log lines."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        component = getattr(record, "component", record.name)
        event = getattr(record, "event", "")
        task_id = getattr(record, "task_id", "")
        card_id = getattr(record, "card_id", "")
        set_id = getattr(record, "set_id", "")

        parts = [f"[{ts}]", f"[{record.levelname}]", f"[{component}]"]
        if event:
            parts.append(f"[{event}]")
        if task_id:
            parts.append(f"[task={task_id}]")
        if card_id:
            parts.append(f"[card={card_id}]")
        if set_id:
            parts.append(f"[set={set_id}]")
        parts.append(record.getMessage())

        line = " ".join(parts)
        if record.exc_info and record.exc_text is None:
            import traceback

            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            line += f"\n{record.exc_text}"
        return line


class AppLogger:
    """Singleton logger manager."""

    _instance: "AppLogger | None" = None
    _logger: logging.Logger | None = None

    @classmethod
    def setup(cls, config: AppConfig | None = None) -> logging.Logger:
        if cls._logger is not None:
            return cls._logger

        if config is None:
            config = AppConfig.load()

        level_str = config.get("logging.level", "INFO")
        level = getattr(logging, level_str.upper(), logging.INFO)

        logs_dir = config.path("logs_dir")
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "swu_manager.log"

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(level)
        logger.handlers.clear()

        fmt = StructuredFormatter()

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # Rotating file handler
        max_bytes = config.get("logging.file_max_bytes", 5_242_880)
        backup_count = config.get("logging.file_backup_count", 5)
        fh = logging.handlers.RotatingFileHandler(
            str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        cls._logger = logger
        return logger

    @classmethod
    def get(cls) -> logging.Logger:
        if cls._logger is None:
            return cls.setup()
        return cls._logger


def get_logger(component: str = "app") -> logging.Logger:
    """Get a child logger for a specific component."""
    logger = AppLogger.get()
    child = logger.getChild(component)
    return child


def log_event(
    level: str,
    component: str,
    message: str,
    event: str = "",
    task_id: str = "",
    card_id: str = "",
    set_id: str = "",
    **kwargs: Any,
) -> None:
    """Log a structured event with extra fields."""
    logger = get_logger(component)
    extra: dict[str, Any] = {
        "component": component,
        "event": event,
        "task_id": task_id,
        "card_id": card_id,
        "set_id": set_id,
    }
    extra.update(kwargs)
    getattr(logger, level.lower(), logger.info)(message, extra=extra)
