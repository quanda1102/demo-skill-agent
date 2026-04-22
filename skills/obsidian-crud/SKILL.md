---
name: obsidian-crud
description: Perform create, read, update, and delete operations on Obsidian vault files.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - obsidian
  - vault
  - notes
  - crud
supported_actions:
  - create
  - read
  - update
  - delete
  - write
forbidden_actions: []
side_effects:
  - file_read
  - file_write
  - file_delete
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Obsidian CRUD

## When to Use

Invoke this skill to create, read, update, or delete notes in an Obsidian vault. Supports full lifecycle management of vault files.

## Input Format

Provide a JSON object via stdin:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation` | string | Yes | One of: `create`, `read`, `update`, `delete` |
| `path` | string | Yes | Relative path to the vault file |
| `content` | string | No | File content (required for create/update) |

## Example Input

```json
{"operation": "read", "path": "notes/my-note.md"}
```

## Output

- `create` / `update`: writes the file; prints the path to stdout
- `read`: prints file content to stdout
- `delete`: deletes the file; prints confirmation to stdout

## Edge Cases

- **File not found on read/delete**: exits with non-zero code and error message
- **Missing content on create/update**: exits with non-zero code and error message
- **Invalid operation**: exits with non-zero code and error message

## Invocation

```bash
echo '{"operation": "create", "path": "notes/hello.md", "content": "# Hello"}' | python scripts/run.py
```
