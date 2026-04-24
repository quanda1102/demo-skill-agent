from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Workflow → Gateway
# ---------------------------------------------------------------------------

WorkflowEventType = Literal[
    "message",
    "human_review_requested",
    "human_input_requested",
    "action_executed",
    "action_blocked",
    "workflow_completed",
    "workflow_failed",
]


class WorkflowEvent(BaseModel):
    type: WorkflowEventType
    run_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Gateway → UI
# ---------------------------------------------------------------------------

UIMessageType = Literal["text", "review_card", "blocked", "completed", "error"]


class UIAction(BaseModel):
    id: str
    label: str
    kind: str = "button"


class UIMessage(BaseModel):
    role: str = "assistant"
    type: UIMessageType = "text"
    text: str
    actions: list[UIAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# UI / user → Gateway → Workflow
# ---------------------------------------------------------------------------

class WorkflowInputEvent(BaseModel):
    type: str
    run_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class HumanDecisionEvent(BaseModel):
    type: Literal["human_decision"] = "human_decision"
    run_id: str
    pending_action_id: str
    decision: Literal["approved", "rejected", "needs_changes"]
    notes: str = ""


# ---------------------------------------------------------------------------
# Workflow pause state (persisted when waiting for human)
# ---------------------------------------------------------------------------

class WorkflowState(BaseModel):
    run_id: str
    # Possible values: running | waiting_for_human | completed | failed
    current: str = "running"
    pending_action_id: str = ""
    proposed_action: str = ""
    allowed_decisions: list[str] = Field(
        default_factory=lambda: ["approve", "reject", "needs_changes"]
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    content_hash: str = ""

    @property
    def waiting_for_human(self) -> bool:
        return self.current == "waiting_for_human"
