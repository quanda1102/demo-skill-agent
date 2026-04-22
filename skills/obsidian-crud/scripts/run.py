#!/usr/bin/env python3
"""Obsidian CRUD — create, read, update, delete vault files."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON input — {exc}", file=sys.stderr)
        sys.exit(1)

    operation = (data.get("operation") or "").lower()
    path = data.get("path", "")
    content = data.get("content", "")

    if not operation:
        print("error: 'operation' is required (create | read | update | delete)", file=sys.stderr)
        sys.exit(1)

    if not path:
        print("error: 'path' is required", file=sys.stderr)
        sys.exit(1)

    target = Path(path)

    if operation == "create":
        if not content:
            print("error: 'content' is required for create", file=sys.stderr)
            sys.exit(1)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(str(target))

    elif operation == "read":
        if not target.exists():
            print(f"error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(target.read_text(encoding="utf-8"))

    elif operation == "update":
        if not content:
            print("error: 'content' is required for update", file=sys.stderr)
            sys.exit(1)
        if not target.exists():
            print(f"error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        target.write_text(content, encoding="utf-8")
        print(str(target))

    elif operation == "delete":
        if not target.exists():
            print(f"error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        target.unlink()
        print(f"deleted: {target}")

    else:
        print(f"error: unknown operation '{operation}' — expected create | read | update | delete", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
