from __future__ import annotations

import json
from typing import Any

import httpx

from src.skill_agent.providers.openai_provider import OpenAIProvider


class FakeResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                response=httpx.Response(self.status_code),
            )


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def raise_for_status(self) -> None:
        return None

    def read(self) -> bytes:
        return b""

    def iter_lines(self):
        yield from self._lines


def test_openai_provider_posts_current_chat_completion_contract(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_registry_manifest", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", max_tokens=123)
    result = provider.invoke(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_registry_manifest",
                    "description": "Return registry",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["max_completion_tokens"] == 123
    assert "max_tokens" not in captured["json"]
    assert captured["json"]["tool_choice"] == "auto"
    assert result["content"] == ""
    assert result["tool_calls"][0]["function"]["name"] == "get_registry_manifest"


def test_openai_provider_serializes_tool_round_trip_messages() -> None:
    messages = OpenAIProvider._serialize_messages(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "submit_workflow", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"accepted"}'},
        ]
    )

    assert messages[0] == {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "submit_workflow", "arguments": "{}"},
            }
        ],
    }
    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"status":"accepted"}',
    }


def test_openai_provider_streams_tool_call_chunks(monkeypatch) -> None:
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"submit_workflow","arguments":"{\\"workflow\\":"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" {}}"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    captured: dict[str, Any] = {}

    def fake_stream(method, url, json, headers, timeout):
        captured["json"] = json
        return FakeStreamResponse(lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini")
    deltas: list[str] = []
    result = provider.invoke(
        [{"role": "user", "content": "build"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "submit_workflow",
                    "description": "Submit",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        on_delta=deltas.append,
    )

    assert captured["json"]["stream"] is True
    assert result["tool_calls"][0]["id"] == "call_1"
    assert result["tool_calls"][0]["function"]["name"] == "submit_workflow"
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"workflow": {}}
