from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.skill_agent.agent.agent_support import (
    rank_skill_stubs,
    serialize_execution_result_payload,
    serialize_loaded_skill_payload,
    serialize_publish_result_payload,
)
from src.skill_agent.agent.loop import AgentLoop, AgentLoopEvent, Tool
from src.skill_agent.memory import MemoryManager
from src.skill_agent.generation.pipeline import build_skill_from_spec
from src.skill_agent.prompt_loader import load_prompt
from src.skill_agent.providers.provider import LLMProvider
from src.skill_agent.runtime import discover_skills, execute_skill, load_skill
from src.skill_agent.runtime.models import SkillStub
from src.skill_agent.sandbox import SandboxRunner
from src.skill_agent.sanitize import clean
from src.skill_agent.schemas.skill_spec_schema import AGENT_BUILD_SKILL_TOOL_PARAMETERS, build_skill_spec
from src.skill_agent.observability.trace_events import adapt_loop_event


class _AgentStateView:
    """
    Backward-compat shim so callers can read agent.state.messages and get
    a live view of the MemoryManager history.

    The list returned by .messages is a snapshot copy — modifying it has no
    effect on the manager's history. Use MemoryManager directly for writes.
    """
    __slots__ = ("_manager",)

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def messages(self) -> list[dict]:
        return self._manager.history


@dataclass
class SkillChatAgent:
    provider: LLMProvider
    generator_provider: LLMProvider
    skills_dir: Path
    workspace_dir: Path
    verbose: bool = False
    event_sink: Callable[[dict], None] | None = None
    sandbox_runner: SandboxRunner | None = None
    # Pass a pre-configured MemoryManager to override storage location or inject
    # a custom MemoryProvider backend. When None, one is created automatically
    # at workspace_dir/.memory using local SQLite + JSON storage.
    memory_manager: MemoryManager | None = field(default=None)

    def __post_init__(self) -> None:
        self._system_prompt = load_prompt("agent_system.md")
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._skill_stubs: list[SkillStub] | None = None
        if self.memory_manager is None:
            self.memory_manager = MemoryManager.create(
                data_dir=self.workspace_dir / ".memory"
            )
        self.memory_manager.on_session_start()

    @property
    def state(self) -> _AgentStateView:
        """
        Backward-compatible view of conversation history.
        agent.state.messages returns the current session's user/assistant history.
        """
        assert self.memory_manager is not None
        return _AgentStateView(self.memory_manager)

    def run_turn(self, user_input: str) -> str:
        mm = self.memory_manager
        assert mm is not None
        user_text = clean(user_input.strip())
        messages = mm.build_context(self._system_prompt, user_text)
        loop = AgentLoop(
            self.provider,
            tools=self._make_tools(),
            on_event=self._handle_event if self.verbose else None,
        )
        result = loop.run_turn(messages)
        turn_messages = result.history[len(messages):]
        mm.on_turn_end(user_text, result.content, turn_messages=turn_messages)
        return result.content

    def reset_session(self) -> None:
        """End the current session and start a fresh one (e.g. UI clear button)."""
        assert self.memory_manager is not None
        self.memory_manager.reset()

    def _handle_event(self, event: AgentLoopEvent) -> None:
        if self.event_sink is None:
            return
        trace_event = adapt_loop_event(event, source="agent")
        if trace_event is not None:
            self.event_sink(trace_event)

    def _make_tools(self) -> list[Tool]:
        return [
            Tool(
                name="filter_skills",
                description=(
                    "Filter the skill catalog using skill id, name, description, and metadata "
                    "without loading SKILL.md. Always call this before load_skill for a new task."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "requested_action": {"type": "string"},
                        "top_k": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                fn=self._tool_filter_skills,
            ),
            Tool(
                name="load_skill",
                description=(
                    "Load the full SKILL.md for a specific skill after filtering narrowed the candidates."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string"},
                    },
                    "required": ["skill_id"],
                },
                fn=self._tool_load_skill,
            ),
            Tool(
                name="execute_skill",
                description=(
                    "Execute a loaded skill by passing the final stdin payload to scripts/run.py. "
                    "Use workspace_dir as the default working directory unless a different one is required."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string"},
                        "input_payload": {"type": "string"},
                        "working_dir": {"type": "string"},
                        "expected_output": {"type": "string"},
                        "validation": {
                            "type": "string",
                            "enum": ["string_match", "contains"],
                        },
                    },
                    "required": ["skill_id", "input_payload"],
                },
                fn=self._tool_execute_skill,
            ),
            Tool(
                name="build_skill_from_spec",
                description=(
                    "Generate, validate, sandbox-test, and publish a new skill from a normalized SkillSpec. "
                    "Only use this after loading the skill-generator skill and after you have enough information."
                ),
                parameters=AGENT_BUILD_SKILL_TOOL_PARAMETERS,
                fn=self._tool_build_skill_from_spec,
            ),
        ]

    def _tool_filter_skills(
        self,
        query: str,
        requested_action: str = "",
        top_k: int = 5,
    ) -> str:
        stubs = self._get_skill_stubs()
        ranked = rank_skill_stubs(
            stubs,
            query=query,
            requested_action=requested_action,
            top_k=top_k,
        )

        payload = {
            "query": query,
            "requested_action": requested_action,
            "candidates": [
                {
                    "skill_id": stub.skill_id,
                    "name": stub.name,
                    "description": stub.description,
                    "domain": stub.domain,
                    "supported_actions": stub.supported_actions,
                    "forbidden_actions": stub.forbidden_actions,
                    "side_effects": stub.side_effects,
                    "score": score,
                }
                for score, stub in ranked
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _tool_load_skill(self, skill_id: str) -> str:
        stub = self._find_stub(skill_id)
        if stub is None:
            return json.dumps({"error": f"Unknown skill_id: {skill_id}"}, ensure_ascii=False)
        loaded, logs = load_skill(stub)
        return json.dumps(serialize_loaded_skill_payload(loaded, logs), ensure_ascii=False, indent=2)

    def _tool_execute_skill(
        self,
        skill_id: str,
        input_payload: str,
        working_dir: str = "",
        expected_output: str = "",
        validation: str = "string_match",
    ) -> str:
        stub = self._find_stub(skill_id)
        if stub is None:
            return json.dumps({"error": f"Unknown skill_id: {skill_id}"}, ensure_ascii=False)
        loaded, _ = load_skill(stub)
        cwd = self._resolve_working_dir(working_dir)
        result = execute_skill(
            loaded,
            input_payload,
            expected_output=expected_output or None,
            validation=validation,
            cwd=cwd,
        )
        return json.dumps(serialize_execution_result_payload(result, working_dir=cwd), ensure_ascii=False, indent=2)

    def _tool_build_skill_from_spec(
        self,
        name: str,
        description: str,
        purpose: str,
        inputs: list[str],
        outputs: list[str],
        workflow_steps: list[str],
        edge_cases: list[str] | None = None,
        runtime: str = "python",
        test_cases: list[dict[str, Any]] | None = None,
        required_files: list[str] | None = None,
    ) -> str:
        spec = build_skill_spec(
            name=name,
            description=description,
            purpose=purpose,
            inputs=inputs,
            outputs=outputs,
            workflow_steps=workflow_steps,
            edge_cases=edge_cases or [],
            required_files=required_files,
            runtime=runtime,
            test_cases=test_cases,
        )
        result, trace = build_skill_from_spec(
            spec=spec,
            generator_provider=self.generator_provider,
            skills_dir=self.skills_dir,
            sandbox_runner=self.sandbox_runner,
            event_sink=self.event_sink,
        )
        if result.published:
            self._refresh_skill_stubs()
        return json.dumps(
            serialize_publish_result_payload(result, trace_events=trace.events),
            ensure_ascii=False,
            indent=2,
        )

    def _get_skill_stubs(self) -> list[SkillStub]:
        if self._skill_stubs is None:
            self._refresh_skill_stubs()
        return list(self._skill_stubs or [])

    def _refresh_skill_stubs(self) -> None:
        self._skill_stubs, _ = discover_skills(self.skills_dir)

    def _find_stub(self, skill_id: str) -> SkillStub | None:
        for stub in self._get_skill_stubs():
            if stub.skill_id == skill_id:
                return stub
        return None

    def _resolve_working_dir(self, working_dir: str) -> Path:
        if not working_dir:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            return self.workspace_dir
        path = Path(working_dir)
        if not path.is_absolute():
            path = self.workspace_dir / path
        path.mkdir(parents=True, exist_ok=True)
        return path
