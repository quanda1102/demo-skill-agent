from __future__ import annotations

from src.skill_agent.agents.workflow_draft import WorkflowDraftManager


def test_memory_workflow_draft_accumulates_state_and_confirms() -> None:
    manager = WorkflowDraftManager()

    first = manager.handle(
        "Bước 1 kiểm tra DCIM. Bước 2 kiểm tra RAM. Tôi muốn xử lý cảnh báo node high memory"
    )
    assert first.workflow is not None
    assert first.ready is False
    assert [node.type for node in first.workflow.nodes][:3] == [
        "check_dcim_service",
        "check_metric_threshold",
        "get_top_processes",
    ]
    assert first.draft is not None
    assert len(first.draft.verifications) == len(first.workflow.nodes)
    assert all(item.ok for item in first.draft.verifications)

    second = manager.handle("ip = 123, ngưỡng tuỳ bạn, mô phỏng windows thôi, không cần")
    assert second.draft is not None
    assert second.draft.slots["ip"] == "123"
    assert second.draft.slots["os"] == "windows"
    assert second.draft.slots["skip_notify"] is True

    confirmed = manager.handle("confirm")
    assert confirmed.ready is True
    assert confirmed.workflow is not None
    assert confirmed.workflow.name == "Draft: Node high memory usage"


def test_draft_manager_does_not_switch_memory_context_to_rssi() -> None:
    manager = WorkflowDraftManager()
    manager.handle("Tôi muốn xử lý cảnh báo node high memory")

    update = manager.handle("RSSI < -95 dBm")

    assert update.draft is not None
    assert update.draft.slots["runbook"] == "high_memory"
    assert update.workflow is not None
    assert update.workflow.name == "Draft: Node high memory usage"


def test_signal_draft_can_be_created_when_requested_first() -> None:
    manager = WorkflowDraftManager()
    result = manager.handle("Build a BTS signal drop alert workflow for station BTS_042 RSSI < -95 dBm")

    assert result.workflow is not None
    assert result.workflow.name == "Draft: RSSI signal alert"
    assert [node.type for node in result.workflow.nodes] == [
        "fetch_signal",
        "threshold_check",
        "send_alert",
    ]
