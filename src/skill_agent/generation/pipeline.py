from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .generator import Generator, SkillAgentError as GeneratorSkillAgentError
from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.schemas.skill_model import PublishResult, SkillSpec, ValidationReport
from src.skill_agent.providers.provider import LLMProvider
from .publisher import PublishGateway
from src.skill_agent.sandbox import LocalSandboxRunner, SandboxRunner
from src.skill_agent.observability.trace_events import build_trace_event
from src.skill_agent.validation.policy import ValidationPolicy
from src.skill_agent.validation.validator import StaticValidator

DEFAULT_MAX_RETRIES = 3
LOGGER = get_logger("skill_agent.pipeline")


@dataclass
class PipelineTrace:
    events: list[dict[str, Any]] = field(default_factory=list)
    event_sink: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)

    def emit(self, entry: dict[str, Any]) -> None:
        normalized = dict(entry)
        normalized.setdefault("source", "pipeline")
        self.events.append(normalized)
        LOGGER.info("[pipeline.%s] %s", normalized.get("kind", "info"), normalized.get("msg", ""))
        if self.event_sink is not None:
            self.event_sink(normalized)

    def log(self, kind: str, msg: str, **data: Any) -> None:
        self.emit(build_trace_event("pipeline", kind, msg=msg, **data))


def build_skill_from_spec(
    spec: SkillSpec,
    generator_provider: LLMProvider,
    skills_dir: Path,
    review_fn: Callable | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sandbox_runner: SandboxRunner | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    policy: ValidationPolicy | None = None,
) -> tuple[PublishResult, PipelineTrace]:
    trace = PipelineTrace(event_sink=event_sink)
    validator = StaticValidator(policy=policy)
    sandbox = sandbox_runner if sandbox_runner is not None else LocalSandboxRunner()
    skill = None
    report = None
    errors: list[str] = []

    for attempt in range(1, max_retries + 1):
        skill = None
        report = None

        trace.log("stage", "Generating skill package", stage_num=2, stage="generate", attempt=attempt, max=max_retries)
        try:
            generator = Generator(generator_provider)
            generator.event_sink = trace.emit
            skill = generator.generate(spec, errors=errors or None)
        except GeneratorSkillAgentError as exc:
            errors = [str(exc)]
            for error in errors:
                trace.log("error", error)
            if attempt < max_retries:
                trace.log(
                    "feedback",
                    f"Sending generator feedback ({len(errors)} error(s))",
                    feedback_source="generator",
                    errors=errors,
                )
                continue
            trace.log("error", f"Generator failed on final attempt: {exc}")
            break

        trace.log("files", f"files: {[f.path for f in skill.files]}", files=[f.path for f in skill.files])

        trace.log("stage", "Static validation", stage_num=3, stage="validate", attempt=attempt, max=max_retries)
        report = validator.validate(skill)
        for name, passed in (
            ("syntax", report.syntax_pass),
            ("metadata", report.metadata_pass),
            ("activation", report.activation_pass),
        ):
            trace.log("check", f"{'PASS' if passed else 'FAIL'}: {name}", name=name, status="pass" if passed else "fail")

        if not report.syntax_pass or not report.metadata_pass or not report.activation_pass:
            errors = list(report.errors)
            for error in errors:
                trace.log("error", error)
            if attempt < max_retries:
                trace.log(
                    "feedback",
                    f"Sending static-validation feedback ({len(errors)} error(s))",
                    feedback_source="static validation",
                    errors=errors,
                )
            continue

        trace.log("stage", "Sandbox tests", stage_num=4, stage="sandbox", attempt=attempt, max=max_retries)
        for index, tc in enumerate(skill.tests, start=1):
            trace.log(
                "sandbox_case",
                f"Case {index}/{len(skill.tests)}: {tc.description}",
                case_index=index,
                total_cases=len(skill.tests),
                description=tc.description,
                rationale=_sandbox_case_rationale(tc),
                expectation=_sandbox_case_expectation(tc),
                input_preview=_preview_text(tc.input),
                fixture_paths=sorted(tc.fixtures),
            )
        report = sandbox.run(skill, report)
        for name, passed in (
            ("execution", report.execution_pass),
            ("regression", report.regression_pass),
        ):
            trace.log("check", f"{'PASS' if passed else 'FAIL'}: {name}", name=name, status="pass" if passed else "fail")
        for line in report.logs:
            trace.log("info", line)

        if report.execution_pass:
            break

        errors = sandbox_errors(report)
        for error in report.errors:
            trace.log("error", error)
        if attempt < max_retries:
            trace.log(
                "feedback",
                f"Sending sandbox feedback ({len(errors)} error(s))",
                feedback_source="sandbox",
                errors=errors,
            )
    else:
        trace.log("error", f"Could not produce a passing skill after {max_retries} attempts")

    if skill is None or report is None:
        report = ValidationReport(
            errors=errors or ["Generation failed before a validation report was produced"]
        )
        report.compute_publishable()
        trace.log("stage", "Evaluating publish eligibility", stage_num=5, stage="publish")
        return (
            PublishResult(
                skill_name=spec.name,
                published=False,
                report=report,
                message=f"Rejected before publish: {report.errors[0]}",
            ),
            trace,
        )

    trace.log("stage", "Evaluating publish eligibility", stage_num=5, stage="publish")
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


def _preview_text(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def _sandbox_case_expectation(tc) -> str:
    parts: list[str] = []
    if tc.expected_output:
        parts.append(f"stdout {tc.validation_method} {tc.expected_output.strip()!r}")
    elif tc.validation_method == "manual":
        parts.append("manual output review")
    else:
        parts.append(f"stdout {tc.validation_method}")
    if tc.expected_stderr is not None:
        parts.append(f"stderr {tc.validation_method} {tc.expected_stderr.strip()!r}")
    expected_exit_code = 0 if tc.expected_exit_code is None else tc.expected_exit_code
    parts.append(f"exit {expected_exit_code}")
    return "; ".join(parts)


def _sandbox_case_rationale(tc) -> str:
    reasons: list[str] = []
    if tc.expected_stderr is not None or (tc.expected_exit_code is not None and tc.expected_exit_code != 0):
        reasons.append("exercise an expected failure path")
    elif tc.validation_method == "string_match":
        reasons.append("verify exact stdout for deterministic behavior")
    elif tc.validation_method == "contains":
        reasons.append("verify the key stdout fragment without requiring a byte-for-byte match")
    elif tc.validation_method == "regex":
        reasons.append("verify the stdout shape with a regex")
    elif tc.validation_method == "manual":
        reasons.append("record the case even though sandbox can only verify successful execution")
    if tc.fixtures:
        reasons.append(
            f"preload {len(tc.fixtures)} fixture file(s) because the skill expects existing files in the sandbox"
        )
    return "; ".join(reasons) or "validate the behavior described by the test case"
