# References

These references informed the design of the project, but they are not the source of truth for current behavior.

See also:

- [architecture.md](./architecture.md) for the current system view
- [skill.md](./skill.md) for the current skill contract
- [schema.md](./schema.md) for internal contracts
- [validation.md](./validation.md) for implemented checks
- [policy.md](./policy.md) for implemented decision rules

For what the code does today, use:

- `README.md`
- `docs/skill.md`
- `docs/schema.md`
- `docs/validation.md`
- `docs/policy.md`
- the tests under `tests/`

## Design References

### 1. Anthropic Engineering - Equipping agents for the real world with Agent Skills

Why it mattered:

- gave the repo its directory-based skill shape
- reinforced the `SKILL.md` entrypoint model
- motivated progressive disclosure through `references/` and `assets/`

URL:

- https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

### 2. Anthropic Docs - Skill authoring best practices

Why it mattered:

- informed naming and description expectations
- helped shape the distinction between lightweight discovery metadata and richer body instructions
- influenced the validator's activation-quality checks

URL:

- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

### 3. Agent Skills Specification

Why it mattered:

- provided a neutral directory-based skill format reference
- aligned the repo around `SKILL.md` rather than a custom manifest-only format

URL:

- https://agentskills.io/specification

### 4. Anthropic Engineering - Demystifying evals for AI agents

Why it mattered:

- reinforced the idea that a generated artifact still needs execution checks
- informed the separation between structural validation and environment-aware execution validation

URL:

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

### 5. Tool-calling LLM workflows

Why it mattered:

- the repo's `Clarifier` and `Generator` both use a tool-calling loop
- this shaped the split between free-form model reasoning and schema/tool-constrained output

Implementation note:

- in this repo, that loop is implemented locally in `src/skill_agent/loop.py`
- the default provider is `MinimaxProvider` in `src/skill_agent/provider.py`

## Repo-Specific Design Choices

The following concepts are specific to this codebase rather than copied from an external standard:

- `SkillRequest`
- `SkillSpec`
- `GeneratedSkill`
- `ValidationReport`
- the three-attempt generator repair loop
- the current runtime `PolicyEngine`
- the `SkillTestCase.fixtures` mechanism

Those are best understood from local source and tests, not from the external references above.
