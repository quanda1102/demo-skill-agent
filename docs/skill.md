# Skill Contract

This document describes the skill format that the current codebase actually consumes.

See also:

- [architecture.md](./architecture.md) for where skill files sit in the end-to-end system
- [schema.md](./schema.md) for the in-memory metadata and artifact models
- [validation.md](./validation.md) for how these files are checked before publish
- [policy.md](./policy.md) for how capability metadata controls runtime access

The project follows a directory-based skill model centered around `SKILL.md`, but the runtime and validator only depend on a small subset of that format today.

## Minimal Directory Layout

```text
my-skill/
├── SKILL.md
└── scripts/
    └── run.py
```

Optional directories still make sense for authoring:

```text
my-skill/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

Current implementation details:

- `SKILL.md` is required.
- `scripts/run.py` is the only executable entrypoint the runtime knows how to run.
- `references/` and `assets/` are preserved in generated artifacts and syntax-checked if declared, but the runtime does not actively consume them yet.

## Required Frontmatter

`SKILL.md` must start with valid YAML frontmatter.

Minimum required keys:

- `name`
- `description`

The validator also expects the generated metadata object to stay consistent with the frontmatter.

Recommended frontmatter for this repo:

```yaml
---
name: word-counter
description: Reads plain text from stdin and prints the word count as an integer to stdout.
version: 0.1.0
owner: skill-agent
runtime: python
status: generated
domain:
  - text
  - analysis
supported_actions:
  - count
  - read
forbidden_actions: []
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---
```

## Metadata Fields Used by the Code

The current code reads or validates these fields:

- `name`: skill identifier used in metadata and publish output.
- `description`: used by validator activation checks and runtime selection scoring.
- `version`: validated loosely; non-semver is currently a warning, not a hard failure.
- `owner`: informational only.
- `runtime`: must be one of `python`, `node`, `shell`, or `other`.
- `status`: must be one of `draft`, `generated`, `validated`, `published`, or `rejected`.
- `entrypoints`: must include an entry pointing to `SKILL.md`.
- `domain`: required by activation validation and parsed into runtime stubs.
- `supported_actions`: required by activation validation and enforced by the runtime capability check.
- `forbidden_actions`: optional deny-list enforced before execution.
- `side_effects`: optional in schema, but effectively expected for skills that describe write/delete behavior.

## Capability Semantics

The runtime uses capability metadata conservatively:

- If an action appears in `forbidden_actions`, execution is denied.
- If `supported_actions` exists and the requested action is missing, execution is denied.
- If neither `domain` nor `supported_actions` exists, capability is treated as unknown.
- If only `domain` exists and `supported_actions` is empty, capability is still treated as unknown.

In practice, a publishable skill in this repo should always define:

- `domain`
- `supported_actions`
- `side_effects`

## Runtime Execution Contract

The runtime expects scripts to behave like simple CLI tools:

- read input from `stdin`
- write the main result to `stdout`
- write errors to `stderr`
- return a non-zero exit code on failure

The current executor always runs:

```bash
python scripts/run.py
```

That means:

- Python is the only runtime that is actually executed today.
- Skills declaring `node`, `shell`, or `other` can pass model validation but are not runnable by the current runtime.

## Test Case Contract

Each generated skill can include `SkillTestCase` entries with:

- `description`
- `input`
- `expected_output`
- `validation_method`
- `fixtures`

`fixtures` is important for file-based skills. The sandbox writes these files into the temporary skill directory before each test case runs.

Example:

```json
{
  "description": "keyword search",
  "input": "{\"directory\": \"notes\", \"keyword\": \"meeting\"}",
  "expected_output": "meeting-notes.md",
  "validation_method": "contains",
  "fixtures": {
    "notes/meeting-notes.md": "# Sprint Meeting"
  }
}
```

## How Discovery Works

`discover_skills()` only reads `SKILL.md` frontmatter and creates a lightweight `SkillStub`.

At discovery time, the runtime does not:

- execute scripts
- validate references in the markdown body
- parse advanced manifest files

It only needs enough metadata to rank and gate the skill.

## What Makes a Good Skill in This Repo

A good skill for the current implementation is:

- explicit about what action it supports
- clear enough to be selected by lexical token overlap
- runnable as `python scripts/run.py`
- testable with deterministic stdin/stdout behavior
- honest about side effects

## Current Gaps

These are worth knowing while authoring skills:

- The runtime does not consume markdown body sections programmatically.
- The runtime does not execute multiple entrypoints.
- The validator does not inspect script quality beyond file presence and metadata alignment.
- There is no package-level isolation for dependencies inside a skill directory.
