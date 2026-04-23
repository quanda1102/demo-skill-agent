from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.skill_agent.agent.loop import AgentLoopEvent
from src.skill_agent.sanitize import clean

TraceSource = Literal["agent", "generator", "pipeline"]


@dataclass(slots=True)
class TraceEvent:
    source: TraceSource
    kind: str
    msg: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"source": self.source, "kind": self.kind}
        if self.msg:
            payload["msg"] = self.msg
        payload.update(self.data)
        return payload


def build_trace_event(source: TraceSource, kind: str, msg: str = "", **data: Any) -> dict[str, Any]:
    return TraceEvent(source=source, kind=kind, msg=msg, data=data).to_dict()


def adapt_loop_event(event: AgentLoopEvent, source: TraceSource) -> dict[str, Any] | None:
    if event.type == "tool_start":
        name = event.payload.get("name", "")
        msg = f"generator calling {name}" if source == "generator" else f"calling {name}"
        return build_trace_event(source, "tool_start", msg=msg, name=name)

    if event.type == "model_response_delta":
        content = event.payload.get("content", "")
        return build_trace_event(source, "model_delta", msg=content, content=content)

    if event.type == "model_response":
        tool_calls = event.payload.get("tool_calls") or []
        if tool_calls:
            names = [call["function"]["name"] for call in tool_calls]
            msg = f"generator calling {', '.join(names)}" if source == "generator" else f"calling {', '.join(names)}"
            return build_trace_event(source, "model", msg=msg, action="tool_calls", tools=names)

        content = clean(str(event.payload.get("content") or ""))
        return build_trace_event(source, "model", msg=content[:240], action="reply", content=content)

    if event.type == "tool_call":
        name = event.payload.get("name", "")
        arguments = event.payload.get("arguments", {})
        output = clean(str(event.payload.get("output", "")))
        return build_trace_event(
            source,
            "tool",
            msg=f"{name} → {output[:120]}",
            name=name,
            args=arguments,
            output=output,
        )

    if event.type == "tool_error":
        name = event.payload.get("name", "")
        error_type = event.payload.get("error_type", "tool_error")
        error = clean(str(event.payload.get("error", "")))
        return build_trace_event(
            source,
            "tool_error",
            msg=f"{name} [{error_type}]: {error[:120]}",
            name=name,
            error_type=error_type,
            error=error,
        )

    return None
