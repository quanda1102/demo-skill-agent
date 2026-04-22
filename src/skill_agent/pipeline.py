from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .generator import Generator, SkillAgentError as GeneratorSkillAgentError
from .logging_utils import get_logger
from .models import PublishResult, SkillSpec, ValidationReport
from .provider import LLMProvider
from .publisher import PublishGateway
from .sandbox import LocalSandboxRunner, SandboxRunner
from .validator import StaticValidator

DEFAULT_MAX_RETRIES = 3
LOGGER = get_logger("skill_agent.pipeline")


@dataclass
class PipelineTrace:
    events: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.events.append(message)
        LOGGER.info(message)


def build_skill_from_spec(
    spec: SkillSpec,
    generator_provider: LLMProvider,
    skills_dir: Path,
    review_fn: Callable | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sandbox_runner: SandboxRunner | None = None,
) -> tuple[PublishResult, PipelineTrace]:
    trace = PipelineTrace()
    validator = StaticValidator()
    sandbox = sandbox_runner if sandbox_runner is not None else LocalSandboxRunner()
    skill = None
    report = None
    errors: list[str] = []

    for attempt in range(1, max_retries + 1):
        attempt_label = f"attempt {attempt}/{max_retries}"
        skill = None
        report = None

        trace.log(f"[2/5] Generating skill package ({attempt_label})...")
        try:
            skill = Generator(generator_provider).generate(spec, errors=errors or None)
        except GeneratorSkillAgentError as exc:
            errors = [str(exc)]
            for error in errors:
                trace.log(f"      ! {error}")
            if attempt < max_retries:
                trace.log(_feedback_block("generator", errors))
                continue
            trace.log(f"[!] Generator failed on final attempt: {exc}")
            break

        trace.log(f"      files: {[f.path for f in skill.files]}")

        trace.log(f"[3/5] Static validation ({attempt_label})...")
        report = validator.validate(skill)
        for name, passed in (
            ("syntax", report.syntax_pass),
            ("metadata", report.metadata_pass),
            ("activation", report.activation_pass),
        ):
            trace.log(f"      [{'PASS' if passed else 'FAIL'}] {name}")

        if not report.syntax_pass or not report.metadata_pass or not report.activation_pass:
            errors = list(report.errors)
            for error in errors:
                trace.log(f"      ! {error}")
            if attempt < max_retries:
                trace.log(_feedback_block("static validation", errors))
            continue

        trace.log(f"[4/5] Sandbox tests ({attempt_label})...")
        report = sandbox.run(skill, report)
        for name, passed in (
            ("execution", report.execution_pass),
            ("regression", report.regression_pass),
        ):
            trace.log(f"      [{'PASS' if passed else 'FAIL'}] {name}")
        for line in report.logs:
            trace.log(f"      {line}")

        if report.execution_pass:
            break

        errors = sandbox_errors(report)
        for error in report.errors:
            trace.log(f"      ! {error}")
        if attempt < max_retries:
            trace.log(_feedback_block("sandbox", errors))
    else:
        trace.log(f"[!] Could not produce a passing skill after {max_retries} attempts.")

    if skill is None or report is None:
        report = ValidationReport(
            errors=errors or ["Generation failed before a validation report was produced"]
        )
        report.compute_publishable()
        trace.log("[5/5] Evaluating publish eligibility...")
        return (
            PublishResult(
                skill_name=spec.name,
                published=False,
                report=report,
                message=f"Rejected before publish: {report.errors[0]}",
            ),
            trace,
        )

    trace.log("[5/5] Evaluating publish eligibility...")
    return PublishGateway(skills_dir).evaluate(skill, report, reviewer=review_fn), trace


def sandbox_errors(report: ValidationReport) -> list[str]:
    errors = list(report.errors)
    context = [
        "Sandbox environment: tests run sequentially in an isolated temp dir that starts with "
        "only the skill files (SKILL.md, scripts/run.py). No other files exist unless a previous "
        "test case created them. Fix test ordering and use consistent filenames across the sequence "
        "(e.g. create 'test-note.md' first, then read/delete that same file)."
    ]
    if any("stderr:" in error or "exit=" in error for error in errors):
        context.append(
            "Sandbox validates stdout, stderr, and exit code separately. For expected failures, "
            "set expected_stderr and/or expected_exit_code instead of expecting error text on stdout."
        )
    if any("http://" in error or "https://" in error for error in errors):
        context.append(
            "Avoid public network dependencies in tests. For URL-fetching skills, create local HTML "
            "fixtures and pass them via file:// URLs so results stay deterministic."
        )
    return context + errors


def _feedback_block(source: str, errors: list[str]) -> str:
    lines = [f"      ↻ Sending {source} feedback to generator ({len(errors)} error(s)):"] 
    lines.extend(f"        · {error}" for error in errors)
    return "\n".join(lines)
