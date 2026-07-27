# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for skill-per-worker dispatch.

### `spawn_instance(agent="worker")`

Create a worker instance to receive a review skill. The worker is generic until I attach a skill via `load_skill`.

```python
worker_id = spawn_instance(agent="worker")
```

### `send_message(instance_id, message, load_skill="...")`

Send the review task and attach a single review skill. The worker loads the skill before processing.

```python
send_message(
    instance_id=worker_id,
    message=(
        "Review <target> for <focus>. "
        "Report findings as: area, file:line, issue, severity (🔴/🟡/🟢), fix. "
        "End with skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>)."
    ),
    load_skill="code-review",   # exactly ONE skill
)
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the worker — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value matches each review type.

---

## Council Management

`council` category — `convene_council` for Deep-Review.

### `convene_council` — DEEP REVIEW

Real signature (verified from `daemon/tools/instance.py:901-956`):

```python
convene_council(
    councilor_agent_id: str,           # REQUIRED — default "wanderer"
    request: str,                      # REQUIRED — deep-review prompt (prepend ⛔ READ-ONLY directive if you rely on governor to enforce it; governor prepends automatically)
    models: list[str] | None = None,             # optional — None lets governor pick diverse models
    max_councilors: int | None = None,           # optional — caps councilors WITHIN the council (≤4)
    instance_name: str | None = None,            # optional — labels the spawned governor instance
)
```

> `convene_council` is **non-blocking**. It returns immediately with `{"status": "convened", ...}`. The governor runs the council asynchronously and the synthesized result is delivered to me as a **new message** later.

### Usage

```python
convene_council(
    councilor_agent_id="wanderer",
    request=(
        "Deep review of <target>. "
        "Focus: <concerns>. Provide thorough analysis of correctness, safety, architecture."
    ),
    models=None,                       # governor picks
    max_councilors=4,
    instance_name="review-council",
)
# END TURN — result arrives as async report
```

> **Note on `models`:** Passing `models=None` tells the governor to use all available models, but the governor's own validation may STOP and ask for an explicit list. For deterministic council behavior, pass an explicit models list (e.g. `models=["model-a", "model-b"]`).

### Critical Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `councilor_agent_id` | YES | Default `"wanderer"`. Never set to `"reviewer"` (recursion). `wanderer` is the purpose-built read-only investigator. |
| `request`             | YES | The deep-review prompt. Governor prepends the ⛔ READ-ONLY directive automatically before dispatching to councilors. |
| `models`              | NO  | `None` lets the governor pick diverse councilors. Pass a list to constrain. |
| `max_councilors`      | NO  | Caps councilors spawned **within this single council** (≤4, WorkerPool alignment). It is NOT the number of councils. |
| `instance_name`       | NO  | A label for the spawned governor instance. |

### What `convene_council` Is Not

- **Not `spawn_councilor` directly.** `spawn_councilor` is identity-guarded to the `governor` agent. As a reviewer, I cannot call it.
- **Not multiple councils per review.** Deep-Review = exactly **one** `convene_council` call. The governor handles councilor spawning within.
- **Not blocking.** Do not poll / sleep / bash waiting for the result. End turn; report arrives async.

---

## Filesystem (quick checks only)

`filesystem` and `bash` tools — I hold them but use them **sparingly and only for quick lookups**, never for full analysis. Prefer worker dispatch for any analysis that touches project source / config / code.

### When to Use Directly

- A single `Read` to peek at a config or `.agents/reviewer/` memory file
- A quick `grep` / `glob` to confirm a file exists or a function appears
- Verifying my own `meta.json` / `skill-set.yaml` structure

### When NOT to Use Directly

- Reviewing actual code → dispatch a worker with `load_skill="code-review"` (etc.)
- Running test suites / builds → not my role
- Mutating project source / config / data → **forbidden** (read-only dispatcher; `db` category is excluded for this reason — see W2)

> Prefer worker dispatch. Direct tool use is for trivial lookups only.

---

## Knowledge

`knowledge` category (delegated via **explorer** team member) — query the knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for relevant prior work, conventions, gotchas
- `experience(text)` — record a new insight into the knowledge base (review lessons learned, recurring issue patterns, project-specific findings)

Pass queries via an explorer team member for synthesis; reserve direct calls for simple, narrow lookups.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped reviewer (skill-per-worker) | Default — standard reviews, multi-area parallel dispatch |
| `governor` | Council convenor (via `convene_council`) | Deep-Review of high-risk targets |
| `explorer` | Knowledge-base retrieval | Project conventions, prior findings, RAG lookup |

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (follow-up review in the same area). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers
- **chart** — diagram generation for architecture reviews (sequence diagrams, component boundaries, dependency graphs)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the review skills themselves

---

## W1 Rationale: Why `"question"` Is Omitted From `tools.allow`

> **The `question` tool is intentionally omitted from `tools.allow`.**

Investigation (verified against `daemon/tools/question_tools.py:124` and `daemon/tools/instance.py:152`):

1. `ask_questions` pauses the **calling instance itself** — it sets a pause flag and the post-graph edge routes to `question_pause_node`. Answers come back via `POST /api/instances/{id}/answer`.
2. Question packs do **NOT propagate to parent callers**. There is no mechanism for a spawned governor or worker to surface its question to me (the reviewer).
3. When `tools.allow` is set (which it is in `meta.json`), `resolve_tool_filter()` returns ONLY the explicitly-allowed tools. Omitting `"question"` filters out `ask_questions`.

**Conclusion:** I am a dispatcher. I delegate all analysis and rarely need to ask the user clarifying questions directly. Workers / council members that pause on questions simply block their own completion report — they do not surface questions up.

**If I need to clarify a review request** (e.g., ambiguous scope), I request clarification **via my response message** rather than via an interactive question pack. If interactive clarifications turn out to be needed on a regular basis, revisit by adding `"question"` to `tools.allow` (see `meta.json` Tool allow list). Re-evaluate after the first end-to-end review run.

---

## NO OPENCODE

This agent does **NOT** use opencode sessions. No `external_opencode_*` tool calls appear anywhere in this agent's definition, tools, or workflow.

All analysis is delegated to:
- **Skill-equipped worker instances** (standard reviews) — primary path, `load_skill`-attributed
- **Governor council via `convene_council`** (deep reviews)

Opencode is not part of `meta.json` (`innate_skills` does not contain `"opencode"`, and `tools.allow` does not contain any `external_opencode_*` entry). Removing opencode from the review surface is a core requirement — it eliminates a heavy external dependency and gives clean skill-evolution attribution per worker dispatch.
