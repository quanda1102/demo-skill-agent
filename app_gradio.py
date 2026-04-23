#!/usr/bin/env python3
"""
Gradio debug UI for skill-agent.

Run:
    uv run python app_gradio.py

Then open http://localhost:7860 in your browser.

Layout:
    Left  — chat (Chatbot + input + buttons)
    Right — trace/debug panel (all model/tool events from the current turn, streamed live)

The agent is instantiated once at startup and reused across all turns.
If MINIMAX_API_KEY is not set, the UI opens but every send shows an error.
"""
from __future__ import annotations

import argparse
import os
import queue
import threading
import traceback as tb
from pathlib import Path
from typing import Any, Generator

import gradio as gr
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
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT_DIR = Path(__file__).parent
SKILLS_DIR = ROOT_DIR / "skills"
WORKSPACE_DIR = ROOT_DIR / "vault" / "agent-gradio"
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY") or None
configure_logging()


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
    return parser.parse_known_args()[0]


_ARGS = _parse_args()


def _build_agent(use_docker: bool) -> SkillChatAgent | Exception:
    sandbox_runner = DockerSandboxRunner() if use_docker else LocalSandboxRunner()
    try:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return SkillChatAgent(
            provider=MinimaxProvider(api_key=MINIMAX_API_KEY, temperature=0.2, top_p=0.9, max_tokens=1600),
            generator_provider=MinimaxProvider(api_key=MINIMAX_API_KEY),
            skills_dir=SKILLS_DIR,
            workspace_dir=WORKSPACE_DIR,
            verbose=True,
            event_sink=None,
            sandbox_runner=sandbox_runner,
        )
    except Exception as exc:
        return exc


_AGENT: SkillChatAgent | Exception = _build_agent(_ARGS.docker)


# ---------------------------------------------------------------------------
# Turn handler — generator function for live streaming
# ---------------------------------------------------------------------------

def send_message(
    user_message: str,
    history: list,
    show_trace: bool,
    current_events: list[dict[str, Any]],
) -> Generator[tuple[list, str, str, list[dict[str, Any]]], None, None]:
    """
    Process one turn and stream updates live.

    Chatbot uses ChatMessage objects so tool calls appear as collapsible blocks
    inside the conversation.  Each tool starts as "pending" (spinner) and
    transitions to "done" when the result arrives.
    """
    user_message = user_message.strip()
    if not user_message:
        yield history, "", render_trace_markdown(current_events, show_trace=show_trace), current_events
        return

    if isinstance(_AGENT, Exception):
        err = str(_AGENT)
        ev: list[dict[str, Any]] = [{"source": "agent", "kind": "error", "msg": f"startup error: {err}"}]
        yield (
            list(history) + [
                ChatMessage(role="user", content=user_message),
                ChatMessage(role="assistant", content=f"Agent unavailable:\n\n{err}"),
            ],
            "",
            render_trace_markdown(ev, show_trace=show_trace),
            ev,
        )
        return

    trace_state = TraceTurnState()
    reply_holder: list[str] = []
    exc_holder: list[str] = []
    q: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()

    def _event_sink(entry: dict[str, Any]) -> None:
        q.put(entry)

    def _run() -> None:
        try:
            _AGENT.event_sink = _event_sink
            reply_holder.append(_AGENT.run_turn(user_message))
        except Exception as exc:
            reply_holder.append(f"Error: {exc}")
            exc_holder.append(tb.format_exc())
        finally:
            _AGENT.event_sink = None
            q.put(None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # --- Chatbot state (mutable list of ChatMessage) ---
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
            "",
            render_trace_markdown(trace_state.events, show_trace=show_trace),
            trace_state.events,
        )

    if exc_holder:
        apply_trace_event(trace_state, {"source": "agent", "kind": "error", "msg": exc_holder[0]})

    thread.join()
    reply = reply_holder[0] if reply_holder else "Error: no reply received"
    finalize_chat_turn(chat_state, events=trace_state.events, reply=reply)

    yield (
        chat_state.messages,
        "",
        render_trace_markdown(trace_state.events, show_trace=show_trace),
        trace_state.events,
    )


def clear_session(show_trace: bool) -> tuple[list, str, str, list]:
    if not isinstance(_AGENT, Exception):
        _AGENT.reset_session()
    return [], "", render_trace_markdown([], show_trace=show_trace), []


def toggle_trace(show_trace: bool, events: list[dict[str, Any]]) -> str:
    return render_trace_markdown(events, show_trace=show_trace)


# ---------------------------------------------------------------------------
# Gradio layout
# ---------------------------------------------------------------------------

with gr.Blocks(
    title="Skill Agent Console",
    fill_width=True,
) as demo:
    raw_trace_state = gr.State([])  # list[dict] — structured events for current turn

    with gr.Row(equal_height=True):
        # Left column: chat
        with gr.Column(scale=5, min_width=520):
            with gr.Group(elem_classes="panel-card"):
                gr.Markdown(
                    """
### Conversation
Use the chat like a normal assistant. The agent keeps its session state until you clear it.
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
                    placeholder="Ask for a new skill, inspect a failure, or refine a generated result. Press Enter to send.",
                    info="Clear resets both the visible chat and the agent's in-memory conversation.",
                    lines=4,
                    max_lines=10,
                    elem_id="message-box",
                )
                with gr.Row(variant="compact"):
                    send_btn = gr.Button("Send", variant="primary")
                    clear_btn = gr.Button("Clear session", variant="secondary")
                gr.Examples(
                    examples=EXAMPLE_PROMPTS,
                    inputs=user_input,
                    label="Prompt ideas",
                    examples_per_page=3,
                    run_on_click=False,
                )

        # Right column: trace / debug
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
- Events stream live as the agent runs — no waiting for the full turn.
- Pipeline stages (generate / validate / sandbox / publish) appear inline.
- Only the latest turn is shown. `Clear session` also resets agent memory.
""",
                        container=False,
                    )

    _send_inputs = [user_input, chatbot, show_trace_cb, raw_trace_state]
    _send_outputs = [chatbot, user_input, trace_box, raw_trace_state]

    send_btn.click(fn=send_message, inputs=_send_inputs, outputs=_send_outputs)
    user_input.submit(fn=send_message, inputs=_send_inputs, outputs=_send_outputs)

    clear_btn.click(fn=clear_session, inputs=show_trace_cb, outputs=[chatbot, user_input, trace_box, raw_trace_state])
    show_trace_cb.change(fn=toggle_trace, inputs=[show_trace_cb, raw_trace_state], outputs=trace_box)

demo.theme = gr.themes.Soft()
demo.css = APP_CSS
demo.queue()  # required for generator-based streaming; must be set at module level


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )
