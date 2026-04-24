"""Tests for the InteractionGateway — render, parse, metadata, and boundary."""
from __future__ import annotations

import pytest

from src.skill_agent.workflow.events import (
    HumanDecisionEvent,
    UIMessage,
    WorkflowEvent,
    WorkflowInputEvent,
    WorkflowState,
)
from src.skill_agent.workflow.gateway import InteractionGateway

_GW = InteractionGateway()


# ── Fixtures / builders ───────────────────────────────────────────────────────

def _review_event(
    run_id: str = "run_1",
    pending_action_id: str = "pa_1",
    risk_level: str = "medium",
    reasons: list[str] | None = None,
    allowed_decisions: list[str] | None = None,
    summary: str = "Skill will write to disk.",
) -> WorkflowEvent:
    return WorkflowEvent(
        type="human_review_requested",
        run_id=run_id,
        payload={
            "pending_action_id": pending_action_id,
            "title": "Review required",
            "risk_level": risk_level,
            "reasons": reasons if reasons is not None else ["Side effect: file_write"],
            "allowed_decisions": (
                allowed_decisions if allowed_decisions is not None
                else ["approve", "reject", "needs_changes"]
            ),
            "summary": summary,
        },
    )


def _waiting(run_id: str = "run_1", pending_action_id: str = "pa_1") -> WorkflowState:
    return WorkflowState(
        run_id=run_id,
        current="waiting_for_human",
        pending_action_id=pending_action_id,
    )


def _meta(run_id: str = "run_1", pending_action_id: str = "pa_1") -> dict:
    return {"run_id": run_id, "pending_action_id": pending_action_id}


# ── Render: human_review_requested ───────────────────────────────────────────

class TestRenderHumanReview:
    def test_type_is_review_card(self):
        assert _GW.render_event(_review_event()).type == "review_card"

    def test_text_contains_risk_level(self):
        msg = _GW.render_event(_review_event(risk_level="high"))
        assert "high" in msg.text.lower()

    def test_text_contains_each_reason(self):
        msg = _GW.render_event(_review_event(reasons=["dangerous operation", "file deleted"]))
        assert "dangerous operation" in msg.text
        assert "file deleted" in msg.text

    def test_actions_match_allowed_decisions_exactly(self):
        msg = _GW.render_event(_review_event(allowed_decisions=["approve", "reject"]))
        ids = {a.id for a in msg.actions}
        assert ids == {"approve", "reject"}

    def test_needs_changes_absent_when_not_allowed(self):
        msg = _GW.render_event(_review_event(allowed_decisions=["approve", "reject"]))
        assert not any(a.id == "needs_changes" for a in msg.actions)

    def test_default_allowed_decisions_produces_all_three_actions(self):
        msg = _GW.render_event(_review_event())
        ids = {a.id for a in msg.actions}
        assert ids == {"approve", "reject", "needs_changes"}

    def test_metadata_includes_run_id(self):
        msg = _GW.render_event(_review_event(run_id="run_42"))
        assert msg.metadata["run_id"] == "run_42"

    def test_metadata_includes_pending_action_id(self):
        msg = _GW.render_event(_review_event(pending_action_id="pa_99"))
        assert msg.metadata["pending_action_id"] == "pa_99"

    def test_summary_appears_in_text(self):
        msg = _GW.render_event(_review_event(summary="Publishes word-counter v0.1.0"))
        assert "Publishes word-counter" in msg.text

    def test_role_is_assistant(self):
        assert _GW.render_event(_review_event()).role == "assistant"


# ── Render: action_blocked ────────────────────────────────────────────────────

class TestRenderActionBlocked:
    def _blocked(self, reasons: list[str] | None = None) -> WorkflowEvent:
        return WorkflowEvent(
            type="action_blocked",
            run_id="r1",
            payload={"reasons": reasons or ["policy rule X"]},
        )

    def test_type_is_blocked(self):
        assert _GW.render_event(self._blocked()).type == "blocked"

    def test_text_contains_reasons(self):
        msg = _GW.render_event(self._blocked(["forbidden side effect"]))
        assert "forbidden side effect" in msg.text

    def test_no_approve_button_for_hard_block(self):
        msg = _GW.render_event(self._blocked())
        assert not any(a.id == "approve" for a in msg.actions)

    def test_no_actions_at_all(self):
        msg = _GW.render_event(self._blocked())
        assert msg.actions == []

    def test_text_mentions_blocked(self):
        msg = _GW.render_event(self._blocked())
        assert "blocked" in msg.text.lower()


# ── Render: workflow_completed ────────────────────────────────────────────────

class TestRenderWorkflowCompleted:
    def _completed(self, summary: str = "") -> WorkflowEvent:
        p = {"summary": summary} if summary else {}
        return WorkflowEvent(type="workflow_completed", run_id="r1", payload=p)

    def test_type_is_completed(self):
        assert _GW.render_event(self._completed()).type == "completed"

    def test_text_mentions_completed(self):
        assert "completed" in _GW.render_event(self._completed()).text.lower()

    def test_optional_summary_included(self):
        msg = _GW.render_event(self._completed(summary="Published skill word-counter v0.1.0"))
        assert "word-counter" in msg.text


# ── Render: workflow_failed ───────────────────────────────────────────────────

class TestRenderWorkflowFailed:
    def _failed(self, reason: str = "", traceback: str = "") -> WorkflowEvent:
        p: dict = {}
        if reason:
            p["reason"] = reason
        if traceback:
            p["traceback"] = traceback
        return WorkflowEvent(type="workflow_failed", run_id="r1", payload=p)

    def test_type_is_error(self):
        assert _GW.render_event(self._failed()).type == "error"

    def test_text_mentions_failed(self):
        assert "failed" in _GW.render_event(self._failed()).text.lower()

    def test_reason_appears_in_text(self):
        msg = _GW.render_event(self._failed(reason="Validation error: description too short"))
        assert "Validation error" in msg.text

    def test_raw_traceback_not_in_output(self):
        msg = _GW.render_event(
            self._failed(
                reason="Internal error",
                traceback="Traceback (most recent call last):\n  line 42\nRuntimeError",
            )
        )
        assert "Traceback" not in msg.text
        assert "most recent call last" not in msg.text


# ── Parse UI action ───────────────────────────────────────────────────────────

class TestParseUiAction:
    def test_approve_button_returns_human_decision_event(self):
        result = _GW.parse_ui_action("approve", _meta())
        assert isinstance(result, HumanDecisionEvent)

    def test_approve_maps_to_approved(self):
        assert _GW.parse_ui_action("approve", _meta()).decision == "approved"

    def test_reject_maps_to_rejected(self):
        assert _GW.parse_ui_action("reject", _meta()).decision == "rejected"

    def test_needs_changes_maps_to_needs_changes(self):
        assert _GW.parse_ui_action("needs_changes", _meta()).decision == "needs_changes"

    def test_preserves_run_id(self):
        result = _GW.parse_ui_action("approve", _meta(run_id="run_77"))
        assert result.run_id == "run_77"

    def test_preserves_pending_action_id(self):
        result = _GW.parse_ui_action("approve", _meta(pending_action_id="pa_55"))
        assert result.pending_action_id == "pa_55"

    def test_notes_passed_through(self):
        result = _GW.parse_ui_action("approve", _meta(), notes="Manually verified output")
        assert result.notes == "Manually verified output"

    def test_unknown_action_id_raises(self):
        with pytest.raises(ValueError, match="Unknown action_id"):
            _GW.parse_ui_action("delete", _meta())

    def test_event_type_is_human_decision(self):
        assert _GW.parse_ui_action("approve", _meta()).type == "human_decision"


# ── Parse free-text while waiting ────────────────────────────────────────────

class TestParseUserInputWhileWaiting:
    _state = _waiting()

    def test_approve_becomes_approved(self):
        r = _GW.parse_user_input("approve", self._state)
        assert r.type == "human_decision"
        assert r.payload["decision"] == "approved"

    def test_approved_becomes_approved(self):
        r = _GW.parse_user_input("approved", self._state)
        assert r.payload["decision"] == "approved"

    def test_ok_approve_becomes_approved(self):
        r = _GW.parse_user_input("ok approve", self._state)
        assert r.payload["decision"] == "approved"

    def test_reject_becomes_rejected(self):
        r = _GW.parse_user_input("reject", self._state)
        assert r.payload["decision"] == "rejected"

    def test_rejected_becomes_rejected(self):
        r = _GW.parse_user_input("rejected", self._state)
        assert r.payload["decision"] == "rejected"

    def test_needs_changes_colon_prefix(self):
        r = _GW.parse_user_input("needs changes: limit file path", self._state)
        assert r.payload["decision"] == "needs_changes"

    def test_needs_changes_captures_notes(self):
        r = _GW.parse_user_input("needs changes: limit file path", self._state)
        assert "limit file path" in r.payload.get("notes", "")

    def test_sửa_maps_to_needs_changes(self):
        r = _GW.parse_user_input("sửa lại cho đúng", self._state)
        assert r.payload["decision"] == "needs_changes"

    def test_ambiguous_text_is_clarification(self):
        r = _GW.parse_user_input("hmm not sure about this", self._state)
        assert r.type == "clarification_needed"

    def test_ambiguous_text_is_not_approved(self):
        r = _GW.parse_user_input("hmm not sure about this", self._state)
        assert r.payload.get("decision") != "approved"

    def test_empty_text_is_clarification_not_approved(self):
        r = _GW.parse_user_input("", self._state)
        assert r.type == "clarification_needed"

    def test_pending_action_id_preserved_in_payload(self):
        state = _waiting(pending_action_id="pa_88")
        r = _GW.parse_user_input("approve", state)
        assert r.payload["pending_action_id"] == "pa_88"

    def test_run_id_taken_from_state(self):
        state = _waiting(run_id="run_55")
        r = _GW.parse_user_input("approve", state)
        assert r.run_id == "run_55"

    def test_clarification_includes_hint(self):
        r = _GW.parse_user_input("maybe", self._state)
        assert "hint" in r.payload


# ── Parse free-text when NOT waiting ─────────────────────────────────────────

class TestParseUserInputNotWaiting:
    def test_approve_without_state_is_normal_input(self):
        r = _GW.parse_user_input("approve")
        assert r.type == "human_input"

    def test_approve_in_running_state_is_normal_input(self):
        state = WorkflowState(run_id="r1", current="running")
        r = _GW.parse_user_input("approve", state)
        assert r.type == "human_input"

    def test_normal_chat_is_human_input(self):
        r = _GW.parse_user_input("what skills are available?")
        assert r.type == "human_input"

    def test_run_id_from_state(self):
        state = WorkflowState(run_id="run_99", current="running")
        r = _GW.parse_user_input("hello", state)
        assert r.run_id == "run_99"

    def test_run_id_from_metadata_when_no_state(self):
        r = _GW.parse_user_input("hello", metadata={"run_id": "run_42"})
        assert r.run_id == "run_42"

    def test_returns_workflow_input_event(self):
        r = _GW.parse_user_input("hello")
        assert isinstance(r, WorkflowInputEvent)


# ── Metadata round-trip ───────────────────────────────────────────────────────

class TestMetadata:
    def test_review_card_run_id_round_trip(self):
        msg = _GW.render_event(_review_event(run_id="run_meta_1"))
        assert msg.metadata["run_id"] == "run_meta_1"

    def test_review_card_pending_action_id_round_trip(self):
        msg = _GW.render_event(_review_event(pending_action_id="pa_meta_1"))
        assert msg.metadata["pending_action_id"] == "pa_meta_1"

    def test_ui_action_run_id_preserved(self):
        result = _GW.parse_ui_action("approve", {"run_id": "rr_1", "pending_action_id": "pp_1"})
        assert result.run_id == "rr_1"

    def test_ui_action_pending_action_id_preserved(self):
        result = _GW.parse_ui_action("approve", {"run_id": "rr_1", "pending_action_id": "pp_2"})
        assert result.pending_action_id == "pp_2"

    def test_free_text_decision_carries_pending_action_id(self):
        state = _waiting(run_id="r1", pending_action_id="pa_rt")
        r = _GW.parse_user_input("approved", state)
        assert r.payload["pending_action_id"] == "pa_rt"

    def test_free_text_decision_carries_run_id(self):
        state = _waiting(run_id="run_rt")
        r = _GW.parse_user_input("approved", state)
        assert r.run_id == "run_rt"


# ── Boundary: gateway is a pure adapter ──────────────────────────────────────

class TestGatewayBoundaries:
    def test_no_execute_method(self):
        assert not hasattr(_GW, "execute")
        assert not hasattr(_GW, "execute_skill")
        assert not hasattr(_GW, "run_skill")

    def test_no_publish_method(self):
        assert not hasattr(_GW, "publish")
        assert not hasattr(_GW, "publish_skill")

    def test_no_policy_override_method(self):
        assert not hasattr(_GW, "override_policy")
        assert not hasattr(_GW, "bypass_gate")
        assert not hasattr(_GW, "approve_action")

    def test_blocked_renders_as_blocked_not_review_card(self):
        event = WorkflowEvent(
            type="action_blocked",
            run_id="r1",
            payload={"reasons": ["policy rule"]},
        )
        msg = _GW.render_event(event)
        assert msg.type == "blocked"
        assert not any(a.id == "approve" for a in msg.actions)

    def test_gateway_renders_only_what_event_allows(self):
        # Policy decides; gateway renders — only reject is in allowed_decisions.
        event = _review_event(allowed_decisions=["reject"])
        msg = _GW.render_event(event)
        assert not any(a.id == "approve" for a in msg.actions)
        assert any(a.id == "reject" for a in msg.actions)

    def test_ambiguous_input_never_auto_approves(self):
        state = _waiting()
        result = _GW.parse_user_input("sure whatever", state)
        assert result.type == "clarification_needed"
        assert result.payload.get("decision") != "approved"

    def test_gateway_accepts_arbitrary_pending_action_id(self):
        # Gateway does NOT validate that the ID exists — that is WorkflowRuntime's job.
        result = _GW.parse_ui_action(
            "approve", {"run_id": "r1", "pending_action_id": "nonexistent_id"}
        )
        assert isinstance(result, HumanDecisionEvent)
        assert result.pending_action_id == "nonexistent_id"
