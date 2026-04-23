from __future__ import annotations

from pathlib import Path

from src.skill_agent.observability.logging_utils import get_logger
from .context_engine import ContextEngine
from .curated_memory import CuratedMemoryStore
from .provider import MemoryProvider, NullMemoryProvider, TurnData
from .transcript_store import TranscriptStore

LOGGER = get_logger("skill_agent.memory.manager")


class MemoryManager:
    """
    Orchestrates all 4 memory layers across the turn lifecycle.

    Layers managed:
    - TranscriptStore (L1): exact persisted session history (SQLite)
    - CuratedMemoryStore (L2): durable high-signal snapshot, frozen per session
    - MemoryProvider (L3): pluggable external backend (default: NullMemoryProvider)
    - ContextEngine (L4): context assembly and compression

    Typical usage:
        manager = MemoryManager.create(data_dir=Path("~/.skill_agent"))
        manager.on_session_start()

        # per turn:
        messages = manager.build_context(system_prompt, user_input)
        # ... call LLM with messages ...
        manager.on_turn_end(user_input, assistant_reply)

        manager.on_session_end()

    UI clear/reset:
        manager.reset()  # ends current session and starts a new one
    """

    def __init__(
        self,
        transcript_store: TranscriptStore,
        curated_memory: CuratedMemoryStore,
        context_engine: ContextEngine,
        memory_provider: MemoryProvider | None = None,
    ) -> None:
        self._transcript = transcript_store
        self._curated = curated_memory
        self._context_engine = context_engine
        self._provider: MemoryProvider = memory_provider or NullMemoryProvider()

        self._session_id: str | None = None
        self._turn_index: int = 0
        # In-memory history: flat list of alternating {role: user} / {role: assistant} dicts.
        # Tool calls inside a turn are ephemeral (not stored here).
        self._history: list[dict] = []

    @classmethod
    def create(
        cls,
        data_dir: Path,
        max_context_chars: int = 32_000,
        min_recent_turns: int = 6,
        memory_provider: MemoryProvider | None = None,
    ) -> MemoryManager:
        """
        Create a MemoryManager with the default local storage layout:
            data_dir/transcript.db       — SQLite session transcript
            data_dir/curated_memory.json — curated memory entries
        """
        data_dir = data_dir.expanduser().resolve()
        return cls(
            transcript_store=TranscriptStore(data_dir / "transcript.db"),
            curated_memory=CuratedMemoryStore(data_dir / "curated_memory.json"),
            context_engine=ContextEngine(
                max_context_chars=max_context_chars,
                min_recent_turns=min_recent_turns,
            ),
            memory_provider=memory_provider,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_session_start(self) -> str:
        """
        Open the transcript store, create a session record, and freeze the
        curated memory snapshot for this session.

        Resets in-memory history and turn counter.
        Returns the new session_id.
        """
        self._transcript.open()
        self._session_id = self._transcript.create_session()
        self._curated.load_snapshot()
        self._history = []
        self._turn_index = 0
        LOGGER.info("Memory session started: %s", self._session_id)
        return self._session_id

    def build_context(self, system_prompt: str, user_input: str) -> list[dict]:
        """
        Assemble the full message list to pass to the LLM.

        Calls MemoryProvider.prefetch(), then delegates to ContextEngine which
        injects curated memory into the system message and compresses history
        if needed.

        Must be called after on_session_start().
        """
        self._require_session()
        recalled = self._provider.prefetch(
            session_id=self._session_id,  # type: ignore[arg-type]
            user_input=user_input,
        )
        curated_snapshot = self._curated.get_snapshot()

        messages, stats = self._context_engine.assemble(
            system_prompt=system_prompt,
            history=self._history,
            recalled_memories=recalled,
            curated_snapshot=curated_snapshot,
            user_input=user_input,
        )

        LOGGER.debug(
            "Context built: %d messages, %d chars, compressed=%s, dropped=%d",
            stats.message_count,
            stats.total_chars,
            stats.compressed,
            stats.dropped_turns,
        )
        return messages

    def on_turn_end(
        self,
        user_input: str,
        assistant_reply: str,
        turn_messages: list[dict] | None = None,
    ) -> None:
        """
        Record a completed turn.

        turn_messages — the full slice of new messages produced during the turn:
            [assistant(tool_calls)?, tool?, ..., assistant(final)]
        All messages except the last are treated as intermediates (the actual
        tool-call/response chain) and stored as separate history entries so the
        LLM retains context of what tools were used.  Tool response content is
        truncated to _MAX_TOOL_CONTENT chars to keep context size manageable.

        Persists to TranscriptStore (errors caught/logged; in-memory history
        preserved regardless).  Notifies MemoryProvider (errors caught/logged).
        """
        self._require_session()

        intermediates = self._build_intermediates(turn_messages)
        user_msg: dict = {"role": "user", "content": user_input}
        assistant_msg: dict = {"role": "assistant", "content": assistant_reply}
        self._history.extend([user_msg, *intermediates, assistant_msg])

        try:
            self._transcript.append_turn(
                session_id=self._session_id,  # type: ignore[arg-type]
                turn_index=self._turn_index,
                user_message=user_msg,
                assistant_message=assistant_msg,
                intermediates=intermediates,
            )
        except Exception:
            LOGGER.exception(
                "Failed to persist turn %d to transcript (session=%s). "
                "In-memory history remains intact.",
                self._turn_index,
                self._session_id,
            )

        try:
            self._provider.on_turn_complete(
                TurnData(
                    session_id=self._session_id,  # type: ignore[arg-type]
                    turn_index=self._turn_index,
                    user_input=user_input,
                    assistant_reply=assistant_reply,
                )
            )
        except Exception:
            LOGGER.exception(
                "MemoryProvider.on_turn_complete failed for turn %d (session=%s).",
                self._turn_index,
                self._session_id,
            )

        self._turn_index += 1
        LOGGER.debug("Turn %d recorded (session=%s)", self._turn_index - 1, self._session_id)

    def _build_intermediates(self, turn_messages: list[dict] | None) -> list[dict]:
        """
        Extract the tool-call chain from a turn's message slice.

        turn_messages = result.history[len(initial_messages):]
        The last element is the final assistant reply — everything before it
        is the intermediate tool-call/response chain we want to store.
        """
        if not turn_messages or len(turn_messages) <= 1:
            return []
        return list(turn_messages[:-1])  # drop only the final assistant message

    def on_session_end(self) -> None:
        """
        End the current session and release the database connection.

        Marks the session ended in TranscriptStore, closes the connection,
        and clears in-memory state. Safe to call even if on_session_start()
        was never called.
        """
        if self._session_id is not None:
            try:
                self._transcript.end_session(self._session_id)
            except Exception:
                LOGGER.exception(
                    "Failed to mark session %s as ended in transcript.", self._session_id
                )
        self._transcript.close()
        self._session_id = None
        self._history = []
        self._turn_index = 0
        LOGGER.info("Memory session ended")

    def reset(self) -> str:
        """
        End the current session and start a fresh one.
        Intended for UI "clear session" actions.
        Returns the new session_id.
        """
        self.on_session_end()
        return self.on_session_start()

    # ── Inspection (read-only properties) ─────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def turn_index(self) -> int:
        return self._turn_index

    @property
    def history(self) -> list[dict]:
        """Snapshot of the current in-memory conversation history."""
        return list(self._history)

    @property
    def curated_memory(self) -> CuratedMemoryStore:
        return self._curated

    @property
    def transcript(self) -> TranscriptStore:
        return self._transcript

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_session(self) -> None:
        if self._session_id is None:
            raise RuntimeError(
                "MemoryManager has no active session. Call on_session_start() first."
            )
