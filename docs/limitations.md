# Current Limitations

This document describes the current limitations of the skill-agent prototype, grounded in the actual implementation. Each limitation explains:

- what the limitation is
- why it exists in the current phase
- what practical impact it has
- where it comes from in the code

See also [policy.md](./policy.md) for what is explicitly reserved for later and [validation.md](./validation.md) for what the validator and sandbox actually check today.

---

## 1. Sandbox provides no isolation by default

**What the limitation is:**  
The default `SandboxRunner` alias points to `LocalSandboxRunner` (see `src/skill_agent/sandbox/__init__.py`). This runner materializes skill files into a `tempfile.TemporaryDirectory` and executes the script via `subprocess.run` with no namespace isolation, no `seccomp`, no chroot, no resource limits, and no network restriction. A skill script runs with the same user account and full environment as the parent process.

**Why this exists now:**  
The project started with a local tempdir + subprocess harness because it was the fastest way to prove the end-to-end concept of skill generation, validation, and execution without forcing every developer workflow to depend on Docker setup from day one. The local runner keeps iteration speed high and reduces friction while the core pipeline is still evolving.

**Practical impact:**  
This is acceptable for local, trusted, deterministic demo skills, but it is not a meaningful security boundary. It should not be treated as safe for untrusted code. It also means local execution behavior can depend on the host machine's filesystem, environment, and network.

**Where it comes from:**  
`src/skill_agent/sandbox/local_runner.py` — the subprocess call has no isolation wrapper. `demo_generation.py`, `demo_runtime.py`, and `app_gradio.py` all default to this runner.

**Docker runner:**  
`DockerSandboxRunner` adds real constraints — `--network none`, `--memory 256m`, `--cpus 0.5`, a non-root `sandbox` user, and container-level process isolation. However it is opt-in via a `--docker` CLI flag, requires the `skill-agent-sandbox:latest` image to be pre-built manually, and is not the default in any demo.

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

**What the limitation is:**  
The `runtime:` field in `SKILL.md` frontmatter supports `python | node | shell | other` at the schema level. However, `execute_skill()` in `src/skill_agent/runtime/executor.py` unconditionally constructs the command as `["python", str(skill.run_script)]`. A skill declaring `runtime: node` or `runtime: shell` will fail with a Python error at execution time, not a useful error message.

**Why this exists now:**  
The generator prompt and current skill flow are optimized around Python-only execution. Multi-runtime support was left open at the metadata level to avoid baking in a future limitation too early, but the actual runtime was intentionally kept narrow so the team could stabilize one execution path first.

**Practical impact:**  
The metadata currently overstates what the runtime can really execute. In practice, generated skills are Python skills, and manually authored non-Python skills will fail in a confusing way unless special-cased elsewhere.

**Where it comes from:**  
`executor.py` line 47 (hardcoded `"python"` prefix). The generator system prompt also only instructs the LLM to write Python, so in practice all generated skills are Python, but manually authored or legacy skills may declare other runtimes.

**Notable example:**  
`skills/scrape-links/` and `skills/skill-generator/` declare `runtime: other`. The skill-generator is handled as a special case by the agent (it triggers `build_skill_from_spec` instead of `execute_skill`), but this is done through special-case logic in `agent.py`, not through the runtime abstraction.

---

## 3. Skill selection is pure lexical token overlap

**What the limitation is:**  
`select_skill()` in `src/skill_agent/runtime/selector.py` tokenizes both the request and each skill's `name + description` using `re.findall(r"[a-z]+", text.lower())` and computes the size of the intersection. Score is the number of shared lowercase letter-only tokens.

This means:
- Synonyms score zero ("tally" for a word-counting skill)
- Paraphrases score zero ("compute frequency" for a word-counting skill)
- Short or generic skill descriptions create ambiguity with unrelated requests
- Skills with verbose descriptions have an unfair scoring advantage

The `filter_skills` tool in `agent.py` adds +2 for `supported_actions` overlap and -3 for `forbidden_actions` overlap, which is a heuristic improvement but still fully lexical.

**Why this exists now:**  
The selector was intentionally kept simple so the team could debug routing behavior and separate selection problems from policy problems and execution problems. A richer routing system would likely be more accurate, but would also make early failures much harder to interpret.

**Practical impact:**  
The current runtime is explainable and easy to debug, but brittle on real-world phrasing. It performs best when skill descriptions are narrow and user requests align closely with the same vocabulary.

**Where it comes from:**  
`selector.py` lines 88-90. No embeddings, TF-IDF, or NLP library is used anywhere in the codebase.

---

## 4. Task verification cannot prove correctness

**What the limitation is:**  
The sandbox verifies test cases using one of three methods:
- `string_match`: `actual.strip() == expected.strip()` — exact string comparison
- `contains`: substring check
- `regex`: regex match anywhere in stdout
- `manual`: always returns `True` unconditionally

What this cannot prove:
- Correctness on inputs not in the test suite
- Whether side effects (files written, network calls made, env variables set) are correct
- Safety on adversarial input (e.g., path traversal strings, malformed JSON, very large inputs)
- That a skill is not simply hardcoding expected outputs for known test inputs

**Why this exists now:**  
The project prioritized deterministic, cheap-to-run task verification over semantic evaluation. This was a deliberate prototype choice: exact/string-based checks are easy to implement, easy to explain, and good enough to validate narrow local skills.

**Practical impact:**  
A passing skill is only proven against the specific tests provided, not against broader behavior. This means “publishable” currently means “passes current checks,” not “robustly correct under varied inputs.”

**Regression testing:**  
`regression_pass` is hardcoded to `True` in both `LocalSandboxRunner.run()` and `DockerSandboxRunner.run()` with the comment `# no prior versions in demo`. No comparison against any prior published version of a skill exists.

**No-test escape hatch:**  
A skill with zero test cases passes sandbox validation with a warning and `execution_pass = True`. The `compute_publishable()` logic in `models.py` does not require a minimum number of test cases.

**Where it comes from:**  
`src/skill_agent/sandbox/local_runner.py` lines 16-27 (verification logic), line 57 (regression stub). `src/skill_agent/models.py` lines 109-114 (publishable computation).

---

## 5. Generator relies on prompt constraints to stay stable

**What the limitation is:** The generator's correctness depends heavily on the LLM following constraints stated in the system prompt (`src/skill_agent/prompts/generator_system.md`). The prompt instructs the model to:
- Write only Python (stdlib only)
- Avoid external dependencies
- Write files in a specific order (`SKILL.md` first, then `scripts/run.py`)
- Use only deterministic test case outputs
- Keep scope narrow

None of these constraints are enforced at the structural level. If the model ignores them, the result reaches static validation and may produce confusing errors there instead of a clear generation error.

**Why this exists now:**  
The system currently stabilizes generation quality through strong prompting because that was the fastest way to learn what constraints actually matter. The team intentionally used prompt narrowing as a control mechanism before deciding which rules deserve hard enforcement in code.

**Practical impact:**  
Generation quality is highly sensitive to prompt wording and scope discipline. The system performs best on narrow, deterministic skills. Broad, ambiguous, or multi-mode requests are more likely to fail generation or produce unstable artifacts.

**Key fragility points:**
- `AgentLoop` has a hard 30-iteration cap (`_MAX_ITERATIONS = 30` in `loop.py`). Complex generations that require many tool calls can hit this limit.
- The `SkillBuilder` silently replaces metadata if `set_metadata` is called twice — the second call wins with no warning.
- The static import scanner (`_THIRD_PARTY_IMPORT_RE` in `validator.py`) uses a fixed allowlist. Packages not on the list (e.g., `anthropic`, `transformers`, `numpy`) escape detection unless they appear in a `requirements.txt`.

---

## 6. Publish gateway is not atomic and not idempotent

**What the limitation is:**  
`materialize_skill()` in `publisher.py` writes files to `skills/<name>/` one at a time. A crash or exception mid-write leaves a partial skill on disk that will be discovered by `discover_skills()` at runtime, potentially causing errors when the partial `SKILL.md` or missing `run.py` is loaded.

Additionally:
- Publishing to `skills/<name>/` silently overwrites any existing skill with the same name.
- The `status:` field is rewritten using a simple string replacement that would fail on malformed frontmatter or if `status:` appears in the SKILL.md body.
- `regression_pass` is not part of `compute_publishable()`, so the publish gate can approve a skill even if regression testing (if it existed) had failed.

**Why this exists now:**  
The publish path is currently optimized for simplicity: write the skill package to disk once it is considered publishable. Atomicity, rollback, and versioned publishing were deferred because the project is still focused on proving skill generation and validation rather than hardened deployment semantics.

**Practical impact:**  
This is acceptable for a local prototype, but it is fragile as a publishing model. A failed publish can leave broken artifacts on disk, and repeated publishes can overwrite prior skills without any safety net.

**Where it comes from:**  
`src/skill_agent/publisher.py` — the `materialize_skill()` call is sequential with no rollback.

---

## 7. Policy confirmation gate has no approval workflow

**What the limitation is:**  
`PolicyEngine` denies any request whose `requested_action` is in the confirmation list (`delete`, `overwrite`, `network` by default). The engine returns `ExecutionStatus.denied` and stops. There is no mechanism for the user to approve the action and resume execution. This means a skill that supports `delete` cannot be used through the current policy layer without removing `delete` from the confirmation list entirely.

**Why this exists now:**  
The policy layer was introduced first as a hard safety gate to prevent unsafe execution. Approval/resume flow was deferred because it adds more product/UI state and requires a clearer interaction model than the current prototype has.

**Practical impact:**  
The system can correctly block destructive actions, but it cannot yet support the full user journey of “this action requires confirmation — approve and continue.” This makes the policy layer safe but incomplete.

**Where it comes from:**  
`src/skill_agent/runtime/policy.py`. The docs in `docs/policy.md` section 8 acknowledge this is reserved for later.

---

## 8. Gradio app is single-tenant

**What the limitation is:**  
`app_gradio.py` creates one `SkillChatAgent` instance at module load time as a module-level variable `_AGENT`. All Gradio users share the same agent instance and the same conversation history. Concurrent requests will corrupt the conversation state. The agent also holds a reference to the sandbox runner, which is stateful.

**Why this exists now:**  
The Gradio app was introduced as a thin debug/demo UI to avoid terminal/TTY issues and make agent traces easier to inspect. It was not designed as a multi-user service.

**Practical impact:**  
The current UI is fine for local single-user demos, but it should not be treated as safe for shared or concurrent usage. State bleed between users is possible.

**Where it comes from:**  
`app_gradio.py` — `_AGENT = SkillChatAgent(...)` at module level.

---

## 9. Path traversal in obsidian-crud with no sandbox

**What the limitation is:**  
`skills/obsidian-crud/scripts/run.py` accepts file paths from stdin JSON (e.g., `{"operation": "read", "path": "../../etc/passwd"}`). There is no path sanitization or containment to a vault directory. In a non-sandboxed execution context (the default), this is a real path traversal vulnerability.

In the Docker runner this is mitigated by the container filesystem boundary, but the local runner provides no protection.

**Why this exists now:**  
The CRUD skill was generated to prove file-oriented skill behavior, not built as a hardened vault abstraction. The current runtime assumes a trusted demo environment more than an adversarial one.

**Practical impact:**  
Under the default local runner, this is a real security problem if untrusted input reaches the skill. It is one of the clearest examples of why the local runner should not be mistaken for a secure sandbox.

**Where it comes from:**  
`skills/obsidian-crud/scripts/run.py` — paths are passed directly to `pathlib.Path`.

---

## 10. Skill metadata contract is evolving faster than the validator

**What the limitation is:**  
The validator (`validator.py`) checks a specific set of required fields and warns on others. Some existing skills (`broken-skill`) have `supported_actions` values (`crash`, `fail`) outside the documented taxonomy, which produce warnings but not errors. The validator does not enforce that action names come from any controlled vocabulary.

Older skills may be missing newer required fields. The runtime's `check_capability()` handles missing `supported_actions` by returning `unknown_capability` (which allows execution to continue), so legacy skills degrade gracefully but unpredictably.

**Why this exists now:**  
The metadata contract is still actively being shaped by what the team is learning from generator failures, runtime selection, and policy design. The validator has not yet caught up into a strict, frozen contract because the contract itself is still moving.

**Practical impact:**  
This makes the system more flexible for experimentation, but less predictable. Skill packages can load and run with degraded capability semantics, and the meaning of “valid skill” is still tighter in some parts of the system than others.

**Where it comes from:**  
`src/skill_agent/validator.py` — action name validation is not present; `src/skill_agent/runtime/capability.py` — the fallback to `unknown_capability` for missing metadata.

---

## Summary Table

| # | Limitation | Category |
|---|---|---|
| 1 | Default sandbox has zero isolation | Sandbox | 
| 2 | Executor ignores `runtime:` field | Runtime | 
| 3 | Selection is pure lexical token overlap | Architectural | 
| 4 | Task verification cannot prove correctness | Sandbox / Test coverage |
| 5 | Generator relies on prompt constraints | Prompt / Validator |
| 6 | Publish is not atomic or idempotent | Architectural | 
| 7 | Confirmation gate has no approval workflow | Architectural | 
| 8 | Gradio app is single-tenant | Architectural | 
| 9 | Path traversal in obsidian-crud | Sandbox / Security | 
| 10 | Skill metadata contract evolving ahead of validator | Validator | 
