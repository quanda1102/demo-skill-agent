from __future__ import annotations

from src.skill_agent.schemas.skill_model import (
    GeneratedSkill,
    Runtime,
    SkillFile,
    SkillMetadata,
    SkillSpec,
    SkillStatus,
    SkillTestCase,
    ValidationReport,
)
from src.skill_agent.sandbox import SandboxRunner

_SPEC = SkillSpec(
    name="word-counter",
    description="Counts words.",
    purpose="Count words.",
    inputs=["text"],
    outputs=["count"],
    workflow_steps=["read", "count", "print"],
    runtime=Runtime.python,
    required_files=["SKILL.md"],
)

_SKILL_MD = "---\nname: word-counter\ndescription: counts words\n---\n"

_ECHO_SCRIPT = """\
import sys
line = sys.stdin.readline().strip()
print(len(line.split()))
"""

_TIMEOUT_SCRIPT = """\
import time
time.sleep(30)
print("done")
"""

_ERROR_SCRIPT = """\
import sys
sys.stderr.write("Error: Could not fetch URL\\n")
sys.exit(1)
"""


def _make_skill(run_py_content: str, tests: list[SkillTestCase]) -> GeneratedSkill:
    return GeneratedSkill(
        metadata=SkillMetadata(name="word-counter", description="Counts words from stdin."),
        files=[
            SkillFile(path="SKILL.md", content=_SKILL_MD),
            SkillFile(path="scripts/run.py", content=run_py_content, executable=True),
        ],
        scripts=["scripts/run.py"],
        tests=tests,
        spec=_SPEC,
        status=SkillStatus.generated,
    )


def test_no_tests_is_vacuously_passing():
    skill = _make_skill(_ECHO_SCRIPT, tests=[])
    report = SandboxRunner().run(_make_skill(_ECHO_SCRIPT, []), ValidationReport())
    assert report.execution_pass is True
    assert any("vacuously" in w.lower() for w in report.warnings)


def test_passing_string_match_test():
    tests = [
        SkillTestCase(
            description="two words",
            input="hello world",
            expected_output="2",
            validation_method="string_match",
        )
    ]
    skill = _make_skill(_ECHO_SCRIPT, tests)
    report = SandboxRunner().run(skill, ValidationReport())
    assert report.execution_pass is True
    assert not report.errors


def test_failing_string_match_test():
    tests = [
        SkillTestCase(
            description="wrong expected output",
            input="hello world",
            expected_output="99",
            validation_method="string_match",
        )
    ]
    skill = _make_skill(_ECHO_SCRIPT, tests)
    report = SandboxRunner().run(skill, ValidationReport())
    assert report.execution_pass is False
    assert report.errors


def test_contains_validation_method():
    script = "import sys; print('The count is: ' + str(len(sys.stdin.readline().split())))"
    tests = [
        SkillTestCase(
            description="contains check",
            input="a b c",
            expected_output="3",
            validation_method="contains",
        )
    ]
    skill = _make_skill(script, tests)
    report = SandboxRunner().run(skill, ValidationReport())
    assert report.execution_pass is True


def test_fixtures_are_written_before_test_runs():
    # Script checks whether the fixture file exists and prints yes/no
    script = (
        "import sys, json, os\n"
        "data = json.loads(sys.stdin.read())\n"
        "print('yes' if os.path.exists(data['path']) else 'no')\n"
    )
    tests = [
        SkillTestCase(
            description="fixture file exists before test",
            input='{"path": "notes/hello.md"}',
            expected_output="yes",
            validation_method="string_match",
            fixtures={"notes/hello.md": "# Hello"},
        )
    ]
    report = SandboxRunner().run(_make_skill(script, tests), ValidationReport())
    assert report.execution_pass is True
    assert not report.errors


def test_fixtures_create_nested_directories():
    script = (
        "import sys, pathlib\n"
        "path = sys.stdin.read().strip()\n"
        "print(pathlib.Path(path).read_text().strip())\n"
    )
    tests = [
        SkillTestCase(
            description="reads deeply nested fixture",
            input="a/b/c/file.txt",
            expected_output="deep content",
            validation_method="string_match",
            fixtures={"a/b/c/file.txt": "deep content"},
        )
    ]
    report = SandboxRunner().run(_make_skill(script, tests), ValidationReport())
    assert report.execution_pass is True


def test_no_fixtures_leaves_sandbox_empty():
    # A test with no fixtures should not see files from a previous test's fixtures
    script = (
        "import sys, os\n"
        "path = sys.stdin.read().strip()\n"
        "print('yes' if os.path.exists(path) else 'no')\n"
    )
    tests = [
        SkillTestCase(
            description="test with fixture",
            input="data.txt",
            expected_output="yes",
            fixtures={"data.txt": "hello"},
        ),
        SkillTestCase(
            description="subsequent test still sees prior fixture (shared dir)",
            input="data.txt",
            expected_output="yes",  # fixture from test 1 persists in the shared temp dir
        ),
    ]
    report = SandboxRunner().run(_make_skill(script, tests), ValidationReport())
    assert report.execution_pass is True


def test_timeout_causes_failure():
    tests = [
        SkillTestCase(
            description="timeout test",
            input="anything",
            expected_output="done",
            validation_method="string_match",
        )
    ]
    skill = _make_skill(_TIMEOUT_SCRIPT, tests)
    report = SandboxRunner().run(skill, ValidationReport())
    assert report.execution_pass is False
    assert any("timed out" in e.lower() or "timed out" in l.lower() for e in report.errors + report.logs)


def test_expected_stderr_and_exit_code_can_pass():
    tests = [
        SkillTestCase(
            description="expected fetch failure",
            input="https://example.invalid",
            expected_output="",
            expected_stderr="Error: Could not fetch URL",
            expected_exit_code=1,
        )
    ]
    report = SandboxRunner().run(_make_skill(_ERROR_SCRIPT, tests), ValidationReport())
    assert report.execution_pass is True
    assert not report.errors


def test_nonzero_exit_fails_even_if_stdout_matches():
    script = "import sys\nprint('2')\nsys.exit(1)\n"
    tests = [
        SkillTestCase(
            description="stdout matches but exit code does not",
            input="hello world",
            expected_output="2",
        )
    ]
    report = SandboxRunner().run(_make_skill(script, tests), ValidationReport())
    assert report.execution_pass is False
    assert any("expected exit=0" in error for error in report.errors)
