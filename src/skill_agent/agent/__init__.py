from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "SkillChatAgent",
    "build_skill_from_spec",
]

if TYPE_CHECKING:
    from .agent import SkillChatAgent
    from src.skill_agent.generation.pipeline import build_skill_from_spec


def __getattr__(name: str) -> Any:
    if name == "SkillChatAgent":
        from .agent import SkillChatAgent as _SkillChatAgent

        return _SkillChatAgent
    if name == "build_skill_from_spec":
        from src.skill_agent.generation.pipeline import build_skill_from_spec as _build_skill_from_spec

        return _build_skill_from_spec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
