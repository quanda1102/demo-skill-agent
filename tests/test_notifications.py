from __future__ import annotations

import importlib.util
from pathlib import Path

from src.skill_agent.engine.registry import get_node
from src.skill_agent.engine.render import render_notifications
from src.skill_agent.engine.runner import run_node_script
from src.skill_agent.engine.models import ExecutionState, NodeState, NodeStatus, Workflow


def _load_notify_node_module():
    path = Path("src/skill_agent/engine/nodes/builtin/notify_owner/node.py").resolve()
    spec = importlib.util.spec_from_file_location("notify_owner_node", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notify_owner_node_is_self_contained_ticket_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_ENDPOINT", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    entry = get_node("notify_owner")
    output = run_node_script(
        entry.path,
        {
            "owner_type": "server",
            "severity": "warning",
            "message": "Server tiếp tục cao tải RAM",
        },
        {
            "ip": "10.0.12.34",
            "service": "web_app",
            "metric": "available_ram_gb",
            "value": 2,
            "threshold": 4,
            "processes": [{"name": "java-api", "pid": 123, "user": "app", "usage": 6144}],
        },
        None,
    )

    assert output["notified"] is True
    assert output["message_source"] == "fallback"
    assert output["raw_message"] == "Server tiếp tục cao tải RAM"
    assert output["message"].startswith("---\n[TICKET]")
    assert output["ticket"]["title"].startswith("WARNING")
    assert output["ticket"]["object"] == "10.0.12.34"
    assert output["ticket"]["metric"] == "available_ram_gb"
    assert "Đối tượng :" in output["message"]
    assert "Chỉ số    : available_ram_gb = 2 (ngưỡng: 4)" in output["message"]
    assert "Hành động đề xuất" not in output["message"]
    assert "Bằng chứng" not in output["message"]


def test_notification_renderer_uses_ticket_schema() -> None:
    workflow = Workflow.model_validate(
        {
            "name": "notify",
            "nodes": [{"id": "n1", "type": "notify_owner", "label": "Notify"}],
            "edges": [],
        }
    )
    state = ExecutionState(
        workflow_id=workflow.workflow_id,
        workflow=workflow,
        status="success",
        nodes={
            "n1": NodeState(
                status=NodeStatus.SUCCESS,
                output={
                    "notified": True,
                    "owner_type": "server",
                    "severity": "critical",
                    "ticket": {
                        "title": "RAM thấp",
                        "object": "10.0.12.34",
                        "metric": "available_ram_gb",
                        "value": "2",
                        "threshold": "4",
                        "detail": "Service web_app.",
                        "timestamp": "now",
                        "id": "notify_test",
                    },
                },
            )
        },
    )

    html = render_notifications(state)

    assert "[TICKET] RAM thấp" in html
    assert "display:grid" in html
    assert "notify_test" in html


def test_notify_owner_composer_uses_local_provider_factory(monkeypatch) -> None:
    module = _load_notify_node_module()

    class FakeProvider:
        def invoke(self, messages, tools=None):
            return {
                "content": (
                    "---\n"
                    "[TICKET] CPU load cao\n"
                    "Đối tượng : node-1\n"
                    "Chỉ số    : cpu_load_average = 24 (ngưỡng: 12)\n"
                    "Chi tiết  : Service web_app trên node-1.\n"
                    "Thời gian : now\n"
                    "ID        : notify_test\n"
                    "---"
                ),
                "tool_calls": None,
            }

    monkeypatch.setattr(module, "_provider_from_env", lambda: FakeProvider())
    ticket, source = module.compose_ticket(
        owner_type="system",
        severity="critical",
        message="CPU load cao",
        context={"metric": "cpu_load_average", "value": 24, "threshold": 12},
        notification_id="notify_test",
        notified_at="now",
    )

    assert source == "llm"
    assert ticket["title"] == "CPU load cao"
