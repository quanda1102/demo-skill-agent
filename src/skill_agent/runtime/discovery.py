from __future__ import annotations

from pathlib import Path

import yaml

from .models import RuntimeLog, SkillStub


def discover_skills(skills_dir: Path) -> tuple[list[SkillStub], list[RuntimeLog]]:
    logs: list[RuntimeLog] = []
    stubs: list[SkillStub] = []

    if not skills_dir.exists():
        logs.append(RuntimeLog("warning", "discovery", f"Skills directory not found: {skills_dir}"))
        return stubs, logs

    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            logs.append(RuntimeLog("warning", "discovery", f"Skipping '{entry.name}': no SKILL.md"))
            continue
        try:
            stub = _parse_stub(entry, skill_md)
            stubs.append(stub)
            logs.append(RuntimeLog("info", "discovery", f"Discovered '{stub.skill_id}': {stub.description[:80]}"))
        except Exception as exc:
            logs.append(RuntimeLog("warning", "discovery", f"Skipping '{entry.name}': {exc}"))

    return stubs, logs


def _parse_stub(skill_dir: Path, skill_md_path: Path) -> SkillStub:
    text = skill_md_path.read_text(encoding="utf-8")
    raw_fm = _extract_frontmatter(text)
    if raw_fm is None:
        raise ValueError("SKILL.md has no YAML frontmatter block")
    data = yaml.safe_load(raw_fm)
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    if not name:
        raise ValueError("frontmatter missing required field 'name'")
    if not description:
        raise ValueError("frontmatter missing required field 'description'")

    def _as_list(val: object) -> list[str]:
        if isinstance(val, list):
            return [str(v) for v in val]
        return []

    return SkillStub(
        skill_id=skill_dir.name,
        name=name,
        description=description,
        skill_dir=skill_dir,
        domain=_as_list(data.get("domain")),
        supported_actions=_as_list(data.get("supported_actions")),
        forbidden_actions=_as_list(data.get("forbidden_actions")),
        side_effects=_as_list(data.get("side_effects")),
    )


def _extract_frontmatter(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end].strip()
