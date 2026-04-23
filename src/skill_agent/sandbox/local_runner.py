from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.schemas.skill_model import GeneratedSkill, ValidationReport, materialize_skill
from ..process import SubprocessContract, run_command
from .base_runner import match_output, format_test_result

TIMEOUT_SECONDS = 10
LOGGER = get_logger("skill_agent.sandbox.local")
LOCAL_SANDBOX_CONTRACT = SubprocessContract(timeout_seconds=TIMEOUT_SECONDS)


class LocalSandboxRunner:
    def run(self, skill: GeneratedSkill, report: ValidationReport) -> ValidationReport:
        if not skill.tests:
            report.warnings.append("No test cases defined — execution_pass skipped (vacuously true)")
            report.execution_pass = True
            report.regression_pass = True
            report.compute_publishable()
            return report

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = materialize_skill(skill, Path(tmp))
            passed = 0
            failed = 0

            for tc in skill.tests:
                result = _run_test_case(skill_dir, tc)
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


def _run_test_case(skill_dir: Path, tc) -> dict[str, str]:
    entrypoint = skill_dir / "scripts" / "run.py"
    if not entrypoint.exists():
        return {"outcome": "skip", "detail": "No scripts/run.py entrypoint found"}

    for rel_path, content in tc.fixtures.items():
        fixture_file = skill_dir / rel_path
        fixture_file.parent.mkdir(parents=True, exist_ok=True)
        fixture_file.write_text(content, encoding="utf-8")

    try:
        proc = run_command(
            ["python", str(entrypoint)],
            contract=LOCAL_SANDBOX_CONTRACT,
            operation_name=f"local sandbox test '{tc.description}'",
            input_text=tc.input,
            cwd=skill_dir,
        )
        output = proc.stdout.strip()
        stderr = proc.stderr.strip()

        expected_exit_code = 0 if tc.expected_exit_code is None else tc.expected_exit_code
        stdout_ok = match_output(output, tc.expected_output, tc.validation_method)
        if tc.expected_stderr is None:
            stderr_ok = stderr == "" if expected_exit_code == 0 else True
        else:
            stderr_ok = match_output(stderr, tc.expected_stderr, tc.validation_method)
        exit_ok = proc.returncode == expected_exit_code

        return format_test_result(
            tc, output, stderr, proc.returncode,
            stdout_ok, stderr_ok, exit_ok,
        )
    except subprocess.TimeoutExpired:
        LOGGER.error("Local sandbox test '%s' timed out after %ss.", tc.description, TIMEOUT_SECONDS)
        return {"outcome": "fail", "detail": f"Timed out after {TIMEOUT_SECONDS}s"}
    except Exception as exc:
        LOGGER.exception("Local sandbox test '%s' failed unexpectedly.", tc.description)
        return {"outcome": "fail", "detail": str(exc)}