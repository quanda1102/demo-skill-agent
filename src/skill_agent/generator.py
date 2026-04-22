from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .logging_utils import get_logger
from .loop import AgentLoop, AgentLoopError, Tool
from .models import GeneratedSkill, SkillFile, SkillMetadata, SkillSpec, SkillStatus, SkillTestCase
from .provider import LLMProvider, ProviderError
from .sanitize import clean

# Canonical taxonomy — duplicated here so the tool description stays self-contained.
# The validator in validator.py enforces this same list.
_TAXONOMY_VERBS = (
    "create read update delete "
    "list move copy rename archive extract "
    "count search summarize parse format validate transform convert encode decode "
    "sort filter split join hash "
    "fetch write append"
)

_SIDE_EFFECTS_ALLOWED = "file_read, file_write, file_delete, network, subprocess"

_PROMPTS_DIR = Path(__file__).parent / "prompts"
LOGGER = get_logger("skill_agent.generator")


class SkillAgentError(Exception):
    pass


def _load_prompt(name: str) -> str:
    return clean((_PROMPTS_DIR / name).read_text(encoding="utf-8"))


@dataclass
class SkillBuilder:
    _spec: SkillSpec
    _metadata: dict | None = field(default=None, init=False)
    _files: list[dict] = field(default_factory=list, init=False)
    _tests: list[dict] = field(default_factory=list, init=False)

    def set_metadata(
        self,
        name: str,
        description: str,
        runtime: str = "python",
        version: str = "0.1.0",
        owner: str = "skill-agent",
        entrypoints: list | None = None,
        domain: list | None = None,
        supported_actions: list | None = None,
        forbidden_actions: list | None = None,
        side_effects: list | None = None,
    ) -> str:
        self._metadata = {
            "name": name,
            "description": description,
            "version": version,
            "owner": owner,
            "runtime": runtime,
            "status": "generated",
            "entrypoints": entrypoints or [{"type": "skill_md", "path": "SKILL.md"}],
            # Normalize to lowercase so taxonomy checks are case-insensitive.
            "domain": [t.lower().strip() for t in (domain or [])],
            "supported_actions": [a.lower().strip() for a in (supported_actions or [])],
            "forbidden_actions": [a.lower().strip() for a in (forbidden_actions or [])],
            "side_effects": [s.lower().strip() for s in (side_effects or [])],
        }
        return "OK"

    def write_file(self, path: str, content: str, executable: bool = False) -> str:
        self._files = [f for f in self._files if f["path"] != path]
        self._files.append({"path": path, "content": content, "executable": executable})
        return "OK"

    def add_test_case(
        self,
        description: str,
        input: str,
        expected_output: str = "",
        validation_method: str = "string_match",
        fixtures: dict | None = None,
        expected_stderr: str | None = None,
        expected_exit_code: int | None = None,
    ) -> str:
        self._tests.append(
            {
                "description": description,
                "input": input,
                "expected_output": expected_output,
                "validation_method": validation_method,
                "fixtures": fixtures or {},
                "expected_stderr": expected_stderr,
                "expected_exit_code": expected_exit_code,
            }
        )
        return "OK"

    def to_generated_skill(self) -> GeneratedSkill:
        if not self._metadata:
            raise ValueError("set_metadata was never called")
        file_paths = {f["path"] for f in self._files}
        if "SKILL.md" not in file_paths:
            raise ValueError("SKILL.md was never written")

        scripts = [f["path"] for f in self._files if f.get("executable")]
        references = [f["path"] for f in self._files if f["path"].startswith("references/")]
        assets = [f["path"] for f in self._files if f["path"].startswith("assets/")]

        return GeneratedSkill(
            metadata=SkillMetadata(**self._metadata),
            files=[SkillFile(**f) for f in self._files],
            scripts=scripts,
            references=references,
            assets=assets,
            tests=[SkillTestCase(**t) for t in self._tests],
            spec=self._spec,
            status=SkillStatus.generated,
        )


def _make_tools(builder: SkillBuilder) -> list[Tool]:
    return [
        Tool(
            name="set_metadata",
            description=(
                "Set the skill's metadata including capability fields required by the "
                "runtime policy layer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Lowercase hyphen-slug skill name"},
                    "description": {"type": "string"},
                    "runtime": {"type": "string", "enum": ["python", "node", "shell", "other"]},
                    "version": {"type": "string"},
                    "owner": {"type": "string"},
                    "entrypoints": {"type": "array", "items": {"type": "object"}},
                    "domain": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1–4 lowercase topic-area tags (e.g. ['notes', 'obsidian'], ['files', 'archive'], ['text', 'analysis'])",
                    },
                    "supported_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            f"Action verbs from the taxonomy ONLY: {_TAXONOMY_VERBS}. "
                            "Use 1–4 verbs. Never invent new verbs outside this list."
                        ),
                    },
                    "forbidden_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            f"Action verbs from the taxonomy that are explicitly denied: {_TAXONOMY_VERBS}. "
                            "Read-only skills must include 'write', 'delete', 'update'."
                        ),
                    },
                    "side_effects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            f"Observable side-effects — allowed values ONLY: {_SIDE_EFFECTS_ALLOWED}. "
                            "Use [] for pure computation. Any value outside this list is invalid."
                        ),
                    },
                },
                "required": ["name", "description", "runtime", "domain", "supported_actions", "side_effects"],
            },
            fn=builder.set_metadata,
        ),
        Tool(
            name="write_file",
            description="Write a file into the skill package.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path, e.g. SKILL.md or scripts/run.py"},
                    "content": {"type": "string"},
                    "executable": {"type": "boolean", "description": "True for runnable scripts"},
                },
                "required": ["path", "content"],
            },
            fn=builder.write_file,
        ),
        Tool(
            name="add_test_case",
            description="Register a test case for the skill.",
            parameters={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "input": {"type": "string", "description": "Exact stdin input for the script"},
                    "expected_output": {
                        "type": "string",
                        "description": "Expected stdout output. Use an empty string when success/failure is validated via stderr or exit code.",
                    },
                    "validation_method": {
                        "type": "string",
                        "enum": ["string_match", "contains", "regex", "manual"],
                    },
                    "fixtures": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": (
                            "Files to create in the sandbox before this test runs. "
                            "Keys are relative paths (e.g. 'notes/meeting.md'), values are file content. "
                            "Use for read-only or search skills that need pre-existing files."
                        ),
                    },
                    "expected_stderr": {
                        "type": "string",
                        "description": "Expected stderr output for error-path tests.",
                    },
                    "expected_exit_code": {
                        "type": "integer",
                        "description": "Expected process exit code. Omit for normal success tests (defaults to 0).",
                    },
                },
                "required": ["description", "input"],
            },
            fn=builder.add_test_case,
        ),
    ]


class Generator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._system_prompt = _load_prompt("generator_system.md")

    def generate(self, spec: SkillSpec, errors: list[str] | None = None) -> GeneratedSkill:
        builder = SkillBuilder(_spec=spec)
        tools = _make_tools(builder)
        loop = AgentLoop(self.provider, tools=tools)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": "Generate a skill package for this spec:\n\n" + spec.model_dump_json(indent=2),
            },
        ]
        if errors:
            messages.append({
                "role": "user",
                "content": "The previous attempt failed with these errors — fix them in your new attempt:\n"
                + "\n".join(f"- {e}" for e in errors),
            })

        try:
            loop.run(messages)
        except ProviderError as exc:
            LOGGER.error("Generator provider call failed: %s", exc)
            raise SkillAgentError(f"Generator API error: {exc}") from exc
        except AgentLoopError as exc:
            LOGGER.error("Generator agent loop failed: %s", exc)
            raise SkillAgentError(str(exc)) from exc

        try:
            return builder.to_generated_skill()
        except ValueError as exc:
            LOGGER.error("Generator produced an incomplete skill: %s", exc)
            raise SkillAgentError(f"Generator produced an incomplete skill: {exc}") from exc
