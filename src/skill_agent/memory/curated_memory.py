from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.skill_agent.observability.logging_utils import get_logger

LOGGER = get_logger("skill_agent.memory.curated")

_FILE_VERSION = 1


@dataclass
class MemoryEntry:
    id: str
    content: str
    created_at: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "created_at": self.created_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MemoryEntry:
        return cls(
            id=data["id"],
            content=data["content"],
            created_at=data.get("created_at", ""),
            tags=data.get("tags", []),
        )


class CuratedMemoryStore:
    """
    Layer 2 — durable, file-backed high-signal memory with session-stable snapshot.

    Lifecycle semantics:
    - Call load_snapshot() once at session start. This freezes a copy of the
      file as the "session snapshot" — immutable for the rest of the session.
    - get_snapshot() returns the frozen list. Use this for context injection.
    - add_entry() / remove_entry_by_id() write to the file immediately.
      These changes are NOT visible via get_snapshot() in the current session;
      they take effect on the next session's load_snapshot().
    - get_all_entries() reads the live file (bypasses the snapshot).

    File format: {"version": 1, "entries": [...]}
    Writes are atomic (temp file + os.replace) to prevent partial writes.

    Removal is by exact ID only — never by substring matching — to prevent
    accidental deletion of entries whose content overlaps.
    """

    def __init__(self, memory_file: Path) -> None:
        self._file = memory_file
        self._snapshot: list[MemoryEntry] | None = None

    # ── Session snapshot ────────────────────────────────────────────────────────

    def load_snapshot(self) -> None:
        """Freeze current file state as the session snapshot. Call once per session."""
        self._snapshot = list(self._read_file())
        LOGGER.debug("Curated memory snapshot loaded: %d entries", len(self._snapshot))

    def get_snapshot(self) -> list[MemoryEntry]:
        """Return the frozen session snapshot. Raises if load_snapshot() not called."""
        if self._snapshot is None:
            raise RuntimeError("Call load_snapshot() before get_snapshot().")
        return list(self._snapshot)

    # ── Live writes ─────────────────────────────────────────────────────────────

    def add_entry(self, content: str, tags: list[str] | None = None) -> MemoryEntry:
        """
        Add an entry to the persistent file.
        The entry is NOT added to the current session snapshot.
        """
        entries = self._read_file()
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
            tags=list(tags or []),
        )
        entries.append(entry)
        self._write_file(entries)
        LOGGER.debug("Curated memory entry added: %s", entry.id)
        return entry

    def remove_entry_by_id(self, entry_id: str) -> bool:
        """
        Remove an entry by exact UUID. Returns True if removed, False if not found.
        ID-based removal prevents accidental substring-based deletion.
        """
        entries = self._read_file()
        original_count = len(entries)
        entries = [e for e in entries if e.id != entry_id]
        if len(entries) == original_count:
            return False
        self._write_file(entries)
        LOGGER.debug("Curated memory entry removed: %s", entry_id)
        return True

    def get_all_entries(self) -> list[MemoryEntry]:
        """Read and return current file state, bypassing the session snapshot."""
        return self._read_file()

    # ── File I/O ────────────────────────────────────────────────────────────────

    def _read_file(self) -> list[MemoryEntry]:
        if not self._file.exists():
            return []
        try:
            raw = self._file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or "entries" not in data:
                LOGGER.warning("Curated memory file has unexpected format; treating as empty.")
                return []
            return [MemoryEntry.from_dict(e) for e in data["entries"]]
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
            LOGGER.warning("Could not read curated memory file (%s); treating as empty.", exc)
            return []

    def _write_file(self, entries: list[MemoryEntry]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _FILE_VERSION, "entries": [e.to_dict() for e in entries]},
            ensure_ascii=False,
            indent=2,
        )
        fd, tmp_path = tempfile.mkstemp(
            dir=self._file.parent,
            prefix=".curated_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self._file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
