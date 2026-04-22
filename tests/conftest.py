from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.skill_agent.models import (
    GeneratedSkill,
    Runtime,
    SkillFile,
    SkillMetadata,
    SkillRequest,
    SkillSpec,
    SkillStatus,
    SkillTestCase,
)

VALID_SKILL_MD = """\
---
name: word-counter
description: Counts the number of words in a line of text read from stdin.
version: 0.1.0
owner: skill-agent
runtime: python
status: generated
entrypoints:
  - type: skill_md
    path: SKILL.md
---

## Word Counter

Reads one line from stdin and prints the word count to stdout.

### Usage

```
echo "hello world" | python scripts/run.py
```
"""

VALID_RUN_PY = """\
import sys

def main():
    if "--help" in sys.argv:
        print("Usage: echo <text> | python run.py")
        sys.exit(0)
    line = sys.stdin.readline().strip()
    print(len(line.split()))

if __name__ == "__main__":
    main()
"""


@pytest.fixture
def sample_request() -> SkillRequest:
    return SkillRequest(
        skill_name="word-counter",
        skill_description="Count words in a line of text",
        sample_inputs=["hello world"],
        expected_outputs=["2"],
        runtime_preference=Runtime.python,
    )


@pytest.fixture
def sample_spec() -> SkillSpec:
    return SkillSpec(
        name="word-counter",
        description="Counts the number of words in a line of text read from stdin.",
        purpose="Provide a simple word-counting utility for agent pipelines.",
        inputs=["a single line of text via stdin"],
        outputs=["integer word count as a string on stdout"],
        workflow_steps=[
            "Read one line from stdin",
            "Split the line on whitespace",
            "Count the resulting tokens",
            "Print the count to stdout",
        ],
        edge_cases=["empty input line returns 0", "input with only spaces returns 0"],
        required_files=["SKILL.md", "scripts/run.py"],
        runtime=Runtime.python,
        test_cases=[
            SkillTestCase(
                description="Basic two-word input",
                input="hello world",
                expected_output="2",
                validation_method="string_match",
            ),
            SkillTestCase(
                description="Empty input",
                input="",
                expected_output="0",
                validation_method="string_match",
            ),
        ],
    )


@pytest.fixture
def sample_skill(sample_spec: SkillSpec) -> GeneratedSkill:
    return GeneratedSkill(
        metadata=SkillMetadata(
            name="word-counter",
            description="Counts the number of words in a line of text read from stdin.",
            version="0.1.0",
            owner="skill-agent",
            runtime=Runtime.python,
            status=SkillStatus.generated,
            entrypoints=[{"type": "skill_md", "path": "SKILL.md"}],
        ),
        files=[
            SkillFile(path="SKILL.md", content=VALID_SKILL_MD),
            SkillFile(path="scripts/run.py", content=VALID_RUN_PY, executable=True),
        ],
        scripts=["scripts/run.py"],
        tests=sample_spec.test_cases,
        spec=sample_spec,
        status=SkillStatus.generated,
    )


@pytest.fixture
def mock_anthropic_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_provider() -> MagicMock:
    return MagicMock()
