# Implementation Status

This document classifies the recently discussed features as `implemented`, `partially implemented`, or `planned / TODO`.

The labels here are based on real code paths in `src/` and coverage in `tests/`, not on aspirational comments in prompts or `policies/mvp-safe.yaml`.

## Status Meanings

- `implemented`: wired into a real generation, validation, runtime, or UI path today
- `partially implemented`: some code exists, but the feature is incomplete, ad hoc, or not wired end to end
- `planned / TODO`: schema, config, or comments exist, but there is no enforcing runtime path yet

## Validation And Publish Path

| Area | Status | Evidence | Current reality |
| --- | --- | --- | --- |
| Skill validation pipeline | Implemented | `src/skill_agent/generation/pipeline.py`, `demo_generation.py`, `tests/test_pipeline.py` | Generation runs through static validation, sandbox execution, and publish gating, with retry loops for generator, static-validation, and sandbox failures. |
| Policy-as-config YAML validation policy | Partially implemented | `src/skill_agent/validation/policy.py`, `policies/mvp-safe.yaml`, `tests/test_validation_policy.py` | YAML is loaded into `ValidationPolicy`, but only `dependencies`, `activation`, `capability`, and `code_safety` are consumed by the validator today. |
| Static validators | Implemented | `src/skill_agent/validation/validator.py`, `src/skill_agent/validation/checks.py`, `tests/test_validator.py` | The validator enforces syntax, metadata, capability/activation quality, duplicate test-case detection, and deterministic test-input checks. |
| Code safety validators | Implemented (MVP) | `src/skill_agent/validation/checks.py`, `tests/test_validation_policy.py` | Python files are scanned against regex-based risky-pattern rules from policy. Findings affect `code_safety_pass` and therefore `publishable`. |
| Package validators | Planned / TODO | `ValidationPolicy.package`, `policies/mvp-safe.yaml` | Package policy exists only as schema/config. No current validation code checks allowed top-level paths, forbidden nested paths, or size limits from this section. |
| Execution / sandbox validation | Implemented | `src/skill_agent/sandbox/local_runner.py`, `src/skill_agent/sandbox/docker_runner.py`, `tests/test_sandbox.py` | Test cases are materialized and executed against `python scripts/run.py`. The default runner is local and non-isolating; Docker is opt-in. |
| Prompt eval placeholders | Planned / TODO | `ValidationPolicy.prompt_eval`, `policies/mvp-safe.yaml` | Prompt-eval config exists, but there is no `PromptEvalRunner`, no prompt-eval case model, and no publish/runtime enforcement path. |

## Validation Policy Coverage

Sections of `ValidationPolicy` currently enforced:

- `dependencies`
- `activation`
- `capability`
- `code_safety`

Sections currently defined but not enforced:

- `package`
- `prompt_eval`
- `review`

Additional notes:

- `ValidationReport.compute_publishable()` includes `code_safety_pass` as a required condition.
- The current retry loop does not treat code-safety failures as a separate repair stage. They still block publish, but they are not surfaced as their own retry gate before sandbox execution.
- `ValidationReport.requires_review` exists as a field but is not currently set by validator logic.

## Workflow, Review, And UI Integration

| Area | Status | Evidence | Current reality |
| --- | --- | --- | --- |
| Human review gate | Partially implemented | `demo_generation.py`, `src/skill_agent/agent/agent.py`, `app_gradio.py` | There are two real review paths today: a synchronous CLI reviewer callback in `demo_generation.py`, and an in-memory pause/resume review step in the agent/Gradio flow. |
| Workflow runtime / state machine | Partially implemented | `src/skill_agent/workflow/events.py`, `src/skill_agent/workflow/gateway.py` | Event models and a simple wait state exist, but there is no standalone `WorkflowRuntime`, no generic state machine class, and no durable workflow engine. |
| Pending actions | Partially implemented | `SkillChatAgent._pending_review`, `WorkflowState.pending_action_id` | The system can hold one pending review action in memory. There is no persistent store, no generic pending-action abstraction, and no multi-action queue. |
| Interaction gateway between workflow events and chat UI | Implemented | `src/skill_agent/workflow/gateway.py`, `app_gradio.py`, `tests/test_interaction_gateway.py` | `InteractionGateway` is a real adapter layer for rendering workflow events into UI messages and parsing button/free-text decisions back into structured events. |

## What Is Still Missing

Not implemented yet, even if config or comments suggest otherwise:

- policy-driven review routing from `ValidationPolicy.review`
- durable storage for workflow state or pending actions
- a reusable workflow runtime/state machine that coordinates arbitrary paused runs
- prompt-eval execution
- package-policy enforcement
- runtime approval/resume flow for `PolicyEngine` confirmation-gated actions such as `delete` or `network`
