# Memoir Embedding Worker — Operator Runbook

The memoir embedding worker (Memoir Store v1, phase 3) computes the
semantic half of memoir episodes in the background. Lexical search is
live the moment a memoir write lands (the DB-side `tsv`); the embedding
arrives when this worker reaches the row — a write is NEVER blocked by
it, and a dead embedding lane degrades recall, never the surface.

Plan: `.agents/shared/planning/ari-memoir-store/phase3-plan.md`
(D3.1–D3.16); amendments AM-27–AM-36 of
`architecture-recommendation.md`.

## The tick line

Every working tick logs one line (the worker's primary operator
surface):

```
[MemoirEmbed] pending=N embedded=K errors=E terminal=M tick_ms=T slow_mode=bool
```

| Field | Meaning |
|-------|---------|
| `pending` | Rows with `embedding IS NULL` that are NOT terminal — the queue depth at tick start. |
| `embedded` | Rows successfully embedded **this tick**. |
| `errors` | Rows that failed **this tick** (embed or write-back). |
| `terminal` | The **TOTAL** terminal rows in the table (the standing backlog), measured after the tick's work. A terminal row is lexically searchable forever and semantically excluded. |
| `tick_ms` | Wall time of the whole tick, in milliseconds. The tick's embed work is bounded at 45 s (`pending` minus `embedded` minus `errors` rows were deferred to the next tick when this budget is hit). |
| `slow_mode` | Whether the lane-down latch is engaged (see below). |

## The knobs (re-read every tick — no restart needed)

| Env var | Default | Effect |
|---------|---------|--------|
| `ENSEMBLE_MEMOIR_ENABLED` | `true` | The kill-switch. `false` **parks** the worker (one `[MemoirEmbed] parked: …` log line; the task stays alive and does no embedding work). Set it back to `true` and the worker **resumes** on the next tick — no restart. At boot it is OFF, the worker task is never started; a restart enables it. |
| `ENSEMBLE_MEMOIR_EMBEDDING_TICK_S` | `60` | Seconds between ticks. Slow it during an incident (e.g. `300`) without a restart. |
| `ENSEMBLE_MEMOIR_EMBEDDING_BATCH_CAP` | `8` | Max rows embedded per tick (embeds are sequential — a count, not a concurrency). |
| `ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS` | `1536` | The expected embedding dimensionality. A mismatch is a per-row TERMINAL (never silently zipped). |
| `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | model `text-embedding-3-small`; base/key fall back to the LLM lane | The shared embedding family the worker consumes. |

A garbage value for a numeric knob is logged and IGNORED (the previous
value keeps applying) — a mis-typed tuning knob never takes the worker
down.

## The lane-down latch

After **3 consecutive all-fail ticks** the worker latches: one
`[MemoirEmbed] latch engaged: …` warning line, then ticks slow to
**300 s** until the first success (`[MemoirEmbed] latch released: …`).
A partial-success tick never latches and releases the latch.

A **401/403** (dead credential) is terminal for the affected rows at
once AND engages the latch on the **first** occurrence — the worker
will not hammer a dead credential.

## Terminal rows

A row is terminal when its `embedding_attempts` reached **5**, or its
`embedding_error` carries the `terminal:` marker (immediate-terminal
classifications: 401/403, other permanent statuses, dimension
mismatch). The worker skips terminal rows forever — they remain
lexically searchable, and recall's footer discloses how many episodes
are semantically excluded. Terminal rows each produce a
`[MemoirEmbed] terminal episode=… reason=… attempts=… error=…` line
when they transition.

**v1 ships NO automatic backfill.** A terminal row is re-embedded only
by the operator requeue below.

## The operator requeue procedure

To re-embed one row (e.g. after fixing the credential or the
dimensionality), clear its attempt counter and its error — the queue
predicate (`embedding IS NULL`) makes it pending again and the next
tick embeds it:

```sql
UPDATE memoir_episodes
SET embedding_attempts = 0,
    embedding_error = NULL
WHERE id = '<episode-id>';
```

To requeue EVERY terminal row:

```sql
UPDATE memoir_episodes
SET embedding_attempts = 0,
    embedding_error = NULL
WHERE embedding IS NULL
  AND (embedding_attempts >= 5 OR embedding_error LIKE 'terminal:%');
```

Notes:

* The requeue is an UPDATE on two columns — it never touches the
  narrative content (the memoir surface is append-only; the kill-switch
  never deletes).
* Do this only after the underlying cause is fixed (a requeued row
  against a still-dead lane just burns its budget again).
* The requeue is idempotent; running it twice changes nothing.

## Readiness surface (advisory)

`GET /readyz` publishes `detail.memoir` — the knob's resolved state
(`enabled`, `resolution`, `source`) plus the worker's live state
(`worker.phase` = `running` | `parked` | `not_started`, the latch flag,
the last tick line's counters). It is ADVISORY ONLY: a parked or failed
worker never flips a readiness component and never adds a degradation
reason — a disabled memoir is not a degraded daemon.

## Placement and isolation

* The worker is one lifespan asyncio task (`memoir-embedding-worker`),
  cancelled and awaited at shutdown.
* Embeds run on a dedicated thread pool (`memoir-embed_*`), never the
  shared default executor; embeds are sequential.
* The embed call carries `httpx.Timeout(30 s, connect 5 s)`; a hung
  endpoint cannot exceed the tick budget.
* All worker SQL is driver-agnostic (no `tsv`); the queue IS
  `embedding IS NULL` — no side table, no outbox, restart-safe by
  construction.
