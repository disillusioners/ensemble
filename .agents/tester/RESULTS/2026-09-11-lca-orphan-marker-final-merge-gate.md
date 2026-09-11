# LCA Orphan+Marker Stack — FINAL MERGE GATE

**Date:** 2026-09-11
**Gate:** `feature/leader-completion-attestation`, delta `latest..975cdf19` (latest = `7d5285aa`), exactly 3 commits: `d950d2c8` (idle-orphan two-set live semantics), `06ddad57` (marker scan + judge-on-allow + informational hint), `975cdf19` (dry-mode marker logging + stable hint supersede id — review W1+W2).
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-lca-idle-orphan` — verified PRISTINE at tip `975cdf19` before any run (inventory child `3e14a6ad`).
**Mode:** 12 workers (1 inventory + 6 matrix packs + PG + A/B + boot + ensure) Wave A, 2 authoring workers Wave B. All runs `uv run python -m pytest`, drift-pinned per invocation, dual-layer timeouts, no `-x`. Zero production-code changes.

## VERDICT: ✅ PASS FOR MERGE (GO) — all 7 jobs green, 0 introduced regressions

---

## Job 1 — Full attestation matrix @ 975cdf19 (clean tip)

**598/598 PASS** (43 files; exact match to developer ground truth 598/598). 6 ad-hoc packs, parallel:

| Pack | Files | Tests | Result | Runtime |
|---|---|---|---|---|
| P1 | judge_resolver, gate, resolver (unit) | 126 | PASS | 0.31s |
| P2 | marker_scanner, report_judge, prompt_contract, conditional_scanner (unit) | 137 | PASS | 0.25s |
| P3 | judge_wiring, registration, scanner, marker_wiring, ledger (unit) | 119 | PASS | 3.87s |
| P4 | 8 trailing unit files | 62 | PASS | 0.54s |
| P5 | live_descendants, runbook_drift, must_not_break, migration (integration) | 93 | PASS | 1.94s |
| P6 | 19 remaining integration files | 61 | PASS | 3.63s |

Zero failures, zero errors, zero skips, zero quick-fixes. All packs drift-pinned `975cdf19` pre+post.
*Note:* a "31 ahead / 3 behind" reading observed by P5 is upstream-tracking divergence (`git status -sb` vs origin), NOT the gate delta — authoritative `git log latest..975cdf19` = exactly 3 commits (verified twice).

## Job 2 — Incident E2E b08f40fe (THE acceptance): ✅ ACCEPTANCE PASS

Coverage audit of existing `test_attestation_idle_orphan_incident.py` (3/3 PASS): 9/12 acceptance assertions pre-covered; 3 gaps found (verbatim incident phrase; row-status ≠ COMPLETED no-silent-completion read; `completion_gate_escalated` reset) → closed in NEW file.

**NEW** `tests/integration/test_attestation_incident_acceptance_lca.py` (2 tests, 2/2 PASS, 0.67s), commit `77a81ac5`:
- **Phase 1 (no attest):** verbatim phrase `"Awaiting final four: C12a/b/c + blame-worker. Then I aggregate and write RESULTS. Ending turn."` in final leader AIMessage; 4 spawned-never-dispatched IDLE grandchildren; decision=**DENIED**; nudge injected; `attestation_denied_count` 0→1; branch-5 `allowed_legitimate_pending_wakeup` NOT fired; leader row ≠ COMPLETED (silent-completion class DEAD); `live_descendants=0`.
- **Phase 2 (recovery):** orphans terminated + `attest_completion` → decision=**ALLOWED**; counter reset to 0; `completion_gate_escalated` (pre-seeded True) cleared.

## Job 3 — Marker-routing E2E: ✅ PASS (all scenarios)

Existing 38 (marker_scanner) + 23 (marker_wiring) green. Scenario matrix (a)–(h) fully covered — 18 NEW tests close the gaps:

| Scenario | Verdict |
|---|---|
| (a) markers + judge-no + nothing pending → DENY+nudge+counter | PASS |
| (b) markers + judge-no + REAL pending → allow stands, hint injected, NO counter, wake preserved | PASS |
| (b)-supersede: TWO consecutive (b) turns → exactly ONE hint block | **PASS — verified vs REAL `langgraph.graph.message.add_messages` + real `MemorySaver` checkpoint round-trip** (root-conftest mock-langgraph eviction pattern; guard test asserts real function; upsert survives checkpoint persistence; source-pinned at langgraph 1.0.9 `graph/message.py:225`) — not a mirrored dict helper |
| (c) judge-complete → plain allow | PASS |
| (d) judge timeout / error / unparsable / wrapper-fault → conservative (a)/(b) | PASS (parametrized, wrapper-layer fault patched at `judge_completion_report_async`) |
| (e) kill-switch OFF → no judge, `marker_judge_verdict=<skipped>` | PASS |
| (f) dry mode → marker_hit logged, ZERO side effects | PASS |
| (g) attested allow → NO marker scan | PASS |
| (h) no markers → no judge call | PASS |

**NEW** `tests/integration/test_attestation_marker_routing_lca.py` (13 tests) + `tests/unit/test_attestation_marker_supersede_lca.py` (5 tests) — 79/79 green in 0.61s. Commit `5ac046ea`. No REAL FAIL findings.

## Job 4 — Idle-orphan semantics under real PostgreSQL: ✅ PASS

Disposable PG14 (port 15441, DB `ensemble_lca_final`, prod 5432 untouched). Coverage gap found (SQLite matrix pinned to `file_sqlite_engine` fixture, no PG-mode existed) → **NEW** `tests/postgres/test_attestation_live_descendants_pg_lca.py` (4 classes, 21/21 PASS, 3.01s), commit `1eb8d033`.

- Message lane `get_unprocessed_for_instances` (NEW in d950d2c8; PENDING/READY/PROCESSING/RETRYING, 50-id IN expansion): **dialect-identical to SQLite, zero dialect errors**.
- Job lane `get_active_by_instance` (`admission_state IN ('queued','active')`): **identical**.
- Two-set matrix (`_dormant_descendants_with_work_en_route` + facade): IDLE-no-work → NOT live; IDLE+message → live; IDLE+job → live; terminal/done → NOT live; unconditional-live set counts without work checks; mixed tree exact. **Identical on PG.**
- b08f40fe regression pinned at PG level: zero `message_queue` + zero `job_queue_items` idle orphans → `count_live_descendants = 0`.
- Migration `20260905_000001` confirmed PG+SQLite-safe (static test; no PG-only DDL trap).
- Teardown verified: cluster stopped, dir removed, 15441 free.

## Job 5 — Neighborhood A/B (manager + instance_messaging + messages + compaction): ✅ PASS — 0 INTRODUCED

A = `latest` @ `7d5285aa` (detached disposable worktree), B = `975cdf19`. 58-file pinned set (exact match to prior LCA judge gate), 6 partitions, byte-identical both sides, all under 5-min cap (A 213s / B 227s wall).

**Failed/ERROR ID sets BYTE-IDENTICAL (2881 bytes, 24 IDs) — B-only (introduced) = 0, A-only (healed) = 0.**
Common 24 = 22 quarantined IDs (QUARANTINE.md row 19: 6 FAILED + 16 PG-ERROR environmental) + **2 symmetric pre-existing** in `test_message_metadata_deletion_paths_prune.py` (stable solo fails both sides, signature `adelete_thread('...') await not found` — mock-await class; introduced between `bb052fce` and `7d5285aa` by non-LCA stacks; NOT LCA-caused). Quarantine row added (see below).
Compaction seam (hint rides `context_kind`): `test_attestation_compaction.py` + all proactive-compaction unit files PASS both sides — no regressions. Baseline worktree removed cleanly.

## Job 6 — Boot smoke from branch: ✅ PASS (all 6 checks)

Disposable PG 15442 + temp DATA_DIR, uvicorn port 8091 (dev.sh bypassed; ports 8079/8088 untouched; no `.env` copy).
1. Boot line `Creating PostgreSQL engine: 127.0.0.1:15442/ensemble_smoke` ✓
2. Attestation boot announcement `mode=enforce window=3 deny_bound=3 attestation_enabled=true llm_judge_enabled=true llm_judge_timeout_s=25.0` ✓ — log ≡ code (`attestation_resolver.py:111 DEFAULT_MODE="enforce"`; ship default confirmed).
3. Gate log row = **exactly 28 `%s` placeholders** (22 canonical + 6 NEW marker fields: `marker_hit`, `marker_terms`, `marker_path`, `marker_judge_verdict`, `marker_judge_latency_ms`, `marker_judge_error_class`) at `attestation_gate.py:990-1003` ✓
4. Natural gate-fire scenario: **SKIPPED — no zero-cost path** (no-LLM boot by design; judge transport failure would populate marker fields with noise, not signal). Static shape check = mandatory bar, satisfied.
5. Health: `/docs` + `/openapi.json` HTTP 200 ✓
6. Graceful shutdown: SIGTERM (PID verified bound to 8091 first) → port free in 1s; full graceful log sequence; PG stopped; temp dirs removed; no leftovers ✓

## ensure.md — Core (scoped): ✅ 4/4 PASS

| Requirement | Form | Result |
|---|---|---|
| No regressions in changed packs | COVERED-BY (matrix 598/598 + A/B 0 introduced) | PASS |
| Deadlock/concurrency integrity | Registered pack `test/packs/concurrency_atomic_unit_test.sh` (PACKS.md line 976) — `timeout 300` outer + internal `timeout 280` | PASS — 98P/74S/0F, baseline-exact |
| No sync DB calls on event loop | Same pack (thread-identity tests) | PASS |
| `dev.sh --timeout-graceful-shutdown 10` | Static grep (dev.sh:102) | PASS |

Zero contradictions with ensure.md; no Improvement Notices. Release Gate section scoped OUT — feature-gate precedent on this repo (wc-wake, answer-gate, LCA judge gates all ran scoped matrix + scenario E2E instead of shared-daemon E2E with real LLM calls); the incident/marker E2Es of Jobs 2–3 serve as the behavioral acceptance.

## Verification commits on branch (test-code only, ADD-only, path-scoped)

| Commit | Subject |
|---|---|
| `1eb8d033` | test(lca): PG-mode dialect canary for d950d2c8 two-set live semantics |
| `77a81ac5` | test(lca): b08f40fe incident acceptance — two-phase deny/attest reconstruction |
| `5ac046ea` | test(lca): marker-routing E2E scenario matrix (a)-(h) gap coverage |

Chain: `975cdf19` → `1eb8d033` → `77a81ac5` → `5ac046ea` (all `test:`-prefixed siblings; `git diff 7d5285aa..HEAD -- daemon/` EMPTY — production frozen).

## Non-blocking notes

1. 🟢 2 symmetric pre-existing failures (`test_message_metadata_deletion_paths_prune.py::{TestOrphanSweepPruneHappyPath::test_orphan_sweep_drops_side_table_rows, TestOrphanSweepPruneNeverRaise::test_prune_failure_does_not_abort_orphan_sweep}`) — NOT LCA-caused (byte-identical at `latest`); QUARANTINE.md row added this gate; fix belongs to the stack that introduced them (post-`bb052fce` merges).
2. 🟢 Boot natural-scenario skipped (no zero-cost no-LLM path); static 28-placeholder + marker-field shape verification stood as the mandatory bar and passed.
3. 🟢 Boot needed `POSTGRES_USER=postgres` on the disposable cluster (initdb superuser ≠ OS user) — documented in worker report; no side effects on sibling clusters.

## Worker instances

Inventory `3e14a6ad` · P1 `b56c9bbf` · P2 `648b5bf3` · P3 `c57780c1` · P4 `5495a389` · P5 `3b93a2e4` · P6 `be4edc8f` · PG `ef8dc877` · A/B `68ff5764` · Boot `3174045c` · Ensure `f4faec04` · Incident `9e959ab3` · Marker `afc3f0b1`

## Overall

- Unit matrix: ✅ 598/598
- Incident acceptance: ✅ both phases
- Marker matrix: ✅ (a)–(h) + real-add_messages supersede
- PG dialect: ✅ identical semantics
- Neighborhood: ✅ 0 introduced
- Boot smoke: ✅ enforce default + 28-field row
- ensure.md Core: ✅ 4/4
- **Testing Complete: ✅ READY FOR MERGE**
