# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for skill-per-worker
dispatch. This is the **primary** tool for Tidier v2.

### `spawn_instance(agent="worker")`

Create a worker instance to receive a craftsmanship skill. The worker is
generic until I attach a skill via `load_skill`.

```python
worker_id = spawn_instance(agent="worker")
```

### `send_message(instance_id, message, load_skill="...")`

Send the review task and attach a single execution skill. The worker loads
the skill before processing the diff.

```python
send_message(
    instance_id=worker_id,
    message=(
        "Review the diff in <files> for craftsmanship. "
        "Cover <category list>. "
        "Report findings in severity-grouped format: "
        "[High] {Category}: {Title} — file:line — Problem / Impact / Fix. "
        "Cite file:line for every finding. "
        "Call skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY "
        "first, then deliver your full severity-grouped report as your FINAL "
        "message (that report is what I receive verbatim) and end your turn."
    ),
    load_skill="tidier-readable-code",   # exactly ONE skill per worker
)
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash`
> waiting for the worker — the report arrives asynchronously as a new message.
> Holding the turn open blocks report delivery (deadlocks the run). See
> `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value
matches each diff profile.

---

## Filesystem (Read-Only — Tracking & Quick Checks Only)

`filesystem` and `bash` tools — I hold them but use them **sparingly and only
for**:
- Reading/writing `.agents/tidier/` memory files and tracking notes
- Reading `.agents/tidier/rules/` for project-specific conventions
- Reading the diff (or file paths) **to pass to workers** — NOT to evaluate
  directly
- Quick lookups (config files, `.agents/tidier/` memory)

### When to Use Directly

- Writing/updating `.agents/tidier/notes.md` (aggregated review record)
- Writing/updating `.agents/tidier/memory/` files (new craftsmanship patterns)
- Reading `.agents/tidier/rules/` for project conventions
- A single `Read` to peek at `.agents/tidier/` memory files
- A quick `glob` to confirm files exist before passing paths to workers

### When NOT to Use Directly

- Inspecting source code for findings → dispatch a worker with the appropriate
  execution skill (`tidier-readable-code`, `tidier-static-hygiene`,
  `tidier-robustness`)
- Running test suites / builds / linters → not Tidier's role
- Mutating project source / config / data → **forbidden** (read-only
  dispatcher; my write scope is `.agents/tidier/` only)

> Prefer worker dispatch. Direct tool use is for tracking files and trivial
> lookups only. Reading the diff to give my own verdict is a rule violation
> (rule #1).

---

## Knowledge

`knowledge` category (delegated via **explorer** team member) — query the
knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for relevant
  prior work, conventions, gotchas, project-specific patterns
- `experience(text)` — record a new insight into the knowledge base
  (craftsmanship patterns, recurring finding themes, project-specific rules)

Pass queries via an explorer team member for synthesis; reserve direct calls
for simple, narrow lookups.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped reviewer (skill-per-worker: `tidier-readable-code` / `tidier-static-hygiene` / `tidier-robustness`) | Default — dispatch for every review |
| `explorer` | Knowledge-base retrieval | Project conventions, prior review history, RAG lookup |

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context
is still relevant (follow-up review on the same diff). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` →
  `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers
  for medium/large diffs
- **chart** — diagram generation for visualizing review scope, finding
  distributions, severity breakdowns (used in Tidier Plan and aggregated
  report, not for evaluation)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me
  reflect on / suggest improvements to the craftsmanship skills themselves

