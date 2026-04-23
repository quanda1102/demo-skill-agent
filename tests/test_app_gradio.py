from __future__ import annotations

import app_gradio


class _FakeAgent:
    def __init__(self, tool_output: str):
        self.tool_output = tool_output
        self.event_sink = None

    def run_turn(self, user_message: str) -> str:
        assert self.event_sink is not None
        self.event_sink({"source": "agent", "kind": "tool_start", "name": "debug_tool", "msg": "calling debug_tool"})
        self.event_sink(
            {
                "source": "agent",
                "kind": "tool",
                "name": "debug_tool",
                "output": self.tool_output,
                "msg": f"debug_tool → {self.tool_output[:120]}",
            }
        )
        return f"done: {user_message}"


class _FakeBuildAgent:
    def __init__(self):
        self.event_sink = None

    def run_turn(self, user_message: str) -> str:
        assert self.event_sink is not None
        self.event_sink({"source": "agent", "kind": "tool_start", "name": "build_skill_from_spec", "msg": "calling build_skill_from_spec"})
        self.event_sink({"source": "pipeline", "kind": "stage", "stage_num": 2, "stage": "generate", "attempt": 1, "max": 3, "msg": "Generating skill package"})
        self.event_sink({"source": "generator", "kind": "model_delta", "content": "drafting skill layout", "msg": "drafting skill layout"})
        self.event_sink({"source": "generator", "kind": "model", "action": "tool_calls", "tools": ["set_metadata", "write_file"], "msg": "generator calling set_metadata, write_file"})
        self.event_sink({"source": "generator", "kind": "tool", "name": "set_metadata", "output": "OK", "msg": "set_metadata → OK"})
        self.event_sink(
            {
                "source": "pipeline",
                "kind": "sandbox_case",
                "case_index": 1,
                "total_cases": 1,
                "description": "Basic two-word input",
                "rationale": "verify exact stdout for deterministic behavior",
                "expectation": "stdout string_match '2'; exit 0",
                "input_preview": "hello world",
                "fixture_paths": [],
                "msg": "Case 1/1: Basic two-word input",
            }
        )
        self.event_sink({"source": "pipeline", "kind": "info", "msg": "[Basic two-word input] pass: 2"})
        self.event_sink(
            {
                "source": "agent",
                "kind": "tool",
                "name": "build_skill_from_spec",
                "output": '{"published": true}',
                "msg": "build_skill_from_spec → published",
            }
        )
        return f"done: {user_message}"


def test_send_message_preserves_full_tool_output(monkeypatch):
    tool_output = "tool-output-" * 80
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeAgent(tool_output))

    updates = list(app_gradio.send_message("run it", [], True, []))
    chat = updates[-1][0]

    tool_messages = [message for message in chat if message.metadata.get("title") == "  debug_tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == f"```\n{tool_output}\n```"
    assert "status" not in tool_messages[0].metadata


def test_send_message_shows_build_trace_in_chat(monkeypatch):
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeBuildAgent())

    updates = list(app_gradio.send_message("build it", [], True, []))
    chat = updates[-1][0]

    trace_messages = [message for message in chat if message.metadata.get("title") == "  build trace"]
    assert len(trace_messages) == 1
    trace_content = trace_messages[0].content
    assert "Stage 2/5 — Generate" in trace_content
    assert "calling `set_metadata`, `write_file`" in trace_content
    assert "sandbox case 1/1" in trace_content
    assert "why: verify exact stdout for deterministic behavior" in trace_content


def test_send_message_streams_build_trace_in_chat(monkeypatch):
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeBuildAgent())

    snapshots = []
    for update in app_gradio.send_message("build it", [], True, []):
        chat = update[0]
        snapshots.append(
            [
                {
                    "metadata": dict(message.metadata),
                    "content": message.content,
                }
                for message in chat
            ]
        )

    assert any(
        any(
            message["metadata"].get("title") == "▸  build trace"
            and "drafting skill layout" in message["content"]
            for message in snapshot
        )
        for snapshot in snapshots[:-1]
    )
