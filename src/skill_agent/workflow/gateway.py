from __future__ import annotations

import re
from typing import Any

from .events import (
    HumanDecisionEvent,
    UIAction,
    UIMessage,
    WorkflowEvent,
    WorkflowInputEvent,
    WorkflowState,
)

# Human-readable labels for the three standard decision actions.
_DECISION_LABELS: dict[str, str] = {
    "approve": "Approve",
    "reject": "Reject",
    "needs_changes": "Needs changes",
}

# Button action_id → canonical decision value.
_BUTTON_MAP: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "needs_changes": "needs_changes",
}

# Free-text patterns — order matters: more specific patterns first.
_APPROVAL_RE = re.compile(r"^(ok\s+)?approv(e|ed)\b", re.IGNORECASE)
_REJECTION_RE = re.compile(r"^reject(ed)?\b", re.IGNORECASE)
_NEEDS_CHANGES_RE = re.compile(r"needs?\s+changes?|change\b|sửa\b", re.IGNORECASE)
_NOTES_RE = re.compile(r"(?:needs?\s+changes?|sửa)[:\s]+(.*)", re.IGNORECASE | re.DOTALL)


class InteractionGateway:
    """
    Adapter between WorkflowRuntime events and the chat/UI layer.

    Responsibilities:
    - Convert WorkflowEvent → UIMessage (render_event)
    - Convert UI button action → HumanDecisionEvent (parse_ui_action)
    - Convert free-text user reply → WorkflowInputEvent (parse_user_input)

    This class contains NO business logic, NO policy decisions, and NO
    side effects.  It is a pure mapping layer.
    """

    # ── Public interface ──────────────────────────────────────────────────────

    def render_event(self, event: WorkflowEvent) -> UIMessage:
        """Render a structured workflow event into a UI-facing message."""
        handlers = {
            "human_review_requested": self._render_human_review_requested,
            "action_blocked": self._render_action_blocked,
            "workflow_completed": self._render_workflow_completed,
            "workflow_failed": self._render_workflow_failed,
            "message": self._render_message,
            "human_input_requested": self._render_human_input_requested,
            "action_executed": self._render_action_executed,
        }
        handler = handlers.get(event.type)
        if handler is None:
            return UIMessage(text=event.payload.get("text", event.type))
        return handler(event)

    def parse_ui_action(
        self,
        action_id: str,
        metadata: dict[str, Any],
        notes: str = "",
    ) -> HumanDecisionEvent:
        """
        Convert a UI button press into a HumanDecisionEvent.

        metadata must include run_id and pending_action_id.
        WorkflowRuntime is responsible for validating that the pending action
        still exists and belongs to this run.
        """
        decision = _BUTTON_MAP.get(action_id)
        if decision is None:
            raise ValueError(
                f"Unknown action_id {action_id!r}. "
                f"Expected one of: {sorted(_BUTTON_MAP)}"
            )
        return HumanDecisionEvent(
            run_id=metadata["run_id"],
            pending_action_id=metadata["pending_action_id"],
            decision=decision,  # type: ignore[arg-type]
            notes=notes,
        )

    def parse_user_input(
        self,
        user_input: str,
        current_state: WorkflowState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowInputEvent:
        """
        Convert a raw user message into a structured workflow input event.

        When current_state indicates the workflow is waiting for human review,
        the text is parsed as a potential decision (approve / reject /
        needs_changes).  Ambiguous text produces a clarification_needed event
        rather than an implicit approval.

        When the workflow is not waiting, the input is treated as normal chat.
        """
        meta = metadata or {}
        run_id = meta.get("run_id") or (current_state.run_id if current_state else "")

        if current_state is None or not current_state.waiting_for_human:
            return WorkflowInputEvent(
                type="human_input",
                run_id=run_id,
                payload={"text": user_input},
            )

        decision = _parse_decision(user_input)
        if decision is None:
            return WorkflowInputEvent(
                type="clarification_needed",
                run_id=run_id,
                payload={
                    "text": user_input,
                    "pending_action_id": current_state.pending_action_id,
                    "hint": "Reply with approve, reject, or needs changes.",
                },
            )

        return WorkflowInputEvent(
            type="human_decision",
            run_id=run_id,
            payload={
                "pending_action_id": (
                    meta.get("pending_action_id") or current_state.pending_action_id
                ),
                "decision": decision,
                "notes": _extract_notes(user_input),
            },
        )

    # ── Render helpers ────────────────────────────────────────────────────────

    def _render_human_review_requested(self, event: WorkflowEvent) -> UIMessage:
        p = event.payload
        risk_level = p.get("risk_level", "unknown")
        reasons: list[str] = p.get("reasons") or []
        # Gateway renders only what the event says — policy decides allowed_decisions.
        allowed: list[str] = p.get("allowed_decisions") or list(_BUTTON_MAP)
        summary: str = p.get("summary", "")
        title: str = p.get("title", "Review required before continuing.")

        lines = [title, "", f"Risk: {risk_level}"]
        if reasons:
            lines += ["", "Reasons:"]
            for i, reason in enumerate(reasons, 1):
                lines.append(f"{i}. {reason}")
        if summary:
            lines += ["", summary]
        lines += ["", "Available actions:"]
        for action_id in allowed:
            lines.append(f"- {_DECISION_LABELS.get(action_id, action_id)}")

        return UIMessage(
            type="review_card",
            text="\n".join(lines),
            actions=[
                UIAction(id=a, label=_DECISION_LABELS.get(a, a))
                for a in allowed
            ],
            metadata={
                "run_id": event.run_id,
                "pending_action_id": p.get("pending_action_id", ""),
            },
        )

    def _render_action_blocked(self, event: WorkflowEvent) -> UIMessage:
        p = event.payload
        reasons: list[str] = p.get("reasons") or []
        lines = ["Action blocked by policy."]
        if reasons:
            lines += ["", "Reasons:"]
            for i, reason in enumerate(reasons, 1):
                lines.append(f"{i}. {reason}")
        return UIMessage(
            type="blocked",
            text="\n".join(lines),
            actions=[],  # gateway never adds approve button for hard blocks
            metadata={"run_id": event.run_id},
        )

    def _render_workflow_completed(self, event: WorkflowEvent) -> UIMessage:
        p = event.payload
        lines = ["Workflow completed."]
        if summary := p.get("summary"):
            lines += ["", summary]
        return UIMessage(
            type="completed",
            text="\n".join(lines),
            metadata={"run_id": event.run_id},
        )

    def _render_workflow_failed(self, event: WorkflowEvent) -> UIMessage:
        p = event.payload
        lines = ["Workflow failed."]
        # Render the human-readable reason only — never expose raw tracebacks.
        if reason := p.get("reason"):
            lines += ["", "Reason:", reason]
        return UIMessage(
            type="error",
            text="\n".join(lines),
            metadata={"run_id": event.run_id},
        )

    def _render_message(self, event: WorkflowEvent) -> UIMessage:
        return UIMessage(
            type="text",
            text=event.payload.get("text", ""),
            metadata={"run_id": event.run_id},
        )

    def _render_human_input_requested(self, event: WorkflowEvent) -> UIMessage:
        return UIMessage(
            type="text",
            text=event.payload.get("prompt", "Input required."),
            metadata={"run_id": event.run_id},
        )

    def _render_action_executed(self, event: WorkflowEvent) -> UIMessage:
        p = event.payload
        text = p.get("summary") or f"Action executed: {p.get('action_type', 'unknown')}"
        return UIMessage(
            type="text",
            text=text,
            metadata={"run_id": event.run_id},
        )


# ── Free-text parsing helpers ─────────────────────────────────────────────────

def _parse_decision(text: str) -> str | None:
    """
    Map free-text to a canonical decision value.

    Returns None when the text is ambiguous — callers must not default to
    approval on None.
    """
    t = text.strip()
    if not t:
        return None
    if _APPROVAL_RE.match(t):
        return "approved"
    if _REJECTION_RE.match(t):
        return "rejected"
    if _NEEDS_CHANGES_RE.search(t):
        return "needs_changes"
    return None


def _extract_notes(text: str) -> str:
    """Extract the note portion after 'needs changes: ...' style prefixes."""
    m = _NOTES_RE.search(text.strip())
    return m.group(1).strip() if m else ""
