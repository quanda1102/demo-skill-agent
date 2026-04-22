from __future__ import annotations

import re
from urllib.parse import urlparse

import yaml

from .models import GeneratedSkill, Runtime, SkillStatus, ValidationReport

_PLACEHOLDER_PATTERNS = re.compile(r"\bTODO\b|\bFIXME\b|\bPLACEHOLDER\b|<[^>]+>")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_VERB_SUFFIXES = re.compile(
    r"\b\w+(s|es|ed|ing|ize|ise|ate|ify|en)\b", re.IGNORECASE
)
_PUBLIC_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)

# Canonical operation taxonomy. Actions outside this set trigger a warning.
_OPERATION_TAXONOMY: frozenset[str] = frozenset({
    # CRUD
    "create", "read", "update", "delete",
    # File management
    "list", "move", "copy", "rename", "archive", "extract",
    # Text / data processing
    "count", "search", "summarize", "parse", "format",
    "validate", "transform", "convert", "encode", "decode",
    "sort", "filter", "split", "join", "hash",
    # I/O
    "fetch", "write", "append",
})

# Closed set — values outside this are an error.
_SIDE_EFFECTS_VALUES: frozenset[str] = frozenset({
    "file_read", "file_write", "file_delete", "network", "subprocess",
})

# Dependency files that must not appear in a skill package.
_FORBIDDEN_DEP_FILES: frozenset[str] = frozenset({
    "requirements.txt", "setup.py", "pyproject.toml", "setup.cfg", "Pipfile",
})

# Third-party packages that skills must not import. Stdlib look-alikes
# (e.g. urllib vs urllib3) are deliberately listed to catch common mistakes.
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


def _parse_frontmatter(content: str) -> dict | None:
    """Extract and parse YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None


class StaticValidator:
    def validate(self, skill: GeneratedSkill) -> ValidationReport:
        report = ValidationReport()
        report.syntax_pass = self._check_syntax(skill, report)
        report.metadata_pass = self._check_metadata(skill, report)
        report.activation_pass = self._check_activation(skill, report)
        if report.activation_pass:
            report.activation_pass = self._check_test_cases(skill, report)
        report.compute_publishable()
        return report

    def _check_syntax(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        paths = {f.path for f in skill.files}

        if "SKILL.md" not in paths:
            report.errors.append("SKILL.md is missing from generated files")
            return False

        skill_md = next(f for f in skill.files if f.path == "SKILL.md")
        fm = _parse_frontmatter(skill_md.content)
        if fm is None:
            report.errors.append("SKILL.md has invalid or missing YAML frontmatter")
            return False

        for key in ("name", "description"):
            if key not in fm:
                report.errors.append(f"SKILL.md frontmatter missing required key: {key}")
                return False

        seen: set[str] = set()
        for f in skill.files:
            if f.path in seen:
                report.errors.append(f"Duplicate file path in generated skill: {f.path}")
                return False
            seen.add(f.path)

        for category, refs in (
            ("scripts", skill.scripts),
            ("references", skill.references),
            ("assets", skill.assets),
        ):
            for ref in refs:
                if ref not in paths:
                    report.errors.append(
                        f"{category} entry '{ref}' not found in files list"
                    )
                    return False

        return True

    def _check_metadata(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        paths = {f.path for f in skill.files}
        if "SKILL.md" not in paths:
            report.errors.append("Cannot check metadata: SKILL.md missing")
            return False

        skill_md = next(f for f in skill.files if f.path == "SKILL.md")
        fm = _parse_frontmatter(skill_md.content)
        if not fm:
            report.errors.append("Cannot check metadata: frontmatter unparseable")
            return False

        if fm.get("name") != skill.metadata.name:
            report.errors.append(
                f"Metadata name mismatch: frontmatter has '{fm.get('name')}', "
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
            report.warnings.append(
                f"Version '{skill.metadata.version}' does not match semver (x.y.z)"
            )

        if not skill.metadata.entrypoints:
            report.errors.append("Entrypoints list is empty")
            return False

        has_skill_md_entry = any(
            ep.get("path") == "SKILL.md" for ep in skill.metadata.entrypoints
        )
        if not has_skill_md_entry:
            report.errors.append("No entrypoint pointing to SKILL.md")
            return False

        return True

    def _check_capability_metadata(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        """Validate capability metadata fields required by the runtime policy layer."""
        m = skill.metadata
        ok = True

        if not m.domain:
            report.errors.append(
                "capability: 'domain' is required — policy engine cannot match domain context"
            )
            ok = False

        if not m.supported_actions:
            report.errors.append(
                "capability: 'supported_actions' is required — policy engine cannot check action access"
            )
            ok = False
        else:
            # Warn about actions outside the canonical taxonomy.
            unknown = [a for a in m.supported_actions if a.lower() not in _OPERATION_TAXONOMY]
            if unknown:
                report.warnings.append(
                    f"capability: supported_actions contains non-taxonomy verbs: {unknown!r} — "
                    f"use verbs from the taxonomy: {sorted(_OPERATION_TAXONOMY)}"
                )
            unknown_fb = [a for a in m.forbidden_actions if a.lower() not in _OPERATION_TAXONOMY]
            if unknown_fb:
                report.warnings.append(
                    f"capability: forbidden_actions contains non-taxonomy verbs: {unknown_fb!r}"
                )

        # side_effects is a closed enum — unknown values are an error.
        unknown_se = [s for s in m.side_effects if s not in _SIDE_EFFECTS_VALUES]
        if unknown_se:
            report.errors.append(
                f"capability: invalid side_effects values: {unknown_se!r}. "
                f"Allowed: {sorted(_SIDE_EFFECTS_VALUES)}"
            )
            ok = False

        if not m.side_effects and any(
            kw in skill.metadata.description.lower()
            for kw in ("write", "creat", "delet", "remov", "updat", "move", "archiv")
        ):
            report.warnings.append(
                "capability: 'side_effects' is empty but description suggests file/network operations"
            )

        return ok

    def _check_no_external_deps(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        """Reject dependency declaration files and non-stdlib imports in Python scripts."""
        for f in skill.files:
            if f.path in _FORBIDDEN_DEP_FILES:
                report.errors.append(
                    f"'{f.path}' must not exist in a skill package — "
                    "skills must be stdlib-only and must not declare external dependencies"
                )
                return False

        for f in skill.files:
            if not f.path.endswith(".py"):
                continue
            m = _THIRD_PARTY_IMPORT_RE.search(f.content)
            if m:
                report.errors.append(
                    f"{f.path}: imports third-party package '{m.group(1)}' — "
                    "skills must use Python stdlib only"
                )
                return False

        return True

    def _check_activation(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        if not self._check_capability_metadata(skill, report):
            return False
        if not self._check_no_external_deps(skill, report):
            return False
        desc = skill.metadata.description
        if len(desc) < 20:
            report.errors.append(
                f"Description too short ({len(desc)} chars); minimum is 20"
            )
            return False

        if len(desc) > 500:
            report.warnings.append(
                f"Description is long ({len(desc)} chars); consider trimming below 500"
            )

        if _PLACEHOLDER_PATTERNS.search(desc):
            report.errors.append("Description contains placeholder text (TODO/FIXME/<...>)")
            return False

        if not _VERB_SUFFIXES.search(desc):
            report.warnings.append(
                "Description may lack an action verb; consider making it more operational"
            )

        if not skill.spec.purpose:
            report.warnings.append("spec.purpose is empty; activation quality may be low")

        return True

    def _check_test_cases(self, skill: GeneratedSkill, report: ValidationReport) -> bool:
        seen_descriptions: set[str] = set()
        for tc in skill.tests:
            if tc.description in seen_descriptions:
                report.errors.append(
                    f"Duplicate test case description: {tc.description!r}"
                )
                return False
            seen_descriptions.add(tc.description)

            live_url = self._find_live_public_url(tc.input)
            if live_url:
                report.errors.append(
                    "Test cases must be deterministic and self-contained; "
                    f"{tc.description!r} uses live URL {live_url!r}. "
                    "Use fixtures plus a file:// URL instead."
                )
                return False

        return True

    def _find_live_public_url(self, value: str) -> str | None:
        for match in _PUBLIC_URL.findall(value):
            parsed = urlparse(match)
            host = (parsed.hostname or "").lower()
            if host and host not in {"localhost", "127.0.0.1"}:
                return match
        return None
