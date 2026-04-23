from __future__ import annotations

from pathlib import Path

from .sanitize import clean

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return clean((_PROMPTS_DIR / name).read_text(encoding="utf-8"))
