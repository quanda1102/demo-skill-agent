from __future__ import annotations

import re
from pathlib import Path

from src.skill_agent.engine.models import Workflow


class WorkflowStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, workflow: Workflow, name: str | None = None) -> Path:
        filename = self._filename(name or workflow.name)
        path = self.root / filename
        path.write_text(workflow.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
        return path

    def load(self, filename: str) -> Workflow:
        path = self.root / filename
        return Workflow.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[str]:
        return sorted(path.name for path in self.root.glob("*.json"))

    @staticmethod
    def _filename(value: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_").lower()
        return f"{stem or 'workflow'}.json"
