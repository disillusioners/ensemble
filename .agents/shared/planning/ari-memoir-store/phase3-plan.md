# Phase 3 — Embedding worker

**Depends on:** P1 (P2's `store_embedding`/`get_pending_embeddings` contract can land in parallel; worker consumes it). **Flags consumed:** #7 (whole phase), #13 (kill-switch gating).

## 1. Objective

The background async embedding compute: after `memoir_write` INSERT, lexical is live immediately (DB-side `tsv`) and the embedding lands when the worker reaches the row — never blocking the write (DD-20), degrading gracefully when the LLM lane is down (P3/DD-6), catching up after restart, observable per tick.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D3.1 | Owner = dedicated lifespan asyncio task (api.py startup, strong-ref set, cancel+await on shutdown) | the house internal-loop family: readiness refresher `daemon/api.py:2772-2777` ("the ONLY database toucher… asyncio.create_task stored on app.state, cancelled and awaited"), bash-reconcile `:940-975` (`_BASH_RECONCILE_BG_TASKS` strong refs) — all verified | job-queue dispatch (agent-work machinery; admission states/watchers = user-visible noise for an internal index loop); generic sweep service (wrong shape: wants short-latency + catch-up) |
| D3.2 | Queue = `embedding IS NULL AND embedded_at IS NULL` (partial index from P1); boot catch-up = first tick re-scans all NULLs — durability by construction | no side table, no outbox, no state to lose; restart-safe trivially | a pending-jobs table (durable but redundant — the NULL IS the state); RAM queue (lost on restart) |
| D3.3 | Per tick: scan ≤ `embedding_batch_cap` (default 8) pending rows; embed `title + body` (title prepended, newline-joined — the critical-notes "summary+reference" shape); via the OpenAI-compatible `/embeddings` endpoint, sync client behind `asyncio.to_thread` | `skill_embedding_service` client shape verified (`client.embeddings.create(model=embed_model, input=text)` at `daemon/services/skill_embedding_service.py:204`; sync-behind-to_thread documented :37); config from the shared `EMBEDDING_*` family (`daemon/config.py:4374-4376`) | async client (no house precedent; to_thread matches the service family) |
| D3.4 | Write-back WITH identity: `store_embedding(id, vector, model, dims, now)` sets embedding/model/dims/embedded_at and CLEARS embedding_error; attempts unchanged on success | per-row model identity is DD-6; `critical_note_embeddings` `model`+`dims` columns precedent (verified :293-347) | trusting the configured model name at read time (config drift would silently mix identities) |
| D3.5 | Retry: `record_embedding_attempt(id, error)` increments `embedding_attempts`, sets `embedding_error`; scan skips rows with `attempts >= 5` (TERMINAL — lexical-only forever, reason preserved, future backfill resets by clearing attempts) | bounded, durable, observable; no infinite retry burn on poison rows | infinite retry (wasted lane calls every tick); dead-letter table (redundant with attempts column) |
| D3.6 | Lane-down latch: after 3 consecutive ALL-FAIL ticks, drop to slow mode (300s ticks) until the first success; a partial-success tick does not latch | the known live-proxy defect class makes graceful lane-down a first-class state; P3 "the lane being down must degrade retrieval, never the write" | hammering a dead lane at 30s |
| D3.7 | Observability: one structured line per tick `[MemoirEmbed] pending=N embedded=K errors=E terminal=M tick_ms=T slow_mode=bool`; no metrics-registry family in v1 (routed as optional follow-up) | greppable, pack-asserted, zero new surface; a central-metrics family is a real but separate lane | silent success (the flag's "invisible until semantic recall misses") |
| D3.8 | Knob: `memoir_enabled()` False ⇒ the task is never started; rows written while OFF simply stay NULL (P3 degradation, not loss) | flag #13 OFF = whole personality; worker off is part of it | worker running with tools off (would embed rows nothing can read — wasted lane) |
| D3.9 | DB access strictly off-loop (`asyncio.to_thread` around repository calls) | the routed event-loop sync-DB incident class; snapshot-digest builder precedent (`context_messages.py:358-392`, `asyncio.to_thread(repo.get, …)` verified) | direct sync calls on the loop |

## 3. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T3.1 | Worker module: loop + scan + batch + identity write-back + attempts | unit-testable with a mock embedder + temp DB | daemon/services/memoir_embedding_worker.py | P1 (P2.T2.1 contract) | AC1 |
| T3.2 | api.py lifespan wiring: start (knob-gated), strong ref, cancel+await shutdown | boot with knob ON starts exactly one task; shutdown drains clean | daemon/api.py (lifespan startup/shutdown blocks) | T3.1 | AC2 |
| T3.3 | Latch + terminal logic (D3.5/D3.6) | mock lane-down: 3 fail-ticks → slow mode; success → normal; 5 failures → terminal, excluded from scans | memoir_embedding_worker.py | T3.1 | AC3 |
| T3.4 | Counter line + log discipline (D3.7) | every tick emits the line; mutation leg removes it → RED | memoir_embedding_worker.py | T3.1 | AC4 |
| T3.5 | Boot catch-up + degradation integration legs | restart with N pending → first tick embeds ≤batch; lane down → lexical-only recall still returns rows (with missing-embedding count in footer) | (pack-level; runs in P7 consolidation too) | T3.2 | AC5 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Batch pipeline: N pending → ≤8 embedded/tick, identity columns set, embedded_at ISO, error cleared; row-level independence (crash mid-batch leaves rest NULL, next tick proceeds) |
| AC2 | Lifespan: task present exactly once (strong-ref census), cancelled+awaited on shutdown (no orphan task leak), absent with knob OFF |
| AC3 | Terminal-at-5 + slow-mode latch behaviors exact (fixtures); terminal row's `embedding_error` retains last reason |
| AC4 | `[MemoirEmbed]` line every tick with honest counters (mutation: counter arithmetic wrong → RED) |
| AC5 | End-to-end: write → (≤35s) semantic recall finds it; lane-down write → lexical-only recall works; daemon restart → catch-up without manual action |

## 5. Test packs

- `test/packs/memoir_embedding_worker_unit_test.sh` — AC1/AC3/AC4 with mock endpoint + temp SQLite.
- `test/packs/memoir_embedding_degradation_test.sh` — AC5 lane-down + restart-catch-up legs (mock embedder fail/succeed switch).
- Worker lifespan legs fold into the P7 boot smoke (task census at boot).

## 6. Flag consumption

#7 entirely (owner/queue/retry/durability/counter = D3.1–D3.7), #13 (D3.8), DD-6 (D3.4), DD-20 (design asymmetry preserved: INSERT never awaits the worker).
