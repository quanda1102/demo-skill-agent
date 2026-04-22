---
name: INVALID NAME WITH SPACES
description: Reads all input from stdin and prints it back to stdout exactly as received. Use this skill when you need to test stdin/stdout connectivity or verify data passthrough.
version: 0.1.0
owner: skill-agent
runtime: python
status: generated
domain:
  - io
  - testing
  - passthrough
supported_actions:
  - read
  - echo
  - passthrough
forbidden_actions: []
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Echo Tool

A simple stdin-to-stdout passthrough utility for testing and data pipeline purposes.

## When to Use

- Test stdin/stdout connectivity in an agentic system
- Verify data passthrough in a pipeline
- Debug input handling

## How It Works

1. Reads all input from stdin until EOF is reached
2. Prints the received input exactly as-is to stdout
3. Exits with code 0

## Usage

```bash
echo "hello" | python scripts/run.py
```

The script reads from stdin and writes to stdout. No arguments are required.

## Edge Cases

- **Empty input**: Prints nothing and exits successfully
- **Very large input**: Handled by reading all data in memory
- **Binary data**: Echoed as-is (may produce non-printable output)

## Invocation

```bash
python scripts/run.py
```

Input is passed via stdin (pipe or redirect).