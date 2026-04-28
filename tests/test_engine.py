from __future__ import annotations

import pytest

from src.skill_agent.agents import WorkflowBuilderAgent
from src.skill_agent.engine import SequentialExecutor, Workflow, WorkflowStore, get_registry_manifest
from src.skill_agent.engine.registry import get_node
from src.skill_agent.engine.runner import run_node_script


def test_workflow_validates_edge_refs() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        Workflow.model_validate(
            {
                "name": "bad",
                "nodes": [{"id": "n1", "type": "fetch_signal", "label": "Fetch"}],
                "edges": [{"from": "n1", "to": "missing"}],
            }
        )


def test_workflow_requires_valid_schedule_trigger() -> None:
    with pytest.raises(ValueError, match="Scheduled workflows require"):
        Workflow.model_validate(
            {
                "name": "scheduled",
                "trigger": {"type": "schedule"},
                "nodes": [{"id": "n1", "type": "fetch_signal", "label": "Fetch"}],
                "edges": [],
            }
        )

    workflow = Workflow.model_validate(
        {
            "name": "manual",
            "trigger": {"type": "on_request"},
            "nodes": [{"id": "n1", "type": "fetch_signal", "label": "Fetch"}],
            "edges": [],
        }
    )
    assert workflow.trigger.type == "on_request"


def test_registry_manifest_contains_builtin_nodes() -> None:
    manifest = get_registry_manifest()
    assert {
        "fetch_signal",
        "threshold_check",
        "time_window",
        "aggregate",
        "send_alert",
        "check_dcim_service",
        "check_metric_threshold",
        "get_top_processes",
        "notify_owner",
        "condition",
    } <= set(manifest)
    assert manifest["fetch_signal"]["built_by"] == "builtin"


def test_node_runner_executes_builtin_threshold_check() -> None:
    entry = get_node("threshold_check")
    output = run_node_script(
        entry.path,
        {"operator": "<", "value": -90, "field": "value", "unit": "dBm"},
        {"value": -95},
        None,
    )
    assert output["passed"] is True
    assert output["threshold"] == -90


def test_node_runner_executes_builtin_condition_fallback() -> None:
    entry = get_node("condition")
    output = run_node_script(
        entry.path,
        {
            "condition": "if value < -90 then alert else ignore",
            "true_branch": "alert",
            "false_branch": "ignore",
            "field": "value",
            "operator": "<",
            "value": -90,
        },
        {"value": -95, "station_id": "BTS_042"},
        None,
    )

    assert output["matched"] is True
    assert output["branch"] == "alert"
    assert output["condition_source"] == "fallback"


def test_executor_routes_selected_branch_and_skips_unselected_branch() -> None:
    workflow = Workflow.model_validate(
        {
            "name": "Branch on RSSI",
            "nodes": [
                {
                    "id": "n1",
                    "type": "fetch_signal",
                    "label": "Fetch RSSI",
                    "params": {"station_id": "BTS_042", "metric": "RSSI", "simulate_drop": True},
                },
                {
                    "id": "n2",
                    "type": "condition",
                    "label": "Route by RSSI threshold",
                    "params": {
                        "condition": "if value < -90 then alert else ignore",
                        "true_branch": "alert",
                        "false_branch": "ignore",
                        "field": "value",
                        "operator": "<",
                        "value": -90,
                    },
                },
                {
                    "id": "n3",
                    "type": "send_alert",
                    "label": "Send critical alert",
                    "params": {"message": "RSSI low", "severity": "critical", "condition_field": "matched"},
                },
                {
                    "id": "n4",
                    "type": "aggregate",
                    "label": "Ignore branch placeholder",
                    "params": {"function": "count", "field": "value"},
                },
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n2", "to": "n3", "when": "alert"},
                {"from": "n2", "to": "n4", "when": "ignore"},
            ],
        }
    )

    state = SequentialExecutor(workflow).run()

    assert state.status == "success"
    assert state.nodes["n2"].output["branch"] == "alert"
    assert state.nodes["n3"].status.value == "success"
    assert state.nodes["n3"].output["sent"] is True
    assert state.nodes["n4"].status.value == "skipped"


def test_executor_branch_join_runs_when_one_incoming_branch_is_active() -> None:
    workflow = Workflow.model_validate(
        {
            "name": "Branch join",
            "nodes": [
                {
                    "id": "n1",
                    "type": "fetch_signal",
                    "label": "Fetch RSSI",
                    "params": {"station_id": "BTS_042", "metric": "RSSI", "simulate_drop": True},
                },
                {
                    "id": "n2",
                    "type": "condition",
                    "label": "Route true false",
                    "params": {
                        "condition": "if value < -90 then alert else ignore",
                        "true_branch": "alert",
                        "false_branch": "ignore",
                        "field": "value",
                        "operator": "<",
                        "value": -90,
                    },
                },
                {
                    "id": "n3",
                    "type": "send_alert",
                    "label": "True branch",
                    "params": {"message": "true branch", "severity": "warning", "condition_field": "matched"},
                },
                {
                    "id": "n4",
                    "type": "aggregate",
                    "label": "False branch",
                    "params": {"function": "count", "field": "value"},
                },
                {
                    "id": "n5",
                    "type": "notify_owner",
                    "label": "Join notification",
                    "params": {"owner_type": "system", "severity": "warning", "message": "Branch completed"},
                },
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n2", "to": "n3", "when": "alert"},
                {"from": "n2", "to": "n4", "when": "ignore"},
                {"from": "n3", "to": "n5"},
                {"from": "n4", "to": "n5"},
            ],
        }
    )

    state = SequentialExecutor(workflow).run()

    assert state.status == "success"
    assert state.nodes["n3"].status.value == "success"
    assert state.nodes["n4"].status.value == "skipped"
    assert state.nodes["n5"].status.value == "success"
    assert state.nodes["n5"].output["notified"] is True


def test_builder_and_executor_happy_path() -> None:
    workflow = WorkflowBuilderAgent().build(
        "Monitor BTS_042 RSSI and alert when it drops below -90 dBm for 30 seconds"
    ).workflow
    state = SequentialExecutor(workflow).run()

    assert state.status == "success"
    assert state.nodes["n4"].output is not None
    assert state.nodes["n4"].output["sent"] is True


def test_high_memory_runbook_uses_builtin_nodes() -> None:
    workflow = WorkflowBuilderAgent().build("Tôi muốn xử lý cảnh báo node high memory").workflow
    assert [node.type for node in workflow.nodes] == [
        "check_dcim_service",
        "check_metric_threshold",
        "get_top_processes",
        "notify_owner",
        "notify_owner",
    ]

    state = SequentialExecutor(workflow).run()
    assert state.status == "success"
    assert state.nodes["n5"].output is not None
    assert state.nodes["n5"].output["notified"] is True


def test_workflow_store_save_load(tmp_path) -> None:
    workflow = WorkflowBuilderAgent().build("Tôi muốn xử lý cảnh báo node high memory").workflow
    store = WorkflowStore(tmp_path)

    path = store.save(workflow)
    loaded = store.load(path.name)

    assert path.name in store.list()
    assert loaded.name == workflow.name
    assert loaded.trigger.type == "on_request"
