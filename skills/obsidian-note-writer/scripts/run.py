from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _slugify_filename(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", title).strip()
    return cleaned or "Untitled Note"


def _unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    for idx in range(2, 1000):
        candidate = base.with_name(f"{stem} {idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not find an available filename")


def main() -> int:
    if "--help" in sys.argv:
        print("Usage: echo '{\"title\": \"Note\", \"content\": \"...\", \"tags\": [\"x\"]}' | python scripts/run.py")
        return 0

    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: Missing JSON input", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Invalid JSON input", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print("Error: Expected a JSON object", file=sys.stderr)
        return 1

    title = _slugify_filename(str(payload.get("title") or "Untitled Note"))
    content = str(payload.get("content") or "")
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        print("Error: tags must be an array", file=sys.stderr)
        return 1

    note_path = _unique_path(Path(f"{title}.md"))
    frontmatter = [
        "---",
        f"title: {title}",
    ]
    if tags:
        frontmatter.append("tags:")
        frontmatter.extend(f"  - {str(tag)}" for tag in tags)
    else:
        frontmatter.append("tags: []")
    frontmatter.append("---")
    body = "\n".join(frontmatter) + "\n\n" + content

    note_path.write_text(body, encoding="utf-8")
    print(f"Created: {note_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
