from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.skill_agent.schemas.skill_model import Runtime, SkillSpec, SkillTestCase

DEFAULT_REQUIRED_FILES = ["SKILL.md", "scripts/run.py"]

_SKILL_TEST_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "input": {"type": "string"},
        "expected_output": {"type": "string"},
        "validation_method": {"type": "string"},
        "fixtures": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "expected_stderr": {"type": "string"},
        "expected_exit_code": {"type": "integer"},
    },
    "required": ["description", "input"],
}

_SKILL_SPEC_TOOL_PROPERTIES: dict[str, Any] = {
    "name": {"type": "string"},
    "description": {"type": "string"},
    "purpose": {"type": "string"},
    "inputs": {"type": "array", "items": {"type": "string"}},
    "outputs": {"type": "array", "items": {"type": "string"}},
    "workflow_steps": {"type": "array", "items": {"type": "string"}},
    "edge_cases": {"type": "array", "items": {"type": "string"}},
    "required_files": {"type": "array", "items": {"type": "string"}},
    "runtime": {"type": "string", "enum": [runtime.value for runtime in Runtime]},
    "test_cases": {
        "type": "array",
        "items": deepcopy(_SKILL_TEST_CASE_SCHEMA),
    },
}


def build_skill_spec_tool_parameters(*, required_fields: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(_SKILL_SPEC_TOOL_PROPERTIES),
        "required": list(required_fields),
    }


CLARIFIER_SUBMIT_SPEC_PARAMETERS = build_skill_spec_tool_parameters(
    required_fields=[
        "name",
        "description",
        "purpose",
        "inputs",
        "outputs",
        "workflow_steps",
        "required_files",
        "runtime",
    ]
)

AGENT_BUILD_SKILL_TOOL_PARAMETERS = build_skill_spec_tool_parameters(
    required_fields=[
        "name",
        "description",
        "purpose",
        "inputs",
        "outputs",
        "workflow_steps",
        "runtime",
        "test_cases",
    ]
)


def build_skill_spec(
    *,
    name: str,
    description: str,
    purpose: str,
    inputs: list[str],
    outputs: list[str],
    workflow_steps: list[str],
    edge_cases: list[str] | None = None,
    runtime: Runtime | str = Runtime.python,
    test_cases: list[dict[str, Any]] | list[SkillTestCase] | None = None,
    required_files: list[str] | None = None,
) -> SkillSpec:
    normalized_runtime = runtime if isinstance(runtime, Runtime) else Runtime(runtime)
    normalized_test_cases = [
        test if isinstance(test, SkillTestCase) else SkillTestCase.model_validate(test)
        for test in (test_cases or [])
    ]
    return SkillSpec(
        name=name,
        description=description,
        purpose=purpose,
        inputs=inputs,
        outputs=outputs,
        workflow_steps=workflow_steps,
        edge_cases=edge_cases or [],
        required_files=required_files or list(DEFAULT_REQUIRED_FILES),
        runtime=normalized_runtime,
        test_cases=normalized_test_cases,
    )
