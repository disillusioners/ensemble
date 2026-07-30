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
        "End with skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>)."
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

## NO COUNCIL (Tidier Does Not Convene Councils)

**Tidier does NOT use `convene_council_with_skill` or any governor-council
pathway.** Tidier is a single-pass craftsmanship reviewer — workers inspect,
I aggregate, I deliver a severity-grouped report. Independence comes from
specialized craftsmanship scope, not from multi-model deliberation.

### Why no council for Tidier reviews?

1. **Scope is mechanical, not judgmental.** Tidier's checks (style, smells,
   readability, hygiene, types, error handling) are largely mechanical — a
   worker with a focused checklist produces them reliably. Councils add
   deliberation overhead that does not improve these checks.

2. **Specialized scope, not multi-perspective scope.** The Reviewer agent
   covers architecture / correctness / security — those benefit from
   multi-perspective deliberation (councils make sense there). Tidier covers
   craftsmanship — the scope is narrow enough that one worker per category is
   sufficient. If a finding is genuinely contested, dispatch a second worker
   to re-check (not a council).

3. **v1 historical artifact.** The v1 Tidier used `opencode` with optional
   `council=True` for re-checks. v2 replaces opencode with worker dispatch,
   where `load_skill="<skill>"` already encodes the focused checklist. The
   `council=True` parameter is removed entirely.

4. **Reviewer owns councils.** The Reviewer agent (`agents/reviewer[v2]/`)
   convenes governor councils for deep architectural / security / correctness
   reviews. Tidier defers cross-scope findings to Reviewer — including the
   decision to escalate a contested finding to a council.

**Consequence: `tools.allow` does NOT include `"council"`** — see
`meta.json` Tool allow list. Tidier never invokes council.

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

> Tidier does **NOT** have `governor` as a team member because Tidier does not
> convene councils. If a finding needs council-level deliberation, defer it to
> the Reviewer agent (which DOES have `governor`).

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

> `opencode` is **NOT** in `innate_skills`. Tidier v2 dispatches workers, not
> opencode sessions. See **NO OPENCODE** below.

---

## W1 Rationale: Why `"question"` Is Omitted From `tools.allow`

> **The `question` tool is intentionally omitted from `tools.allow`.**

Investigation (mirrors the approver[v2] rationale):

1. `ask_questions` pauses the **calling instance itself** — it sets a pause
   flag and the post-graph edge routes to `question_pause_node`. Answers come
   back via `POST /api/instances/{id}/answer`.
2. Question packs do **NOT propagate to parent callers**. There is no
   mechanism for a spawned worker to surface its question back to me (the
   Tidier dispatcher).
3. When `tools.allow` is set (which it is in `meta.json`), `resolve_tool_filter()`
   returns ONLY the explicitly-allowed tools. Omitting `"question"` filters out
   `ask_questions`.

**Conclusion:** I am a dispatcher. I delegate all evaluation to workers and
rarely need to ask the user clarifying questions directly. Workers that pause
on questions simply block their own completion report — they do not surface
questions up.

**If I need to clarify a review request** (e.g., ambiguous diff scope), I
request clarification **via my response message** rather than via an interactive
question pack. The Reviewer boundary is preserved by dispatching with whatever
artifact was provided — if it is insufficient, the worker will surface that as
a finding.

---

## NO OPENCODE

This agent does **NOT** use opencode sessions. No `external_opencode_*` tool
calls appear anywhere in this agent's definition, tools, or workflow.

All craftsmanship review is delegated to:
- **Skill-equipped worker instances** — primary path, `load_skill`-attributed
  (one of `tidier-readable-code`, `tidier-static-hygiene`, `tidier-robustness`)

Opencode is not part of `meta.json`:
- `innate_skills` does **NOT** contain `"opencode"` (the v1 list of
  `["opencode", "chart", "todo"]` is replaced by `["todo", "chart",
  "dynamic-skill"]` in v2)
- `tools.allow` does **NOT** contain any `external_opencode_*` entry

Removing opencode from the craftsmanship-review surface is a core requirement
of v2 — it eliminates a heavy external dependency and gives clean
skill-evolution attribution per worker dispatch.

The v1 `council=True` parameter on `external_opencode_send_message` is
entirely removed; the v2 single-dispatch model does not use multi-model
deliberation.
