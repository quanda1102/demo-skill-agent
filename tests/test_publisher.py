from __future__ import annotations

from pathlib import Path

import pytest

from src.skill_agent.publisher import PublishGateway
from src.skill_agent.models import ValidationReport


def test_rejected_skill_does_not_write_files(sample_skill, tmp_path):
    report = ValidationReport(publishable=False, errors=["syntax check failed"])
    result = PublishGateway(tmp_path).evaluate(sample_skill, report)

    assert result.published is False
    assert result.skill_path is None
    assert not (tmp_path / "word-counter").exists()


def test_published_skill_writes_files(sample_skill, tmp_path):
    report = ValidationReport(
        syntax_pass=True,
        metadata_pass=True,
        activation_pass=True,
        execution_pass=True,
        publishable=True,
    )
    result = PublishGateway(tmp_path).evaluate(sample_skill, report)

    assert result.published is True
    skill_dir = tmp_path / "word-counter"
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "scripts" / "run.py").exists()


def test_published_skill_md_has_published_status(sample_skill, tmp_path):
    report = ValidationReport(
        syntax_pass=True,
        metadata_pass=True,
        activation_pass=True,
        execution_pass=True,
        publishable=True,
    )
    PublishGateway(tmp_path).evaluate(sample_skill, report)

    content = (tmp_path / "word-counter" / "SKILL.md").read_text()
    assert "status: published" in content
    assert "status: generated" not in content


def test_reject_message_includes_errors(sample_skill, tmp_path):
    report = ValidationReport(
        publishable=False,
        errors=["missing SKILL.md", "bad metadata"],
    )
    result = PublishGateway(tmp_path).evaluate(sample_skill, report)
    assert "missing SKILL.md" in result.message or "2 error" in result.message
