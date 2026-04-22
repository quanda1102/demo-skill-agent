from __future__ import annotations

from pathlib import Path

from .models import LoadedSkill, RuntimeLog, SkillStub


def load_skill(stub: SkillStub) -> tuple[LoadedSkill, list[RuntimeLog]]:
    logs: list[RuntimeLog] = []

    skill_md_path = stub.skill_dir / "SKILL.md"
    skill_md = skill_md_path.read_text(encoding="utf-8")
    logs.append(RuntimeLog("info", "load", f"Loaded SKILL.md for '{stub.skill_id}' ({len(skill_md)} chars)"))

    run_script_candidate: Path = stub.skill_dir / "scripts" / "run.py"
    run_script: Path | None
    if run_script_candidate.exists():
        run_script = run_script_candidate
        logs.append(RuntimeLog("info", "load", "Run script found: scripts/run.py"))
    else:
        run_script = None
        logs.append(RuntimeLog("warning", "load", f"No scripts/run.py in '{stub.skill_id}'"))

    return LoadedSkill(stub=stub, skill_md=skill_md, run_script=run_script), logs
