# Internal Schemas

This document summarizes the data models the code uses today. These are implementation contracts for `skill-agent`, not a general Agent Skills standard.

See also:

- [architecture.md](./architecture.md) for where each schema moves through the system
- [policy.md](./policy.md) for how status fields and decisions are interpreted
- [validation.md](./validation.md) for how `ValidationReport` is produced
- [skill.md](./skill.md) for how metadata maps onto on-disk skill files

## Generation Pipeline Models

### `SkillRequest`

Raw user input collected by `demo_generation.py` or the agent's `build_skill_from_spec` tool.

```json
{
  "skill_name": "word-counter",
  "skill_description": "Count words in stdin and print the result",
  "sample_inputs": ["hello world"],
  "expected_outputs": ["2"],
  "constraints": ["Return an integer only"],
  "runtime_preference": "python"
}
```

Notes:

- `sample_inputs`, `expected_outputs`, and `constraints` default to empty lists.
- `runtime_preference` defaults to `python`.

### `SkillTestCase`

Execution test attached to a `SkillSpec` or `GeneratedSkill`.

```json
{
  "description": "two words",
  "input": "hello world",
  "expected_output": "2",
  "validation_method": "string_match",
  "fixtures": {
    "notes/example.md": "# Example"
  }
}
```

Field semantics:

- `validation_method` supports `string_match`, `contains`, `regex`, and `manual`.
- In the current sandbox, `manual` falls back to exit-code success only.
- `fixtures` maps relative file paths to file contents written before the test runs.

### `SkillSpec`

Normalized output from the clarifier and input to the generator.

```json
{
  "name": "word-counter",
  "description": "Counts words from stdin.",
  "purpose": "Provide a small text utility.",
  "inputs": ["plain text"],
  "outputs": ["integer word count"],
  "workflow_steps": ["read stdin", "split on whitespace", "print count"],
  "edge_cases": ["empty input"],
  "required_files": ["SKILL.md", "scripts/run.py"],
  "runtime": "python",
  "test_cases": []
}
```

Notes:

- `purpose` is not strictly required for validation to pass, but an empty value can trigger an activation warning.
- `required_files` is descriptive; the validator still checks actual generated files.

### `SkillFile`

Single file inside a generated skill package.

```json
{
  "path": "scripts/run.py",
  "content": "print('hello')",
  "executable": true
}
```

### `SkillMetadata`

Structured metadata attached to the generated artifact.

```json
{
  "name": "word-counter",
  "description": "Reads plain text from stdin and prints the word count as an integer to stdout.",
  "version": "0.1.0",
  "owner": "skill-agent",
  "runtime": "python",
  "status": "generated",
  "entrypoints": [{"type": "skill_md", "path": "SKILL.md"}],
  "domain": ["text", "analysis"],
  "supported_actions": ["count", "read"],
  "forbidden_actions": [],
  "side_effects": []
}
```

Notes:

- `domain`, `supported_actions`, `forbidden_actions`, and `side_effects` feed the runtime policy layer.
- `entrypoints` defaults to a single `SKILL.md` entry.

### `GeneratedSkill`

The artifact produced by `Generator`.

```json
{
  "metadata": {},
  "files": [],
  "scripts": ["scripts/run.py"],
  "references": [],
  "assets": [],
  "tests": [],
  "spec": {},
  "status": "generated"
}
```

Notes:

- `scripts`, `references`, and `assets` are derived from `files`.
- `spec` is preserved so validation can reason about the original clarified intent.

### `ValidationReport`

Result produced across static validation and sandbox execution.

```json
{
  "syntax_pass": true,
  "metadata_pass": true,
  "activation_pass": true,
  "execution_pass": true,
  "regression_pass": true,
  "publishable": true,
  "errors": [],
  "warnings": [],
  "logs": []
}
```

Notes:

- `publishable` is derived from `syntax_pass`, `metadata_pass`, `activation_pass`, `execution_pass`, and the absence of errors.
- `regression_pass` is currently set to `true` by the demo runtime because there is no historical baseline.

### `PublishResult`

Return value from `PublishGateway.evaluate()`.

```json
{
  "skill_name": "word-counter",
  "published": true,
  "skill_path": "skills/word-counter",
  "report": {},
  "message": "Published to skills/word-counter"
}
```

## Runtime Models

### `SkillStub`

Lightweight skill discovered from `skills/<id>/SKILL.md`.

```json
{
  "skill_id": "note-searcher",
  "name": "note-searcher",
  "description": "Searches markdown notes in a specified directory for a keyword.",
  "skill_dir": "skills/note-searcher",
  "domain": ["notes", "markdown", "search", "files"],
  "supported_actions": ["search", "read"],
  "forbidden_actions": ["write", "delete", "update"],
  "side_effects": ["file_read"]
}
```

### `PolicyDecision`

Structured output from `PolicyEngine.evaluate()`.

```json
{
  "selection_status": "matched",
  "capability_status": "supported",
  "execution_status": "allowed",
  "task_status": "unknown",
  "reason": "All policy checks passed - execution allowed",
  "selected_stub": {},
  "logs": []
}
```

Notes:

- `execution_allowed` is a derived property, true only when `execution_status == allowed`.
- `task_status` remains `unknown` until execution or task validation happens.

### `ExecutionResult`

Result returned by `execute_skill()`.

```json
{
  "status": "ok",
  "stdout": "2\n",
  "stderr": "",
  "exit_code": 0,
  "skill_id": "word-counter",
  "logs": [],
  "execution_status": "succeeded",
  "task_status": "satisfied"
}
```

Status meanings:

- `status`: low-level launcher result: `ok`, `error`, or `no_script`.
- `execution_status`: policy/execution layer result: `skipped`, `allowed`, `denied`, `succeeded`, `failed`.
- `task_status`: semantic outcome: `satisfied`, `incorrect`, `unsupported`, `not_applicable`, or `unknown`.

## Enums Used Across the Project

### `Runtime`

```text
python | node | shell | other
```

### `SkillStatus`

```text
draft | generated | validated | published | rejected
```

### Runtime status enums

```text
SelectionStatus   = matched | low_confidence | ambiguous | no_match
CapabilityStatus  = supported | unsupported_operation | unsupported_domain | unknown_capability
ExecutionStatus   = allowed | denied | skipped | succeeded | failed
TaskStatus        = satisfied | unsupported | incorrect | not_applicable | unknown
```

`unsupported_domain` exists in the enum for future expansion, but the current capability checker does not emit it.
