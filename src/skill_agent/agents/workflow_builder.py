from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from src.skill_agent.agent import AgentLoop, Tool
from src.skill_agent.agents.node_builder import NodeBuilderAgent
from src.skill_agent.engine.models import Workflow
from src.skill_agent.engine.registry import get_registry_manifest
from src.skill_agent.providers.provider import LLMProvider, MinimaxProvider
from src.skill_agent.providers.openai_provider import OpenAIProvider


@dataclass
class WorkflowBuildResult:
    workflow: Workflow | None
    notes: list[str]
    trace: list[dict[str, Any]] = field(default_factory=list)


class WorkflowBuilderAgent:
    """
    ReAct-style workflow builder.

    With a provider, the model must inspect the registry, optionally build
    missing nodes, then submit workflow JSON. The submit tool is the only exit
    path and enforces the Pydantic Workflow schema.

    Without a provider, deterministic runbook workflows remain as a local
    fallback so the demo can run without API credentials.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        node_builder: NodeBuilderAgent | None = None,
    ) -> None:
        self.provider = provider
        self.node_builder = node_builder or NodeBuilderAgent(provider=provider)
        self.registry_manifest = get_registry_manifest()
        self._last_workflow: Workflow | None = None
        self._last_notes: list[str] = []
        self._trace: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls) -> WorkflowBuilderAgent:
        provider_config = os.environ.get("LLM_PROVIDER", "").strip().lower()

        if provider_config == "openai":
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError(
                    "LLM_PROVIDER=openai but OPENAI_API_KEY is not set. "
                    "Add it to your .env file."
                )
            provider = OpenAIProvider(temperature=0.1, top_p=0.9, max_tokens=2400)
            node_builder = NodeBuilderAgent(provider=provider)
            return cls(provider=provider, node_builder=node_builder)

        # Default: MiniMax
        if not os.environ.get("MINIMAX_ENDPOINT"):
            return cls(provider=None)
        provider = MinimaxProvider(temperature=0.1, top_p=0.9, max_tokens=2400)
        node_builder = NodeBuilderAgent(provider=provider)
        return cls(provider=provider, node_builder=node_builder)

    def build(self, user_input: str) -> WorkflowBuildResult:
        self.registry_manifest = get_registry_manifest()
        self._last_workflow = None
        self._last_notes = []
        self._trace = []

        if self.provider is None:
            return self._fallback_build(user_input)

        loop = AgentLoop(
            provider=self.provider,
            tools=self._tools(),
            should_stop=self._should_stop_after_tool,
            on_event=lambda event: self._trace.append({"type": event.type, **event.payload}),
        )
        result = loop.run_turn(
            [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_input},
            ]
        )
        if self._last_workflow is None:
            return WorkflowBuildResult(
                workflow=None,
                notes=[
                    result.content
                    or "I need a concrete runbook or monitoring request before I can build a workflow."
                ],
                trace=self._trace,
            )
        return WorkflowBuildResult(
            workflow=self._last_workflow,
            notes=self._last_notes,
            trace=self._trace,
        )

    def _tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_registry_manifest",
                description="Return all currently available node types and their params/output schemas.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                fn=self._get_registry_manifest_tool,
            ),
            Tool(
                name="build_missing_node",
                description="Ask Node Builder Agent to create and register a missing node type.",
                parameters={
                    "type": "object",
                    "required": ["node_type", "description", "params_schema", "output_schema"],
                    "properties": {
                        "node_type": {"type": "string"},
                        "description": {"type": "string"},
                        "params_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    },
                },
                fn=self._build_missing_node_tool,
            ),
            Tool(
                name="submit_workflow",
                description="Submit the final workflow JSON. This validates with the engine Pydantic schema.",
                parameters={
                    "type": "object",
                    "required": ["notes"],
                    "properties": {
                        "workflow": {"type": "object"},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                },
                fn=self._submit_workflow_tool,
            ),
        ]

    def _system_prompt(self) -> str:
        return f"""
Bạn là Workflow Builder Agent cho hệ thống workflow runbook vận hành viễn thông.
Ưu tiên giao tiếp bằng tiếng Việt rõ ràng, ngắn gọn, dùng thuật ngữ NOC/BO/ứng dụng khi phù hợp.

ReAct rules:
1. Luôn gọi get_registry_manifest trước để biết hệ thống đang có node nào.
2. Dịch yêu cầu tiếng Việt của user thành workflow JSON tuần tự.
3. Ưu tiên dùng node có sẵn trong registry.
4. Nếu thiếu node bắt buộc, gọi build_missing_node với mô tả rõ và schema.
5. Chỉ kết thúc bằng submit_workflow khi đã có workflow object hoàn chỉnh.
6. Nếu user chỉ chào hỏi hoặc chưa yêu cầu workflow/runbook, không submit_workflow; hãy hỏi lại bằng tiếng Việt: cần cảnh báo/runbook nào.

Workflow schema:
{Workflow.model_json_schema()}

Registry snapshot at startup:
{json.dumps(self.registry_manifest, ensure_ascii=False, indent=2)}

Constraints:
- Node ids must be unique: n1, n2, n3...
- Edges must reference existing node ids.
- No visual position fields.
- credential_ref should be null unless explicitly needed.
- For demo, prefer a simple linear graph.
- Use builtin condition node when the runbook contains if/else, "nếu", "trường hợp", or branching logic.
- Branching is expressed on edges with `when`.
- For condition edges, use string `when` to match condition output `branch`, e.g. `{{"from":"n2","to":"n3","when":"alert"}}`.
- You can also use boolean `when` to match condition output `matched`, e.g. `{{"from":"n2","to":"n3","when":true}}`.
- Nodes on unselected branches are skipped by the engine.
- If submit_workflow returns validation_error, fix the JSON and submit again.
- Do not call submit_workflow unless you have a complete workflow object.
- Notes trả về cho user phải bằng tiếng Việt.
""".strip()

    def _get_registry_manifest_tool(self) -> str:
        self.registry_manifest = get_registry_manifest()
        return json.dumps(self.registry_manifest, ensure_ascii=False, indent=2)

    def _build_missing_node_tool(
        self,
        node_type: str,
        description: str,
        params_schema: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str:
        result = self.node_builder.build(
            node_type,
            description,
            params_schema=params_schema,
            output_schema=output_schema,
        )
        self.registry_manifest = get_registry_manifest()
        payload = {
            "status": "registered" if result.ok else "failed",
            "node_type": node_type,
            "detail": result.message,
            "registry": self.registry_manifest,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _submit_workflow_tool(
        self,
        workflow: dict[str, Any] | None = None,
        notes: list[str] | None = None,
    ) -> str:
        if workflow is None:
            return json.dumps(
                {
                    "status": "clarification_needed",
                    "detail": (
                        "submit_workflow requires a workflow object. If the user only greeted you "
                        "or did not request a runbook, answer normally without calling submit_workflow."
                    ),
                    "notes": notes or [],
                },
                ensure_ascii=False,
            )
        try:
            parsed = Workflow.model_validate(workflow)
        except ValidationError as exc:
            return json.dumps(
                {
                    "status": "validation_error",
                    "detail": exc.errors(),
                    "schema": Workflow.model_json_schema(),
                },
                ensure_ascii=False,
            )

        missing = [node.type for node in parsed.nodes if node.type not in get_registry_manifest()]
        if missing:
            return json.dumps(
                {
                    "status": "validation_error",
                    "detail": f"Unknown node types: {sorted(set(missing))}",
                    "registry": get_registry_manifest(),
                },
                ensure_ascii=False,
            )

        self._last_workflow = parsed
        self._last_notes = notes or ["Workflow generated and validated."]
        return json.dumps(
            {
                "status": "accepted",
                "workflow_id": parsed.workflow_id,
                "detail": "Workflow accepted.",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _should_stop_after_tool(name: str, output: str) -> bool:
        if name != "submit_workflow":
            return False
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return False
        return payload.get("status") == "accepted"

    def _fallback_build(self, user_input: str) -> WorkflowBuildResult:
        normalized = user_input.lower()
        if not self._looks_like_workflow_request(normalized):
            return WorkflowBuildResult(
                workflow=None,
                notes=[
                    "Hi! What alarm or runbook workflow would you like me to build?",
                    "Supported demo prompts: `node high memory`, `node high CPU load`, or `node high CPU usage`.",
                ],
            )
        if self._is_memory_runbook(normalized):
            return self._fallback_memory_runbook(user_input)
        if self._is_cpu_load_runbook(normalized):
            return self._fallback_cpu_load_runbook(user_input)
        if self._is_cpu_usage_runbook(normalized):
            return self._fallback_cpu_usage_runbook(user_input)
        return self._fallback_signal_runbook(user_input)

    def _fallback_memory_runbook(self, user_input: str) -> WorkflowBuildResult:
        ip = self._extract_ip(user_input)
        workflow = Workflow.model_validate(
            {
                "name": "Runbook: Node high memory usage",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "check_dcim_service",
                        "label": "Check DCIM service by IP",
                        "params": {"ip": ip, "excluded_services": ["database", "cloud_compute"]},
                    },
                    {
                        "id": "n2",
                        "type": "check_metric_threshold",
                        "label": "Check available RAM",
                        "params": {
                            "metric": "available_ram_gb",
                            "operator": "<",
                            "value": 4,
                            "mock_value": 2,
                            "unit": "GB",
                        },
                    },
                    {
                        "id": "n3",
                        "type": "get_top_processes",
                        "label": "Find top RAM processes",
                        "params": {"metric": "ram", "limit": 5},
                    },
                    {
                        "id": "n4",
                        "type": "notify_owner",
                        "label": "Notify application owner",
                        "params": {
                            "owner_type": "application",
                            "severity": "critical",
                            "message": "Node high memory usage: stop/restart high RAM process or escalate system service.",
                        },
                    },
                    {
                        "id": "n5",
                        "type": "notify_owner",
                        "label": "Escalate server RAM upgrade if persistent",
                        "params": {
                            "owner_type": "server",
                            "severity": "warning",
                            "message": "If memory remains high, request RAM expansion/upgrade.",
                        },
                    },
                ],
                "edges": [
                    {"from": "n1", "to": "n2"},
                    {"from": "n2", "to": "n3"},
                    {"from": "n3", "to": "n4"},
                    {"from": "n4", "to": "n5"},
                ],
            }
        )
        return WorkflowBuildResult(
            workflow=workflow,
            notes=[
                "Demo 1: generated high-memory runbook using only built-in nodes.",
                "Covers DCIM lookup, RAM threshold, top RAM processes, owner notification, and capacity escalation.",
            ],
        )

    def _fallback_cpu_load_runbook(self, user_input: str) -> WorkflowBuildResult:
        ip = self._extract_ip(user_input)
        built_nodes: list[str] = []
        for node_type, description, params_schema, output_schema in [
            (
                "check_io_stat",
                "Parse iostat -xz output and detect disk IO bottleneck when %util > 90.",
                {"sample_output": "string", "threshold": "number"},
                {"passed": "boolean", "value": "number", "conclusion": "string"},
            ),
            (
                "check_nfs_mount",
                "Detect hung NFS/network mount by modeling df -h timeout behavior.",
                {"timeout_seconds": "number", "simulate_hang": "boolean"},
                {"passed": "boolean", "hung": "boolean", "conclusion": "string"},
            ),
        ]:
            if node_type not in get_registry_manifest():
                result = self.node_builder.build(
                    node_type,
                    description,
                    params_schema=params_schema,
                    output_schema=output_schema,
                )
                if result.ok:
                    built_nodes.append(node_type)

        workflow = Workflow.model_validate(
            {
                "name": "Runbook: Node high CPU load",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "check_dcim_service",
                        "label": "Check DCIM service by IP",
                        "params": {"ip": ip, "excluded_services": ["database", "cloud_compute"]},
                    },
                    {
                        "id": "n2",
                        "type": "check_metric_threshold",
                        "label": "Check CPU load average",
                        "params": {
                            "metric": "cpu_load_average",
                            "operator": ">",
                            "value": 12,
                            "mock_value": 24,
                            "unit": "load",
                        },
                    },
                    {
                        "id": "n3",
                        "type": "check_metric_threshold",
                        "label": "Check CPU usage",
                        "params": {
                            "metric": "cpu_usage_percent",
                            "operator": ">",
                            "value": 85,
                            "mock_value": 70,
                            "unit": "%",
                        },
                    },
                    {
                        "id": "n4",
                        "type": "check_io_stat",
                        "label": "Check IO util with iostat",
                        "params": {"threshold": 90},
                    },
                    {
                        "id": "n5",
                        "type": "check_nfs_mount",
                        "label": "Check NFS/network mount hang",
                        "params": {"timeout_seconds": 3, "simulate_hang": True},
                    },
                    {
                        "id": "n6",
                        "type": "notify_owner",
                        "label": "Notify infrastructure owner",
                        "params": {
                            "owner_type": "system",
                            "severity": "critical",
                            "message": "Node high CPU load: IO or NFS mount bottleneck detected, escalate to BO HT.",
                        },
                    },
                ],
                "edges": [
                    {"from": "n1", "to": "n2"},
                    {"from": "n2", "to": "n3"},
                    {"from": "n3", "to": "n4"},
                    {"from": "n4", "to": "n5"},
                    {"from": "n5", "to": "n6"},
                ],
            }
        )
        notes = [
            "Demo 2: generated high-CPU-load runbook.",
            "This scenario intentionally requires missing nodes: check_io_stat and check_nfs_mount.",
        ]
        if built_nodes:
            notes.append(f"Node Builder created and registered: {', '.join(built_nodes)}.")
        return WorkflowBuildResult(workflow=workflow, notes=notes)

    def _fallback_cpu_usage_runbook(self, user_input: str) -> WorkflowBuildResult:
        ip = self._extract_ip(user_input)
        workflow = Workflow.model_validate(
            {
                "name": "Runbook: Node high CPU usage",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "check_dcim_service",
                        "label": "Check DCIM service by IP",
                        "params": {"ip": ip, "excluded_services": ["database", "cloud_compute"]},
                    },
                    {
                        "id": "n2",
                        "type": "check_metric_threshold",
                        "label": "Check CPU usage",
                        "params": {
                            "metric": "cpu_usage_percent",
                            "operator": ">",
                            "value": 85,
                            "mock_value": 92,
                            "unit": "%",
                        },
                    },
                    {
                        "id": "n3",
                        "type": "get_top_processes",
                        "label": "Find top CPU processes",
                        "params": {"metric": "cpu", "limit": 5},
                    },
                    {
                        "id": "n4",
                        "type": "notify_owner",
                        "label": "Notify app or system owner",
                        "params": {
                            "owner_type": "system",
                            "severity": "critical",
                            "message": "Node high CPU usage: inspect top process owner and escalate application/root process.",
                        },
                    },
                ],
                "edges": [
                    {"from": "n1", "to": "n2"},
                    {"from": "n2", "to": "n3"},
                    {"from": "n3", "to": "n4"},
                ],
            }
        )
        return WorkflowBuildResult(
            workflow=workflow,
            notes=[
                "Backup demo: generated high-CPU-usage runbook using existing built-ins.",
                "This overlaps with memory and CPU-load demos, so it is lower demo value.",
            ],
        )

    def _fallback_signal_runbook(self, user_input: str) -> WorkflowBuildResult:
        station_id = self._extract_station_id(user_input)
        threshold = self._extract_threshold(user_input)
        duration = self._extract_duration(user_input)
        metric = "RSSI"

        workflow = Workflow.model_validate(
            {
                "name": f"Monitor {station_id} {metric}",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "fetch_signal",
                        "label": f"Fetch {metric} from {station_id}",
                        "params": {
                            "station_id": station_id,
                            "metric": metric,
                            "simulate_drop": True,
                        },
                    },
                    {
                        "id": "n2",
                        "type": "threshold_check",
                        "label": f"Check {metric} < {threshold} dBm",
                        "params": {
                            "operator": "<",
                            "value": threshold,
                            "field": "value",
                            "unit": "dBm",
                        },
                    },
                    {
                        "id": "n3",
                        "type": "time_window",
                        "label": f"Confirm for {duration}s",
                        "params": {
                            "duration_seconds": duration,
                            "condition_field": "passed",
                        },
                    },
                    {
                        "id": "n4",
                        "type": "send_alert",
                        "label": "Send alert",
                        "params": {
                            "message": f"{station_id} {metric} dropped below {threshold} dBm",
                            "severity": "critical",
                            "condition_field": "satisfied",
                        },
                    },
                ],
                "edges": [
                    {"from": "n1", "to": "n2"},
                    {"from": "n2", "to": "n3"},
                    {"from": "n3", "to": "n4"},
                ],
            }
        )
        return WorkflowBuildResult(
            workflow=workflow,
            notes=[
                "Generated with deterministic fallback because no LLM provider is configured.",
                "No missing registry nodes were required.",
            ],
        )

    @staticmethod
    def _is_memory_runbook(text: str) -> bool:
        return "memory" in text or "ram" in text

    @staticmethod
    def _is_cpu_load_runbook(text: str) -> bool:
        return "cpu load" in text or "load average" in text or "cao tải cpu load" in text

    @staticmethod
    def _is_cpu_usage_runbook(text: str) -> bool:
        return "cpu usage" in text or "cpu sử dụng" in text or "cao cpu" in text

    @staticmethod
    def _looks_like_workflow_request(text: str) -> bool:
        keywords = [
            "workflow",
            "runbook",
            "alert",
            "alarm",
            "cảnh báo",
            "xu ly",
            "xử lý",
            "monitor",
            "node",
            "memory",
            "ram",
            "cpu",
            "rssi",
            "bts",
        ]
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _extract_ip(text: str) -> str:
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        return match.group(0) if match else "10.0.12.34"

    @staticmethod
    def _extract_station_id(text: str) -> str:
        match = re.search(r"\bBTS[_-]?\d+\b", text, flags=re.IGNORECASE)
        if not match:
            return "BTS_042"
        raw = match.group(0).upper().replace("-", "_")
        if "_" not in raw:
            raw = raw.replace("BTS", "BTS_")
        return raw

    @staticmethod
    def _extract_threshold(text: str) -> float:
        match = re.search(r"(-\d+(?:\.\d+)?)\s*(?:dbm|dBm)?", text)
        return float(match.group(1)) if match else -90.0

    @staticmethod
    def _extract_duration(text: str) -> int:
        match = re.search(r"(\d+)\s*(?:s|sec|second|seconds|giây)", text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 30
