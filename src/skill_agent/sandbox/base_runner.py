from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.schemas.skill_model import GeneratedSkill, SkillTestCase, ValidationReport

LOGGER = get_logger("skill_agent.sandbox.base")


def match_output(actual: str, expected: str | None, method: str) -> bool:
    """Check if actual output matches expected output using the specified validation method."""
    if expected is None:
        return True
    if method == "string_match":
        return actual == expected.strip()
    if method == "contains":
        return expected.strip() in actual
    if method == "regex":
        return bool(re.search(expected.strip(), actual))
    return True


def format_test_result(
    tc: SkillTestCase,
    output: str,
    stderr: str,
    returncode: int,
    stdout_ok: bool,
    stderr_ok: bool,
    exit_ok: bool,
) -> dict[str, str]:
    """Format test case result - returns pass/fail dict with detail."""
    expected_exit_code = 0 if tc.expected_exit_code is None else tc.expected_exit_code

    if stdout_ok and stderr_ok and exit_ok:
        detail = output[:200] or stderr[:200] or f"exit={returncode}"
        return {"outcome": "pass", "detail": detail}

    parts = []
    if tc.expected_output and not stdout_ok:
        parts.append(
            f"expected stdout {tc.validation_method}: {tc.expected_output.strip()[:200]!r}"
        )
    if output:
        parts.append(f"stdout: {output[:200]}")
    if tc.expected_stderr is not None and not stderr_ok:
        parts.append(
            f"expected stderr {tc.validation_method}: {tc.expected_stderr.strip()[:200]!r}"
        )
    if stderr:
        parts.append(f"stderr: {stderr[:400]}")
    if not exit_ok:
        parts.append(f"expected exit={expected_exit_code}")
    if returncode != 0 or not exit_ok:
        parts.append(f"exit={returncode}")
    detail = " | ".join(parts) if parts else f"expected {tc.expected_output!r}, got {output!r}"
    return {"outcome": "fail", "detail": detail}


class BaseSandboxRunner(ABC):
    """Base class for sandbox runners. Handles test case iteration and result aggregation."""

    TIMEOUT_SECONDS: int

    @abstractmethod
    def _execute_test(self, skill_dir: Path, tc: SkillTestCase) -> dict[str, str]:
        """Execute a single test case. Implement in subclass."""
        pass

    def run(self, skill: GeneratedSkill, report: ValidationReport) -> ValidationReport:
        """Run all test cases for a skill."""
        if not skill.tests:
            report.warnings.append("No test cases defined — execution_pass skipped (vacuously true)")
            report.execution_pass = True
            report.regression_pass = True
            report.compute_publishable()
            return report

        passed = 0
        failed = 0

        for tc in skill.tests:
            result = self._execute_test(skill, tc)
            report.logs.append(f"[{tc.description}] {result['outcome']}: {result['detail']}")
            if result["outcome"] == "pass":
                passed += 1
            else:
                failed += 1
                report.errors.append(
                    f"Test failed: {tc.description} — {result['detail']}"
                )

        report.execution_pass = failed == 0
        report.regression_pass = True  # no prior versions in demo
        report.compute_publishable()
        return report

    def _run_test_case(self, skill_dir: Path, tc: SkillTestCase) -> dict[str, str]:
        """Shared test case execution logic: fixtures, entrypoint check, result formatting."""
        entrypoint = skill_dir / "scripts" / "run.py"
        if not entrypoint.exists():
            return {"outcome": "skip", "detail": "No scripts/run.py entrypoint found"}

        # Create fixture files
        for rel_path, content in tc.fixtures.items():
            fixture_file = skill_dir / rel_path
            fixture_file.parent.mkdir(parents=True, exist_ok=True)
            fixture_file.write_text(content, encoding="utf-8")

        return self._execute_and_validate(skill_dir, tc)

    @abstractmethod
    def _execute_and_validate(self, skill_dir: Path, tc: SkillTestCase) -> dict[str, str]:
        """Execute command and validate result. Implement in subclass."""
        pass


def run_tests_in_directory(
    skill: GeneratedSkill,
    skill_dir: Path,
    runner: BaseSandboxRunner,
    report: ValidationReport,
) -> ValidationReport:
    """Helper to run tests using a runner in a pre-created skill directory."""
    if not skill.tests:
        report.warnings.append("No test cases defined — execution_pass skipped (vacuously true)")
        report.execution_pass = True
        report.regression_pass = True
        report.compute_publishable()
        return report

    passed = 0
    failed = 0

    for tc in skill.tests:
        result = runner._run_test_case(skill_dir, tc)
        report.logs.append(f"[{tc.description}] {result['outcome']}: {result['detail']}")
        if result["outcome"] == "pass":
            passed += 1
        else:
            failed += 1
            report.errors.append(
                f"Test failed: {tc.description} — {result['detail']}"
            )

    report.execution_pass = failed == 0
    report.regression_pass = True
    report.compute_publishable()
    return report