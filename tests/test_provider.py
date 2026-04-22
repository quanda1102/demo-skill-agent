from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from src.skill_agent.provider import MinimaxProvider, ProviderCircuitOpenError, ProviderError


def _mock_response(content: str = "Done.") -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "id": "resp-1",
        "model": "MiniMax/MiniMax-M2.7",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content,
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return response


def test_minimax_provider_retries_transient_timeout(monkeypatch):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow response")
        return _mock_response("Recovered")

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = MinimaxProvider(endpoint="http://test", api_key="test-key", max_retries=2, retry_backoff_seconds=0.0)

    result = provider.invoke([{"role": "user", "content": "hello"}])

    assert result["content"] == "Recovered"
    assert calls["n"] == 3


def test_minimax_provider_raises_after_retry_budget(monkeypatch):
    def fake_post(*args, **kwargs):
        raise httpx.ReadTimeout("still timing out")

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = MinimaxProvider(endpoint="http://test", api_key="test-key", max_retries=1, retry_backoff_seconds=0.0)

    with pytest.raises(ProviderError, match="still timing out"):
        provider.invoke([{"role": "user", "content": "hello"}])


def test_minimax_provider_opens_circuit_after_failure_threshold(monkeypatch):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        raise httpx.ReadTimeout("backend unavailable")

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = MinimaxProvider(
        endpoint="http://test",
        api_key="test-key",
        max_retries=0,
        retry_backoff_seconds=0.0,
        circuit_failure_threshold=1,
        circuit_recovery_seconds=60.0,
    )

    with pytest.raises(ProviderError, match="backend unavailable"):
        provider.invoke([{"role": "user", "content": "hello"}])

    with pytest.raises(ProviderCircuitOpenError, match="circuit is open"):
        provider.invoke([{"role": "user", "content": "hello again"}])

    assert calls["n"] == 1
