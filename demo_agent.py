#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.skill_agent.agent import SkillChatAgent
from src.skill_agent.observability.logging_utils import configure_logging
from src.skill_agent.providers.provider import MinimaxProvider
from src.skill_agent.sandbox import DockerSandboxRunner, LocalSandboxRunner

SKILLS_DIR = Path(__file__).parent / "skills"
WORKSPACE_DIR = Path(__file__).parent / "vault" / "agent-demo"


def _print_verbose(event: dict) -> None:
    print(f"  [{event.get('kind', 'event')}] {event.get('msg', '')}")


def _save_tty_state():
    try:
        import termios
        if sys.stdin.isatty():
            return termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass
    return None


def _restore_tty_state(state) -> None:
    try:
        import termios
        if state is not None and sys.stdin.isatty():
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, state)
    except Exception:
        pass


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Interactive end-to-end skill agent demo.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print model/tool steps while the agent runs.",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=SKILLS_DIR,
        help="Directory containing published skills.",
    )
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        default=WORKSPACE_DIR,
        help="Shared workspace directory for skill execution side effects.",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run sandbox tests inside Docker (requires skill-agent-sandbox:latest image).",
    )
    args = parser.parse_args()

    api_key = os.environ.get("MINIMAX_API_KEY")
    args.workspace_dir.mkdir(parents=True, exist_ok=True)

    sandbox_runner = DockerSandboxRunner() if args.docker else LocalSandboxRunner()

    tty_state = _save_tty_state()

    agent_provider = MinimaxProvider(
        api_key=api_key,
        temperature=0.2,
        top_p=0.9,
        max_tokens=1600,
    )
    generator_provider = MinimaxProvider(api_key=api_key)

    agent = SkillChatAgent(
        provider=agent_provider,
        generator_provider=generator_provider,
        skills_dir=args.skills_dir,
        workspace_dir=args.workspace_dir,
        verbose=args.verbose,
        event_sink=_print_verbose if args.verbose else None,
        sandbox_runner=sandbox_runner,
    )

    print("╔══════════════════════════════════╗")
    print("║   Skill Agent — End-to-End      ║")
    print("╚══════════════════════════════════╝")
    print()
    print(f"skills_dir    : {args.skills_dir}")
    print(f"workspace_dir : {args.workspace_dir}")
    print(f"sandbox       : {'docker (skill-agent-sandbox:latest)' if args.docker else 'local subprocess'}")
    print("commands      : quit, exit")
    print()

    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not raw:
            continue
        if raw.lower() in {"quit", "exit"}:
            print("Goodbye.")
            break

        try:
            reply = agent.run_turn(raw)
            _restore_tty_state(tty_state)
        except Exception as exc:
            _restore_tty_state(tty_state)
            print(f"Agent error: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            continue

        print(reply)
        print()


if __name__ == "__main__":
    main()
