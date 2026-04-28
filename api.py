#!/usr/bin/env python3
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.skill_agent.agents import WorkflowBuilderAgent, WorkflowDraftManager
from src.skill_agent.engine import ExecutionStore, SequentialExecutor, Workflow, WorkflowStore
from src.skill_agent.engine.models import ExecutionState, NodeState
from src.skill_agent.engine.registry import get_registry_manifest
from src.skill_agent.memory import MemoryManager
from src.skill_agent.observability.logging_utils import configure_logging

ROOT = Path(__file__).parent
MEMORY_DIR = ROOT / "data" / "memory"
WORKFLOW_DIR = ROOT / "data" / "workflows"

load_dotenv(override=True)
configure_logging()

app = FastAPI(title="AI-Native Workflow Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Single-session global state ──────────────────────────────────────────────
builder = WorkflowBuilderAgent.from_env()
drafts = WorkflowDraftManager()
execution_store = ExecutionStore()
workflow_store = WorkflowStore(WORKFLOW_DIR)
memory = MemoryManager.create(MEMORY_DIR)
memory.on_session_start()

CURRENT_WORKFLOW: Workflow | None = None
CURRENT_EXECUTION_ID: str | None = None
_lock = threading.Lock()


# ── Request / response models ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


class SaveRequest(BaseModel):
    name: str | None = None


class LoadRequest(BaseModel):
    filename: str


# ── Helpers ──────────────────────────────────────────────────────────────────
def _wf_dict(workflow: Workflow | None) -> dict[str, Any] | None:
    if workflow is None:
        return None
    return workflow.model_dump(by_alias=True, mode="json")


def _exec_dict(state: ExecutionState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return state.model_dump(by_alias=True, mode="json")


def _launch_execution(workflow: Workflow, exec_id: str) -> None:
    """Store an initial pending state and start execution in a background thread."""
    initial = ExecutionState(
        execution_id=exec_id,
        workflow_id=workflow.workflow_id,
        workflow=workflow,
        nodes={n.id: NodeState() for n in workflow.nodes},
    )
    execution_store.put(initial)

    def _run() -> None:
        SequentialExecutor(workflow, store=execution_store, execution_id=exec_id).run()

    threading.Thread(target=_run, daemon=True).start()


# ── Routes ───────────────────────────────────────────────────────────────────
@app.post("/api/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID

    draft_result = drafts.handle(req.message)

    if draft_result.draft is not None:
        with _lock:
            CURRENT_WORKFLOW = draft_result.draft.workflow

    # Draft workflow is confirmed/ready → execute
    if draft_result.workflow is not None and draft_result.ready:
        wf = draft_result.workflow
        exec_id = str(uuid.uuid4())
        with _lock:
            CURRENT_WORKFLOW = wf
            CURRENT_EXECUTION_ID = exec_id
        _launch_execution(wf, exec_id)
        memory.on_turn_end(req.message, draft_result.message)
        return {
            "assistant_reply": draft_result.message,
            "execution_id": exec_id,
            "workflow": _wf_dict(wf),
        }

    # Draft is in progress (not yet confirmed)
    if draft_result.changed or draft_result.draft is not None:
        memory.on_turn_end(req.message, draft_result.message)
        return {
            "assistant_reply": draft_result.message,
            "execution_id": CURRENT_EXECUTION_ID,
            "workflow": _wf_dict(CURRENT_WORKFLOW),
        }

    # No draft matched → fall back to workflow builder (may use LLM)
    build = builder.build(req.message)
    if build.workflow is None:
        reply = "\n".join(build.notes) or "Không thể tạo workflow. Vui lòng thử lại."
        memory.on_turn_end(req.message, reply)
        return {
            "assistant_reply": reply,
            "execution_id": CURRENT_EXECUTION_ID,
            "workflow": _wf_dict(CURRENT_WORKFLOW),
        }

    wf = build.workflow
    exec_id = str(uuid.uuid4())
    with _lock:
        CURRENT_WORKFLOW = wf
        CURRENT_EXECUTION_ID = exec_id
    _launch_execution(wf, exec_id)

    lines = [
        f"Đã tạo và bắt đầu thực thi workflow **{wf.name}**.",
        *[f"- {note}" for note in build.notes],
    ]
    reply = "\n".join(lines)
    memory.on_turn_end(req.message, reply)
    return {
        "assistant_reply": reply,
        "execution_id": exec_id,
        "workflow": _wf_dict(wf),
    }


@app.get("/api/execution/{execution_id}")
def get_execution(execution_id: str) -> dict[str, Any]:
    state = execution_store.get(execution_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _exec_dict(state)  # type: ignore[return-value]


@app.get("/api/workflow/current")
def get_current_workflow() -> dict[str, Any] | None:
    return _wf_dict(CURRENT_WORKFLOW)


@app.post("/api/workflow/run")
def run_current_workflow() -> dict[str, Any]:
    global CURRENT_EXECUTION_ID
    if CURRENT_WORKFLOW is None:
        raise HTTPException(status_code=400, detail="No active workflow")
    exec_id = str(uuid.uuid4())
    with _lock:
        CURRENT_EXECUTION_ID = exec_id
    _launch_execution(CURRENT_WORKFLOW, exec_id)
    return {
        "execution_id": exec_id,
        "status": "pending",
        "message": f"Đang chạy workflow **{CURRENT_WORKFLOW.name}**...",
    }


@app.post("/api/workflow/save")
def save_workflow(req: SaveRequest) -> dict[str, Any]:
    if CURRENT_WORKFLOW is None:
        raise HTTPException(status_code=400, detail="No active workflow")
    path = workflow_store.save(CURRENT_WORKFLOW, req.name or None)
    return {"message": f"Đã lưu workflow `{path.name}`.", "filename": path.name}


@app.get("/api/workflow/list")
def list_workflows() -> list[str]:
    return workflow_store.list()


@app.post("/api/workflow/load")
def load_workflow(req: LoadRequest) -> dict[str, Any]:
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID
    try:
        wf = workflow_store.load(req.filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow '{req.filename}' not found")
    with _lock:
        CURRENT_WORKFLOW = wf
        CURRENT_EXECUTION_ID = None
    drafts.reset()
    return _wf_dict(wf)  # type: ignore[return-value]


@app.delete("/api/session")
def clear_session() -> dict[str, str]:
    global CURRENT_WORKFLOW, CURRENT_EXECUTION_ID
    with _lock:
        CURRENT_WORKFLOW = None
        CURRENT_EXECUTION_ID = None
    drafts.reset()
    memory.reset()
    return {"status": "cleared"}


@app.get("/api/registry")
def get_registry() -> dict[str, Any]:
    return get_registry_manifest()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
