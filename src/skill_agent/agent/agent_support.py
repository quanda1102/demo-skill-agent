from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from src.skill_agent.schemas.skill_model import PublishResult
from src.skill_agent.runtime.models import ExecutionResult, LoadedSkill, RuntimeLog, SkillStub


def rank_skill_stubs(
    stubs: list[SkillStub],
    *,
    query: str,
    requested_action: str = "",
    top_k: int = 5,
) -> list[tuple[int, SkillStub]]:
    request_tokens = _tokenize_text(f"{query} {requested_action}")
    scored_stubs: list[tuple[int, SkillStub]] = []

    for stub in stubs:
        haystack = " ".join(
            [
                stub.skill_id,
                stub.name,
                stub.description,
                " ".join(stub.domain),
                " ".join(stub.supported_actions),
                " ".join(stub.forbidden_actions),
                " ".join(stub.side_effects),
            ]
        )
        score = len(request_tokens & _tokenize_text(haystack))
        requested_action_lower = requested_action.lower()
        if requested_action and requested_action_lower in [action.lower() for action in stub.supported_actions]:
            score += 2
        if requested_action and requested_action_lower in [action.lower() for action in stub.forbidden_actions]:
            score -= 3
        scored_stubs.append((score, stub))

    if request_tokens:
        scored_stubs.sort(key=lambda item: item[0], reverse=True)
        return [(score, stub) for score, stub in scored_stubs if score > 0][:top_k]

    return sorted(scored_stubs, key=lambda item: item[1].skill_id)[:top_k]


def serialize_loaded_skill_payload(
    loaded_skill: LoadedSkill,
    logs: Iterable[RuntimeLog],
) -> dict[str, Any]:
    return {
        "skill_id": loaded_skill.stub.skill_id,
        "name": loaded_skill.stub.name,
        "description": loaded_skill.stub.description,
        "domain": loaded_skill.stub.domain,
        "supported_actions": loaded_skill.stub.supported_actions,
        "forbidden_actions": loaded_skill.stub.forbidden_actions,
        "side_effects": loaded_skill.stub.side_effects,
        "has_run_script": loaded_skill.run_script is not None,
        "skill_md": loaded_skill.skill_md,
        "logs": [log.message for log in logs],
    }


def serialize_execution_result_payload(
    result: ExecutionResult,
    *,
    working_dir: Path,
) -> dict[str, Any]:
    return {
        "skill_id": result.skill_id,
        "status": result.status,
        "execution_status": result.execution_status.value,
        "task_status": result.task_status.value,
        "exit_code": result.exit_code,
        "working_dir": str(working_dir),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "artifact_path": resolve_artifact_path(result, working_dir),
        "logs": [log.message for log in result.logs],
    }


def serialize_publish_result_payload(
    result: PublishResult,
    *,
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "skill_name": result.skill_name,
        "published": result.published,
        "skill_path": result.skill_path,
        "message": result.message,
        "errors": result.report.errors,
        "warnings": result.report.warnings,
        "trace": trace_events,
    }


def _tokenize_text(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def resolve_artifact_path(result: ExecutionResult, working_dir: Path) -> str | None:
    if result.status != "ok" or not result.stdout.strip():
        return None
    first_line = result.stdout.strip().splitlines()[0].strip()

    if result.skill_id == "obsidian-note-writer":
        match = re.search(r"Created:\s*(.+)$", first_line)
        if match:
            return str((working_dir / match.group(1).strip()).resolve())

    if result.skill_id == "obsidian-crud":
        raw_path = first_line.removeprefix("deleted: ").strip() if first_line.startswith("deleted: ") else first_line
        artifact_path = Path(raw_path)
        if not artifact_path.is_absolute():
            artifact_path = working_dir / artifact_path
        return str(artifact_path.resolve())

    return None
