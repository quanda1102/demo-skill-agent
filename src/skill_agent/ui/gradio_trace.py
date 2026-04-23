from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gradio import ChatMessage


def render_trace_markdown(events: list[dict[str, Any]], *, show_trace: bool) -> str:
    if not show_trace:
        return "*Trace hidden.* Enable the inspector to see model and tool events."
    if not events:
        return "*Waiting for a turn.* Send a message to inspect model and tool events."
    return _render_events(events)


def render_chat_trace(events: list[dict[str, Any]]) -> str:
    relevant_events = [event for event in events if event.get("source") in {"generator", "pipeline"}]
    if not relevant_events:
        return ""
    return _render_events(relevant_events)


@dataclass
class ChatTurnState:
    messages: list[ChatMessage]
    assistant_stream_idx: int | None = None
    pending_tool_idx: int | None = None
    build_trace_idx: int | None = None


@dataclass
class TraceTurnState:
    events: list[dict[str, Any]] = field(default_factory=list)
    stream_idx: int | None = None
    stream_source: str | None = None


def apply_trace_event(state: TraceTurnState, entry: dict[str, Any]) -> None:
    kind = entry.get("kind")
    source = entry.get("source", "pipeline")

    if kind == "model_delta":
        chunk = entry.get("content", "")
        if not chunk:
            return
        if state.stream_idx is not None and state.stream_source == source:
            state.events[state.stream_idx]["content"] += chunk
            state.events[state.stream_idx]["msg"] = state.events[state.stream_idx]["content"][:240]
            return
        state.events.append({"source": source, "kind": "model_streaming", "content": chunk, "msg": chunk})
        state.stream_idx = len(state.events) - 1
        state.stream_source = source
        return

    if kind == "model" and state.stream_idx is not None and state.stream_source == source:
        state.events[state.stream_idx] = entry
        state.stream_idx = None
        state.stream_source = None
        return

    state.stream_idx = None
    state.stream_source = None
    state.events.append(entry)


def apply_agent_chat_event(state: ChatTurnState, entry: dict[str, Any]) -> None:
    if entry.get("source") != "agent":
        return

    kind = entry.get("kind")
    if kind == "model_delta":
        chunk = entry.get("content", "")
        if not chunk:
            return
        if state.assistant_stream_idx is not None:
            existing_message = state.messages[state.assistant_stream_idx]
            state.messages[state.assistant_stream_idx] = ChatMessage(
                role="assistant",
                content=existing_message.content + chunk,
            )
            return
        state.messages.append(ChatMessage(role="assistant", content=chunk))
        state.assistant_stream_idx = len(state.messages) - 1
        return

    if kind == "model":
        if entry.get("action") == "tool_calls":
            state.assistant_stream_idx = None
        return

    if kind == "tool_start":
        tool_name = entry.get("name", "")
        state.assistant_stream_idx = None
        state.messages.append(
            ChatMessage(
                role="assistant",
                content="",
                metadata={"title": f"▸  {tool_name}", "status": "pending"},
            )
        )
        state.pending_tool_idx = len(state.messages) - 1
        return

    if kind == "tool":
        tool_name = entry.get("name", "")
        tool_output = entry.get("output", "")
        if state.pending_tool_idx is None:
            return
        state.messages[state.pending_tool_idx] = ChatMessage(
            role="assistant",
            content=f"```\n{tool_output}\n```" if tool_output.strip() else "",
            metadata={"title": f"  {tool_name}"},
        )
        state.pending_tool_idx = None
        return

    if kind == "tool_error":
        tool_name = entry.get("name", "")
        error_message = entry.get("error", "")
        error_type = entry.get("error_type", "error")
        if state.pending_tool_idx is None:
            return
        state.messages[state.pending_tool_idx] = ChatMessage(
            role="assistant",
            content=f"`{error_type}`: {error_message}",
            metadata={"title": f"  {tool_name}  —  failed"},
        )
        state.pending_tool_idx = None


def should_update_chat_trace(entry: dict[str, Any]) -> bool:
    return entry.get("source") in {"generator", "pipeline"}


def upsert_build_trace_message(
    state: ChatTurnState,
    *,
    events: list[dict[str, Any]],
    pending: bool,
) -> None:
    trace_content = render_chat_trace(events)
    if not trace_content:
        return
    metadata = {"title": "▸  build trace", "status": "pending"} if pending else {"title": "  build trace"}
    trace_message = ChatMessage(role="assistant", content=trace_content, metadata=metadata)
    if state.build_trace_idx is not None:
        state.messages[state.build_trace_idx] = trace_message
        return
    state.messages.append(trace_message)
    state.build_trace_idx = len(state.messages) - 1


def finalize_chat_turn(
    state: ChatTurnState,
    *,
    events: list[dict[str, Any]],
    reply: str,
) -> None:
    if state.pending_tool_idx is not None:
        orphan_tool_message = state.messages[state.pending_tool_idx]
        stale_title = orphan_tool_message.metadata.get("title", "tool").replace("▸  ", "").strip()
        state.messages[state.pending_tool_idx] = ChatMessage(
            role="assistant",
            content="",
            metadata={"title": f"  {stale_title}", "status": "done"},
        )

    if state.build_trace_idx is not None:
        upsert_build_trace_message(state, events=events, pending=False)

    if state.assistant_stream_idx is not None:
        state.messages[state.assistant_stream_idx] = ChatMessage(role="assistant", content=reply)
        return

    state.messages.append(ChatMessage(role="assistant", content=reply))


def _render_events(events: list[dict[str, Any]]) -> str:
    rendered_parts: list[str] = []
    for event in events:
        rendered_event = _render_event(event)
        if rendered_event:
            rendered_parts.append(rendered_event)
    return "\n".join(rendered_parts)


def _render_event(event: dict[str, Any]) -> str:
    kind = event.get("kind", "info")
    message = event.get("msg", "")
    source = event.get("source", "pipeline")

    if kind == "stage":
        stage_num = event.get("stage_num", "?")
        stage = event.get("stage", "").title()
        attempt = event.get("attempt")
        max_attempts = event.get("max")
        header = f"\n---\n**Stage {stage_num}/5 — {stage}**"
        if attempt and max_attempts:
            header += f" *(attempt {attempt}/{max_attempts})*"
        return header

    if kind == "check":
        check_name = event.get("name", "")
        status = event.get("status", "")
        icon = "✓" if status == "pass" else "✗"
        return f"  {icon} {check_name}"

    if kind == "error":
        return f"  **⚠** {message}"

    if kind == "feedback":
        feedback_source = event.get("feedback_source", "")
        errors = event.get("errors", [])
        return f"  ↻ Sending {feedback_source} feedback ({len(errors)} error(s))"

    if kind == "files":
        files = event.get("files", [])
        return f"  files: `{'`, `'.join(files)}`"

    if kind == "info":
        return f"  {message}" if message else ""

    if kind == "sandbox_case":
        case_index = event.get("case_index", "?")
        total_cases = event.get("total_cases", "?")
        description = event.get("description", "")
        rationale = event.get("rationale", "")
        expectation = event.get("expectation", "")
        input_preview = event.get("input_preview", "")
        fixture_paths = event.get("fixture_paths", [])
        lines = [f"**[sandbox case {case_index}/{total_cases}]** {description}"]
        if rationale:
            lines.append(f"  why: {rationale}")
        if expectation:
            lines.append(f"  expect: {expectation}")
        if input_preview:
            lines.append(f"  input: `{input_preview}`")
        if fixture_paths:
            lines.append(f"  fixtures: `{'`, `'.join(fixture_paths)}`")
        return "\n".join(lines)

    if kind == "tool_start":
        tool_name = event.get("name", "")
        label = "generator" if source == "generator" else "tool"
        return f"**[{label}]** starting `{tool_name}`"

    if kind == "model_streaming":
        content = event.get("content", "").replace("\n", " ")
        label = "generator ▸" if source == "generator" else "model ▸"
        return f"**[{label}]** {content[:300]}…"

    if kind == "model":
        action = event.get("action", "")
        label = "generator" if source == "generator" else "model"
        if action == "tool_calls":
            tools = event.get("tools", [])
            return f"**[{label}]** calling `{'`, `'.join(tools)}`"
        content = event.get("content", "").replace("\n", " ")
        return f"**[{label}]** → {content[:200]}"

    if kind == "tool":
        tool_name = event.get("name", "")
        output = event.get("output", "").replace("\n", " ")
        label = "generator tool" if source == "generator" else "tool"
        return f"**[{label}]** `{tool_name}` → {output[:180]}"

    if kind == "tool_error":
        tool_name = event.get("name", "")
        error_type = event.get("error_type", "")
        error_message = event.get("error", "")
        label = "generator tool error" if source == "generator" else "tool error"
        return f"**[{label}]** `{tool_name}` [{error_type}] → {error_message[:180]}"

    return message if message else ""
