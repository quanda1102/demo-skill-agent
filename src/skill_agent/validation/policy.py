from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

_BUNDLED_POLICY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "policies"
_DEFAULT_POLICY_PATH = _BUNDLED_POLICY_DIR / "mvp-safe.yaml"


class DependencyPolicy(BaseModel):
    mode: Literal["stdlib_only", "allowlist", "locked"] = "stdlib_only"
    allowed_imports: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=lambda: [
        "requirements.txt", "setup.py", "pyproject.toml", "setup.cfg", "Pipfile",
    ])


class ActivationPolicy(BaseModel):
    min_description_chars: int = 20
    max_description_chars: int = 500
    require_action_verb: bool = True
    require_domain: bool = True
    forbidden_placeholder_patterns: list[str] = Field(default_factory=lambda: [
        r"\bTODO\b", r"\bFIXME\b", r"\bPLACEHOLDER\b", r"<[^>]+>",
    ])


class CapabilityPolicy(BaseModel):
    operation_taxonomy: list[str] = Field(default_factory=lambda: [
        "create", "read", "update", "delete", "list", "move", "copy", "rename",
        "archive", "extract", "count", "search", "summarize", "parse", "format",
        "validate", "transform", "convert", "encode", "decode", "sort", "filter",
        "split", "join", "hash", "fetch", "write", "append", "execute", "run",
    ])
    allowed_side_effects: list[str] = Field(default_factory=lambda: [
        "file_read", "file_write", "file_delete", "network", "subprocess",
    ])
    action_side_effect_hints: dict[str, list[str]] = Field(default_factory=dict)


class RiskyPatternRule(BaseModel):
    severity: Literal["error", "warning"]
    patterns: list[str]


class CodeSafetyPolicy(BaseModel):
    risky_patterns: dict[str, RiskyPatternRule] = Field(default_factory=dict)


class PackagePolicy(BaseModel):
    allowed_top_level_paths: list[str] = Field(default_factory=lambda: [
        "SKILL.md", "scripts/", "references/", "assets/", "tests/",
    ])
    forbidden_paths: list[str] = Field(default_factory=lambda: [
        ".env", ".DS_Store", ".git/", "__pycache__/", ".pytest_cache/", ".mypy_cache/",
    ])
    max_file_size_bytes: int = 500_000
    max_skill_md_chars: int = 8_000


class PromptEvalPolicy(BaseModel):
    required: bool = False
    min_cases: int = 2
    allow_llm_judge: bool = True


class ReviewPolicy(BaseModel):
    require_for_side_effects: list[str] = Field(default_factory=lambda: [
        "file_write", "file_delete", "network", "subprocess",
    ])
    require_for_warnings: bool = False


class ValidationPolicy(BaseModel):
    profile: str = "mvp-safe"
    dependencies: DependencyPolicy = Field(default_factory=DependencyPolicy)
    activation: ActivationPolicy = Field(default_factory=ActivationPolicy)
    capability: CapabilityPolicy = Field(default_factory=CapabilityPolicy)
    code_safety: CodeSafetyPolicy = Field(default_factory=CodeSafetyPolicy)
    package: PackagePolicy = Field(default_factory=PackagePolicy)
    prompt_eval: PromptEvalPolicy = Field(default_factory=PromptEvalPolicy)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)


class ValidationPolicyLoader:
    @staticmethod
    def load(path: str | Path) -> ValidationPolicy:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Validation policy file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            try:
                raw = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"Invalid YAML in policy file {path}: {exc}") from exc
        try:
            return ValidationPolicy.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"Invalid validation policy: {exc}") from exc

    @staticmethod
    def default() -> ValidationPolicy:
        env_path = os.environ.get("SKILL_VALIDATION_POLICY")
        if env_path:
            return ValidationPolicyLoader.load(env_path)
        return ValidationPolicyLoader.load(_DEFAULT_POLICY_PATH)
