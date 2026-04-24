#!/usr/bin/env python3
"""
Gradio debug UI for skill-agent.

Run:
    uv run python app_gradio.py

Then open http://localhost:7860 in your browser.

Layout:
    Chat tab   — chat, review actions, and live trace panel
    Config tab — validation policy controls for future skill builds

The agent is instantiated once at startup and reused across all turns.
Human review cards appear as inline action buttons when a skill build requires approval.
"""
from __future__ import annotations

import argparse
import functools
import os
import queue
import re
import threading
import traceback as tb
from pathlib import Path
from typing import Any, Generator

import gradio as gr
from dotenv import load_dotenv
from gradio import ChatMessage

from src.skill_agent.agent.agent import SkillChatAgent
from src.skill_agent.observability.logging_utils import configure_logging
from src.skill_agent.providers.provider import MinimaxProvider
from src.skill_agent.sandbox import DockerSandboxRunner, LocalSandboxRunner
from src.skill_agent.ui.gradio_assets import APP_CSS, EXAMPLE_PROMPTS
from src.skill_agent.ui.gradio_trace import (
    ChatTurnState,
    TraceTurnState,
    apply_agent_chat_event,
    apply_trace_event,
    finalize_chat_turn,
    render_trace_markdown,
    should_update_chat_trace,
    upsert_build_trace_message,
)
from src.skill_agent.validation.policy import ValidationPolicy, ValidationPolicyLoader
from src.skill_agent.workflow import (
    HumanDecisionEvent,
    InteractionGateway,
    WorkflowEvent,
    WorkflowState,
)

load_dotenv(override=True)

ROOT_DIR = Path(__file__).parent
SKILLS_DIR = ROOT_DIR / "skills"
WORKSPACE_DIR = ROOT_DIR / "vault" / "agent-gradio"
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY") or None
configure_logging()

_BUNDLED_POLICY_PATH = ROOT_DIR / "policies" / "mvp-safe.yaml"

# ---------------------------------------------------------------------------
# Interaction gateway — pure adapter, instantiated once at startup.
# ---------------------------------------------------------------------------

_GATEWAY = InteractionGateway()


# ---------------------------------------------------------------------------
# Agent instantiation — done once at module load time.
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gradio debug UI for skill-agent.")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use DockerSandboxRunner for skill generation tests (requires skill-agent-sandbox:latest).",
    )
    parser.add_argument(
        "--no-review",
        action="store_true",
        help="Disable human review gate — skills publish automatically after passing all checks.",
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=None,
        help="Path to validation policy YAML file. If not provided, use the active default.",
    )
    return parser.parse_known_args()[0]


_ARGS = _parse_args()


def _policy_source_label(path: str | None) -> str:
    if path:
        return str(Path(path))
    env_path = os.environ.get("SKILL_VALIDATION_POLICY")
    if env_path:
        return f"{env_path} (from SKILL_VALIDATION_POLICY)"
    return "bundled mvp-safe.yaml"


def _load_policy(path: str | None) -> tuple[ValidationPolicy, str, str | None]:
    normalized = path.strip() if path and path.strip() else None
    source_label = _policy_source_label(normalized)

    try:
        if normalized is not None:
            return ValidationPolicyLoader.load(normalized), source_label, None
        return ValidationPolicyLoader.default(), source_label, None
    except Exception as exc:
        fallback = ValidationPolicyLoader.load(_BUNDLED_POLICY_PATH)
        warning = (
            f"Failed to load policy from `{source_label}`: {exc}. "
            "Falling back to bundled mvp-safe.yaml."
        )
        return fallback, "bundled mvp-safe.yaml", warning


_ACTIVE_POLICY, _ACTIVE_POLICY_SOURCE, _POLICY_STATUS = _load_policy(_ARGS.policy)


def _build_agent(
    use_docker: bool,
    require_review: bool,
    policy: ValidationPolicy,
) -> SkillChatAgent | Exception:
    sandbox_runner = DockerSandboxRunner() if use_docker else LocalSandboxRunner()
    try:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return SkillChatAgent(
            provider=MinimaxProvider(
                api_key=MINIMAX_API_KEY,
                temperature=0.2,
                top_p=0.9,
                max_tokens=1600,
            ),
            generator_provider=MinimaxProvider(api_key=MINIMAX_API_KEY),
            skills_dir=SKILLS_DIR,
            workspace_dir=WORKSPACE_DIR,
            verbose=True,
            event_sink=None,
            sandbox_runner=sandbox_runner,
            require_human_review=require_review,
            validation_policy=policy,
        )
    except Exception as exc:
        return exc


_AGENT: SkillChatAgent | Exception = _build_agent(
    use_docker=_ARGS.docker,
    require_review=not _ARGS.no_review,
    policy=_ACTIVE_POLICY,
)


def _set_active_policy(policy: ValidationPolicy, source: str, status: str | None = None) -> None:
    global _ACTIVE_POLICY, _ACTIVE_POLICY_SOURCE, _POLICY_STATUS
    _ACTIVE_POLICY = policy
    _ACTIVE_POLICY_SOURCE = source
    _POLICY_STATUS = status
    if not isinstance(_AGENT, Exception):
        _AGENT.validation_policy = policy


def _current_policy() -> ValidationPolicy:
    if not isinstance(_AGENT, Exception) and _AGENT.validation_policy is not None:
        return _AGENT.validation_policy
    return _ACTIVE_POLICY


def _policy_code_safety_view(policy: ValidationPolicy | None = None) -> dict[str, Any]:
    active = policy or _current_policy()
    return {
        name: {
            "severity": rule.severity,
            "patterns": list(rule.patterns),
        }
        for name, rule in active.code_safety.risky_patterns.items()
    }


def _join_lines(values: list[str]) -> str:
    return "\n".join(values)


def _policy_form_values(policy: ValidationPolicy | None = None) -> tuple[Any, ...]:
    active = policy or _current_policy()
    return (
        active.activation.min_description_chars,
        active.activation.max_description_chars,
        _join_lines(active.activation.forbidden_placeholder_patterns),
        active.activation.require_action_verb,
        _join_lines(active.dependencies.allowed_imports),
        _join_lines(active.dependencies.forbidden_files),
        _join_lines(active.capability.operation_taxonomy),
        _join_lines(active.capability.allowed_side_effects),
        _policy_code_safety_view(active),
        _render_active_policy_markdown(active),
        _render_policy_status_markdown(),
    )


def _render_active_policy_markdown(policy: ValidationPolicy | None = None) -> str:
    active = policy or _current_policy()
    side_effects = ", ".join(active.capability.allowed_side_effects) or "none"
    return (
        "### Active Policy\n"
        f"Source: `{_ACTIVE_POLICY_SOURCE}`\n\n"
        f"- Description length: `{active.activation.min_description_chars}` to "
        f"`{active.activation.max_description_chars}` chars\n"
        f"- Action verb required: `{active.activation.require_action_verb}`\n"
        f"- Allowed imports: `{len(active.dependencies.allowed_imports)}`\n"
        f"- Forbidden files: `{len(active.dependencies.forbidden_files)}`\n"
        f"- Operation taxonomy verbs: `{len(active.capability.operation_taxonomy)}`\n"
        f"- Allowed side effects: `{side_effects}`\n"
        f"- Code safety rules: `{len(active.code_safety.risky_patterns)}`\n\n"
        "Changes here affect future skill builds started from this UI."
    )


def _render_policy_status_markdown(message: str | None = None) -> str:
    status = message if message is not None else _POLICY_STATUS
    if not status:
        status = "Ready."
    if isinstance(_AGENT, Exception):
        status += " Agent startup failed, so policy updates will not take effect until restart."
    return f"### Policy Status\n{status}"


def _split_multiline_list(value: str) -> list[str]:
    parts = re.split(r"[\n,]", value or "")
    return [part.strip() for part in parts if part.strip()]


def _coerce_int(value: Any, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be an integer.")
    return int(value)


def load_policy_from_ui(path: str) -> tuple[Any, ...]:
    policy, source, warning = _load_policy(path)
    if warning and path and path.strip():
        return (
            *_policy_form_values(_current_policy())[:-1],
            _render_policy_status_markdown(warning),
        )
    status = warning or f"Loaded policy from `{source}`."
    _set_active_policy(policy, source, status)
    return _policy_form_values(policy)


def reset_policy_form() -> tuple[Any, ...]:
    return (
        *_policy_form_values(_current_policy())[:-1],
        _render_policy_status_markdown("Reset the form to the active policy."),
    )


def apply_policy_form(
    min_desc: Any,
    max_desc: Any,
    forbid_patterns: str,
    require_verb: bool,
    allowed_imports: str,
    forbidden_files: str,
    taxonomy: str,
    side_effects: str,
) -> tuple[dict[str, Any], str, str]:
    active = _current_policy()
    try:
        min_desc_int = _coerce_int(min_desc, "min_description_chars")
        max_desc_int = _coerce_int(max_desc, "max_description_chars")
        if min_desc_int < 0:
            raise ValueError("min_description_chars must be >= 0.")
        if max_desc_int < min_desc_int:
            raise ValueError("max_description_chars must be >= min_description_chars.")

        updated = active.model_copy(
            update={
                "activation": active.activation.model_copy(
                    update={
                        "min_description_chars": min_desc_int,
                        "max_description_chars": max_desc_int,
                        "forbidden_placeholder_patterns": _split_multiline_list(forbid_patterns),
                        "require_action_verb": bool(require_verb),
                    }
                ),
                "dependencies": active.dependencies.model_copy(
                    update={
                        "allowed_imports": _split_multiline_list(allowed_imports),
                        "forbidden_files": _split_multiline_list(forbidden_files),
                    }
                ),
                "capability": active.capability.model_copy(
                    update={
                        "operation_taxonomy": _split_multiline_list(taxonomy),
                        "allowed_side_effects": _split_multiline_list(side_effects),
                    }
                ),
            }
        )
    except Exception as exc:
        return (
            _policy_code_safety_view(active),
            _render_active_policy_markdown(active),
            _render_policy_status_markdown(f"Could not apply UI policy changes: {exc}"),
        )

    previous_source = _ACTIVE_POLICY_SOURCE
    source = f"UI form overrides on {previous_source}"
    _set_active_policy(updated, source, "Applied UI policy overrides.")
    return (
        _policy_code_safety_view(updated),
        _render_active_policy_markdown(updated),
        _render_policy_status_markdown(
            "Applied UI policy overrides. Future skill builds will use the updated policy."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers shared by send_message and handle_action
# ---------------------------------------------------------------------------

def _apply_decision(
    decision: HumanDecisionEvent,
    user_label: str,
    history: list,
    show_trace: bool,
    current_events: list[dict[str, Any]],
) -> tuple:
    """
    Call confirm_pending_skill, render the result, and return a full output tuple.
    This is the WorkflowRuntime validation + gateway render step combined.
    """
    assert not isinstance(_AGENT, Exception)
    reply, wf_events = _AGENT.confirm_pending_skill(decision)

    display_reply = reply
    if wf_events:
        display_reply = _GATEWAY.render_event(wf_events[-1]).text

    updated_history = list(history) + [
        ChatMessage(role="user", content=f"[{user_label}]"),
        ChatMessage(role="assistant", content=display_reply),
    ]
    return (
        updated_history,
        gr.update(value="", interactive=True),
        render_trace_markdown(current_events, show_trace=show_trace),
        current_events,
        None,
        {},
        gr.update(visible=False),
        gr.update(visible=False, value=""),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


# ---------------------------------------------------------------------------
# Turn handler — generator function for live streaming
# ---------------------------------------------------------------------------

def send_message(
    user_message: str,
    history: list,
    show_trace: bool,
    current_events: list[dict[str, Any]],
    wf_state: WorkflowState | None,
    review_meta: dict,
) -> Generator[tuple, None, None]:
    """
    Process one turn and stream updates live.

    The gateway sits between the user message and the agent:
    1. parse_user_input() maps the message to a WorkflowInputEvent.
    2. If waiting for human review, a decision event bypasses the agent loop.
    3. Otherwise the normal agent loop runs, collecting WorkflowEvents.
    4. render_event() maps the last WorkflowEvent to the final UIMessage.
    """
    user_message = user_message.strip()

    if not user_message:
        yield (
            history,
            gr.update(value="", interactive=not (wf_state.waiting_for_human if wf_state else False)),
            render_trace_markdown(current_events, show_trace=show_trace),
            current_events,
            wf_state,
            review_meta,
            gr.update(),
            gr.update(),
            gr.update(interactive=not (wf_state.waiting_for_human if wf_state else False)),
            gr.update(interactive=not (wf_state.waiting_for_human if wf_state else False)),
        )
        return

    if isinstance(_AGENT, Exception):
        err = str(_AGENT)
        ev: list[dict[str, Any]] = [{"source": "agent", "kind": "error", "msg": f"startup error: {err}"}]
        yield (
            list(history)
            + [
                ChatMessage(role="user", content=user_message),
                ChatMessage(role="assistant", content=f"Agent unavailable:\n\n{err}"),
            ],
            gr.update(value="", interactive=True),
            render_trace_markdown(ev, show_trace=show_trace),
            ev,
            None,
            {},
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(interactive=True),
            gr.update(interactive=True),
        )
        return

    if wf_state is not None and wf_state.waiting_for_human:
        input_event = _GATEWAY.parse_user_input(user_message, wf_state, review_meta)

        if input_event.type == "human_decision":
            decision = HumanDecisionEvent(
                run_id=input_event.run_id,
                pending_action_id=input_event.payload["pending_action_id"],
                decision=input_event.payload["decision"],  # type: ignore[arg-type]
                notes=input_event.payload.get("notes", ""),
            )
            label_map = {
                "approved": "Approved",
                "rejected": "Rejected",
                "needs_changes": "Needs changes",
            }
            user_label = label_map.get(decision.decision, decision.decision)
            if decision.notes:
                user_label += f": {decision.notes}"
            yield _apply_decision(decision, user_label, history, show_trace, current_events)
            return

        if input_event.type == "clarification_needed":
            hint = input_event.payload.get("hint", "Please reply with approve, reject, or needs changes.")
            yield (
                list(history)
                + [
                    ChatMessage(role="user", content=user_message),
                    ChatMessage(role="assistant", content=hint),
                ],
                gr.update(value="", interactive=False),
                render_trace_markdown(current_events, show_trace=show_trace),
                current_events,
                wf_state,
                review_meta,
                gr.update(),
                gr.update(),
                gr.update(interactive=False),
                gr.update(interactive=False),
            )
            return

    trace_state = TraceTurnState()
    reply_holder: list[str] = []
    exc_holder: list[str] = []
    workflow_events: list[WorkflowEvent] = []
    q: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()

    def _event_sink(entry: dict[str, Any]) -> None:
        q.put(entry)

    def _workflow_event_sink(event: WorkflowEvent) -> None:
        workflow_events.append(event)

    def _run() -> None:
        try:
            _AGENT.event_sink = _event_sink
            _AGENT.workflow_event_sink = _workflow_event_sink
            reply_holder.append(_AGENT.run_turn(user_message))
        except Exception as exc:
            reply_holder.append(f"Error: {exc}")
            exc_holder.append(tb.format_exc())
        finally:
            _AGENT.event_sink = None
            _AGENT.workflow_event_sink = None
            q.put(None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    chat_state = ChatTurnState(messages=list(history) + [ChatMessage(role="user", content=user_message)])

    while True:
        entry = q.get()
        if entry is None:
            break

        apply_trace_event(trace_state, entry)
        apply_agent_chat_event(chat_state, entry)

        if should_update_chat_trace(entry):
            upsert_build_trace_message(chat_state, events=trace_state.events, pending=True)

        yield (
            chat_state.messages,
            gr.update(value="", interactive=not (wf_state.waiting_for_human if wf_state else False)),
            render_trace_markdown(trace_state.events, show_trace=show_trace),
            trace_state.events,
            wf_state,
            review_meta,
            gr.update(),
            gr.update(),
            gr.update(interactive=not (wf_state.waiting_for_human if wf_state else False)),
            gr.update(interactive=not (wf_state.waiting_for_human if wf_state else False)),
        )

    if exc_holder:
        apply_trace_event(trace_state, {"source": "agent", "kind": "error", "msg": exc_holder[0]})

    thread.join()
    raw_reply = reply_holder[0] if reply_holder else "Error: no reply received"

    new_wf_state: WorkflowState | None = None
    new_review_meta: dict = {}
    has_actions = False

    if workflow_events:
        last_event = workflow_events[-1]
        ui_msg = _GATEWAY.render_event(last_event)
        reply = ui_msg.text
        has_actions = bool(ui_msg.actions)

        if last_event.type == "human_review_requested":
            new_wf_state = WorkflowState(
                run_id=last_event.run_id,
                current="waiting_for_human",
                pending_action_id=last_event.payload.get("pending_action_id", ""),
            )
            new_review_meta = {
                "run_id": last_event.run_id,
                "pending_action_id": last_event.payload.get("pending_action_id", ""),
            }
    else:
        reply = raw_reply

    finalize_chat_turn(chat_state, events=trace_state.events, reply=reply)

    yield (
        chat_state.messages,
        gr.update(value="", interactive=not has_actions),
        render_trace_markdown(trace_state.events, show_trace=show_trace),
        trace_state.events,
        new_wf_state,
        new_review_meta,
        gr.update(visible=has_actions),
        gr.update(visible=has_actions),
        gr.update(interactive=not has_actions),
        gr.update(interactive=not has_actions),
    )


# ---------------------------------------------------------------------------
# Button action handler — not a generator; result is immediate
# ---------------------------------------------------------------------------

def handle_action(
    action_id: str,
    review_meta: dict,
    notes: str,
    history: list,
    show_trace: bool,
    current_events: list[dict[str, Any]],
) -> tuple:
    """
    Convert a button click into a HumanDecisionEvent via the gateway,
    then call confirm_pending_skill on the agent (WorkflowRuntime validation).
    """
    _empty = (
        history,
        gr.update(value="", interactive=not bool(review_meta.get("run_id"))),
        render_trace_markdown(current_events, show_trace=show_trace),
        current_events,
        None,
        {},
        gr.update(visible=False),
        gr.update(visible=False, value=""),
        gr.update(interactive=not bool(review_meta.get("run_id"))),
        gr.update(interactive=not bool(review_meta.get("run_id"))),
    )

    if isinstance(_AGENT, Exception) or not review_meta.get("run_id"):
        return _empty

    try:
        decision = _GATEWAY.parse_ui_action(action_id, review_meta, notes)
    except ValueError:
        return _empty

    label_map = {"approved": "Approved", "rejected": "Rejected", "needs_changes": "Needs changes"}
    user_label = label_map.get(decision.decision, decision.decision)
    if notes:
        user_label += f": {notes}"

    return _apply_decision(decision, user_label, history, show_trace, current_events)


def clear_session(
    show_trace: bool,
    history: list,
    current_events: list[dict[str, Any]],
    wf_state: WorkflowState | None,
    review_meta: dict,
) -> tuple:
    waiting_for_review = bool(wf_state and wf_state.waiting_for_human)

    if waiting_for_review:
        return (
            history,
            gr.update(value="", interactive=False),
            render_trace_markdown(current_events, show_trace=show_trace),
            current_events,
            wf_state,
            review_meta,
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )

    if not isinstance(_AGENT, Exception):
        _AGENT.reset_session()
        _AGENT._pending_review = None
    return (
        [],
        gr.update(value="", interactive=True),
        render_trace_markdown([], show_trace=show_trace),
        [],
        None,
        {},
        gr.update(visible=False),
        gr.update(visible=False, value=""),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def toggle_trace(show_trace: bool, events: list[dict[str, Any]]) -> str:
    return render_trace_markdown(events, show_trace=show_trace)


# ---------------------------------------------------------------------------
# Gradio layout
# ---------------------------------------------------------------------------

with gr.Blocks(title="Skill Agent Console", fill_width=True) as demo:
    raw_trace_state = gr.State([])
    workflow_state = gr.State(None)
    review_meta_state = gr.State({})

    with gr.Tabs():
        with gr.Tab("Chat"):
            with gr.Row(equal_height=True):
                with gr.Column(scale=5, min_width=520):
                    with gr.Group(elem_classes="panel-card"):
                        gr.Markdown(
                            """
### Conversation
Use the chat like a normal assistant. The agent keeps its session state until you clear it.
When a skill build requires review, action buttons appear below the chat.
""",
                            container=False,
                        )
                        chatbot = gr.Chatbot(
                            label="Conversation",
                            show_label=False,
                            elem_id="chatbot",
                            min_height=560,
                            layout="bubble",
                            placeholder="The conversation will appear here.",
                            buttons=["copy_all"],
                            feedback_options=None,
                            group_consecutive_messages=False,
                        )
                        user_input = gr.Textbox(
                            label="Ask the agent",
                            show_label=False,
                            placeholder=(
                                "Ask for a new skill, inspect a failure, or refine a generated result. "
                                "Press Enter to send."
                            ),
                            info="Clear resets both the visible chat and the agent's in-memory conversation.",
                            lines=4,
                            max_lines=10,
                            elem_id="message-box",
                        )
                        with gr.Row(variant="compact"):
                            send_btn = gr.Button("Send", variant="primary")
                            clear_btn = gr.Button("Clear session", variant="secondary")

                        with gr.Row(visible=False) as action_row:
                            approve_btn = gr.Button("Approve", variant="primary")
                            reject_btn = gr.Button("Reject", variant="stop")
                            needs_changes_btn = gr.Button("Needs changes", variant="secondary")
                        notes_input = gr.Textbox(
                            label="Notes (optional)",
                            placeholder="Add a note for the reviewer log…",
                            lines=2,
                            visible=False,
                        )

                        gr.Examples(
                            examples=EXAMPLE_PROMPTS,
                            inputs=user_input,
                            label="Prompt ideas",
                            examples_per_page=3,
                            run_on_click=False,
                        )

                with gr.Column(scale=3, min_width=360):
                    with gr.Group(elem_classes="panel-card"):
                        gr.Markdown(
                            """
### Turn Inspector
Live model and tool events for the current turn. Each event streams in as it happens.
""",
                            container=False,
                        )
                        show_trace_cb = gr.Checkbox(
                            label="Show trace inspector",
                            info="Applies immediately and only affects the panel on the right.",
                            value=True,
                            elem_classes="trace-toggle",
                        )
                        trace_box = gr.Markdown(
                            value=render_trace_markdown([], show_trace=True),
                            label="Latest turn trace",
                            elem_id="trace-box",
                        )
                        with gr.Accordion("Inspector notes", open=False):
                            gr.Markdown(
                                """
- Events stream live as the agent runs.
- Pipeline stages (generate / validate / sandbox / publish) appear inline.
- Only the latest turn is shown. `Clear session` also resets agent memory.
- When human review is required, Approve / Reject / Needs changes appear below the chat.
""",
                                container=False,
                            )

        with gr.Tab("Config"):
            with gr.Group(elem_classes="panel-card"):
                gr.Markdown(
                    """
### Validation Policy
Use this tab to inspect, load, and override the validation policy used for future skill builds.
""",
                    container=False,
                )
                gr.Markdown(
                    """
**Hướng dẫn nhanh**

- `Policy YAML Path`: nhập đường dẫn file YAML nếu muốn nạp policy từ đĩa, rồi bấm `Load Policy File`.
- Chỉnh các trường ngay bên dưới để đổi rule cho phiên hiện tại.
- Bấm `Apply UI Overrides` để áp dụng cấu hình mới cho các lần tạo skill tiếp theo.
- Bấm `Reset Form To Active` để đưa form về policy đang active.
- `code_safety.risky_patterns` hiện chỉ xem được trong UI. Muốn đổi regex hoặc severity thì sửa file YAML rồi nạp lại.
- Cấu hình mới chỉ áp dụng cho các lần build sau khi bấm `Apply UI Overrides`, không sửa lại lượt build hoặc review đang chạy.

Xem thêm: `docs/policy-ui.vi.md`
""",
                    container=False,
                )
                with gr.Accordion("Giải thích theo architecture", open=False):
                    gr.Markdown(
                        """
Trong kiến trúc hiện tại, tab `Config` đi theo nhánh generate/publish:

`Config Tab -> ValidationPolicy -> SkillChatAgent -> build_skill_from_spec -> StaticValidator -> SandboxRunner -> PublishGateway`

Điều đó có nghĩa là các field bên dưới chủ yếu ảnh hưởng bước `StaticValidator` trước khi skill được đưa sang sandbox và publish:

- `activation.*`: kiểm tra chất lượng mô tả skill
- `dependencies.*`: chặn dependency/file không mong muốn
- `capability.*`: chuẩn hóa metadata để runtime/policy hiểu skill làm gì
- `code_safety.risky_patterns`: quét regex trên code Python

Chúng không điều khiển trực tiếp runtime `PolicyEngine` trong `demo_runtime.py`, nhưng metadata được validate ở đây sẽ được runtime dùng lại khi chọn và kiểm tra skill.
""",
                        container=False,
                    )
                active_policy_md = gr.Markdown(_render_active_policy_markdown())
                policy_status_md = gr.Markdown(_render_policy_status_markdown())

                policy_path_input = gr.Textbox(
                    label="Policy YAML Path",
                    placeholder="Optional: load a policy file from disk, then edit it below.",
                    info="Nạp một file YAML policy vào ValidationPolicyLoader. Policy active ở đây sẽ được agent dùng cho các lần build skill tiếp theo.",
                    value=_ARGS.policy or "",
                )
                with gr.Row(variant="compact"):
                    load_policy_btn = gr.Button("Load Policy File", variant="secondary")
                    apply_policy_btn = gr.Button("Apply UI Overrides", variant="primary")
                    reset_policy_btn = gr.Button("Reset Form To Active", variant="secondary")

                with gr.Row():
                    min_desc = gr.Number(
                        label="min_description_chars",
                        precision=0,
                        info="Đi vào validate_skill_activation(). Nếu description ngắn hơn ngưỡng này thì validation fail trước sandbox.",
                    )
                    max_desc = gr.Number(
                        label="max_description_chars",
                        precision=0,
                        info="Cũng đi vào activation check. Hiện vượt ngưỡng này chỉ tạo warning, chưa chặn publish.",
                    )
                    require_verb = gr.Checkbox(
                        label="require_action_verb",
                        info="Bật heuristic kiểm tra description có động từ hành động hay không. Hiện thiếu verb chỉ tạo warning.",
                    )

                forbid_patterns = gr.Textbox(
                    label="forbidden_placeholder_patterns",
                    lines=4,
                    info="Regex chạy trên description ở activation check. Nếu match thì validation fail. Mỗi dòng một pattern; dấu phẩy cũng được.",
                )

                with gr.Row():
                    allowed_imports = gr.Textbox(
                        label="allowed_imports",
                        lines=5,
                        info="Allowlist cho third-party imports bị detector bắt được. Dùng khi muốn nới rule stdlib-only. Mỗi dòng một package; dấu phẩy cũng được.",
                    )
                    forbidden_files = gr.Textbox(
                        label="forbidden_files",
                        lines=5,
                        info="Tên file bị chặn trong generated skill package. Nếu file path xuất hiện trong output thì validation fail. Mỗi dòng một path; dấu phẩy cũng được.",
                    )

                with gr.Row():
                    taxonomy = gr.Textbox(
                        label="operation_taxonomy",
                        lines=8,
                        info="Danh sách verb chuẩn cho metadata.supported_actions / forbidden_actions. Ngoài taxonomy hiện chỉ warning, nhưng runtime sẽ dựa vào metadata này để hiểu skill làm gì.",
                    )
                    side_effects = gr.Textbox(
                        label="allowed_side_effects",
                        lines=8,
                        info="Danh sách side effect hợp lệ cho metadata.side_effects. Khai báo ngoài danh sách này sẽ fail validation. Runtime/review cũng dựa vào side effects để đánh giá rủi ro.",
                    )

                risky_output = gr.JSON(
                    label="code_safety.risky_patterns",
                    value=_policy_code_safety_view(),
                )

                gr.Markdown(
                    "*`code_safety.risky_patterns` được `validate_code_safety()` quét trên file `.py`. "
                    "Rule có severity `error` sẽ block publish; `warning` chỉ thêm cảnh báo. "
                    "Phần này hiện chỉ đọc trong UI, muốn sửa thì nạp YAML khác hoặc sửa file policy.*"
                )

    _shared_outputs = [
        chatbot,
        user_input,
        trace_box,
        raw_trace_state,
        workflow_state,
        review_meta_state,
        action_row,
        notes_input,
        send_btn,
        clear_btn,
    ]

    _send_inputs = [user_input, chatbot, show_trace_cb, raw_trace_state, workflow_state, review_meta_state]
    _action_inputs = [review_meta_state, notes_input, chatbot, show_trace_cb, raw_trace_state]

    send_btn.click(fn=send_message, inputs=_send_inputs, outputs=_shared_outputs)
    user_input.submit(fn=send_message, inputs=_send_inputs, outputs=_shared_outputs)

    clear_btn.click(
        fn=clear_session,
        inputs=[show_trace_cb, chatbot, raw_trace_state, workflow_state, review_meta_state],
        outputs=_shared_outputs,
    )
    show_trace_cb.change(fn=toggle_trace, inputs=[show_trace_cb, raw_trace_state], outputs=trace_box)

    for _action_id, _btn in [
        ("approve", approve_btn),
        ("reject", reject_btn),
        ("needs_changes", needs_changes_btn),
    ]:
        _btn.click(
            fn=functools.partial(handle_action, _action_id),
            inputs=_action_inputs,
            outputs=_shared_outputs,
        )

    _policy_form_outputs = [
        min_desc,
        max_desc,
        forbid_patterns,
        require_verb,
        allowed_imports,
        forbidden_files,
        taxonomy,
        side_effects,
        risky_output,
        active_policy_md,
        policy_status_md,
    ]

    load_policy_btn.click(
        fn=load_policy_from_ui,
        inputs=policy_path_input,
        outputs=_policy_form_outputs,
    )
    reset_policy_btn.click(
        fn=reset_policy_form,
        outputs=_policy_form_outputs,
    )
    apply_policy_btn.click(
        fn=apply_policy_form,
        inputs=[
            min_desc,
            max_desc,
            forbid_patterns,
            require_verb,
            allowed_imports,
            forbidden_files,
            taxonomy,
            side_effects,
        ],
        outputs=[risky_output, active_policy_md, policy_status_md],
    )

    demo.load(fn=reset_policy_form, outputs=_policy_form_outputs)

demo.theme = gr.themes.Soft()
demo.css = APP_CSS
demo.queue()


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )
