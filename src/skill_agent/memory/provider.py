from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TurnData:
    """Structured record of a completed conversation turn passed to the provider."""
    session_id: str
    turn_index: int
    user_input: str
    assistant_reply: str


class MemoryProvider(ABC):
    """
    Layer 3 — pluggable external memory backend abstraction.

    Implement this interface to connect an external store (vector DB, remote
    API, knowledge graph, etc.). NullMemoryProvider is the safe default.

    Contract:
    - prefetch() is called at turn start, before context assembly.
      Return relevant text snippets; they are injected into the system message.
      Return [] if nothing is relevant or the backend is unavailable.
    - on_turn_complete() is called after a turn finishes successfully.
      Index or sync the turn as the backend requires. Must not raise — use
      defensive error handling inside implementations.

    STUB NOTE: Semantic / vector retrieval is NOT implemented in this codebase.
    NullMemoryProvider is the only fully implemented provider.
    """

    @abstractmethod
    def prefetch(self, session_id: str, user_input: str) -> list[str]:
        """
        Return relevant memory snippets for the current user input.
        Called before context assembly each turn. May return [].
        """
        ...

    @abstractmethod
    def on_turn_complete(self, turn: TurnData) -> None:
        """Called after a turn completes. Index or sync the turn data."""
        ...


class NullMemoryProvider(MemoryProvider):
    """No-op provider. Safe default when no external backend is configured."""

    def prefetch(self, session_id: str, user_input: str) -> list[str]:
        return []

    def on_turn_complete(self, turn: TurnData) -> None:
        pass
