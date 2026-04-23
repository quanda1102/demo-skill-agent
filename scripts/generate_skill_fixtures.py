#!/usr/bin/env python3
"""
scripts/generate_skill_fixtures.py — Fixture generation via the project's own engine.

ENTRYPOINTS USED
----------------
Happy-path cases (1–3):
  main.run_pipeline()
    The canonical, fully-tested 5-stage pipeline: clarify → generate → validate →
    sandbox → publish.  ask_fn and review_fn are injected for non-interactive use.
    Internal retry logic is preserved.  Output goes to fixtures/<case-id>/ instead
    of the live skills/ directory.

Seeded-failure cases (4–5):
  Clarifier.clarify() + Generator.generate() → mutation → StaticValidator + SandboxRunner
    run_pipeline() is bypassed intentionally for these cases: its retry loop would
    attempt to repair any validation errors that arise.  We generate a clean skill
    via the LLM, apply a deterministic corruption *after* generation, then run the
    validator and sandbox directly so the corruption is what actually gets tested.

Why this split:
  Cases 1–3 prove the engine produces publishable output end-to-end.
  Cases 4–5 prove the validator and sandbox correctly catch broken output even when
  the LLM itself produced something structurally sound.

FIXTURE CASES
-------------
  01-happy-path         word-counter              all stages pass; published
  02-destructive-action file-archiver             delete side-effect; published
  03-unsupported-action note-searcher             read-only with forbidden writes; published
  04-invalid-metadata   echo-tool (corrupted)     frontmatter name mismatch; rejected at static validation
  05-broken-execution   echo-tool-v2 (broken)     crashing run.py; rejected at sandbox

USAGE
-----
  export MINIMAX_API_KEY=<key> (optional)
  uv run python scripts/generate_skill_fixtures.py
  uv run python scripts/generate_skill_fixtures.py --cases 01-happy-path 04-invalid-metadata
  uv run python scripts/generate_skill_fixtures.py --output-dir /tmp/fixtures
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Ensure project root is importable
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from demo_generation import run_pipeline  # noqa: E402 — must come after sys.path insert

from src.skill_agent.generation.clarifier import Clarifier  # noqa: E402
from src.skill_agent.generation.generator import Generator  # noqa: E402
from src.skill_agent.schemas.skill_model import (  # noqa: E402
    GeneratedSkill,
    PublishResult,
    Runtime,
    SkillFile,
    SkillRequest,
    materialize_skill,
)
from src.skill_agent.providers.provider import MinimaxProvider  # noqa: E402
from src.skill_agent.generation.publisher import PublishGateway  # noqa: E402
from src.skill_agent.sandbox import SandboxRunner  # noqa: E402
from src.skill_agent.validation.validator import StaticValidator  # noqa: E402


# ── Mutations applied after LLM generation ────────────────────────────────────

def _corrupt_metadata(skill: GeneratedSkill) -> GeneratedSkill:
    """
    Case 4: overwrite the 'name' field in SKILL.md frontmatter with a value that
    does not match skill.metadata.name.  StaticValidator._check_metadata detects
    the mismatch and sets metadata_pass=False.
    """
    new_files = []
    for f in skill.files:
        if f.path == "SKILL.md":
            corrupted = f.content.replace(
                f"name: {skill.metadata.name}",
                "name: INVALID NAME WITH SPACES",
                1,
            )
            new_files.append(f.model_copy(update={"content": corrupted}))
        else:
            new_files.append(f)
    return skill.model_copy(update={"files": new_files})


def _inject_broken_script(skill: GeneratedSkill) -> GeneratedSkill:
    """
    Case 5: replace scripts/run.py with a script that always raises RuntimeError.
    StaticValidator passes (SKILL.md and metadata are valid), but SandboxRunner
    fails when it tries to execute the test cases.
    """
    broken = (
        "#!/usr/bin/env python3\n"
        "# Intentionally broken for fixture testing — do not repair\n"
        "raise RuntimeError('fixture: this script always crashes')\n"
    )
    replaced = False
    new_files = []
    for f in skill.files:
        if f.path == "scripts/run.py":
            new_files.append(f.model_copy(update={"content": broken, "executable": True}))
            replaced = True
        else:
            new_files.append(f)
    if not replaced:
        # LLM used a different entrypoint path; add the broken script explicitly
        new_files.append(SkillFile(path="scripts/run.py", content=broken, executable=True))
        skill = skill.model_copy(update={"scripts": skill.scripts + ["scripts/run.py"]})
    return skill.model_copy(update={"files": new_files})


# ── Fixture case registry ──────────────────────────────────────────────────────

@dataclass
class FixtureCase:
    case_id: str
    label: str
    request: SkillRequest
    expected_outcome: str                                          # "published" | "rejected"
    mutation: Callable[[GeneratedSkill], GeneratedSkill] | None   # None → use run_pipeline
    mutation_stage: str                                            # "" | "metadata" | "execution"


FIXTURE_CASES: list[FixtureCase] = [

    # ── 1. Happy path ─────────────────────────────────────────────────────────
    FixtureCase(
        case_id="01-happy-path",
        label="word-counter (happy path)",
        request=SkillRequest(
            skill_name="word-counter",
            skill_description=(
                "Reads plain text from stdin and prints the word count as an integer "
                "to stdout.  No side effects.  Deterministic output."
            ),
            sample_inputs=["Hello world this is a test"],
            expected_outputs=["6"],
            constraints=[
                "Read entire stdin, split on whitespace, print only the integer count",
                "Exit 0 on success; exit 1 on empty input",
                "No file writes, no network calls",
            ],
            runtime_preference=Runtime.python,
        ),
        expected_outcome="published",
        mutation=None,
        mutation_stage="",
    ),

    # ── 2. Destructive action (side-effect: file_delete) ──────────────────────
    FixtureCase(
        case_id="02-destructive-action",
        label="file-archiver (move files — destructive side-effect)",
        request=SkillRequest(
            skill_name="file-archiver",
            skill_description=(
                "Moves files matching a glob pattern from a source directory into an "
                "archive subdirectory, removing originals after copying."
            ),
            sample_inputs=['{"source_dir": "notes", "pattern": "*.md", "archive_dir": "notes/archive"}'],
            expected_outputs=["archived: 0 file(s)"],
            constraints=[
                "Accept JSON from stdin: source_dir, pattern, archive_dir",
                "Create archive_dir if it does not exist",
                "Print count of archived files to stdout",
                "Exit 0 on success; exit 1 on missing source_dir or bad JSON",
                "Declare side_effects: file_write, file_delete in SKILL.md frontmatter",
                "Declare supported_actions: archive, move in SKILL.md frontmatter",
                "Declare forbidden_actions: delete_permanent in SKILL.md frontmatter",
            ],
            runtime_preference=Runtime.python,
        ),
        expected_outcome="published",
        mutation=None,
        mutation_stage="",
    ),

    # ── 3. Read-only with explicit action rules ───────────────────────────────
    FixtureCase(
        case_id="03-unsupported-action",
        label="note-searcher (read-only, explicit supported/forbidden action rules)",
        request=SkillRequest(
            skill_name="note-searcher",
            skill_description=(
                "Searches markdown notes in a given directory for a keyword and prints "
                "matching filenames to stdout.  Read-only — never modifies any file."
            ),
            sample_inputs=['{"directory": "notes", "keyword": "meeting"}'],
            expected_outputs=["No matches found"],
            constraints=[
                "Accept JSON from stdin: directory (str), keyword (str)",
                "Print each matching filename on its own line; print 'No matches found' if none",
                "Read-only: no file writes, no deletes, no network calls; exit 0 always",
                "Declare side_effects: file_read in SKILL.md frontmatter",
                "Declare supported_actions: search, read in SKILL.md frontmatter",
                "Declare forbidden_actions: write, delete, update in SKILL.md frontmatter",
            ],
            runtime_preference=Runtime.python,
        ),
        expected_outcome="published",
        mutation=None,
        mutation_stage="",
    ),

    # ── 4. Seeded: valid LLM output → metadata corrupted ─────────────────────
    FixtureCase(
        case_id="04-invalid-metadata",
        label="echo-tool with SKILL.md name corrupted (metadata mismatch)",
        request=SkillRequest(
            skill_name="echo-tool",
            skill_description="Reads a message from stdin and prints it back to stdout unchanged.",
            sample_inputs=["hello"],
            expected_outputs=["hello"],
            constraints=["Echo stdin to stdout exactly", "Exit 0 always"],
            runtime_preference=Runtime.python,
        ),
        expected_outcome="rejected",
        mutation=_corrupt_metadata,
        mutation_stage="metadata",
    ),

    # ── 5. Seeded: valid LLM output → run.py replaced with crashing script ───
    FixtureCase(
        case_id="05-broken-execution",
        label="echo-tool-v2 with crashing scripts/run.py injected",
        request=SkillRequest(
            skill_name="echo-tool-v2",
            skill_description=(
                "Reads a message from stdin and prints it back to stdout. "
                "Improved version with structured error handling."
            ),
            sample_inputs=["hello"],
            expected_outputs=["hello"],
            constraints=["Echo stdin to stdout exactly", "Exit 0 always"],
            runtime_preference=Runtime.python,
        ),
        expected_outcome="rejected",
        mutation=_inject_broken_script,
        mutation_stage="execution",
    ),
]


# ── Pipeline execution ────────────────────────────────────────────────────────

def _auto_ask(question: str) -> str:
    """Non-interactive response for Clarifier when it needs more information."""
    _log(f"clarifier asked: {question}")
    _log("auto-reply: Use reasonable defaults and proceed.")
    return "Use reasonable defaults and proceed."


def run_happy_path_case(
    case: FixtureCase,
    provider: MinimaxProvider,
    fixtures_dir: Path,
) -> tuple[bool, dict]:
    """
    Calls the full run_pipeline() with output directed to fixtures/<case-id>/.
    Success = the skill was published as expected.
    """
    case_dir = fixtures_dir / case.case_id
    result: PublishResult = run_pipeline(
        request=case.request,
        clarifier_provider=provider,
        generator_provider=provider,
        ask_fn=_auto_ask,
        review_fn=None,          # auto-approve at publish gate
        skills_dir=case_dir,
        verbose=True,
    )
    _save_publish_result(case_dir, result)
    ok = result.published
    return ok, _build_row(case, result)


def run_seeded_failure_case(
    case: FixtureCase,
    provider: MinimaxProvider,
    fixtures_dir: Path,
) -> tuple[bool, dict]:
    """
    Calls Clarifier + Generator to produce raw LLM output, applies case.mutation,
    then runs StaticValidator + SandboxRunner + PublishGateway directly.
    run_pipeline() is not used here because its retry logic would undo the mutation.
    Success = the skill was correctly rejected after mutation.
    """
    assert case.mutation is not None
    case_dir = fixtures_dir / case.case_id

    # Stage 1 — Clarify
    _log("clarify...")
    spec = Clarifier(provider, ask_fn=_auto_ask).clarify(case.request)

    # Stage 2 — Generate (single attempt; no retries — we want the raw output)
    _log(f"generate {spec.name!r}...")
    raw_skill = Generator(provider).generate(spec, errors=None)

    # Stage 3 — Mutate (apply the deterministic corruption)
    _log(f"mutate/{case.mutation_stage}...")
    skill = case.mutation(raw_skill)

    # Stage 4 — Static validation
    _log("validate (static)...")
    report = StaticValidator().validate(skill)
    _print_checks(
        ("syntax",     report.syntax_pass),
        ("metadata",   report.metadata_pass),
        ("activation", report.activation_pass),
    )

    # Stage 5 — Sandbox (only if static validation cleared enough to attempt execution)
    if report.syntax_pass and report.metadata_pass:
        _log("validate (sandbox)...")
        report = SandboxRunner().run(skill, report)
        _print_checks(("execution", report.execution_pass))

    # Stage 6 — Publish gate
    result = PublishGateway(case_dir).evaluate(skill, report, reviewer=None)

    # Stage 7 — Materialize skill files for fixture consumers.
    # The publisher only writes on success; we always write so the fixture directory
    # contains the (broken) skill files for downstream tests to inspect.
    if not result.published:
        _log(f"materialize (rejected) → {case_dir / skill.metadata.name}")
        materialize_skill(skill, case_dir)

    _save_publish_result(case_dir, result)
    ok = not result.published   # success = correctly rejected
    return ok, _build_row(case, result)


# ── Output helpers ────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"    [{msg}]")


def _print_checks(*checks: tuple[str, bool]) -> None:
    for name, passed in checks:
        print(f"      {'✓' if passed else '✗'} {name}")


def _build_row(case: FixtureCase, result: PublishResult) -> dict:
    return {
        "case_id": case.case_id,
        "label": case.label,
        "expected_outcome": case.expected_outcome,
        "actual_outcome": "published" if result.published else "rejected",
        "mutation_stage": case.mutation_stage or None,
        "skill_name": result.skill_name,
        "skill_path": result.skill_path,
        "errors": result.report.errors,
        "warnings": result.report.warnings,
        "message": result.message,
    }


def _save_publish_result(case_dir: Path, result: PublishResult) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    out = case_dir / "publish_result.json"
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _log(f"saved → {out}")


def _save_summary(fixtures_dir: Path, rows: list[dict], elapsed_s: float) -> None:
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_s, 1),
        "total": len(rows),
        "passed": sum(1 for r in rows if r.get("ok")),
        "failed": sum(1 for r in rows if not r.get("ok")),
        "cases": rows,
    }
    out = fixtures_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  summary → {out}")


def _banner(title: str) -> None:
    bar = "─" * 64
    print(f"\n{bar}\n  {title}\n{bar}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate skill test fixtures using the project's real generation engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures",
        metavar="DIR",
        help="Root directory for fixture output (default: tests/fixtures)",
    )
    parser.add_argument(
        "--cases",
        nargs="*",
        metavar="CASE_ID",
        help="Run only these case IDs, e.g. --cases 01-happy-path 04-invalid-metadata",
    )
    args = parser.parse_args()

    api_key = os.environ.get("MINIMAX_API_KEY") or None

    provider = MinimaxProvider(api_key=api_key)
    fixtures_dir = Path(args.output_dir).resolve()
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    cases = FIXTURE_CASES
    if args.cases:
        cases = [c for c in FIXTURE_CASES if c.case_id in args.cases]
        if not cases:
            print(f"ERROR: no cases matched {args.cases}")
            print(f"       valid IDs: {[c.case_id for c in FIXTURE_CASES]}")
            sys.exit(1)

    _banner("Skill Fixture Generator")
    print(f"  output dir : {fixtures_dir}")
    print(f"  cases      : {len(cases)}")
    print(f"  provider   : {provider.model}")
    print(f"  case IDs   : {[c.case_id for c in cases]}")

    rows: list[dict] = []
    start = datetime.now(timezone.utc)

    for i, case in enumerate(cases, 1):
        _banner(f"[{i}/{len(cases)}] {case.label}")
        print(f"  case_id  : {case.case_id}")
        print(f"  expected : {case.expected_outcome}")
        if case.mutation_stage:
            print(f"  mutation : {case.mutation_stage}")

        ok = False
        row: dict = {}
        try:
            if case.mutation is None:
                ok, row = run_happy_path_case(case, provider, fixtures_dir)
            else:
                ok, row = run_seeded_failure_case(case, provider, fixtures_dir)

            row["ok"] = ok
            outcome_matches = row["actual_outcome"] == case.expected_outcome
            status = "OK  " if outcome_matches else "FAIL"
            print(f"\n  [{status}] actual={row['actual_outcome']}  expected={case.expected_outcome}")
            if not outcome_matches:
                print(f"         ↑ outcome mismatch")
            for e in row.get("errors") or []:
                print(f"         ! {e}")

        except Exception as exc:
            row = {
                "case_id": case.case_id,
                "label": case.label,
                "expected_outcome": case.expected_outcome,
                "actual_outcome": "error",
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"\n  [ERROR] {exc}")
            traceback.print_exc()

        rows.append(row)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    _banner("Summary")
    for row in rows:
        icon = "✓" if row.get("ok") else "✗"
        print(f"  {icon}  {row['case_id']:<35}  {row.get('actual_outcome', 'error')}")

    passed = sum(1 for r in rows if r.get("ok"))
    print(f"\n  {passed}/{len(rows)} cases produced expected outcome  ({elapsed:.1f}s)")
    _save_summary(fixtures_dir, rows, elapsed)

    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
