# Round-2 Verification — MERGED TREE + STAGED ARTIFACT v0.25.3 (boot-orphan-sweep × qpk-rehydrate composition)

- **Date**: 2026-10-19
- **Verdict**: 🟢 **PASS-WITH-NOTES — all 4 commissioned items green; zero failures attributable to our fix, qpk's change, or the merge interaction**
- **Merged HEAD**: `ac1e6454bad68abb3a661d9c8c13598365293e9b` (= a2252c6f1 + fix merge `2098be075` [--no-ff, zero textual conflicts] + bump v0.25.3), branch `latest`
- **Staged**: `/home/nea/agents-ensemble/releases/v0.25.3`, binary_sha256 `a2edc31ebcdbdbb1aa38335195188577caa6289e3cb356fe78f3603edf4337bb`, NO PROMOTE (`current → releases/v0.25.1` unchanged, live daemon PID 131667 untouched)
- **Workers**: 4 (`4465e0a6` corpus-reuse · `78198b0c` sim-reuse · `796f0e06` audit · `45abe0f6` staged-integrity). 0 re-dispatches, 0 timeouts, no `TESTER_CANT_OPTIMIZE_TEST_PACK`. Main checkout READ-ONLY (no commits; porcelain byte-identical across every lane).
- **Round-1 record**: RESULTS/2026-10-19-boot-orphan-sweep-functional-verification.md (branch tip 12f298ba2, PASS-WITH-NOTES)

---

## Item 1 — Corpus on merged HEAD — ✅ PASS (6 pre-existing test_api.py failures quarantined, zero merge-attributable)

Per-file (one `timeout 300` each, fence asserted):

| file | P/F | runtime | note |
|---|---|---|---|
| tests/unit/services/test_boot_orphan_sweep.py | 32/0 | 39.9s | holds vs R1 |
| tests/unit/services/test_auto_continue_boot_pass.py | 23/0 | 4.9s | holds |
| tests/unit/services/test_auto_continue_interleaving.py | 14/0 | 11.0s | holds |
| tests/unit/repositories/test_auto_continue_candidates.py | 26/0 | 17.9s | holds |
| tests/test_api.py | 41/**6** | 11.3s | all 6 PRE-EXISTING (below) |
| tests/test_question_pack_boot_rehydrate.py | 7/0 | 16.5s | qpk suite green |

**The 6 `tests/test_api.py` failures are provably pre-existing on latest** (byte-archaeology @ base a2252c6f1: `git log a2252c6f1..ac1e6454b` EMPTY for routers/instances.py + routers/messages.py + manager.py; failing seams byte-identical at base; only in-range test change = version bump 0.25.2→0.25.3, which passes). 4× stale mock expectations (missing `source=None` the router passes at `daemon/routers/instances.py:468`), 2× plain-Mock awaited where AsyncMock needed (`daemon/routers/messages.py:326`). **Registered in QUARANTINE.md (2026-10-19 row); test-debt ticket surfaced — will redden any future gate including that file until fixed.** Base leg not executed (read-only mandate) — attribution by byte-identity, disclosed.

## Item 2 — Lifespan ordering audit — ✅ VERDICT: ORDER-SAFE

Merged order inside `lifespan` (api.py:450), all sequential awaits, zero interleaving:

| step | merged site | (pre-merge) |
|---|---|---|
| `await manager.initialize()` | api.py:**663** | — |
| `capture_boot_epoch(manager.engine)` | api.py:**676** | — |
| **(i) `manager.setup_worker_pool()`** (wires `_task_repo`, pool_orchestrator.py:245) | api.py:**703** | — |
| **(ii) `await _boot_rehydrate_question_packs(manager)`** (helper api.py:338 → manager.py:4563) | api.py:**725** | qpk's +115 |
| …doomed-ids :776, maintenance, resource-monitor :2091, readiness :2291… | — | — |
| **(iii) AutoContinue `continue_running_instances_after_restart`** | api.py:**2481** | was :2366 |
| **(iii) `sweep_orphaned_live_turns`** | api.py:**2486** | was :2371 |

- **(a) Premature resume? NO** — sweep asserts its key dependency loudly (`auto_continue_boot_pass.py:702-705`: `_task_repo` wired); every resume-lane consumer (boot_epoch :676, `_task_repo` via :703, checkpointer via initialize :663, `_graph_tasks`/repos via `__init__`) is wired strictly earlier; the worker pool itself is not a resume dependency (resume = direct background astream bypassing the task-processor lane) — and it is up anyway.
- **(b) qpk ↔ AutoContinue envelope: orthogonal** — qpk consumes `_task_repo.find_suspended_turn_for_answer` + instance metadata, writes only `QuestionManager._packs` (RAM); never reads boot_epoch. The sweep selector (`repository.py:1175-1178`) requires `status='running'` AND **zero** task rows in (pending,running,paused) — a paused/awaiting_answer row (qpk's entire candidate set) categorically excludes the instance. Only shared object = task repo/engine, read-only in both.
- **(c) Order (ii)→(iii) correct in BOTH directions** — disjoint predicates, disjoint write surfaces, sequential awaits. Neither pass reads anything the other writes.
- Cosmetic residual (not a bug): `auto_continue_boot_pass.py` docstrings cite pre-merge `api.py:2361/:2365` (now :2476/:2481) — doc-only drift, one-line fix on the next docs vehicle.
- Accepted-design note: resumed turns become live asyncio tasks interleaving with the remainder of lifespan startup — identical risk profile to the pre-existing AutoContinue pass at the same site (StaleTaskRecovery backstop at boot+10min).

## Item 3 — Restart-simulation on merged HEAD — ✅ PASS 9/9, ZERO behavioral deltas vs R1

Harness rebuilt from committed RESULTS/LESSONS (worktree was removed); 2 packs, 41.8s + 41.5s wall under `timeout 280`/`alarm(240)`; rows `orphansim2-%`; real PG ensemble_dev (double fence + the NEW main-checkout gotchas handled — see LESSONS §5). Same 3 stubs as R1 (astream + faithful checkpoint write, PoolOrchestrator.setup partial, mcp_pool off); everything else real.

| scenario | verdict | evidence |
|---|---|---|
| (a) orphan rescue | ✅ | `candidates=1 scheduled=1`, astream 1× `orphansim2-a-e983b64c`, ckpt 16:30:49→16:41:04 (≥ epoch), JobItem active→done, instance running→completed |
| (b) current-gen safety | ✅ | `scheduled=0`, astream 0, per-instance `SKIPPED … >= boot_epoch … natural immunity` line |
| (c1) idempotency | ✅ | re-sweep `scheduled=0`, astream total still 1 |
| (c2) multi-restart | ✅ | later epoch + new orphan → `scheduled=1`, astream 1× |
| (d) ×5 | ✅ | COMPLETED/CANCELLED/idle/WAITING_CHILDREN not swept; **FAILED IS swept** (1× astream) |
| (e) observability | ✅ | summary line every run incl. `candidates=0` |

**Composition proof (beyond the letter)**: the merged qpk boot step was driven FOR REAL, in merged order, before the sweep — `INFO daemon: question-packs rehydrated: 0` (genuine no-op on dev DB), sweep behavior identical after. Sweep/repo code byte-identical R1↔R2 (merge touched only api.py/manager.py/__init__); summary-line format byte-identical. Verbatim lines in the worker report; representative:
```
2026-10-19 16:40:49,481 INFO daemon.services.auto_continue_boot_pass: [BOOT_CONTINUE] orphan-sweep summary: candidates=1 scheduled=1 skipped_no_checkpoint=0 skipped_resume_refused=0 skipped_kill_switch=0 skipped_no_boot_epoch=0 errors=0 duration_s=0.066 boot_epoch=2026-10-19T16:40:49.390301
```
Cleanup: fresh-process whole-table scan → 0 leftover `orphansim2-` rows; histograms pre=post; scratch removed; porcelain byte-identical (2 gitignored .pyc + 3 gitignored log lines disclosed).

## Item 4 — Staged-artifact light integrity — ✅ PASS 6/6 (19/19 assertions)

1. Manifest: `binary_version 0.25.3`, sha `a2edc31e…`, fe_index `a017dd21…`, `rollback_safe true`, `known_schema_gen 20261018_090004…`; mtime == staged_at to the second. git_head attested via build sidecars (verbatim: `git_head ac1e6454bad68abb…, git_dirty false, artifact_sha256 a2edc31e…`) — the v0.25.2 attestation-gap class did NOT recur.
2. Binary sha256 triple-match (recomputed == manifest == expected); config/launcher also recomputed ✓.
3. FE index recomputed `a017dd21…` (4th consecutive daemon-only byte-identical); ALL 3 tree hashes fully recomputed (agents `4c1a89a5…`, frontend `8d6afeaa…`, plugins `4715cf64…`) + 7 spot checks, 0 mismatches.
4. Migration delta EMPTY: 104==104 (git-objects method — staged payloads are source-less by design; disclosed), head gen == known_schema_gen ⇒ rollback_safe consistent.
5. NO-PROMOTE: `current → releases/v0.25.1`; `state.json` `in_flight: null`, `pending_op: null`; zero v0.25.3 promote journal events; live daemon PID 131667 observed only.
6. Scrubbed `--version` (`env -i` shape from v0.25.2 precedent): stdout `Ensemble v0.25.3`, rc=0, immediate exit, no boot/port/DB. Bonus: **binary-only delta vs v0.25.2** (all trees + config + launcher identical).

## ensure.md (unchanged scope, R2)

Core criticals remain 4/4 (R1 evidence carries; R2 corpus re-confirms #1 on merged HEAD; concurrency pack ran green on the branch tip and the merged tree's daemon/ is unchanged in the concurrency-tested paths — concurrency pack NOT re-run in R2 per light-merge scope; note for the record). No contradictions, no improvement notices.

## Notes & residuals

1. `tests/test_api.py` ×6 pre-existing (QUARANTINE.md) — test-debt commission recommended (4× `source=None`, 2× AsyncMock).
2. Stale docstring line refs in `auto_continue_boot_pass.py` (cosmetic, doc-only).
3. R1 residuals restated: no real model turn; `watcher` agent; W-B (MAX(Task.id) auxiliary contamination) and the busy-task/resume-refused branches of W-A not exercised; HTTP boot call site not executed (in-process seam; ordering pinned statically by the audit + qpk's ordering-contract tests).
4. Ledger files written UNCOMMITTED per R2's read-only/no-commit mandate (QUARANTINE row, LESSONS §5 addendum, this file, PACKS block).
