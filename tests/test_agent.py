from __future__ import annotations

import json
from pathlib import Path

from src.skill_agent.agent.agent import SkillChatAgent
from src.skill_agent.agent.loop import AgentLoopEvent
from src.skill_agent.schemas.skill_model import PublishResult, ValidationReport
from src.skill_agent.generation.pipeline import PipelineTrace

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _tool_call(id: str, name: str, args: dict) -> dict:
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _make_agent(provider, generator_provider, tmp_path) -> SkillChatAgent:
    return SkillChatAgent(
        provider=provider,
        generator_provider=generator_provider,
        skills_dir=SKILLS_DIR,
        workspace_dir=tmp_path,
    )


def test_filter_skills_surfaces_skill_generator_for_generation_request(mock_provider, tmp_path):
    agent = _make_agent(mock_provider, mock_provider, tmp_path)

    payload = json.loads(
        agent._tool_filter_skills(
            query="generate and publish a new skill for scraping links from websites",
            requested_action="generate",
        )
    )

    skill_ids = [candidate["skill_id"] for candidate in payload["candidates"]]
    assert "skill-generator" in skill_ids


def test_run_turn_stores_tool_messages_in_history(monkeypatch, tmp_path):
    """Tool-call chain from a turn is persisted in history so the LLM can
    see which tools/skills were used when building context for the next turn."""
    calls = {"n": 0}
    second_turn_roles: list[str] = []

    class FakeProvider:
        def invoke(self, messages, tools=None, on_delta=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "c1",
                            "filter_skills",
                            {"query": "write an obsidian note about sprint planning", "requested_action": "create"},
                        )
                    ],
                }
            if calls["n"] == 2:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _tool_call("c2", "load_skill", {"skill_id": "obsidian-note-writer"}),
                    ],
                }
            if calls["n"] == 3:
                return {
                    "role": "assistant",
                    "content": "Dùng obsidian-note-writer. Cho tao nội dung cụ thể nếu cần tạo note.",
                    "tool_calls": None,
                }
            # Second turn: capture the roles the LLM sees in history
            second_turn_roles.extend(m.get("role") for m in messages)
            return {
                "role": "assistant",
                "content": "Chưa có file nào được tạo ở turn trước.",
                "tool_calls": None,
            }

    agent = _make_agent(FakeProvider(), FakeProvider(), tmp_path)

    first_reply = agent.run_turn("write an obsidian note about sprint planning")
    second_reply = agent.run_turn("file ở đâu?")

    assert "obsidian-note-writer" in first_reply
    assert "Chưa có file nào" in second_reply

    # Tool messages from turn 1 must be visible to the LLM during turn 2
    assert "tool" in second_turn_roles, "role:tool messages should appear in history"

    # History: user + 4 intermediates (2×assistant_tool_call + 2×tool) + assistant + user + assistant
    assert len(agent.state.messages) == 8
    roles_in_history = {m["role"] for m in agent.state.messages}
    assert roles_in_history == {"user", "assistant", "tool"}


def test_build_skill_from_spec_tool_serializes_publish_result(monkeypatch, mock_provider, tmp_path):
    agent = _make_agent(mock_provider, mock_provider, tmp_path)

    def fake_build_skill_from_spec(spec, generator_provider, skills_dir, review_fn=None, max_retries=3, **kwargs):
        return (
            PublishResult(
                skill_name=spec.name,
                published=True,
                skill_path=str(skills_dir / spec.name),
                report=ValidationReport(
                    syntax_pass=True,
                    metadata_pass=True,
                    activation_pass=True,
                    execution_pass=True,
                    regression_pass=True,
                    publishable=True,
                ),
                message="published",
            ),
            PipelineTrace(events=[{"kind": "stage", "stage_num": 2, "stage": "generate", "attempt": 1, "max": 3, "msg": "Generating skill package"}]),
        )

    monkeypatch.setattr("src.skill_agent.agent.build_skill_from_spec", fake_build_skill_from_spec)

    raw = agent._tool_build_skill_from_spec(
        name="link-scraper",
        description="Extract links from HTML input.",
        purpose="Collect hyperlinks from a page.",
        inputs=["URL or HTML file path"],
        outputs=["List of extracted URLs"],
        workflow_steps=["Read input", "Parse HTML", "Extract href values", "Print URLs"],
        edge_cases=["empty page", "invalid HTML"],
        runtime="python",
        test_cases=[
            {
                "description": "basic fixture",
                "input": "file://fixtures/page.html",
                "expected_output": "https://example.com",
                "validation_method": "contains",
            }
        ],
    )

    payload = json.loads(raw)
    assert payload["published"] is True
    assert payload["skill_path"].endswith("link-scraper")
    assert payload["trace"]


def test_handle_event_preserves_full_tool_payloads(mock_provider, tmp_path):
    agent = _make_agent(mock_provider, mock_provider, tmp_path)
    events = []
    tool_output = "output-" * 100
    tool_error = "error-" * 100
    agent.event_sink = events.append

    agent._handle_event(
        AgentLoopEvent(
            type="tool_call",
            payload={"name": "debug_tool", "arguments": {"a": 1}, "output": tool_output},
        )
    )
    agent._handle_event(
        AgentLoopEvent(
            type="tool_error",
            payload={"name": "debug_tool", "error_type": "tool_execution_failed", "error": tool_error},
        )
    )

    assert events[0]["source"] == "agent"
    assert events[0]["output"] == tool_output
    assert events[1]["source"] == "agent"
    assert events[1]["error"] == tool_error
