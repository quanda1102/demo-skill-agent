from __future__ import annotations

from src.skill_agent.schemas.skill_model import GeneratedSkill, ValidationReport
from src.skill_agent.validation.checks import (
    validate_skill_activation,
    validate_skill_metadata,
    validate_skill_syntax,
    validate_skill_test_cases,
)


class StaticValidator:
    def validate(self, skill: GeneratedSkill) -> ValidationReport:
        report = ValidationReport()
        report.syntax_pass = validate_skill_syntax(skill, report)
        report.metadata_pass = validate_skill_metadata(skill, report)
        report.activation_pass = validate_skill_activation(skill, report)
        if report.activation_pass:
            report.activation_pass = validate_skill_test_cases(skill, report)
        report.compute_publishable()
        return report
