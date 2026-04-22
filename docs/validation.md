# Validation Model

This document describes the validation behavior that is implemented today.

See also:

- [architecture.md](./architecture.md) for where validation sits in the full flow
- [policy.md](./policy.md) for the publish gate and runtime policy decisions around these checks
- [schema.md](./schema.md) for `ValidationReport`, `GeneratedSkill`, and `SkillTestCase`
- [skill.md](./skill.md) for the on-disk files being validated

Validation in `skill-agent` happens in two concrete stages:

1. static validation through `StaticValidator`
2. execution validation through `SandboxRunner`

The publish decision is then made by `PublishGateway`.

## 1. Static Validation

`src/skill_agent/validator.py` produces a `ValidationReport` with three booleans:

- `syntax_pass`
- `metadata_pass`
- `activation_pass`

### Syntax Checks

Static syntax validation currently verifies:

- `SKILL.md` exists in the generated file list
- `SKILL.md` starts with valid YAML frontmatter
- frontmatter includes `name` and `description`
- generated file paths are unique
- every declared script, reference, and asset path exists in `files`

What it does not do:

- lint Python code
- execute scripts
- resolve links embedded in markdown body text

### Metadata Checks

Metadata validation currently verifies:

- frontmatter `name` matches `GeneratedSkill.metadata.name`
- `status` is a valid `SkillStatus`
- `runtime` is a valid `Runtime`
- `entrypoints` is not empty
- at least one entrypoint points to `SKILL.md`

Warnings:

- non-semver `version` values only produce a warning

### Activation Checks

Activation validation exists because a structurally valid skill can still be hard to select correctly.

Current checks:

- `domain` must be present
- `supported_actions` must be present
- `description` must be at least 20 characters
- descriptions containing `TODO`, `FIXME`, `PLACEHOLDER`, or `<...>` fail

Current warnings:

- descriptions longer than 500 characters
- descriptions that may lack an action verb
- empty `spec.purpose`
- empty `side_effects` when the description implies write/delete/network behavior

## 2. Sandbox Execution

`src/skill_agent/sandbox.py` runs test cases after static validation succeeds.

### How the Sandbox Works

The current sandbox is lightweight:

1. create a temporary directory with `tempfile.TemporaryDirectory()`
2. materialize the generated skill into that directory
3. run each `SkillTestCase` against `python scripts/run.py`
4. collect logs, failures, and execution results into the same `ValidationReport`

Important implementation details:

- the sandbox is local only
- it is not containerized
- it is not a security boundary
- test cases run sequentially in one shared temporary directory

That last point matters: files created by an earlier test case can still exist for later test cases in the same sandbox run.

### Fixtures

Before each test case runs, the sandbox writes `tc.fixtures` into the temp directory.

This allows tests for read/search skills such as:

- search in an existing notes folder
- read a nested file path
- operate on a known file tree without forcing the skill to create it first

### Validation Methods

The sandbox supports these checks:

- `string_match`: exact `stdout.strip()` comparison
- `contains`: substring search in `stdout`
- `regex`: regex match against `stdout`
- `manual`: currently treated as "exit code must be 0"

Any unrecognized validation method currently falls back to the same behavior as `manual`.

### Timeout

Each sandbox test case has a hard timeout of 10 seconds.

Timeout outcome:

- `execution_pass = false`
- an error is appended to the report
- a failure log is added

### No-Test Behavior

If a skill defines no test cases:

- `execution_pass` is set to `true`
- `regression_pass` is set to `true`
- a warning is added saying execution was skipped vacuously

This is intentionally permissive for the demo, but it should not be mistaken for strong validation.

## 3. Publish Decision

`src/skill_agent/publisher.py` makes the final publish decision.

A skill is publishable only when:

- `report.publishable` is true
- any optional reviewer approves it

If publish succeeds:

- files are written to `skills/<skill-name>/`
- `SKILL.md` is rewritten from `status: generated` or similar to `status: published`

If publish fails:

- no files are written
- the rejection reason is returned in `PublishResult.message`

## Publishable Means

`ValidationReport.compute_publishable()` currently sets:

```text
publishable =
  syntax_pass
  and metadata_pass
  and activation_pass
  and execution_pass
  and not errors
```

`regression_pass` is tracked in the report, but it is not currently part of the publishable formula.

## Common Failure Modes

Typical reasons a skill is rejected:

- missing or invalid frontmatter in `SKILL.md`
- metadata/frontmatter mismatch
- no capability metadata
- description too short or placeholder-heavy
- sandbox test failure
- missing `scripts/run.py` when tests expect execution

## What Validation Does Not Guarantee

The current validation model is useful, but limited.

It does not guarantee:

- safe execution on untrusted input
- dependency isolation
- network isolation
- correctness beyond the included test cases
- compatibility with runtimes other than Python

That gap is intentional in the current prototype and should be documented clearly when the repo is shared.
