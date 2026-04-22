---
name: note-searcher
description: Searches markdown notes in a specified directory for a keyword and outputs matching filenames, one per line.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - notes
  - markdown
  - search
  - files
supported_actions:
  - search
  - read
forbidden_actions:
  - write
  - delete
  - update
side_effects:
  - file_read
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Note Searcher

Searches markdown files in a directory for a specified keyword and returns matching filenames.

## When to Use

Use this skill when you need to quickly find notes containing a specific topic or keyword without manually browsing through files.

## How to Invoke

Provide a JSON object via stdin with two fields:
- `directory`: Path to the directory containing markdown files
- `keyword`: The term to search for (case-insensitive)

Example input:
```json
{"directory": "notes", "keyword": "meeting"}
```

## Output

- Each matching filename is printed on its own line
- If no matches are found, prints: `No matches found`
- Errors are printed to stderr with a non-zero exit code

## Behavior

1. Validates that the directory exists and is accessible
2. Recursively finds all `.md` files in the directory
3. Searches each file for the keyword (case-insensitive)
4. Returns filenames of all files containing the keyword

## Edge Cases

- **Directory not found**: Prints error to stderr, exits with code 1
- **Empty directory or no markdown files**: Prints `No matches found`
- **Keyword not found**: Prints `No matches found`
- **Non-UTF8 files**: Skipped with warning to stderr