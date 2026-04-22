---
name: broken-skill
description: Intentionally broken skill that always crashes at execution time. Used to verify runtime error handling.
version: 0.1.0
owner: skill-agent
runtime: python
status: draft
domain:
  - testing
  - debug
supported_actions:
  - crash
  - fail
forbidden_actions: []
side_effects: []
entrypoints:
  - type: skill_md
    path: SKILL.md
---

# Broken Skill

This skill is intentionally broken. Its run script always raises a RuntimeError.
It is used to verify that the runtime handles execution failures gracefully.

## Invocation

```bash
echo '{}' | python scripts/run.py
```
