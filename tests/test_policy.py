from __future__ import annotations

from pathlib import Path

import pytest

from src.skill_agent.runtime import (
    CapabilityStatus,
    ExecutionStatus,
    SelectionConfig,
    SelectionStatus,
    SkillStub,
    TaskStatus,
    check_capability,
    discover_skills,
)
from src.skill_agent.runtime.policy import PolicyConfig, PolicyDecision, PolicyEngine

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _stub(
    skill_id: str = "test",
    description: str = "test skill",
    domain: list[str] | None = None,
    supported_actions: list[str] | None = None,
    forbidden_actions: list[str] | None = None,
) -> SkillStub:
    return SkillStub(
        skill_id=skill_id,
        name=skill_id,
        description=description,
        skill_dir=Path("."),
        domain=domain or [],
        supported_actions=supported_actions or [],
        forbidden_actions=forbidden_actions or [],
    )


# ── capability checks ─────────────────────────────────────────────────────────

class TestCheckCapability:
    def test_supported_action_returns_supported(self):
        stub = _stub(supported_actions=["create", "write"])
        status, logs = check_capability(stub, "create")
        assert status == CapabilityStatus.supported
        assert any("supported" in l.message for l in logs)

    def test_unsupported_action_returns_unsupported_operation(self):
        stub = _stub(supported_actions=["create", "write"])
        status, logs = check_capability(stub, "delete")
        assert status == CapabilityStatus.unsupported_operation

    def test_forbidden_action_is_denied_even_if_in_supported(self):
        stub = _stub(supported_actions=["create", "delete"], forbidden_actions=["delete"])
        status, logs = check_capability(stub, "delete")
        assert status == CapabilityStatus.unsupported_operation
        assert any("forbidden" in l.message for l in logs)

    def test_no_metadata_returns_unknown_capability(self):
        stub = _stub()
        status, logs = check_capability(stub, "create")
        assert status == CapabilityStatus.unknown_capability

    def test_domain_only_without_supported_actions_returns_unknown(self):
        stub = _stub(domain=["obsidian"])
        status, logs = check_capability(stub, "create")
        assert status == CapabilityStatus.unknown_capability

    def test_empty_action_returns_unknown(self):
        stub = _stub(supported_actions=["create"])
        status, logs = check_capability(stub, "")
        assert status == CapabilityStatus.unknown_capability

    def test_case_insensitive_matching(self):
        stub = _stub(supported_actions=["Create", "WRITE"])
        status, _ = check_capability(stub, "create")
        assert status == CapabilityStatus.supported

    def test_real_obsidian_note_writer_denies_delete(self):
        stubs, _ = discover_skills(SKILLS_DIR)
        nw = next(s for s in stubs if s.skill_id == "obsidian-note-writer")
        status, _ = check_capability(nw, "delete")
        assert status == CapabilityStatus.unsupported_operation

    def test_real_obsidian_crud_allows_delete(self):
        stubs, _ = discover_skills(SKILLS_DIR)
        crud = next(s for s in stubs if s.skill_id == "obsidian-crud")
        status, _ = check_capability(crud, "delete")
        assert status == CapabilityStatus.supported


# ── policy engine ─────────────────────────────────────────────────────────────

class TestPolicyEngine:
    def _note_writer_stub(self) -> SkillStub:
        return _stub(
            skill_id="obsidian-note-writer",
            description="Creates markdown notes in Obsidian vault",
            domain=["obsidian", "notes"],
            supported_actions=["create", "write", "format"],
            forbidden_actions=["delete"],
        )

    def _crud_stub(self) -> SkillStub:
        return _stub(
            skill_id="obsidian-crud",
            description="Perform CRUD operations on vault files",
            domain=["obsidian", "vault", "crud"],
            supported_actions=["create", "read", "update", "delete"],
        )

    def test_matched_supported_action_allows_execution(self):
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="create a markdown note in obsidian vault",
            requested_action="create",
        )
        assert decision.selection_status == SelectionStatus.matched
        assert decision.capability_status == CapabilityStatus.supported
        assert decision.execution_status == ExecutionStatus.allowed
        assert decision.execution_allowed is True

    def test_unsupported_action_denies_execution(self):
        """obsidian-note-writer forbids delete — execution must be denied."""
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="delete a note from the obsidian vault",
            requested_action="delete",
        )
        assert decision.selection_status == SelectionStatus.matched
        assert decision.capability_status == CapabilityStatus.unsupported_operation
        assert decision.execution_status == ExecutionStatus.denied
        assert decision.task_status == TaskStatus.unsupported
        assert decision.execution_allowed is False

    def test_no_match_skips_execution(self):
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="deploy kubernetes to production",
        )
        assert decision.selection_status == SelectionStatus.no_match
        assert decision.execution_status == ExecutionStatus.skipped
        assert decision.execution_allowed is False

    def test_delete_action_requires_confirmation(self):
        """Delete is in require_confirmation_for — must be denied even if supported."""
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._crud_stub()],
            user_request="delete vault files",
            requested_action="delete",
        )
        assert decision.capability_status == CapabilityStatus.supported
        assert decision.execution_status == ExecutionStatus.denied
        assert "confirmation" in decision.reason.lower()

    def test_configurable_confirmation_list(self):
        """Removing delete from confirmation list allows it through."""
        config = PolicyConfig(require_confirmation_for=[])
        engine = PolicyEngine(config)
        decision = engine.evaluate(
            [self._crud_stub()],
            user_request="delete vault crud files",
            requested_action="delete",
        )
        assert decision.execution_status == ExecutionStatus.allowed

    def test_low_confidence_skips_execution(self):
        stubs = [_stub("obscure-skill", "Something obscure and rare")]
        engine = PolicyEngine()
        decision = engine.evaluate(stubs, user_request="obscure")
        assert decision.selection_status == SelectionStatus.low_confidence
        assert decision.execution_status == ExecutionStatus.skipped

    def test_ambiguous_match_skips_execution(self):
        stubs = [
            _stub("a", "create markdown notes obsidian vault"),
            _stub("b", "create markdown notes obsidian files"),
        ]
        engine = PolicyEngine()
        decision = engine.evaluate(stubs, user_request="create markdown notes obsidian")
        assert decision.selection_status == SelectionStatus.ambiguous
        assert decision.execution_status == ExecutionStatus.skipped

    def test_no_action_specified_allowed_when_matched(self):
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="create a markdown note in obsidian vault",
        )
        assert decision.selection_status == SelectionStatus.matched
        assert decision.execution_status == ExecutionStatus.allowed

    def test_policy_decision_exposes_selected_stub(self):
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="create a note in obsidian vault",
            requested_action="create",
        )
        assert decision.selected_stub is not None
        assert decision.selected_stub.skill_id == "obsidian-note-writer"

    def test_policy_decision_logs_are_structured(self):
        engine = PolicyEngine()
        decision = engine.evaluate(
            [self._note_writer_stub()],
            user_request="create a markdown note in obsidian vault",
            requested_action="create",
        )
        assert len(decision.logs) > 0
        assert all(hasattr(l, "phase") and hasattr(l, "level") for l in decision.logs)

    def test_policy_example_1_supported_create(self):
        """Policy doc example 1: create request → matched/supported/allowed/unknown."""
        stubs, _ = discover_skills(SKILLS_DIR)
        engine = PolicyEngine()
        decision = engine.evaluate(
            stubs,
            user_request="create an obsidian markdown note",
            requested_action="create",
        )
        assert decision.selection_status == SelectionStatus.matched
        assert decision.capability_status == CapabilityStatus.supported
        assert decision.execution_status == ExecutionStatus.allowed

    def test_policy_example_2_unsupported_delete(self):
        """Policy doc example 2: delete request on note-writer → denied."""
        stubs, _ = discover_skills(SKILLS_DIR)
        nw_stubs = [s for s in stubs if s.skill_id == "obsidian-note-writer"]
        engine = PolicyEngine(PolicyConfig(require_confirmation_for=[]))
        decision = engine.evaluate(
            nw_stubs,
            user_request="delete a note from the obsidian vault",
            requested_action="delete",
        )
        assert decision.selection_status == SelectionStatus.matched
        assert decision.capability_status == CapabilityStatus.unsupported_operation
        assert decision.execution_status == ExecutionStatus.denied
        assert decision.task_status == TaskStatus.unsupported

    def test_policy_example_3_no_match(self):
        """Policy doc example 3: unrelated request → no_match/skipped."""
        stubs, _ = discover_skills(SKILLS_DIR)
        engine = PolicyEngine()
        decision = engine.evaluate(stubs, user_request="deploy kubernetes cluster to production")
        assert decision.selection_status in (SelectionStatus.no_match, SelectionStatus.low_confidence)
        assert decision.execution_status == ExecutionStatus.skipped
