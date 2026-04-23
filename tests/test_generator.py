from __future__ import annotations

import json

import pytest

from src.skill_agent.generation.generator import Generator, SkillAgentError
from src.skill_agent.schemas.skill_model import Runtime, SkillSpec, SkillTestCase
from src.skill_agent.providers.provider import ProviderError

_SPEC = SkillSpec(
    name="word-counter",
    description="Counts words in stdin.",
    purpose="Count words.",
    inputs=["text line"],
    outputs=["word count"],
    workflow_steps=["read stdin", "split", "print count"],
    runtime=Runtime.python,
    required_files=["SKILL.md", "scripts/run.py"],
    test_cases=[
        SkillTestCase(
            description="two words",
            input="hello world",
            expected_output="2",
            validation_method="string_match",
        )
    ],
)

_SKILL_MD = "---\nname: word-counter\ndescription: Counts words in stdin.\nversion: 0.1.0\nowner: skill-agent\nruntime: python\nstatus: generated\nentrypoints:\n  - type: skill_md\n    path: SKILL.md\n---\nBody.\n"
_RUN_PY = "import sys\nprint(len(sys.stdin.readline().split()))\n"


def _tool_call(id: str, name: str, args: dict) -> dict:
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_generator_happy_path(mock_provider):
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words in stdin.", "runtime": "python"}),
                    _tool_call("c2", "write_file", {"path": "SKILL.md", "content": _SKILL_MD, "executable": False}),
                    _tool_call("c3", "write_file", {"path": "scripts/run.py", "content": _RUN_PY, "executable": True}),
                    _tool_call("c4", "add_test_case", {"description": "two words", "input": "hello world", "expected_output": "2", "validation_method": "string_match"}),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    skill = Generator(mock_provider).generate(_SPEC)

    assert skill.metadata.name == "word-counter"
    assert any(f.path == "SKILL.md" for f in skill.files)
    assert any(f.path == "scripts/run.py" for f in skill.files)
    assert skill.tests[0].input == "hello world"
    assert "scripts/run.py" in skill.scripts


def test_generator_raises_when_metadata_missing(mock_provider):
    mock_provider.invoke.return_value = {"role": "assistant", "content": "Done.", "tool_calls": None}
    with pytest.raises(SkillAgentError, match="incomplete"):
        Generator(mock_provider).generate(_SPEC)


def test_generator_raises_when_skill_md_missing(mock_provider):
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words.", "runtime": "python"}),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    with pytest.raises(SkillAgentError, match="incomplete"):
        Generator(mock_provider).generate(_SPEC)


def test_write_file_replaces_duplicate_path(mock_provider):
    """A second write_file call for the same path must replace the first, not append."""
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words in stdin.", "runtime": "python"}),
                    _tool_call("c2", "write_file", {"path": "SKILL.md", "content": _SKILL_MD}),
                    _tool_call("c3", "write_file", {"path": "scripts/run.py", "content": _RUN_PY, "executable": True}),
                    _tool_call("c4", "write_file", {"path": "scripts/run.py", "content": "# updated\n" + _RUN_PY, "executable": True}),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    skill = Generator(mock_provider).generate(_SPEC)

    run_py_files = [f for f in skill.files if f.path == "scripts/run.py"]
    assert len(run_py_files) == 1, "duplicate write_file must replace, not append"
    assert "# updated" in run_py_files[0].content, "last write must win"


def test_add_test_case_fixtures_stored(mock_provider):
    """fixtures dict passed to add_test_case must survive to the generated skill."""
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words in stdin.", "runtime": "python"}),
                    _tool_call("c2", "write_file", {"path": "SKILL.md", "content": _SKILL_MD}),
                    _tool_call("c3", "write_file", {"path": "scripts/run.py", "content": _RUN_PY, "executable": True}),
                    _tool_call("c4", "add_test_case", {
                        "description": "fixture test",
                        "input": '{"path": "notes/a.md"}',
                        "expected_output": "yes",
                        "fixtures": {"notes/a.md": "# Hello"},
                    }),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    skill = Generator(mock_provider).generate(_SPEC)

    assert skill.tests[0].fixtures == {"notes/a.md": "# Hello"}


def test_add_test_case_error_expectations_stored(mock_provider):
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words in stdin.", "runtime": "python"}),
                    _tool_call("c2", "write_file", {"path": "SKILL.md", "content": _SKILL_MD}),
                    _tool_call("c3", "write_file", {"path": "scripts/run.py", "content": _RUN_PY, "executable": True}),
                    _tool_call("c4", "add_test_case", {
                        "description": "network failure",
                        "input": "https://example.invalid",
                        "expected_output": "",
                        "expected_stderr": "Error: Could not fetch URL",
                        "expected_exit_code": 1,
                    }),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    skill = Generator(mock_provider).generate(_SPEC)

    assert skill.tests[0].expected_stderr == "Error: Could not fetch URL"
    assert skill.tests[0].expected_exit_code == 1


def test_generator_raises_on_api_error(mock_provider):
    mock_provider.invoke.side_effect = ProviderError("connection error")
    with pytest.raises(SkillAgentError, match="API error"):
        Generator(mock_provider).generate(_SPEC)


def test_generator_forwards_trace_events(mock_provider):
    call_count = {"n": 0}
    events = []

    def mock_invoke(messages, tools=None, on_delta=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "set_metadata", {"name": "word-counter", "description": "Counts words in stdin.", "runtime": "python"}),
                    _tool_call("c2", "write_file", {"path": "SKILL.md", "content": _SKILL_MD, "executable": False}),
                    _tool_call("c3", "write_file", {"path": "scripts/run.py", "content": _RUN_PY, "executable": True}),
                    _tool_call("c4", "add_test_case", {"description": "two words", "input": "hello world", "expected_output": "2", "validation_method": "string_match"}),
                ],
            }
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    generator = Generator(mock_provider)
    generator.event_sink = events.append

    generator.generate(_SPEC)

    assert any(
        event["source"] == "generator" and event["kind"] == "model" and event.get("action") == "tool_calls"
        for event in events
    )
    assert any(
        event["source"] == "generator" and event["kind"] == "tool" and event.get("name") == "set_metadata"
        for event in events
    )
    assert any(
        event["source"] == "generator" and event["kind"] == "tool" and event.get("name") == "write_file"
        for event in events
    )


def test_generator_forwards_streaming_deltas(mock_provider):
    events = []

    def mock_invoke(messages, tools=None, on_delta=None):
        assert on_delta is not None
        on_delta("drafting skill layout")
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    generator = Generator(mock_provider)
    generator.event_sink = events.append

    with pytest.raises(SkillAgentError, match="incomplete"):
        generator.generate(_SPEC)

    assert any(
        event["source"] == "generator"
        and event["kind"] == "model_delta"
        and event.get("content") == "drafting skill layout"
        for event in events
    )
