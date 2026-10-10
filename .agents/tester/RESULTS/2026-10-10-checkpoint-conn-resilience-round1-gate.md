# Test Report: checkpoint-conn-resilience — ROUND 1 (pre-fix HEAD)

Date: 2026-10-10
Commission: TEST & VERIFY the checkpoint-connection-resilience fix (incident 2026-10-10).
Worktree: `/home/nea/ensemble-src-wt-checkpoint-conn-resilience`, branch `feature/checkpoint-conn-resilience`.
Incident doc: `.agents/shared/incident-20261010-checkpoint-connection-pool.md` (148 lines, committed on the branch).

## Certified range
- Commissioned tip: `f19803dc3` (base `0c040e5f8`). Production delta (12 files, +2099/−95): `daemon/{api,checkpoint_adapter,persistence}.py`, `daemon/services/readiness.py`, tests (5 files), `.agents/shared/` incident doc.
- Test-lane commits added during gate (authorized quick fixes, test-code only): `97c7f7bdc` (helper `LANGGRAPH_MOCK_KEYS` += `langgraph.checkpoint.base`), `0ea77bfdf` (caplog logger `daemon.services.checkpoint_prune` at 2 dry-run sites). Effective gate HEAD for A2/A3/C: **`0ea77bfdf`**.
- Venv verified: `daemon` resolves INSIDE the worktree; pins langgraph 1.0.9 / langgraph-checkpoint-postgres 3.1.0 / psycopg 3.3.4 / psycopg_pool 3.3.1 (unchanged by the fix — confirmed).

## ROUND-1 VERDICT: ❌ NOT READY (independent review REJECTED the HEAD mid-gate)
Mid-flight, the leader relayed an independent code-review rejection with 3 criticals (K1 `get_next_version` MRO stub → `NotImplementedError` on resumed threads; K2 `alist` inert passthrough → `aget_state_history` broken; K3 conftest langgraph mock inverts proxy semantics). Round 1 was wrapped up per redirect; a developer fix round is landing in the worktree; **round 2 will be commissioned on the fixed HEAD**. This file records what round 1 established — including two ADDITIONAL tester-found defects (H1, and the degrade-flip attack-vector gap) that round 2 must cover.

## Scope Decision
Leader's 3-phase plan (A/B/C) + ensure.md always-on Core. `tests/postgres/` reduced to `tests/postgres/test_readiness_pg.py` (only file in that dir touched by the diff; other 37 files = base state). Release-gate E2E (real-LLM packs) excluded — bounded persistence fix, leader's phase plan governs; revisit at promote gate.

## Phase results

### Recon (worker a952d961, no skill) — PASS
HEAD/branch/clean-tree verified; venv + pins confirmed; new-test inventory with node IDs; **ambient shell `POSTGRES_*` resolves to PROD (10.44.0.2/ensemble_prod) → mandatory `env -u` fence applied on every pack**; PG server binaries at `/usr/lib/postgresql/16/bin` (not on PATH); dev PG exists at 127.0.0.1:5432 (`ensemble_dev`, non-prod, untouched); `tests/postgres` consumes **`PG_TEST_*`** namespace (not `POSTGRES_*`).

### Phase A1 — targeted delivered tests: ✅ PASS (worker 910da1cd, test-pack-execution)
`tests/{test_persistence,test_checkpoint_adapter_resilience,test_readiness_checkpoint_saver,test_health_probes}.py` → **126 passed / 2 skipped / 0 failed** — exact delivered baseline. Runtime 13.3s (pytest). Both skips are the delivered-baseline pair (`langgraph.types` absent in langgraph 1.0.9; real-`langgraph.graph` required) — see Coverage Gaps.

### Phase B1 (folded into A1) — failure-class node-ID evidence
11/12 fix-specific node IDs PASSED, incl. retry-once semantics (`test_retries_once_on_operational_closed_then_succeeds`, `test_retries_once_on_sqlstate_08006`), no-retry-on-non-connection-error, second-failure-reraises, `alist` passthrough, close-topology (pool + single-conn), pool-construction (`test_creates_adapter_with_pool_backed_saver`), probe-degrade (`test_failing_probe_degrades`, `test_hanging_probe_times_out_and_degrades`).
⚠️ 1/12 SKIPPED (env): `TestSaverRetryProxyABCRegistration::test_stategraph_compile_accepts_proxy_as_checkpointer` — **mock-lane caveat: these semantics are certified against the conftest langgraph mock** (review finding K3 makes this material; see round-2 plan).

### Phase A2 — checkpoint integration + helpers: ✅ PASS after 2 quick fixes (worker 8086b7cd, test-pack-execution)
`tests/integration/checkpoint_prune_real_saver.py` + `checkpoint_prune_restore_rehearsal.py` → final **10 passed / 0 failed / 0 skipped** (10.75s). Helper import + mock-eviction round-trip proven.
Quick fixes (test-code only, committed): `97c7f7bdc`, `0ea77bfdf` (details above). Both root causes were branch-introduced test-harness breakage.
Notes: (a) neither integration file carries the `integration` marker — default suite partition does NOT exclude them (test-architecture flag); (b) prod fence proven live (ambient 10.44.0.2 stripped; helper uses `PG_TEST_*` → localhost dev PG).

### ensure.md Core: ✅ PASS (worker b6a354db + recon statics)
`concurrency_atomic_unit_test` → **99 passed / 0 failed / 74 skipped** (68.4s; baseline 98P/0F/74S — +1 pass informational, skip count stable, 0 regressions). Statics (recon): `dev.sh:142` `--timeout-graceful-shutdown 10` ✓; all `_get_system_prompt_tokens`/`_compute_context_usage`/`get_queue_stats` call sites awaited ✓; `psycopg_pool.AsyncConnectionPool` wired at `persistence.py:273` with `check=check_connection` (:278) ✓; `make_checkpoint_saver_probe` at `readiness.py:535` ✓.

### Phase B2 — REAL PG recovery (the incident's exact repro): ✅ REAL-RECOVERY-PROVEN (worker 2365b524, integration-test)
Throwaway PG 16.15 cluster (127.0.0.1:15432, `ensemble_b2_test`, trust auth). Script `/tmp/cp-resilience-b2/recovery_probe.py` (log alongside) using the REAL `create_postgres_checkpointer`:
- topology: raw_saver = `_SaverRetryProxy`, saver.conn = `psycopg_pool AsyncConnectionPool` ✓
- pre-kill aput/aget_tuple PASS (4.2/5.6 ms)
- `pg_terminate_backend` on **3/3 own connections** (the incident's server-side kill)
- **post-kill `aget_tuple` PASS ~992 ms, post-kill `aput` PASS 5.6 ms — NO daemon restart** (3/3 runs)
- pool stats post-recovery: `connections_lost: 2` detected + replaced ✓
- `make_checkpoint_saver_probe` → True after recovery (4.3 ms) ✓
- cleanup: cluster stopped, pgdata removed, 0 listeners, 0 residual sessions ✓
Throwaway-cluster RECIPE recorded (worker report; `/usr/lib/postgresql/16/bin`, port 15432, sock dir `/tmp/...` required).

### Phase A3 — PG-side readiness pack: ⚠️ PASS-but-GATE-NOT-SATISFIED (worker 3b7a9001, test-pack-execution)
`tests/postgres/test_readiness_pg.py` (scoped; `PG_TEST_*` → throwaway cluster) → **7 passed / 2 skipped / 0 failed** (6.9s). **Both fix-specific tests SKIPPED — deterministically, branch-caused (H1)**: async fixture `pool_backed_checkpointer` (test_readiness_pg.py:272) calls the SYNC `require_postgres()` guard (asyncio.run inside) from pytest-asyncio's running loop → `RuntimeError: asyncio.run() cannot be called from a running event loop` → helper's bare except mislabels it "PostgreSQL not available" → pytest.skip. Evidence: live `SELECT 1` OK on the same cluster; 7 siblings PASSED; isolated repro of both halves; skip is 100% deterministic. The tests' own skip text says "do NOT merge … on a skip". **The PG-side probe coverage for this fix NEVER EXECUTED in round 1.**

### Phase C — live-boot smoke: ⚠️ SMOKE-PASS-PARTIAL (worker f16e2790, e2e)
Boot HEAD `0ea77bfdf`, unchanged through the window (concurrent fix did not land mid-smoke; attribution clean).
- daemon boots (banner `Starting Ensemble v0.18.5`), T+12s to /livez 200, port 8381, throwaway CWD `/tmp/cp-resilience-c` ✓
- **pool-backed saver on the PG path** (log line verbatim): `daemon.persistence - INFO - PostgreSQL checkpointer adapter ready (saver=AsyncPostgresSaver(pool=AsyncConnectionPool), adapter_pool=asyncpg.Pool)` ✓ (no sqlite fallback)
- `/readyz` 200: `"checkpoint_saver": true` (+ database/queue_freshness/services) ✓
- **Degrade flip NOT OBSERVED**: two attack rounds (single kill-burst of 11 conns; sustained 0.3s-interval killing ~75s) — `checkpoint_saver` stayed true throughout while logs showed `psycopg.pool - WARNING - discarding broken connection` + an `AdminShutdown` hit. Root cause: the pool self-heals conn churn faster than the 10s readiness refresh tick; `make_checkpoint_saver_probe` fails closed only on STRUCTURAL failure (timeout/exception of `pool.check()`), which conn-churn never produces. **Attack-vector mismatch, not a wiring defect** (wiring proven by log line + B2 probe pass). Round 2 must use an outage-class attack (stop the throwaway PG > 2× refresh interval) to observe `checkpoint_saver:false` + HTTP 503 with /livez still 200, then recovery.
- shutdown/cleanup clean (daemon TERM'd by recorded PID, ports freed, cluster stopped, dirs removed). Runtime 5m34s (config.yaml retry + sustained window; noted).
- Process-safety honored: live dev/demo processes observed but untouched; port 8088 never involved.

### K1/K2 runtime confirmation probe: ✅ CONFIRMED pre-fix / REFUTED post-fix (worker 79312f8b)
Authoritative run = **pinned-source@f19803dc3** (verified byte-identical to committed HEAD `0ea77bfdf` for `daemon/`; the live tree was transiently dirty mid-probe from the concurrently-landing fix round, so that run was superseded). Plain interpreter — REAL langgraph loaded from site-packages (no pytest, no conftest mock).
- **K1 CONFIRMED**: `proxy.get_next_version('…0001', None)` → **NotImplementedError**, raise site = real base stub `langgraph/checkpoint/base/__init__.py:707`. Mechanism proven: `getattr_static` resolves to `BaseCheckpointSaver.__dict__['get_next_version']`, absent from the proxy class `__dict__` → `__getattr__` never fires. Control: wrapped saver's own `get_next_version` works (`'…0002'`) — unreachable through the proxy. Deployment blocker confirmed: resumed pre-fix threads crash on first message.
- **K2 CONFIRMED (BASE-RESOLVED, inert)**: `proxy.alist` binds `BaseCheckpointSaver.alist`; call-level `async for` → NotImplementedError via base stub chain; wrapped saver's `alist` (MarkerError-armed, control-proven) never reached → `aget_state_history` broken pre-fix.
- **Post-fix cross-check @ `0122db579`** (clean `daemon/` tree, attributable — "explicit forwarders for every concrete BaseCheckpointSaver member"): **K1-REFUTED** (returns `'…0002'` via explicit proxy forwarder) and **K2-DELEGATES** (`alist` reaches the wrapped saver — MarkerError fired). Proxy class dict now carries 25 explicit non-dunder members.
- Probe adaptation (noted): `_SaverRetryProxy` is factory-nested inside `_wrap_saver_with_connection_retry` — probe constructs via the production factory (same class object production creates). Version note: base package `langgraph-checkpoint` is **4.1.1** in the venv (distinct from `langgraph-checkpoint-postgres` 3.1.0; pins unchanged by the fix).
- Artifacts: `/tmp/k1-probe/{k1_probe_pinned.py,cpa_pinned.py,run-pinned.log,run2-live-dirty.log}`; pinned source sha256 `2b79628a…51b1`.

## Findings ledger (round 1)
| ID | Class | Finding | Disposition |
|----|-------|---------|-------------|
| K1 | review-critical | `_SaverRetryProxy.get_next_version` MRO → base stub → `NotImplementedError` for str versions; resumed pre-fix threads crash post-deploy (pregel ×8 sites) | Runtime CONFIRMED pre-fix (probe, pinned f19803dc3); fix commit `0122db579` runtime-refutes; round-2 regression test mandatory |
| K2 | review-critical | `alist` `__getattr__` passthrough inert (base-concrete method) → `aget_state_history` broken | Runtime CONFIRMED pre-fix (BASE-RESOLVED + call-level stub chain); `0122db579` DELEGATES (MarkerError proven); round-2 regression test mandatory |
| K3 | review-critical | conftest langgraph mock inverts proxy semantics — A1/B1 mock-lane certificates certify mock-world MRO | Review finding; round-2 must add real-langgraph lane |
| H1 | tester-found (harness) | async fixture × sync `require_postgres()` guard → deterministic skip of BOTH fix-specific PG tests (false-green) | Round-2 must fix harness + make the tests RUN |
| D1 | tester-found (coverage gap) | `test_stategraph_compile_accepts_proxy_as_checkpointer` env-skipped (langgraph 1.0.9 lacks `langgraph.types`) — proxy ABC acceptance unproven at runtime | Round-2: real-langgraph lane / pin decision |
| D2 | tester-found (test-arch) | integration files carry no `integration` marker → default suite partition does not exclude them | Flag to lane owner; not this gate's fix |
| D3 | tester-found (coverage gap) | degrade-flip (`checkpoint_saver:false` + 503) unproven live — conn-churn attacks can't produce structural failure within refresh tick | Round-2: outage-class attack |

## ensure.md Validation Results (round 1)
- Critical: changed-packs-no-regression — A1 ✓ A2 ✓ core ✓; A3 fix-specific portion SKIPPED (H1) ⚠️; overall round gated by review rejection, not by these packs.
- Critical: `concurrency_atomic_unit_test` ✓ PASS. Critical: no-sync-DB-on-loop ✓ (same pack). Critical: `dev.sh` graceful-shutdown flag ✓ (static).
- Important: await-callers ✓ (all sites awaited). Nice-to-have: no dead code ✓ (imports/wiring present).

## ORIGINAL SYMPTOM CLOSURE (round-1 statement)
`psycopg.OperationalError: the connection is closed → instance terminal after PG restart` — **PARTIALLY PROVEN FIXED at the connection layer, round incomplete overall**:
- PROVEN (strong): real server-side kill of every checkpoint connection recovers in ~1 s with zero restarts through the real production code path (B2, 3/3 runs); pool detects + replaces dead conns; readiness probe passes post-recovery; live daemon boots pool-backed with `checkpoint_saver:true` (C).
- NOT PROVEN: (a) resumed-thread safety — K1 (`get_next_version` stub) means pre-fix threads RESUMED after deploy would crash on first message — the fix as reviewed is NOT deployable; (b) the readiness degrade path (`checkpoint_saver:false`) never observed live (D3); (c) PG-side probe tests never executed (H1).
- Final verdict deferred to round 2 on the fixed HEAD.

## Round-2 test plan (recommended, mandatory items marked)
1. **[M] K1 regression**: real-langgraph (no conftest mock) test — `proxy.get_next_version` for str AND int versions; resumed-thread shape end-to-end (pregel resume path).
2. **[M] K2 regression**: `proxy.alist` delegates to wrapped saver (aget_state_history path) — real langgraph.
3. **[M] K3 lane fix**: conftest mock must not be the only certification lane for proxy MRO semantics; add a real-langgraph lane; re-enable/un-skip `test_stategraph_compile_accepts_proxy_as_checkpointer` (D1 — needs `langgraph.types`, absent in 1.0.9; pin decision or shim).
4. **[M] H1 harness fix**: call the PG availability guard BEFORE the fixture goes async (or add an async-safe probe variant); the two `test_pg_checkpoint_saver_probe_*` tests must RUN (not skip) against a live throwaway PG.
5. **[M] Degrade-flip live test**: outage-class attack — `pg_ctl stop` the throwaway cluster for > 2× refresh interval (10s) → expect `/readyz` `checkpoint_saver:false` + HTTP 503 while `/livez` stays 200; restart cluster → expect recovery flip to true (bounded wait). This closes the incident's blind-spot end-to-end.
6. **[M] Re-run B2 real-recovery repro** at fixed HEAD (pool recovery must stay green after K1/K2 fixes).
7. Full A1/A2/core-ensure re-run at fixed HEAD (cheap: <2 min combined).

## Documentation
- PACKS.md: round-1 ad-hoc pack registrations appended (see gate header).
- LESSONS/2026-10-10-cp-resilience-round1-lessons.md: mock-lane inversion, false-green skip family, attack-vector mismatch.
- Quarantine: none added (A1's 2 skips are the delivered baseline's own env-conditional skips, documented above).

## Workers used
a952d961 (recon) · 910da1cd (A1/B1) · 8086b7cd (A2) · b6a354db (core-ensure) · 2365b524 (B2) · 3b7a9001 (A3) · f16e2790 (C) · 79312f8b (K1/K2 probe, pending at draft time)
