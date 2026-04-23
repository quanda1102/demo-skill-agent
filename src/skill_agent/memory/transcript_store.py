from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.skill_agent.observability.logging_utils import get_logger

LOGGER = get_logger("skill_agent.memory.transcript")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TranscriptStore:
    """
    Layer 1 — exact, persisted session conversation history (SQLite).

    Lifecycle:
        store = TranscriptStore(Path("~/.skill_agent/transcript.db"))
        store.open()
        sid = store.create_session()
        store.append_turn(sid, 0, user_msg, assistant_msg)
        store.end_session(sid)
        store.close()

    Schema:
        sessions(id, started_at, ended_at)
        messages(id, session_id, turn_index, role, content, tool_calls, tool_call_id, ts)

    Search:
        search(query)               — cross-session keyword search (LIKE, no FTS)
        search(query, session_id=s) — scoped to one session

    Thread safety: NOT thread-safe. Designed for single-writer local use.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id         TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        ended_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT    NOT NULL REFERENCES sessions(id),
        turn_index   INTEGER NOT NULL,
        role         TEXT    NOT NULL,
        content      TEXT,
        tool_calls   TEXT,
        tool_call_id TEXT,
        ts           TEXT    NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_messages_session
        ON messages (session_id, turn_index);
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection lifecycle ───────────────────────────────────────────────────

    def open(self) -> None:
        """Open connection and create schema if missing. Idempotent."""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; transactions managed explicitly
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(self._SCHEMA)
        LOGGER.debug("TranscriptStore opened: %s", self._db_path)

    def close(self) -> None:
        """Close the database connection. Safe to call multiple times."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            LOGGER.debug("TranscriptStore closed")

    def __enter__(self) -> TranscriptStore:
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── Session management ─────────────────────────────────────────────────────

    def create_session(self) -> str:
        """Create a new session record and return its UUID."""
        conn = self._require_conn()
        session_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, _now_iso()),
        )
        LOGGER.debug("Session created: %s", session_id)
        return session_id

    def end_session(self, session_id: str) -> None:
        """Stamp the session ended_at timestamp."""
        conn = self._require_conn()
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        LOGGER.debug("Session ended: %s", session_id)

    # ── Append ────────────────────────────────────────────────────────────────

    def append_turn(
        self,
        session_id: str,
        turn_index: int,
        user_message: dict,
        assistant_message: dict,
        intermediates: list[dict] | None = None,
    ) -> None:
        """
        Persist a completed turn atomically.

        Stores the user message, any intermediate tool-call messages
        (assistant messages with tool_calls and role:tool responses), then
        the final assistant message.  All tool_calls fields are JSON-serialized.
        """
        conn = self._require_conn()
        ts = _now_iso()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, turn_index, role, content, tool_calls, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, turn_index, "user", user_message.get("content"), None, ts),
            )
            for msg in (intermediates or []):
                tc = msg.get("tool_calls")
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, turn_index, role, content, tool_calls, tool_call_id, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        turn_index,
                        msg.get("role", ""),
                        msg.get("content"),
                        json.dumps(tc, ensure_ascii=False) if tc else None,
                        msg.get("tool_call_id"),
                        ts,
                    ),
                )
            tc = assistant_message.get("tool_calls")
            conn.execute(
                "INSERT INTO messages "
                "(session_id, turn_index, role, content, tool_calls, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    turn_index,
                    "assistant",
                    assistant_message.get("content"),
                    json.dumps(tc, ensure_ascii=False) if tc else None,
                    ts,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_session_messages(self, session_id: str) -> list[dict]:
        """Return all messages for a session in insertion order."""
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT role, content, tool_calls, tool_call_id "
            "FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def search(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Keyword search over message content (LIKE, case-insensitive).

        If session_id is given, search is scoped to that session.
        Otherwise, cross-session search.

        Note: semantic / vector search is NOT implemented.
        """
        conn = self._require_conn()
        pattern = f"%{query}%"
        if session_id:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_call_id, session_id "
                "FROM messages WHERE session_id = ? AND content LIKE ? LIMIT ?",
                (session_id, pattern, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_call_id, session_id "
                "FROM messages WHERE content LIKE ? LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return [
            {**self._row_to_message(row[:4]), "session_id": row[4]}
            for row in rows
        ]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("TranscriptStore is not open. Call open() first.")
        return self._conn

    @staticmethod
    def _row_to_message(row: tuple) -> dict:
        role, content, tool_calls_json, tool_call_id = row[:4]
        msg: dict = {"role": role, "content": content}
        if tool_calls_json:
            msg["tool_calls"] = json.loads(tool_calls_json)
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        return msg
