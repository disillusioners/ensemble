# Phase 5 OOS-1..7 — Out-of-Scope Enumeration

> Date: 2026-09-04 (UTC) | Author: coder[v2] (Phase-5 Stage-2 implementer)
> Branch: `feature/langgraph-checkpoint-perf-v2` @ `41347ee4`
> Source: `.agents/shared/planning/langgraph-checkpoint-perf-v2/phase5-plan.md` T5.15 + `plan-overview.md` "Out of Scope" + `requirements.md` FR-14
> Folded-into-commit: yes — staged together with the deferred-decision docs (T5.10, T5.14) per the brief's "fold into a docs commit with later doc tasks" instruction.

The seven items below are EXPLICITLY OUT OF SCOPE for the v2
langgraph-checkpoint-perf initiative. Each item carries its rationale
("why it is not here, not just because we ran out of time"), the
binding constraint that excludes it (architect § / Phase-1 decision / v1
D-row), and the re-trigger condition (under what circumstances the item
would be RE-OPENED — or its removal-class stamp, for HARD EXCLUSIONS).
A reviewer reading this list in two years must be able to determine, for
each OOS, exactly which guard would have to break before v3 picks it
up.

---

## OOS-1 — Cursor pagination on `GET /instances/{id}/messages`

**Out-of-scope + rationale.** Cursor pagination is a frontend-consumer
concern (the agent chat panel and any paginated message-list UI). The
v2 daemon already accepts `limit` and `before` query params on the
endpoint (per `tests/integration/test_api_messages.py`); the absence of
a `cursor` / opaque-cursor return shape is a UI/AFFORDANCE call, not a
daemon-internal pathology. Adding it requires:

1. A wire-format addition (return a `next_cursor` field; consumers
   switch from `before=<timestamp>` to `cursor=<token>`).
2. A stable cursor encoding (typically `(created_at, message_id)` pair
   or a server-side cursor table).
3. FE plumbing (chat panel state machine, infinite-scroll trigger,
   back-button behavior).

All three are FE-blast-radius changes. The v2 initiative is
**daemon-side only** (C-1 / branch boundary), and a coordinated FE
deploy is not in the v2 schedule. Reintroducing the alist walk as a
daemon-side "fix" for a UI pagination gap would directly re-violate
PR3's read flip and the §32 binding intent (FR-2).

**Re-trigger condition (pick this up in v3 IF either applies).**
- A FE consumer ships a new paginated message view that genuinely
  cannot be expressed via `limit + before` without round-tripping the
  full thread (e.g. mobile infinite-scroll where each page-fetch
  re-renders chat history from scratch). Cursor is then a UI
  ergonomics fix, not a backend pathology fix.
- A new sequencing consumer needs server-side cursor stability (e.g.
  audit log replay). This also unblocks OOS-related decisions T5.10
  (D-2 seq-index DEFER triggers on this same condition).

**NOT a v2 deliverable. NOT a "we'll see" defer — the item has a named
re-trigger.**

---

## OOS-2 — `agent_messages` / `agent_events` durable store / schema change

**Out-of-scope + rationale.** Promoting `message_metadata` (or any
side-table) to a first-class event-sourcing substrate (`agent_messages`
events table, `agent_events` topic, schema-registry-managed payloads)
is a SCHEMA REDESIGN with HIGH blast radius:

- All backends (PG + SQLite) need the new tables + indices, with
  dual-driver discipline preserved (C-15: `create_all` is canonical).
- Every consumer that today reads `message_metadata` /
  `checkpoints.channel_values.messages` would need a migration path.
- The read-flip (PR3) is a strict subset: it makes the existing
  checkpoint-store-only path FAST by side-table enrichment. Adding a
  separate durable event store does not make the existing path faster
  and creates two sources of truth for first-appearance timestamps.

The v2 initiative's goal is to LAND the v1 fix set without breaking
the v2 schema, NOT to introduce new tables. The event-store path was
considered (Solution B in `~/Downloads/langgraph-checkpoint-performance-discussion.md`)
and explicitly de-prioritized in `plan-overview.md` §"Architect §7
source-doc gap triage" — "orthogonal persistence substrate".

**Re-trigger condition.** A v3+ event-sourcing initiative is a separate,
cross-cutting concern (would touch repositories, routers, FE replay
buffers, and the checkpoint adapter). Out of scope for v2.

**NOT a v2 deliverable. NOT a "we'll see" defer — orthogonal persistence
substrate, would be its own initiative.**

---

## OOS-3 — `ShallowPostgresSaver` (HARD EXCLUSION)

**Out-of-scope + rationale.** Upstream `langgraph.checkpoint.postgres.shallow.Shal­lowPostgresSaver`
is **DEPRECATED** by the LangGraph maintainers (the shallow saver keeps
only the latest checkpoint per thread, breaking multi-turn resume and
history audit). v2 has no `ShallowPostgresSaver` consumer; introducing
one would:

- Lose history for every long-lived agent thread (the only customers
  of v2's checkpoint system are paused/revivable terminals; "shallow"
  silently destroys their work on the next aput).
- Have no SQLite equivalent (no `AsyncShallowSqliteSaver` exists), so
  v2's test fixtures would silently diverge from prod (C-13
  reproducibility violation).
- Violate PR2 review §2 ("shallow is not a fix for performance; it is
  a fix for storage cost, and it trades correctness for speed").
- Conflict with the v2 revive-on-send semantics
  (`daemon/services/instance_messaging.py` reuse-revive path reads the
  prior checkpoint to reconstitute state; a shallow saver would always
  hand back the LATEST checkpoint's state and silently merge messages).

This is a HARD owner exclusion — not a "future work". Adding it back
requires a deliberate decision by the project owner (NOT implementer).

**Re-trigger condition.** NONE for v2 / v3. This is a project-policy
exclusion, not a scope item. Any agent that picks this up must be
re-dispatched with explicit owner sign-off AND a fallback story for
SQLite test parity.

**HARD EXCLUSION. Re-introduction requires owner sign-off (NOT
implementer decision).**

---

## OOS-4 — LZ4 / TOAST compression on `checkpoint_blobs` / message payloads

**Out-of-scope + rationale.** LangGraph's `AsyncPostgresSaver` stores
checkpoint channel blobs (including messages) as raw `bytea` columns;
PG's TOAST kicks in automatically above ~2KB and stores large blobs
out-of-line (possibly compressed by PG's built-in pglz). Adding LZ4
compression at the application layer would:

- Cost CPU on every aput (compression is non-trivial for message-rich
  payloads; 206 MB of pre-fix-pathology was the APUT cost, not the
  read cost).
- Interact poorly with PG's TOAST + pglz double-compression (you'd
  be paying compress twice for marginal gain).
- Have NO observable benefit at v2's measured scale: post-PR3 the
  `GET /messages` measured transfer is 762 KB at 100 messages (NFR-3
  target `<1 MB` is met at the larger scale too — see
  `tests/performance/test_message_api_cost.py` cell `(1000, 100)`).
  Compression saves bytes you don't have.

The v1 source doc §32 discussion (Solution K) explicitly says "perf
gain unproven at v2's scale (<1 MB transfer for 100 msgs)". The v2
plan inherits this and lists LZ4 in the OOS section with the same
rationale. Adding it back is a perf-investment call that requires
measured evidence that compression actually wins at prod volume
(current data: it would not).

**Re-trigger condition.** A measured N where (a) the
`message_api_saver_op_latency_seconds` histogram (T5.3 metrics
surface) shows p99 aput latency exceeding an SLO AND (b) the bulk of
that latency is serialization / transfer-bound (not commit-bound). At
that point a compression A/B test becomes a real candidate; before
that, it is speculation.

**NOT a v2 deliverable. NOT a "we'll see" defer — unproven gain at
measured v2 scale.**

---

## OOS-5 — Thread rotation

**Out-of-scope + rationale.** Thread rotation (re-keying an agent's
checkpoint history under a new `thread_id` on a cadence, e.g. daily or
weekly) is a HIGH blast-radius schema + lifecycle change:

- Breaks resume continuity for any in-flight user: rotating mid-conversation
  would silently orphan the user's active session.
- Requires a new "thread lineage" table (or a `parent_thread_id` column)
  to preserve the audit trail, plus a migration path for existing
  threads (which is the entire prod corpus).
- Conflicts with the COMPLETED→RUNNING revive-on-send semantics:
  `daemon/services/instance_messaging.py` resolves a thread by the
  instance's stored `thread_id`; rotation would break that lookup
  unless the lineage table is consulted.
- Storage cost: `checkpoint_blobs` already has a reference-aware
  prune (PR4); thread rotation is a SECONDARY cost-control measure
  that does not address the actual pathology (orphan blob retention
  from unbounded turns).

The PR4 fold (SERIALIZABLE wrap + ZERO_REFS_FAIL_SAFE) is the
correct cost-control answer for v2's scale. Thread rotation is a
"the whole corpus is too big" lever, which v2 is not in.

**Re-trigger condition.** (a) prod `pg_relation_size(checkpoint_blobs)`
crosses a named ceiling (e.g. > 100 GB) AFTER PR4's destructive enable
is armed; (b) the operator is willing to accept a brief per-user
resume discontinuity. Until both, this is speculation.

**NOT a v2 deliverable. NOT a "we'll see" defer — high blast radius,
orthogonal cost-control.**

---

## OOS-6 — Artifact out-of-band (OOB) storage

**Out-of-scope + rationale.** Artifacts (LLM prompt caches, RAG
embeddings, large tool outputs) currently live as paths inside the
checkpoint channel_values; moving them to a separate object store
(S3, GCS, or a local blob dir) is a storage-architecture change:

- Touches the artifact-creation surface (every tool that emits a
  large payload) AND the artifact-consumption surface (every agent
  that reads it back).
- Has its own correctness story (eventual consistency, cache
  invalidation, signing) that has nothing to do with checkpoint
  performance.
- The 33–114× read-flip win (PR3) makes the artifact cost irrelevant
  on the GET /messages path; artifacts are bounded per-call, not
  per-thread-history.

The v1 source doc §32 (Solution R / infra-level) explicitly labels
this "orthogonal infra decision" and `plan-overview.md` §"Architect §7
source-doc gap triage" carries the same disposition. There is no v2
work to do here.

**Re-trigger condition.** A separate storage-architecture initiative
(off the v2 branch, off the v2 timeline) is the right shape. Out of
scope here.

**NOT a v2 deliverable. NOT a "we'll see" defer — orthogonal infra
decision.**

---

## OOS-7 — Backfill (Solution N)

**Out-of-scope + rationale.** Backfilling `message_metadata` rows for
checkpoints created BEFORE the PR2 migration landed (i.e. pre-side-table
history) would fill the timestamp gap for very-old messages that
currently fall through to `state.ts` fallback (PR3).

The v1 source doc §32 (Solution N) called backfill "OPTIONAL final
phase ONLY IF cheap". The corrected disposition (architect §2.4,
plan-overview.md §"Out of Scope · Backfill", `requirements.md` FR-14 +
AC-14.1 + AC-14.2) is to evaluate the corrected Criteria
**A′/B′/C′** and DROP the backfill iff all three are TRUE:

- **A′** — PR3's `state.ts` fallback timestamps suffice for UI display
  of pre-side-table messages (accepted degradation, non-breaking;
  `daemon/persistence.py:368-371` documents this explicitly: "side-table
  rows for messages NOT in the latest checkpoint simply never join…
  Under-record… is NOT a bug: it falls through to the ``state.ts``
  fallback").
- **B′** — NO scheduled/batch consumer needs accurate first-appearance
  timestamps for pre-side-table history. `created_at` is the only
  consumer of these timestamps today; `created_at` covers first-appearance
  for any tapped message, and the fallback covers pre-tap history.
- **C′** — The §3 row-growth defect (the `message_metadata` unbounded
  growth) is addressed by the `delete_for_thread` prune (architect §3
  MERGE PRECONDITION — T5.19 in `phase5-plan.md`), NOT by backfill.

The full evaluation is recorded in
`.agents/shared/planning/langgraph-checkpoint-perf-v2/phase5-backfill-disposition.md`
per T5.14. Architect-verified expected outcome: backfill is DROPPED
(all three criteria TRUE).

If ANY of A′/B′/C′ were FALSE, the only acceptable shape would be a
bounded, operator-initiated **OFFLINE** backfill via
`daemon/migrations/checkpoint_migrator.py` (already exempt from the
§33 alist ban). The online / live-path shape is UNACCEPTABLE per
architect §2.4 — it would re-introduce the O(N²) alist walk that PR3
explicitly removes (re-walking every checkpoint to mint rows for
un-tapped messages would resurrect the pre-PR3 pathology as the
backfill's own runtime cost). Live-path backfill would also violate
the Phase-1 read-flip acceptance test
(`tests/unit/persistence/test_get_instance_messages_no_alist.py`)
which fails LOUDLY if alist fires.

**Re-trigger condition.** If a future consumer needs accurate
first-appearance timestamps for pre-side-table history AND the A′/B′
fallback path is shown insufficient (e.g. via a documented user report
that the uniform-`state.ts` is misleading). At that point the
**OFFLINE-ONLY** shape is the only acceptable path, with operator
sign-off + bounded batch size + `MAX_BACKFILL_ROWS` ceiling (W11
hardening, see `phase5-backfill-disposition.md` §"If ever revisited").
The LIVE-PATH shape stays UNACCEPTABLE — it is a HARD exclusion
tied to PR3's read flip.

**Dropped per architect §2.4 (A′/B′/C′ all TRUE expected); live-path
backfill is a HARD EXCLUSION; offline shape is the only re-introduction
path and requires operator sign-off + named ceiling.**

---

## Summary

| # | Item | Class | Re-trigger |
|---|------|-------|-----------|
| 1 | Cursor pagination | Defer w/ re-trigger | FE consumer ships a cursor-only view |
| 2 | agent_messages / agent_events durable store | Schema redesign | Separate initiative (not v2) |
| 3 | Shallow saver (HARD EXCLUSION) | Owner policy | NEVER (project-policy exclusion) |
| 4 | LZ4 / TOAST compression | Unproven gain | p99 aput SLO breach + serialization-bound evidence |
| 5 | Thread rotation | High blast radius | `checkpoint_blobs` > 100 GB after PR4 destructive + op accepts discontinuity |
| 6 | Artifact OOB storage | Orthogonal infra | Separate initiative |
| 7 | Backfill (Solution N) | DROP / HARD LIVE EXCLUSION | A′/B′ fails → OFFLINE-only with sign-off |

A reader in two years who finds one of these items inside the v2
deliverable has found a scope violation — point them back to this file.
