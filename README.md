# skill-agent

`skill-agent` is a local Python prototype for two related workflows:

1. Generate a skill package from a natural-language request.
2. Discover and execute published skills with a small policy-aware runtime.

The primary demo is a Gradio web UI (`app_gradio.py`) that runs a multi-turn agent capable of both executing existing skills and generating new ones on request.

## What Is Implemented

- `app_gradio.py` runs the multi-turn `SkillChatAgent` as a Gradio web UI with a turn-by-turn trace inspector.
- `demo_agent.py` runs the same agent as a CLI chat loop.
- `demo_generation.py` runs a clarify → generate → validate → sandbox → publish pipeline.
- `demo_runtime.py` discovers skills in `skills/`, runs scripted policy scenarios, and prints execution results.
- `src/skill_agent/agent/agent.py` contains the end-to-end agent orchestration, tool surface, and pending review flow.
- `src/skill_agent/validation/validator.py` validates skill structure, frontmatter, metadata consistency, activation quality, and regex-based code safety rules.
- `src/skill_agent/sandbox/` runs test cases in a temporary directory (local) or Docker container (opt-in) and reports execution failures.
- `src/skill_agent/generation/publisher.py` writes publishable skills to disk and stamps `status: published`.
- `src/skill_agent/runtime/` contains discovery, selection, capability checks, policy decisions, loading, and execution.
- `src/skill_agent/workflow/` contains workflow event/state models plus the UI interaction gateway used by Gradio.

## Implementation Status

The repo now has both fully wired features and config/schema placeholders. The table below reflects the current code paths, not TODO comments in prompts or YAML.

| Area | Status | Current reality |
| --- | --- | --- |
| Skill validation pipeline | Implemented | `build_skill_from_spec()` and `demo_generation.run_pipeline()` execute generate → static validate → sandbox → publish with retries. |
| Policy-as-config YAML validation policy | Partially implemented | `ValidationPolicyLoader` is live, but only some policy sections are consumed by validation code. |
| Static validators | Implemented | Syntax, metadata, activation, and deterministic test-case checks run in `StaticValidator`. |
| Code safety validators | Implemented (MVP) | Regex-based risky-pattern checks run during validation and block publish via `code_safety_pass`. |
| Package validators | Planned / TODO | `package:` exists in the policy schema, but no validator enforces it yet. |
| Execution / sandbox validation | Implemented | Local tempdir execution is the default; Docker isolation is opt-in. |
| Prompt eval placeholders | Planned / TODO | `prompt_eval:` exists in policy only; there is no prompt eval runner. |
| Human review gate | Partially implemented | CLI publish review exists, and Gradio can pause after automated checks for approve/reject/needs changes. |
| Workflow runtime / state machine | Partially implemented | Workflow event/state models exist, but there is no standalone `WorkflowRuntime` or generic state machine engine. |
| Pending actions | Partially implemented | The agent keeps one in-memory pending review action; there is no durable store or queue. |
| Interaction gateway between workflow events and chat UI | Implemented | `InteractionGateway` renders/parses workflow events and is wired into `app_gradio.py`. |

See [docs/status.md](docs/status.md) for code-level detail and evidence.

## Current Scope

This is a working local prototype, not a production system.

- All LLM calls use `MinimaxProvider` (MiniMax API).
- Runtime execution assumes `python scripts/run.py` regardless of the `runtime:` field.
- The default sandbox is a local temp directory with no isolation. Docker mode is opt-in.
- The schema supports `python | node | shell | other` runtimes, but only Python is executed.

## Quick Start

Install dependencies:

If `uv` is not installed yet, install it first:

### macOS / Linux
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows (PowerShell)

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Install dependencies

```bash
uv sync --dev
```

Configure environment variables:

```bash
cp .env.example .env
# Edit .env and set MINIMAX_ENDPOINT (required) and MINIMAX_API_KEY (if your endpoint needs one)
```

`MINIMAX_ENDPOINT` is the only required env var. `MINIMAX_API_KEY` is optional — only needed if your endpoint requires a bearer token.

Run the Gradio web UI (primary demo):

```bash
uv run python app_gradio.py --docker
# Then open http://localhost:7860
```

The `--docker` flag is recommended from the start. It runs skill generation tests inside a Docker container with network isolation and resource limits. It requires the sandbox image:

```bash
docker build -t skill-agent-sandbox:latest docker/
```

Run the test suite:

```bash
uv run pytest
```

## Gradio Web UI

The primary end-to-end demo. Runs the full `SkillChatAgent` — the agent can execute existing skills, generate new ones on request, and show a turn-by-turn trace of every model and tool event.

```bash
# Recommended (with Docker sandbox isolation)
uv run python app_gradio.py --docker

# Without Docker (no sandbox isolation)
uv run python app_gradio.py
```

Then open `http://localhost:7860`.

Flags:

- `--docker` uses `DockerSandboxRunner` for skill generation tests. Requires `skill-agent-sandbox:latest` to be built first.

## CLI Agent

Same `SkillChatAgent` as the Gradio UI, in a terminal chat loop:

```bash
uv run python demo_agent.py --docker
```

Flags:

- `--verbose` prints model/tool loop steps.
- `--docker` for Docker sandbox (recommended).

## Skill Generation Demo

Run interactive mode:

```bash
uv run python demo_generation.py
```

Run non-interactive mode:

```bash
uv run python demo_generation.py \
  --name word-counter \
  --description "Count words from stdin and print the result" \
  --sample-input "hello world" \
  --expected-output "2" \
  --constraint "Return an integer only"
```

Useful flags:

- `--verbose` prints spec details, generated files, and validation progress.
- `--no-review` skips the manual approval prompt before publishing.

The pipeline performs up to three generate/repair attempts:

1. `Clarifier` turns `SkillRequest` into `SkillSpec`.
2. `Generator` emits metadata, files, and test cases through tool calls.
3. `StaticValidator` checks syntax, metadata, and activation quality.
4. `SandboxRunner` materializes the skill into a temp directory and runs tests.
5. `PublishGateway` either rejects the skill or writes it under `skills/<skill-name>/`.

## Runtime Demo

Run scripted scenarios:

```bash
uv run python demo_runtime.py
```

This mode runs predefined scenarios and prints policy + execution logs.

## End-to-End Agent
Run the multi-turn agent:

```bash
uv run python demo_agent.py
```

Useful flag:

- `--verbose` prints model/tool loop steps as the agent filters skills, loads selected `SKILL.md`, and executes tools.

The agent uses a global system prompt plus tool-calling:

1. filter skills by `skill_id`, name, description, and metadata
2. decide whether to load a candidate skill
3. inject the selected skill's `SKILL.md` into the current turn only
4. execute the skill or call `build_skill_from_spec` when `skill-generator` is selected
5. append user/assistant turns to preserve multi-turn chat context

## Shared Runtime Flow

The runtime demos and agent share the same core runtime primitives:

1. `discover_skills()` parses frontmatter from each `skills/*/SKILL.md`.
2. `select_skill()` scores candidates by token overlap on name and description.
3. `check_capability()` enforces `supported_actions` and `forbidden_actions`.
4. `PolicyEngine` can deny risky actions such as `delete`, `overwrite`, and `network`.
5. `load_skill()` reads `SKILL.md` and locates `scripts/run.py`.
6. `execute_skill()` runs the Python script and reports both execution and task outcome.

For cleanliness:

- `demo_runtime.py` routes scripted file-writing output into `vault/runtime-demo/`
- `demo_agent.py` uses `vault/agent-demo/` as its shared execution workspace

Sample skills in the repo:

- `word-counter`
- `note-searcher`
- `file-archiver`
- `obsidian-note-writer`
- `obsidian-crud`
- `broken-skill`

## Repository Layout

```text
skill-agent/
├── README.md
├── demo_generation.py
├── demo_runtime.py
├── demo_agent.py
├── docs/
├── skills/
├── src/skill_agent/
└── tests/
```

Important directories:

- `src/skill_agent/` contains the pipeline, validator, sandbox, publisher, and runtime code.
- `skills/` contains published and sample skill packages used by the runtime demo.
- `tests/` covers pipeline models, validator rules, sandbox behavior, runtime logic, and publish policy.
- `docs/` explains the skill contract, schemas, policy, validation model, and design references.

## Documentation Map

- [docs/status.md](docs/status.md): implementation status matrix for validation, review, workflow, and policy-as-config work.
- [docs/architecture.md](docs/architecture.md): high-to-low system architecture from entrypoints down to module boundaries.
- [docs/skill.md](docs/skill.md): the on-disk skill contract and authoring conventions.
- [docs/schema.md](docs/schema.md): the Pydantic models and runtime result shapes used by the code.
- [docs/validation.md](docs/validation.md): what the validator and sandbox actually check today.
- [docs/policy.md](docs/policy.md): the implemented runtime policy and publish gate behavior.
- [docs/policy-ui.vi.md](docs/policy-ui.vi.md): Vietnamese guide explaining `Config` tab policy fields, what they do, and where they sit in the architecture.
- [docs/limitations.md](docs/limitations.md): detailed analysis of current limitations grounded in the implementation.
- [docs/references.md](docs/references.md): external material that informed the design.

## Current Limitations

### Sandbox isolation

The default sandbox (`LocalSandboxRunner`) provides **no isolation**. Skills run as the current user with full filesystem, network, and process access. Docker mode (`--docker` flag) adds memory/CPU limits, network blocking (`--network none`), and a non-root user, but is opt-in and requires a pre-built image (`skill-agent-sandbox:latest`). Most demos default to the local runner.

### Skill selection

Skill selection is **pure lexical token overlap** between the request string and each skill's `name + description`. Synonyms, paraphrases, and domain vocabulary not present verbatim in skill metadata score zero. There is no semantic retrieval or embedding-based matching.

### Task verification

Sandbox tests verify specific output strings for specific inputs via exact match, `contains`, or regex. They cannot prove semantic correctness, detect unintended side effects, handle adversarial input, or substitute for integration testing. `regression_pass` is permanently stubbed to `True` — no comparison against prior skill versions is performed.

### Runtime executor

The executor always launches `python scripts/run.py` regardless of the `runtime:` field in `SKILL.md`. Skills declaring `node`, `shell`, or `other` will silently fail or produce Python errors at execution time.

### No-test skills

A skill with zero test cases passes sandbox validation with a warning. Execution-level checks are bypassed entirely. This is a significant gap in the publish gate.

### Confirmation gate

The policy engine denies `delete`, `overwrite`, and `network` actions with no mechanism to resume after user approval. Confirmation is a hard denial, not a two-step workflow.

### Generator scope

The generator performs best on narrow, single-operation requests with deterministic stdout output. Broad scopes, multi-step workflows, skills requiring external dependencies, and non-deterministic output (timestamps, random values, file paths) reduce generation reliability significantly.

See [docs/limitations.md](docs/limitations.md) for a detailed analysis grounded in the implementation.

## Status

The current state is best described as a working local prototype:

- generation and runtime demos are working
- validation and publish gating are real, but still MVP-level
- workflow/review integration exists for the Gradio path, but not as a general runtime
- some policy/config sections are placeholders only (`package`, `prompt_eval`, most of `review`)

The next step is to harden the current implementation and close the gaps between the demo sandbox, the partial workflow/review path, and a safer runtime.
