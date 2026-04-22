from __future__ import annotations

import pytest

from src.skill_agent.models import (
    GeneratedSkill,
    Runtime,
    SkillFile,
    SkillMetadata,
    SkillSpec,
    SkillStatus,
    SkillTestCase,
)
from src.skill_agent.validator import StaticValidator

_SPEC = SkillSpec(
    name="word-counter",
    description="Counts words.",
    purpose="Count words.",
    inputs=["text"],
    outputs=["count"],
    workflow_steps=["read", "count", "print"],
    runtime=Runtime.python,
    required_files=["SKILL.md", "scripts/run.py"],
)

_VALID_MD = """\
---
name: word-counter
description: Counts the number of words in a line of text read from stdin.
version: 0.1.0
owner: skill-agent
runtime: python
status: generated
domain:
  - text
  - analysis
supported_actions:
  - count
  - read
forbidden_actions: []
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---
Body text here.
"""


def _make_skill(**overrides) -> GeneratedSkill:
    defaults = dict(
        metadata=SkillMetadata(
            name="word-counter",
            description="Counts the number of words in a line of text read from stdin.",
            domain=["text", "analysis"],
            supported_actions=["count", "read"],
        ),
        files=[
            SkillFile(path="SKILL.md", content=_VALID_MD),
            SkillFile(path="scripts/run.py", content="print(1)", executable=True),
        ],
        scripts=["scripts/run.py"],
        spec=_SPEC,
    )
    defaults.update(overrides)
    return GeneratedSkill(**defaults)


def test_valid_skill_passes_all():
    report = StaticValidator().validate(_make_skill())
    assert report.syntax_pass
    assert report.metadata_pass
    assert report.activation_pass
    assert not report.errors


def test_missing_skill_md_fails_syntax():
    skill = _make_skill(files=[SkillFile(path="scripts/run.py", content="x")])
    report = StaticValidator().validate(skill)
    assert not report.syntax_pass
    assert any("SKILL.md" in e for e in report.errors)


def test_invalid_frontmatter_fails_syntax():
    bad_md = "no frontmatter here\njust text"
    skill = _make_skill(files=[
        SkillFile(path="SKILL.md", content=bad_md),
        SkillFile(path="scripts/run.py", content="x"),
    ])
    report = StaticValidator().validate(skill)
    assert not report.syntax_pass


def test_missing_required_frontmatter_key_fails_syntax():
    bad_md = "---\ndescription: something\n---\nbody"
    skill = _make_skill(files=[
        SkillFile(path="SKILL.md", content=bad_md),
        SkillFile(path="scripts/run.py", content="x"),
    ])
    report = StaticValidator().validate(skill)
    assert not report.syntax_pass
    assert any("name" in e for e in report.errors)


def test_duplicate_file_paths_fail_syntax():
    skill = _make_skill(files=[
        SkillFile(path="SKILL.md", content=_VALID_MD),
        SkillFile(path="SKILL.md", content=_VALID_MD),
    ])
    report = StaticValidator().validate(skill)
    assert not report.syntax_pass


def test_unresolved_script_ref_fails_syntax():
    skill = _make_skill(
        files=[SkillFile(path="SKILL.md", content=_VALID_MD)],
        scripts=["scripts/missing.py"],
    )
    report = StaticValidator().validate(skill)
    assert not report.syntax_pass


def test_metadata_name_mismatch_fails_metadata():
    skill = _make_skill(
        metadata=SkillMetadata(
            name="different-name",
            description="Counts the number of words in a line of text read from stdin.",
        )
    )
    report = StaticValidator().validate(skill)
    assert not report.metadata_pass
    assert any("mismatch" in e.lower() for e in report.errors)


def test_description_too_short_fails_activation():
    skill = _make_skill(
        metadata=SkillMetadata(name="word-counter", description="Short")
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass


def test_placeholder_in_description_fails_activation():
    skill = _make_skill(
        metadata=SkillMetadata(
            name="word-counter",
            description="TODO: fill in description for this skill later on.",
        )
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass


def test_missing_domain_fails_activation():
    skill = _make_skill(
        metadata=SkillMetadata(
            name="word-counter",
            description="Counts the number of words in a line of text read from stdin.",
            domain=[],
            supported_actions=["count"],
        )
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass
    assert any("domain" in e for e in report.errors)


def test_missing_supported_actions_fails_activation():
    skill = _make_skill(
        metadata=SkillMetadata(
            name="word-counter",
            description="Counts the number of words in a line of text read from stdin.",
            domain=["text"],
            supported_actions=[],
        )
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass
    assert any("supported_actions" in e for e in report.errors)


def test_duplicate_test_case_descriptions_fail_activation():
    skill = _make_skill(
        tests=[
            SkillTestCase(description="duplicate", input="a", expected_output="1"),
            SkillTestCase(description="duplicate", input="b", expected_output="2"),
        ]
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass
    assert any("Duplicate test case description" in e for e in report.errors)


def test_live_public_url_in_test_input_fails_activation():
    skill = _make_skill(
        tests=[
            SkillTestCase(
                description="live url",
                input="https://example.com",
                expected_output="https://iana.org/domains/example",
            )
        ]
    )
    report = StaticValidator().validate(skill)
    assert not report.activation_pass
    assert any("live URL" in e for e in report.errors)
