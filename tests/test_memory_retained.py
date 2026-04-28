from __future__ import annotations

from src.skill_agent.memory import ContextEngine


def test_memory_context_engine_is_retained() -> None:
    messages, stats = ContextEngine(max_context_chars=500).assemble(
        system_prompt="system",
        history=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        recalled_memories=[],
        curated_snapshot=[],
        user_input="next",
    )
    assert messages[0]["role"] == "system"
    assert messages[-1]["content"] == "next"
    assert stats.message_count == 4
