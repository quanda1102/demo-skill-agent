from __future__ import annotations

import json
from dataclasses import dataclass

from .curated_memory import MemoryEntry
from src.skill_agent.observability.logging_utils import get_logger

LOGGER = get_logger("skill_agent.memory.context")


@dataclass
class ContextStats:
    total_chars: int
    message_count: int
    compressed: bool
    dropped_turns: int


class ContextEngine:
    """
    Layer 4 — context-window assembly and compression policy.

    Responsibilities:
    - Inject curated memory and recalled snippets into the system message.
    - Measure context size in characters (≈ tokens, no tokenizer needed).
    - Compress history by dropping oldest complete turn pairs when over limit.
    - Never break turn-pair coherence (user + assistant always stay together).

    Compression policy (v1 — deterministic, no ML summarization):
    - Always keep: system message and the min_recent_turns newest turn pairs.
    - Over limit: drop oldest turn pairs until under limit or at minimum floor.
    - If still over limit after dropping: warn and pass through (no truncation).
    - Summarization of dropped turns is NOT implemented. Noted for future work.

    Context assembly order:
    1. System message  (base prompt + curated memory + recalled snippets)
    2. Compressed history
    3. Current user turn
    """

    def __init__(
        self,
        max_context_chars: int = 32_000,
        min_recent_turns: int = 6,
    ) -> None:
        self._max_chars = max_context_chars
        self._min_recent_turns = min_recent_turns

    def assemble(
        self,
        system_prompt: str,
        history: list[dict],
        recalled_memories: list[str],
        curated_snapshot: list[MemoryEntry],
        user_input: str,
    ) -> tuple[list[dict], ContextStats]:
        """
        Build the full message list for an LLM call.

        Args:
            system_prompt: Base system prompt text.
            history: Prior [user, assistant] dicts — already-recorded turns only.
            recalled_memories: Snippets from MemoryProvider.prefetch() (may be []).
            curated_snapshot: Session-stable curated entries (may be []).
            user_input: The current user message text.

        Returns:
            (messages, stats)
        """
        system_content = self._build_system_content(
            system_prompt, curated_snapshot, recalled_memories
        )

        system_chars = len(system_content)
        user_chars = len(user_input)
        available_for_history = self._max_chars - system_chars - user_chars

        if available_for_history < 0:
            LOGGER.warning(
                "System prompt + user input (%d chars) already exceeds max_context_chars (%d). "
                "No history will be included.",
                system_chars + user_chars,
                self._max_chars,
            )
            available_for_history = 0

        compressed_history, dropped = self._compress_history(history, available_for_history)

        messages: list[dict] = [
            {"role": "system", "content": system_content},
            *compressed_history,
            {"role": "user", "content": user_input},
        ]

        total_chars = sum(len(str(m.get("content") or "")) for m in messages)
        stats = ContextStats(
            total_chars=total_chars,
            message_count=len(messages),
            compressed=dropped > 0,
            dropped_turns=dropped,
        )

        if dropped > 0:
            LOGGER.info(
                "Context compressed: dropped %d old turn pair(s) "
                "(total_chars=%d, limit=%d)",
                dropped, total_chars, self._max_chars,
            )

        return messages, stats

    # ── Private ────────────────────────────────────────────────────────────────

    def _build_system_content(
        self,
        base_prompt: str,
        curated: list[MemoryEntry],
        recalled: list[str],
    ) -> str:
        parts = [base_prompt.rstrip()]
        if curated:
            entries_text = "\n".join(f"- {e.content}" for e in curated)
            parts.append(f"\n\n## Persistent Memory\n{entries_text}")
        if recalled:
            recalled_text = "\n".join(f"- {r}" for r in recalled)
            parts.append(f"\n\n## Recalled Context\n{recalled_text}")
        return "".join(parts)

    def _compress_history(
        self,
        history: list[dict],
        available_chars: int,
    ) -> tuple[list[dict], int]:
        """
        Drop oldest complete turn groups to fit within available_chars.

        A turn group starts with a user message and contains all subsequent
        messages (assistant tool_calls, tool responses, final assistant reply)
        that belong to that turn.  Groups are dropped from the front (oldest first).
        min_recent_turns groups are always preserved regardless of size.

        Returns (compressed_history, dropped_group_count).
        """
        if not history:
            return [], 0
        if available_chars <= 0:
            n_turns = sum(1 for m in history if m.get("role") == "user")
            return [], n_turns

        # Split into turn groups: each starts with a user message.
        groups: list[list[dict]] = []
        for msg in history:
            if msg.get("role") == "user":
                groups.append([msg])
            elif groups:
                groups[-1].append(msg)
            else:
                groups.append([msg])  # orphaned non-user message

        if not groups:
            return history, 0

        def _group_chars(g: list[dict]) -> int:
            total = 0
            for m in g:
                total += len(str(m.get("content") or ""))
                tc = m.get("tool_calls")
                if tc:
                    total += len(json.dumps(tc))
            return total

        def _groups_chars(gs: list[list[dict]]) -> int:
            return sum(_group_chars(g) for g in gs)

        dropped = 0
        max_droppable = max(0, len(groups) - self._min_recent_turns)

        while dropped < max_droppable and _groups_chars(groups[dropped:]) > available_chars:
            dropped += 1

        if _groups_chars(groups[dropped:]) > available_chars:
            LOGGER.warning(
                "History (%d chars) still exceeds available budget (%d chars) after dropping "
                "%d turn(s). min_recent_turns=%d prevents further compression. "
                "Consider raising max_context_chars or lowering min_recent_turns.",
                _groups_chars(groups[dropped:]), available_chars,
                dropped, self._min_recent_turns,
            )

        kept = groups[dropped:]
        return [msg for group in kept for msg in group], dropped
