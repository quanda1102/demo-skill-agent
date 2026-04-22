from __future__ import annotations

from pathlib import Path
from typing import Callable

from .logging_utils import get_logger
from .loop import AgentLoop, AgentLoopError, Tool
from .models import SkillRequest, SkillSpec
from .provider import LLMProvider, ProviderError
from .sanitize import clean

_PROMPTS_DIR = Path(__file__).parent / "prompts"
LOGGER = get_logger("skill_agent.clarifier")

_SUBMIT_SPEC_PARAMETERS = {
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
        "runtime": {"type": "string", "enum": ["python", "node", "shell", "other"]},
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
        "name", "description", "purpose", "inputs", "outputs",
        "workflow_steps", "required_files", "runtime",
    ],
}


class SkillAgentError(Exception):
    pass


def _load_prompt(name: str) -> str:
    return clean((_PROMPTS_DIR / name).read_text(encoding="utf-8"))


def _stdin_ask(question: str) -> str:
    print(f"\n  Clarifier: {question}")
    return clean(input("  You: ").strip())


class Clarifier:
    def __init__(
        self,
        provider: LLMProvider,
        ask_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.provider = provider
        self._ask_fn = ask_fn or _stdin_ask
        self._system_prompt = _load_prompt("clarifier_system.md")

    def clarify(self, request: SkillRequest) -> SkillSpec:
        collected: dict = {}

        def _ask_user(question: str) -> str:
            return self._ask_fn(question)

        def _submit_spec(**kwargs) -> str:
            collected.update(kwargs)
            return "OK"

        tools = [
            Tool(
                name="ask_user",
                description="Ask the user one focused clarifying question when the request is ambiguous.",
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                    "required": ["question"],
                },
                fn=_ask_user,
            ),
            Tool(
                name="submit_spec",
                description="Submit the completed SkillSpec once you have all required information.",
                parameters=_SUBMIT_SPEC_PARAMETERS,
                fn=_submit_spec,
            ),
        ]

        loop = AgentLoop(self.provider, tools=tools, stop_on="submit_spec")
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": "Clarify this skill request:\n\n" + request.model_dump_json(indent=2),
            },
        ]

        try:
            loop.run(messages)
        except ProviderError as exc:
            LOGGER.error("Clarifier provider call failed: %s", exc)
            raise SkillAgentError(f"Clarifier API error: {exc}") from exc
        except AgentLoopError as exc:
            LOGGER.error("Clarifier agent loop failed: %s", exc)
            raise SkillAgentError(str(exc)) from exc

        if not collected:
            LOGGER.error("Clarifier exited without submit_spec.")
            raise SkillAgentError("Clarifier never called submit_spec — no spec was produced.")

        try:
            return SkillSpec.model_validate(collected)
        except Exception as exc:
            LOGGER.error("Clarifier returned invalid schema payload: %s", exc)
            raise SkillAgentError(
                f"Clarifier response failed schema validation: {exc}\nCollected: {collected}"
            ) from exc
