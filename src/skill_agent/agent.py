from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .loop import AgentLoop, AgentLoopEvent, Tool
from .models import Runtime, SkillSpec, SkillTestCase
from .pipeline import build_skill_from_spec
from .provider import LLMProvider
from .runtime import discover_skills, execute_skill, load_skill
from .runtime.models import ExecutionResult, SkillStub
from .sandbox import LocalSandboxRunner, SandboxRunner
from .sanitize import clean

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return clean((_PROMPTS_DIR / name).read_text(encoding="utf-8"))


@dataclass
class ChatAgentState:
    messages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SkillChatAgent:
    provider: LLMProvider
    generator_provider: LLMProvider
    skills_dir: Path
    workspace_dir: Path
    verbose: bool = False
    event_sink: Callable[[str], None] | None = None
    sandbox_runner: SandboxRunner | None = None
    state: ChatAgentState = field(default_factory=ChatAgentState)

    def __post_init__(self) -> None:
        self._system_prompt = _load_prompt("agent_system.md")
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._skill_stubs: list[SkillStub] | None = None

    def run_turn(self, user_input: str) -> str:
        user_text = clean(user_input.strip())
        messages = [
            {"role": "system", "content": self._system_prompt},
            *self.state.messages,
            {"role": "user", "content": user_text},
        ]
        loop = AgentLoop(
            self.provider,
            tools=self._make_tools(),
            on_event=self._handle_event if self.verbose else None,
        )
        result = loop.run_turn(messages)
        self.state.messages.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": result.content},
            ]
        )
        return result.content

    def _handle_event(self, event: AgentLoopEvent) -> None:
        if self.event_sink is None:
            return
        if event.type == "model_response":
            tool_calls = event.payload.get("tool_calls") or []
            if tool_calls:
                names = ", ".join(call["function"]["name"] for call in tool_calls)
                self.event_sink(f"[model] tool_calls → {names}")
            else:
                content = clean(str(event.payload.get("content") or ""))
                self.event_sink(f"[model] reply → {content[:240]}")
            return
        if event.type == "tool_call":
            name = event.payload.get("name", "")
            arguments = json.dumps(event.payload.get("arguments", {}), ensure_ascii=False)
            output = clean(str(event.payload.get("output", "")))
            self.event_sink(f"[tool] {name}({arguments})")
            self.event_sink(f"[tool] result → {output[:320]}")
            return
        if event.type == "tool_error":
            name = event.payload.get("name", "")
            error_type = event.payload.get("error_type", "tool_error")
            error = clean(str(event.payload.get("error", "")))
            self.event_sink(f"[tool:error] {name} [{error_type}] → {error[:320]}")

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
                parameters=self._build_skill_tool_parameters(),
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
        tokens = _tokenize(f"{query} {requested_action}")
        scored: list[tuple[int, SkillStub]] = []
        for stub in stubs:
            haystack = " ".join(
                [
                    stub.skill_id,
                    stub.name,
                    stub.description,
                    " ".join(stub.domain),
                    " ".join(stub.supported_actions),
                    " ".join(stub.forbidden_actions),
                    " ".join(stub.side_effects),
                ]
            )
            score = len(tokens & _tokenize(haystack))
            if requested_action and requested_action.lower() in [a.lower() for a in stub.supported_actions]:
                score += 2
            if requested_action and requested_action.lower() in [a.lower() for a in stub.forbidden_actions]:
                score -= 3
            scored.append((score, stub))

        if tokens:
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked = [(score, stub) for score, stub in scored if score > 0][:top_k]
        else:
            ranked = sorted(scored, key=lambda item: item[1].skill_id)[:top_k]

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
        return json.dumps(
            {
                "skill_id": loaded.stub.skill_id,
                "name": loaded.stub.name,
                "description": loaded.stub.description,
                "domain": loaded.stub.domain,
                "supported_actions": loaded.stub.supported_actions,
                "forbidden_actions": loaded.stub.forbidden_actions,
                "side_effects": loaded.stub.side_effects,
                "has_run_script": loaded.run_script is not None,
                "skill_md": loaded.skill_md,
                "logs": [log.message for log in logs],
            },
            ensure_ascii=False,
            indent=2,
        )

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
        return json.dumps(
            self._serialize_execution_result(result, cwd),
            ensure_ascii=False,
            indent=2,
        )

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
        spec = SkillSpec(
            name=name,
            description=description,
            purpose=purpose,
            inputs=inputs,
            outputs=outputs,
            workflow_steps=workflow_steps,
            edge_cases=edge_cases or [],
            required_files=required_files or ["SKILL.md", "scripts/run.py"],
            runtime=Runtime(runtime),
            test_cases=[SkillTestCase.model_validate(test) for test in (test_cases or [])],
        )
        result, trace = build_skill_from_spec(
            spec=spec,
            generator_provider=self.generator_provider,
            skills_dir=self.skills_dir,
            sandbox_runner=self.sandbox_runner,
        )
        if result.published:
            self._refresh_skill_stubs()
        return json.dumps(
            {
                "skill_name": result.skill_name,
                "published": result.published,
                "skill_path": result.skill_path,
                "message": result.message,
                "errors": result.report.errors,
                "warnings": result.report.warnings,
                "trace": trace.events,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _build_skill_tool_parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "purpose": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "workflow_steps": {"type": "array", "items": {"type": "string"}},
                "edge_cases": {"type": "array", "items": {"type": "string"}},
                "required_files": {"type": "array", "items": {"type": "string"}},
                "runtime": {"type": "string", "enum": [r.value for r in Runtime]},
                "test_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "input": {"type": "string"},
                            "expected_output": {"type": "string"},
                            "validation_method": {"type": "string"},
                            "fixtures": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                            "expected_stderr": {"type": "string"},
                            "expected_exit_code": {"type": "integer"},
                        },
                        "required": ["description", "input"],
                    },
                },
            },
            "required": [
                "name",
                "description",
                "purpose",
                "inputs",
                "outputs",
                "workflow_steps",
                "runtime",
                "test_cases",
            ],
        }

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

    def _serialize_execution_result(self, result: ExecutionResult, cwd: Path) -> dict[str, Any]:
        return {
            "skill_id": result.skill_id,
            "status": result.status,
            "execution_status": result.execution_status.value,
            "task_status": result.task_status.value,
            "exit_code": result.exit_code,
            "working_dir": str(cwd),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "artifact_path": _artifact_path_from_result(result, cwd),
            "logs": [log.message for log in result.logs],
        }


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _artifact_path_from_result(result: ExecutionResult, working_dir: Path) -> str | None:
    if result.status != "ok" or not result.stdout.strip():
        return None
    first_line = result.stdout.strip().splitlines()[0].strip()

    if result.skill_id == "obsidian-note-writer":
        match = re.search(r"Created:\s*(.+)$", first_line)
        if match:
            return str((working_dir / match.group(1).strip()).resolve())

    if result.skill_id == "obsidian-crud":
        if first_line.startswith("deleted: "):
            raw_path = first_line.removeprefix("deleted: ").strip()
        else:
            raw_path = first_line
        path = Path(raw_path)
        if not path.is_absolute():
            path = working_dir / path
        return str(path.resolve())

    return None
