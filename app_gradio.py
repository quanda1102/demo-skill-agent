#!/usr/bin/env python3
"""
Gradio debug UI for skill-agent.

Run:
    uv run python app_gradio.py

Then open http://localhost:7860 in your browser.

Layout:
    Left  — chat (Chatbot + input + buttons)
    Right — trace/debug panel (all model/tool events from the current turn)

The agent is instantiated once at startup and reused across all turns.
If MINIMAX_API_KEY is not set, the UI opens but every send shows an error.
"""
from __future__ import annotations

import argparse
import os
import traceback as tb
from pathlib import Path

import gradio as gr

from src.skill_agent.agent import SkillChatAgent
from src.skill_agent.logging_utils import configure_logging
from src.skill_agent.provider import MinimaxProvider
from src.skill_agent.sandbox import DockerSandboxRunner, LocalSandboxRunner
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT_DIR = Path(__file__).parent
SKILLS_DIR = ROOT_DIR / "skills"
# Use a separate workspace so the Gradio session doesn't mix with the CLI demo.
WORKSPACE_DIR = ROOT_DIR / "vault" / "agent-gradio"
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY") or None
configure_logging()

EXAMPLE_PROMPTS = [
    ["Create a skill that extracts top 5 url from a url"],
    ["Covert https://vnexpress.net/ into markdown file "],
    ["Debug why the generated SKILL.md is failing validation and suggest a fix."],
]

APP_CSS = """
:root {
  --paper: #f6f0e4;
  --paper-deep: #eee5d3;
  --panel: rgba(255, 250, 242, 0.9);
  --panel-strong: #fffdf9;
  --ink: #241e16;
  --ink-soft: #645847;
  --line: rgba(88, 67, 36, 0.14);
  --shadow: 0 18px 48px rgba(66, 49, 22, 0.12);
  --ok: #1a6a46;
  --warn: #9a6a00;
  --error: #9a2b20;
}

body,
.gradio-container {
  background:
    radial-gradient(circle at top left, rgba(225, 166, 72, 0.22), transparent 30%),
    radial-gradient(circle at bottom right, rgba(161, 124, 71, 0.14), transparent 28%),
    linear-gradient(180deg, var(--paper) 0%, var(--paper-deep) 100%);
  color: var(--ink);
}

.gradio-container {
  max-width: 1320px !important;
  padding: 24px !important;
}

.hero-card,
.panel-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 22px;
  box-shadow: var(--shadow);
}

.panel-card {
  padding: 10px !important;
}

#chatbot,
#trace-box {
  border-radius: 18px;
  overflow: hidden;
}

#chatbot {
  min-height: 560px;
}

#message-box textarea {
  font-size: 1rem !important;
  line-height: 1.55 !important;
}

#trace-box textarea,
#trace-box pre,
#trace-box code {
  font-size: 13px !important;
  line-height: 1.55 !important;
}

.trace-toggle {
  margin-bottom: 10px;
}

.gradio-button {
  border-radius: 999px !important;
}

@media (max-width: 900px) {
  .gradio-container {
    padding: 14px !important;
  }

  .hero-card,
  .panel-card {
    border-radius: 18px;
  }

  #chatbot {
    min-height: 420px;
  }
}
"""


# ---------------------------------------------------------------------------
# Agent instantiation — done once at module load time.
#
# We always pass verbose=True so that _handle_event fires and forwards events
# to whatever event_sink is currently set. The verbose checkbox in the UI only
# controls whether the trace panel is shown, not whether events are collected.
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gradio debug UI for skill-agent.")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use DockerSandboxRunner for skill generation tests (requires skill-agent-sandbox:latest).",
    )
    # Ignore Gradio's own flags that appear when launched via `gradio app_gradio.py`.
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
# Turn handler
# ---------------------------------------------------------------------------

def send_message(
    user_message: str,
    history: list[dict],
    show_trace: bool,
    current_trace: str,
) -> tuple[list[dict], str, str, str]:
    """
    Process one turn and return (updated_history, cleared_input, trace_text, raw_trace).

    history uses the Gradio messages format:
        [{"role": "user" | "assistant", "content": str}, ...]
    """
    user_message = user_message.strip()
    if not user_message:
        return history, "", _format_trace(current_trace, show_trace), current_trace

    if isinstance(_AGENT, Exception):
        err = str(_AGENT)
        raw_trace = f"startup error\n{err}"
        return (
            history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": f"⚠️ Agent unavailable:\n\n{err}"},
            ],
            "",
            _format_trace(raw_trace, show_trace),
            raw_trace,
        )

    # Collect all model/tool events emitted during this turn.
    events: list[str] = []
    _AGENT.event_sink = events.append
    try:
        reply = _AGENT.run_turn(user_message)
    except Exception as exc:
        reply = f"Error: {exc}"
        events.append("--- traceback ---")
        events.append(tb.format_exc())
    finally:
        # Always clear the sink so stray events from a later background call
        # (if any) don't accumulate in a stale list.
        _AGENT.event_sink = None

    raw_trace = "\n".join(events)
    updated = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": reply},
    ]
    return updated, "", _format_trace(raw_trace, show_trace), raw_trace


def clear_session(show_trace: bool) -> tuple[list, str, str, str]:
    """Reset the chat history, input box, trace panel, and agent memory."""
    if not isinstance(_AGENT, Exception):
        _AGENT.state.messages.clear()
    return [], "", _format_trace("", show_trace), ""


def toggle_trace(show_trace: bool, raw_trace: str) -> str:
    return _format_trace(raw_trace, show_trace)


def _format_trace(raw_trace: str, show_trace: bool) -> str:
    if not show_trace:
        return "# Trace hidden\nEnable the inspector when you need raw model and tool events for the latest turn."

    raw_trace = raw_trace.strip()
    if not raw_trace:
        return "# Waiting for a turn\nSend a message to inspect model and tool events for the latest turn."
    return raw_trace


# ---------------------------------------------------------------------------
# Gradio layout
# ---------------------------------------------------------------------------

with gr.Blocks(
    title="Skill Agent Console",
    fill_width=True,
) as demo:
    raw_trace_state = gr.State("")

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
This panel shows raw model and tool events for the latest turn. Hide it when you want a calmer chat view.
""",
                    container=False,
                )
                show_trace_cb = gr.Checkbox(
                    label="Show trace inspector",
                    info="Applies immediately and only affects the panel on the right.",
                    value=True,
                    elem_classes="trace-toggle",
                )
                trace_box = gr.Code(
                    value=_format_trace("", True),
                    language="shell",
                    label="Latest turn trace",
                    lines=30,
                    interactive=False,
                    show_line_numbers=False,
                    wrap_lines=True,
                    buttons=["copy"],
                    elem_id="trace-box",
                )
                with gr.Accordion("Inspector notes", open=False):
                    gr.Markdown(
                        """
- Only the latest turn is shown here.
- `Clear session` also clears the agent's conversation memory.
- Startup warnings stay in the header so the chat area remains uncluttered.
""",
                        container=False,
                    )

    _send_inputs = [user_input, chatbot, show_trace_cb, raw_trace_state]
    _send_outputs = [chatbot, user_input, trace_box, raw_trace_state]

    send_btn.click(fn=send_message, inputs=_send_inputs, outputs=_send_outputs)
    # Submit on Enter so the user doesn't have to click.
    user_input.submit(fn=send_message, inputs=_send_inputs, outputs=_send_outputs)

    clear_btn.click(fn=clear_session, inputs=show_trace_cb, outputs=[chatbot, user_input, trace_box, raw_trace_state])
    show_trace_cb.change(fn=toggle_trace, inputs=[show_trace_cb, raw_trace_state], outputs=trace_box)

demo.theme = gr.themes.Soft()
demo.css = APP_CSS


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )
