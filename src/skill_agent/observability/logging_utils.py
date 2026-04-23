from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

_DEFAULT_LEVEL = "INFO"


class _HumanFormatter(logging.Formatter):
    """Readable, optionally colored console format."""

    _COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        ms = f"{int(record.msecs):03d}"
        level = f"{record.levelname:<8}"
        module = record.name.removeprefix("skill_agent.")
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        if sys.stderr.isatty():
            color = self._COLORS.get(record.levelname, "")
            return f"{ts}.{ms} {color}{level}{self._RESET} [{module}] {msg}"
        return f"{ts}.{ms} {level} [{module}] {msg}"


class _JsonFormatter(logging.Formatter):
    """JSON-lines format for machine-readable output (SKILL_AGENT_LOG_FORMAT=json)."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("event", "stage", "attempt", "tool", "status"):
            val = record.__dict__.get(key)
            if val is not None:
                entry[key] = val
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _coerce_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        candidate = logging.getLevelName(level.upper())
        if isinstance(candidate, int):
            return candidate
    return logging.INFO


def configure_logging(level: str | int | None = None) -> logging.Logger:
    """Configure the package logger once."""
    package_logger = logging.getLogger("skill_agent")

    if not package_logger.handlers:
        handler = logging.StreamHandler()
        use_json = os.environ.get("SKILL_AGENT_LOG_FORMAT", "").lower() == "json"
        handler.setFormatter(_JsonFormatter() if use_json else _HumanFormatter())
        package_logger.addHandler(handler)
        package_logger.propagate = False

    package_logger.setLevel(
        _coerce_level(level or os.environ.get("SKILL_AGENT_LOG_LEVEL", _DEFAULT_LEVEL))
    )
    return package_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
