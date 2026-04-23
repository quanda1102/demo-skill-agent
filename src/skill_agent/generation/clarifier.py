from __future__ import annotations

from typing import Callable

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.agent.loop import AgentLoop, AgentLoopError, Tool
from src.skill_agent.schemas.skill_model import SkillRequest, SkillSpec
from src.skill_agent.prompt_loader import load_prompt
from src.skill_agent.providers.provider import LLMProvider, ProviderError
from src.skill_agent.sanitize import clean
from src.skill_agent.schemas.skill_spec_schema import CLARIFIER_SUBMIT_SPEC_PARAMETERS

LOGGER = get_logger("skill_agent.clarifier")


class SkillAgentError(Exception):
    pass

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
        self._system_prompt = load_prompt("clarifier_system.md")

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
                parameters=CLARIFIER_SUBMIT_SPEC_PARAMETERS,
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
