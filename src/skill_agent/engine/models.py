from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Edge(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    when: str | bool | None = None

    model_config = ConfigDict(populate_by_name=True)


class Node(BaseModel):
    id: str
    type: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)
    credential_ref: str | None = None


class WorkflowTrigger(BaseModel):
    type: Literal["on_request", "schedule"] = "on_request"
    description: str = "Run when user requests it from chat or clicks Run in the UI."
    schedule: str | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> WorkflowTrigger:
        if self.type == "schedule" and not self.schedule:
            raise ValueError("Scheduled workflows require a schedule expression")
        return self


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)

    @field_validator("nodes")
    @classmethod
    def nodes_not_empty(cls, nodes: list[Node]) -> list[Node]:
        if not nodes:
            raise ValueError("Workflow must have at least one node")
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow node ids must be unique")
        return nodes

    @model_validator(mode="after")
    def validate_edges(self) -> Workflow:
        node_ids = {node.id for node in self.nodes}
        for edge in self.edges:
            if edge.from_node not in node_ids:
                raise ValueError(f"Edge 'from' node '{edge.from_node}' does not exist")
            if edge.to_node not in node_ids:
                raise ValueError(f"Edge 'to' node '{edge.to_node}' does not exist")
        return self


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class NodeState(BaseModel):
    status: NodeStatus = NodeStatus.PENDING
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExecutionState(BaseModel):
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    status: Literal["pending", "running", "success", "error"] = "pending"
    nodes: dict[str, NodeState]
    workflow: Workflow
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
