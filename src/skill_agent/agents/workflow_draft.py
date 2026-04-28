from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.skill_agent.engine.models import Edge, Node, Workflow, utc_now
from src.skill_agent.engine.registry import get_node, get_registry_manifest
from src.skill_agent.engine.runner import run_node_script


@dataclass
class DraftNodeVerification:
    node_id: str
    node_type: str
    ok: bool
    output: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class WorkflowDraft:
    workflow: Workflow
    current_output: dict[str, Any] = field(default_factory=dict)
    verifications: list[DraftNodeVerification] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)
    status: str = "draft"


@dataclass
class DraftUpdateResult:
    message: str
    draft: WorkflowDraft | None
    workflow: Workflow | None = None
    changed: bool = False
    ready: bool = False


class WorkflowDraftManager:
    """
    Stateful workflow JSON builder.

    It owns the draft workflow and verifies every appended node with the same
    node runner used by the execution engine. The first implementation is
    deterministic for the demo runbooks; LLM-driven patching can sit on top of
    this state model later.
    """

    def __init__(self) -> None:
        self.current: WorkflowDraft | None = None

    def reset(self) -> None:
        self.current = None

    def handle(self, user_input: str) -> DraftUpdateResult:
        text = user_input.strip()
        normalized = text.lower()

        if self.current is None:
            if self._wants_memory_runbook(normalized):
                return self._start_memory_runbook(text)
            if self._wants_cpu_load_runbook(normalized):
                return self._start_cpu_load_runbook(text)
            if self._wants_signal_runbook(normalized):
                return self._start_signal_runbook(text)
            return DraftUpdateResult(
                message=(
                    "Mình cần bạn mô tả cảnh báo/runbook muốn tạo workflow trước. "
                    "Demo hiện hỗ trợ: `node high memory`, `node high CPU load`, hoặc `RSSI signal alert`."
                ),
                draft=None,
            )

        if self._is_cancel(normalized):
            self.reset()
            return DraftUpdateResult(message="Đã xoá bản nháp workflow.", draft=None, changed=True)

        updated = self._update_slots(self.current, text)
        if self._is_confirm(normalized):
            self.current.status = "ready"
            return DraftUpdateResult(
                message="Đã xác nhận bản nháp workflow và bắt đầu chạy.",
                draft=self.current,
                workflow=self.current.workflow,
                changed=True,
                ready=True,
            )

        if updated:
            return DraftUpdateResult(
                message=self._draft_status_message(self.current),
                draft=self.current,
                workflow=self.current.workflow,
                changed=True,
            )

        return DraftUpdateResult(
            message=(
                "Đang có một bản nháp workflow. Bạn có thể bổ sung thông tin hoặc gõ `confirm` để chạy. "
                f"Bản nháp hiện tại: `{self.current.workflow.name}`."
            ),
            draft=self.current,
            workflow=self.current.workflow,
        )

    def _start_memory_runbook(self, text: str) -> DraftUpdateResult:
        workflow = self._new_workflow("Draft: Node high memory usage")
        self.current = WorkflowDraft(workflow=workflow, slots={"runbook": "high_memory"})
        self._update_slots(self.current, text)

        self._append_verified_node(
            self.current,
            "check_dcim_service",
            "Check DCIM service by IP",
            {
                "ip": self.current.slots.get("ip", "10.0.12.34"),
                "excluded_services": ["database", "cloud_compute"],
                "mock_service_type": self.current.slots.get("service_type", "web_app"),
            },
        )
        self._append_verified_node(
            self.current,
            "check_metric_threshold",
            "Check available RAM",
            {
                "metric": "available_ram_gb",
                "operator": "<",
                "value": self.current.slots.get("ram_threshold_gb", 4),
                "mock_value": self.current.slots.get("available_ram_gb", 2),
                "unit": "GB",
            },
        )
        self._append_verified_node(
            self.current,
            "get_top_processes",
            "Find top RAM processes",
            {"metric": "ram", "limit": 5},
        )

        if not self.current.slots.get("skip_notify"):
            self._append_verified_node(
                self.current,
                "notify_owner",
                "Notify application owner",
                {
                    "owner_type": "application",
                    "severity": "critical",
                    "message": "Node high memory usage: stop/restart high RAM process or escalate system service.",
                },
            )
            self._append_verified_node(
                self.current,
                "notify_owner",
                "Escalate server RAM upgrade if persistent",
                {
                    "owner_type": "server",
                    "severity": "warning",
                    "message": "If memory remains high, request RAM expansion/upgrade.",
                },
            )

        return DraftUpdateResult(
            message=self._draft_status_message(self.current),
            draft=self.current,
            workflow=self.current.workflow,
            changed=True,
        )

    def _start_cpu_load_runbook(self, text: str) -> DraftUpdateResult:
        workflow = self._new_workflow("Draft: Node high CPU load")
        self.current = WorkflowDraft(workflow=workflow, slots={"runbook": "high_cpu_load"})
        self._update_slots(self.current, text)
        for node_type, label, params in [
            (
                "check_dcim_service",
                "Check DCIM service by IP",
                {"ip": self.current.slots.get("ip", "10.0.12.34"), "excluded_services": ["database", "cloud_compute"]},
            ),
            (
                "check_metric_threshold",
                "Check CPU load average",
                {"metric": "cpu_load_average", "operator": ">", "value": 12, "mock_value": 24, "unit": "load"},
            ),
            (
                "check_metric_threshold",
                "Check CPU usage",
                {"metric": "cpu_usage_percent", "operator": ">", "value": 85, "mock_value": 70, "unit": "%"},
            ),
        ]:
            self._append_verified_node(self.current, node_type, label, params)
        return DraftUpdateResult(
            message=self._draft_status_message(self.current),
            draft=self.current,
            workflow=self.current.workflow,
            changed=True,
        )

    def _start_signal_runbook(self, text: str) -> DraftUpdateResult:
        workflow = self._new_workflow("Draft: RSSI signal alert")
        self.current = WorkflowDraft(workflow=workflow, slots={"runbook": "rssi_signal"})
        self._update_slots(self.current, text)
        station = self.current.slots.get("station_id", "BTS_042")
        threshold = self.current.slots.get("rssi_threshold", -95)
        for node_type, label, params in [
            (
                "fetch_signal",
                f"Fetch RSSI from {station}",
                {"station_id": station, "metric": "RSSI", "simulate_drop": True},
            ),
            (
                "threshold_check",
                f"Check RSSI < {threshold} dBm",
                {"operator": "<", "value": threshold, "field": "value", "unit": "dBm"},
            ),
            (
                "send_alert",
                "Notify owner",
                {"message": f"{station} RSSI below {threshold} dBm", "severity": "critical", "condition_field": "passed"},
            ),
        ]:
            self._append_verified_node(self.current, node_type, label, params)
        return DraftUpdateResult(
            message=self._draft_status_message(self.current),
            draft=self.current,
            workflow=self.current.workflow,
            changed=True,
        )

    def _append_verified_node(
        self,
        draft: WorkflowDraft,
        node_type: str,
        label: str,
        params: dict[str, Any],
    ) -> None:
        get_node(node_type)
        node_id = f"n{len(draft.workflow.nodes) + 1}"
        node = Node(id=node_id, type=node_type, label=label, params=params)
        nodes = [*draft.workflow.nodes, node]
        edges = [*draft.workflow.edges]
        if len(nodes) > 1:
            edges.append(Edge(from_node=nodes[-2].id, to_node=node_id))
        draft.workflow = draft.workflow.model_copy(update={"nodes": nodes, "edges": edges})

        try:
            entry = get_node(node_type)
            output = run_node_script(entry.path, params, draft.current_output, node.credential_ref)
            draft.current_output = output
            draft.verifications.append(
                DraftNodeVerification(node_id=node_id, node_type=node_type, ok=True, output=output)
            )
        except Exception as exc:
            draft.verifications.append(
                DraftNodeVerification(node_id=node_id, node_type=node_type, ok=False, error=str(exc))
            )

    @staticmethod
    def _new_workflow(name: str) -> Workflow:
        return Workflow(
            name=name,
            nodes=[
                Node(
                    id="draft_start",
                    type="check_metric_threshold",
                    label="Draft start marker",
                    params={
                        "metric": "draft_start",
                        "operator": ">=",
                        "value": 0,
                        "mock_value": 0,
                    },
                )
            ],
            edges=[],
            created_at=utc_now(),
        ).model_copy(update={"nodes": []})

    def _update_slots(self, draft: WorkflowDraft, text: str) -> bool:
        before = dict(draft.slots)
        if ip := self._extract_ip(text):
            draft.slots["ip"] = ip
        elif match := re.search(r"\bip\s*=\s*([A-Za-z0-9_.:-]+)", text, flags=re.IGNORECASE):
            draft.slots["ip"] = match.group(1)

        if "windows" in text.lower():
            draft.slots["os"] = "windows"
        if "linux" in text.lower():
            draft.slots["os"] = "linux"
        if "không cần" in text.lower() or "khong can" in text.lower() or "no notify" in text.lower():
            draft.slots["skip_notify"] = True
        if station := self._extract_station_id(text):
            draft.slots["station_id"] = station
        if threshold := self._extract_rssi_threshold(text):
            draft.slots["rssi_threshold"] = threshold
        return before != draft.slots

    def _draft_status_message(self, draft: WorkflowDraft) -> str:
        verified = sum(1 for item in draft.verifications if item.ok)
        failed = [item for item in draft.verifications if not item.ok]
        lines = [
            f"Đã cập nhật bản nháp workflow: `{draft.workflow.name}`.",
            f"Số bước: {len(draft.workflow.nodes)}. Bước đã kiểm thử: {verified}.",
            "Gõ `confirm` để chạy workflow, hoặc tiếp tục bổ sung thông tin để cập nhật bản nháp.",
        ]
        if failed:
            lines.append(f"Bước kiểm thử lỗi: {[item.node_id for item in failed]}")
        return "\n".join(lines)

    @staticmethod
    def _wants_memory_runbook(text: str) -> bool:
        return "memory" in text or "ram" in text

    @staticmethod
    def _wants_cpu_load_runbook(text: str) -> bool:
        return "cpu load" in text or "load average" in text

    @staticmethod
    def _wants_signal_runbook(text: str) -> bool:
        return "rssi" in text or "bts" in text or "signal" in text

    @staticmethod
    def _is_confirm(text: str) -> bool:
        return text.strip().lower() in {"confirm", "ok", "execute", "run", "chạy", "xac nhan", "xác nhận"}

    @staticmethod
    def _is_cancel(text: str) -> bool:
        return text.strip().lower() in {"cancel", "clear", "reset", "huỷ", "hủy"}

    @staticmethod
    def _extract_ip(text: str) -> str | None:
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        return match.group(0) if match else None

    @staticmethod
    def _extract_station_id(text: str) -> str | None:
        match = re.search(r"\bBTS[_-]?\d+\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        raw = match.group(0).upper().replace("-", "_")
        if "_" not in raw:
            raw = raw.replace("BTS", "BTS_")
        return raw

    @staticmethod
    def _extract_rssi_threshold(text: str) -> float | None:
        if "rssi" not in text.lower():
            return None
        match = re.search(r"(-\d+(?:\.\d+)?)\s*(?:dbm|dBm)?", text)
        return float(match.group(1)) if match else None
