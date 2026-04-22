from __future__ import annotations

from pathlib import Path
from typing import Callable

from .models import GeneratedSkill, PublishResult, ValidationReport, materialize_skill


class PublishGateway:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def evaluate(
        self,
        skill: GeneratedSkill,
        report: ValidationReport,
        reviewer: Callable[[GeneratedSkill, ValidationReport], bool] | None = None,
    ) -> PublishResult:
        if not report.publishable:
            return PublishResult(
                skill_name=skill.metadata.name,
                published=False,
                report=report,
                message=f"Rejected: {len(report.errors)} error(s) — {report.errors[:3]}",
            )

        if reviewer is not None:
            rejection_reason = reviewer(skill, report)
            if rejection_reason is not None:
                return PublishResult(
                    skill_name=skill.metadata.name,
                    published=False,
                    report=report,
                    message=f"Rejected by reviewer: {rejection_reason}",
                )

        skill_path = materialize_skill(skill, self.skills_dir)
        _stamp_published(skill_path)

        return PublishResult(
            skill_name=skill.metadata.name,
            published=True,
            skill_path=str(skill_path),
            report=report,
            message=f"Published to {skill_path}",
        )


def _stamp_published(skill_path: Path) -> None:
    """Rewrite status in SKILL.md frontmatter to 'published'."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return
    content = skill_md.read_text(encoding="utf-8")
    for old in ("status: generated", "status: validated", "status: draft"):
        content = content.replace(old, "status: published")
    skill_md.write_text(content, encoding="utf-8")
