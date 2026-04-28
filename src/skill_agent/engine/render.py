from __future__ import annotations

import html
import json
from typing import Any

from src.skill_agent.engine.models import ExecutionState, NodeStatus, Workflow

_STATUS_COLOR = {
    NodeStatus.PENDING: "#667085",
    NodeStatus.RUNNING: "#b54708",
    NodeStatus.SUCCESS: "#027a48",
    NodeStatus.ERROR: "#b42318",
    NodeStatus.SKIPPED: "#98a2b3",
}


def render_mermaid(workflow: Workflow | None, state: ExecutionState | None = None) -> str:
    if workflow is None:
        return "<div style='padding:24px;border:1px dashed #c9d1d9;border-radius:14px;color:#57606a'>No workflow generated yet.</div>"

    node_states = state.nodes if state else {}
    node_width = 250
    node_height = 88
    gap = 54
    margin = 28
    count = len(workflow.nodes)
    width = max(520, margin * 2 + count * node_width + max(0, count - 1) * gap)
    height = 250

    svg_parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='100%' height='{height}' role='img'>",
        "<defs>",
        "<filter id='shadow' x='-20%' y='-20%' width='140%' height='140%'>",
        "<feDropShadow dx='0' dy='2' stdDeviation='3' flood-color='#101828' flood-opacity='0.08'/>",
        "</filter>",
        "<marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto-start-reverse'>",
        "<path d='M 0 0 L 10 5 L 0 10 z' fill='#98a2b3'/>",
        "</marker>",
        "</defs>",
        "<rect x='0' y='0' width='100%' height='100%' rx='18' fill='#ffffff'/>",
    ]

    for idx, node in enumerate(workflow.nodes):
        x = margin + idx * (node_width + gap)
        y = 82
        if idx > 0:
            line_x1 = x - gap + 7
            line_x2 = x - 10
            line_y = y + node_height / 2
            svg_parts.append(
                f"<line x1='{line_x1}' y1='{line_y}' x2='{line_x2}' y2='{line_y}' stroke='#98a2b3' stroke-width='2.5' marker-end='url(#arrow)'/>"
            )
        status = node_states.get(node.id).status if node.id in node_states else NodeStatus.PENDING
        color = _STATUS_COLOR[status]
        title = html.escape(node.label[:34])
        node_type = html.escape(node.type)
        status_text = html.escape(status.value.title())
        svg_parts.extend(
            [
                f"<rect x='{x}' y='{y}' width='{node_width}' height='{node_height}' rx='12' fill='#ffffff' stroke='#d0d5dd' stroke-width='1.5' filter='url(#shadow)'/>",
                f"<rect x='{x}' y='{y}' width='8' height='{node_height}' rx='4' fill='{color}'/>",
                f"<text x='{x + 20}' y='{y + 32}' font-family='Verdana, sans-serif' font-size='15' font-weight='700' fill='#101828'>{title}</text>",
                f"<text x='{x + 20}' y='{y + 58}' font-family='Verdana, sans-serif' font-size='12' fill='#475467'>{node_type}</text>",
                f"<text x='{x + node_width - 18}' y='{y + 58}' text-anchor='end' font-family='Verdana, sans-serif' font-size='12' font-weight='700' fill='{color}'>{status_text}</text>",
            ]
        )

    svg_parts.append("</svg>")
    return "<div class='workflow-card'>" + "".join(svg_parts) + "</div>"


def render_workflow_summary(workflow: Workflow | None, state: ExecutionState | None = None) -> str:
    if workflow is None:
        return "No workflow is loaded yet.\n\nAsk for a runbook, load a saved workflow, or create one in chat."

    lines = [
        f"Workflow: {workflow.name}",
        f"Run mode: {_trigger_label(workflow)}",
        f"Steps: {len(workflow.nodes)}",
    ]
    if state is not None:
        lines.append(f"Run status: {_status_label(state.status)}")
        if state.error:
            lines.append(f"Problem: {state.error}")
        for node in workflow.nodes:
            node_state = state.nodes.get(node.id)
            status = node_state.status.value if node_state else "pending"
            suffix = ""
            if node_state and node_state.error:
                suffix = f" - {node_state.error}"
            lines.append(f"- {node.label}: {_status_label(status)}{suffix}")
    else:
        lines.append("Run status: Not run yet")
        lines.extend(f"- {node.label}: ready" for node in workflow.nodes)
    return "\n".join(lines)


def render_client_result(workflow: Workflow | None, state: ExecutionState | None = None) -> str:
    if workflow is None:
        return "### Chưa có workflow\nHãy mô tả cảnh báo/runbook trong khung chat để hệ thống tạo workflow."
    if state is None:
        return (
            f"### Sẵn sàng chạy\n"
            f"**Workflow:** {workflow.name}\n\n"
            f"**Cách chạy:** {_trigger_label(workflow)}\n\n"
            "Workflow chưa chạy. Bấm **Chạy workflow** hoặc gõ `confirm` trong chat."
        )

    final_output = _final_output(state)
    if state.status == "success":
        return (
            "### Đã chạy xong\n"
            f"**Workflow:** {workflow.name}\n\n"
            f"**Kết quả:** Thành công\n\n"
            f"**Output cuối:**\n```json\n{json.dumps(final_output, ensure_ascii=False, indent=2)}\n```"
        )
    return (
        "### Chạy thất bại\n"
        f"**Workflow:** {workflow.name}\n\n"
        f"**Lỗi:** {state.error or 'Không rõ lỗi'}"
    )


def render_node_outputs(workflow: Workflow | None, state: ExecutionState | None = None) -> str:
    if workflow is None:
        return "Chưa có workflow."
    if state is None:
        return "Workflow chưa chạy nên chưa có output từng node."

    sections = ["### Output từng node"]
    for node in workflow.nodes:
        node_state = state.nodes.get(node.id)
        if node_state is None:
            payload = {"status": "missing"}
        else:
            payload = {
                "node_id": node.id,
                "node_type": node.type,
                "label": node.label,
                "status": node_state.status.value,
                "output": node_state.output,
                "error": node_state.error,
                "started_at": node_state.started_at.isoformat() if node_state.started_at else None,
                "finished_at": node_state.finished_at.isoformat() if node_state.finished_at else None,
            }
        sections.append(
            f"#### {html.escape(node.id)} · {html.escape(node.label)}\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
    return "\n\n".join(sections)


def render_notifications(state: ExecutionState | None = None) -> str:
    notifications = _notifications(state)
    if not notifications:
        return "### Thông báo\nChưa có thông báo. Chạy workflow có bước notify để xem kết quả."

    cards = ["### Thông báo"]
    for item in reversed(notifications):
        severity = str(item.get("severity", "info")).upper()
        owner = item.get("owner_type", "owner")
        notification_id = item.get("notification_id", "")
        notified_at = item.get("notified_at", "")
        ticket = item.get("ticket") if isinstance(item.get("ticket"), dict) else None
        if ticket:
            cards.append(_render_ticket_card(ticket, severity=severity, owner=str(owner)))
        else:
            message = item.get("message", "")
            cards.append(
                "\n".join(
                    [
                        f"**Đã gửi thông báo {severity} tới `{owner}`**",
                        f"- Nội dung: {message}",
                        f"- Mã thông báo: `{notification_id}`",
                        f"- Thời điểm: `{notified_at}`",
                    ]
                )
            )
    return "\n\n---\n\n".join(cards)


def notification_items(state: ExecutionState | None = None) -> list[dict[str, Any]]:
    return _notifications(state)


def _render_ticket_card(ticket: dict[str, Any], *, severity: str, owner: str) -> str:
    title = ticket.get("title", "")
    obj = ticket.get("object", "")
    metric = ticket.get("metric", "")
    value = ticket.get("value", "")
    threshold = ticket.get("threshold", "")
    detail = ticket.get("detail", "")
    timestamp = ticket.get("timestamp", "")
    ticket_id = ticket.get("id", "")
    stripe = "#b42318" if severity in {"CRITICAL", "ERROR"} else "#344054"
    return f"""
<div style="border:1px solid #d0d5dd;border-left:5px solid {stripe};border-radius:12px;padding:14px 16px;background:#ffffff;color:#101828;margin:10px 0;font-family:Verdana,sans-serif;">
  <div style="font-size:12px;font-weight:700;color:#475467;text-transform:uppercase;letter-spacing:.04em;">{html.escape(severity)} · gửi tới {html.escape(owner)}</div>
  <div style="font-size:18px;font-weight:800;margin-top:6px;color:#101828;line-height:1.35;">[TICKET] {html.escape(str(title))}</div>
  <div style="display:grid;grid-template-columns:118px 1fr;gap:7px 12px;margin-top:14px;font-size:14px;line-height:1.45;color:#101828;">
    <div style="font-weight:700;color:#344054;">Đối tượng</div><div>{html.escape(str(obj))}</div>
    <div style="font-weight:700;color:#344054;">Chỉ số</div><div><code style="background:#f2f4f7;color:#101828;padding:2px 5px;border-radius:5px;">{html.escape(str(metric))}</code> = <b>{html.escape(str(value))}</b> <span style="color:#475467;">(ngưỡng: {html.escape(str(threshold))})</span></div>
    <div style="font-weight:700;color:#344054;">Chi tiết</div><div>{html.escape(str(detail))}</div>
    <div style="font-weight:700;color:#344054;">Thời gian</div><div>{html.escape(str(timestamp))}</div>
    <div style="font-weight:700;color:#344054;">ID</div><div><code style="background:#f2f4f7;color:#101828;padding:2px 5px;border-radius:5px;">{html.escape(str(ticket_id))}</code></div>
  </div>
</div>
""".strip()


def _final_output(state: ExecutionState) -> dict[str, Any]:
    for node_id in reversed(list(state.nodes.keys())):
        node_state = state.nodes[node_id]
        output = node_state.output
        if output is not None and node_state.status != NodeStatus.SKIPPED:
            return output
    return {}


def _notifications(state: ExecutionState | None) -> list[dict[str, Any]]:
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    for node_state in state.nodes.values():
        output = node_state.output or {}
        if output.get("notified") is True or output.get("sent") is True:
            out.append(output)
    return out


def _trigger_label(workflow: Workflow) -> str:
    if workflow.trigger.type == "schedule":
        return f"Theo lịch: {workflow.trigger.schedule}"
    return "Theo yêu cầu: gõ confirm trong chat hoặc bấm Chạy workflow"


def _status_label(status: str) -> str:
    return {
        "pending": "Đang chờ",
        "running": "Đang chạy",
        "success": "Thành công",
        "error": "Thất bại",
        "skipped": "Bỏ qua",
    }.get(status, status)


def _escape_label(value: str) -> str:
    return value.replace('"', "'")
