"""
Docker-based sandbox runner for isolated skill execution.

Usage
-----
Build the sandbox image once:

    docker build -f docker/Dockerfile -t skill-agent-sandbox:latest .

Then use DockerSandboxRunner in place of (or alongside) LocalSandboxRunner:

    from skill_agent.sandbox import DockerSandboxRunner
    runner = DockerSandboxRunner()
    report = runner.run(skill, report)

The runner mounts the skill workspace into /workspace inside the container and
executes `python scripts/run.py` with network disabled by default.

Each test case reuses the same mounted directory, so files written by one test
are visible to subsequent tests — matching the LocalSandboxRunner behavior.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..logging_utils import get_logger
from ..models import GeneratedSkill, SkillTestCase, ValidationReport, materialize_skill
from ..process import SubprocessContract, run_command
from .local_runner import TIMEOUT_SECONDS, _matches

# Path inside the container where the skill workspace is mounted.
CONTAINER_WORKSPACE = "/workspace"

DEFAULT_IMAGE = "skill-agent-sandbox:latest"
LOGGER = get_logger("skill_agent.sandbox.docker")
DOCKER_HEALTH_CONTRACT = SubprocessContract(timeout_seconds=5)


class DockerSandboxRunner:
    """
    Runs skill tests inside a Docker container.

    Network is disabled by default (--network none). Each test in a run shares
    the same mounted temp directory, preserving the stateful fixture semantics
    of LocalSandboxRunner.

    Parameters
    ----------
    image:
        Docker image to use. Must have Python available at /usr/bin/python or
        the PATH equivalent. Build with: docker build -f docker/Dockerfile .
    timeout:
        Per-test timeout in seconds.
    memory_limit:
        Docker --memory flag value (e.g. "256m"). None = no limit.
    cpus:
        Docker --cpus flag value (e.g. 0.5 for half a core). None = no limit.
    network:
        Docker --network value. Defaults to "none" (fully isolated).
        Pass "bridge" only if the skill explicitly requires outbound access.
    """

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
        self._docker_ok: bool | None = None  # cached after first check

    # ------------------------------------------------------------------
    # Public API — same shape as LocalSandboxRunner
    # ------------------------------------------------------------------

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

        # All tests share one temp dir so fixture files created by an earlier
        # test are visible to later tests (same semantic as LocalSandboxRunner).
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
        report.regression_pass = True  # no prior versions in demo
        report.compute_publishable()
        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
            stdout_ok = _matches(output, tc.expected_output, tc.validation_method)
            if tc.expected_stderr is None:
                stderr_ok = stderr == "" if expected_exit_code == 0 else True
            else:
                stderr_ok = _matches(stderr, tc.expected_stderr, tc.validation_method)
            exit_ok = proc.returncode == expected_exit_code
            ok = stdout_ok and stderr_ok and exit_ok

            if ok:
                detail = output[:200] or stderr[:200] or f"exit={proc.returncode}"
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
            if proc.returncode != 0 or not exit_ok:
                parts.append(f"exit={proc.returncode}")
            detail = " | ".join(parts) if parts else f"expected {tc.expected_output!r}, got {output!r}"
            return {"outcome": "fail", "detail": detail}

        except subprocess.TimeoutExpired:
            # Kill the container on timeout to avoid orphan processes.
            _kill_timed_out_container(self.image)
            LOGGER.error("Docker sandbox test '%s' timed out after %ss.", tc.description, self.timeout)
            return {"outcome": "fail", "detail": f"Timed out after {self.timeout}s"}
        except Exception as exc:
            LOGGER.exception("Docker sandbox test '%s' failed unexpectedly.", tc.description)
            return {"outcome": "fail", "detail": str(exc)}

    def _build_docker_cmd(self, skill_dir: Path) -> list[str]:
        cmd = [
            "docker", "run",
            "--rm",          # remove container after exit
            "-i",            # keep stdin open so we can pipe input
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


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _docker_available() -> bool:
    """Return True if Docker CLI is present and the daemon is reachable."""
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
    """Best-effort attempt to stop any running container from this image."""
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
