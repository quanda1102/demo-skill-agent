# Architecture

This repo uses the packaged layout under `src/skill_agent` instead of the flat file layout from the original draft spec. That keeps reusable infrastructure importable while still matching the conceptual architecture.

## Demo Stack

- UI: Gradio `Blocks` and `Timer`
- Graph: SVG workflow graph rendered in a dedicated `gr.HTML` panel
- Engine: in-memory workflow and execution state
- Nodes: standalone Python scripts invoked as subprocesses
- Agent layer: ReAct-style `WorkflowBuilderAgent` and `NodeBuilderAgent` built on the retained `AgentLoop`

## Current Flow

```text
User chat
  -> WorkflowDraftManager
  -> WorkflowBuilderAgent ReAct loop
  -> Workflow JSON
  -> SequentialExecutor
  -> built-in node subprocesses
  -> ExecutionState
  -> graph / result / notifications / node outputs render
```

No FastAPI or WebSocket layer exists yet. `gr.Timer` polls the in-memory `ExecutionStore`.

If `MINIMAX_ENDPOINT` is configured, `WorkflowBuilderAgent.from_env()` uses `MinimaxProvider`. Otherwise it uses deterministic fallbacks so the demo remains runnable offline.

## Current Engine Contract

- `Edge.when` controls branch routing.
- `condition` nodes emit `matched`, `branch`, and `reason`.
- Nodes outside the selected branch are marked `skipped`.
- Linear workflows without edges still run sequentially.

## Agent Tools

`WorkflowBuilderAgent` tools:

- `get_registry_manifest`: returns all registered nodes and schemas.
- `build_missing_node`: delegates to `NodeBuilderAgent`.
- `submit_workflow`: validates the final workflow with Pydantic and rejects unknown node types.

`NodeBuilderAgent` tools:

- `get_node_contract`: returns stdin/stdout contract and registry context.
- `write_node_files`: writes `node.py` and `requirements.txt`.
- `test_node`: runs the node through the engine runner with mock params/input.
- `register_node`: adds the node to `NODE_REGISTRY` after a passing test.

## Reusable Infrastructure

The cleanup kept the previous agent loop and memory system:

- `src/skill_agent/agent/loop.py`
- `src/skill_agent/memory/`
- `src/skill_agent/providers/`
- `src/skill_agent/observability/`
- `src/skill_agent/process.py`

These are architecture-neutral enough to support later LLM-driven workflow and node builders.

## Deferred Work

- FastAPI
- WebSockets
- durable execution storage
- credential management
- registry search
- parallel execution
