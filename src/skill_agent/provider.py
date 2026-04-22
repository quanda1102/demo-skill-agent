from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .logging_utils import get_logger
from .resilience import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerError, RetryPolicy, run_with_retry
from .sanitize import clean
from .tool import MinimaxToolCall

LOGGER = get_logger("skill_agent.provider")


@dataclass
class MinimaxResponseMessage:
    role: str
    content: Optional[str]
    tool_calls: list[MinimaxToolCall] = field(default_factory=list)
    reasoning: Optional[str] = None


@dataclass
class MinimaxChoice:
    index: int
    finish_reason: str
    message: MinimaxResponseMessage


@dataclass
class MinimaxUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class MinimaxResponse:
    id: str
    model: str
    choices: list[MinimaxChoice]
    usage: MinimaxUsage

    @classmethod
    def from_dict(cls, data: dict) -> "MinimaxResponse":
        choices = [
            MinimaxChoice(
                index=c["index"],
                finish_reason=c["finish_reason"],
                message=MinimaxResponseMessage(
                    role=c["message"]["role"],
                    content=c["message"].get("content"),
                    reasoning=c["message"].get("reasoning"),
                    tool_calls=[
                        MinimaxToolCall.from_dict(tc)
                        for tc in (c["message"].get("tool_calls") or [])
                    ],
                ),
            )
            for c in data["choices"]
        ]
        usage_data = data.get("usage", {})
        usage = MinimaxUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        return cls(
            id=data.get("id", ""),
            model=data.get("model", ""),
            choices=choices,
            usage=usage,
        )


@dataclass
class MinimaxRequest:
    model: str
    messages: list[dict]
    temperature: float
    top_p: float
    max_tokens: int
    tools: Optional[list] = None
    response_format: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict = {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if self.tools:
            d["tools"] = self.tools
            d["tool_choice"] = "auto"
        if self.response_format:
            d["response_format"] = self.response_format
        return d


class LLMProvider(ABC):
    @abstractmethod
    def invoke(self, messages: list, tools: list | None = None) -> dict:
        ...


class ProviderError(RuntimeError):
    """Raised when a provider call fails after retries or returns invalid data."""


class ProviderCircuitOpenError(ProviderError):
    """Raised when a provider circuit breaker rejects a request."""


class MinimaxProvider(LLMProvider):
    DEFAULT_MODEL = "MiniMax/MiniMax-M2.7"
    DEFAULT_CONNECT_TIMEOUT = 10.0
    DEFAULT_READ_TIMEOUT = 180.0
    DEFAULT_WRITE_TIMEOUT = 30.0
    DEFAULT_POOL_TIMEOUT = 30.0
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
    DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0
    DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 3
    DEFAULT_CIRCUIT_RECOVERY_SECONDS = 30.0

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 8096,
        tools: list | None = None,
        api_key: str | None = None,
        response_format: dict | None = None,
        timeout: httpx.Timeout | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        retry_backoff_multiplier: float | None = None,
        circuit_failure_threshold: int | None = None,
        circuit_recovery_seconds: float | None = None,
    ) -> None:
        resolved_endpoint = endpoint or os.environ.get("MINIMAX_ENDPOINT")
        if not resolved_endpoint:
            raise ValueError(
                "MINIMAX_ENDPOINT is not set. "
                "Add it to your .env file or pass endpoint= explicitly."
            )
        self.endpoint = resolved_endpoint
        self.model = model or self.DEFAULT_MODEL
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.tools = tools if tools else None
        self.api_key = api_key
        self.response_format = response_format
        self.timeout = timeout or self._default_timeout()
        self.max_retries = max_retries if max_retries is not None else self._env_int(
            "MINIMAX_HTTP_MAX_RETRIES",
            self.DEFAULT_MAX_RETRIES,
        )
        self.retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else self._env_float(
                "MINIMAX_HTTP_RETRY_BACKOFF_SECONDS",
                self.DEFAULT_RETRY_BACKOFF_SECONDS,
            )
        )
        self.retry_backoff_multiplier = (
            retry_backoff_multiplier
            if retry_backoff_multiplier is not None
            else self._env_float(
                "MINIMAX_HTTP_RETRY_BACKOFF_MULTIPLIER",
                self.DEFAULT_RETRY_BACKOFF_MULTIPLIER,
            )
        )
        self.retry_policy = RetryPolicy(
            max_attempts=self.max_retries + 1,
            backoff_seconds=self.retry_backoff_seconds,
            backoff_multiplier=self.retry_backoff_multiplier,
        )
        self.circuit_breaker = CircuitBreaker(
            name=f"minimax:{self.model}",
            config=CircuitBreakerConfig(
                failure_threshold=(
                    circuit_failure_threshold
                    if circuit_failure_threshold is not None
                    else self._env_int(
                        "MINIMAX_HTTP_CIRCUIT_FAILURE_THRESHOLD",
                        self.DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
                    )
                ),
                recovery_timeout_seconds=(
                    circuit_recovery_seconds
                    if circuit_recovery_seconds is not None
                    else self._env_float(
                        "MINIMAX_HTTP_CIRCUIT_RECOVERY_SECONDS",
                        self.DEFAULT_CIRCUIT_RECOVERY_SECONDS,
                    )
                ),
            ),
            logger=LOGGER,
        )

    @classmethod
    def _env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def _env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @classmethod
    def _default_timeout(cls) -> httpx.Timeout:
        return httpx.Timeout(
            connect=cls._env_float("MINIMAX_HTTP_CONNECT_TIMEOUT", cls.DEFAULT_CONNECT_TIMEOUT),
            read=cls._env_float("MINIMAX_HTTP_READ_TIMEOUT", cls.DEFAULT_READ_TIMEOUT),
            write=cls._env_float("MINIMAX_HTTP_WRITE_TIMEOUT", cls.DEFAULT_WRITE_TIMEOUT),
            pool=cls._env_float("MINIMAX_HTTP_POOL_TIMEOUT", cls.DEFAULT_POOL_TIMEOUT),
        )

    def _serialize_messages(self, messages: list) -> list[dict]:
        out = []
        for m in messages:
            if isinstance(m, dict):
                role = m.get("role")
                content = m.get("content")
                tool_calls = m.get("tool_calls")
                tool_id = m.get("tool_call_id")
            else:
                role = getattr(m, "role", None)
                content = getattr(m, "content", None)
                tool_calls = getattr(m, "tool_calls", None)
                tool_id = getattr(m, "tool_id", None)

            safe_content = clean(content) if isinstance(content, str) else content

            if role == "tool":
                out.append({"role": "tool", "tool_call_id": tool_id, "content": safe_content})
            elif role == "assistant" and tool_calls:
                out.append({"role": "assistant", "content": safe_content, "tool_calls": tool_calls})
            elif role in ("system", "user", "assistant"):
                out.append({"role": role, "content": safe_content})
        return out

    def invoke(self, messages: list, tools: list | None = None) -> dict:
        serialized = self._serialize_messages(messages)
        request = MinimaxRequest(
            model=self.model,
            messages=serialized,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            tools=tools if tools is not None else self.tools,
            response_format=self.response_format,
        )

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        operation_name = f"MinimaxProvider.invoke(model={self.model})"
        try:
            self.circuit_breaker.before_call()
        except CircuitBreakerError as exc:
            message = f"{operation_name} rejected because the circuit is open: {exc}"
            LOGGER.error(message)
            raise ProviderCircuitOpenError(message) from exc

        try:
            response = run_with_retry(
                operation_name=operation_name,
                func=lambda: self._request_once(request, headers),
                retry_policy=self.retry_policy,
                logger=LOGGER,
                is_retryable=self._is_retryable_error,
            )
            data = response.json()
            parsed = MinimaxResponse.from_dict(data)
            if not parsed.choices:
                raise ValueError("response did not contain any choices")
        except Exception as exc:
            self.circuit_breaker.record_failure(exc)
            message = f"{operation_name} failed: {exc}"
            LOGGER.error(message)
            raise ProviderError(message) from exc

        self.circuit_breaker.record_success()
        choice = parsed.choices[0]

        if choice.finish_reason == "tool_calls":
            return {
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": data["choices"][0]["message"]["tool_calls"],
            }
        return {
            "role": "assistant",
            "content": choice.message.content,
            "tool_calls": None,
        }

    def _request_once(self, request: MinimaxRequest, headers: dict[str, str]) -> httpx.Response:
        response = httpx.post(
            self.endpoint,
            json=request.to_dict(),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.TimeoutException):
            return True
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        return False
