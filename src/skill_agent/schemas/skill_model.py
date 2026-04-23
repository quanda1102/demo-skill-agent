from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Runtime(str, Enum):
    python = "python"
    node = "node"
    shell = "shell"
    other = "other"


class SkillStatus(str, Enum):
    draft = "draft"
    generated = "generated"
    validated = "validated"
    published = "published"
    rejected = "rejected"


class SkillRequest(BaseModel):
    skill_name: str
    skill_description: str
    sample_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    runtime_preference: Runtime = Runtime.python


class SkillTestCase(BaseModel):
    description: str
    input: str
    expected_output: str = ""
    validation_method: str = "string_match"  # string_match | contains | regex | manual
    # Files to create in the sandbox before this test runs.
    # Keys are relative paths; values are file content.
    # Use this for read-only skills that cannot create their own test fixtures.
    fixtures: dict[str, str] = Field(default_factory=dict)
    # Optional stderr expectation for negative/error-path tests.
    expected_stderr: str | None = None
    # Defaults to 0 when omitted. Set explicitly for expected failures.
    expected_exit_code: int | None = None


class SkillSpec(BaseModel):
    name: str
    description: str
    purpose: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    workflow_steps: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    required_files: list[str] = Field(default_factory=list)
    runtime: Runtime = Runtime.python
    test_cases: list[SkillTestCase] = Field(default_factory=list)


class SkillFile(BaseModel):
    path: str
    content: str
    executable: bool = False


class SkillMetadata(BaseModel):
    name: str
    description: str
    version: str = "0.1.0"
    owner: str = "skill-agent"
    runtime: Runtime = Runtime.python
    status: SkillStatus = SkillStatus.generated
    entrypoints: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"type": "skill_md", "path": "SKILL.md"}]
    )
    # Capability metadata — consumed by the runtime policy layer
    domain: list[str] = Field(default_factory=list)
    supported_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)


class GeneratedSkill(BaseModel):
    metadata: SkillMetadata
    files: list[SkillFile] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    tests: list[SkillTestCase] = Field(default_factory=list)
    spec: SkillSpec
    status: SkillStatus = SkillStatus.generated


class ValidationReport(BaseModel):
    syntax_pass: bool = False
    metadata_pass: bool = False
    activation_pass: bool = False
    execution_pass: bool = False
    regression_pass: bool = False
    publishable: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)

    def compute_publishable(self) -> None:
        self.publishable = (
            self.syntax_pass
            and self.metadata_pass
            and self.activation_pass
            and self.execution_pass
            and not self.errors
        )


class PublishResult(BaseModel):
    skill_name: str
    published: bool
    skill_path: str | None = None
    report: ValidationReport
    message: str


def materialize_skill(skill: GeneratedSkill, output_dir: Path) -> Path:
    """Write all skill files to disk under output_dir/<skill_name>/."""
    skill_dir = output_dir / skill.metadata.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    for f in skill.files:
        path = skill_dir / f.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.content, encoding="utf-8")
        if f.executable:
            path.chmod(path.stat().st_mode | 0o111)
    return skill_dir
