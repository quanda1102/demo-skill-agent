from __future__ import annotations

import json
import subprocess
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.skill_agent.agent import AgentLoop, Tool
from src.skill_agent.engine.registry import NodeEntry, get_registry_manifest, register_node
from src.skill_agent.engine.runner import run_node_script
from src.skill_agent.providers.provider import LLMProvider


@dataclass
class NodeBuildResult:
    ok: bool
    node_type: str
    message: str
    entry: NodeEntry | None = None


class NodeBuilderAgent:
    """
    ReAct-style builder for missing node types.

    The model gets tools to inspect the contract, write files, run sandbox tests,
    and register the node. The actual filesystem write/test/register operations
    are deterministic Python tools so the engine boundary stays enforceable.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        root_dir: Path | None = None,
        max_attempts: int = 5,
    ) -> None:
        self.provider = provider
        self.root_dir = root_dir or Path(__file__).resolve().parents[1] / "engine" / "nodes" / "agent_built"
        self.max_attempts = max_attempts
        self._drafts: dict[str, NodeEntry] = {}

    def build(
        self,
        node_type: str,
        description: str,
        *,
        params_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> NodeBuildResult:
        if self.provider is None:
            return self._build_demo_template(
                node_type,
                description,
                params_schema=params_schema or {},
                output_schema=output_schema or {},
            )

        loop = AgentLoop(
            provider=self.provider,
            tools=self._tools(),
            stop_on="register_node",
        )
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "node_type": node_type,
                        "description": description,
                        "params_schema": params_schema or {},
                        "output_schema": output_schema or {},
                        "max_attempts": self.max_attempts,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        result = loop.run_turn(messages)
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            return NodeBuildResult(False, node_type, f"Node builder returned non-JSON result: {result.content}")

        if payload.get("status") != "registered":
            return NodeBuildResult(False, node_type, payload.get("detail", result.content))

        entry = self._drafts.get(node_type)
        return NodeBuildResult(True, node_type, payload.get("detail", "Node registered."), entry)

    def _tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_node_contract",
                description="Return the node stdin/stdout contract and current registry manifest.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                fn=self._get_node_contract,
            ),
            Tool(
                name="write_node_files",
                description="Write node.py and requirements.txt for a candidate node implementation.",
                parameters={
                    "type": "object",
                    "required": [
                        "node_type",
                        "description",
                        "node_py",
                        "requirements_txt",
                        "params_schema",
                        "output_schema",
                    ],
                    "properties": {
                        "node_type": {"type": "string"},
                        "description": {"type": "string"},
                        "node_py": {"type": "string"},
                        "requirements_txt": {"type": "string"},
                        "params_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    },
                },
                fn=self._write_node_files,
            ),
            Tool(
                name="test_node",
                description="Run the candidate node with mock params/input and verify stdout is a JSON object.",
                parameters={
                    "type": "object",
                    "required": ["node_type", "mock_payload"],
                    "properties": {
                        "node_type": {"type": "string"},
                        "mock_payload": {
                            "type": "object",
                            "properties": {
                                "params": {"type": "object"},
                                "input": {"type": "object"},
                            },
                            "required": ["params", "input"],
                        },
                    },
                },
                fn=self._test_node,
            ),
            Tool(
                name="register_node",
                description="Register a candidate node after tests pass.",
                parameters={
                    "type": "object",
                    "required": ["node_type"],
                    "properties": {"node_type": {"type": "string"}},
                },
                fn=self._register_node,
            ),
        ]

    def _system_prompt(self) -> str:
        return """
You are Node Builder Agent.

Build one reusable Python node at a time. Follow this exact contract:
- stdin is JSON with keys: params, input, credentials
- stdout must be one JSON object
- exit 0 means success
- non-zero exit with stderr means failure

ReAct loop:
1. Call get_node_contract.
2. Call write_node_files with node.py and requirements.txt.
3. Call test_node using realistic mock params/input.
4. If the test fails, repair by calling write_node_files again, then test_node again.
5. Only call register_node after test_node returns status=pass.

Do not include markdown fences in node_py.
""".strip()

    def _get_node_contract(self) -> str:
        return json.dumps(
            {
                "stdin": {"params": {}, "input": {}, "credentials": {}},
                "stdout": "valid JSON object",
                "registry": get_registry_manifest(),
            },
            ensure_ascii=False,
        )

    def _write_node_files(
        self,
        node_type: str,
        description: str,
        node_py: str,
        requirements_txt: str,
        params_schema: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str:
        node_dir = self.root_dir / node_type
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "node.py").write_text(node_py, encoding="utf-8")
        (node_dir / "requirements.txt").write_text(requirements_txt, encoding="utf-8")

        try:
            entry = NodeEntry(
                node_type=node_type,
                description=description,
                path=str(node_dir.resolve()),
                params_schema=params_schema,
                output_schema=output_schema,
                built_by="agent",
            )
        except ValidationError as exc:
            return json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False)

        self._drafts[node_type] = entry
        install_result = self._install_requirements(node_dir, requirements_txt)
        return json.dumps(
            {
                "status": "written" if install_result is None else "error",
                "node_type": node_type,
                "path": str(node_dir),
                "detail": install_result or "Files written.",
            },
            ensure_ascii=False,
        )

    def _test_node(self, node_type: str, mock_payload: dict[str, Any]) -> str:
        entry = self._drafts.get(node_type)
        if entry is None:
            return json.dumps({"status": "fail", "detail": "write_node_files must be called first"})

        try:
            output = run_node_script(
                entry.path,
                mock_payload.get("params", {}),
                mock_payload.get("input", {}),
                None,
            )
            return json.dumps({"status": "pass", "output": output}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "fail", "detail": str(exc)}, ensure_ascii=False)

    def _register_node(self, node_type: str) -> str:
        entry = self._drafts.get(node_type)
        if entry is None:
            return json.dumps({"status": "error", "detail": "No candidate node exists."}, ensure_ascii=False)
        register_node(entry)
        return json.dumps(
            {
                "status": "registered",
                "node_type": node_type,
                "detail": f"Node '{node_type}' registered.",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _install_requirements(node_dir: Path, requirements_txt: str) -> str | None:
        if not requirements_txt.strip():
            return None

        venv_dir = node_dir / ".venv"
        if not venv_dir.exists():
            venv.create(venv_dir, with_pip=True)
        pip = venv_dir / "bin" / "pip"
        try:
            subprocess.run(
                [str(pip), "install", "-r", str(node_dir / "requirements.txt")],
                cwd=node_dir,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            return exc.stderr.strip() or exc.stdout.strip() or str(exc)
        except Exception as exc:
            return str(exc)
        return None

    def _build_demo_template(
        self,
        node_type: str,
        description: str,
        *,
        params_schema: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> NodeBuildResult:
        template = _DEMO_NODE_TEMPLATES.get(node_type)
        if template is None:
            return NodeBuildResult(
                ok=False,
                node_type=node_type,
                message="NodeBuilderAgent requires an LLM provider for non-demo node generation.",
            )

        write_result = json.loads(
            self._write_node_files(
                node_type=node_type,
                description=description or template["description"],
                node_py=template["node_py"],
                requirements_txt="",
                params_schema=params_schema or template["params_schema"],
                output_schema=output_schema or template["output_schema"],
            )
        )
        if write_result.get("status") != "written":
            return NodeBuildResult(False, node_type, write_result.get("detail", "Failed to write node."))

        test_result = json.loads(
            self._test_node(
                node_type,
                {"params": template["mock_params"], "input": template["mock_input"]},
            )
        )
        if test_result.get("status") != "pass":
            return NodeBuildResult(False, node_type, test_result.get("detail", "Generated node failed test."))

        register_result = json.loads(self._register_node(node_type))
        entry = self._drafts.get(node_type)
        return NodeBuildResult(
            ok=register_result.get("status") == "registered",
            node_type=node_type,
            message=register_result.get("detail", "Demo node registered."),
            entry=entry,
        )


_CHECK_IO_STAT_NODE = r'''
from __future__ import annotations

import json
import re
import sys


def _parse_max_util(text: str) -> float:
    values = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Device") or stripped.startswith("Linux"):
            continue
        parts = stripped.split()
        if not parts:
            continue
        try:
            values.append(float(parts[-1]))
        except ValueError:
            match = re.search(r"(\d+(?:\.\d+)?)\s*$", stripped)
            if match:
                values.append(float(match.group(1)))
    return max(values) if values else 0.0


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]
    sample_output = params.get(
        "sample_output",
        "Device r/s w/s rkB/s wkB/s rrqm/s wrqm/s %util\nsda 1 2 3 4 0 0 95.4",
    )
    threshold = float(params.get("threshold", 90))
    max_util = _parse_max_util(sample_output)
    print(
        json.dumps(
            {
                **input_data,
                "metric": "io_util_percent",
                "value": max_util,
                "threshold": threshold,
                "passed": max_util > threshold,
                "conclusion": "io_bottleneck" if max_util > threshold else "io_normal",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
'''.strip()


_CHECK_NFS_MOUNT_NODE = r'''
from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    params = payload["params"]
    input_data = payload["input"]
    timeout_seconds = float(params.get("timeout_seconds", 3))
    simulated_hang = bool(params.get("simulate_hang", True))
    print(
        json.dumps(
            {
                **input_data,
                "metric": "nfs_mount",
                "timeout_seconds": timeout_seconds,
                "hung": simulated_hang,
                "passed": simulated_hang,
                "conclusion": "mount_hung" if simulated_hang else "mount_ok",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
'''.strip()


_DEMO_NODE_TEMPLATES: dict[str, dict[str, Any]] = {
    "check_io_stat": {
        "description": "Parse iostat output and detect IO bottleneck when max %util is greater than threshold.",
        "node_py": _CHECK_IO_STAT_NODE,
        "params_schema": {
            "sample_output": "string, mocked iostat -xz output",
            "threshold": "number, default 90",
        },
        "output_schema": {
            "metric": "io_util_percent",
            "value": "number",
            "threshold": "number",
            "passed": "boolean",
            "conclusion": "string",
        },
        "mock_params": {"threshold": 90},
        "mock_input": {"ip": "10.0.12.34"},
    },
    "check_nfs_mount": {
        "description": "Detect hung NFS/network mount by modeling df -h timeout behavior.",
        "node_py": _CHECK_NFS_MOUNT_NODE,
        "params_schema": {
            "timeout_seconds": "number, df -h timeout",
            "simulate_hang": "boolean, demo flag",
        },
        "output_schema": {
            "metric": "nfs_mount",
            "timeout_seconds": "number",
            "hung": "boolean",
            "passed": "boolean",
            "conclusion": "string",
        },
        "mock_params": {"timeout_seconds": 3, "simulate_hang": True},
        "mock_input": {"ip": "10.0.12.34"},
    },
}
