from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

from src.skill_agent.sanitize import clean
from src.skill_agent.clarifier import Clarifier, SkillAgentError as ClarifierSkillAgentError
from src.skill_agent.generator import Generator, SkillAgentError as GeneratorSkillAgentError
from src.skill_agent.logging_utils import configure_logging
from src.skill_agent.models import GeneratedSkill, PublishResult, Runtime, SkillRequest, ValidationReport
from src.skill_agent.provider import MinimaxProvider
from src.skill_agent.publisher import PublishGateway
from src.skill_agent.sandbox import SandboxRunner
from src.skill_agent.validator import StaticValidator

SKILLS_DIR = Path(__file__).parent / "skills"
_MAX_RETRIES = 3


# ── pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(
    request: SkillRequest,
    clarifier_provider: MinimaxProvider,
    generator_provider: MinimaxProvider,
    ask_fn: Callable[[str], str] | None = None,
    review_fn: Callable[[GeneratedSkill, ValidationReport], str | None] | None = None,
    skills_dir: Path = SKILLS_DIR,
    verbose: bool = False,
) -> PublishResult:

    # ── 1. Clarify ──────────────────────────────────────────────────────────
    print(f"\n[1/5] Clarifying '{request.skill_name}'...")
    try:
        spec = Clarifier(clarifier_provider, ask_fn=ask_fn).clarify(request)
    except ClarifierSkillAgentError as exc:
        report = ValidationReport(errors=[str(exc)])
        report.compute_publishable()
        print(f"      ! {exc}")
        print("\n[5/5] Evaluating publish eligibility...")
        result = PublishResult(
            skill_name=request.skill_name,
            published=False,
            report=report,
            message=f"Clarifier failed: {exc}",
        )
        _print_result(result)
        return result
    if verbose:
        print(f"      spec: {spec.name} | steps: {len(spec.workflow_steps)} | tests: {len(spec.test_cases)}")

    # ── 2-4. Generate → Validate → Sandbox (with retry) ────────────────────
    validator = StaticValidator()
    sandbox = SandboxRunner()
    skill = None
    report = None
    errors: list[str] = []

    for attempt in range(1, _MAX_RETRIES + 1):
        attempt_label = f"attempt {attempt}/{_MAX_RETRIES}"
        skill = None
        report = None

        # Generate
        print(f"\n[2/5] Generating skill package ({attempt_label})...")
        try:
            skill = Generator(generator_provider).generate(spec, errors=errors or None)
        except GeneratorSkillAgentError as exc:
            errors = [str(exc)]
            _print_errors(errors)
            if attempt < _MAX_RETRIES:
                _print_feedback("generator", errors)
                continue
            print(f"\n[!] Generator failed on final attempt: {exc}")
            break
        if verbose:
            print(f"      files: {[f.path for f in skill.files]}")

        # Static validation
        print(f"[3/5] Static validation ({attempt_label})...")
        report = validator.validate(skill)
        _print_checks(
            ("syntax", report.syntax_pass),
            ("metadata", report.metadata_pass),
            ("activation", report.activation_pass),
        )

        if not report.syntax_pass or not report.metadata_pass or not report.activation_pass:
            errors = list(report.errors)
            _print_errors(errors)
            if attempt < _MAX_RETRIES:
                _print_feedback("static validation", errors)
            continue

        # Sandbox
        print(f"[4/5] Sandbox tests ({attempt_label})...")
        report = sandbox.run(skill, report)
        _print_checks(
            ("execution", report.execution_pass),
            ("regression", report.regression_pass),
        )
        for line in report.logs:
            print(f"      {line}")

        if report.execution_pass:
            break

        errors = _sandbox_errors(report)
        _print_errors(report.errors)
        if attempt < _MAX_RETRIES:
            _print_feedback("sandbox", errors)
    else:
        print(f"\n[!] Could not produce a passing skill after {_MAX_RETRIES} attempts.")

    if skill is None or report is None:
        report = ValidationReport(errors=errors or ["Generation failed before a validation report was produced"])
        report.compute_publishable()
        print("\n[5/5] Evaluating publish eligibility...")
        result = PublishResult(
            skill_name=request.skill_name,
            published=False,
            report=report,
            message=f"Rejected before publish: {report.errors[0]}",
        )
        _print_result(result)
        return result

    # ── 5. Publish ──────────────────────────────────────────────────────────
    print("\n[5/5] Evaluating publish eligibility...")
    result = PublishGateway(skills_dir).evaluate(skill, report, reviewer=review_fn)
    _print_result(result)
    return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _print_checks(*checks: tuple[str, bool]) -> None:
    for name, passed in checks:
        print(f"      [{'PASS' if passed else 'FAIL'}] {name}")


def _print_errors(errors: list[str]) -> None:
    for e in errors:
        print(f"      ! {e}")


def _print_feedback(source: str, errors: list[str]) -> None:
    print(f"\n      ↻ Sending {source} feedback to generator ({len(errors)} error(s)):")
    for e in errors:
        print(f"        · {e}")


def _sandbox_errors(report: ValidationReport) -> list[str]:
    """Wrap sandbox errors with environment context so the generator understands why files are missing."""
    errors = list(report.errors)
    context = [
        "Sandbox environment: tests run sequentially in an isolated temp dir that starts with "
        "only the skill files (SKILL.md, scripts/run.py). No other files exist unless a previous "
        "test case created them. Fix test ordering and use consistent filenames across the sequence "
        "(e.g. create 'test-note.md' first, then read/delete that same file)."
    ]
    if any("stderr:" in error or "exit=" in error for error in errors):
        context.append(
            "Sandbox validates stdout, stderr, and exit code separately. For expected failures, "
            "set expected_stderr and/or expected_exit_code instead of expecting error text on stdout."
        )
    if any("http://" in error or "https://" in error for error in errors):
        context.append(
            "Avoid public network dependencies in tests. For URL-fetching skills, create local HTML "
            "fixtures and pass them via file:// URLs so results stay deterministic."
        )
    return context + errors


def _print_result(result: PublishResult) -> None:
    print()
    if result.published:
        print(f"  PUBLISHED : {result.skill_name}")
        print(f"  Path      : {result.skill_path}")
    else:
        print(f"  REJECTED  : {result.skill_name}")
        print(f"  Reason    : {result.message}")
    print()


def _make_providers(api_key: str | None) -> tuple[MinimaxProvider, MinimaxProvider]:
    clarifier_provider = MinimaxProvider(api_key=api_key)
    generator_provider = MinimaxProvider(api_key=api_key)
    return clarifier_provider, generator_provider


# ── interactive mode ──────────────────────────────────────────────────────────

def _interactive_ask(question: str) -> str:
    """Called by Clarifier when it needs more info from the user."""
    print(f"\n  Clarifier: {question}")
    return clean(input("  You: ").strip())


def _interactive_review(skill: GeneratedSkill, report: ValidationReport) -> str | None:
    """Called by PublishGateway before writing files. Returns None to approve, reason string to reject."""
    print("\n  ── Review before publishing ──────────────────────")
    print(f"  Skill    : {skill.metadata.name}")
    print(f"  Files    : {[f.path for f in skill.files]}")
    print(f"  Tests    : {len(skill.tests)} case(s)")
    print(f"  Warnings : {report.warnings or 'none'}")
    answer = input("\n  Approve and publish? [y/N]: ").strip().lower()
    if answer == "y":
        return None  # approved
    reason = input("  Rejection reason (optional): ").strip()
    return reason or "Declined at review step"


def _interactive() -> None:
    print("╔══════════════════════════════╗")
    print("║       Skill Builder          ║")
    print("╚══════════════════════════════╝")
    print("Press Ctrl-C at any prompt to exit.\n")

    name = clean(input("Skill name: ").strip())
    if not name:
        print("Name is required.")
        sys.exit(1)

    description = clean(input("Description (what should it do?): ").strip())
    if not description:
        print("Description is required.")
        sys.exit(1)

    runtimes = ", ".join(r.value for r in Runtime)
    runtime_input = clean(input(f"Runtime [{runtimes}] (default: python): ").strip()) or "python"

    print("\nSample inputs  — one per line, blank line to finish:")
    sample_inputs = _read_lines("  input> ")

    print("Expected outputs — one per line, blank line to finish:")
    expected_outputs = _read_lines("  output> ")

    print("Constraints      — one per line, blank line to finish:")
    constraints = _read_lines("  constraint> ")

    request = SkillRequest(
        skill_name=name,
        skill_description=description,
        sample_inputs=sample_inputs,
        expected_outputs=expected_outputs,
        constraints=constraints,
        runtime_preference=Runtime(runtime_input),
    )

    clarifier_provider, generator_provider = _make_providers(os.environ.get("MINIMAX_API_KEY"))
    print()
    result = run_pipeline(
        request,
        clarifier_provider,
        generator_provider,
        ask_fn=_interactive_ask,
        review_fn=_interactive_review,
        verbose=True,
    )
    sys.exit(0 if result.published else 1)


def _read_lines(prompt: str) -> list[str]:
    lines = []
    while True:
        line = clean(input(prompt).strip())
        if not line:
            break
        lines.append(line)
    return lines


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Skill Builder — run without flags for interactive chat mode"
    )
    p.add_argument("--name", help="Short skill name")
    p.add_argument("--description", help="What the skill should do")
    p.add_argument(
        "--runtime",
        choices=[r.value for r in Runtime],
        default="python",
    )
    p.add_argument("--sample-input", action="append", dest="sample_inputs", default=[], metavar="INPUT")
    p.add_argument("--expected-output", action="append", dest="expected_outputs", default=[], metavar="OUTPUT")
    p.add_argument("--constraint", action="append", dest="constraints", default=[], metavar="CONSTRAINT")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument(
        "--no-review",
        action="store_true",
        help="Skip the manual review step (auto-approve if validation passes)",
    )
    return p


def main() -> None:
    configure_logging()
    args = _build_parser().parse_args()

    if not args.name or not args.description:
        try:
            _interactive()
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(0)
        return

    request = SkillRequest(
        skill_name=args.name,
        skill_description=args.description,
        sample_inputs=args.sample_inputs,
        expected_outputs=args.expected_outputs,
        constraints=args.constraints,
        runtime_preference=Runtime(args.runtime),
    )

    clarifier_provider, generator_provider = _make_providers(os.environ.get("MINIMAX_API_KEY"))
    review_fn = None if args.no_review else _interactive_review

    result = run_pipeline(
        request,
        clarifier_provider,
        generator_provider,
        ask_fn=_interactive_ask,
        review_fn=review_fn,
        verbose=args.verbose,
    )
    sys.exit(0 if result.published else 1)


if __name__ == "__main__":
    main()
