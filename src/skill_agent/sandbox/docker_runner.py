from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from src.skill_agent.observability.logging_utils import get_logger
from src.skill_agent.schemas.skill_model import GeneratedSkill, SkillTestCase, ValidationReport, materialize_skill
from ..process import SubprocessContract, run_command
from .base_runner import match_output, format_test_result

CONTAINER_WORKSPACE = "/workspace"
DEFAULT_IMAGE = "skill-agent-sandbox:latest"
TIMEOUT_SECONDS = 10
LOGGER = get_logger("skill_agent.sandbox.docker")
DOCKER_HEALTH_CONTRACT = SubprocessContract(timeout_seconds=5)


class DockerSandboxRunner:
    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        timeout: int = TIMEOUT_SECONDS,
        memory_limit: str | None = "256m",
        cpus: float | None = 0.5,
        network: str = "none",
    ) -> None:
        self.image = image
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.network = network
        self._docker_ok: bool | None = None

    def run(self, skill: GeneratedSkill, report: ValidationReport) -> ValidationReport:
        if not _docker_available():
            report.errors.append(
                "Docker is not available or not running — "
                "install Docker and ensure the daemon is started, "
                "or use LocalSandboxRunner instead."
            )
            report.execution_pass = False
            report.regression_pass = False
            report.compute_publishable()
            return report

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
                result = self._run_test_case(skill_dir, tc)
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

    def _run_test_case(self, skill_dir: Path, tc: SkillTestCase) -> dict[str, str]:
        entrypoint = skill_dir / "scripts" / "run.py"
        if not entrypoint.exists():
            return {"outcome": "skip", "detail": "No scripts/run.py entrypoint found"}

        for rel_path, content in tc.fixtures.items():
            fixture_file = skill_dir / rel_path
            fixture_file.parent.mkdir(parents=True, exist_ok=True)
            fixture_file.write_text(content, encoding="utf-8")

        cmd = self._build_docker_cmd(skill_dir)

        try:
            proc = run_command(
                cmd,
                contract=SubprocessContract(timeout_seconds=self.timeout),
                operation_name=f"docker sandbox test '{tc.description}'",
                input_text=tc.input,
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
            _kill_timed_out_container(self.image)
            LOGGER.error("Docker sandbox test '%s' timed out after %ss.", tc.description, self.timeout)
            return {"outcome": "fail", "detail": f"Timed out after {self.timeout}s"}
        except Exception as exc:
            LOGGER.exception("Docker sandbox test '%s' failed unexpectedly.", tc.description)
            return {"outcome": "fail", "detail": str(exc)}

    def _build_docker_cmd(self, skill_dir: Path) -> list[str]:
        cmd = [
            "docker", "run",
            "--rm",
            "-i",
            "--network", self.network,
            "-v", f"{skill_dir}:{CONTAINER_WORKSPACE}",
            "-w", CONTAINER_WORKSPACE,
        ]
        if self.memory_limit:
            cmd += ["--memory", self.memory_limit]
        if self.cpus is not None:
            cmd += ["--cpus", str(self.cpus)]
        cmd += [self.image, "python", "scripts/run.py"]
        return cmd


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = run_command(
            ["docker", "info"],
            contract=DOCKER_HEALTH_CONTRACT,
            operation_name="docker availability check",
        )
        return result.returncode == 0
    except Exception:
        LOGGER.exception("Docker availability check failed.")
        return False


def _kill_timed_out_container(image: str) -> None:
    try:
        ids_result = run_command(
            ["docker", "ps", "-q", "--filter", f"ancestor={image}"],
            contract=DOCKER_HEALTH_CONTRACT,
            operation_name=f"docker ps for timed-out container cleanup ({image})",
        )
        for cid in ids_result.stdout.split():
            run_command(
                ["docker", "kill", cid],
                contract=DOCKER_HEALTH_CONTRACT,
                operation_name=f"docker kill {cid}",
            )
    except Exception:
        LOGGER.exception("Failed to clean up timed-out Docker containers for image '%s'.", image)