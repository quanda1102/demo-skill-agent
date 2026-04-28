#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import gradio as gr
from dotenv import load_dotenv

from src.skill_agent.agents import WorkflowBuilderAgent, WorkflowDraftManager
from src.skill_agent.engine import ExecutionStore, SequentialExecutor, Workflow, WorkflowStore
from src.skill_agent.engine.models import ExecutionState
from src.skill_agent.engine.registry import get_registry_manifest
from src.skill_agent.engine.render import (
    notification_items,
    render_client_result,
    render_mermaid,
    render_node_outputs,
    render_notifications,
    render_workflow_summary,
)
from src.skill_agent.memory import MemoryManager
from src.skill_agent.observability.logging_utils import configure_logging

ROOT = Path(__file__).parent
MEMORY_DIR = ROOT / "data" / "memory"
WORKFLOW_DIR = ROOT / "data" / "workflows"

load_dotenv(override=True)
configure_logging()

builder = WorkflowBuilderAgent.from_env()
drafts = WorkflowDraftManager()
store = ExecutionStore()
workflow_store = WorkflowStore(WORKFLOW_DIR)
memory = MemoryManager.create(MEMORY_DIR)
memory.on_session_start()
CURRENT_WORKFLOW: Workflow | None = None
CURRENT_EXECUTION_ID: str | None = None
SEEN_NOTIFICATION_IDS: set[str] = set()


def _toast_new_notifications(execution: ExecutionState | None) -> None:
    for item in notification_items(execution):
        notification_id = str(item.get("notification_id") or item.get("alert_id") or "")
        if not notification_id or notification_id in SEEN_NOTIFICATION_IDS:
            continue
        SEEN_NOTIFICATION_IDS.add(notification_id)
        severity = str(item.get("severity", "info")).lower()
        owner = item.get("owner_type", "owner")
        message = item.get("message", "Notification sent")
        text = f"Notification sent to {owner}: {message}"
        if severity in {"critical", "error"}:
            gr.Warning(text)
        else:
            gr.Info(text)


def _chat(user_input: str, history: list[dict[str, Any]]):
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID
    draft_result = drafts.handle(user_input)
    if draft_result.draft is not None:
        CURRENT_WORKFLOW = draft_result.draft.workflow

    if draft_result.workflow is not None and draft_result.ready:
        execution = SequentialExecutor(draft_result.workflow, store=store).run()
        _toast_new_notifications(execution)
        CURRENT_WORKFLOW = draft_result.workflow
        CURRENT_EXECUTION_ID = execution.execution_id
        assistant_reply = _render_reply([draft_result.message], execution)
        memory.on_turn_end(user_input, assistant_reply)
        return (
            assistant_reply,
            render_mermaid(draft_result.workflow, execution),
            render_workflow_summary(draft_result.workflow, execution),
            render_client_result(draft_result.workflow, execution),
            render_notifications(execution),
            render_node_outputs(draft_result.workflow, execution),
        )

    if draft_result.changed or draft_result.draft is not None:
        assistant_reply = draft_result.message
        memory.on_turn_end(user_input, assistant_reply)
        return (
            assistant_reply,
            render_mermaid(CURRENT_WORKFLOW),
            render_workflow_summary(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
            render_client_result(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
            render_notifications(store.get(CURRENT_EXECUTION_ID)),
            render_node_outputs(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
        )

    build = builder.build(user_input)
    if build.workflow is None:
        assistant_reply = "\n".join(build.notes)
        memory.on_turn_end(user_input, assistant_reply)
        return (
            assistant_reply,
            render_mermaid(CURRENT_WORKFLOW),
            render_workflow_summary(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
            render_client_result(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
            render_notifications(store.get(CURRENT_EXECUTION_ID)),
            render_node_outputs(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
        )

    workflow = build.workflow
    execution = SequentialExecutor(workflow, store=store).run()
    _toast_new_notifications(execution)
    CURRENT_WORKFLOW = workflow
    CURRENT_EXECUTION_ID = execution.execution_id

    assistant_reply = _render_reply(build.notes, execution)
    memory.on_turn_end(user_input, assistant_reply)
    return (
        assistant_reply,
        render_mermaid(workflow, execution),
        render_workflow_summary(workflow, execution),
        render_client_result(workflow, execution),
        render_notifications(execution),
        render_node_outputs(workflow, execution),
    )


def _submit(user_input: str, history: list[dict[str, Any]] | None):
    history = list(history or [])
    assistant_reply, graph_html, workflow_summary, result_card, notification_center, node_outputs = _chat(user_input, history)
    history.extend(
        [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_reply},
        ]
    )
    return "", history, graph_html, workflow_summary, result_card, notification_center, node_outputs


def _refresh_graph():
    execution = store.get(CURRENT_EXECUTION_ID)
    return (
        render_mermaid(CURRENT_WORKFLOW, execution),
        render_workflow_summary(CURRENT_WORKFLOW, execution),
        render_client_result(CURRENT_WORKFLOW, execution),
        render_notifications(execution),
        render_node_outputs(CURRENT_WORKFLOW, execution),
    )


def _clear():
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID
    CURRENT_WORKFLOW = None
    CURRENT_EXECUTION_ID = None
    drafts.reset()
    memory.reset()
    return (
        [],
        "",
        render_mermaid(None),
        render_workflow_summary(None),
        render_client_result(None),
        render_notifications(None),
        render_node_outputs(None),
        gr.update(choices=workflow_store.list(), value=None),
    )


def _save_current_workflow(name: str | None):
    if CURRENT_WORKFLOW is None:
        return "No active workflow to save.", gr.update(choices=workflow_store.list())
    path = workflow_store.save(CURRENT_WORKFLOW, name or None)
    return (
        f"Saved workflow `{path.name}` at `{path}`.",
        gr.update(choices=workflow_store.list(), value=path.name),
    )


def _load_workflow(filename: str | None):
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID
    if not filename:
        return (
            render_mermaid(CURRENT_WORKFLOW),
            render_workflow_summary(CURRENT_WORKFLOW),
            render_client_result(CURRENT_WORKFLOW),
            render_notifications(store.get(CURRENT_EXECUTION_ID)),
            render_node_outputs(CURRENT_WORKFLOW, store.get(CURRENT_EXECUTION_ID)),
            "Select a workflow file to load.",
        )
    CURRENT_WORKFLOW = workflow_store.load(filename)
    CURRENT_EXECUTION_ID = None
    drafts.reset()
    return (
        render_mermaid(CURRENT_WORKFLOW),
        render_workflow_summary(CURRENT_WORKFLOW),
        render_client_result(CURRENT_WORKFLOW),
        render_notifications(None),
        render_node_outputs(CURRENT_WORKFLOW),
        f"Loaded workflow `{filename}` from `{workflow_store.root / filename}`.",
    )


def _run_current_workflow():
    global CURRENT_EXECUTION_ID
    if CURRENT_WORKFLOW is None:
        return (
            render_mermaid(None),
            render_workflow_summary(None),
            render_client_result(None),
            render_notifications(None),
            render_node_outputs(None),
            "No active workflow to run.",
        )
    execution = SequentialExecutor(CURRENT_WORKFLOW, store=store).run()
    _toast_new_notifications(execution)
    CURRENT_EXECUTION_ID = execution.execution_id
    return (
        render_mermaid(CURRENT_WORKFLOW, execution),
        render_workflow_summary(CURRENT_WORKFLOW, execution),
        render_client_result(CURRENT_WORKFLOW, execution),
        render_notifications(execution),
        render_node_outputs(CURRENT_WORKFLOW, execution),
        f"Ran workflow `{CURRENT_WORKFLOW.name}` with status `{execution.status}`.",
    )


def _render_reply(notes: list[str], execution: ExecutionState) -> str:
    lines = [
        f"Workflow `{execution.workflow.name}` executed with status `{execution.status}`.",
        *[f"- {note}" for note in notes],
    ]
    if execution.error:
        lines.append(f"- Error: {execution.error}")
    else:
        final = next(reversed(execution.nodes.values()))
        if final.output and final.output.get("sent"):
            lines.append(f"- Alert sent: `{final.output.get('alert_id')}`")
    return "\n".join(lines)


def _render_execution_json(execution: ExecutionState | None) -> str:
    if execution is None:
        return "{}"
    return execution.model_dump_json(indent=2)


_theme = gr.themes.Soft(
    primary_hue=gr.themes.colors.blue,
    secondary_hue=gr.themes.colors.slate,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
).set(
    # body
    body_background_fill="#f4f6f9",
    body_text_color="#101828",
    body_text_color_subdued="#667085",
    background_fill_primary="#ffffff",
    background_fill_secondary="#f4f6f9",
    # blocks
    block_background_fill="#ffffff",
    block_border_color="#d0d5dd",
    block_border_width="1px",
    block_label_text_color="#344054",
    block_title_text_color="#101828",
    # inputs
    input_background_fill="#ffffff",
    input_border_color="#d0d5dd",
    input_border_color_focus="#84caff",
    input_placeholder_color="#98a2b3",
    # buttons
    button_primary_background_fill="#1570ef",
    button_primary_background_fill_hover="#175cd3",
    button_primary_text_color="#ffffff",
    button_primary_border_color="#1570ef",
    button_secondary_background_fill="#ffffff",
    button_secondary_background_fill_hover="#f4f6f9",
    button_secondary_text_color="#344054",
    button_secondary_border_color="#d0d5dd",
    # code / misc
    code_background_fill="#f0f2f5",
    shadow_drop="none",
    shadow_drop_lg="0 4px 16px rgba(16,24,40,0.08)",
)

APP_CSS = """
/* force light mode globally */
:root { color-scheme: light; }

.gradio-container { max-width: 1560px !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar       { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #d0d5dd; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #98a2b3; }

/* ═══════════════════════════════════════════════
   CHAT PANEL  (left column)
═══════════════════════════════════════════════ */
#chat-col {
  background: #ffffff;
  border: 1px solid #eaecf0;
  border-radius: 16px;
  padding: 0 !important;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* strip default block borders inside the chat column */
#chat-col > .block,
#chat-col .gap {
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  padding: 0 !important;
}

/* chatbot scroll area */
#chat-col .wrap {
  background: #ffffff !important;
  padding: 12px 16px !important;
}

/* panel-layout: user row */
#chat-col [data-testid="user"] {
  background: #f4f6f9 !important;
  border-radius: 12px 12px 4px 12px !important;
  padding: 10px 14px !important;
  margin: 2px 0 !important;
}

/* panel-layout: bot row */
#chat-col [data-testid="bot"] {
  background: #ffffff !important;
  border-radius: 4px 12px 12px 12px !important;
  padding: 10px 14px !important;
  margin: 2px 0 !important;
}

#chat-col [data-testid="user"] *,
#chat-col [data-testid="bot"] * {
  color: #101828 !important;
  font-size: 14px !important;
  line-height: 1.65 !important;
}

/* chat input area — sits at bottom */
#chat-input-area {
  border-top: 1px solid #eaecf0;
  padding: 12px 16px;
  background: #ffffff;
}

#chat-input-area textarea {
  border: 1px solid #d0d5dd !important;
  border-radius: 10px !important;
  background: #ffffff !important;
  color: #101828 !important;
  font-size: 14px !important;
  resize: none !important;
}

#chat-input-area textarea:focus {
  border-color: #84caff !important;
  box-shadow: 0 0 0 3px rgba(20,112,239,0.1) !important;
}

/* ═══════════════════════════════════════════════
   TASK PANEL  (right column — canvas feel)
═══════════════════════════════════════════════ */
#task-col {
  background: #f4f6f9;
  border: 1px solid #eaecf0;
  border-radius: 16px;
  padding: 20px !important;
}

#task-col > .block,
#task-col .gap {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}

/* graph canvas card */
.graph-panel {
  background: #ffffff;
  border: 1px solid #eaecf0;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(16,24,40,0.06);
}

/* result / notification cards */
.result-card,
.notification-card {
  background: #ffffff;
  border: 1px solid #eaecf0;
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 10px;
  box-shadow: 0 1px 4px rgba(16,24,40,0.04);
  color: #101828;
}

/* action bar */
.action-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

/* ── Dropdown: Gradio sets border-none → invisible ── */
input.border-none,
input[role="listbox"],
input[role="combobox"] {
  border: 1px solid #d0d5dd !important;
  color: #101828 !important;
  background: #ffffff !important;
  border-radius: 6px !important;
  padding: 4px 10px !important;
  opacity: 1 !important;
}
input.border-none::placeholder,
input[role="listbox"]::placeholder {
  color: #98a2b3 !important;
  opacity: 1 !important;
}
"""


with gr.Blocks(title="AI-Native Workflow Engine", theme=_theme) as demo:
    gr.Markdown("# AI-Native Workflow Engine")
    with gr.Row(equal_height=True):
        with gr.Column(scale=4, elem_id="chat-col"):
            chat = gr.Chatbot(
                label="Chat với agent",
                height=580,
                value=[],
                layout="panel",
                show_label=False,
                placeholder="Bắt đầu bằng cách mô tả cảnh báo bạn muốn xử lý...",
            )
            with gr.Group(elem_id="chat-input-area"):
                prompt = gr.Textbox(
                    show_label=False,
                    placeholder="Ví dụ: Tôi muốn xử lý cảnh báo node high memory",
                    lines=2,
                    container=False,
                )
                with gr.Row():
                    submit = gr.Button("Gửi", variant="primary", scale=3)
                    clear = gr.Button("Xoá phiên", scale=1)
        with gr.Column(scale=7, elem_id="task-col"):
            gr.Markdown("## Generated task")
            with gr.Group(elem_classes=["graph-panel"]):
                gr.Markdown("### Workflow graph")
                graph = gr.HTML(render_mermaid(None), label="Workflow graph")
            result_card = gr.Markdown(
                render_client_result(None),
                elem_classes=["result-card"],
            )
            notification_center = gr.Markdown(
                render_notifications(None),
                elem_classes=["notification-card"],
            )
            with gr.Row(elem_classes=["action-bar"]):
                run_workflow = gr.Button("▶ Chạy workflow", variant="primary", scale=2)
                workflow_name = gr.Textbox(
                    show_label=False,
                    placeholder="Tên workflow...",
                    scale=3,
                    container=False,
                )
                save_workflow = gr.Button("Lưu", scale=1)
            workflow_action_status = gr.Markdown("")
            with gr.Accordion("Chi tiết chạy / logs", open=False):
                workflow_summary = gr.Textbox(value=render_workflow_summary(None), label="Run details", lines=12)
                node_outputs = gr.Markdown(render_node_outputs(None), label="Node outputs")
            with gr.Accordion("Lưu / tải workflow", open=False):
                workflow_files = gr.Dropdown(
                    choices=workflow_store.list(),
                    label="Workflow đã lưu",
                    interactive=True,
                )
                with gr.Row():
                    load_workflow = gr.Button("Tải workflow")
            with gr.Accordion("Debug: system capabilities", open=False):
                gr.JSON(value=get_registry_manifest(), label="Node Registry Manifest")

    submit.click(
        fn=_submit,
        inputs=[prompt, chat],
        outputs=[prompt, chat, graph, workflow_summary, result_card, notification_center, node_outputs],
    )
    prompt.submit(
        fn=_submit,
        inputs=[prompt, chat],
        outputs=[prompt, chat, graph, workflow_summary, result_card, notification_center, node_outputs],
    )
    clear.click(_clear, outputs=[chat, prompt, graph, workflow_summary, result_card, notification_center, node_outputs, workflow_files])
    save_workflow.click(fn=_save_current_workflow, inputs=[workflow_name], outputs=[workflow_action_status, workflow_files])
    load_workflow.click(
        fn=_load_workflow,
        inputs=[workflow_files],
        outputs=[graph, workflow_summary, result_card, notification_center, node_outputs, workflow_action_status],
    )
    run_workflow.click(
        fn=_run_current_workflow,
        outputs=[graph, workflow_summary, result_card, notification_center, node_outputs, workflow_action_status],
    )

    timer = gr.Timer(value=1.0)
    timer.tick(fn=_refresh_graph, inputs=[], outputs=[graph, workflow_summary, result_card, notification_center, node_outputs])


if __name__ == "__main__":
    demo.launch(css=APP_CSS)
