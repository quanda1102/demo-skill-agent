from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.skill_agent.schemas.skill_model import (
    Runtime,
    SkillFile,
    SkillMetadata,
    SkillRequest,
    SkillTestCase,
    SkillStatus,
    ValidationReport,
)


def test_skill_request_defaults():
    req = SkillRequest(skill_name="foo", skill_description="bar")
    assert req.runtime_preference == Runtime.python
    assert req.sample_inputs == []


def test_skill_request_runtime_coercion():
    req = SkillRequest(skill_name="foo", skill_description="bar", runtime_preference="node")
    assert req.runtime_preference == Runtime.node


def test_skill_request_missing_required():
    with pytest.raises(ValidationError):
        SkillRequest(skill_name="foo")  # missing skill_description


def test_skill_metadata_defaults():
    m = SkillMetadata(name="x", description="y")
    assert m.version == "0.1.0"
    assert m.status == SkillStatus.generated
    assert m.entrypoints[0]["path"] == "SKILL.md"


def test_skill_file_not_executable_by_default():
    f = SkillFile(path="SKILL.md", content="---\nname: x\n---")
    assert f.executable is False


def test_skill_test_case_error_defaults():
    tc = SkillTestCase(description="x", input="y")
    assert tc.expected_output == ""
    assert tc.expected_stderr is None
    assert tc.expected_exit_code is None


def test_validation_report_compute_publishable_all_pass():
    r = ValidationReport(
        syntax_pass=True,
        metadata_pass=True,
        activation_pass=True,
        execution_pass=True,
        regression_pass=True,
    )
    r.compute_publishable()
    assert r.publishable is True


def test_validation_report_not_publishable_when_errors():
    r = ValidationReport(
        syntax_pass=True,
        metadata_pass=True,
        activation_pass=True,
        execution_pass=True,
        errors=["something went wrong"],
    )
    r.compute_publishable()
    assert r.publishable is False


def test_validation_report_not_publishable_when_check_fails():
    r = ValidationReport(
        syntax_pass=True,
        metadata_pass=True,
        activation_pass=False,
        execution_pass=True,
    )
    r.compute_publishable()
    assert r.publishable is False


def test_skill_status_enum_values():
    assert SkillStatus.draft == "draft"
    assert SkillStatus.published == "published"
    assert SkillStatus.rejected == "rejected"
