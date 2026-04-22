#!/usr/bin/env python3
"""
Scripted runtime demo.

Usage:
  uv run demo_runtime.py
"""
from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from src.skill_agent.logging_utils import configure_logging
from src.skill_agent.runtime import (
    ExecutionResult,
    PolicyDecision,
    PolicyEngine,
    RuntimeLog,
    SkillStub,
    discover_skills,
    execute_skill,
    load_skill,
)

SKILLS_DIR = Path(__file__).parent / "skills"
DEMO_WORKSPACE_DIR = Path(__file__).parent / "vault" / "runtime-demo"
_W = 72
_policy = PolicyEngine()


def _hr(char: str = "─") -> None:
    print(char * _W)


def _section(title: str) -> None:
    print()
    _hr("═")
    print(f"  {title}")
    _hr("═")


def _print_logs(logs: list[RuntimeLog]) -> None:
    for log in logs:
        icon = {"info": "·", "warning": "⚠", "error": "✗"}.get(log.level, "?")
        print(f"    {icon} [{log.phase:<10}] {log.message}")


def _print_result(result: ExecutionResult) -> None:
    icon = {"ok": "✓", "error": "✗", "no_script": "○"}.get(result.status, "?")
    task_icon = {"satisfied": "✓", "incorrect": "✗", "unsupported": "○", "unknown": "~", "not_applicable": "-"}.get(result.task_status.value, "?")
    print(f"\n  result          : {icon} {result.status.upper()}  (exit {result.exit_code})")
    print(f"  execution_status: {result.execution_status.value}")
    print(f"  task_status     : {task_icon} {result.task_status.value}")
    if result.stdout:
        print(f"  stdout          : {result.stdout.strip()[:160]}")
    if result.stderr:
        print(f"  stderr          : {result.stderr.strip()[:160]}")
    print()
    _print_logs(result.logs)


def _print_policy(decision: PolicyDecision) -> None:
    _print_logs(decision.logs)
    print(
        f"\n  selection={decision.selection_status.value}  "
        f"capability={decision.capability_status.value}  "
        f"execution={decision.execution_status.value}  "
        f"task={decision.task_status.value}"
    )


def _discover() -> list[SkillStub]:
    _section("Discovery")
    stubs, logs = discover_skills(SKILLS_DIR)
    _print_logs(logs)
    print(f"\n  Found {len(stubs)} skill(s): {[s.skill_id for s in stubs]}")
    return stubs


def _prepare_workspace(path: Path, reset: bool = False) -> Path:
    if reset and path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _scratch_dir():
    with tempfile.TemporaryDirectory(prefix="skill-demo-") as tmp:
        root = Path(tmp)
        notes = root / "notes"
        notes.mkdir()
        (notes / "meeting-notes.md").write_text(
            "# Sprint Meeting\nDiscussed velocity, timeline, and blockers.\n"
        )
        (notes / "project-plan.md").write_text(
            "# Project Plan\nKey milestones, deliverables, and owners.\n"
        )
        (notes / "daily-standup.md").write_text(
            "# Daily Standup\nYesterday: PR reviews. Today: meeting prep.\n"
        )
        yield root


def run_scenario(
    stubs: list[SkillStub],
    label: str,
    user_request: str,
    input_payload: str,
    requested_action: str = "",
    note: str = "",
    expected_output: str | None = None,
    validation: str = "string_match",
    execution_cwd: Path | None = None,
) -> None:
    _section(f"Scenario: {label}")
    print(f"  Request : {user_request!r}")
    if requested_action:
        print(f"  Action  : {requested_action!r}")
    if note:
        print(f"  Note    : {note}")
    if execution_cwd is not None:
        print(f"  CWD     : {execution_cwd}")
    print(f"  Input   : {input_payload[:120]}")

    print("\n  [policy]")
    decision = _policy.evaluate(stubs, user_request, requested_action)
    _print_policy(decision)

    if not decision.execution_allowed:
        print(f"  → Blocked: {decision.reason}")
        return

    stub = decision.selected_stub
    assert stub is not None

    print("\n  [load]")
    skill, load_logs = load_skill(stub)
    _print_logs(load_logs)

    print("\n  [execute]")
    result = execute_skill(
        skill,
        input_payload,
        expected_output=expected_output,
        validation=validation,
        cwd=execution_cwd,
    )
    _print_result(result)


def _run_demo(stubs: list[SkillStub]) -> None:
    scripted_workspace = _prepare_workspace(DEMO_WORKSPACE_DIR / "scripted")

    run_scenario(
        stubs,
        label="Word counter — happy path",
        user_request="count the words in this text",
        input_payload="The quick brown fox jumps over the lazy dog",
        requested_action="count",
        note="Expects: 9",
        expected_output="9",
    )

    run_scenario(
        stubs,
        label="Word counter — multiple spaces",
        user_request="count plain text word count",
        input_payload="one   two   three",
        requested_action="count",
        note="Split on whitespace; expects 3 regardless of spacing",
        expected_output="3",
    )

    with _scratch_dir() as tmp:
        notes_dir = str(tmp / "notes")
        run_scenario(
            stubs,
            label="Note searcher — keyword found",
            user_request="search my notes for a keyword",
            input_payload=json.dumps({"directory": notes_dir, "keyword": "meeting"}),
            requested_action="search",
            note="3 notes exist; 2 contain 'meeting'",
            expected_output="meeting-notes.md",
            validation="contains",
        )

    note_writer_workspace = _prepare_workspace(scripted_workspace / "obsidian-note-writer", reset=True)
    run_scenario(
        stubs,
        label="Obsidian note writer — create note",
        user_request="create an obsidian markdown note about my meeting",
        input_payload=json.dumps(
            {
                "title": "Sprint Planning",
                "content": "# Notes\n\n- Velocity review\n- Story grooming",
                "tags": ["meeting", "sprint"],
            }
        ),
        requested_action="create",
        expected_output="Sprint Planning.md",
        validation="contains",
        note="Writes note files into vault/runtime-demo/scripted/obsidian-note-writer.",
        execution_cwd=note_writer_workspace,
    )

    run_scenario(
        stubs,
        label="Policy — no match",
        user_request="configure nginx reverse proxy settings",
        input_payload="{}",
        note="No token overlap with any skill; selection=no_match",
    )

    _section("Demo complete")


def main() -> None:
    configure_logging()
    stubs = _discover()
    _run_demo(stubs)


if __name__ == "__main__":
    main()
