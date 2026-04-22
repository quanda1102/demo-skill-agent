from __future__ import annotations

import demo_generation as dg
from src.skill_agent.generator import SkillAgentError as GeneratorSkillAgentError
from src.skill_agent.models import PublishResult, ValidationReport


def test_run_pipeline_retries_after_generator_api_error(monkeypatch, sample_request, sample_spec, sample_skill):
    calls = {"generator": 0, "publish": 0}

    class FakeClarifier:
        def __init__(self, provider, ask_fn=None):
            pass

        def clarify(self, request):
            return sample_spec

    class FakeGenerator:
        def __init__(self, provider):
            pass

        def generate(self, spec, errors=None):
            calls["generator"] += 1
            if calls["generator"] == 1:
                raise GeneratorSkillAgentError("Generator API error: The read operation timed out")
            return sample_skill

    class FakeValidator:
        def validate(self, skill):
            return ValidationReport(
                syntax_pass=True,
                metadata_pass=True,
                activation_pass=True,
            )

    class FakeSandbox:
        def run(self, skill, report):
            report.execution_pass = True
            report.regression_pass = True
            report.compute_publishable()
            return report

    class FakePublisher:
        def __init__(self, skills_dir):
            self.skills_dir = skills_dir

        def evaluate(self, skill, report, reviewer=None):
            calls["publish"] += 1
            return PublishResult(
                skill_name=skill.metadata.name,
                published=True,
                skill_path=str(self.skills_dir / skill.metadata.name),
                report=report,
                message="published",
            )

    monkeypatch.setattr(dg, "Clarifier", FakeClarifier)
    monkeypatch.setattr(dg, "Generator", FakeGenerator)
    monkeypatch.setattr(dg, "StaticValidator", FakeValidator)
    monkeypatch.setattr(dg, "SandboxRunner", FakeSandbox)
    monkeypatch.setattr(dg, "PublishGateway", FakePublisher)

    result = dg.run_pipeline(sample_request, object(), object())

    assert result.published is True
    assert calls["generator"] == 2
    assert calls["publish"] == 1


def test_run_pipeline_returns_rejected_result_after_final_generator_failure(
    monkeypatch,
    sample_request,
    sample_spec,
):
    class FakeClarifier:
        def __init__(self, provider, ask_fn=None):
            pass

        def clarify(self, request):
            return sample_spec

    class FakeGenerator:
        def __init__(self, provider):
            pass

        def generate(self, spec, errors=None):
            raise GeneratorSkillAgentError("Generator API error: The read operation timed out")

    monkeypatch.setattr(dg, "Clarifier", FakeClarifier)
    monkeypatch.setattr(dg, "Generator", FakeGenerator)
    monkeypatch.setattr(dg, "StaticValidator", lambda: None)
    monkeypatch.setattr(dg, "SandboxRunner", lambda: None)

    result = dg.run_pipeline(sample_request, object(), object())

    assert result.published is False
    assert "Rejected before publish" in result.message
    assert "timed out" in result.message


def test_run_pipeline_retries_on_activation_failure_before_sandbox(
    monkeypatch,
    sample_request,
    sample_spec,
    sample_skill,
):
    calls = {"generator": 0, "sandbox": 0}

    class FakeClarifier:
        def __init__(self, provider, ask_fn=None):
            pass

        def clarify(self, request):
            return sample_spec

    class FakeGenerator:
        def __init__(self, provider):
            pass

        def generate(self, spec, errors=None):
            calls["generator"] += 1
            return sample_skill

    class FakeValidator:
        def validate(self, skill):
            if calls["generator"] == 1:
                return ValidationReport(
                    syntax_pass=True,
                    metadata_pass=True,
                    activation_pass=False,
                    errors=["live URL test case"],
                )
            return ValidationReport(
                syntax_pass=True,
                metadata_pass=True,
                activation_pass=True,
            )

    class FakeSandbox:
        def run(self, skill, report):
            calls["sandbox"] += 1
            report.execution_pass = True
            report.regression_pass = True
            report.compute_publishable()
            return report

    class FakePublisher:
        def __init__(self, skills_dir):
            self.skills_dir = skills_dir

        def evaluate(self, skill, report, reviewer=None):
            return PublishResult(
                skill_name=skill.metadata.name,
                published=True,
                skill_path=str(self.skills_dir / skill.metadata.name),
                report=report,
                message="published",
            )

    monkeypatch.setattr(dg, "Clarifier", FakeClarifier)
    monkeypatch.setattr(dg, "Generator", FakeGenerator)
    monkeypatch.setattr(dg, "StaticValidator", FakeValidator)
    monkeypatch.setattr(dg, "SandboxRunner", FakeSandbox)
    monkeypatch.setattr(dg, "PublishGateway", FakePublisher)

    result = dg.run_pipeline(sample_request, object(), object())

    assert result.published is True
    assert calls["generator"] == 2
    assert calls["sandbox"] == 1
