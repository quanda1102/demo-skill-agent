from __future__ import annotations

import json

import pytest

from src.skill_agent.clarifier import Clarifier, SkillAgentError
from src.skill_agent.models import Runtime, SkillRequest, SkillSpec
from src.skill_agent.provider import ProviderError

_VALID_SPEC = SkillSpec(
    name="word-counter",
    description="Counts the number of words in a line of text read from stdin.",
    purpose="Provide a simple word-counting utility.",
    inputs=["a single line of text"],
    outputs=["integer word count as string"],
    workflow_steps=["read stdin", "split on whitespace", "print count"],
    runtime=Runtime.python,
    required_files=["SKILL.md", "scripts/run.py"],
)

_REQUEST = SkillRequest(
    skill_name="word-counter",
    skill_description="Count words in a line of text",
    runtime_preference=Runtime.python,
)


def _submit_call(spec: SkillSpec) -> dict:
    return {
        "id": "s1",
        "type": "function",
        "function": {
            "name": "submit_spec",
            "arguments": json.dumps(spec.model_dump()),
        },
    }


def test_clarifier_happy_path(mock_provider):
    """Model calls submit_spec directly without asking questions."""
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [_submit_call(_VALID_SPEC)],
            }
        return {"role": "assistant", "content": None, "tool_calls": None}

    mock_provider.invoke.side_effect = mock_invoke
    spec = Clarifier(mock_provider).clarify(_REQUEST)

    assert spec.name == "word-counter"
    assert spec.runtime == Runtime.python
    assert "SKILL.md" in spec.required_files


def test_clarifier_asks_followup_then_submits(mock_provider):
    """Model asks one question then calls submit_spec."""
    call_count = {"n": 0}

    def mock_invoke(messages, tools=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "q1",
                        "type": "function",
                        "function": {
                            "name": "ask_user",
                            "arguments": '{"question": "What runtime do you prefer?"}',
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [_submit_call(_VALID_SPEC)],
        }

    mock_provider.invoke.side_effect = mock_invoke
    answers = iter(["python"])
    spec = Clarifier(mock_provider, ask_fn=lambda q: next(answers)).clarify(_REQUEST)

    assert spec.name == "word-counter"
    assert call_count["n"] == 2


def test_clarifier_raises_when_submit_spec_never_called(mock_provider):
    mock_provider.invoke.return_value = {
        "role": "assistant",
        "content": "Here is my answer.",
        "tool_calls": None,
    }
    with pytest.raises(SkillAgentError, match="never called submit_spec"):
        Clarifier(mock_provider).clarify(_REQUEST)


def test_clarifier_raises_on_api_error(mock_provider):
    mock_provider.invoke.side_effect = ProviderError("connection error")
    with pytest.raises(SkillAgentError, match="API error"):
        Clarifier(mock_provider).clarify(_REQUEST)
