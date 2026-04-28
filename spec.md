# AI-Native Workflow Engine - Current Implementation Spec

> Status: demo-ready v0.5  
> Last updated: 2026-04-28  
> Scope: telecom / network alarm runbooks rendered and executed in Gradio  
> Stack: Python, Pydantic, Gradio, subprocess nodes, in-memory execution state

## 1. Shared View

This repository is no longer a skill-package generator.
It is now a local workflow engine demo where an agent converts a natural-language runbook request into workflow JSON, validates it with Pydantic, executes it through subprocess nodes, and renders the result in Gradio.

The architecture is split into two stable layers:

- Agent layer: interprets user intent, inspects registry capability, drafts workflow JSON, and can build missing node types.
- Engine layer: validates schema, executes nodes, routes branches, stores execution snapshots, and renders UI state.

The engine does not import or depend on agent internals.
The agent only talks to the engine through explicit tools, registry manifests, and Pydantic models.

## 2. Current Demo Flow

The current end-to-end flow is:

```text
User chat in Gradio
  -> WorkflowDraftManager detects workflow intent
  -> workflow draft is created or updated step by step
  -> each appended node is verified by running the node script
  -> user confirms the draft or clicks Run
  -> SequentialExecutor runs the workflow
  -> ExecutionStore captures execution snapshots
  -> Gradio polls and renders graph, summary, result, notifications, node outputs
```

There is no FastAPI layer and no WebSocket layer.
Gradio uses `gr.Timer` to refresh the UI from an in-memory execution store.

## 3. Repository Layout

```text
app.py
src/skill_agent/
├── agents/
│   ├── workflow_builder.py
│   ├── node_builder.py
│   └── workflow_draft.py
├── agent/
│   └── loop.py
├── engine/
│   ├── models.py
│   ├── registry.py
│   ├── runner.py
│   ├── executor.py
│   ├── storage.py
│   ├── render.py
│   ├── credentials.py
│   └── nodes/
│       ├── builtin/
│       └── agent_built/
├── memory/
├── observability/
├── process.py
└── providers/
```

Reusable core that was intentionally retained:

- `agent/loop.py`: generic tool-calling / ReAct loop.
- `memory/`: session memory and transcript retention.
- `providers/`: LLM provider contracts and provider implementations.
- `process.py`: subprocess helper with timeout and contract handling.
- `observability/`: logging utilities.

Old architecture that was removed:

- skill package schema generation
- skill runtime / sandbox / policy stack
- old skill vault fixtures
- old Gradio skill UI
- old generation pipeline

## 4. Workflow Schema

Implemented in `src/skill_agent/engine/models.py`.

### 4.1 Workflow

```json
{
  "workflow_id": "auto-generated UUID",
  "name": "Monitor BTS_042 RSSI",
  "created_at": "2026-04-28T10:00:00Z",
  "trigger": {
    "type": "on_request",
    "description": "Run when user requests it from chat or clicks Run in the UI.",
    "schedule": null
  },
  "nodes": [],
  "edges": []
}
```

Rules:

- `nodes` must not be empty.
- Node IDs must be unique.
- Every edge `from` and `to` must reference an existing node.
- `credential_ref` is optional.
- Visual layout fields such as `position` are not part of the model.
- The model serializes and loads cleanly with Pydantic.

### 4.2 Trigger

Supported trigger types:

- `on_request`
- `schedule`

Behavior:

- `on_request` is the minimum viable trigger and is the default.
- `schedule` requires a `schedule` string.
- The demo does not yet run a background scheduler.
- Saved scheduled workflows can be loaded, but execution still happens manually through UI or chat.

### 4.3 Node

```json
{
  "id": "n1",
  "type": "fetch_signal",
  "label": "Fetch RSSI from BTS_042",
  "params": {
    "station_id": "BTS_042",
    "metric": "RSSI"
  },
  "credential_ref": null
}
```

### 4.4 Edge

```json
{
  "from": "n1",
  "to": "n2",
  "when": null
}
```

`when` is the branching contract:

- `null`: edge is always eligible.
- string: edge is eligible when upstream output `branch` matches the string.
- boolean: edge is eligible when upstream output `matched` matches the boolean.

This is the current branching mechanism in the engine.

### 4.5 Execution State

Node status values:

- `pending`
- `running`
- `success`
- `error`
- `skipped`

Execution status values:

- `pending`
- `running`
- `success`
- `error`

`skipped` means the node was reachable in the graph but not selected by the active branch path.

## 5. Node Contract

Each node is a standalone Python script.
The engine runs it as a subprocess through `run_node_script()`.

### 5.1 Input contract

Node stdin receives:

```json
{
  "params": {},
  "input": {},
  "credentials": {}
}
```

### 5.2 Output contract

Node stdout must be a JSON object.

Rules enforced by the runner:

- exit code `0` means success
- non-zero exit means failure
- stderr is surfaced as the node error
- stdout must parse to a JSON object
- `.venv/bin/python` inside the node folder is used if present
- otherwise the current interpreter is used
- timeout is currently 30 seconds

This boundary is intentionally simple so node implementations do not depend on engine internals.

## 6. Registry

Implemented in `src/skill_agent/engine/registry.py`.

Registry entry:

```python
class NodeEntry(BaseModel):
    node_type: str
    description: str
    path: str
    params_schema: dict[str, Any]
    output_schema: dict[str, Any]
    built_by: str
```

Registry behavior:

- `NODE_REGISTRY` is an in-memory dictionary.
- Built-in nodes are registered at import time.
- Agent-built nodes are registered with `register_node(entry)`.
- `get_registry_manifest()` returns a capability snapshot for the agent.

The workflow builder reads the full manifest.
Registry search is not implemented yet.

## 7. Built-In Nodes

Current built-in nodes in the registry:

| node_type | Purpose | Key params | Output shape |
| --- | --- | --- | --- |
| `check_dcim_service` | Mock DCIM service lookup by IP. | `ip`, `excluded_services` | `service`, `service_type`, `excluded`, `ip` |
| `check_metric_threshold` | Mock generic metric threshold check. | `metric`, `operator`, `value`, `mock_value`, `unit` | `metric`, `value`, `threshold`, `passed`, `unit` |
| `get_top_processes` | Mock top N processes by RAM or CPU. | `metric`, `limit`, `mock_processes` | `processes`, `metric` |
| `notify_owner` | Notification node that formats a Vietnamese ticket and emits a notification payload. | `owner_type`, `message`, `severity` | `notified`, `owner_type`, `message`, `severity`, `ticket`, `notification_id` |
| `condition` | LLM-assisted if/else decision node with deterministic fallback. | `condition`, `true_branch`, `false_branch`, `field`, `operator`, `value` | `matched`, `branch`, `condition`, `reason`, `condition_source` |
| `fetch_signal` | Simulated BTS signal fetch. | `station_id`, `metric`, `simulate_drop` | `value`, `timestamp`, `station_id`, `metric` |
| `threshold_check` | Numeric comparison node. | `operator`, `value`, `field`, `unit` | `passed`, `threshold`, `operator`, passthrough input |
| `time_window` | Duration condition wrapper. | `duration_seconds`, `condition_field` | `satisfied`, `duration`, passthrough input |
| `aggregate` | Aggregate numeric values. | `function`, `field` | `result`, `count`, `function`, passthrough input |
| `send_alert` | Emit an alert payload. | `message`, `severity`, `condition_field` | `sent`, `alert_id`, `sent_at`, `message`, passthrough input |

`notify_owner` is self-contained inside its own folder and does not depend on a shared `notifications.py`.

### 7.1 Condition node contract

The `condition` node exists to let the workflow express if/else logic without hardcoding branching into the node scripts themselves.

Behavior:

- It inspects the incoming JSON payload.
- It can evaluate a natural-language condition through the configured LLM provider.
- If provider evaluation is unavailable or fails, it falls back to deterministic evaluation using `field`, `operator`, and `value`.
- It emits `matched`, `branch`, `reason`, and `condition_source`.

The engine uses `branch` or `matched` to route edges.

## 8. Execution Engine

Implemented in `src/skill_agent/engine/executor.py`.

### 8.1 Behavior

The executor is currently branching-aware.

Execution steps:

1. Create an `ExecutionState` with all nodes in `pending`.
2. Validate that the graph is acyclic.
3. If the workflow has no edges, run it in linear order.
4. If the workflow has edges, traverse the graph dynamically.
5. Execute a node when it is activated by at least one active upstream path.
6. Route outgoing edges according to `when`.
7. Mark nodes not selected by the active branch path as `skipped`.
8. Stop with `error` on any node failure.
9. Mark workflow `success` when traversal completes.

### 8.2 Branching semantics

Branching is edge-driven, not node-driven.

Rules:

- `when is null`: edge always participates.
- `when is true/false`: compare against upstream output `matched`.
- `when is string`: compare against upstream output `branch`.

This keeps the node implementation simple and moves routing responsibility into the engine.

### 8.3 Merge semantics

Current merge rule:

- if a node has multiple incoming edges and at least one active incoming branch reaches it, the node runs
- upstream outputs are merged shallowly for that node’s input

This is enough for the demo.
It is not yet a full dataflow engine with typed fan-in resolution.

### 8.4 Non-goals

Not implemented yet:

- parallel execution
- async node execution
- durable execution store
- typed branch join resolution
- retry policies per node

## 9. Workflow Builder Agent

Implemented in `src/skill_agent/agents/workflow_builder.py`.

### 9.1 Purpose

The workflow builder translates user intent into valid workflow JSON.
It should know what nodes are available before it drafts the workflow.
If a required node is missing, it can delegate to the node builder.

### 9.2 Behavior

- `WorkflowBuilderAgent.from_env()` picks a provider from environment variables.
- If a provider exists, the builder uses the retained ReAct loop.
- If no provider exists, it falls back to deterministic demo workflows so the app still runs offline.
- The fallback currently recognizes memory, CPU load, CPU usage, and RSSI demos.

### 9.3 Tools

`get_registry_manifest`

- returns current nodes and schemas
- intended first step before workflow drafting

`build_missing_node`

- delegates to `NodeBuilderAgent`
- used when the registry cannot express the desired workflow

`submit_workflow`

- validates the workflow with Pydantic
- rejects unknown node types
- stores the accepted workflow internally

### 9.4 Prompt contract

The system prompt tells the model:

- to reason in Vietnamese
- to inspect the registry first
- to use available nodes before inventing new ones
- to use `condition` when the runbook contains branching logic
- to express branching via `edges[].when`
- to repair workflow JSON if validation fails

## 10. Node Builder Agent

Implemented in `src/skill_agent/agents/node_builder.py`.

### 10.1 Purpose

The node builder creates missing node types as self-contained folders under:

```text
src/skill_agent/engine/nodes/agent_built/<node_type>/
```

### 10.2 Behavior

- It runs as a ReAct loop when a provider exists.
- It writes `node.py` and `requirements.txt`.
- It tests the generated node through the same runner used by the engine.
- It registers the node only after a successful test.

### 10.3 Demo fallback

Without a provider, the builder can still create the demo nodes needed for CPU load scenarios:

- `check_io_stat`
- `check_nfs_mount`

That keeps the demo working offline.

### 10.4 Safety boundary

Current safety is minimal:

- deterministic file writes
- subprocess testing
- registration only after passing test

Not yet present:

- static import allowlist
- code sandboxing beyond subprocess boundaries
- filesystem/network policy enforcement

## 11. Workflow Draft Manager

Implemented in `src/skill_agent/agents/workflow_draft.py`.

This is the stateful layer that the chat UI uses before a user confirms execution.

Behavior:

- detects workflow intent in chat
- creates or updates a workflow draft
- appends nodes one by one
- verifies each node with `run_node_script()`
- keeps the current draft alive across turns
- avoids drifting a memory workflow into an unrelated RSSI/BTS workflow after later user input

User flow:

- when a draft exists, the UI shows the draft graph instead of immediately running the full workflow
- when the user types `confirm`, the draft workflow is executed
- if no draft is active, the app falls back to the workflow builder

## 12. Gradio UI

Implemented in `app.py`.

### 12.1 Layout

- left side: chat history and prompt
- right side: generated task panel
- graph is displayed in its own dedicated div/panel
- logs/details are hidden behind accordions
- the user-facing result is kept prominent

### 12.2 Current UX contract

The UI is optimized for a non-technical client who should be able to tell:

- whether the workflow exists
- whether it ran
- what the result was
- where the notification or output is

### 12.3 Visible sections

- Workflow graph
- Workflow summary
- Result card
- Notification panel
- Node outputs
- Save / load / run controls
- Debug registry manifest

### 12.4 Save / load / run

`WorkflowStore` saves workflow JSON files under `data/workflows`.

Current behavior:

- save active workflow to disk
- load a saved workflow into the current UI state
- run the currently loaded workflow manually
- refresh execution state with `gr.Timer`

## 13. Notification Ticket Rendering

The notification node now produces a ticket-like payload for demo UI rendering.

The ticket format is intentionally structured:

```text
---
[TICKET] <one-line incident description>
Đối tượng : <object>
Chỉ số    : <metric> = <value> (ngưỡng: <threshold>)
Chi tiết  : <facts>
Thời gian : <timestamp>
ID        : <id>
---
```

UI rendering:

- ticket is shown as a white card
- values are laid out in rows
- the most important fields are easy to scan
- the whole block is readable even on a bright screen

The notification node can use an LLM provider to format the ticket, but it falls back to deterministic formatting if the provider is unavailable.

## 14. Current Demo Scenarios

### Demo 1: Node High Memory Usage

Purpose:

- main workflow generation demo
- uses only built-in nodes
- shows chat intent to workflow JSON to execution to notification

Typical workflow:

```text
check_dcim_service
  -> check_metric_threshold(metric=available_ram_gb, operator=<, value=4)
  -> get_top_processes(metric=ram)
  -> notify_owner(owner_type=application)
  -> notify_owner(owner_type=server)
```

### Demo 2: Node High CPU Load

Purpose:

- demo of missing node generation
- intentionally needs `check_io_stat` and `check_nfs_mount`
- shows node builder creating a missing node live

Typical workflow:

```text
check_dcim_service
  -> check_metric_threshold(metric=cpu_load_average, operator=>, value=12)
  -> check_metric_threshold(metric=cpu_usage_percent, operator=>, value=85)
  -> check_io_stat
  -> check_nfs_mount
  -> notify_owner(owner_type=system)
```

### Demo 3: Node High CPU Usage

Purpose:

- backup demo
- lower value than Demo 1 and Demo 2

Typical workflow:

```text
check_dcim_service
  -> check_metric_threshold(metric=cpu_usage_percent, operator=>, value=85)
  -> get_top_processes(metric=cpu)
  -> notify_owner(owner_type=system)
```

### Demo 4: Branching / condition

Purpose:

- shows a real if/else structure
- uses the `condition` node plus `edges[].when`
- demonstrates that the engine can skip unselected branches

Typical pattern:

```text
n1 fetch input
n2 condition
n3 true branch
n4 false branch
n5 join / continue
```

## 15. Testing Status

Current focused tests cover:

- workflow schema validation
- trigger validation
- registry manifest
- node runner contract
- branching executor behavior
- workflow builder ReAct flow
- node builder ReAct flow
- workflow store save/load
- retained memory engine
- notification formatting

Run:

```bash
uv run pytest
```

Current expected result:

```text
26 passed
```

## 16. Known Gaps

Still intentionally deferred:

- FastAPI
- WebSockets
- persistent workflow/execution storage
- credential management
- registry search
- parallel execution
- richer branch join semantics
- multi-session Gradio isolation
- stronger node safety validation

The current implementation is enough for the local demo, but not yet a production workflow runtime.
