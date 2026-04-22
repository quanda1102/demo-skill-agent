from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .logging_utils import get_logger
from .resilience import RetryPolicy, run_with_retry

LOGGER = get_logger("skill_agent.process")


@dataclass(frozen=True)
class SubprocessContract:
    timeout_seconds: float
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.0


def run_command(
    command: Sequence[str],
    *,
    contract: SubprocessContract,
    operation_name: str,
    input_text: str | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    def _invoke() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=contract.timeout_seconds,
            cwd=str(cwd) if cwd is not None else None,
        )

    return run_with_retry(
        operation_name=operation_name,
        func=_invoke,
        retry_policy=RetryPolicy(
            max_attempts=contract.max_attempts,
            backoff_seconds=contract.retry_backoff_seconds,
        ),
        logger=LOGGER,
        is_retryable=lambda exc: isinstance(exc, OSError) and not isinstance(exc, subprocess.TimeoutExpired),
    )
