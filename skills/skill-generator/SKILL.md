---
name: skill-generator
description: Designs, generates, validates, sandbox-tests, and publishes a new skill package from a normalized spec.
version: 0.1.0
owner: skill-agent
runtime: other
status: published
domain:
  - skills
  - generation
  - automation
supported_actions:
  - create
  - generate
  - publish
forbidden_actions:
  - delete
side_effects:
  - file_write
  - network
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Skill Generator

## When to Use

Load this skill when the user wants to create a brand new skill package or update the design of a new generated skill before publishing it.

## How It Works

This skill does not execute through `scripts/run.py`. Instead, once loaded, the agent must collect a complete normalized skill specification and call the `build_skill_from_spec` tool.

## Required Spec Fields

Provide a complete normalized spec with these fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Lowercase hyphen-slug skill id |
| `description` | string | Yes | Operational description of what the skill does |
| `purpose` | string | Yes | Why the skill exists |
| `inputs` | array | Yes | Concrete input descriptions |
| `outputs` | array | Yes | Concrete output descriptions |
| `workflow_steps` | array | Yes | Ordered execution steps |
| `edge_cases` | array | No | Failure cases and corner cases |
| `runtime` | string | Yes | One of: `python`, `node`, `shell`, `other` |
| `test_cases` | array | Yes | Deterministic test cases |

## Test Case Rules

- Every test case must be deterministic and self-contained.
- For read-only or search skills, prefer `fixtures`.
- For expected failures, use `expected_stderr` and `expected_exit_code`.
- Avoid live network URLs in tests; prefer local fixtures or deterministic `file://` inputs.

## Agent Behavior

1. Filter the catalog and select this skill when the user wants to build a new skill.
2. Load this skill to pull the exact spec requirements into context.
3. Ask follow-up questions until the required spec fields are complete.
4. Call `build_skill_from_spec` with the final normalized spec.
5. Summarize the publish result, errors, warnings, and skill path to the user.
