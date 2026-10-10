# Test Report: checkpoint-conn-resilience — ROUND 2 (fixed HEAD) — ✅ ALL GATES PASS

Date: 2026-10-10 (round 2)
Commission: round-2 verification on the re-review-APPROVED branch (leader checklist: R1 isolation, H1 harness, degrade-flip outage drill, B2 re-run + K1-shape, full re-run, optional hardening).
Worktree: `/home/nea/ensemble-src-wt-checkpoint-conn-resilience`, branch `feature/checkpoint-conn-resilience`.
Certified HEAD: settled `7ec80b6c3` (dev round-2: 0122db579/0f376f63d/cea8a4c73/97862499e/6a335a5c9 + docs) **+ tester round-2 lane commits `5f8c20b3f` (R1) and `fb72bae84` (H1)** → effective gate HEAD **`fb72bae84`**. All round-2 runs recorded HEAD `fb72bae84` before AND after (zero drift).
Product delta from tester lane: **EMPTY** (`git diff --stat 7ec80b6c3..fb72bae84` = 3 test files only).

## Gate results (leader checklist order)

### 1. R1 — cross-file test isolation: ✅ FIXED + VERIFIED (commit `5f8c20b3f`)
- Repro: combined session order-1 failed exactly as reviewed — `test_get_checkpointer_sqlite_returns_adapter` isinstance False; traceback showed two distinct class objects with identical reprs. Order-2 passed (order-dependent).
- Root cause (deeper than reviewer's framing): `daemon/persistence.py:40` binds `SqliteCheckpointerAdapter` at import; the fixture's "restore" was a **second `importlib.reload`** — reload re-creates every class → epoch-0 (persistence) vs epoch-2 (test) identity split. The langgraph-key evict/restore was already symmetric; the daemon-module restore was not.
- Fix (test-code): `snapshot_module_state`/`restore_module_state` in `tests/helpers/checkpoint_prune_pg.py` — exact-object snapshot before reload, exact-object restore in `finally`. Diff: helper +26, fixture +18−3.
- Verify: combined order-1 **67P**, order-2 **67P**, resilience alone **43P**, persistence alone **24P** (4/4 green). Re-covered implicitly by A1's single-session 4-file run (below).

### 2. H1 — PG probe harness: ✅ FIXED + VERIFIED LIVE (commit `fb72bae84`)
- Repro on live throwaway PG: both `test_pg_checkpoint_saver_probe_*` SKIPPED with the mislabeled "PostgreSQL not available" (real cause: `asyncio.run()` inside running loop).
- Fix (test-code): async-safe twin `require_postgres_async()` (identical loud-skip contract; all 6 sync callers untouched) + the missing module-level autouse `_real_langgraph` eviction fixture (surfaced once the guard stopped lying — real `langgraph.checkpoint.postgres.aio` unresolvable under the `__path__=[]` conftest mock). Diff: helper +44−8, test +33.
- Verify: `tests/postgres/test_readiness_pg.py` → **9 passed / 0 skipped**; both gate tests RUN + PASS by name. Sync-caller regression (`test_direct_anti_join.py`) 11P.

### 3. Degrade-flip OUTAGE drill (closes D3 + incident blind-spot): ✅ OUTAGE-DRILL-PASS
Live daemon from worktree (PID 2900212, port 8381, throwaway PG16 :15433, boot → livez in 15.2s, pool-backed saver log line verbatim, initial readyz 200 all-true):
- **OUTAGE** (`pg_ctl stop -m fast`): /readyz → **HTTP 503, `checkpoint_saver:false` at t+6.4s** with THREE structured reasons (`checkpoint_saver: sentinel checkpoint read (aget_tuple) timed out after 1.0s`; `database: SELECT 1 probe failed or timed out`; `checkpoint_saver: AsyncConnectionPool.check() failed or timed out`); **/livez stayed 200 on every poll; process alive throughout (no restart)** — degrade-not-restart proven live.
- **RECOVERY** (`pg_ctl start`, same datadir): database component true at t+6.5s; `checkpoint_saver` true + readyz 200 at **t+49.7s** (within 120s bound).
- Operator note (non-gating): recovery asymmetry — checkpoint_saver lags database by ~43s (AsyncConnectionPool reconnect backoff between probe ticks). Suggested future polish: shorter backoff or a re-validated-within-Ns timestamp so operators can distinguish recovering vs real outage.

### 4. B2 re-run at fixed HEAD + K1-shape: ✅ REAL-RECOVERY-PROVEN (3/3) + K1-SHAPE-PASS
Script `/tmp/cp-r2-b2/recovery_probe_b2.py` (retained for audit; 6 run logs), throwaway PG16 :15434, real `create_postgres_checkpointer`:
- Kill 3/3 own saver conns server-side → post-kill `aget_tuple` **918/1045/1099 ms**, post-kill `aput` ~7 ms, verify-head + readiness probe PASS — **zero restarts, 3/3 settled runs** (runs 1–3 exposed a test-script lexicographic-id bug — ASCII digits < letters made `'9'*32` lex-smaller than `'f'*32; fixed to `'z'*32;` test-design only, NOT a product defect).
- Pool stats: `connections_lost: 2` detected + replaced, pool self-replenished (identical shape all runs).
- **K1-shape (a)**: `proxy.get_next_version('…0001', None)` → bumped str version (`'…0002.<rand>'`) on all 3 runs — the K1 forwarder (`daemon/checkpoint_adapter.py:337-338`) live on the RECOVERED proxy, not `NotImplementedError`.
- **K1-shape (b)**: full resumed-thread round-trip — v1 stored → read back → `get_next_version` bump → v2 stored on SAME thread with bumped `channel_versions` + `versions_seen` → re-read matches. PASS ×3.
- Safety: env-`u` fence proved load-bearing (ambient `POSTGRES_HOST=10.44.0.2` present in parent shell); zero prod contact; cleanup zero-residue (0 listeners 15434).

### 5. Full re-run @ `fb72bae84` (all PASS, zero skips where required):
| Pack | Result | Baseline | Notes |
|---|---|---|---|
| A1 targeted (4 files, `-rs`) | **143 passed / 0 failed / 0 skipped** (14.5s) | ~143P/0S expected | both formerly-skipping real-langgraph tests RUN; `-rs` emitted nothing; HEAD stable |
| A2 integration ×2 + helper import | **10P / 0F / 0S** (10.3s) | 10P | import OK incl. wave-1 snapshot/restore helpers |
| Core `concurrency_atomic_unit_test` | **99P / 0F / 74S** (68.1s) | 99P/0F/74S | exact parity |
| PG readiness (H1-fixed) | **9P / 0F / 0S** (8.2s) | 9P/0S | both gate tests RUN + PASS |

### 6. Optional hardening: ⚠️ DEFERRED (worker `8aff7b13` ERRORED at 13:04Z — transient provider 502; per leader: no re-spawn)
All five optional items (K-guard mutation automation, sentinel re-init pin, classmethod/staticmethod K-guard, with_allowlist routing, pool duck-type passthrough) remain unpinned as automated tests. Non-gating: the manual mutation repro is review-verified, and A1's 143P includes the dev's own K-guard/sentinel tests. Carry as future test-lane debt.

## Test-lane commits (this round)
- `5f8c20b3f` — `test:` R1 exact-object module snapshot/restore (fixture isolation)
- `fb72bae84` — `test:` H1 async-safe PG availability guard + autouse `_real_langgraph` eviction
(Round-1 lane: `97c7f7bdc`, `0ea77bfdf` — already on-branch.)
Product-code diff across all tester lanes: **EMPTY**.

## Incident-acceptance cross-check (from `.agents/shared/incident-20261010-checkpoint-connection-pool.md`)
1. Pool-backed saver w/ check+reconnect ✅ (construction tests + live boot log + B2 topology)
2. Readiness exercises the checkpoint path ✅ (sentinel aget_tuple probe — reason line proves it fires; outage drill flips it)
3. Recovery without restart ✅ (B2 ×2 rounds, 6/6 runs total; outage drill process-alive proof)

## ORIGINAL SYMPTOM CLOSURE — **PROVEN-FIXED**
`psycopg.OperationalError: the connection is closed → instance terminal after PG restart, /readyz blind`:
- **Connection layer**: server-side kill of every checkpoint connection recovers in ~0.9–1.1 s with zero restarts through the real production path — proven at rejected HEAD (round 1, 3/3) AND fixed HEAD (round 2, 3/3), pool self-replenish stats in evidence.
- **Instance-terminal path**: retry-once proxy re-drives connection-class failures only (unit-pinned); K1/K2 deploy blockers fixed by explicit forwarders — runtime-refuted at `0122db579`, re-pinned at settled HEAD on the recovered proxy (K1-shape ×3) incl. full resumed-thread str-version round-trip (the exact pregel resume shape that would have crashed pre-fix).
- **Readyz blindness**: outage drill proves `/readyz` now degrades to 503 + `checkpoint_saver:false` within 6.4 s of a full PG outage (three greppable reasons) while `/livez` stays 200 and the process survives — then recovers to 200/true in 49.7 s. The exact incident signature is now both survivable AND visible.
- Residuals (non-blocking): recovery asymmetry (operator UX), optional guard-test automation (deferred, worker errored).

## Workers used (round 2)
95868eb2 (fixes) · 8d1756a0 (A1) · 263be5a6 (A2) · 58379c87 (core) · 7525fc44 (PG pack) · bb6af516 (B2 — revived once after transient provider 5xx, then delivered 3/3) · ef93e0ed (outage drill) · 8aff7b13 (optional — ERRORED, deferred)
