# Current Limitations

This document describes the current limitations of the skill-agent prototype, grounded in the actual implementation. Each limitation includes where it comes from in the code, its category, and an honest assessment of severity.

See also [policy.md](./policy.md) for what is explicitly reserved for later and [validation.md](./validation.md) for what the validator and sandbox actually check today.

---

## 1. Sandbox provides no isolation by default

**What the limitation is:** The default `SandboxRunner` alias points to `LocalSandboxRunner` (see `src/skill_agent/sandbox/__init__.py`). This runner materializes skill files into a `tempfile.TemporaryDirectory` and executes the script via `subprocess.run` with no namespace isolation, no `seccomp`, no chroot, no resource limits, and no network restriction. A skill script runs with the same user account and full environment as the parent process.

**Where it comes from:** `src/skill_agent/sandbox/local_runner.py` — the subprocess call has no isolation wrapper. `demo_generation.py`, `demo_runtime.py`, and `app_gradio.py` all default to this runner.

**Docker runner:** `DockerSandboxRunner` adds real constraints — `--network none`, `--memory 256m`, `--cpus 0.5`, a non-root `sandbox` user, and container-level process isolation. However it is opt-in via a `--docker` CLI flag, requires the `skill-agent-sandbox:latest` image to be pre-built manually, and is not the default in any demo.

**Sandbox isolation matrix:**

| Dimension | LocalSandboxRunner (default) | DockerSandboxRunner (opt-in) |
|---|---|---|
| Filesystem | None | Partial (mount + non-root user, no `--read-only`) |
| Network | None | Yes (`--network none` by default) |
| Process isolation | None | Partial (container namespace, no explicit `--pids-limit`) |
| Memory limit | None | 256 MB default |
| CPU limit | None | 0.5 cores default |
| Production-grade? | No | Prototype-grade |

---

## 2. Runtime executor ignores `runtime:` metadata

**What the limitation is:** The `runtime:` field in `SKILL.md` frontmatter supports `python | node | shell | other` at the schema level. However, `execute_skill()` in `src/skill_agent/runtime/executor.py` unconditionally constructs the command as `["python", str(skill.run_script)]`. A skill declaring `runtime: node` or `runtime: shell` will fail with a Python error at execution time, not a useful error message.

**Where it comes from:** `executor.py` line 47 (hardcoded `"python"` prefix). The generator system prompt also only instructs the LLM to write Python, so in practice all generated skills are Python, but manually authored or legacy skills may declare other runtimes.

**Notable example:** `skills/scrape-links/` and `skills/skill-generator/` declare `runtime: other`. The skill-generator is handled as a special case by the agent (it triggers `build_skill_from_spec` instead of `execute_skill`), but this is done through special-case logic in `agent.py`, not through the runtime abstraction.


---

## 3. Skill selection is pure lexical token overlap

**What the limitation is:** `select_skill()` in `src/skill_agent/runtime/selector.py` tokenizes both the request and each skill's `name + description` using `re.findall(r"[a-z]+", text.lower())` and computes the size of the intersection. Score is the number of shared lowercase letter-only tokens.

This means:
- Synonyms score zero ("tally" for a word-counting skill)
- Paraphrases score zero ("compute frequency" for a word-counting skill)
- Short or generic skill descriptions create ambiguity with unrelated requests
- Skills with verbose descriptions have an unfair scoring advantage

The `filter_skills` tool in `agent.py` adds +2 for `supported_actions` overlap and -3 for `forbidden_actions` overlap, which is a heuristic improvement but still fully lexical.

**Where it comes from:** `selector.py` lines 88-90. No embeddings, TF-IDF, or NLP library is used anywhere in the codebase.


---

## 4. Task verification cannot prove correctness

**What the limitation is:** The sandbox verifies test cases using one of three methods:
- `string_match`: `actual.strip() == expected.strip()` — exact string comparison
- `contains`: substring check
- `regex`: regex match anywhere in stdout
- `manual`: always returns `True` unconditionally

What this cannot prove:
- Correctness on inputs not in the test suite
- Whether side effects (files written, network calls made, env variables set) are correct
- Safety on adversarial input (e.g., path traversal strings, malformed JSON, very large inputs)
- That a skill is not simply hardcoding expected outputs for known test inputs

**Regression testing:** `regression_pass` is hardcoded to `True` in both `LocalSandboxRunner.run()` and `DockerSandboxRunner.run()` with the comment `# no prior versions in demo`. No comparison against any prior published version of a skill exists.

**No-test escape hatch:** A skill with zero test cases passes sandbox validation with a warning and `execution_pass = True`. The `compute_publishable()` logic in `models.py` does not require a minimum number of test cases.

**Where it comes from:** `src/skill_agent/sandbox/local_runner.py` lines 16-27 (verification logic), line 57 (regression stub). `src/skill_agent/models.py` lines 109-114 (publishable computation).


---

## 5. Generator relies on prompt constraints to stay stable

**What the limitation is:** The generator's correctness depends heavily on the LLM following constraints stated in the system prompt (`src/skill_agent/prompts/generator_system.md`). The prompt instructs the model to:
- Write only Python (stdlib only)
- Avoid external dependencies
- Write files in a specific order (`SKILL.md` first, then `scripts/run.py`)
- Use only deterministic test case outputs
- Keep scope narrow

None of these constraints are enforced at the structural level. If the model ignores them, the result reaches static validation and may produce confusing errors there instead of a clear generation error.

**Key fragility points:**
- `AgentLoop` has a hard 30-iteration cap (`_MAX_ITERATIONS = 30` in `loop.py`). Complex generations that require many tool calls can hit this limit.
- The `SkillBuilder` silently replaces metadata if `set_metadata` is called twice — the second call wins with no warning.
- The static import scanner (`_THIRD_PARTY_IMPORT_RE` in `validator.py`) uses a fixed allowlist. Packages not on the list (e.g., `anthropic`, `transformers`, `numpy`) escape detection unless they appear in a `requirements.txt`.

---

## 6. Publish gateway is not atomic and not idempotent

**What the limitation is:** `materialize_skill()` in `publisher.py` writes files to `skills/<name>/` one at a time. A crash or exception mid-write leaves a partial skill on disk that will be discovered by `discover_skills()` at runtime, potentially causing errors when the partial SKILL.md or missing `run.py` is loaded.

Additionally:
- Publishing to `skills/<name>/` silently overwrites any existing skill with the same name.
- The `status:` field is rewritten using a simple string replacement that would fail on malformed frontmatter or if `status:` appears in the SKILL.md body.
- `regression_pass` is not part of `compute_publishable()`, so the publish gate can approve a skill even if regression testing (if it existed) had failed.

**Where it comes from:** `src/skill_agent/publisher.py` — the `materialize_skill()` call is sequential with no rollback.

---

## 7. Policy confirmation gate has no approval workflow

**What the limitation is:** `PolicyEngine` denies any request whose `requested_action` is in the confirmation list (`delete`, `overwrite`, `network` by default). The engine returns `ExecutionStatus.denied` and stops. There is no mechanism for the user to approve the action and resume execution. This means a skill that supports `delete` cannot be used through the current policy layer without removing `delete` from the confirmation list entirely.

**Where it comes from:** `src/skill_agent/runtime/policy.py`. The docs in `docs/policy.md` section 8 acknowledge this is reserved for later.


---

## 8. Gradio app is single-tenant

**What the limitation is:** `app_gradio.py` creates one `SkillChatAgent` instance at module load time as a module-level variable `_AGENT`. All Gradio users share the same agent instance and the same conversation history. Concurrent requests will corrupt the conversation state. The agent also holds a reference to the sandbox runner, which is stateful.

**Where it comes from:** `app_gradio.py` — `_AGENT = SkillChatAgent(...)` at module level.

---

## 9. Path traversal in obsidian-crud with no sandbox

**What the limitation is:** `skills/obsidian-crud/scripts/run.py` accepts file paths from stdin JSON (e.g., `{"operation": "read", "path": "../../etc/passwd"}`). There is no path sanitization or containment to a vault directory. In a non-sandboxed execution context (the default), this is a real path traversal vulnerability.

In the Docker runner this is mitigated by the container filesystem boundary, but the local runner provides no protection.

**Where it comes from:** `skills/obsidian-crud/scripts/run.py` — paths are passed directly to `pathlib.Path`.



---

## 10. Skill metadata contract is evolving faster than the validator

**What the limitation is:** The validator (`validator.py`) checks a specific set of required fields and warns on others. Some existing skills (`broken-skill`) have `supported_actions` values (`crash`, `fail`) outside the documented taxonomy, which produce warnings but not errors. The validator does not enforce that action names come from any controlled vocabulary.

Older skills may be missing newer required fields. The runtime's `check_capability()` handles missing `supported_actions` by returning `unknown_capability` (which allows execution to continue), so legacy skills degrade gracefully but unpredictably.

**Where it comes from:** `src/skill_agent/validator.py` — action name validation is not present; `src/skill_agent/runtime/capability.py` — the fallback to `unknown_capability` for missing metadata.


---

## Summary Table

| # | Limitation | Category | Confidence |
|---|---|---|---|
| 1 | Default sandbox has zero isolation | Sandbox | High |
| 2 | Executor ignores `runtime:` field | Runtime | High |
| 3 | Selection is pure lexical token overlap | Architectural | High |
| 4 | Task verification cannot prove correctness | Sandbox / Test coverage | High |
| 5 | Generator relies on prompt constraints | Prompt / Validator | High |
| 6 | Publish is not atomic or idempotent | Architectural | High |
| 7 | Confirmation gate has no approval workflow | Architectural | High |
| 8 | Gradio app is single-tenant | Architectural | High |
| 9 | Path traversal in obsidian-crud | Sandbox / Security | High |
| 10 | Skill metadata contract evolving ahead of validator | Validator | Medium-High |
