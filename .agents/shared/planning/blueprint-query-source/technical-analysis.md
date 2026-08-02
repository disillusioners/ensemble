# Technical Analysis: Blueprint Query Source for Parent→Child Spawns

Date: 2026-08-02
Author: planner[v2] via technical-analysis worker
Analysis depth: focused (one design decision)
Status: Draft / Ready for Review

---

## Question

When a parent agent spawns a child agent and sends its first task message, what
text should be used as the **query** for Project Blueprint matching?

Blueprints are concise markdown architectural docs injected into agent context as
persistent messages at instance creation / first message receipt. Matching is
gated by the `project_injected` flag and runs **once per instance** (results
persist in the persistent-context block for the lifetime of the instance).

**Constraints (from caller):**
- Token budget: cap at 5 blueprints, each concise.
- One-shot matching — no re-matching on later turns.
- The four candidate approaches (A/B/C/D) are pre-specified; this analysis
  evaluates them against explicit criteria and recommends one.

---

## Context Summary

We are designing the Project Blueprint subsystem. The matching pipeline uses
BM25 + vector similarity against a corpus of project blueprints. The query is
the primary signal that drives which blueprints surface.

For **user→agent** messages, the query is the message text — this is
straightforward. For **parent→child** spawns, the parent has already curated
intent into two channels:

1. The **task message** (free-text, always present at first-message receipt).
2. The **structured `context` parameter** (Tier 2A) — a parent-supplied JSON
   blob that the parent uses to hand the child a structured definition of the
   task. It is optional, rendered as a `[SYSTEM CONTEXT: Task Context]`
   markdown block, and stored on the MessageQueue row.

The interesting design question is whether the blueprint query should consume
just the task message, both signals, or something else (e.g., inherited
parent context). The cost of getting this wrong is silent: the child will work
on the wrong blueprint set for its entire lifetime, with no recovery short of
restarting the instance.

This is a **one-shot, persistent** decision — the matched blueprint set
follows the child for its whole lifetime. There is no second chance to
re-match if the first query is wrong.

---

## Architecture

### Current Patterns (related to this decision)

- **One-shot persistent context** — `project_injected` flag is checked/set at
  the top of `_process_message_with_tracking()` BEFORE graph execution
  (`daemon/services/instance_messaging.py:1920-1942`). This is the natural
  gate where blueprint matching would run, and the natural storage location
  for matched blueprints is the `persistent_context_msgs` block.
- **Context assembly** — `assemble_context_messages()` returns
  `(persistent_msgs, ephemeral_msgs)` (`daemon/services/context_messages.py:1065-1165`).
  Persistent msgs are checkpointed once on first turn and read from
  `state['messages']` on subsequent turns. Matched blueprints would slot in as
  additional `HumanMessage` entries with
  `additional_kwargs={"injected_message": True, "context_kind": "blueprints"}`.
- **Task context rendering** — the `context` param is rendered into a
  `[SYSTEM CONTEXT: Task Context]` markdown block by `_format_task_context()`
  (`daemon/tools/instance.py:47`), stored as `metadata={"task_context": "..."}`
  on the **MessageQueue row** (not on the instance record), and extracted in
  `task_processor.py:342`. It is injected as a HumanMessage at position 0 of
  `persistent_context_msgs`.

### Module Boundaries

```
parent‑agent
   │
   │ send_message(task_message, context=<optional JSON>)
   ▼
[MessageQueue row]  ← metadata.task_context lives here
   │
   │ child instance created
   ▼
[_process_message_with_tracking] ← project_injected gate (line 1920‑1942)
   │
   │ ★ BLUEPRINT MATCH HOOK (proposed) ★
   │   query source = ?
   │
   ▼
[assemble_context_messages] → persistent_msgs(blueprints) → checkpoint
```

The key surface is the **MessageQueue row**, which is the only place a
parent-supplied structured signal can survive the spawn boundary. The task
message is also in scope via `_process_message_with_tracking()`'s function
parameters.

### Data Flow (proposed)

1. Child instance is created from spawn params
   (`_spawn_instance_db_sync()`, `daemon/services/instance_lifecycle.py:3036-3190`).
2. First message is enqueued (task message + parent-supplied `context`).
3. `_process_message_with_tracking()` is invoked.
4. **At the `project_injected` gate** (before graph execution):
   - Read `task_message` (function arg).
   - Read `task_context` from `message_queue_row.metadata["task_context"]`
     (may be null).
   - Construct query (see "Recommendation" below).
   - Run blueprint matching service (BM25 + vector).
   - Cap at 5 results.
   - Inject matched blueprints into `persistent_context_msgs`.
   - Set `project_injected = True` (so subsequent turns skip matching).
5. Graph executes normally with the augmented context.

---

## Integration Points

| # | Integration | Type | Contract | Auth | Failure Mode | File:Line |
|---|-------------|------|----------|------|--------------|-----------|
| 1 | `task_message` (text) | sync arg | UTF-8 string; non-null after `_process_message_with_tracking` validation | n/a | If empty: skip matching → 0 blueprints injected (graceful) | `daemon/services/instance_messaging.py:1972` |
| 2 | `task_context` (parent `context` param) | sync via MessageQueue row metadata | JSON → rendered markdown block; **nullable** | n/a | If null/empty: query degrades to task message only | `daemon/tools/instance.py:47` (render); `daemon/services/task_processor.py:342` (extract) |
| 3 | `matched_blueprints` (proposed) | sync via `assemble_context_messages` | List of `{ blueprint_id, content, score }`; capped at 5 | n/a | If match fails: log + inject empty list (graceful) | `daemon/services/context_messages.py:1065-1165` (assembly) |
| 4 | `project_injected` flag | sync gate | Boolean on instance | n/a | Read-only gate — never reset | `daemon/services/instance_messaging.py:1920-1942` |
| 5 | Blueprint match service | sync (one-shot) | BM25 + vector similarity; returns top-N | n/a | If service unavailable: treat as 0 matches (graceful) | not yet implemented (proposed: `daemon/services/blueprint_match.py`) |

### Integration Detail: parent→child context channel

- **Protocol:** structured JSON via `send_message` tool's `context` parameter.
- **Data format:** JSON → rendered as `[SYSTEM CONTEXT: Task Context]`
  markdown block by `_format_task_context()`.
- **Storage:** `MessageQueue.metadata["task_context"]` (NOT on the instance
  record — this is the only place it survives the spawn boundary).
- **Extraction:** `task_processor.py:342` reads from `metadata` and injects
  into `persistent_context_msgs[0]`.
- **Known property:** **optional/nullable**. Many parent→child sends will NOT
  include it. This is the strongest signal that the design must degrade
  gracefully when the signal is absent.

### Integration Detail: parent blueprint inheritance (NOT in current architecture)

- **Proposed location (Option C/D):** store parent's matched blueprints on
  MessageQueue row as `metadata={"matched_blueprints": [...]}` and extract
  in child.
- **Current state:** **does not exist**. Parent blueprint matches are not
  persisted anywhere — context is re-derived each turn by
  `assemble_context_messages()`. Options C/D require building this new
  plumbing.

---

## Trade-offs

### Options Considered

1. **Option A — Task message only.** Query = the task instruction the parent
   sends.
2. **Option B — Task message + `context` parameter.** Query = concatenation of
   task message + the Tier 2A `context` structured context (when non-empty).
3. **Option C — Inherit parent's blueprint matches + task-message re-match.**
   Child inherits parent's matched blueprints, then re-matches on task message.
4. **Option D — Hybrid: inherit `core.md` + task-message-match for area
   blueprints.** Always get the project overview, then match task-specific
   blueprints.

### Comparison

| Criterion | A — Task only | B — Task + context | C — Inherit + re-match | D — Core.md + match |
|-----------|---------------|--------------------|------------------------|---------------------|
| **Matching accuracy** | Low: ignores parent's structured intent (objective, constraints, scope). | High: matches against both natural-language and structured signals. | Medium-high: re-match on task message is fine, but inherited blueprints may be off-topic. | Medium: core.md is usually relevant; re-match on task is fine. |
| **Token budget (≤5)** | ≤5 used on task-similar blueprints; may miss overviews. | ≤5 used on best collective matches — most efficient use. | Could breach cap: parent matches + new matches → dedup needed. | 1 slot reserved for core.md → only 4 left for task matches; could cap task-specific matches. |
| **Implementation simplicity** | **Best.** Uses only what already exists. No new plumbing. | **Best.** Uses only what already exists. Read `task_context` from `metadata`. No new plumbing. | **Worst.** Requires new `metadata={"matched_blueprints": [...]}` field on MessageQueue **plus** extraction in child. | **Worst.** Same as C (inheritance plumbing) plus a hardcoded "always inject core.md" rule. |
| **Reliability (robust to missing signals)** | **Best.** Single source — task message is always present. | **Good.** Context is optional; gracefully degrades to A. | **Medium.** Inherited set may be stale (parent worked on a different task). | **Medium.** Inherited core.md may be irrelevant for ephemeral/projects with no real `core.md`. |
| **Reversibility** | Easy to add signals later. | Easy to add signals later. | Hard to undo once inheritance is in production. | Hard to undo once hardcoded rule is in production. |
| **Cost (latency on first turn)** | Minimal. | Minimal. | Higher — match twice, dedup. | Higher — match + dedup + hardcoded rule. |
| **Win?** | Smartest fallback, but baseline. | **★** Best on balance. | Over-engineered. | Over-engineered + special-cases. |

### Recommendation

**Pick: Option B — Task message + `context` parameter.**

**Reasoning:** Option B is the only option that simultaneously (a) consumes
both parent-supplied signals, (b) requires no new storage plumbing, and
(c) degrades gracefully when `context` is absent. Options C and D would
require inventing a new persistence scheme for parent blueprint matches —
which does not exist in the current architecture and would have to be
designed, tested, and shipped alongside the blueprint subsystem itself.

The structurally-new plumbing for C/D is a high cost for a low benefit:
parent blueprint matches are **tasks the parent worked on**, not tasks the
child will work on. Inheritance would push the child toward blueprints
relevant to the parent's recent work, which is a different signal than
"what is this child about to do." That semantic gap is the core reason
inheritance is the wrong default.

**Hardcoded "always inject core.md" (Option D's signature move) is
rejected** — see "Open Question / Should we always inherit core.md?"
below. If `core.md` is genuinely relevant for a project, it will score
highly on virtually any task query and will naturally surface in the top-5.
Hardcoding the injection defeats the entire point of relevance matching.

**Assumptions (must hold for recommendation to be valid):**

- `task_context` rendering (`_format_task_context()`, `daemon/tools/instance.py:47`)
  produces text that is semantically meaningful for embedding and BM25
  matching. This is plausible: parents use it to encode scope, constraints,
  and acceptance criteria — exactly the signals that improve retrieval.
- BM25 + vector similarity is sensitive to query length. Both shorter
  (task-only) and longer (task + context) queries must produce comparable
  ranking quality. If long-context queries degrade recall, Option B's
  advantage shrinks.
- Parents that choose to pass `context` use it meaningfully (not as
  boilerplate). If the field is noisy, including it could hurt matching
  — this should be a follow-up empirical check.

**Reversibility:** Easy. Option B → Option A is a one-line simplification
(drop the context concatenation). Option B → Option C (if inheritance
ever becomes desirable) is a feature addition, not a breaking change.

---

## Open Question: Should children ALWAYS inherit `core.md` regardless of match score?

**Argument FOR:** The project overview is foundational — it establishes the
project's identity, structure, and conventions. Any task the child works on
benefits from having this context. In a well-designed project, `core.md`
should be one of the most general but useful documents; letting it slip
out of the top-5 because the task message is too narrow would be a
miss. Reserving a slot for `core.md` is a small cost for a guaranteed
contextual anchor.

**Argument AGAINST:** Hardcoding injection defeats the purpose of relevance
matching. If `core.md` is genuinely a high-quality overview, it will
score highly on nearly any task query and naturally surface in the top-5.
In a project where `core.md` is stale, low-quality, or non-existent, a
hardcoded injection wastes a token slot on noise. The matching service
should be the sole authority on what surfaces. If we discover through
empirical evaluation that `core.md` is consistently missed despite being
relevant, the fix is to **improve matching** (e.g., boost project-overview
files in the index), not to **special-case** the result.

**Decision: NO** — do not hardcode `core.md` injection. Trust matching.
If empirical evidence later shows `core.md` is consistently missed when
relevant, the fix is at the **indexing/ranking** layer (e.g., a
"core document" weight boost), not at the **injection** layer. This
keeps the design clean and uniform.

---

## Specification (for Option B)

### Query construction

```python
def build_blueprint_query(task_message: str, task_context: str | None) -> str:
    """Build the query string for blueprint matching.

    Both signals are concatenated when task_context is present and non-empty.
    When task_context is missing (the common case for many parent→child sends),
    the query degrades to the task message alone — identical to Option A.
    """
    if task_context and task_context.strip():
        # Two newlines act as a soft separator for both BM25 and embeddings.
        return f"{task_message}\n\n{task_context}"
    return task_message
```

### Capping and ordering

- Cap at **5** blueprints, ordered by `combined_score` (BM25 + vector).
- No re-ranking LLM step (keep first-turn latency low).
- If `< 5` matches exceed the relevance threshold, inject only those.
- If no matches exceed the threshold, inject **0** blueprints (do not
  pad with low-relevance hits).

### Concurrency and ordering

- Match runs **once** at the `project_injected` gate, **before** graph
  execution.
- Match latency is added to the first turn's latency. This is acceptable
  because: (a) subsequent turns have zero match cost, (b) the blueprin
  matching service is synchronous and fast (BM25 + small vector model),
  (c) the user does not perceive first-turn latency as "task work"
  latency.

---

## Implementation Location

### Primary hook

**File:** `daemon/services/instance_messaging.py`
**Function:** `_process_message_with_tracking()`
**Lines:** ~1920-1942 (the `project_injected` gate), before line 1972 (the
established function entry per the research).

The hook would:

1. Check `project_injected` (already done today).
2. If false, build the query from `task_message` + `metadata.task_context`.
3. Call blueprint match service.
4. Persist results into `persistent_context_msgs` (the persistent block
   already assembled by `assemble_context_messages()`).
5. Set `project_injected = True`.

### Data flow through `assemble_context_messages()`

The blueprint match service is called **once** at the gate; its results are
passed into `assemble_context_messages()` as an additional argument (or
attached to the instance/task state and read inside). Matched blueprints
become `HumanMessage` entries with:

```python
HumanMessage(
    content=<blueprint markdown content>,
    additional_kwargs={
        "injected_message": True,
        "context_kind": "blueprints",
        "blueprint_id": "<id>",
        "score": <float>,
    },
)
```

This matches the existing pattern for other injected context kinds
(`task_context`, `shared_context`, etc., per `context_messages.py:1065-1165`).

### Where `task_context` is read

Already extracted in `task_processor.py:342` and injected into
`persistent_context_msgs[0]`. No new extraction code needed — the same
value is available for the blueprint query.

---

## Risks and Edge Cases

| # | Edge case | Impact | Mitigation |
|---|-----------|--------|------------|
| 1 | Empty task message | Query is empty → 0 matches → no blueprints injected. Child has no project context. | Acceptable: a task with no text is degenerate; blueprint matching can do nothing useful. The project-context block is still populated via other channels (shared context, project metadata). |
| 2 | Very long `task_context` (e.g., 50K chars) | Query becomes huge; BM25/embedding latency spikes; may OOM or degrade ranking. | Truncate `task_context` to a reasonable cap (e.g., 4K chars) before concatenation. Or: use the **task message** as the primary signal and only sample the first N tokens of `task_context` for enrichment. |
| 3 | Parent spawns with no project (project_id is null) | Blueprint matching is project-scoped; no project → no corpus → 0 matches. | No-op: skip matching. This is the **correct** behavior. |
| 4 | `task_context` is set but spammy/boilerplate | Noisy signal drags matching toward low-relevance blueprints. | Empirical check after rollout. If it hurts, revisit with a quality gate (e.g., reject context that fails a relevance check before adding to query). |
| 5 | Match service unavailable on first turn | First turn cannot inject blueprints. | Graceful degrade: log + inject 0 blueprints. Do not block message processing. The child will still see other context kinds. |
| 6 | Core.md missed despite being relevant | Recommendation argument: matching should naturally surface it. | If empirically observed, fix at the **indexing/ranking** layer (boost project-overview docs), not via hardcoded injection. |
| 7 | Blueprint subsystem is rolled out project-by-project | A child spawned from a project with no blueprints yet → 0 matches. | No-op. The system should be inactive when no blueprints exist; do not error. |
| 8 | Concurrency: two spawns race to set `project_injected` | Duplicate or racing matches. | Auth: `project_injected` is set under the same per-instance lock/scope as the rest of the first-turn setup. Already handled by existing turn-reconciler infrastructure. |
| 9 | Parent sends via the old `send_message` (no `context` param) | Query falls back to task message only (Option A behavior). | This is the graceful-degradation path. Tested explicitly. |

---

## Scalability

### Growth Assumptions

- **Agents per project:** hundreds to low thousands over 2 years.
- **Blueprints per project:** dozens (target).
- **First-turn latency budget:** ~500ms added for BM25 + vector match on
  50–100 blueprints. Acceptable: first-turn latency is already
  dominated by other IO.

### Bottlenecks

| # | Bottleneck | Threshold | File:Line | Impact |
|---|------------|-----------|-----------|--------|
| 1 | First-turn match latency | ~50-200ms per instance (50-100 blueprints) | proposed `blueprint_match.py` | Adds to first-turn latency. Subsequent turns: zero. |
| 2 | Concurrency on first turns | N children all matching at once | same | Embarrasingly parallel — match service should be stateless and thread-safe. |
| 3 | `task_context` size | Truncate to 4K chars (proposed) | `daemon/tools/instance.py:47` (render) | Bounded query latency. |

### Scaling cliffs

- **Corpus size > 500 blueprints per project:** switch to a precomputed
  index (e.g., FAISS or pgvector) — out of scope for the first iteration.
- **Match latency > 1s on first turn:** move to async pre-warming — out of
  scope for the first iteration.

---

## Technical Debt

### Items Affecting This Decision

| # | Debt Item | Impact on Recommendation | Severity | File:Line |
|---|-----------|--------------------------|----------|-----------|
| 1 | Parent blueprint matches are NOT persisted | Forces recommendation toward B (which needs no new plumbing); C/D would require building this. | Low (workaround exists) | n/a — does not exist in current code |
| 2 | No `core.md`-specific weighting | Recommendation rejects hardcoded injection; relies on ranking. If empirical eval shows misses, fixing requires indexing-layer changes (out of scope now). | Low | n/a |
| 3 | `task_context` truncation policy undefined | Very long context could degrade match quality. | Medium | `daemon/tools/instance.py:47` render path |

### Items NOT Affecting This Decision

- **Turn-reconciler migration** (Phase 4b/4c deferred): future-proofing work
  that does not touch blueprint matching.
- **PG-only schema migration**: blueprint storage will follow the same
  dual-driver pattern (`_ensure_postgres_columns()`).
- **Skill-evolution routine**: completely orthogonal.

### Recommended Paydown

- **Truncate `task_context` before query use** (4K char cap) — small
  hygiene fix to bound match latency.
- **Empirical evaluation after rollout** (1–2 weeks of telemetry): does
  Option B produce measurably better task-relevant blueprint injection
  than Option A? If not, revert to A. If yes, ship B.

---

## Open Questions

1. **Should `core.md` get a soft ranking boost in the index?** The
   recommendation says no hardcoded injection, but a small ranking boost
   could be useful. This is an indexing-layer decision, not an
   injection-layer one. Resolve during the indexing implementation.
2. **What is the right `task_context` truncation cap?** 4K chars is a
   guess. Empirical tuning: does 2K vs 8K chars materially change match
   quality?
3. **Should the match service emit a debug log of the chosen top-5?**
   Useful for the empirical evaluation. Probably yes, gated behind a
   feature flag.
4. **Does the `send_message` API surface `context` to children-only or
   always?** Per the research, the `context` param is general (not
   child-spawn-only). The blueprint matching hook would activate on
   **any** first-turn message that has a `task_context` — including
   user-to-standalone-agent messages. This is the desired behavior (the
   same parametric intent applies), but it should be called out.

---

## References

- **Research Finding 1** — `task_message` reliably available at
  `_process_message_with_tracking()`: `daemon/services/instance_messaging.py:1972`
- **Research Finding 2** — `task_context` rendered/extract path:
  `daemon/tools/instance.py:47` (render), `daemon/services/task_processor.py:342` (extract),
  stored in `metadata.task_context` on **MessageQueue row** (not instance)
- **Research Finding 3** — Parent blueprint matches NOT persisted:
  `assemble_context_messages()` re-derives context each turn
  (`daemon/services/context_messages.py:1065-1165`)
- **Research Finding 4** — `project_injected` gate is the natural hook:
  `daemon/services/instance_messaging.py:1920-1942`
- **Research Finding 5** — `agent_id` and `project_id` reliable at spawn:
  `_spawn_instance_db_sync()` (`daemon/services/instance_lifecycle.py:3036-3190`)
- **Research Finding 6** — `assemble_context_messages()` returns
  `(persistent_msgs, ephemeral_msgs)`; persistent msgs checkpointed once:
  `daemon/services/context_messages.py:1065-1165`
- **Tier 2A feature** — `context` parameter added to `send_message` tool
  (recent history, project-blueprint subsystem)
- **Pattern** — `context_kind` taxonomy for injected messages (existing
  convention used by `task_context`, `shared_context`, etc.)
