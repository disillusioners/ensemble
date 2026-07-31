# Tool Usage Notes

This file is **tool-by-tool reference** for developer[v2]. The dispatch mechanics (tier selection, dispatch snippets, fan-in) live in `dev-strategy.md` and `workflow.md` — I do not duplicate them here.

---

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for two-tier dispatch. This is my **primary** tool path: I plan, then delegate execution to either `coder` (complex / multi-file) or `worker` (skill-based / quick). I never analyze or edit project source directly.

The dispatch snippets (Coder, Worker+skill, Worker no-skill) are in **`dev-strategy.md` → "Worker Dispatch Pattern"**; the process around them is in `workflow.md`. I always END TURN after `send_message`.

---

## Read-Only Allow-List (filesystem + bash)

> 🔴 There is **NO `"git"` tool category** in the ensemble registry (`daemon/tools/_tool_registry.py`). Git works through the **bash** category — `git status`, `git log`, `git diff` are bash commands.

I hold `filesystem` + `bash` but my direct use is **read-only and bounded** (rule §13). Here is the explicit allow/deny:

| Tool | Allowed directly (read-only) | Forbidden → dispatch instead |
|------|------------------------------|------------------------------|
| `bash` | `git status`, `git log --oneline -N`, `git diff [--staged] [--stat]`, `wc` for scope | grep/ast-grep on source files, builds, tests, linters, `git add`, `git commit` |
| `filesystem` | `Read` on `.agents/shared/**`, `*.json`, `*.yaml`, planning/convention files; single `grep`/`glob` to confirm a file exists | `edit_file`, `write_file`, `apply_patch`, any source mutation |

**Commands I run directly:**
```bash
git status                # what's dirty / staged
git log --oneline -10     # recent commit context
git diff --stat           # scope of unstaged changes
git diff --staged --stat  # scope of staged changes
```

**What I delegate:**

| Operation | Path |
|-----------|------|
| `git add` + `git commit` (conventional) | worker + `load_skill="git-commit"` |
| Resolving merge conflicts | `coder` |
| Branch / PR lifecycle (push, rebase, merge) | `coder` or `worker` + `load_skill` |
| Any source edit / build / test / lint | `coder` or `worker` + skill |

> **Reasoning:** Commits are execution — they belong to the dispatched tier that owns the change. Mixing commit responsibility into the dispatcher role blurs accountability and breaks `git-commit` skill-evolution attribution.

---

## Knowledge

`knowledge` category — `explore(query)` / `experience(text)`, accessed **directly** (not via an explorer instance).

- `explore(query)` — search the project knowledge base (RAG) for relevant prior work, conventions, gotchas, recurring patterns
- `experience(text)` — record a new insight (dev lessons learned, recurring workflow patterns, project-specific findings)

I reserve direct `explore` calls for simple, narrow lookups. For synthesis-grade queries I still call `explore` directly — explorer is **not** a developer[v2] team member (rule / `soul.md`). My knowledge lookups are provided through the `knowledge` tool category; I do not spawn an explorer sub-instance.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `coder` | Complex / multi-file implementer (standalone agent with own skills) | New features, architectural changes, complex bugs, multi-file refactors, >2h |
| `worker` | Skill-equipped quick executor (skill-per-worker) | `code-fix`, `code-implementation`, `code-refactor`, `git-commit`, `code-review`; <2h |

> **Why two tiers?** Generic `worker` dispatch excels at single-skill, bounded tasks (skill-per-worker). `coder` is its own specialized implementer for multi-file coordination and architectural judgment — scope that justifies a heavier dispatch. Forcing all work through one path either bloats worker context or under-serves complex change.

**Worker reuse:** a worker can be re-dispatched with a new `load_skill` if context is still relevant (e.g., follow-up `code-fix` in the same area) — that produces a separate skill-feedback attribution record per load. Otherwise spawn fresh. `coder` instances are spawned fresh per task (coder owns the work end-to-end; a reused instance carries stale state).

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for fan-in when dispatching 2+ parallel instances (`todo_graph_create` → `todo_graph_update` → `todo_view`)
- **chart** — diagram generation. **Trigger:** emit a mermaid/visual chart when a plan fans out to **≥2 parallel instances** or crosses **≥2 modules** — so the caller can see the dispatch shape. Not for single-instance tasks.
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to execution skills (`code-implementation`, `code-fix`, `code-refactor`, `git-commit`) via the project skill bank

---

## Tool-Category Validity

> 🟡 All entries in `tools.allow` were validated against `daemon/tools/_tool_registry.py` (source of truth for tool categories). Adding a non-existent category is a fail-fast — preferable to silently losing a tool.

| Entry | Status | Notes |
|-------|--------|-------|
| `instance` | ✅ | Primary dispatch |
| `bash` | ✅ | Shell exec + **git operations** (no separate `git` category) — read-only allow-list only |
| `proc` | ✅ | Process control utilities |
| `filesystem` | ✅ | Read-only quick lookups (NOT code editing — dispatched) |
| `time` | ✅ | Time utilities |
| `self` | ✅ | Self-introspection |
| `help` | ✅ | Help / docs |
| `image` | ✅ | Image handling (passing visual context to workers) |
| `knowledge` | ✅ | `explore` / `experience` (direct) |
| `mcp` | ✅ | MCP-resource access |
| `context` | ✅ | Per-instance context files |
| `shared_context` | ✅ | Cross-instance shared context |

`proc`, `image`, `mcp`, `context`, `shared_context`, `time`, `self`, `help` are allow-listed for completeness but undocumented here — I use them only when an explicit dispatch need calls for them; I do not default to reaching for them.
