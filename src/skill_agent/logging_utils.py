from __future__ import annotations

import logging
import os

_DEFAULT_LEVEL = "INFO"
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _coerce_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        candidate = logging.getLevelName(level.upper())
        if isinstance(candidate, int):
            return candidate
    return logging.INFO


def configure_logging(level: str | int | None = None) -> logging.Logger:
    """
    Configure the package logger once.

    Entry points can call this to make retry/circuit-breaker failures visible
    without each module attaching its own handler.
    """
    package_logger = logging.getLogger("skill_agent")

    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        package_logger.addHandler(handler)
        package_logger.propagate = False

    package_logger.setLevel(_coerce_level(level or os.environ.get("SKILL_AGENT_LOG_LEVEL", _DEFAULT_LEVEL)))
    return package_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
