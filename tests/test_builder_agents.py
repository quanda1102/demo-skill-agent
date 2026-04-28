from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.skill_agent.agents.node_builder import NodeBuilderAgent
from src.skill_agent.agents.workflow_builder import WorkflowBuilderAgent
from src.skill_agent.engine import SequentialExecutor
from src.skill_agent.engine.registry import get_node
from src.skill_agent.engine.runner import run_node_script


class SequencedProvider:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls
        self.index = 0

    def invoke(self, messages: list, tools: list | None = None, on_delta=None) -> dict:
        call = self.calls[self.index]
        self.index += 1
        if "tool" not in call:
            return {
                "role": "assistant",
                "content": call.get("content", ""),
                "tool_calls": None,
            }
        return {
            "role": "assistant",
            "content": call.get("content"),
            "tool_calls": [
                {
                    "id": f"call_{self.index}",
                    "type": "function",
                    "function": {
                        "name": call["tool"],
                        "arguments": json.dumps(call.get("args", {}), ensure_ascii=False),
                    },
                }
            ],
        }


def test_workflow_builder_uses_react_loop_and_schema_validation() -> None:
    provider = SequencedProvider(
        [
            {"tool": "get_registry_manifest"},
            {
                "tool": "submit_workflow",
                "args": {
                    "workflow": {
                        "name": "Monitor BTS_007 RSSI",
                        "nodes": [
                            {
                                "id": "n1",
                                "type": "fetch_signal",
                                "label": "Fetch RSSI",
                                "params": {"station_id": "BTS_007", "metric": "RSSI"},
                            },
                            {
                                "id": "n2",
                                "type": "threshold_check",
                                "label": "Check threshold",
                                "params": {"operator": "<", "value": -90, "field": "value"},
                            },
                        ],
                        "edges": [{"from": "n1", "to": "n2"}],
                    },
                    "notes": ["Validated via submit_workflow."],
                },
            },
        ]
    )
    result = WorkflowBuilderAgent(provider=provider).build("monitor BTS_007")

    assert result.workflow.name == "Monitor BTS_007 RSSI"
    assert result.workflow.nodes[0].type == "fetch_signal"
    assert "Validated" in result.notes[0]


def test_workflow_builder_does_not_stop_on_invalid_submit() -> None:
    provider = SequencedProvider(
        [
            {"tool": "get_registry_manifest"},
            {"tool": "submit_workflow", "args": {"notes": ["greeting only"]}},
            {
                "content": "Bạn muốn xử lý cảnh báo/runbook nào? Ví dụ: node high memory hoặc node high CPU load.",
            },
        ]
    )
    result = WorkflowBuilderAgent(provider=provider).build("hello")

    assert result.workflow is None
    assert "node high memory" in result.notes[0]


def test_workflow_builder_repairs_after_validation_error() -> None:
    provider = SequencedProvider(
        [
            {"tool": "get_registry_manifest"},
            {
                "tool": "submit_workflow",
                "args": {
                    "workflow": {
                        "name": "bad",
                        "nodes": [{"id": "n1", "type": "fetch_signal", "label": "Fetch"}],
                        "edges": [{"from": "n1", "to": "missing"}],
                    },
                    "notes": ["bad first submit"],
                },
            },
            {
                "tool": "submit_workflow",
                "args": {
                    "workflow": {
                        "name": "fixed",
                        "nodes": [{"id": "n1", "type": "fetch_signal", "label": "Fetch"}],
                        "edges": [],
                    },
                    "notes": ["fixed"],
                },
            },
        ]
    )
    result = WorkflowBuilderAgent(provider=provider).build("monitor")

    assert result.workflow is not None
    assert result.workflow.name == "fixed"


def test_node_builder_writes_tests_and_registers_node(tmp_path: Path) -> None:
    node_type = "scale_value_test"
    node_py = """
from __future__ import annotations
import json
import sys

def main():
    payload = json.loads(sys.stdin.read())
    value = payload["input"].get("value", 1)
    factor = payload["params"].get("factor", 2)
    print(json.dumps({"value": value * factor}))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
""".strip()
    provider = SequencedProvider(
        [
            {"tool": "get_node_contract"},
            {
                "tool": "write_node_files",
                "args": {
                    "node_type": node_type,
                    "description": "Scale an input value by a factor.",
                    "node_py": node_py,
                    "requirements_txt": "",
                    "params_schema": {"factor": "number"},
                    "output_schema": {"value": "number"},
                },
            },
            {
                "tool": "test_node",
                "args": {
                    "node_type": node_type,
                    "mock_payload": {"params": {"factor": 3}, "input": {"value": 4}},
                },
            },
            {"tool": "register_node", "args": {"node_type": node_type}},
        ]
    )

    result = NodeBuilderAgent(provider=provider, root_dir=tmp_path).build(
        node_type,
        "Scale an input value by a factor.",
        params_schema={"factor": "number"},
        output_schema={"value": "number"},
    )

    assert result.ok is True
    entry = get_node(node_type)
    output = run_node_script(entry.path, {"factor": 5}, {"value": 6}, None)
    assert output == {"value": 30}


def test_cpu_load_fallback_uses_demo_node_builder(tmp_path: Path) -> None:
    builder = WorkflowBuilderAgent(
        provider=None,
        node_builder=NodeBuilderAgent(provider=None, root_dir=tmp_path),
    )
    result = builder.build("Tôi muốn xử lý cảnh báo node high CPU load")

    node_types = [node.type for node in result.workflow.nodes]
    assert "check_io_stat" in node_types
    assert "check_nfs_mount" in node_types
    assert any("Node Builder created" in note for note in result.notes)

    state = SequentialExecutor(result.workflow).run()
    assert state.status == "success"
    assert state.nodes["n4"].output is not None
    assert state.nodes["n4"].output["conclusion"] == "io_bottleneck"
    assert state.nodes["n5"].output is not None
    assert state.nodes["n5"].output["conclusion"] == "mount_hung"


def test_fallback_greeting_returns_clarification() -> None:
    result = WorkflowBuilderAgent(provider=None).build("hello")

    assert result.workflow is None
    assert "What alarm or runbook" in result.notes[0]
