from __future__ import annotations

from dataclasses import dataclass, field

from .capability import check_capability
from .models import (
    CapabilityStatus,
    ExecutionStatus,
    RuntimeLog,
    SelectionConfig,
    SelectionStatus,
    SkillStub,
    TaskStatus,
)
from .selector import select_skill


@dataclass
class PolicyConfig:
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    # Actions that require explicit user confirmation before execution
    require_confirmation_for: list[str] = field(
        default_factory=lambda: ["delete", "overwrite", "network"]
    )


@dataclass
class PolicyDecision:
    selection_status: SelectionStatus
    capability_status: CapabilityStatus
    execution_status: ExecutionStatus
    task_status: TaskStatus
    reason: str = ""
    selected_stub: SkillStub | None = None
    logs: list[RuntimeLog] = field(default_factory=list)

    @property
    def execution_allowed(self) -> bool:
        return self.execution_status == ExecutionStatus.allowed


class PolicyEngine:
    """Applies policy v1 checks in order: selection → capability → execution gate."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def evaluate(
        self,
        stubs: list[SkillStub],
        user_request: str,
        requested_action: str = "",
    ) -> PolicyDecision:
        logs: list[RuntimeLog] = []

        # ── Layer 1: Selection ────────────────────────────────────────────────
        stub, sel_status, sel_logs = select_skill(stubs, user_request, self.config.selection, requested_action)
        logs.extend(sel_logs)

        if sel_status == SelectionStatus.no_match:
            return PolicyDecision(
                selection_status=sel_status,
                capability_status=CapabilityStatus.unknown_capability,
                execution_status=ExecutionStatus.skipped,
                task_status=TaskStatus.not_applicable,
                reason="No skill matched the request",
                selected_stub=None,
                logs=logs,
            )

        if sel_status in (SelectionStatus.low_confidence, SelectionStatus.ambiguous):
            return PolicyDecision(
                selection_status=sel_status,
                capability_status=CapabilityStatus.unknown_capability,
                execution_status=ExecutionStatus.skipped,
                task_status=TaskStatus.not_applicable,
                reason=(
                    "Match confidence too low — ask user for clarification"
                    if sel_status == SelectionStatus.low_confidence
                    else "Ambiguous match — ask user for clarification"
                ),
                selected_stub=stub,
                logs=logs,
            )

        assert stub is not None  # implied by SelectionStatus.matched

        # ── Layer 2: Capability ───────────────────────────────────────────────
        if requested_action:
            cap_status, cap_logs = check_capability(stub, requested_action)
            logs.extend(cap_logs)

            if cap_status == CapabilityStatus.unsupported_operation:
                return PolicyDecision(
                    selection_status=sel_status,
                    capability_status=cap_status,
                    execution_status=ExecutionStatus.denied,
                    task_status=TaskStatus.unsupported,
                    reason=f"Action '{requested_action}' is not supported by '{stub.skill_id}'",
                    selected_stub=stub,
                    logs=logs,
                )
        else:
            cap_status = (
                CapabilityStatus.supported
                if stub.supported_actions
                else CapabilityStatus.unknown_capability
            )

        # ── Layer 3: Execution gate ───────────────────────────────────────────
        confirm_actions = [a.lower() for a in self.config.require_confirmation_for]
        if requested_action and requested_action.lower() in confirm_actions:
            logs.append(
                RuntimeLog(
                    "warning",
                    "execution",
                    f"Action '{requested_action}' requires user confirmation before execution",
                )
            )
            return PolicyDecision(
                selection_status=sel_status,
                capability_status=cap_status,
                execution_status=ExecutionStatus.denied,
                task_status=TaskStatus.not_applicable,
                reason=f"Action '{requested_action}' requires explicit user confirmation",
                selected_stub=stub,
                logs=logs,
            )

        logs.append(RuntimeLog("info", "execution", f"Policy checks passed for '{stub.skill_id}'"))
        return PolicyDecision(
            selection_status=sel_status,
            capability_status=cap_status,
            execution_status=ExecutionStatus.allowed,
            task_status=TaskStatus.unknown,
            reason="All policy checks passed — execution allowed",
            selected_stub=stub,
            logs=logs,
        )
