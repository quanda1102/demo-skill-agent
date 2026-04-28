from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from src.skill_agent.providers.openai_provider import OpenAIProvider
    from src.skill_agent.providers.provider import MinimaxProvider
except Exception:
    OpenAIProvider = None
    MinimaxProvider = None


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]

    owner_type = params.get("owner_type", "application")
    message = params.get("message", "Runbook notification")
    severity = params.get("severity", "warning")
    notification_id = f"notify_{uuid.uuid4().hex[:8]}"
    notified_at = datetime.now(timezone.utc).isoformat()
    ticket, message_source = compose_ticket(
        owner_type=owner_type,
        severity=severity,
        message=message,
        context=input_data,
        notification_id=notification_id,
        notified_at=notified_at,
    )
    notification_message = format_ticket(ticket)
    print(
        json.dumps(
            {
                **input_data,
                "notified": True,
                "notification_id": notification_id,
                "owner_type": owner_type,
                "raw_message": message,
                "message": notification_message,
                "ticket": ticket,
                "message_source": message_source,
                "severity": severity,
                "notified_at": notified_at,
            }
        )
    )


def compose_ticket(
    *,
    owner_type: str,
    severity: str,
    message: str,
    context: dict[str, Any],
    notification_id: str,
    notified_at: str,
) -> tuple[dict[str, str], str]:
    input_json = {
        "owner_type": owner_type,
        "severity": severity,
        "message": message,
        "context": context,
        "timestamp": notified_at,
        "id": notification_id,
    }
    provider = _provider_from_env()
    if provider is None:
        return _fallback_ticket(input_json), "fallback"

    try:
        result = provider.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Bạn là hệ thống giám sát tự động. Nhiệm vụ là tạo ticket sự cố ngắn gọn gửi cho người xử lý.\n\n"
                        "Yêu cầu:\n"
                        "- Chỉ trình bày facts từ dữ liệu đầu vào, KHÔNG đề xuất hành động, KHÔNG giải thích\n"
                        "- Ngắn gọn — người nhận đọc 10 giây biết ngay vấn đề là gì\n"
                        "- Không dùng bullet point lồng nhau, không dùng từ \"Bằng chứng\", \"Hành động đề xuất\"\n\n"
                        "Format output BẮT BUỘC:\n"
                        "---\n"
                        "[TICKET] <mô tả sự cố một dòng>\n"
                        "Đối tượng : <tên/id đối tượng bị ảnh hưởng>\n"
                        "Chỉ số    : <tên chỉ số> = <giá trị hiện tại> (ngưỡng: <ngưỡng cảnh báo>)\n"
                        "Chi tiết  : <1-2 dòng facts quan trọng nhất, nếu có>\n"
                        "Thời gian : <timestamp>\n"
                        "ID        : <id>\n"
                        "---"
                    ),
                },
                {
                    "role": "user",
                    "content": "Dữ liệu đầu vào:\n" + json.dumps(input_json, ensure_ascii=False, indent=2),
                },
            ],
            tools=None,
        )
    except Exception:
        return _fallback_ticket(input_json), "fallback"

    content = str(result.get("content") or "").strip()
    if not content:
        return _fallback_ticket(input_json), "fallback"
    parsed = _parse_ticket_text(content)
    if parsed is None:
        return _fallback_ticket(input_json), "fallback"
    return parsed, "llm"


def compose_ticket_message(**kwargs) -> tuple[str, str]:
    ticket, source = compose_ticket(**kwargs)
    return format_ticket(ticket), source


def _provider_from_env():
    provider_config = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider_config == "openai":
        if OpenAIProvider is None or not os.environ.get("OPENAI_API_KEY"):
            return None
        return OpenAIProvider(temperature=0.1, top_p=0.9, max_tokens=500)
    if MinimaxProvider is not None and os.environ.get("MINIMAX_ENDPOINT"):
        return MinimaxProvider(temperature=0.1, top_p=0.9, max_tokens=500)
    return None


def _fallback_ticket(input_json: dict[str, Any]) -> dict[str, str]:
    context = input_json.get("context", {})
    metric = context.get("metric", "unknown")
    value = context.get("value", "unknown")
    threshold = context.get("threshold", "unknown")
    obj = context.get("ip") or context.get("station_id") or context.get("service") or input_json.get("owner_type")
    detail = _detail(context, input_json.get("message", ""))
    title = f"{input_json.get('severity', 'warning').upper()} - {input_json.get('message', 'Sự cố cần xử lý')}"
    return {
        "title": title,
        "object": str(obj),
        "metric": str(metric),
        "value": str(value),
        "threshold": str(threshold),
        "detail": detail,
        "timestamp": str(input_json.get("timestamp")),
        "id": str(input_json.get("id")),
    }


def format_ticket(ticket: dict[str, str]) -> str:
    return (
        "---\n"
        f"[TICKET] {ticket.get('title', '')}\n"
        f"Đối tượng : {ticket.get('object', '')}\n"
        f"Chỉ số    : {ticket.get('metric', '')} = {ticket.get('value', '')} (ngưỡng: {ticket.get('threshold', '')})\n"
        f"Chi tiết  : {ticket.get('detail', '')}\n"
        f"Thời gian : {ticket.get('timestamp', '')}\n"
        f"ID        : {ticket.get('id', '')}\n"
        "---"
    )


def _parse_ticket_text(text: str) -> dict[str, str] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "---"]
    data: dict[str, str] = {}
    for line in lines:
        if line.startswith("[TICKET]"):
            data["title"] = line.removeprefix("[TICKET]").strip()
        elif line.startswith("Đối tượng"):
            data["object"] = line.split(":", 1)[1].strip()
        elif line.startswith("Chỉ số"):
            raw = line.split(":", 1)[1].strip()
            metric_value, _, threshold_part = raw.partition("(ngưỡng:")
            metric, _, value = metric_value.partition("=")
            data["metric"] = metric.strip()
            data["value"] = value.strip()
            data["threshold"] = threshold_part.removesuffix(")").strip()
        elif line.startswith("Chi tiết"):
            data["detail"] = line.split(":", 1)[1].strip()
        elif line.startswith("Thời gian"):
            data["timestamp"] = line.split(":", 1)[1].strip()
        elif line.startswith("ID"):
            data["id"] = line.split(":", 1)[1].strip()
    required = {"title", "object", "metric", "value", "threshold", "detail", "timestamp", "id"}
    return data if required <= set(data) else None


def _detail(context: dict[str, Any], message: str) -> str:
    processes = context.get("processes")
    if isinstance(processes, list) and processes:
        first = processes[0]
        if isinstance(first, dict):
            return (
                f"{message}. Tiến trình nổi bật: {first.get('name', 'unknown')} "
                f"pid={first.get('pid', 'unknown')} user={first.get('user', 'unknown')} usage={first.get('usage', 'unknown')}."
            )
    service = context.get("service")
    ip = context.get("ip")
    if service or ip:
        return f"{message}. Service={service or 'unknown'}, IP={ip or 'unknown'}."
    return message


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
