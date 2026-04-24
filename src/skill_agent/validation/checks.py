from __future__ import annotations

import re
from urllib.parse import urlparse

from src.skill_agent.schemas.skill_model import GeneratedSkill, Runtime, SkillStatus, ValidationReport
from .frontmatter import parse_frontmatter
from .policy import ValidationPolicy

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_VERB_SUFFIXES = re.compile(r"\b\w+(s|es|ed|ing|ize|ise|ate|ify|en)\b", re.IGNORECASE)
_PUBLIC_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)

# Known third-party package detector — the detection logic lives in Python;
# the allowlist (permitted exceptions) lives in policy.dependencies.allowed_imports.
_THIRD_PARTY_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+("
    r"requests|httpx|aiohttp|urllib3|"
    r"bs4|beautifulsoup4|lxml|html5lib|"
    r"pandas|numpy|scipy|matplotlib|seaborn|"
    r"pydantic|attrs|"
    r"click|rich|typer|"
    r"boto3|botocore|"
    r"sqlalchemy|pymongo|redis|"
    r"flask|fastapi|django|starlette|"
    r"PIL|pillow"
    r")\b",
    re.MULTILINE | re.IGNORECASE,
)


# ── Pure-structure checks (no policy needed) ──────────────────────────────────

def validate_skill_syntax(skill: GeneratedSkill, report: ValidationReport) -> bool:
    paths = {file.path for file in skill.files}

    if "SKILL.md" not in paths:
        report.errors.append("SKILL.md is missing from generated files")
        return False

    skill_md = next(file for file in skill.files if file.path == "SKILL.md")
    frontmatter = parse_frontmatter(skill_md.content)
    if frontmatter is None:
        report.errors.append("SKILL.md has invalid or missing YAML frontmatter")
        return False

    for required_key in ("name", "description"):
        if required_key not in frontmatter:
            report.errors.append(f"SKILL.md frontmatter missing required key: {required_key}")
            return False

    seen_paths: set[str] = set()
    for file in skill.files:
        if file.path in seen_paths:
            report.errors.append(f"Duplicate file path in generated skill: {file.path}")
            return False
        seen_paths.add(file.path)

    for category, refs in (
        ("scripts", skill.scripts),
        ("references", skill.references),
        ("assets", skill.assets),
    ):
        for ref in refs:
            if ref not in paths:
                report.errors.append(f"{category} entry '{ref}' not found in files list")
                return False

    return True


def validate_skill_metadata(skill: GeneratedSkill, report: ValidationReport) -> bool:
    paths = {file.path for file in skill.files}
    if "SKILL.md" not in paths:
        report.errors.append("Cannot check metadata: SKILL.md missing")
        return False

    skill_md = next(file for file in skill.files if file.path == "SKILL.md")
    frontmatter = parse_frontmatter(skill_md.content)
    if not frontmatter:
        report.errors.append("Cannot check metadata: frontmatter unparseable")
        return False

    if frontmatter.get("name") != skill.metadata.name:
        report.errors.append(
            f"Metadata name mismatch: frontmatter has '{frontmatter.get('name')}', "
            f"metadata object has '{skill.metadata.name}'"
        )
        return False

    try:
        SkillStatus(skill.metadata.status)
    except ValueError:
        report.errors.append(f"Invalid status value: {skill.metadata.status}")
        return False

    try:
        Runtime(skill.metadata.runtime)
    except ValueError:
        report.errors.append(f"Invalid runtime value: {skill.metadata.runtime}")
        return False

    if not _SEMVER.match(skill.metadata.version):
        report.warnings.append(f"Version '{skill.metadata.version}' does not match semver (x.y.z)")

    if not skill.metadata.entrypoints:
        report.errors.append("Entrypoints list is empty")
        return False

    has_skill_md_entry = any(entrypoint.get("path") == "SKILL.md" for entrypoint in skill.metadata.entrypoints)
    if not has_skill_md_entry:
        report.errors.append("No entrypoint pointing to SKILL.md")
        return False

    return True


def validate_skill_test_cases(skill: GeneratedSkill, report: ValidationReport) -> bool:
    seen_descriptions: set[str] = set()
    for test_case in skill.tests:
        if test_case.description in seen_descriptions:
            report.errors.append(f"Duplicate test case description: {test_case.description!r}")
            return False
        seen_descriptions.add(test_case.description)

        live_url = _find_live_public_url(test_case.input)
        if live_url:
            report.errors.append(
                "Test cases must be deterministic and self-contained; "
                f"{test_case.description!r} uses live URL {live_url!r}. "
                "Use fixtures plus a file:// URL instead."
            )
            return False

    return True


# ── Policy-driven checks ──────────────────────────────────────────────────────

def validate_skill_activation(
    skill: GeneratedSkill,
    report: ValidationReport,
    policy: ValidationPolicy,
) -> bool:
    if not _validate_capability_metadata(skill, report, policy):
        return False
    if not _validate_no_external_dependencies(skill, report, policy):
        return False

    ap = policy.activation
    description = skill.metadata.description

    if len(description) < ap.min_description_chars:
        report.errors.append(
            f"Description too short ({len(description)} chars); "
            f"minimum is {ap.min_description_chars} (policy: activation.min_description_chars)"
        )
        return False

    if len(description) > ap.max_description_chars:
        report.warnings.append(
            f"Description is long ({len(description)} chars); "
            f"consider trimming below {ap.max_description_chars} "
            f"(policy: activation.max_description_chars)"
        )

    placeholder_re = _build_placeholder_re(ap.forbidden_placeholder_patterns)
    if placeholder_re and placeholder_re.search(description):
        report.errors.append(
            "Description contains placeholder text matching "
            f"policy rule activation.forbidden_placeholder_patterns"
        )
        return False

    if ap.require_action_verb and not _VERB_SUFFIXES.search(description):
        report.warnings.append("Description may lack an action verb; consider making it more operational")

    if not skill.spec.purpose:
        report.warnings.append("spec.purpose is empty; activation quality may be low")

    return True


def validate_code_safety(
    skill: GeneratedSkill,
    report: ValidationReport,
    policy: ValidationPolicy,
) -> bool:
    """Scan Python files against risky-pattern rules from policy.code_safety."""
    python_files = [f for f in skill.files if f.path.endswith(".py")]
    passed = True

    for rule_name, rule in policy.code_safety.risky_patterns.items():
        compiled: list[re.Pattern[str]] = []
        for pat_str in rule.patterns:
            try:
                compiled.append(re.compile(pat_str, re.MULTILINE))
            except re.error as exc:
                report.warnings.append(
                    f"code_safety: rule '{rule_name}' has invalid regex {pat_str!r}: {exc}"
                )

        for file in python_files:
            for pattern in compiled:
                if pattern.search(file.content):
                    msg = (
                        f"{file.path}: matched code safety rule '{rule_name}' "
                        f"with pattern {pattern.pattern!r}"
                    )
                    if rule.severity == "error":
                        report.errors.append(msg)
                        passed = False
                    else:
                        report.warnings.append(msg)

    return passed


# ── Internal helpers ──────────────────────────────────────────────────────────

def _validate_capability_metadata(
    skill: GeneratedSkill,
    report: ValidationReport,
    policy: ValidationPolicy,
) -> bool:
    metadata = skill.metadata
    cp = policy.capability
    taxonomy = frozenset(t.lower() for t in cp.operation_taxonomy)
    allowed_side_effects = frozenset(cp.allowed_side_effects)
    validation_passed = True

    if not metadata.domain:
        report.errors.append(
            "capability: 'domain' is required — policy engine cannot match domain context"
        )
        validation_passed = False

    if not metadata.supported_actions:
        report.errors.append(
            "capability: 'supported_actions' is required — policy engine cannot check action access"
        )
        validation_passed = False
    else:
        unknown_actions = [a for a in metadata.supported_actions if a.lower() not in taxonomy]
        if unknown_actions:
            report.warnings.append(
                f"capability: supported_actions contains non-taxonomy verbs: {unknown_actions!r} — "
                f"use verbs from policy capability.operation_taxonomy: {sorted(taxonomy)}"
            )
        unknown_forbidden = [a for a in metadata.forbidden_actions if a.lower() not in taxonomy]
        if unknown_forbidden:
            report.warnings.append(
                f"capability: forbidden_actions contains non-taxonomy verbs: {unknown_forbidden!r}"
            )

    unknown_side_effects = [se for se in metadata.side_effects if se not in allowed_side_effects]
    if unknown_side_effects:
        report.errors.append(
            f"capability: invalid side_effects values: {unknown_side_effects!r}. "
            f"Allowed by policy capability.allowed_side_effects: {sorted(allowed_side_effects)}"
        )
        validation_passed = False

    if not metadata.side_effects and any(
        keyword in metadata.description.lower()
        for keyword in ("write", "creat", "delet", "remov", "updat", "move", "archiv")
    ):
        report.warnings.append(
            "capability: 'side_effects' is empty but description suggests file/network operations"
        )

    return validation_passed


def _validate_no_external_dependencies(
    skill: GeneratedSkill,
    report: ValidationReport,
    policy: ValidationPolicy,
) -> bool:
    dp = policy.dependencies
    forbidden_files = frozenset(dp.forbidden_files)
    allowed_imports = frozenset(dp.allowed_imports)

    for file in skill.files:
        if file.path in forbidden_files:
            report.errors.append(
                f"'{file.path}' must not exist in a skill package — "
                "skills must be stdlib-only and must not declare external dependencies "
                "(policy: dependencies.forbidden_files)"
            )
            return False

    for file in skill.files:
        if not file.path.endswith(".py"):
            continue
        match = _THIRD_PARTY_IMPORT_RE.search(file.content)
        if match:
            pkg = match.group(1)
            if pkg in allowed_imports:
                continue
            report.errors.append(
                f"{file.path}: imports third-party package '{pkg}' — "
                "skills must use Python stdlib only "
                f"(add '{pkg}' to policy dependencies.allowed_imports to permit it)"
            )
            return False

    return True


def _build_placeholder_re(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    combined = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(combined)


def _find_live_public_url(value: str) -> str | None:
    for match in _PUBLIC_URL.findall(value):
        parsed_url = urlparse(match)
        host = (parsed_url.hostname or "").lower()
        if host and host not in {"localhost", "127.0.0.1"}:
            return match
    return None
