---
name: scrape-links
description: Extract all hyperlinks (href) from a given URL. Reads HTML content from a file:// URL or fetches from HTTP/HTTPS, parses for anchor tags, and returns deduplicated absolute URLs.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - web
  - scraping
  - html
supported_actions:
  - fetch
  - parse
  - extract
forbidden_actions:
  - write
  - delete
  - update
side_effects:
  - file_read
  - network
entrypoints:
  - type: skill_md
    path: SKILL.md
  - type: script
    path: scripts/run.py
---

# scrape-links

Extract all hyperlinks (href) from a given URL.

## Usage

```bash
python scripts/run.py <url>
```

The URL can be:
- `file://<path>` — local HTML file
- `http://...` or `https://...` — remote webpage

## Output

Prints a JSON array of absolute URLs to stdout:
```json
["https://example.com/page1", "https://example.com/page2"]
```

## Examples

Extract links from a local HTML file:
```bash
python scripts/run.py "file://./test.html"
```

Extract links from a remote page:
```bash
python scripts/run.py "https://example.com"
```

## Notes

- Relative URLs are converted to absolute using the page's base URL
- Duplicate links are removed while preserving order
- Empty or missing href attributes are skipped
- Uses only Python standard library (urllib, re, html.parser)