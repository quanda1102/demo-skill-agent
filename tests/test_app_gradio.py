from __future__ import annotations

import app_gradio
from src.skill_agent.validation.policy import ValidationPolicy
from src.skill_agent.workflow import WorkflowEvent, WorkflowState


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


class _FakeReviewAgent:
    def __init__(self):
        self.event_sink = None
        self.workflow_event_sink = None

    def run_turn(self, user_message: str) -> str:
        assert self.workflow_event_sink is not None
        self.workflow_event_sink(
            WorkflowEvent(
                type="human_review_requested",
                run_id="run_review",
                payload={
                    "pending_action_id": "pa_review",
                    "allowed_decisions": ["approve", "reject", "needs_changes"],
                    "title": "Review required",
                    "summary": "Review this generated skill before publishing.",
                },
            )
        )
        return f"done: {user_message}"


def test_send_message_preserves_full_tool_output(monkeypatch):
    tool_output = "tool-output-" * 80
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeAgent(tool_output))

    updates = list(app_gradio.send_message("run it", [], True, [], None, {}))
    chat = updates[-1][0]

    tool_messages = [message for message in chat if message.metadata.get("title") == "  debug_tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == f"```\n{tool_output}\n```"
    assert "status" not in tool_messages[0].metadata


def test_send_message_shows_build_trace_in_chat(monkeypatch):
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeBuildAgent())

    updates = list(app_gradio.send_message("build it", [], True, [], None, {}))
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
    for update in app_gradio.send_message("build it", [], True, [], None, {}):
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


def test_send_message_disables_chat_controls_while_review_is_pending(monkeypatch):
    monkeypatch.setattr(app_gradio, "_AGENT", _FakeReviewAgent())

    updates = list(app_gradio.send_message("build it", [], True, [], None, {}))
    final_update = updates[-1]

    assert final_update[1]["interactive"] is False
    assert final_update[4].waiting_for_human is True
    assert final_update[8]["interactive"] is False
    assert final_update[9]["interactive"] is False


def test_clear_session_is_blocked_while_review_is_pending():
    wf_state = WorkflowState(
        run_id="run_review",
        current="waiting_for_human",
        pending_action_id="pa_review",
    )
    history = ["existing history"]
    current_events = [{"source": "agent", "kind": "info", "msg": "pending review"}]
    review_meta = {"run_id": "run_review", "pending_action_id": "pa_review"}

    result = app_gradio.clear_session(True, history, current_events, wf_state, review_meta)

    assert result[0] == history
    assert result[4] == wf_state
    assert result[5] == review_meta
    assert result[8]["interactive"] is False
    assert result[9]["interactive"] is False


def test_load_policy_from_ui_invalid_path_keeps_active_policy(monkeypatch):
    baseline = ValidationPolicy(profile="baseline")

    class _DummyAgent:
        validation_policy = baseline

    monkeypatch.setattr(app_gradio, "_AGENT", _DummyAgent())
    monkeypatch.setattr(app_gradio, "_ACTIVE_POLICY", baseline)
    monkeypatch.setattr(app_gradio, "_ACTIVE_POLICY_SOURCE", "baseline-source")
    monkeypatch.setattr(app_gradio, "_POLICY_STATUS", "Ready.")

    result = app_gradio.load_policy_from_ui("does-not-exist.yaml")

    assert app_gradio._ACTIVE_POLICY is baseline
    assert app_gradio._ACTIVE_POLICY_SOURCE == "baseline-source"
    assert result[9].startswith("### Active Policy\nSource: `baseline-source`")
    assert "Failed to load policy" in result[10]
