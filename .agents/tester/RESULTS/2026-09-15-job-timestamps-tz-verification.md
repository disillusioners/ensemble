# Job Timestamps Timezone Fix — Final Gate Verification

**Branch:** `feature/fix-job-queue-timestamps-tz` @ `6e5a7cb8` (base `9db17e7d` = merge with `latest`)
**Delta:** 35 files — 21 daemon (1 NEW: `daemon/services/timestamps.py`) + 12 tests (5 NEW) + 2 `.agents/shared/planning/fix-job-timestamps/` docs. **Zero `frontend/` files. Zero `daemon/migrations/` files.**
**Worktree:** `agents-ensemble-wt-tz-fix` (HEAD verified `6e5a7cb8` by every worker, before AND after each pack; `git status --porcelain` clean start == end; main checkout never touched; port 8088 never touched; `ensemble_prod` never contacted — `POSTGRES_*`/`PERSISTENCE_DB_PATH`/`DATA_DIR`/`ENSEMBLE_DATA_DIR` scrubbed before every batch, all DB work on disposable PG14 clusters, ports 15432–15436, all freed + PGDATA removed at pack end).
**Date:** 2026-09-15/16 (runs 19:24–02:31 +07) · **Dispatcher:** tester · **Workers:** 9 (IDs in §8)

---

## VERDICT: ✅ **PASS-WITH-NOTES** — all four original symptoms closed with byte-level evidence; 93/93 independent runtime checks green; whole-tree sweep 0 NEW regressions; notes are non-blocking debts outside the fix contract.

---

## Scope Decision

Final-gate mission explicitly covering the full original-symptom matrix + whole-tree regression → **blast radius = critical/release-gate; full scope warranted** (not reduced). ensure.md Release-Gate real-LLM E2E (`tests/e2e/test_e2e_workflows.py`) excluded by design (requires live daemon + real LLM — out of mission scope); all non-integration suites covered via sharded packs per ensure.md rules.

---

## 1. Item-by-Item Results

| # | Leader item | Worker / Pack | Result |
|---|---|---|---|
| 1 | Lifecycle E2E on adversarial PG | tz-e2e `job_timestamps_lifecycle_e2e` (port 15432) | **PASS 35/35** |
| 2 | API payload serialization (jobs + missions) | tz-e2e (S1–S3) | **PASS** |
| 3 | Adversarial frame: engine sessions + SQL readers | tz-frame `engine_frame_readers_integration` (15433) | **PASS 15/15** |
| 4 | Mixed legacy/new rows | tz-mixed `mixed_rows_integration` (15434) | **PASS 15/15 ×2 runs** |
| 5a | Mock-quality audit (static) | tz-mocks | **CLEAN** (5/5 dimensions) |
| 5b | Edge cases: midnight/retry/dead-letter | tz-edge `timestamps_edge_integration` (15435) | **PASS 28/28** |
| 6 | Whole-tree regression vs baseline | tz-sweep-unit/mid/pg (15436) | **PASS — 0 NEW blockers** |
| 7 | FE | inventory diff evidence | **OUT OF SCOPE — zero FE files in delta; rationale below** |

### Item 1+2 — Lifecycle E2E + serialization (tz-e2e, PASS 35/35)
**Fidelity (documented):** hybrid — real routers (`daemon.routers.jobs/work/missions`) over real services (`JobQueueService`, `WorkResolverService`, `MissionResolver`, `DeadLetterService`) on real repositories against disposable PG; lifecycle driven through **production seams** (`JobQueueService.start_job` → `TaskRepository.create` + work_id=job_id dual-backing → `claim_pending_task` → `complete_task` → turn-mirror reconcile finalizes JobItem). No full daemon boot / graph run; nothing called an LLM (no mock LLM needed).
**Adversarial frame verified:** `psql SHOW timezone` = `Asia/Ho_Chi_Minh`; engine session `SHOW timezone` = `UTC` (forcing live).

**Original-symptom closure matrix (verbatim evidence):**

| Symptom | Check | Evidence |
|---|---|---|
| #1 `started_at` local digits vs `completed_at` UTC on SAME row (7h skew) | L3 | intra-row spread **0.047 s** (vs 25,200 s symptom); all fields `+00:00`; `started ≤ completed` (Δ 0.012 s) |
| #2 `created_at` re-stamped on later view (14:09:59+00:00 → 21:10:01+00:00) | L4 | `'2026-09-15T19:43:35.594412+00:00'` **byte-identical** across create-response / job_get / job_list / work view |
| #3 pending jobs carrying `started_at` | L1 | `null` on job_get AND job_list while pending |
| #4 list ordering / since-filters 7h off | L6+L7 | default order monotonic; since-filter `+07:00` form ≡ `+00:00` form → **identical subsets**, no 7h window shift |

- **S1/S3:** every `*_at` field offset-bearing on job payloads + work view (3/3 + 3/3).
- **S2 missions:** 200s; `started_at`/`last_activity_at` offset-bearing; **drift vs true instant = 0.000 s (no NEW 7h skew)**; epoch record `{seq:1, started_at:'…+00:00', ended_at:null}` — KNOWN D6 epoch bug recorded, not failed, **no regression**.
- **L5 storage truth:** `task` row naive-UTC digits (`19:43:36.06591…` — render-normalized `::text` drops trailing zeros, cosmetic); `job_queue_items.created_at` TEXT byte-equals API string; zero `timestamptz` columns.
- **Informational:** no `?since=` query param exists on the jobs HTTP API (verified across jobs/work/missions routers) — the branch's since surface is the missions tool (`daemon/tools/missions.py::_parse_since`), verified there; `job_queue_items` carries NO `started_at`/`completed_at` columns (Phase-5 dropped) — execution timing lives solely on `task` (D1 confirmed at storage layer).

### Item 3 — Engine frame + SQL readers (tz-frame, PASS 15/15)
- **All three construction sites force UTC at runtime:** main factory (`factory.py:206` def, `:259` canonical), **else-branch** (`factory.py:165`), **ens_db repair engine** (`ens_db_tools.py:496`, merged into libpq options with the three timeout `-c` flags). Grep-proven: no other sites in `daemon/`.
- **Sensitivity proof (defect class demonstrated live):** same fresh heartbeat row — forced session reads age **31.3 s** (FRESH, threshold 120 s); unforced +07 session reads **25,231.3 s = exactly +7 h inflation** (would falsely degrade `/readyz`, flag every live child hung).
- **Readers:** heartbeat fresh/stale correctly discriminated (0.0007 s vs 240.001 s); hung-children watchdog flags only the genuinely-old child (3720 s), live child absent.
- **Helper boundaries:** `23:59:59+07 ↔ 16:59:59Z`, `00:00:01+07 ↔ 17:00:01Z` (day-roll) lossless; `now_utc_naive()` Δ 4 µs vs aware clock; roundtrips 2/2.
- Independently closes the audit's R7 gaps (else-branch + `_build_repair_engine` had NO dev-test pins).

### Item 4 — Mixed legacy/new rows (tz-mixed, PASS 15/15, reproduced ×2)
- **M1 seeding reproduced the DC-A mechanism live:** aware bind through a +07 session → naive cols store `2026-09-15 21:10:01.435315`-style digits (evidence-4 specimen shape); new rows via daemon's own write paths (`task_repo.create → claim_pending_task → complete_task → job_repo.create`).
- **M2:** every rendered ts (36+/round) offset-bearing. **NEW rows byte-exact UTC — no new row mislabeled.** Legacy `wl-1` renders `2026-09-15T21:10:01.435315+00:00` = **+7.00 h late** — exactly the documented ACCEPTED transitional skew (D7 backfill pending).
- **M3:** mixed sort completes, monotonic under single assume-UTC frame, no frame-flip. **M4:** since-filter hard requirement holds (new-frame rows classified correctly); legacy task-records land IN per their +7h-late rendering (expected); no crash. **M5 (D4):** 36/36 byte-identical detail vs list on both surfaces, both frames. **M6:** DB byte-identical after 2 read rounds — reads never write back.

### Item 5a — Mock-quality static audit (tz-mocks, CLEAN)
- **Coverage adjudication:** dev tests adequate at R3 (engine-frame readers, real adversarial PG) / D5 helpers / factory-kwargs / D1-resolver; **partial at R1/R2/R4/R5/R8/R9** — every gap independently closed at runtime by this gate's packs (R1+R8+R9→tz-e2e; R2→tz-e2e S1/S2; R4→tz-mixed; R5→tz-edge; R7 else-branch+repair-engine→tz-frame).
- **Mocks sound on all five audit dimensions:** (a) kwarg-spy + libpq-consumption layering sound — removing the UTC forcing WOULD turn `test_pg_session_utc_frame_pg.py` RED; (b) zero clock patches, honest SQLite-scope limitation marker; (c) spies capture `connect_args` honestly; (d) every offset/window assertion pins a true instant — no 7h-skew-hiding class; (e) zero `datetime.utcnow()` traps in new tests.
- **🟠 Risk finding (F1 below):** `tests/unit/services/test_job_queue_proxy_phase1.py` — **5 test bodies REDUCED** (instance-derived assertions deleted; `QUARANTINE: body pins REMOVED pre-D1 instance-derived contract` markers at :294/:416/:467/:510/:985). D1 positive case now pinned only at unit level in the dev suite — this gate's e2e provides the live positive pin (L2/L3).

### Item 5b — Edge cases (tz-edge, PASS 28/28)
- **Midnight +07:** boundary conversions lossless both sides (incl. day-roll); live Task lifecycle UTC-digit consistent, `started ≤ completed` (Δ 0.056 s), no ±7h, no date/sign flip.
- **Retry:** real `JobItem` `queued→active→done(failed)→atomic_retry→…` ×2; all VARCHAR stamps `+00:00`; `next_retry_at` monotonic; `failed_at` cleared on retry; no local-digit stamps.
- **Dead-letter:** real exhaustion path (`max_retries=2` → `dead` → `DeadLetterRepository.enqueue`); `failed_at`/`moved_to_dlq_at` both `+00:00` UTC ISO, no skew vs failure instant.
- **No mocks** — real clock + real PG via daemon's own factory; adversarial discriminator verified (control engine `Asia/Ho_Chi_Minh` vs daemon engine `UTC`).
- Nuance: midnight **boundary values** proven at helper level; live-path lifecycle ran at 19:24 UTC (real clock cannot sit at local midnight) — invariant coverage intact via E1.a roundtrip + E1.b/E4 digit checks.

### Item 6 — Whole-tree regression sweep (0 NEW blockers)

| Part | Shards | Collected | P / F / E / S | Red nodes | NEW |
|---|---|---:|---|---|---:|
| unit (U1–U6) | 6 | 11,358 | 11,220 / 57 / 23 / 58 | 80 — 100% KNOWN | **0** |
| non-unit (M1–M6) | 6 | 4,550 | 4,437 / 36 / 7 / 67 | 43 — 100% KNOWN | **0** |
| postgres (P1 serial) | 1 | 308 | 267 / 5 / 0 / 34 | 5 — pre-existing, diff-empty proven | **0** |
| **Total** | 13 | **16,216** | **15,924 / 98 / 30 / 159** | **128** | **0** |

- **proxy_phase1 verdict: still-red ×7 — not fixed-by-branch, not new.** Same nodes as documented derived-status family (class/method structure byte-identical base→HEAD); branch's re-contract (`df92e8a2`) changed bodies, not node identity; zero reds beyond the documented 7.
- **Leader baseline reconciliation:** ≈69 family + 7 phase1 (≈76) expected in unit sweep → **80 observed (+4 family drift**, every node signature-matched to a documented QUARANTINE row — row-10 adjudication is 4 merges old); improvement-side drift: watchover ×47 and task_reconciliation ×6 families did NOT manifest. PG-suite **5/5 exact match** (see below). Non-unit 43 all mapped to 21 distinct QUARANTINE rows.
- **PG-suite 5 reds (leader's "2 order-dependent + 3 isolation") — empirical classes:** 2 solo-PASS order-dependent (`test_list_queues_with_admittable_work_pg` ×2 — `job_locks` trigger state pollution) + 3 **deterministic solo-FAIL pre-existing feature gaps** (`test_06f500af…` `_sweep_orphan_watchers` "Phase 1 not implemented"; `test_report_deferred_migration_pg` `claim_for_injection` drained-count; `test_report_delivery_recovery_pg` dependency-watchers SQL-shape). **Attribution: `git diff 9db17e7d..HEAD` + `git diff -S <symbol>` EMPTY for all 5 files and their target code paths** — zero tz-fix causation. Formalized into QUARANTINE.md (this commit).
- **Branch-owned surfaces all green:** 3 NEW unit tz files + 4 MODIFIED unit files (0 reds, arithmetic closure); `test_n8_hot_path_pin` 2/2; `test_task_heartbeat` 19/19; 2 NEW PG files **7/7** (`test_pg_session_utc_frame_pg` 3 + `test_pg_engine_utc_session` 4).
- **ensure.md Core: ✅** — concurrency pack `concurrency_atomic_unit_test` **98P/74S/0F in 9.87 s** (exact baseline); `dev.sh` `--timeout-graceful-shutdown 10` present @ :102 (static PASS). Release-Gate real-LLM E2E excluded by design (above).
- **Exclusions (documented):** `tests/e2e/test_e2e_workflows.py` (real-LLM release gate); `test_context_injection_hybrid.py` (live-daemon collection ERROR — existing row-64 member).

### Item 7 — FE rationale (no web automation)
`git diff 9db17e7d..HEAD` shows **zero `frontend/` files** — no FE change exists to test. The API change is shape-preserving on the FE side: all wire timestamps were already strings and the FE already parses offset-bearing strings (established `created_at` `+00:00` precedent — the original bug's `created_at` rendered correctly). `started_at`/`completed_at` merely gain the same offset-bearing form. **No FE automation warranted.**

---

## 2. Findings & Follow-ups (non-blocking, outside fix contract)

| # | Finding | Severity | Route |
|---|---|---|---|
| F1 | `test_job_queue_proxy_phase1.py` 5 test bodies REDUCED (assertions deleted, in-file QUARANTINE markers) pending re-contraction; D1 positive case only unit-pinned in dev suite (independently covered live by this gate) | 🟠 test-debt | owner: re-contraction pass |
| F2 | Dev tests missing pins for: ens_db repair-engine UTC frame, factory else-branch PG path, HTTP-level offset symmetry, mission offsets, mixed-row PG, midnight crossing, DLQ stamps — all verified at runtime by this gate | 🟢 nice-to-have | promote gate harness checks into repo tests |
| F3 | Legacy +07-digit rows render **7h late** until backfill (D7 proposal pending user decision) — accepted transitional, read-only verified (M6 no write-back) | 🟡 known/accepted | D7 decision |
| F4 | Mission epoch `started==ended`/derived-epoch bug (D6 findings) — no regression from this fix (drift 0.000 s); future phase | 🟡 known/deferred | D6 recommendation |
| F5 | 5 PG-suite reds formalized into QUARANTINE.md (this commit); 3 are deterministic feature gaps failing every commit until fixed | 🟠 pre-existing | route to owner |
| F6 | Ops traps documented in LESSONS/2026-09-15-tz-verification-pg-suite-execution-notes.md: `tests/postgres` conftest reads `PG_TEST_*` (NOT `ENSEMBLE_TEST_PG_URL`); xdist-skip guard makes `-n auto` a **false-PASS** (308 skipped in 4s) — serial `--override-ini="addopts=" -m postgres` required; dispatcher-injected `POSTGRES_*` persists across worker subshells (redirect-to-disposable pattern); PG `timestamp::text` render-normalization for byte-compares; legacy-row seeding must bind AWARE values through a +07 session | 🟢 ops | LESSONS |
| F7 | Unit sweep +4 family drift vs row-10 adjudication (4 merges stale) — all signature-matched; QUARANTINE row refresh candidate | 🟢 hygiene | next gate |

---

## 3. ensure.md Validation Results

- **Critical:** scoped packs PASS ✅ · concurrency pack 98P/74S/0F ✅ · sync-DB thread-identity (in concurrency pack) ✅ · dev.sh graceful-shutdown grep ✅
- **Release Gate:** full non-integration suite via sharded packs ✅ (0 NEW); real-LLM E2E items **not run — excluded by design** (no live daemon / real LLM in mission scope; zero FE delta). No contradictions with ensure.md methods encountered (all validations ran as packs with dual-layer timeouts).
- **Improvement notices:** none (no ensure.md contradiction found this gate).

---

## 4. Artifacts & Hygiene

- Harnesses + evidence: `/tmp/tzverify-{e2e,frame,mixed,edge}/`, `/tmp/tzverify-sweeppg/`, `/tmp/sweep-m1to6/`, `/tmp/p1-output/`, `/tmp/p5-output/` (ephemeral, outside WT).
- Ports 15432–15436 + 16001 (mock LLM, never needed): all freed; PGDATA dirs removed; worktree porcelain clean at every pack boundary; HEAD `6e5a7cb8` never drifted.
- **No production code, no existing tests modified. Nothing in `tests/` added by this gate.** Documentation-only commit: RESULTS (this file) + PACKS.md banner + QUARANTINE.md row + LESSONS note.

## 5. Worker Roster

| Worker | Instance | Pack/Task |
|---|---|---|
| tz-inv | 862f28c2 | inventory + shard plan + QUARANTINE enum |
| tz-e2e | c6033d6c | lifecycle E2E + serialization (35/35) |
| tz-frame | fa7ebfe9 | engine frame + readers (15/15) |
| tz-mixed | 3e8d0d39 | mixed rows (15/15 ×2) |
| tz-mocks | d7cd39a7 | coverage + mock audit |
| tz-edge | ce51d4bd | edge cases (28/28) |
| tz-sweep-unit | d3cc344c | U1–U6 |
| tz-sweep-mid | a8d7cc4f | M1–M6 |
| tz-sweep-pg | b1734e28 | P1–P5 + ensure.md Core |

**Overall: Unit-style independent packs 93/93 PASS · Sweep 16,216 collected / 128 known / 0 NEW · ensure.md Core PASS · Verdict PASS-WITH-NOTES (notes = F1–F7, all non-blocking).**
