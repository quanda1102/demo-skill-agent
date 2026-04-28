from __future__ import annotations

from collections import defaultdict, deque
from datetime import timezone
from typing import Callable

from src.skill_agent.engine.models import Edge, ExecutionState, NodeState, NodeStatus, Workflow, utc_now
from src.skill_agent.engine.registry import get_node
from src.skill_agent.engine.runner import run_node_script

StateListener = Callable[[ExecutionState], None]


class ExecutionStore:
    def __init__(self) -> None:
        self._states: dict[str, ExecutionState] = {}

    def put(self, state: ExecutionState) -> None:
        self._states[state.execution_id] = state.model_copy(deep=True)

    def get(self, execution_id: str | None) -> ExecutionState | None:
        if execution_id is None:
            return None
        return self._states.get(execution_id)


class SequentialExecutor:
    def __init__(
        self,
        workflow: Workflow,
        *,
        store: ExecutionStore | None = None,
        on_state: StateListener | None = None,
        execution_id: str | None = None,
    ) -> None:
        self.workflow = workflow
        self.store = store
        self.on_state = on_state
        self._preset_execution_id = execution_id

    def run(self) -> ExecutionState:
        state = self._init_state()
        state.status = "running"
        self._publish(state)

        try:
            self._topological_sort()
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)
            state.finished_at = utc_now()
            self._publish(state)
            return state

        if not self.workflow.edges:
            return self._run_linear_without_edges(state)

        incoming, outgoing = self._edge_maps()
        node_order = {node.id: idx for idx, node in enumerate(self.workflow.nodes)}
        roots = sorted(
            [node.id for node in self.workflow.nodes if not incoming[node.id]],
            key=node_order.get,
        )
        queue: deque[tuple[str, bool]] = deque((node_id, True) for node_id in roots)
        processed_incoming: dict[str, int] = defaultdict(int)
        active_inputs: dict[str, list[dict]] = defaultdict(list)

        while queue:
            node_id, should_run = queue.popleft()
            node_state = state.nodes[node_id]
            if node_state.status != NodeStatus.PENDING:
                continue

            if should_run:
                ok = self._execute_node(state, node_id, self._merge_inputs(active_inputs[node_id]))
                if not ok:
                    return state
                output = node_state.output or {}
            else:
                output = {"skipped": True, "reason": "No active incoming branch selected this node."}
                node_state.status = NodeStatus.SKIPPED
                node_state.output = output
                node_state.finished_at = utc_now()
                self._publish(state)

            for edge in outgoing[node_id]:
                active = should_run and self._edge_is_active(edge, output)
                processed_incoming[edge.to_node] += 1
                if active:
                    active_inputs[edge.to_node].append(output)
                if processed_incoming[edge.to_node] == len(incoming[edge.to_node]):
                    queue.append((edge.to_node, bool(active_inputs[edge.to_node])))

        for node_id, node_state in state.nodes.items():
            if node_state.status == NodeStatus.PENDING:
                node_state.status = NodeStatus.SKIPPED
                node_state.output = {"skipped": True, "reason": "Node was not reachable from any root."}
                node_state.finished_at = utc_now()
                self._publish(state)

        state.status = "success"
        state.finished_at = utc_now()
        self._publish(state)
        return state

    def _run_linear_without_edges(self, state: ExecutionState) -> ExecutionState:
        current_data: dict = {}
        for node in self.workflow.nodes:
            ok = self._execute_node(state, node.id, current_data)
            if not ok:
                return state
            current_data = state.nodes[node.id].output or {}
        state.status = "success"
        state.finished_at = utc_now()
        self._publish(state)
        return state

    def _execute_node(self, state: ExecutionState, node_id: str, input_data: dict) -> bool:
        node_def = self._get_node_def(node_id)
        node_state = state.nodes[node_id]
        node_state.status = NodeStatus.RUNNING
        node_state.started_at = utc_now()
        self._publish(state)

        try:
            node_entry = get_node(node_def.type)
            output = run_node_script(
                node_entry.path,
                node_def.params,
                input_data,
                node_def.credential_ref,
            )
        except Exception as exc:
            node_state.status = NodeStatus.ERROR
            node_state.error = str(exc)
            node_state.finished_at = utc_now()
            state.status = "error"
            state.error = f"Node '{node_id}' ({node_def.type}) failed: {exc}"
            state.finished_at = utc_now()
            self._publish(state)
            return False

        node_state.status = NodeStatus.SUCCESS
        node_state.output = output
        node_state.finished_at = utc_now()
        self._publish(state)
        return True

    def _init_state(self) -> ExecutionState:
        if self._preset_execution_id is not None:
            return ExecutionState(
                execution_id=self._preset_execution_id,
                workflow_id=self.workflow.workflow_id,
                workflow=self.workflow,
                nodes={node.id: NodeState() for node in self.workflow.nodes},
            )
        return ExecutionState(
            workflow_id=self.workflow.workflow_id,
            workflow=self.workflow,
            nodes={node.id: NodeState() for node in self.workflow.nodes},
        )

    def _publish(self, state: ExecutionState) -> None:
        if self.store is not None:
            self.store.put(state)
        if self.on_state is not None:
            self.on_state(state.model_copy(deep=True))

    def _get_node_def(self, node_id: str):
        for node in self.workflow.nodes:
            if node.id == node_id:
                return node
        raise ValueError(f"Node '{node_id}' does not exist")

    def _edge_maps(self) -> tuple[dict[str, list[Edge]], dict[str, list[Edge]]]:
        incoming: dict[str, list[Edge]] = defaultdict(list)
        outgoing: dict[str, list[Edge]] = defaultdict(list)
        for node in self.workflow.nodes:
            incoming.setdefault(node.id, [])
            outgoing.setdefault(node.id, [])
        for edge in self.workflow.edges:
            incoming[edge.to_node].append(edge)
            outgoing[edge.from_node].append(edge)
        return incoming, outgoing

    @staticmethod
    def _edge_is_active(edge: Edge, output: dict) -> bool:
        if edge.when is None:
            return True
        if isinstance(edge.when, bool):
            return output.get("matched") is edge.when
        expected = str(edge.when).strip()
        if expected.lower() == "true":
            return output.get("matched") is True
        if expected.lower() == "false":
            return output.get("matched") is False
        return str(output.get("branch", "")).strip() == expected

    @staticmethod
    def _merge_inputs(inputs: list[dict]) -> dict:
        if not inputs:
            return {}
        if len(inputs) == 1:
            return dict(inputs[0])
        merged: dict = {"_upstream_outputs": inputs}
        for item in inputs:
            merged.update(item)
        return merged

    def _topological_sort(self) -> list[str]:
        in_degree: dict[str, int] = defaultdict(int)
        adj: dict[str, list[str]] = defaultdict(list)
        nodes = {node.id for node in self.workflow.nodes}

        for edge in self.workflow.edges:
            adj[edge.from_node].append(edge.to_node)
            in_degree[edge.to_node] += 1
            in_degree.setdefault(edge.from_node, 0)

        queue = deque([node_id for node_id in sorted(nodes) if in_degree[node_id] == 0])
        order: list[str] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for neighbor in adj[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(nodes):
            raise ValueError("Workflow has a cycle and cannot execute")
        return order


def iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()
