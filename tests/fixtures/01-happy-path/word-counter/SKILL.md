---
name: word-counter
description: Reads plain text from stdin and prints the word count as an integer to stdout.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - text
  - analysis
  - counting
supported_actions:
  - count
  - read
forbidden_actions: []
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Word Counter

A simple utility that counts whitespace-separated words in text input.

## When to Use

Use this skill when you need to quickly count the number of words in a text string. It reads from stdin and outputs an integer representing the word count.

## Usage

Provide text via stdin:

```bash
echo "Hello world this is a test" | python scripts/run.py
```

Or with input redirection:

```bash
python scripts/run.py < input.txt
```

## Behavior

1. Reads all input from stdin as a single string
2. Splits input on whitespace into tokens
3. Filters out empty strings from the split result
4. Counts the remaining tokens
5. Prints the count as an integer to stdout

## Exit Codes

- **0**: Success — word count printed to stdout
- **1**: Empty input — no output produced

## Edge Cases

- **Empty input**: Exits with code 1, no output
- **Only whitespace**: Outputs `0`
- **Multiple consecutive spaces**: Treated as a single separator

## Examples

```bash
# Basic usage
echo "Hello world this is a test" | python scripts/run.py
# Output: 6

# Single word
echo "word" | python scripts/run.py
# Output: 1

# Multiple spaces
echo "one   two   three" | python scripts/run.py
# Output: 3
```