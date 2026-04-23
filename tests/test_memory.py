from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.skill_agent.memory import (
    ContextEngine,
    CuratedMemoryStore,
    MemoryManager,
    MemoryProvider,
    TranscriptStore,
    TurnData,
)


# ── TranscriptStore ────────────────────────────────────────────────────────────


class TestTranscriptStore:
    def test_open_creates_schema(self, tmp_path: Path) -> None:
        store = TranscriptStore(tmp_path / "t.db")
        store.open()
        assert (tmp_path / "t.db").exists()
        store.close()

    def test_open_is_idempotent(self, tmp_path: Path) -> None:
        store = TranscriptStore(tmp_path / "t.db")
        store.open()
        store.open()  # should not raise
        store.close()

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        store = TranscriptStore(tmp_path / "t.db")
        store.open()
        store.close()
        store.close()  # should not raise

    def test_context_manager(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            assert sid

    def test_create_and_end_session(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            assert len(sid) == 36  # UUID format
            store.end_session(sid)  # should not raise

    def test_append_and_read_turn(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            store.append_turn(
                sid,
                turn_index=0,
                user_message={"role": "user", "content": "hello"},
                assistant_message={"role": "assistant", "content": "hi there"},
            )
            msgs = store.get_session_messages(sid)

        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "hi there"

    def test_append_multiple_turns(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            for i in range(3):
                store.append_turn(
                    sid,
                    turn_index=i,
                    user_message={"role": "user", "content": f"user {i}"},
                    assistant_message={"role": "assistant", "content": f"assistant {i}"},
                )
            msgs = store.get_session_messages(sid)

        assert len(msgs) == 6

    def test_search_within_session(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            store.append_turn(
                sid, 0,
                {"role": "user", "content": "tell me about dragons"},
                {"role": "assistant", "content": "Dragons are mythical creatures"},
            )
            results = store.search("dragons", session_id=sid)

        assert len(results) >= 1
        assert any("dragons" in r["content"].lower() for r in results)

    def test_search_cross_session(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid1 = store.create_session()
            store.append_turn(
                sid1, 0,
                {"role": "user", "content": "unique_keyword_xyz"},
                {"role": "assistant", "content": "I see the unique_keyword_xyz"},
            )
            sid2 = store.create_session()
            store.append_turn(
                sid2, 0,
                {"role": "user", "content": "something else"},
                {"role": "assistant", "content": "something else"},
            )
            results = store.search("unique_keyword_xyz")

        assert len(results) >= 1

    def test_get_session_messages_returns_empty_for_no_turns(self, tmp_path: Path) -> None:
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            msgs = store.get_session_messages(sid)
        assert msgs == []

    def test_requires_open_before_use(self, tmp_path: Path) -> None:
        store = TranscriptStore(tmp_path / "t.db")
        with pytest.raises(RuntimeError, match="not open"):
            store.create_session()

    def test_assistant_tool_calls_serialized(self, tmp_path: Path) -> None:
        tool_calls = [{"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}}]
        with TranscriptStore(tmp_path / "t.db") as store:
            sid = store.create_session()
            store.append_turn(
                sid, 0,
                {"role": "user", "content": "do something"},
                {"role": "assistant", "content": "done", "tool_calls": tool_calls},
            )
            msgs = store.get_session_messages(sid)

        assistant_msg = msgs[1]
        assert "tool_calls" in assistant_msg
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "foo"


# ── CuratedMemoryStore ─────────────────────────────────────────────────────────


class TestCuratedMemoryStore:
    def test_empty_file_returns_empty_snapshot(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        store.load_snapshot()
        assert store.get_snapshot() == []

    def test_add_and_retrieve_entry(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        entry = store.add_entry("user prefers dark mode", tags=["ui"])
        assert entry.id
        assert entry.content == "user prefers dark mode"
        assert "ui" in entry.tags

        all_entries = store.get_all_entries()
        assert len(all_entries) == 1
        assert all_entries[0].id == entry.id

    def test_snapshot_frozen_after_load(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        store.add_entry("existing entry")
        store.load_snapshot()
        snapshot_before = store.get_snapshot()

        # Add entry AFTER snapshot was loaded
        store.add_entry("new entry after snapshot")

        # Snapshot should not change within the session
        snapshot_after = store.get_snapshot()
        assert len(snapshot_before) == len(snapshot_after) == 1

    def test_remove_by_id(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        e1 = store.add_entry("entry one")
        e2 = store.add_entry("entry two")

        removed = store.remove_entry_by_id(e1.id)
        assert removed is True

        remaining = store.get_all_entries()
        assert len(remaining) == 1
        assert remaining[0].id == e2.id

    def test_remove_by_id_not_found(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        result = store.remove_entry_by_id("nonexistent-id")
        assert result is False

    def test_remove_by_id_never_removes_wrong_entry(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        e = store.add_entry("some content")
        # Attempt removal with a prefix of the real ID — must not remove
        partial_id = e.id[:8]
        removed = store.remove_entry_by_id(partial_id)
        assert removed is False
        assert len(store.get_all_entries()) == 1

    def test_get_snapshot_raises_before_load(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "mem.json")
        with pytest.raises(RuntimeError, match="load_snapshot"):
            store.get_snapshot()

    def test_atomic_write_creates_file(self, tmp_path: Path) -> None:
        store = CuratedMemoryStore(tmp_path / "sub" / "mem.json")
        store.add_entry("test")
        assert (tmp_path / "sub" / "mem.json").exists()

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        mem_file = tmp_path / "mem.json"
        mem_file.write_text("not valid json", encoding="utf-8")
        store = CuratedMemoryStore(mem_file)
        store.load_snapshot()
        assert store.get_snapshot() == []


# ── ContextEngine ──────────────────────────────────────────────────────────────


class TestContextEngine:
    def _make_history(self, n: int) -> list[dict]:
        msgs = []
        for i in range(n):
            msgs.append({"role": "user", "content": f"user message {i}"})
            msgs.append({"role": "assistant", "content": f"assistant reply {i}"})
        return msgs

    def test_assemble_no_history(self) -> None:
        engine = ContextEngine(max_context_chars=10_000, min_recent_turns=3)
        messages, stats = engine.assemble(
            system_prompt="You are a helpful assistant.",
            history=[],
            recalled_memories=[],
            curated_snapshot=[],
            user_input="hello",
        )
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "hello"
        assert stats.dropped_turns == 0
        assert not stats.compressed

    def test_assemble_injects_curated_memory_into_system(self) -> None:
        from src.skill_agent.memory.curated_memory import MemoryEntry
        engine = ContextEngine()
        entry = MemoryEntry(id="1", content="user likes Python", created_at="", tags=[])
        messages, _ = engine.assemble(
            system_prompt="Base prompt.",
            history=[],
            recalled_memories=[],
            curated_snapshot=[entry],
            user_input="hi",
        )
        system_content = messages[0]["content"]
        assert "user likes Python" in system_content
        assert "Persistent Memory" in system_content

    def test_assemble_injects_recalled_memories(self) -> None:
        engine = ContextEngine()
        messages, _ = engine.assemble(
            system_prompt="Base.",
            history=[],
            recalled_memories=["previously discussed topic A"],
            curated_snapshot=[],
            user_input="hi",
        )
        system_content = messages[0]["content"]
        assert "previously discussed topic A" in system_content
        assert "Recalled Context" in system_content

    def test_no_compression_when_under_limit(self) -> None:
        engine = ContextEngine(max_context_chars=100_000, min_recent_turns=3)
        history = self._make_history(5)
        messages, stats = engine.assemble(
            system_prompt="sys",
            history=history,
            recalled_memories=[],
            curated_snapshot=[],
            user_input="new question",
        )
        assert stats.dropped_turns == 0
        assert not stats.compressed
        # system + 10 history messages + user = 12
        assert len(messages) == 12

    def test_compression_drops_oldest_pairs(self) -> None:
        # 10 pairs × 31 chars ≈ 310 chars; limit of 120 forces compression
        engine = ContextEngine(max_context_chars=120, min_recent_turns=1)
        history = self._make_history(10)
        messages, stats = engine.assemble(
            system_prompt="sys",
            history=history,
            recalled_memories=[],
            curated_snapshot=[],
            user_input="new",
        )
        assert stats.compressed
        assert stats.dropped_turns > 0
        # Remaining messages should still be well-formed pairs
        body = messages[1:-1]  # strip system and user
        assert len(body) % 2 == 0
        for i in range(0, len(body), 2):
            assert body[i]["role"] == "user"
            assert body[i + 1]["role"] == "assistant"

    def test_min_recent_turns_floor_respected(self) -> None:
        engine = ContextEngine(max_context_chars=50, min_recent_turns=2)
        history = self._make_history(10)
        _, stats = engine.assemble(
            system_prompt="s",
            history=history,
            recalled_memories=[],
            curated_snapshot=[],
            user_input="q",
        )
        # We can drop at most 10-2=8 pairs; min 2 pairs must remain
        assert stats.dropped_turns <= 8

    def test_system_message_always_first(self) -> None:
        engine = ContextEngine()
        messages, _ = engine.assemble(
            system_prompt="THE SYSTEM PROMPT",
            history=self._make_history(2),
            recalled_memories=[],
            curated_snapshot=[],
            user_input="hi",
        )
        assert messages[0]["role"] == "system"
        assert "THE SYSTEM PROMPT" in messages[0]["content"]

    def test_user_input_always_last(self) -> None:
        engine = ContextEngine()
        messages, _ = engine.assemble(
            system_prompt="sys",
            history=self._make_history(3),
            recalled_memories=[],
            curated_snapshot=[],
            user_input="final question",
        )
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "final question"


# ── MemoryManager ──────────────────────────────────────────────────────────────


class TestMemoryManager:
    def _make_manager(self, tmp_path: Path) -> MemoryManager:
        return MemoryManager.create(data_dir=tmp_path / "memory")

    def test_full_lifecycle(self, tmp_path: Path) -> None:
        manager = self._make_manager(tmp_path)
        sid = manager.on_session_start()
        assert sid
        assert manager.session_id == sid
        assert manager.turn_index == 0

        messages = manager.build_context("You are helpful.", "what is 2+2?")
        assert messages[0]["role"] == "system"
        assert messages[-1]["content"] == "what is 2+2?"

        manager.on_turn_end("what is 2+2?", "It is 4.")
        assert manager.turn_index == 1
        assert len(manager.history) == 2

        manager.on_session_end()
        assert manager.session_id is None
        assert manager.history == []

    def test_history_persisted_to_transcript(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "memory"
        manager = MemoryManager.create(data_dir=data_dir)
        sid = manager.on_session_start()
        manager.on_turn_end("hello", "hi")
        manager.on_session_end()

        # Verify persistence by reading the transcript directly
        with TranscriptStore(data_dir / "transcript.db") as store:
            msgs = store.get_session_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["content"] == "hi"

    def test_reset_starts_new_session(self, tmp_path: Path) -> None:
        manager = self._make_manager(tmp_path)
        sid1 = manager.on_session_start()
        manager.on_turn_end("turn1", "reply1")

        sid2 = manager.reset()
        assert sid2 != sid1
        assert manager.turn_index == 0
        assert manager.history == []

    def test_build_context_requires_session(self, tmp_path: Path) -> None:
        manager = self._make_manager(tmp_path)
        with pytest.raises(RuntimeError, match="on_session_start"):
            manager.build_context("sys", "user input")

    def test_on_turn_end_requires_session(self, tmp_path: Path) -> None:
        manager = self._make_manager(tmp_path)
        with pytest.raises(RuntimeError, match="on_session_start"):
            manager.on_turn_end("x", "y")

    def test_on_session_end_safe_without_start(self, tmp_path: Path) -> None:
        manager = self._make_manager(tmp_path)
        manager.on_session_end()  # should not raise

    def test_curated_memory_snapshot_stable_across_turns(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "memory"
        manager = MemoryManager.create(data_dir=data_dir)
        manager.on_session_start()

        # Add entry after session starts (should NOT appear in this session's context)
        manager.curated_memory.add_entry("late entry")

        messages = manager.build_context("sys", "hi")
        system_content = messages[0]["content"]
        assert "late entry" not in system_content

        manager.on_session_end()

        # Next session picks up the entry
        manager2 = MemoryManager.create(data_dir=data_dir)
        manager2.on_session_start()
        messages2 = manager2.build_context("sys", "hi")
        assert "late entry" in messages2[0]["content"]
        manager2.on_session_end()

    def test_provider_prefetch_injected_into_context(self, tmp_path: Path) -> None:
        class SnippetProvider(MemoryProvider):
            def prefetch(self, session_id: str, user_input: str) -> list[str]:
                return ["relevant snippet from provider"]

            def on_turn_complete(self, turn: TurnData) -> None:
                pass

        data_dir = tmp_path / "memory"
        manager = MemoryManager.create(data_dir=data_dir, memory_provider=SnippetProvider())
        manager.on_session_start()
        messages = manager.build_context("sys", "hi")
        system_content = messages[0]["content"]
        assert "relevant snippet from provider" in system_content
        manager.on_session_end()

    def test_provider_on_turn_complete_called(self, tmp_path: Path) -> None:
        recorded: list[TurnData] = []

        class RecordingProvider(MemoryProvider):
            def prefetch(self, session_id: str, user_input: str) -> list[str]:
                return []

            def on_turn_complete(self, turn: TurnData) -> None:
                recorded.append(turn)

        manager = MemoryManager.create(
            data_dir=tmp_path / "memory", memory_provider=RecordingProvider()
        )
        manager.on_session_start()
        manager.on_turn_end("question", "answer")
        assert len(recorded) == 1
        assert recorded[0].user_input == "question"
        assert recorded[0].assistant_reply == "answer"
        assert recorded[0].turn_index == 0
        manager.on_session_end()

    def test_create_factory_makes_nested_dirs(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "deep" / "nested" / "dir"
        manager = MemoryManager.create(data_dir=data_dir)
        manager.on_session_start()
        manager.on_session_end()
        assert (data_dir / "transcript.db").exists()


# ── Integration: SkillChatAgent with MemoryManager ─────────────────────────────


class TestAgentMemoryIntegration:
    def _make_agent(self, tmp_path: Path) -> Any:
        from src.skill_agent.agent.agent import SkillChatAgent
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = {"role": "assistant", "content": "reply", "tool_calls": None}
        return SkillChatAgent(
            provider=mock_provider,
            generator_provider=mock_provider,
            skills_dir=tmp_path / "skills",
            workspace_dir=tmp_path / "ws",
        )

    def test_state_messages_reflects_memory_manager_history(self, tmp_path: Path) -> None:
        agent = self._make_agent(tmp_path)
        assert agent.state.messages == []

        assert agent.memory_manager is not None
        agent.memory_manager.on_turn_end("hello", "hi")
        msgs = agent.state.messages
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "hello"}
        assert msgs[1] == {"role": "assistant", "content": "hi"}

    def test_reset_session_clears_history(self, tmp_path: Path) -> None:
        agent = self._make_agent(tmp_path)
        assert agent.memory_manager is not None
        agent.memory_manager.on_turn_end("hello", "hi")
        assert len(agent.state.messages) == 2

        agent.reset_session()
        assert agent.state.messages == []

    def test_reset_session_creates_new_session_id(self, tmp_path: Path) -> None:
        agent = self._make_agent(tmp_path)
        assert agent.memory_manager is not None
        sid1 = agent.memory_manager.session_id
        agent.reset_session()
        assert agent.memory_manager.session_id != sid1

    def test_memory_manager_auto_created_at_workspace_memory_dir(self, tmp_path: Path) -> None:
        agent = self._make_agent(tmp_path)
        assert agent.memory_manager is not None
        assert agent.memory_manager.session_id is not None
        agent.memory_manager.on_session_end()
        # transcript.db created under workspace_dir/.memory
        assert (tmp_path / "ws" / ".memory" / "transcript.db").exists()

    def test_custom_memory_manager_used_when_provided(self, tmp_path: Path) -> None:
        from src.skill_agent.agent.agent import SkillChatAgent
        from unittest.mock import MagicMock

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = {"role": "assistant", "content": "reply", "tool_calls": None}

        custom_manager = MemoryManager.create(data_dir=tmp_path / "custom_memory")
        agent = SkillChatAgent(
            provider=mock_provider,
            generator_provider=mock_provider,
            skills_dir=tmp_path / "skills",
            workspace_dir=tmp_path / "ws",
            memory_manager=custom_manager,
        )
        assert agent.memory_manager is custom_manager
        assert (tmp_path / "custom_memory" / "transcript.db").exists()
        custom_manager.on_session_end()
