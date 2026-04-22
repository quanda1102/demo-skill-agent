from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

LogLevel = Literal["info", "warning", "error"]
LogPhase = Literal["discovery", "selection", "capability", "load", "execution"]
ExecStatus = Literal["ok", "error", "no_script"]


class SelectionStatus(str, Enum):
    matched = "matched"
    low_confidence = "low_confidence"
    ambiguous = "ambiguous"
    no_match = "no_match"


class CapabilityStatus(str, Enum):
    supported = "supported"
    unsupported_operation = "unsupported_operation"
    unsupported_domain = "unsupported_domain"
    unknown_capability = "unknown_capability"


class ExecutionStatus(str, Enum):
    allowed = "allowed"
    denied = "denied"
    skipped = "skipped"
    succeeded = "succeeded"
    failed = "failed"


class TaskStatus(str, Enum):
    satisfied = "satisfied"
    unsupported = "unsupported"
    incorrect = "incorrect"
    not_applicable = "not_applicable"
    unknown = "unknown"


@dataclass
class SelectionConfig:
    # Minimum token-overlap score to consider a skill at all
    min_score: int = 1
    # Score below this value → low_confidence (>= min_score but weak)
    low_confidence_threshold: int = 2
    # If the top-two scores are both >= low_confidence_threshold and differ by
    # at most this value, treat the match as ambiguous
    ambiguity_margin: int = 1


@dataclass
class RuntimeLog:
    level: LogLevel
    phase: LogPhase
    message: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class SkillStub:
    skill_id: str
    name: str
    description: str
    skill_dir: Path
    domain: list[str] = field(default_factory=list)
    supported_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)


@dataclass
class LoadedSkill:
    stub: SkillStub
    skill_md: str
    run_script: Path | None


@dataclass
class ExecutionResult:
    status: ExecStatus
    stdout: str
    stderr: str
    exit_code: int
    skill_id: str
    logs: list[RuntimeLog]
    execution_status: ExecutionStatus = ExecutionStatus.skipped
    task_status: TaskStatus = TaskStatus.not_applicable
