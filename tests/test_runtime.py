from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skill_agent.runtime import (
    ExecutionResult,
    ExecutionStatus,
    LoadedSkill,
    RuntimeLog,
    SelectionStatus,
    SkillStub,
    TaskStatus,
    discover_skills,
    execute_skill,
    load_skill,
    select_skill,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _make_stub(tmp_path: Path, skill_id: str, name: str, description: str) -> SkillStub:
    skill_dir = tmp_path / skill_id
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n"
    )
    return SkillStub(skill_id=skill_id, name=name, description=description, skill_dir=skill_dir)


# ── discovery ─────────────────────────────────────────────────────────────────

class TestDiscoverSkills:
    def test_missing_dir_returns_empty(self, tmp_path):
        stubs, logs = discover_skills(tmp_path / "nonexistent")
        assert stubs == []
        assert any(log.level == "warning" for log in logs)

    def test_discovers_valid_skills(self, tmp_path):
        for sid, name, desc in [
            ("skill-a", "skill-a", "Does A things"),
            ("skill-b", "skill-b", "Does B things"),
        ]:
            _make_stub(tmp_path, sid, name, desc)

        stubs, logs = discover_skills(tmp_path)
        assert len(stubs) == 2
        assert {s.skill_id for s in stubs} == {"skill-a", "skill-b"}
        assert all(log.level == "info" for log in logs)

    def test_skips_dir_without_skill_md(self, tmp_path):
        (tmp_path / "orphan").mkdir()
        stubs, logs = discover_skills(tmp_path)
        assert stubs == []
        assert any("no SKILL.md" in log.message for log in logs)

    def test_skips_skill_with_no_frontmatter(self, tmp_path):
        d = tmp_path / "bare"
        d.mkdir()
        (d / "SKILL.md").write_text("# No frontmatter here\n")
        stubs, logs = discover_skills(tmp_path)
        assert stubs == []
        assert any(log.level == "warning" for log in logs)

    def test_skips_skill_missing_required_fields(self, tmp_path):
        d = tmp_path / "no-desc"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: something\n---\n")
        stubs, logs = discover_skills(tmp_path)
        assert stubs == []
        assert any("description" in log.message for log in logs)

    def test_discovers_real_skills_dir(self):
        stubs, logs = discover_skills(SKILLS_DIR)
        assert len(stubs) >= 2
        ids = {s.skill_id for s in stubs}
        assert "obsidian-note-writer" in ids
        assert "obsidian-crud" in ids
        assert "broken-skill" in ids

    def test_parses_capability_metadata(self):
        stubs, _ = discover_skills(SKILLS_DIR)
        note_writer = next(s for s in stubs if s.skill_id == "obsidian-note-writer")
        assert "obsidian" in note_writer.domain
        assert "create" in note_writer.supported_actions
        assert "delete" in note_writer.forbidden_actions
        assert "file_write" in note_writer.side_effects

    def test_parses_crud_capability_metadata(self):
        stubs, _ = discover_skills(SKILLS_DIR)
        crud = next(s for s in stubs if s.skill_id == "obsidian-crud")
        assert "obsidian" in crud.domain
        assert "delete" in crud.supported_actions
        assert "create" in crud.supported_actions


# ── selector ──────────────────────────────────────────────────────────────────

class TestSelectSkill:
    def _stubs(self) -> list[SkillStub]:
        return [
            SkillStub("note-writer", "note-writer", "Creates markdown notes in Obsidian vault", Path(".")),
            SkillStub("crud-ops", "crud-ops", "Perform CRUD operations on vault files", Path(".")),
            SkillStub("broken", "broken", "Intentionally broken crash skill", Path(".")),
        ]

    def test_no_stubs_returns_none(self):
        stub, status, logs = select_skill([], "write a note")
        assert stub is None
        assert status == SelectionStatus.no_match
        assert any(log.level == "warning" for log in logs)

    def test_empty_request_returns_none(self):
        stub, status, logs = select_skill(self._stubs(), "")
        assert stub is None
        assert status == SelectionStatus.no_match

    def test_selects_best_match(self):
        stub, status, logs = select_skill(self._stubs(), "create a markdown note in obsidian")
        assert stub is not None
        assert stub.skill_id == "note-writer"
        assert status == SelectionStatus.matched

    def test_no_match_returns_none(self):
        stub, status, logs = select_skill(self._stubs(), "deploy kubernetes to production")
        assert stub is None
        assert status == SelectionStatus.no_match
        assert any("scores = 0" in log.message or "all scores" in log.message for log in logs)

    def test_logs_scores_for_all_candidates(self):
        _, _, logs = select_skill(self._stubs(), "vault crud operations")
        score_logs = [l for l in logs if "score=" in l.message and l.message.strip().startswith("'")]
        assert len(score_logs) == 3

    def test_low_confidence_match(self):
        stubs = [SkillStub("odd-one", "odd-one", "Something obscure", Path("."))]
        stub, status, logs = select_skill(stubs, "obscure")
        # score = 1 (only "obscure" matches), below low_confidence_threshold=2
        assert stub is not None
        assert status == SelectionStatus.low_confidence

    def test_ambiguous_match(self):
        # Two skills that both score highly on the same request
        stubs = [
            SkillStub("a", "a", "create markdown notes obsidian vault", Path(".")),
            SkillStub("b", "b", "create markdown notes obsidian files", Path(".")),
        ]
        stub, status, logs = select_skill(stubs, "create markdown notes obsidian")
        assert status == SelectionStatus.ambiguous


# ── loader ────────────────────────────────────────────────────────────────────

class TestLoadSkill:
    def test_loads_skill_md_content(self, tmp_path):
        stub = _make_stub(tmp_path, "my-skill", "my-skill", "Does something")
        skill, logs = load_skill(stub)
        assert "# Body" in skill.skill_md
        assert any("Loaded SKILL.md" in l.message for l in logs)

    def test_detects_run_script(self, tmp_path):
        stub = _make_stub(tmp_path, "with-script", "with-script", "Has run script")
        scripts = stub.skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "run.py").write_text("print('hello')")
        skill, logs = load_skill(stub)
        assert skill.run_script is not None
        assert any("Run script found" in l.message for l in logs)

    def test_missing_run_script_is_none(self, tmp_path):
        stub = _make_stub(tmp_path, "no-script", "no-script", "No run script")
        skill, logs = load_skill(stub)
        assert skill.run_script is None
        assert any(l.level == "warning" for l in logs)


# ── executor ──────────────────────────────────────────────────────────────────

class TestExecuteSkill:
    def _stub(self, skill_dir: Path) -> SkillStub:
        return SkillStub("test-skill", "test-skill", "desc", skill_dir)

    def test_no_script_returns_no_script_status(self, tmp_path):
        stub = self._stub(tmp_path)
        skill = LoadedSkill(stub=stub, skill_md="", run_script=None)
        result = execute_skill(skill, "{}")
        assert result.status == "no_script"
        assert result.exit_code == -1
        assert result.execution_status == ExecutionStatus.skipped
        assert result.task_status == TaskStatus.not_applicable

    def test_successful_script(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("import sys; print('output'); sys.exit(0)\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "hello")
        assert result.status == "ok"
        assert "output" in result.stdout
        assert result.exit_code == 0
        assert result.execution_status == ExecutionStatus.succeeded
        assert result.task_status == TaskStatus.unknown

    def test_failing_script_returns_error(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("raise RuntimeError('boom')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "")
        assert result.status == "error"
        assert result.exit_code != 0
        assert "RuntimeError" in result.stderr or "boom" in result.stderr
        assert result.execution_status == ExecutionStatus.failed
        assert result.task_status == TaskStatus.unknown

    def test_stdin_is_passed_to_script(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("import sys; data = sys.stdin.read(); print(f'got:{data.strip()}')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "HELLO")
        assert result.status == "ok"
        assert "got:HELLO" in result.stdout

    def test_custom_cwd_is_used_for_execution(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text(
            "from pathlib import Path\n"
            "Path('nested').mkdir(exist_ok=True)\n"
            "Path('nested/out.txt').write_text('ok', encoding='utf-8')\n"
            "print(Path.cwd())\n"
        )
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        result = execute_skill(skill, "", cwd=workspace)

        assert result.status == "ok"
        assert str(workspace) in result.stdout
        assert (workspace / "nested" / "out.txt").read_text(encoding="utf-8") == "ok"

    def test_task_satisfied_on_exact_match(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("print('42')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "", expected_output="42")
        assert result.task_status == TaskStatus.satisfied

    def test_task_incorrect_on_mismatch(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("print('42')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "", expected_output="99")
        assert result.task_status == TaskStatus.incorrect

    def test_task_satisfied_on_contains_match(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("print('The answer is 42')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "", expected_output="42", validation="contains")
        assert result.task_status == TaskStatus.satisfied

    def test_task_unknown_when_no_expected_output(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("print('whatever')\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "")
        assert result.task_status == TaskStatus.unknown

    def test_task_unknown_on_nonzero_exit_even_with_expected(self, tmp_path):
        stub = self._stub(tmp_path)
        script = tmp_path / "run.py"
        script.write_text("import sys; print('42'); sys.exit(1)\n")
        skill = LoadedSkill(stub=stub, skill_md="", run_script=script)
        result = execute_skill(skill, "", expected_output="42")
        assert result.task_status == TaskStatus.unknown

    def test_broken_skill_integration(self):
        stubs, _ = discover_skills(SKILLS_DIR)
        broken = next(s for s in stubs if s.skill_id == "broken-skill")
        skill, _ = load_skill(broken)
        result = execute_skill(skill, "{}")
        assert result.status == "error"
        assert result.exit_code != 0
        assert result.execution_status == ExecutionStatus.failed

    def test_real_obsidian_note_writer(self, tmp_path):
        stubs, _ = discover_skills(SKILLS_DIR)
        stub = next(s for s in stubs if s.skill_id == "obsidian-note-writer")
        skill, _ = load_skill(stub)
        payload = json.dumps({"title": "Test Note", "content": "Hello", "tags": ["test"]})
        result = execute_skill(skill, payload, cwd=tmp_path)
        assert result.status == "ok"
        assert result.exit_code == 0
        assert "Test Note" in result.stdout or ".md" in result.stdout
        assert result.execution_status == ExecutionStatus.succeeded
        assert any(p.name.startswith("Test Note") and p.suffix == ".md" for p in tmp_path.iterdir())


# ── log structure ─────────────────────────────────────────────────────────────

class TestRuntimeLog:
    def test_log_has_timestamp(self):
        log = RuntimeLog("info", "discovery", "test message")
        assert log.timestamp
        assert "T" in log.timestamp  # ISO format

    def test_log_fields(self):
        log = RuntimeLog("error", "execution", "something failed")
        assert log.level == "error"
        assert log.phase == "execution"
        assert log.message == "something failed"
