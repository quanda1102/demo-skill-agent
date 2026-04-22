---
name: file-archiver
description: Moves files matching a glob pattern from a source directory into an archive subdirectory, removing originals after copying.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - files
  - archive
  - storage
supported_actions:
  - move
  - copy
  - archive
  - glob
forbidden_actions: []
side_effects:
  - file_read
  - file_write
  - file_delete
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# File Archiver

Archives files by moving them from a source directory to an archive directory based on a glob pattern.

## When to Use

Use this skill when you need to consolidate files matching a specific pattern (e.g., `*.md`, `*.log`) into a dedicated archive folder while preserving originals until the copy is verified.

## How It Works

1. Reads JSON input from stdin containing `source_dir`, `pattern`, and `archive_dir`
2. Validates that `source_dir` exists and is a directory
3. Creates `archive_dir` (with any missing parent directories) if it doesn't exist
4. Finds files in `source_dir` matching the glob `pattern` (non-recursive)
5. Copies each matched file to `archive_dir`
6. Deletes each original file after successful copy
7. Prints the count of archived files to stdout

## Usage

```bash
echo '{"source_dir": "notes", "pattern": "*.md", "archive_dir": "notes/archive"}' | python scripts/run.py
```

## Input Format

```json
{
  "source_dir": "string",
  "pattern": "glob string",
  "archive_dir": "string"
}
```

## Output

- Success: `archived: N file(s)`
- No matches: `archived: 0 file(s)`

## Error Handling

- Missing or invalid source directory: exit code 1
- Invalid JSON input: exit code 1
- Cannot create archive directory (permissions): exit code 1