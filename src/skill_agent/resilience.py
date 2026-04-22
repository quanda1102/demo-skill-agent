from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitBreakerError(RuntimeError):
    """Raised when an operation is rejected because the circuit is open."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 30.0

    def normalized(self) -> "RetryPolicy":
        attempts = max(1, int(self.max_attempts))
        backoff = max(0.0, float(self.backoff_seconds))
        multiplier = max(1.0, float(self.backoff_multiplier))
        max_backoff = max(backoff, float(self.max_backoff_seconds))
        return RetryPolicy(
            max_attempts=attempts,
            backoff_seconds=backoff,
            backoff_multiplier=multiplier,
            max_backoff_seconds=max_backoff,
        )


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    half_open_success_threshold: int = 1

    def normalized(self) -> "CircuitBreakerConfig":
        return CircuitBreakerConfig(
            failure_threshold=max(1, int(self.failure_threshold)),
            recovery_timeout_seconds=max(0.0, float(self.recovery_timeout_seconds)),
            half_open_success_threshold=max(1, int(self.half_open_success_threshold)),
        )


class CircuitState(str, Enum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig,
        logger: logging.Logger,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.config = config.normalized()
        self.logger = logger
        self._clock = clock or time.monotonic
        self._state = CircuitState.closed
        self._opened_at: float | None = None
        self._consecutive_failures = 0
        self._half_open_successes = 0

    @property
    def state(self) -> CircuitState:
        return self._state

    def before_call(self) -> None:
        if self._state != CircuitState.open:
            return

        assert self._opened_at is not None
        elapsed = self._clock() - self._opened_at
        remaining = self.config.recovery_timeout_seconds - elapsed
        if remaining > 0:
            raise CircuitBreakerError(
                f"Circuit '{self.name}' is open after {self._consecutive_failures} consecutive failures; "
                f"retry after {remaining:.1f}s."
            )

        self._state = CircuitState.half_open
        self._half_open_successes = 0
        self.logger.warning("Circuit '%s' transitioned to half-open; allowing a probe call.", self.name)

    def record_success(self) -> None:
        if self._state == CircuitState.half_open:
            self._half_open_successes += 1
            if self._half_open_successes >= self.config.half_open_success_threshold:
                self.logger.info("Circuit '%s' closed after successful probe.", self.name)
                self._close()
            return

        if self._consecutive_failures:
            self.logger.info("Circuit '%s' reset after a successful call.", self.name)
        self._close()

    def record_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1

        if self._state == CircuitState.half_open:
            self.logger.error(
                "Circuit '%s' probe failed; reopening circuit. reason=%s",
                self.name,
                exc,
            )
            self._open()
            return

        if self._consecutive_failures >= self.config.failure_threshold:
            self.logger.error(
                "Circuit '%s' opened after %s consecutive failures. reason=%s",
                self.name,
                self._consecutive_failures,
                exc,
            )
            self._open()
            return

        self.logger.warning(
            "Circuit '%s' recorded failure %s/%s. reason=%s",
            self.name,
            self._consecutive_failures,
            self.config.failure_threshold,
            exc,
        )

    def _open(self) -> None:
        self._state = CircuitState.open
        self._opened_at = self._clock()
        self._half_open_successes = 0

    def _close(self) -> None:
        self._state = CircuitState.closed
        self._opened_at = None
        self._consecutive_failures = 0
        self._half_open_successes = 0


def run_with_retry(
    *,
    operation_name: str,
    func: Callable[[], T],
    retry_policy: RetryPolicy,
    logger: logging.Logger,
    is_retryable: Callable[[Exception], bool],
    sleep_fn: Callable[[float], None] | None = None,
) -> T:
    policy = retry_policy.normalized()
    sleeper = sleep_fn or time.sleep
    delay = policy.backoff_seconds

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except Exception as exc:
            retryable = is_retryable(exc)
            final_attempt = attempt >= policy.max_attempts
            if final_attempt or not retryable:
                logger.error(
                    "%s failed on attempt %s/%s. retryable=%s error=%s",
                    operation_name,
                    attempt,
                    policy.max_attempts,
                    retryable,
                    exc,
                )
                raise

            logger.warning(
                "%s failed on attempt %s/%s; retrying in %.2fs. error=%s",
                operation_name,
                attempt,
                policy.max_attempts,
                delay,
                exc,
            )
            if delay > 0:
                sleeper(delay)
            delay = min(policy.max_backoff_seconds, delay * policy.backoff_multiplier or delay)

    raise RuntimeError(f"{operation_name} exhausted its retry budget unexpectedly")
