# skill-agent

`skill-agent` is now a local demo of an AI-native workflow engine for telecom runbooks.

The current demo path is intentionally simple:

1. User describes a monitoring workflow in chat.
2. `WorkflowBuilderAgent` runs a ReAct loop against registry/schema tools, or uses an offline fallback when no provider is configured.
3. `SequentialExecutor` runs each node script through a stdin/stdout JSON contract and now supports edge-driven branching with `when`.
4. Gradio renders the workflow state with a dedicated graph panel, result card, notification card, node-output logs, and polls in-memory execution state with `gr.Timer`.

FastAPI, WebSockets, persistent storage, and credentials are deferred. Agent-built nodes are implemented as a local sandbox/register loop, but still need stronger safety controls before production use.

## Run

```bash
uv sync --dev
uv run python app.py
```

Then open the Gradio URL printed by the command.

If `MINIMAX_ENDPOINT` is set, the app uses the LLM-backed workflow builder. Without it, the demo uses deterministic fallbacks so the local demo still runs.

Example prompt:

```text
Tôi muốn xử lý cảnh báo node high memory
```

Node Builder demo prompt:

```text
Tôi muốn xử lý cảnh báo node high CPU load
```

## Test

```bash
uv run pytest
```

Current expected result: `26 passed`

## Current Layout

```text
app.py
src/skill_agent/
├── agents/
│   ├── workflow_builder.py
│   └── node_builder.py
├── agent/
│   └── loop.py              # retained reusable tool-calling loop
├── memory/                  # retained conversation memory system
├── engine/
│   ├── models.py
│   ├── registry.py
│   ├── runner.py
│   ├── executor.py
│   ├── render.py
│   └── nodes/
├── observability/
├── providers/
├── process.py
└── prompt_loader.py
```

## Reused Core

- `src/skill_agent/agent/loop.py` remains as the reusable tool-calling loop.
- `src/skill_agent/memory/` remains available for chat/session context.
- `src/skill_agent/providers/`, `observability/`, and `process.py` remain as shared infrastructure.

## Current Behavior

- Workflows can be saved and loaded from `data/workflows`.
- The `condition` builtin node supports if/else-style branching.
- Branch routing is controlled by `edges[].when`.
- Unselected branch nodes are marked `skipped`.
- Notification cards are rendered as structured ticket blocks in the UI.

## Removed Scope

The previous skill-package generator/runtime, sample skills, policy docs, and vault fixtures were removed because they represented a different architecture.
