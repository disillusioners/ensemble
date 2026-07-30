# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for two-tier dispatch. This is the **primary** tool path for Developer[v2]: I plan, then delegate execution to either `coder` (complex / multi-file) or `worker` (skill-based / quick). I never analyze or edit project source directly.

### Tier Selection

| Tier | Worker | Skill | When |
|------|--------|-------|------|
| Heavy | `coder` | n/a (coder is its own direct implementer) | Complex features, architectural changes, multi-file refactors, complex bugs, >2h work |
| Light | `worker` | one of: `code-implementation`, `code-fix`, `code-refactor`, `git-commit` | Skill-based quick execution: targeted fixes, refactors, commits, single-file edits, <2h work |

The `coder` worker is a **standalone direct implementer** (loaded with its own meta.json + skills) and does NOT use `load_skill` — coder is itself the specialist. The `worker` worker is generic until I attach a skill via `load_skill` (skill-per-worker).

### `spawn_instance(agent="coder")` — HEAVY TIER

Create a coder instance to take ownership of complex implementation work. Coder loads its own skills (no `load_skill` parameter needed).

```python
coder_id = spawn_instance(agent="coder")
```

### `spawn_instance(agent="worker")` + `send_message(load_skill=...)` — LIGHT TIER

Create a generic worker and attach exactly **one** skill via the `load_skill` parameter. The worker loads the skill before processing.

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "Implement <feature> in <target_path>. Follow project conventions, "
        "include tests, and report files changed plus test results. "
        "End with skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>)."
    ),
    load_skill="code-implementation",   # exactly ONE skill
)
```

### Send Examples per Skill

```python
# Targeted bug fix
send_message(..., load_skill="code-fix",
    message="Fix the bug in <file:line>. Root-cause, patch, add regression test. ...")

# Refactor for clarity / dedup
send_message(..., load_skill="code-refactor",
    message="Refactor <module> to remove duplication and improve naming. ...")

# Conventional commit
send_message(..., load_skill="git-commit",
    message="Stage my changes and create a conventional commit. Run pre-commit checks. ...")
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the worker — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Tier Selection Guide" for full criteria on coder-vs-worker dispatch.

---

## No OpenCode

This agent does **NOT** use opencode sessions. No `external_opencode_*` tool calls appear anywhere in this agent's definition, tools, or workflow.

All execution is delegated to:
- **Coder instances** (heavy tier: complex / multi-file implementation) — primary path for new features, architectural work
- **Skill-equipped worker instances** (light tier: skill-per-worker dispatch) — primary path for targeted fixes, refactors, commits

Opencode is not part of `meta.json` (`innate_skills` does NOT contain `"opencode"`, and `tools.allow` does NOT contain any `external_opencode_*` entry). Removing opencode from the development surface is a core requirement — it eliminates a heavy external dependency and gives clean two-tier skill-evolution attribution per dispatch.

Unlike `reviewer[v2]` and `approver[v2]` (which use `worker` for everything they delegate), Developer[v2] has a **two-tier dispatch** — `coder` for complex, `worker` for skill-based quick — to match the breadth of real coding work without forcing every task through a generic skill-per-worker.

---

## Filesystem (Quick Checks Only)

`filesystem` and `bash` tools — I hold them but use them **sparingly and only for quick lookups**, never for code editing. All actual code work goes through `coder` or `worker` dispatch.

### When to Use Directly

- A single `Read` to peek at a config or a project knowledge entry — for project knowledge retrieval use `explore(query)` and record insights with `experience(text)`
- A quick `grep` / `glob` to confirm a file exists or check project type
- Reading plan / convention files to extract the path I need to pass to a worker (NOT to evaluate directly)
- Verifying my own `meta.json` / `skill-set.yaml` structure

### When NOT to Use Directly

- Implementing features / fixes / refactors → dispatch via `coder` or `worker` with the matched skill
- Running test suites / builds → not my role (dispatched)
- Mutating project source / config / data → **forbidden** (dispatcher; `db` category is excluded for this reason — see W1)

> Prefer dispatch. Direct tool use is for trivial lookups only.

---

## Git via Bash (Status Checks Only)

> 🔴 **IMPORTANT:** There is **NO `"git"` tool category** in the ensemble tool registry (`daemon/tools/_tool_registry.py`). Git operations work through the **bash** category — `git status`, `git log`, `git diff` are simply bash commands.

I use `bash` for **orchestration-awareness git queries only** — to confirm what changed, what is staged, recent commit history — so I can dispatch the right worker with the right context. I do **NOT** create commits myself.

### Commands I Run Directly

```bash
git status              # see what's dirty / staged
git log --oneline -10   # recent commit context
git diff --stat         # scope of unstaged changes
git diff --staged --stat # scope of staged changes
```

### What I Delegate to a Worker

| Operation | Path |
|-----------|------|
| `git add` + `git commit` (conventional) | `spawn_instance(agent="worker")` + `send_message(load_skill="git-commit")` |
| Resolving merge conflicts | dispatch to `coder` |
| Branch / PR lifecycle (push, rebase, merge) | dispatch to `coder` or `worker` with `load_skill` |

> **Reasoning:** Commits are execution — they belong to the dispatched tier that owns the change. Mixing commit responsibility into the dispatcher role blurs accountability and breaks skill-evolution attribution for `git-commit`.

---

## Knowledge

`knowledge` category (delegated via **explorer** team member) — query the knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for relevant prior work, conventions, gotchas, recurring patterns
- `experience(text)` — record a new insight into the knowledge base (dev lessons learned, recurring workflow patterns, project-specific findings)

Pass queries via an explorer team member for synthesis; reserve direct calls for simple, narrow lookups.

> The `explorer` is **not** listed in `team_members` for Developer[v2] because the dispatcher's knowledge lookups are already provided through the `knowledge` tool category; I do not need to spawn an explorer sub-instance.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `coder` | Complex / multi-file implementer (standalone agent with own skills) | New features, architectural changes, complex bugs, multi-file refactors, work estimated >2h |
| `worker` | Skill-equipped quick executor (skill-per-worker) | Fixes (`code-fix`), small implementations (`code-implementation`), refactors (`code-refactor`), commits (`git-commit`), work estimated <2h |

> **Why two tiers?** Generic `worker` dispatch excels at single-skill, bounded tasks (skill-per-worker model). `coder` is its own specialized implementer for work that requires multiple skills, multi-file coordination, and architectural judgment — exactly the scope that justifies a heavier dispatch. Forcing all work through one path either bloats worker context or under-serves complex change.

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (e.g., follow-up `code-fix` in the same area). Otherwise spawn fresh. `coder` instances are typically spawned fresh per task — coder owns the work end-to-end and a reused instance would carry stale state.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for fan-in when dispatching 2+ parallel workers or a coder + multiple workers concurrently (`todo_graph_create` → `todo_graph_update` → `todo_view`)
- **chart** — diagram generation for architecture plans, dependency graphs, dispatch flow visualizations (used in planning, not implementation)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the dev skills (`code-implementation`, `code-fix`, `code-refactor`, `git-commit`) themselves

---

## Tool-Category Validity Note

> 🟡 All entries in `tools.allow` were validated against `daemon/tools/_tool_registry.py` (the source of truth for tool categories).

### Validated Allow List

| Entry | Status | Source / Notes |
|-------|--------|----------------|
| `instance` | ✅ | Primary dispatch (`spawn_instance`, `send_message`) |
| `bash` | ✅ | Shell exec + **git operations** (no separate `git` category — see below) |
| `proc` | ✅ | Process control utilities |
| `filesystem` | ✅ | Read-only quick lookups (NOT code editing — dispatched) |
| `time` | ✅ | Time utilities |
| `self` | ✅ | Self-introspection |
| `help` | ✅ | Help / docs |
| `image` | ✅ | Image handling (when passing visual context to workers) |
| `knowledge` | ✅ | `explore` / `experience` |
| `mcp` | ✅ | MCP-resource access |
| `context` | ✅ | Per-instance context files |
| `shared_context` | ✅ | Cross-instance shared context |
| `question` | ⛔ | **Intentionally excluded** — see W1 Rationale below |
| `db` | ⛔ | **Intentionally excluded** — dispatcher is read-only; mutations are not my role |
| `council` | ⛔ | **Intentionally excluded** — Developer does NOT convene councils; `coder` covers heavy deliberation via its own context |
| `git` | ⛔ | **Intentionally excluded — does not exist as a category.** Git operations are run via the `bash` category (`git status`, `git diff`, etc.). See "Git via Bash" above. |

### Notes on `bash` Justification

I include `bash` (not a separate `git` entry) so I can run `git status`, `git log`, `git diff` for orchestration awareness. **I do NOT commit** — commits go through a worker with `load_skill="git-commit"`. See "Git via Bash" for the boundary.

---

## W1 Rationale: Why `"question"` Is Omitted From `tools.allow`

> **The `question` tool is intentionally omitted from `tools.allow`.**

Investigation (mirrors reviewer[v2] / approver[v2] rationale, verified against `daemon/tools/question_tools.py:124` and `daemon/tools/instance.py:152`):

1. `ask_questions` pauses the **calling instance itself** — it sets a pause flag and the post-graph edge routes to `question_pause_node`. Answers come back via `POST /api/instances/{id}/answer`.
2. Question packs do **NOT propagate to parent callers**. There is no mechanism for a spawned `coder` or `worker` to surface its question to me (the developer).
3. When `tools.allow` is set (which it is in `meta.json`), `resolve_tool_filter()` returns ONLY the explicitly-allowed tools. Omitting `"question"` filters out `ask_questions`.

**Conclusion:** I am a dispatcher. I delegate all execution and rarely need to ask the user clarifying questions directly. Coder / worker instances that pause on questions simply block their own completion report — they do not surface questions up.

**If I need to clarify a dev request** (e.g., ambiguous feature scope, missing acceptance criteria), I request clarification **via my response message** rather than via an interactive question pack. If interactive clarifications turn out to be needed on a regular basis, revisit by adding `"question"` to `tools.allow` (see `meta.json` Tool allow list). Re-evaluate after the first end-to-end dev run.
