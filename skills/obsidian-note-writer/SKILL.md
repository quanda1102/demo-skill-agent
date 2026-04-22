---
name: obsidian-note-writer
description: Creates and saves markdown notes in Obsidian-compatible format with YAML frontmatter. Activated when an agent needs to capture information as a persistent, searchable note.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - obsidian
  - notes
  - markdown
supported_actions:
  - create
  - write
  - format
forbidden_actions:
  - delete
side_effects:
  - file_write
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Obsidian Note Writer

## When to Use

Invoke this skill when you need to capture information as a persistent, searchable Obsidian note. This skill creates markdown files with proper YAML frontmatter for tags, dates, and metadata.

## Input Format

Provide a JSON object via stdin with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | The note title |
| `content` | string | Yes | The markdown content |
| `tags` | array | No | List of tags for the note |

## Example Input

```json
{"title": "Meeting Notes", "content": "# Summary\n\nDiscussed the project timeline.", "tags": ["meeting", "project"]}
```

## Output

The script creates a `.md` file in the current directory with Obsidian-compatible frontmatter and writes the created filename to stdout.

## Edge Cases

- **Empty title**: Defaults to "Untitled Note"
- **Empty content**: Creates note with frontmatter only
- **Invalid JSON**: Prints an error to stderr and exits non-zero
- **File exists**: Appends a numeric suffix to avoid overwriting

## Invocation

```bash
echo '{"title": "Note", "content": "Content"}' | python scripts/run.py
```
