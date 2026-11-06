# Phase 3 Notes — the embedding worker

Wave 3 of 5, Memoir Store v1 (`feature/ari-memoir-store`, base HEAD at
lane start `12971ce5b`). Ran PARALLEL with the phase-5 coder in the same
worktree (`memoir_briefing.py` / `context_messages.py` /
`instance_messaging.py` are theirs; every commit below is pathspec'd to
this phase's files only).

## 1. Anchor sweep (P-7)

| Plan anchor | Verified value | Drift |
|---|---|---|
| `api.py:2788` `app.state.readiness_refresher_task` naming precedent | present (startup block, now ~:2777-:2800; shifted +~55 lines by this phase's insert above it) | line drift only |
| `api.py:3585-3596` shutdown cancel+await shape | confirmed; now ~:3600+ after this phase's insert | line drift only |
| `api.py:4588-4592` loop guard (`CancelledError: raise` / `Exception: log`) | confirmed in `_periodic_readiness_refresh_loop` (now ~:4640+; +55 line shift from this phase) | line drift only |
| `config.py:4374-4397` the shared `EMBEDDING_*` family | **DRIFTED**: the family (`EmbeddingConfig` + `_shared_embedding_fallback`) lives at ~:4503-:4626. The env names are unchanged: `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` (+ `EMBEDDING_DIMENSIONS` for the skills family) | used the real values; recorded |
| `config.py` `MemoirConfig` numerics | `embedding_tick_s=60`, `embedding_batch_cap=8`, `embedding_dimensions=1536` (env `ENSEMBLE_MEMOIR_EMBEDDING_{TICK_S,BATCH_CAP,DIMENSIONS}`) at ~:3276-:3296 | exact |
| `llm_error_classifier.py:23` `RETRYABLE_STATUS_CODES` | `{429, 500, 502, 503, 504, 520, 521, 522, 523, 524}` — **no 408** (AM-32's premise verified) | exact |
| `skill_embedding_service.py:204` the embed call shape | `client.embeddings.create(model=…, input=text)` confirmed (~:204); the house client sets NO timeout (AM-28's premise verified) | exact |
| wave-2 contract `store_embedding(id, vector, model, dims, now)` | repository.py ~:755 — ONE atomic column-targeted UPDATE, `to_utc_iso(stamp)` internally; returns rowcount | exact |
| wave-2 contract `record_embedding_attempt(id, error) -> int` | repository.py ~:784 — increments + writes error (truncated 500), returns the count AFTER the increment | exact |
| wave-2 contract `get_pending_embeddings` predicate | repository.py ~:825 — **`embedding.is_(None)` ONLY**. The phase3-plan/brief prose says "(plus `embedded_at IS NULL`)" — the frozen surface carries just the one column. EQUIVALENT IN PRACTICE: the four embedding columns are written together atomically, so `embedding IS NULL` implies `embedded_at IS NULL` for every state the worker can observe; the briefing's rule ("do NOT change those signatures") stands | anchor drift, recorded; no repo change |
| D2.11 footer contract | `MemoirRetrievalService.recall` carries `active_model` / `embedding_excluded` / `missing_embedding` / `semantic_enabled` + footer — verified in `memoir_retrieval.py` | exact |
| `openai.OpenAI(timeout=…)` | accepts `httpx.Timeout` (used `Timeout(30.0, connect=5.0)`); `httpx.Timeout` exposes `.read/.write/.pool/.connect`, NOT `.timeout` (the pin asserts the four-attr form) | noted |

## 2. Mechanism decisions (recorded, not silent)

1. **Terminal recording mechanism (the plan names the state, not the
   mechanism).** The frozen wave-2 contract has exactly one
   error-recording primitive (`record_embedding_attempt`, +1 attempt and
   an error string). Terminal is therefore: `attempts >= 5` **OR**
   `embedding_error` starts with the `terminal:` marker; the worker
   prefixes immediate-terminal classifications (401/403, other
   permanent statuses, dims mismatch) with `terminal:` when recording.
   One failed attempt per failure (honest counters), immediate-terminal
   takes effect on the NEXT scan without burning the 5-budget, and the
   runbook requeue (clear both columns) resets it. Pinned by tests +
   the M6 mutation leg.
2. **`M` is measured AFTER the tick's work; `pending=N` at tick START.**
   The tick line's slices: N = what the tick considered, K/E = this
   tick's outcomes, M = the standing terminal backlog NOW (a row that
   goes terminal this tick is counted in the same line). Pinned by the
   M5 mutation leg.
3. **T0.5 injection point = the `/readyz` route, POST-verdict.** The
   advisory detail keys of the house (`detail.mcp`, `detail.skill_sources`)
   are typed fields on `ReadinessComposite` + `to_payload()` blocks in
   `daemon/services/readiness.py` — a file OUTSIDE this phase's
   exclusive set (frozen under the concurrency protocol). The route
   lives in `api.py` (this phase's file), so the block is injected
   AFTER `to_payload()` and after the status code is chosen: it can
   NEVER flip a component or contribute to `reasons` — the guarantee is
   structural. Shape is the `detail.mcp` family (`enabled`/`resolution`/
   `source`/`rejected_value` + `worker` sub-block). If a later council
   prefers the refresh-cycle pattern (a composite field), the block's
   dict is the same and only the plumbing moves.
4. **Per-tick settings re-read is EXPLICIT env reads over the boot
   tier** (D3.12): `_env_int(ENV, boot_value)` per numeric knob;
   garbage/non-positive env values warn and keep the boot value. The
   worker NEVER constructs a fresh `MemoirConfig()` for the numerics —
   a garbage numeric env would make the whole settings model
   unparseable (pydantic parses `embedding_batch_cap` from the env) and
   poison the knob resolution with it. The knob (enabled) still goes
   through `resolve_memoir_knob(config)` — its own fail-closed error
   handling is the house truth table.
5. **Scan window = 200** (`SCAN_LIMIT`): the frozen contract has no
   terminal-aware scan, so the worker fetches a bounded working set and
   filters terminal rows in memory before slicing the batch cap.
   `pending=N` is a COUNT (not the window), so the tick line stays
   honest regardless of windowing.
6. **The 401/403 latch arm applies only on an all-fail tick.** A tick
   with any success is a success (the credential is demonstrably
   alive); `credential_rejected` forces the streak to the threshold so
   the latch engages on the first dead-credential occurrence of an
   all-fail tick (D3.9's "first occurrence").
7. **`classify_embed_failure` is the ONE classification site**: status
   attr (SDK) or response attr (transport) → the explicit lane-local
   set (`RETRYABLE_STATUS_CODES | {408}`); connection shapes transient;
   bare `APIError` bodies through the house
   `classify_transient_apierror_body`; everything else transient
   (conservative — the 5-cap bounds it).
8. **The `run_in_executor` discipline**: the embed AND the repository
   calls run on the module-level dedicated executor — the module holds
   ZERO `to_thread` call sites (pinned), so the event loop never blocks
   and the shared default executor is never touched (AM-29 extended to
   the DB calls by the same reasoning; the house just spent v0.27.14
   evicting loop-thread sync DB).

## 3. Pack inventory

* `tests/unit/test_memoir_embedding_worker_unit.py` — **35 nodes
  GREEN** (~45 s, SQLite only).
* `tests/unit/test_memoir_embedding_degradation.py` — **6 nodes GREEN**
  (~9 s).
* `test/packs/memoir_embedding_worker_unit_test.sh` — PASS: baseline
  GREEN → five mutation legs (each planted → target RED with the FAILED
  node id captured → restored) → byte-identity check → full suite GREEN:
  * M1 `asyncio.to_thread` substitution → RED:
    `test_embeds_run_on_the_memoir_embed_thread_prefix`
  * M2 write-back failure not counted → RED:
    `test_write_back_failure_counts_as_an_attempt`
  * M3 401 treated as transient → RED:
    `test_401_is_terminal_at_once_and_latches_that_tick`
  * M4 latch threshold 3→1 → RED: `test_latch_slows_the_tick_interval`
  * M5 tick-line M=this-tick → RED:
    `test_tick_line_m_is_the_total_terminal_not_this_tick`
* `test/packs/memoir_embedding_degradation_test.sh` — PASS: baseline
  GREEN → M6 (terminal filter disabled — the re-embed-forever class)
  → RED: `test_the_runbook_requeue_procedure_revives_a_terminal_row`
  → restored GREEN.
* Regression: `tests/test_health_probes.py` 56/56 GREEN (the additive
  `detail.memoir` key breaks no payload pin).

**Honest process note:** the pack's FIRST run had a harness bug —
`restore()` deleted the shared backup after leg M1, so legs M2-M5
"restored" from a vanished file and the mutations ACCUMULATED (the
final restore-proof caught it: 6 RED). The module was repaired by
inverting the four plants with exact-string reverse transforms
(count==1 guards), and the harness was fixed to keep ONE pristine
backup for the whole pack + a byte-identity gate before cleanup. The
passing run above is the fixed harness.

## 4. The exact tick line (as implemented)

```
[MemoirEmbed] pending=N embedded=K errors=E terminal=M tick_ms=T slow_mode=bool
```

Rendered by a single f-string; `bool` is Python `str(bool)` (True/False);
`tick_ms` is an int. Pinned byte-wise by
`test_tick_line_bytes_are_exact` (regex with literal anchors).

Executor thread-prefix proof: `current_executor_thread_name()` returns
`memoir-embed_0`; the suite captures `threading.current_thread().name`
from INSIDE the embed call and asserts the `memoir-embed` prefix. The
"no to_thread" proof is a source pin over the ast-unparsed module
(docstrings stripped — the prose discusses the ban, the code must not
commit it).

## 5. T0.5 — what the readiness surface publishes

`GET /readyz` → `payload["detail"]["memoir"]`:

* `enabled` / `resolution` / `source` (`rejected_value` when
  indeterminate) — the knob resolved with the SAME config tier the boot
  used (`app.state.memoir_config`, stashed by the lifespan);
* `worker` — `phase` (`running` | `parked` | `not_started`), `parked`,
  `latched`, `consecutive_all_fail_ticks`, the last tick line's counters
  (`last_pending/embedded/errors/terminal`), `last_tick_at`, `last_tick_ms`,
  `embedding_model`.

Never a component, never a reason — injected after the verdict and the
status code (structural). Cheap: two in-memory reads, no DB, no
filesystem. A builder failure degrades the BLOCK (`degraded_reason` =
exception class name), never the probe.

## 6. Concurrency protocol

Foreign modified paths observed in `git status` and left untouched:
`daemon/services/context_messages.py`, `daemon/services/instance_messaging.py`,
`daemon/services/memoir_briefing.py` (the phase-5 coder's). Every commit
below is pathspec-limited to this phase's files; no stash, no reset, no
foreign-path staging.

## 7. For waves 4-7

* The worker instance is also stashed on
  `app.state.memoir_embedding_worker` (and registered module-side for
  the readiness snapshot via `latest_snapshot()`); the task is
  `app.state.memoir_embedding_worker_task` (name
  `memoir-embedding-worker`).
* Boot-OFF starts NO task (flag #13's boot clause); a restart is needed
  to enable — the in-process park/resume applies to a RUNNING worker
  only. P7's boot smoke: task census in both knob states.
* The `terminal:` marker + the requeue are the operator contract in
  `docs/runbooks/memoir-embedding.md` — phase 4's tool docs should
  point at the runbook, not restate the SQL.
* The embedder remains reachable ONLY from
  `daemon/services/memoir_embedding_worker.py` (D3.16/DD-20) — phase
  4's purity pin expects exactly that.
* The lifetime cleanup of the module-level executor is daemon-lifetime
  (idle threads; the interpreter owns them at exit) — no shutdown drain
  needed beyond the task cancel+await.
* Knob accessor note for phase 5: the briefing builder should read
  `memoir_knob_state(config)` per assembly (AM-27); the worker's
  per-tick pattern (`_resolve_settings`) is the reference shape.
