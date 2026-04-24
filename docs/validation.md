# Validation Model

This document describes the validation behavior that is actually enforced today.

See also:

- [status.md](./status.md) for implemented vs partial vs TODO status labels
- [architecture.md](./architecture.md) for where validation sits in the full flow
- [policy.md](./policy.md) for publish gate and runtime policy behavior around these checks
- [schema.md](./schema.md) for `ValidationReport`, `GeneratedSkill`, and `SkillTestCase`
- [skill.md](./skill.md) for the on-disk files being validated

Validation in `skill-agent` currently has three concrete enforcement layers:

1. static validation through `src/skill_agent/validation/validator.py`
2. execution validation through `src/skill_agent/sandbox/`
3. final publish gating through `src/skill_agent/generation/publisher.py`

## 1. Status At A Glance

| Area | Status | Notes |
| --- | --- | --- |
| Static validation | Implemented | Syntax, metadata, activation, test-case determinism, and regex-based code safety are live. |
| Policy-as-config YAML | Partially implemented | The validator consumes only part of `ValidationPolicy`. |
| Package policy | Planned / TODO | `package:` is defined in policy, but not enforced. |
| Prompt eval policy | Planned / TODO | `prompt_eval:` is config only. |
| Review policy in validator | Planned / TODO | `review:` is not used to set `requires_review` or block publish. |
| Sandbox validation | Implemented | Local tempdir execution is default; Docker exists as an opt-in runner. |

## 2. Static Validation

`StaticValidator.validate()` currently computes these report fields before sandbox execution:

- `syntax_pass`
- `metadata_pass`
- `activation_pass`
- `code_safety_pass`

### Syntax Checks

`validate_skill_syntax()` enforces:

- `SKILL.md` must exist in `GeneratedSkill.files`
- `SKILL.md` must contain parseable YAML frontmatter
- frontmatter must include `name` and `description`
- generated file paths must be unique
- every declared script, reference, and asset path must exist in `files`

What it does not do:

- normalize or sanitize paths
- enforce package layout policy from `ValidationPolicy.package`
- lint source code
- execute scripts

### Metadata Checks

`validate_skill_metadata()` enforces:

- frontmatter `name` must match `GeneratedSkill.metadata.name`
- `status` must be a valid `SkillStatus`
- `runtime` must be a valid `Runtime`
- `entrypoints` must be non-empty
- at least one entrypoint must point to `SKILL.md`

Warnings:

- non-semver `version` values are warnings only

### Activation And Capability Checks

`validate_skill_activation()` is policy-driven. It currently uses `policy.activation`, `policy.capability`, and `policy.dependencies`.

Enforced today:

- `domain` must be present
- `supported_actions` must be present
- `side_effects` must use allowed values from policy
- descriptions must meet the minimum length from policy
- descriptions matching forbidden placeholder regexes fail
- duplicate test-case descriptions fail
- live public URLs in test inputs fail

Warnings today:

- descriptions longer than the configured maximum
- descriptions that may lack an action verb
- unknown verbs in `supported_actions` / `forbidden_actions`
- empty `spec.purpose`
- empty `side_effects` when the description implies write/delete/network behavior

Important limitation:

- `policy.activation.require_domain` exists in schema, but the current check always requires a domain regardless of that flag

### Dependency Checks

`_validate_no_external_dependencies()` currently enforces:

- forbidden files from `policy.dependencies.forbidden_files`
- a regex-based third-party import detector, with exceptions from `policy.dependencies.allowed_imports`

This is real validation, but still shallow:

- it checks only file names and a fixed import regex set
- it does not detect dynamic imports reliably
- it does not isolate Python environments at runtime

### Code Safety Checks

`validate_code_safety()` scans Python files against `policy.code_safety.risky_patterns`.

Current behavior:

- `error` severity adds report errors and flips `code_safety_pass` to `False`
- `warning` severity adds report warnings only
- invalid regexes in policy are reported as warnings and skipped

This is implemented today, but still MVP-level:

- it is regex-based, not AST-based
- it only scans `.py` files
- it does not provide runtime enforcement of the same rules

## 3. Policy-As-Config Coverage

`ValidationPolicyLoader` loads YAML from either:

- `SKILL_VALIDATION_POLICY`
- bundled default `policies/mvp-safe.yaml`

Policy sections enforced by validator code today:

- `dependencies`
- `activation`
- `capability`
- `code_safety`

Policy sections defined in schema but not enforced today:

- `package`
- `prompt_eval`
- `review`

Related gaps:

- `ValidationReport.requires_review` exists, but the validator never sets it
- `package.max_file_size_bytes` and `package.max_skill_md_chars` are not checked
- `prompt_eval.required` does not trigger any runner or rejection path

## 4. Sandbox Execution

Sandbox validation happens after static validation in the generation flows.

Available runners:

- `LocalSandboxRunner`: default runner and `SandboxRunner` alias
- `DockerSandboxRunner`: opt-in runner used by `--docker`

### Local Sandbox

`LocalSandboxRunner` does the following:

1. materialize the generated skill into a temporary directory
2. write each test case's `fixtures`
3. execute `python scripts/run.py`
4. validate `stdout`, `stderr`, and exit code
5. append logs and failures to `ValidationReport`

Important implementation details:

- the local runner is not a security boundary
- tests run sequentially in one shared temp directory
- fixtures from an earlier test can persist into later tests
- timeout is 10 seconds per test case

### Docker Sandbox

`DockerSandboxRunner` uses `docker run` with:

- `--network none`
- optional memory and CPU limits
- the skill mounted into `/workspace`

This improves isolation, but it is still:

- opt-in
- dependent on Docker being installed and running
- hardcoded to `python scripts/run.py`

### Validation Methods

The runners currently support:

- `string_match`
- `contains`
- `regex`
- `manual`

`manual` currently means:

- do not assert specific output text
- still require the expected exit code, which defaults to `0`

### No-Test Behavior

If a skill defines no test cases:

- `execution_pass = True`
- `regression_pass = True`
- a warning is added saying execution was skipped vacuously

That is intentional for the prototype, but it is a real publish-gate gap.

### Regression Status

`regression_pass` is currently a placeholder field only:

- it is always set to `True`
- there is no prior-version comparison logic

## 5. Publish Decision

`PublishGateway.evaluate()` is the final publish gate.

A skill is publishable only when:

- `report.publishable` is true
- any optional reviewer callback approves it

`ValidationReport.compute_publishable()` currently requires:

```text
syntax_pass
and metadata_pass
and activation_pass
and code_safety_pass
and execution_pass
and not errors
```

If publish succeeds:

- files are written to `skills/<skill-name>/`
- `SKILL.md` status is rewritten to `published`

If publish fails:

- no files are written
- the rejection reason is returned in `PublishResult.message`

## 6. What Validation Does Not Enforce Yet

Validation does not currently guarantee:

- package layout policy from `ValidationPolicy.package`
- prompt eval execution
- policy-driven review requirements from `ValidationPolicy.review`
- AST-level code-safety analysis
- runtime isolation by default
- semantic correctness beyond the included test cases
- runtime compatibility beyond `python scripts/run.py`
