from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from src.skill_agent.observability.logging_utils import get_logger
from ..process import SubprocessContract, run_command
from .models import ExecutionResult, ExecutionStatus, LoadedSkill, RuntimeLog, TaskStatus

_TIMEOUT_SECONDS = 30
LOGGER = get_logger("skill_agent.runtime.executor")
EXECUTION_CONTRACT = SubprocessContract(timeout_seconds=_TIMEOUT_SECONDS)


def execute_skill(
    skill: LoadedSkill,
    input_data: str,
    expected_output: str | None = None,
    validation: str = "string_match",
    cwd: str | Path | None = None,
) -> ExecutionResult:
    logs: list[RuntimeLog] = []
    skill_id = skill.stub.skill_id

    if skill.run_script is None:
        logs.append(RuntimeLog("warning", "execution", f"No run script for '{skill_id}' — nothing to execute"))
        return ExecutionResult(
            status="no_script",
            stdout="",
            stderr="",
            exit_code=-1,
            skill_id=skill_id,
            logs=logs,
            execution_status=ExecutionStatus.skipped,
            task_status=TaskStatus.not_applicable,
        )

    logs.append(
        RuntimeLog("info", "execution", f"Executing '{skill_id}' with {len(input_data)} bytes of input")
    )
    if cwd is not None:
        logs.append(RuntimeLog("info", "execution", f"Working directory: {Path(cwd)}"))

    try:
        proc = run_command(
            ["python", str(skill.run_script)],
            contract=EXECUTION_CONTRACT,
            operation_name=f"runtime execute '{skill_id}'",
            input_text=input_data,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        LOGGER.error("Runtime execution for '%s' timed out after %ss.", skill_id, _TIMEOUT_SECONDS)
        logs.append(RuntimeLog("error", "execution", f"'{skill_id}' timed out after {_TIMEOUT_SECONDS}s"))
        return ExecutionResult(
            status="error",
            stdout="",
            stderr=f"execution timed out after {_TIMEOUT_SECONDS}s",
            exit_code=-1,
            skill_id=skill_id,
            logs=logs,
            execution_status=ExecutionStatus.failed,
            task_status=TaskStatus.unknown,
        )
    except Exception as exc:
        LOGGER.exception("Runtime execution for '%s' failed to launch.", skill_id)
        logs.append(RuntimeLog("error", "execution", f"Failed to launch '{skill_id}': {exc}"))
        return ExecutionResult(
            status="error",
            stdout="",
            stderr=str(exc),
            exit_code=-1,
            skill_id=skill_id,
            logs=logs,
            execution_status=ExecutionStatus.failed,
            task_status=TaskStatus.unknown,
        )

    status: Literal["ok", "error"] = "ok" if proc.returncode == 0 else "error"
    exec_status = ExecutionStatus.succeeded if status == "ok" else ExecutionStatus.failed
    log_level = "info" if status == "ok" else "error"
    logs.append(RuntimeLog(log_level, "execution", f"'{skill_id}' exited {proc.returncode}"))  # type: ignore[arg-type]

    task_status = TaskStatus.unknown
    if proc.returncode == 0 and expected_output is not None:
        actual = proc.stdout.strip()
        expected = expected_output.strip()
        if validation == "string_match":
            matched = actual == expected
        elif validation == "contains":
            matched = expected in actual
        else:
            matched = False
        task_status = TaskStatus.satisfied if matched else TaskStatus.incorrect
        result_word = "satisfied" if matched else "incorrect"
        logs.append(RuntimeLog(
            "info" if matched else "warning",
            "execution",
            f"Task validation ({validation}): {result_word}",
        ))

    return ExecutionResult(
        status=status,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        skill_id=skill_id,
        logs=logs,
        execution_status=exec_status,
        task_status=task_status,
    )
