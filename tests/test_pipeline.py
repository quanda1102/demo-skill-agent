from __future__ import annotations

from src.skill_agent.schemas.skill_model import ValidationReport
from src.skill_agent.generation.pipeline import build_skill_from_spec


def test_build_skill_from_spec_emits_generator_and_sandbox_trace(
    monkeypatch,
    sample_spec,
    sample_skill,
    tmp_path,
):
    class FakeGenerator:
        def __init__(self, provider):
            self.provider = provider

        def generate(self, spec, errors=None):
            assert spec == sample_spec
            if callable(getattr(self, "event_sink", None)):
                self.event_sink(
                    {
                        "source": "generator",
                        "kind": "tool",
                        "name": "write_file",
                        "output": "OK",
                        "msg": "write_file → OK",
                    }
                )
            return sample_skill

    class FakeValidator:
        def validate(self, skill):
            report = ValidationReport(
                syntax_pass=True,
                metadata_pass=True,
                activation_pass=True,
            )
            return report

    class FakeSandbox:
        def run(self, skill, report):
            report.logs.append("[Basic two-word input] pass: 2")
            report.execution_pass = True
            report.regression_pass = True
            report.compute_publishable()
            return report

    monkeypatch.setattr("src.skill_agent.pipeline.Generator", FakeGenerator)
    monkeypatch.setattr("src.skill_agent.pipeline.StaticValidator", FakeValidator)

    result, trace = build_skill_from_spec(
        spec=sample_spec,
        generator_provider=object(),
        skills_dir=tmp_path,
        sandbox_runner=FakeSandbox(),
    )

    assert result.published is True
    assert any(event["source"] == "generator" and event["kind"] == "tool" for event in trace.events)
    sandbox_cases = [event for event in trace.events if event["kind"] == "sandbox_case"]
    assert len(sandbox_cases) == len(sample_spec.test_cases)
    assert sandbox_cases[0]["source"] == "pipeline"
    assert sandbox_cases[0]["description"] == "Basic two-word input"
    assert "exact stdout" in sandbox_cases[0]["rationale"]
    assert "stdout string_match" in sandbox_cases[0]["expectation"]
