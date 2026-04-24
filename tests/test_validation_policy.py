"""Tests for the policy-as-config validation architecture."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.skill_agent.schemas.skill_model import (
    GeneratedSkill,
    Runtime,
    SkillFile,
    SkillMetadata,
    SkillSpec,
)
from src.skill_agent.validation.policy import (
    ActivationPolicy,
    CapabilityPolicy,
    CodeSafetyPolicy,
    DependencyPolicy,
    RiskyPatternRule,
    ValidationPolicy,
    ValidationPolicyLoader,
)
from src.skill_agent.validation.checks import (
    validate_code_safety,
    validate_skill_activation,
)
from src.skill_agent.validation.validator import StaticValidator
from src.skill_agent.schemas.skill_model import ValidationReport


_BUNDLED_POLICY = Path(__file__).resolve().parent.parent / "policies" / "mvp-safe.yaml"

_SPEC = SkillSpec(
    name="word-counter",
    description="Counts words.",
    purpose="Count words.",
    inputs=["text"],
    outputs=["count"],
    workflow_steps=["read", "count", "print"],
    runtime=Runtime.python,
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


def _make_skill(description: str | None = None, run_py: str = "print(1)", **overrides) -> GeneratedSkill:
    desc = description or "Counts the number of words in a line of text read from stdin."
    defaults = dict(
        metadata=SkillMetadata(
            name="word-counter",
            description=desc,
            domain=["text"],
            supported_actions=["count", "read"],
        ),
        files=[
            SkillFile(path="SKILL.md", content=_VALID_MD),
            SkillFile(path="scripts/run.py", content=run_py, executable=True),
        ],
        scripts=["scripts/run.py"],
        spec=_SPEC,
    )
    defaults.update(overrides)
    return GeneratedSkill(**defaults)


def _safe_policy(**overrides) -> ValidationPolicy:
    """Minimal in-memory policy matching default behavior."""
    return ValidationPolicy(**overrides)


# ── Policy loading ────────────────────────────────────────────────────────────

class TestPolicyLoading:
    def test_bundled_default_loads(self):
        policy = ValidationPolicyLoader.load(_BUNDLED_POLICY)
        assert policy.profile == "mvp-safe"
        assert "count" in policy.capability.operation_taxonomy
        assert "file_write" in policy.capability.allowed_side_effects

    def test_default_loader_returns_valid_policy(self):
        policy = ValidationPolicyLoader.default()
        assert isinstance(policy, ValidationPolicy)
        assert policy.activation.min_description_chars == 20

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            ValidationPolicyLoader.load(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{invalid: yaml: content: [}", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            ValidationPolicyLoader.load(bad)

    def test_invalid_policy_schema_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("activation:\n  min_description_chars: not-a-number\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid validation policy"):
            ValidationPolicyLoader.load(bad)

    def test_missing_sections_use_defaults(self, tmp_path):
        minimal = tmp_path / "minimal.yaml"
        minimal.write_text("profile: custom\n", encoding="utf-8")
        policy = ValidationPolicyLoader.load(minimal)
        assert policy.profile == "custom"
        assert policy.activation.min_description_chars == 20
        assert "count" in policy.capability.operation_taxonomy

    def test_env_var_overrides_default(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.yaml"
        custom.write_text("profile: env-override\nactivation:\n  min_description_chars: 5\n", encoding="utf-8")
        monkeypatch.setenv("SKILL_VALIDATION_POLICY", str(custom))
        policy = ValidationPolicyLoader.default()
        assert policy.profile == "env-override"
        assert policy.activation.min_description_chars == 5


# ── Activation policy ─────────────────────────────────────────────────────────

class TestActivationPolicy:
    def test_min_description_chars_from_policy(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(min_description_chars=50)
        )
        skill = _make_skill(description="Short description here.")  # 23 chars
        report = ValidationReport()
        result = validate_skill_activation(skill, report, policy)
        assert not result
        assert any("50" in e for e in report.errors)

    def test_max_description_chars_produces_warning(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(max_description_chars=30)
        )
        skill = _make_skill(description="Counts the number of words in a line of text read from stdin.")
        report = ValidationReport()
        validate_skill_activation(skill, report, policy)
        assert any("30" in w for w in report.warnings)

    def test_placeholder_patterns_from_policy(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(
                forbidden_placeholder_patterns=[r"\bDRAFT\b"]
            )
        )
        skill = _make_skill(description="DRAFT skill that counts words in the text provided.")
        report = ValidationReport()
        result = validate_skill_activation(skill, report, policy)
        assert not result
        assert any("placeholder" in e.lower() for e in report.errors)

    def test_default_placeholder_todo_blocked(self):
        skill = _make_skill(description="TODO: add a proper description later please.")
        report = StaticValidator().validate(skill)
        assert not report.activation_pass

    def test_custom_placeholder_pattern_replaces_default(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(
                forbidden_placeholder_patterns=[r"\bDRAFT\b"]
            )
        )
        # TODO is not blocked by this custom policy
        skill = _make_skill(description="TODO: counts words in text for testing purposes.")
        report = ValidationReport()
        result = validate_skill_activation(skill, report, policy)
        assert result  # TODO is not a banned placeholder in this policy

    def test_empty_placeholder_patterns_allows_anything(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(forbidden_placeholder_patterns=[])
        )
        skill = _make_skill(description="TODO FIXME PLACEHOLDER <fill me in> text here.")
        report = ValidationReport()
        # Should not fail on placeholder (but may fail on domain if not set)
        # Force domain to be present via skill metadata
        from src.skill_agent.schemas.skill_model import SkillMetadata
        skill2 = _make_skill(
            description="TODO FIXME PLACEHOLDER <fill me in> and some real content.",
        )
        report2 = ValidationReport()
        validate_skill_activation(skill2, report2, policy)
        assert not any("placeholder" in e.lower() for e in report2.errors)


# ── Capability policy ─────────────────────────────────────────────────────────

class TestCapabilityPolicy:
    def test_custom_taxonomy_warns_on_unknown_verb(self):
        policy = ValidationPolicy(
            capability=CapabilityPolicy(operation_taxonomy=["create", "read"])
        )
        skill = _make_skill()
        # Default skill has supported_actions=["count", "read"] — "count" not in ["create","read"]
        report = ValidationReport()
        validate_skill_activation(skill, report, policy)
        assert any("non-taxonomy" in w for w in report.warnings)

    def test_custom_allowed_side_effects_rejects_unknown(self):
        policy = ValidationPolicy(
            capability=CapabilityPolicy(
                allowed_side_effects=["file_read"],  # only file_read allowed
                operation_taxonomy=["count", "read", "create", "write"],
            )
        )
        from src.skill_agent.schemas.skill_model import SkillMetadata
        skill = _make_skill()
        skill = skill.model_copy(update={
            "metadata": SkillMetadata(
                name="word-counter",
                description="Counts the number of words in a line of text read from stdin.",
                domain=["text"],
                supported_actions=["count"],
                side_effects=["file_write"],  # not in custom allowed_side_effects
            )
        })
        report = ValidationReport()
        result = validate_skill_activation(skill, report, policy)
        assert not result
        assert any("file_write" in e for e in report.errors)


# ── Dependency policy ─────────────────────────────────────────────────────────

class TestDependencyPolicy:
    def test_forbidden_files_from_policy(self):
        policy = ValidationPolicy(
            dependencies=DependencyPolicy(forbidden_files=["requirements.txt"])
        )
        skill = _make_skill()
        skill = skill.model_copy(update={
            "files": [
                SkillFile(path="SKILL.md", content=_VALID_MD),
                SkillFile(path="scripts/run.py", content="print(1)"),
                SkillFile(path="requirements.txt", content="requests"),
            ]
        })
        report = StaticValidator(policy=policy).validate(skill)
        assert not report.activation_pass
        assert any("requirements.txt" in e for e in report.errors)

    def test_custom_forbidden_file_not_in_default_list(self):
        policy = ValidationPolicy(
            dependencies=DependencyPolicy(forbidden_files=["banned-file.txt"])
        )
        # requirements.txt NOT in the custom forbidden list — should not fail
        skill = _make_skill()
        skill = skill.model_copy(update={
            "files": [
                SkillFile(path="SKILL.md", content=_VALID_MD),
                SkillFile(path="scripts/run.py", content="print(1)"),
                SkillFile(path="requirements.txt", content="requests"),
            ]
        })
        report = ValidationReport()
        from src.skill_agent.validation.checks import _validate_no_external_dependencies
        result = _validate_no_external_dependencies(skill, report, policy)
        # requirements.txt not banned by this custom policy
        assert result

    def test_allowed_imports_whitelist_permits_third_party(self):
        policy = ValidationPolicy(
            dependencies=DependencyPolicy(
                mode="stdlib_only",
                allowed_imports=["requests"],
            )
        )
        skill = _make_skill(run_py="import requests\nprint(requests.get('x'))")
        report = ValidationReport()
        from src.skill_agent.validation.checks import _validate_no_external_dependencies
        result = _validate_no_external_dependencies(skill, report, policy)
        assert result  # requests is whitelisted

    def test_non_whitelisted_third_party_fails(self):
        policy = ValidationPolicy(
            dependencies=DependencyPolicy(mode="stdlib_only", allowed_imports=[])
        )
        skill = _make_skill(run_py="import requests\nprint(requests.get('x'))")
        report = ValidationReport()
        from src.skill_agent.validation.checks import _validate_no_external_dependencies
        result = _validate_no_external_dependencies(skill, report, policy)
        assert not result
        assert any("requests" in e for e in report.errors)


# ── Code safety policy ────────────────────────────────────────────────────────

class TestCodeSafetyPolicy:
    def _policy_with_rule(self, rule_name: str, severity: str, patterns: list[str]) -> ValidationPolicy:
        return ValidationPolicy(
            code_safety=CodeSafetyPolicy(
                risky_patterns={rule_name: RiskyPatternRule(severity=severity, patterns=patterns)}
            )
        )

    def test_error_severity_fails_validator(self):
        policy = self._policy_with_rule("no_eval", "error", [r"\beval\s*\("])
        skill = _make_skill(run_py="result = eval(user_input)")
        report = ValidationReport()
        result = validate_code_safety(skill, report, policy)
        assert not result
        assert any("no_eval" in e for e in report.errors)

    def test_warning_severity_does_not_fail_validator(self):
        policy = self._policy_with_rule("env_read", "warning", [r"\bos\.environ\b"])
        skill = _make_skill(run_py="import os\nval = os.environ.get('HOME')")
        report = ValidationReport()
        result = validate_code_safety(skill, report, policy)
        assert result  # warning does not fail
        assert any("env_read" in w for w in report.warnings)

    def test_invalid_regex_adds_warning_and_skips_rule(self):
        policy = self._policy_with_rule("bad_rule", "error", [r"[unclosed"])
        skill = _make_skill(run_py="print('hello')")
        report = ValidationReport()
        result = validate_code_safety(skill, report, policy)
        assert result  # invalid regex skipped, not an error in the skill
        assert any("invalid regex" in w for w in report.warnings)

    def test_no_risky_patterns_always_passes(self):
        policy = ValidationPolicy(code_safety=CodeSafetyPolicy(risky_patterns={}))
        skill = _make_skill(run_py="import os; os.system('rm -rf /')")
        report = ValidationReport()
        result = validate_code_safety(skill, report, policy)
        assert result

    def test_safe_code_passes_default_policy(self):
        report = StaticValidator().validate(_make_skill(run_py="print(1)"))
        assert report.code_safety_pass

    def test_eval_fails_default_policy(self):
        report = StaticValidator().validate(_make_skill(run_py="eval(input())"))
        assert not report.code_safety_pass
        assert any("eval" in e for e in report.errors)

    def test_env_read_warns_in_default_policy(self):
        report = StaticValidator().validate(
            _make_skill(run_py="import os\nprint(os.environ.get('HOME'))")
        )
        assert report.code_safety_pass  # warning only, not error
        assert any("env_read" in w for w in report.warnings)

    def test_rule_name_appears_in_report_message(self):
        policy = self._policy_with_rule("custom_rule_xyz", "error", [r"\bos\.system\s*\("])
        skill = _make_skill(run_py="import os\nos.system('ls')")
        report = ValidationReport()
        validate_code_safety(skill, report, policy)
        assert any("custom_rule_xyz" in e for e in report.errors)

    def test_only_py_files_are_scanned(self):
        policy = self._policy_with_rule("no_eval", "error", [r"\beval\s*\("])
        skill = _make_skill()
        skill = skill.model_copy(update={
            "files": [
                SkillFile(path="SKILL.md", content=_VALID_MD + "\neval(something)"),
                SkillFile(path="scripts/run.py", content="print(1)"),
            ]
        })
        report = ValidationReport()
        result = validate_code_safety(skill, report, policy)
        assert result  # SKILL.md is not a .py file


# ── End-to-end: StaticValidator with custom policy ────────────────────────────

class TestStaticValidatorWithPolicy:
    def test_validator_accepts_custom_policy(self):
        policy = ValidationPolicy(
            activation=ActivationPolicy(min_description_chars=5)
        )
        skill = _make_skill(description="Short")
        report = StaticValidator(policy=policy).validate(skill)
        assert report.activation_pass

    def test_validator_exposes_policy(self):
        policy = ValidationPolicyLoader.default()
        v = StaticValidator(policy=policy)
        assert v.policy is policy

    def test_changing_severity_changes_outcome(self):
        # Default policy has env_read as "warning" — change to "error"
        default = ValidationPolicyLoader.default()
        # Build a policy with env_read as error
        strict_rules = dict(default.code_safety.risky_patterns)
        strict_rules["env_read"] = RiskyPatternRule(severity="error", patterns=[r"\bos\.environ\b"])
        strict_policy = default.model_copy(update={
            "code_safety": CodeSafetyPolicy(risky_patterns=strict_rules)
        })
        skill = _make_skill(run_py="import os\nprint(os.environ.get('HOME'))")
        report = StaticValidator(policy=strict_policy).validate(skill)
        assert not report.code_safety_pass
        assert any("env_read" in e for e in report.errors)
