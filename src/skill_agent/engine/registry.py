from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel


class NodeEntry(BaseModel):
    node_type: str
    description: str
    path: str
    params_schema: dict[str, Any]
    output_schema: dict[str, Any]
    built_by: str


_NODE_ROOT = Path(__file__).parent / "nodes" / "builtin"


def _entry(
    node_type: str,
    description: str,
    params_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> NodeEntry:
    return NodeEntry(
        node_type=node_type,
        description=description,
        path=str((_NODE_ROOT / node_type).resolve()),
        params_schema=params_schema,
        output_schema=output_schema,
        built_by="builtin",
    )


NODE_REGISTRY: dict[str, NodeEntry] = {
    "check_dcim_service": _entry(
        "check_dcim_service",
        "Look up what service runs on a network node IP and mark excluded database/cloud compute services.",
        {
            "ip": "string, network node IP",
            "excluded_services": "list[string], services excluded from this runbook",
        },
        {"service": "string", "service_type": "string", "excluded": "boolean", "ip": "string"},
    ),
    "check_metric_threshold": _entry(
        "check_metric_threshold",
        "Mock a metric collection and compare it with a threshold. Reusable for RAM, CPU, load, and IO.",
        {
            "metric": "available_ram_gb|cpu_usage_percent|cpu_load_average|io_util_percent",
            "operator": "one of <, <=, >, >=, ==, !=",
            "value": "number threshold",
            "mock_value": "optional number for deterministic demo",
            "unit": "string",
        },
        {"metric": "string", "value": "number", "threshold": "number", "passed": "boolean", "unit": "string"},
    ),
    "get_top_processes": _entry(
        "get_top_processes",
        "Return top N processes by RAM or CPU usage.",
        {
            "metric": "ram|cpu",
            "limit": "integer",
            "mock_processes": "optional list of process objects",
        },
        {"processes": "list[{pid,user,usage,name,metric}]", "metric": "string"},
    ),
    "notify_owner": _entry(
        "notify_owner",
        "Mock notification to the application, system, or server owner.",
        {
            "owner_type": "application|system|server|bo_ht",
            "message": "string",
            "severity": "info|warning|critical",
        },
        {"notified": "boolean", "owner_type": "string", "message": "string", "severity": "string"},
    ),
    "condition": _entry(
        "condition",
        "LLM-assisted if/else condition node. Evaluates dynamic natural-language conditions against upstream node output and emits matched plus branch.",
        {
            "condition": "string, natural-language if/else condition to evaluate against input JSON",
            "true_branch": "string label when condition is true",
            "false_branch": "string label when condition is false",
            "field": "optional deterministic fallback input field path, e.g. value or metrics.cpu",
            "operator": "optional deterministic fallback operator: <, <=, >, >=, ==, !=",
            "value": "optional deterministic fallback comparison value",
        },
        {
            "matched": "boolean",
            "branch": "string",
            "condition": "string",
            "reason": "string",
            "condition_source": "llm|fallback",
        },
    ),
    "fetch_signal": _entry(
        "fetch_signal",
        "Fetch a simulated telecom signal metric for a BTS station.",
        {
            "station_id": "string, e.g. BTS_042",
            "metric": "string, defaults to RSSI",
            "simulate_drop": "boolean, force degraded RSSI for demos",
        },
        {"value": "number", "timestamp": "ISO datetime", "station_id": "string", "metric": "string"},
    ),
    "threshold_check": _entry(
        "threshold_check",
        "Compare an input value with a numeric threshold.",
        {"operator": "one of <, <=, >, >=, ==, !=", "value": "number", "field": "input field, default value", "unit": "string"},
        {"passed": "boolean", "value": "number", "threshold": "number", "operator": "string", "unit": "string"},
    ),
    "time_window": _entry(
        "time_window",
        "Represent a duration requirement for an already-computed condition.",
        {"duration_seconds": "integer", "condition_field": "input field, default passed"},
        {"satisfied": "boolean", "duration": "integer"},
    ),
    "aggregate": _entry(
        "aggregate",
        "Aggregate numeric values with avg, min, max, sum, or count.",
        {"function": "avg|min|max|sum|count", "field": "input field, default value"},
        {"result": "number", "count": "integer", "function": "string"},
    ),
    "send_alert": _entry(
        "send_alert",
        "Emit an alert payload when the upstream condition is satisfied.",
        {"message": "string", "severity": "info|warning|critical", "condition_field": "input field, default satisfied"},
        {"alert_id": "string|null", "sent_at": "ISO datetime|null", "sent": "boolean", "message": "string"},
    ),
}


def get_node(node_type: str) -> NodeEntry:
    if node_type not in NODE_REGISTRY:
        valid = ", ".join(sorted(NODE_REGISTRY))
        raise ValueError(f"Node type '{node_type}' does not exist. Valid types: {valid}")
    return NODE_REGISTRY[node_type]


def register_node(entry: NodeEntry) -> None:
    NODE_REGISTRY[entry.node_type] = entry


def get_registry_manifest() -> dict[str, dict[str, Any]]:
    return {
        node_type: {
            "description": entry.description,
            "built_by": entry.built_by,
            "params_schema": entry.params_schema,
            "output_schema": entry.output_schema,
        }
        for node_type, entry in NODE_REGISTRY.items()
    }
