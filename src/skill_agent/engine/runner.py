from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.skill_agent.engine.credentials import CREDENTIAL_STORE
from src.skill_agent.process import SubprocessContract, run_command

NODE_CONTRACT = SubprocessContract(timeout_seconds=30)


class NodeExecutionError(RuntimeError):
    pass


def resolve_credential(ref: str | None) -> dict[str, Any]:
    if ref is None:
        return {}
    return CREDENTIAL_STORE.get(ref, {})


def run_node_script(
    node_path: str | Path,
    params: dict[str, Any],
    input_data: dict[str, Any],
    credential_ref: str | None,
) -> dict[str, Any]:
    path = Path(node_path)
    python_path = path / ".venv" / "bin" / "python"
    command = [str(python_path if python_path.exists() else sys.executable), "node.py"]
    payload = json.dumps(
        {
            "params": params,
            "input": input_data,
            "credentials": resolve_credential(credential_ref),
        },
        ensure_ascii=False,
    )

    try:
        env = os.environ.copy()
        project_root = str(Path(__file__).resolve().parents[3])
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            project_root
            if not existing_pythonpath
            else f"{project_root}{os.pathsep}{existing_pythonpath}"
        )
        result = run_command(
            command,
            contract=NODE_CONTRACT,
            operation_name=f"node {path.name}",
            input_text=payload,
            cwd=path,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise NodeExecutionError(f"Node timed out after {NODE_CONTRACT.timeout_seconds}s") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
        raise NodeExecutionError(detail)

    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NodeExecutionError(f"Node returned invalid JSON stdout: {result.stdout[:500]!r}") from exc
    if not isinstance(output, dict):
        raise NodeExecutionError("Node stdout must decode to a JSON object")
    return output
