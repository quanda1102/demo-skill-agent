---
name: echo-tool-v2
description: Reads input from stdin and writes it back to stdout unchanged. Should be used when testing pipeline connectivity or verifying data passthrough.
version: 0.1.0
owner: skill-agent
runtime: python
status: generated
domain:
  - testing
  - pipeline
  - debugging
supported_actions:
  - echo
  - passthrough
  - read
forbidden_actions:
  - file_write
  - file_delete
  - file_read
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Echo Tool v2

A simple stdin-to-stdout passthrough utility for testing and debugging agentic pipelines.

## When to Use

Use this skill when you need to:
- Verify pipeline connectivity between components
- Test data passthrough through a pipeline stage
- Debug data flow in agentic systems
- Validate that input reaches the expected output unchanged

## How It Works

1. Reads all available input from stdin until EOF
2. Writes the input back to stdout exactly as received
3. Exits with code 0 on success

## Usage

```bash
echo "hello" | python scripts/run.py
```

The tool accepts any text input (single or multiple lines) and echoes it back unchanged.

## Edge Cases

- **Empty input**: Produces empty output (no output written)
- **Binary/non-UTF-8 data**: May produce encoding errors or partial output
- **Very large input**: May exceed memory limits; use with caution for large payloads

## Invocation

This skill is invoked by piping data into `scripts/run.py`. No arguments are required.