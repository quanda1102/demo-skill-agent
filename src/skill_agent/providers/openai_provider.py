from __future__ import annotations

import json
import os
from typing import Callable

import httpx

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.providers.provider import LLMProvider, ProviderCircuitOpenError, ProviderError
from src.skill_agent.providers.resilience import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerError, RetryPolicy, run_with_retry

LOGGER = get_logger("skill_agent.provider.openai")

OPENAI_API_BASE = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    DEFAULT_MODEL = "gpt-4o-mini"
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
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 8096,
        tools: list | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        retry_backoff_multiplier: float | None = None,
        circuit_failure_threshold: int | None = None,
        circuit_recovery_seconds: float | None = None,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. "
                "Add it to your .env file or pass api_key= explicitly."
            )
        self.api_key = resolved_api_key
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", OPENAI_API_BASE)
        self.organization = os.environ.get("OPENAI_ORGANIZATION")
        self.project = os.environ.get("OPENAI_PROJECT")
        self.model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.tools = tools if tools else None
        self.timeout = timeout or self._default_timeout()
        self.max_retries = max_retries if max_retries is not None else self._env_int(
            "OPENAI_HTTP_MAX_RETRIES", self.DEFAULT_MAX_RETRIES
        )
        self.retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else self._env_float(
                "OPENAI_HTTP_RETRY_BACKOFF_SECONDS",
                self.DEFAULT_RETRY_BACKOFF_SECONDS,
            )
        )
        self.retry_backoff_multiplier = (
            retry_backoff_multiplier
            if retry_backoff_multiplier is not None
            else self._env_float(
                "OPENAI_HTTP_RETRY_BACKOFF_MULTIPLIER",
                self.DEFAULT_RETRY_BACKOFF_MULTIPLIER,
            )
        )
        self.retry_policy = RetryPolicy(
            max_attempts=self.max_retries + 1,
            backoff_seconds=self.retry_backoff_seconds,
            backoff_multiplier=self.retry_backoff_multiplier,
        )
        self.circuit_breaker = CircuitBreaker(
            name=f"openai:{self.model}",
            config=CircuitBreakerConfig(
                failure_threshold=(
                    circuit_failure_threshold
                    if circuit_failure_threshold is not None
                    else self._env_int(
                        "OPENAI_HTTP_CIRCUIT_FAILURE_THRESHOLD",
                        self.DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
                    )
                ),
                recovery_timeout_seconds=(
                    circuit_recovery_seconds
                    if circuit_recovery_seconds is not None
                    else self._env_float(
                        "OPENAI_HTTP_CIRCUIT_RECOVERY_SECONDS",
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
            connect=cls._env_float("OPENAI_HTTP_CONNECT_TIMEOUT", cls.DEFAULT_CONNECT_TIMEOUT),
            read=cls._env_float("OPENAI_HTTP_READ_TIMEOUT", cls.DEFAULT_READ_TIMEOUT),
            write=cls._env_float("OPENAI_HTTP_WRITE_TIMEOUT", cls.DEFAULT_WRITE_TIMEOUT),
            pool=cls._env_float("OPENAI_HTTP_POOL_TIMEOUT", cls.DEFAULT_POOL_TIMEOUT),
        )

    def invoke(
        self,
        messages: list,
        tools: list | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        if self.project:
            headers["OpenAI-Project"] = self.project

        payload = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_completion_tokens": self.max_tokens,
        }
        effective_tools = tools if tools is not None else self.tools
        if effective_tools:
            payload["tools"] = effective_tools
            payload["tool_choice"] = "auto"

        stream = on_delta is not None
        if stream:
            payload["stream"] = True

        operation_name = f"OpenAIProvider.invoke(model={self.model})"
        try:
            self.circuit_breaker.before_call()
        except CircuitBreakerError as exc:
            message = f"{operation_name} rejected because the circuit is open: {exc}"
            LOGGER.error(message)
            raise ProviderCircuitOpenError(message) from exc

        try:
            if stream:
                result = self._invoke_streaming(payload, headers, on_delta)
            else:
                response = run_with_retry(
                    operation_name=operation_name,
                    func=lambda: self._request_once(payload, headers),
                    retry_policy=self.retry_policy,
                    logger=LOGGER,
                    is_retryable=self._is_retryable_error,
                )
                data = response.json()
                choice = data["choices"][0]
                message = choice["message"]
                result = {
                    "role": "assistant",
                    "content": self._content_to_text(message.get("content")),
                    "tool_calls": message.get("tool_calls") or None,
                }
        except Exception as exc:
            self.circuit_breaker.record_failure(exc)
            message = f"{operation_name} failed: {exc}"
            LOGGER.error(message)
            raise ProviderError(message) from exc

        self.circuit_breaker.record_success()
        return result

    def _invoke_streaming(
        self,
        payload: dict,
        headers: dict[str, str],
        on_delta: Callable[[str], None],
    ) -> dict:
        content_parts: list[str] = []
        tool_calls_acc: list[dict] = []
        finish_reason = "stop"

        try:
            with httpx.stream(
                "POST",
                self.base_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            ) as response:
                if response.status_code != 200:
                    error_body = response.read().decode("utf-8", errors="replace")
                    LOGGER.error("OpenAI API error %s: %s", response.status_code, error_body)
                    try:
                        error_json = json.loads(error_body)
                        LOGGER.error("OpenAI error details: %s", json.dumps(error_json, indent=2))
                    except json.JSONDecodeError:
                        pass
                    response.raise_for_status()

                for raw_line in response.iter_lines():
                    line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta", {})

                    content = delta.get("content")
                    if content:
                        content_parts.append(content)
                        on_delta(content)

                    tool_call_chunks = delta.get("tool_calls") or []
                    for tc in tool_call_chunks:
                        idx = tc.get("index", 0)
                        while len(tool_calls_acc) <= idx:
                            tool_calls_acc.append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        if tc.get("id"):
                            tool_calls_acc[idx]["id"] = tc["id"]
                        if tc.get("type"):
                            tool_calls_acc[idx]["type"] = tc["type"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            tool_calls_acc[idx]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            tool_calls_acc[idx]["function"]["arguments"] += fn["arguments"]
                    if tool_call_chunks and not content:
                        on_delta("")
        except Exception as exc:
            raise ProviderError(f"OpenAI streaming request failed: {exc}") from exc

        full_content = "".join(content_parts)
        if finish_reason == "tool_calls" and tool_calls_acc:
            return {"role": "assistant", "content": full_content, "tool_calls": tool_calls_acc}
        return {"role": "assistant", "content": full_content or "", "tool_calls": None}

    def _request_once(self, payload: dict, headers: dict[str, str]) -> httpx.Response:
        response = httpx.post(
            self.base_url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _serialize_messages(messages: list) -> list[dict]:
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
                tool_id = getattr(m, "tool_call_id", None)

            if role == "tool":
                msg = {"role": "tool", "tool_call_id": tool_id}
                msg["content"] = str(content) if content is not None else ""
                out.append(msg)
            elif role == "assistant" and tool_calls:
                msg = {"role": "assistant", "tool_calls": tool_calls}
                if content is not None:
                    msg["content"] = content
                out.append(msg)
            elif role == "assistant":
                msg = {"role": "assistant"}
                if content is not None:
                    msg["content"] = content
                out.append(msg)
            elif role in ("system", "user"):
                msg = {"role": role}
                if content is not None:
                    msg["content"] = content
                out.append(msg)
        return out

    @staticmethod
    def _content_to_text(content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "".join(parts)
        return str(content)

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.TimeoutException):
            return True
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        return False
