from __future__ import annotations

import json

from src.skill_agent.agent.loop import AgentLoop, Tool


def _tool_call(name: str, arguments: str) -> dict:
    return {
        "id": "tc-1",
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def test_agent_loop_surfaces_tool_execution_errors_structurally():
    events = []

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, messages, tools=None, on_delta=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call("explode", json.dumps({"value": "boom"}))],
                }

            assert messages[-1]["role"] == "tool"
            payload = json.loads(messages[-1]["content"])
            assert payload["error_type"] == "tool_execution_failed"
            assert payload["tool"] == "explode"
            return {"role": "assistant", "content": "handled", "tool_calls": None}

    def _explode(value: str) -> str:
        raise ValueError(f"bad value: {value}")

    loop = AgentLoop(
        FakeProvider(),
        tools=[
            Tool(
                name="explode",
                description="Explodes for test coverage.",
                parameters={"type": "object", "properties": {"value": {"type": "string"}}},
                fn=_explode,
            )
        ],
        on_event=events.append,
    )

    result = loop.run_turn([{"role": "user", "content": "trigger the tool"}])

    assert result.content == "handled"
    assert any(event.type == "tool_error" for event in events)
