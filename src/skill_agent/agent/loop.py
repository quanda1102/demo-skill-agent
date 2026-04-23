from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.providers.provider import LLMProvider

_MAX_ITERATIONS = 30
LOGGER = get_logger("skill_agent.loop")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class AgentLoopError(Exception):
    pass


@dataclass
class AgentLoopEvent:
    type: str
    payload: dict[str, Any]


@dataclass
class AgentLoopResult:
    content: str
    history: list[dict[str, Any]]


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        tools: list[Tool] | None = None,
        stop_on: str | None = None,
        on_event: Callable[[AgentLoopEvent], None] | None = None,
    ) -> None:
        self.provider = provider
        self._tool_map = {t.name: t for t in (tools or [])}
        self._tool_schemas: list[dict] | None = [t.to_schema() for t in tools] if tools else None
        self._stop_on = stop_on
        self._on_event = on_event

    def run(self, messages: list) -> str:
        return self.run_turn(messages).content

    def _on_content_delta(self, content: str) -> None:
        self._emit("model_response_delta", content=content)

    def run_turn(self, messages: list) -> AgentLoopResult:
        history = list(messages)

        for iteration in range(1, _MAX_ITERATIONS + 1):
            on_delta = self._on_content_delta if self._on_event else None
            try:
                result = self.provider.invoke(history, tools=self._tool_schemas, on_delta=on_delta)
            except Exception:
                LOGGER.exception(
                    "Provider invocation failed at loop iteration %s/%s.",
                    iteration,
                    _MAX_ITERATIONS,
                )
                raise
            self._emit(
                "model_response",
                content=result.get("content"),
                tool_calls=result.get("tool_calls") or [],
            )

            history.append({
                "role": "assistant",
                "content": result.get("content"),
                "tool_calls": result.get("tool_calls"),
            })

            if not result.get("tool_calls"):
                return AgentLoopResult(
                    content=result.get("content") or "",
                    history=history,
                )

            stop_result: str | None = None
            for tc in result["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must decode to a JSON object")
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    args = {}
                    output = self._tool_error_output(
                        name=name,
                        error_type="invalid_tool_arguments",
                        detail=str(exc),
                    )
                    LOGGER.warning("Tool '%s' received invalid arguments: %s", name, exc)
                    self._emit(
                        "tool_error",
                        name=name,
                        arguments=args,
                        error=str(exc),
                        error_type="invalid_tool_arguments",
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": output,
                    })
                    continue

                tool = self._tool_map.get(name)
                if tool is None:
                    output = self._tool_error_output(
                        name=name,
                        error_type="unknown_tool",
                        detail=f"Unknown tool '{name}'",
                    )
                    LOGGER.warning("Model requested unknown tool '%s'.", name)
                    self._emit(
                        "tool_error",
                        name=name,
                        arguments=args,
                        error=f"Unknown tool '{name}'",
                        error_type="unknown_tool",
                    )
                else:
                    self._emit("tool_start", name=name, arguments=args)
                    try:
                        output = tool.fn(**args)
                    except Exception as exc:
                        LOGGER.exception("Tool '%s' failed with arguments=%s", name, args)
                        output = self._tool_error_output(
                            name=name,
                            error_type="tool_execution_failed",
                            detail=str(exc),
                        )
                        self._emit(
                            "tool_error",
                            name=name,
                            arguments=args,
                            error=str(exc),
                            error_type="tool_execution_failed",
                        )
                self._emit(
                    "tool_call",
                    name=name,
                    arguments=args,
                    output=str(output),
                )

                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(output),
                })

                if name == self._stop_on:
                    stop_result = str(output)

            if stop_result is not None:
                return AgentLoopResult(content=stop_result, history=history)

        raise AgentLoopError(f"Agent loop exceeded {_MAX_ITERATIONS} iterations without finishing")

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._on_event is not None:
            self._on_event(AgentLoopEvent(type=event_type, payload=payload))

    @staticmethod
    def _tool_error_output(name: str, error_type: str, detail: str) -> str:
        return json.dumps(
            {
                "status": "error",
                "tool": name,
                "error_type": error_type,
                "detail": detail,
            },
            ensure_ascii=False,
        )
