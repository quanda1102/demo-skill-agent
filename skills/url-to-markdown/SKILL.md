---
name: url-to-markdown
description: Fetch HTML from a URL and convert it to clean markdown, preserving images, tables, and code blocks.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - web
  - markdown
  - conversion
supported_actions:
  - fetch
  - convert
  - write
forbidden_actions:
  - read
  - delete
  - update
side_effects:
  - file_write
  - network
entrypoints:
  - type: skill_md
    path: SKILL.md
  - type: script
    path: scripts/run.py
---

# URL to Markdown Converter

Fetches HTML content from a URL and converts it to clean markdown while preserving structural elements.

## Usage

```bash
python scripts/run.py <url> [output_path]
```

**Arguments:**
- `url` (required): The URL to fetch and convert
- `output_path` (optional): Output markdown file path, defaults to `./output.md`

**Input format (stdin):**
```json
{"url": "https://example.com", "output_path": "page.md"}
```

## Output

Saves the converted markdown to the specified file and prints the file path to stdout.

## Error Handling

- Invalid URL → stderr error message, exit 1
- Non-200 HTTP status → stderr error message, exit 1
- Empty HTML response → stderr warning, exit 1
- No network → stderr error message, exit 1
- Missing URL argument → stderr error message, exit 1

## Dependencies

- requests
- beautifulsoup4
- markdownify