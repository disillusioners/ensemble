# Phase 2: Harden Reliability (Tier P2)

Branch: `feature/schedule-review-improve` @ `46349698` (v0.11.0)
Plan tier: **P2 (Reliability Hardening)**
Issues owned by this phase: **INV-4, INV-5, INV-6, INV-7, INV-8**
Phase-gate level: Phase closes when ALL five land. **Phase 4 INV-13 does NOT gate on Phase 2 INV-5** (architect §5; decisions D7.7 — premise false; replaced by a vocabulary freeze on the named-transition + post-commit-reconcile idiom).

---

## Header — Per-Issue Identification (Quoting Frozen Inventory)

| INV | Title | E2E Gate | Files (verbatim from research-findings.md) | Lines |
|-----|-------|----------|--------------------------------------------|-------|
| INV-4 | Unbounded SKIP contention TOCTOU (per-queue lease / set-aside design) | **Release gate** | `daemon/services/job_processor.py` | 711-714 (scan) ↔ 1054-1116 (start) |
| INV-5 | Task↔JobItem reconciliation gap | **Release gate** | `daemon/repositories/task/repository.py`; `daemon/services/instance_lifecycle.py` | 2126-2241; 2474-2518, 3693+, 3870-3874 |
| INV-6 | DB-time convention violations | Core gate (repo pack) | `daemon/repositories/task/repository.py` | 694, 1657, 2078, 2107 |
| INV-7 | F16 residual hardening (telemetry + terminal_reason validation) | Core gate | `daemon/services/work_status.py` | 192-272 |
| INV-8 | Circuit-breaker `reset()` / `_probe_in_flight` invariant untested | Core gate (source pack) | `daemon/sources/circuit_breaker.py` | 103-124 |

Frozen inventory provenance: `research-findings.md` Partition 1 (INV-4), Partition 2 (INV-5, INV-6, INV-7), Partition 3 (INV-8). All line refs spot-verified against `latest` HEAD 2026-08-24.

---

## Objective

Close race windows, fix DB-time skew, harden legacy-status derivation, and assert circuit-breaker invariants across five independent reliability surfaces in `daemon/services/job_processor.py`, `daemon/repositories/task/repository.py`, `daemon/services/work_status.py`, and `daemon/sources/circuit_breaker.py` — restoring operator signal on orphaned jobs (via INV-5 reconciliation closure) without expanding scope to the 10 inventoried items owned by Phases 1/3/4.

---

## Component Inventory

| Surface | Files Touched | New Files | Tier |
|---------|---------------|-----------|------|
| Queue SKIP contention (INV-4) | `daemon/services/job_processor.py` | none | P2 |
| Task↔JobItem reconciliation (INV-5) | `daemon/repositories/task/repository.py`, `daemon/services/instance_lifecycle.py` | `tests/unit/repositories/task/test_task_jobitem_reconcile_resume.py` | P2 |
| DB-time convention (INV-6) | `daemon/repositories/task/repository.py` | none | P2 |
| F16 residual (INV-7) | `daemon/services/work_status.py` | `tests/unit/services/test_work_status_terminal_reason_validation.py` | P2 |
| Circuit-breaker invariants (INV-8) | `daemon/sources/circuit_breaker.py` (test-only) | `tests/unit/sources/test_circuit_breaker_invariants.py` | P2 |

No new dependencies; no schema migrations; no API surface changes. INV-6 is a **single change unit** — the four query sites (`list_pending_tasks_older_than` ~694, `update_heartbeat` ~1657, `find_stale_running_tasks` ~2078, `reset_stale_tasks` ~2107) share a common defensive predicate and must NOT be split across workers (coupling map: `plan-overview.md §INV-6`).

---

## Internal Ordering & Sub-Slice Map

### Sequential Gates (must hold)

```
Phase 1 closes (INV-1 lands)  ─►  Phase 2 sub-slice A: INV-4 can start
Phase 2 sub-slice A' (INV-5)  ─►  Phase 2 closes (gate)
```

### Sequencing amendments (architect §5; decisions D7.7)

- **INV-6 → INV-5 hard edge DISSOLVES.** The original rationale ("the resume-time sweep reuses the same age predicate that INV-6 SQL-ifies") was never true — A′3's spec is an age-free NOT-EXISTS, and the *redesigned* INV-5 (A′3r) needs no `_age_seconds_sql`. Keep only the **soft** same-file ordering inside the repo-worker (B before A′).
- **"INV-5 gates Phase-4 INV-13" claim is DELETED.** Premise false (architect §2c: the three "unmigrated" call-sites are already named-transition wrappers; D2's gate dissolves). The new constraint is: the **named-transition + post-commit-reconcile vocabulary** is frozen before both INV-5's redesigned sweep and Phase-4 INV-13-as-verification land — not "INV-5 finishes first."
- **Pack contention:** four parallel sub-slices all run `concurrency_atomic_unit_test` (13 files, 280s internal cap, timing-sensitive `gate_threading_serialization`). Safe (self-contained tmp fixtures) but contending — **stagger runs via dispatcher-level queue**.

### Parallel Sub-Slices (encode explicitly for instance-reuse)

| Sub-slice | Worker | Owns | Reads (no edits) | Independent of |
|-----------|--------|------|------------------|----------------|
| **A** | queue-worker (parallel) | INV-4 (per-queue lease / set-aside list — cross-cycle counter in `JobProcessor` instance state; no `asyncio.sleep` in `_process_next_job`; outer scan continues other queues during set-aside window) | INV-1 patch (Phase 1 close) | sub-slices B, C, D |
| **B** | repo-worker (parallel) | INV-6 (DB-time convention, all 4 sites in one unit) | none | sub-slice A (different files); sub-slice C (different files); INV-5 reads B's output but only as a contract artifact |
| **C** | source-worker (parallel) | INV-8 (circuit-breaker tests + high-concurrency stress + probe-slot invariant) | none | sub-slice A; sub-slice B |
| **D** | core-worker (parallel) | INV-7 (terminal_reason validation + telemetry on unknown-admission fallback) | none | sub-slices A, B, C |
| **A'** | repo-worker (sequential after B — soft same-file order only, see Dependency Notes) | INV-5 (named-transition post-commit `AbortTurn(reason='failed')` gated on `status='paused' AND NOT EXISTS live JobItem`; SKIP-LOCKED sweep invoking the transition per row; document defensive NOT-EXISTS clauses without removing; fix path typo — see D7.6 redesign) | INV-6's `_age_seconds_sql` helper NOT required by the redesigned A′ (age-free NOT-EXISTS) | sub-slices C, D can run alongside |

**Critical-path rationale (corrected per architect §5; decisions D7.7)**: INV-5 does NOT gate Phase 4 INV-13 (see Sequencing amendments above). The original D2 rationale was based on a now-stale premise (three "unmigrated" call-sites that were already migrated). The new constraint is a **vocabulary freeze** on the named-transition + post-commit-reconcile idiom — both the redesigned A′3r sweep and Phase-4 INV-13-as-verification use the same `AbortTurn(reason='failed') + reconcile_turn_mirror + emit_terminal` shape. Soft same-file ordering inside the repo-worker (B before A′) is preserved as a hygiene convention, not a hard dependency.

### Parallelization Discipline

- All four parallel workers (A, B, C, D) commit to **separate commits** on `feature/schedule-review-improve` so review and bisect stay clean.
- A' (INV-5) reads INV-6's commit message + shape; if INV-6's diff is non-trivial (e.g., adds a new `_age_seconds_sql()` helper), INV-5 imports the helper rather than re-implementing the SQL idiom.
- INV-7 (D) is fully orthogonal; standalone anywhere.
- Per `decisions.md §D4` (intra-Phase-1 sequencing rule applied symmetrically here): lower-risk fix lands first within a sub-slice if it shadows a higher-risk fix. INV-6 (mechanical SQL idiom swap) precedes INV-5 (resume-transition semantics) inside the repo-worker.

---

## Tasks

### Sub-slice A — Queue Worker (INV-4)

> **Design amendment (architect §1b; decisions D7.5):** The original per-scan-cycle consecutive counter is wrong — `job_processor.py:1052` `continue` resets the counter at iteration end so sustained cross-cycle contention never accumulates. The `asyncio.sleep` primitive inside `_process_next_job` is also wrong — `_process_loop` is single-threaded at `job_processor.py:650` and awaits `_process_next_job` once per iteration, so a sleep stalls ALL queues (every other queue waits out the hot queue's backoff). The `system_parallel_queue` concurrency=5 means a 5-way race produces 4 SKIPs — the old threshold=3 trips on the first race and penalizes all queues.
>
> **Replacement design: per-queue LEASE / SET-ASIDE LIST.** When a queue exceeds the skip threshold, EXCLUDE it from the scan set for an exponentially-growing window with jitter (no inner-loop sleep). The outer `_process_loop` continues iterating other queues. Keep the `event=skip_backoff` log line — it remains the right cheap metric.

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A1 | Add `per_queue_skip_state: dict[queue_id, PerQueueSkipState]` to `JobProcessor` instance state (per-queue, **rolling across scan cycles** — survives the `continue` at `job_processor.py:1052`). Each `PerQueueSkipState` carries: `consecutive_skips: int`, `set_aside_until: float` (monotonic deadline), `set_aside_window_ms: int`. Reset `consecutive_skips` only on a successful claim, **not** at iteration end. | none | State dict exists in `JobProcessor.__init__`; first SKIP for a queue writes `consecutive_skips=1` and does NOT touch `set_aside_until`. |
| A2 | Implement per-queue LEASE / SET-ASIDE semantics. At scan-entry of `_process_next_job` (`job_processor.py:711-714`), for each candidate queue: if `time.monotonic() < set_aside_until`, skip with NO log emission (already set-aside — silence is correct). When a SKIP pushes `consecutive_skips >= skip_backoff_threshold` (default 3), compute `window_ms = min(base_ms * 2^(consecutive_skips - threshold) + jitter_ms, cap_ms)` with defaults `base_ms=50`, `cap_ms=2000`, `jitter_ms=random(0, 250)`, set `set_aside_until = time.monotonic() + window_ms/1000.0`, then emit the log line and **continue** the outer scan (do not sleep inside the loop). Constants live in `daemon/services/job_processor.py` module-level and are flagged `# TODO(post-deploy-tune): tune from production scan timing — INV-4 known gap`. **Critical: the outer `_process_loop` (`job_processor.py:650`) must iterate other queues while the hot queue is set aside — verified by reading the loop body during implementation.** | A1 | A forced 5-way race on `system_parallel_queue` shows: 4 SKIPs emit log lines; the loop continues processing OTHER queues while the hot queue is set aside; after window expiry, the hot queue rejoins the scan (re-entry mechanics left to executing worker per open-question flag). |
| A3 | Add metric emission (`logger.info` with structured fields `queue_id`, `consecutive_skips`, `window_ms`, `event=skip_backoff`) for every backoff-triggering SKIP — keep metric cheap (one log line, no remote telemetry). Log fires ONCE per set-aside transition, not per skipped iteration within the window. | A2 | Log line emitted exactly once per `set_aside_until` advance; sampled by grep `event=skip_backoff` after a force-skipped scan. |
| A4 | Unit test: simulate 5 concurrent processor instances racing on a single hot-queue JobItem; assert (a) at most 1 instance wins the claim, (b) losers observe SKIP, (c) `consecutive_skips` increments per loser across scan cycles (NOT reset at iteration end), (d) set-aside triggers after threshold and the hot queue is excluded from the scan set, (e) OTHER queues continue to be processed during the set-aside window (no loop stall), (f) recovery to claim path resumes after window expiry. Test lives in `tests/unit/services/test_job_processor_skip_contention.py`. | A1, A2, A3 | Test PASSES under `concurrency_atomic_unit_test` pack. |
| A5 | Verify INV-1 (Phase 1) is already merged to `feature/schedule-review-improve` HEAD before A1 starts; if not, block until Phase 1 closes (per dispatch note "INV-1 fix may unmask this contention — overview coupling map"). | upstream Phase 1 | `git log --oneline feature/schedule-review-improve` shows INV-1 commit SHA. |

> **Open question for the executing worker (architect §10):** Lease re-entry mechanics — how a set-aside queue rejoins the scan after its window expires (lazy at scan-entry check vs. event-driven wake). Direction is firm (set-aside list + per-queue state); the re-entry trigger is an implementation detail.

**Sub-slice A exit criterion**: Per-queue lease state + set-aside scan exclusion + metric + test in place; the hot-queue starvation reproduces in the new contention test only with explicit threshold breach; OTHER queues continue processing during the set-aside window (no `asyncio.sleep` stall).

### Sub-slice B — Repo Worker (INV-6)

> **Design amendment (architect §6; decisions D7.4):** DB-side `now()` ages are the correct pattern. Three plan-text corrections are required: **(a)** The write side binds `datetime.now(timezone.utc)` (aware UTC) — **NOT** `datetime.utcnow()` — at `repository.py:1657`; psycopg renders it into session-local wall time for the naive TIMESTAMP column, and PG `now()` returns the same session-local wall time → `EXTRACT(EPOCH FROM (now()-col))` is frame-consistent. The plan text at the old B3 mistakenly said `utcnow()` — fix it. **(b)** The session-TZ invariant is LOAD-BEARING: correctness relies on the PG session TZ matching the daemon-local rendering frame (same invariant `readiness.py:88-99` relies on). It holds by accident today; document the invariant at `_age_seconds_sql` and recommend `SET TIME ZONE 'UTC'` at connection time to make it contractual. **(c)** `julianday()` has **millisecond** (not microsecond) precision — sub-second thresholds are unreliable. Plan defaults are minutes-scale (fine), but flag in the helper docstring. PG `EXTRACT` returns `Decimal` via psycopg — `float()` coercion is mandatory; B6 must assert non-Decimal returns.

Single change unit — do not split. The four sites share the age-math predicate.

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| B1 | Add private helper `_age_seconds_sql(column: str) -> str` in `daemon/repositories/task/repository.py` that returns PG `EXTRACT(EPOCH FROM (now() - {column}))` when `self._dialect == "postgres"`, else SQLite `((julianday('now') - julianday({column})) * 86400.0)`. Coerce result to `float()` in Python to neutralize `Decimal` returned by psycopg. **Helper docstring must state:** (i) the session-TZ invariant (frame-consistency between PG session TZ and daemon-local rendering requires `SET TIME ZONE 'UTC'` at connection time to be contractual; today it holds by accident); (ii) `julianday()` has millisecond (not microsecond) precision — sub-second thresholds unreliable, plan defaults are minutes-scale (fine); (iii) PG `EXTRACT` returns `Decimal` via psycopg — callers MUST coerce to `float()`. | none | Helper exists; docstring carries all three annotations; unit test confirms both dialects return `float` (not `Decimal`). |
| B2 | Migrate `list_pending_tasks_older_than` (~line 694): replace Python-side `(now - col).total_seconds()` with SQL `WHERE {_age_seconds_sql("created_at")} > :threshold_seconds`. Threshold parameter switches from `timedelta` to `float` seconds. API callers updated to pass `float` (one grep-and-replace at call sites — verify count ≤ 6 by static check). | B1 | No `timedelta` arithmetic on naive TIMESTAMP in this query path; tests PASS. |
| B3 | Migrate `update_heartbeat` (~line 1657): the write path uses `datetime.now(timezone.utc)` (aware UTC — psycopg renders it into session-local wall time for the naive TIMESTAMP column — **not** `utcnow()`) to set `last_heartbeat_at`. Keep the write surface unchanged (naive TIMESTAMP column unchanged) but adjust downstream readers (covered by B4 + B5) to compute age SQL-side. NOTE: heartbeat write is **coupled** with staleness reads per dispatch note — keep both ends in this same unit. | B1 | Write path unchanged (now aware UTC); readers no longer depend on Python-side delta. |
| B4 | Migrate `find_stale_running_tasks` (~line 2078): SQL-side `WHERE {_age_seconds_sql("last_heartbeat_at")} > :stale_seconds`. Parameter `stale_seconds: float`. | B1 | No naive-TIMESTAMP delta in this query. |
| B5 | Migrate `reset_stale_tasks` (~line 2107): SQL-side age filter (same idiom as B4) for selecting rows to reset. | B1, B4 | No naive-TIMESTAMP delta in this query. |
| B6 | Unit test suite per dialect: `tests/unit/repositories/task/test_db_time_convention.py` — 4 cases × 2 dialects = 8 tests. Inject a row with `last_heartbeat_at` set 90 seconds in the past, assert each query's age filter selects/excludes correctly on both PG (use `pytest-postgresql` fixture) and SQLite. **Each test MUST additionally assert** the return type is `float` (NOT `Decimal`) — psycopg `EXTRACT` returns `Decimal`; the helper must coerce. | B1-B5 | All 8 tests PASS in `concurrency_atomic_unit_test` pack; no Python-side `timedelta` arithmetic remains (grep-clean); each test asserts `isinstance(result, float)`. |
| B7 | Static check (no test): grep `daemon/repositories/task/repository.py` for `datetime.utcnow()` age comparisons and `(datetime - col)` patterns. Expect 0 results on the four sites; older sites (pre-2024) outside scope remain unchanged. | B1-B5 | `grep -nE "(datetime\.utcnow\(\) - |\(.*-.*\)\.total_seconds\(\))" daemon/repositories/task/repository.py` returns 0 hits in the four migrated paths. |

**Sub-slice B exit criterion**: All four sites SQL-side; helper exists with full docstring (session-TZ invariant + julianday millisecond precision + Decimal-coercion warning); dialect-aware tests assert non-Decimal returns; **recommended follow-up (not in-cycle, document only):** add `SET TIME ZONE 'UTC'` at connection time to make the session-TZ invariant contractual.

### Sub-slice C — Source Worker (INV-8)

> **Design amendment (architect §3b; decisions D7.7):** Plan task C3 references `_set_state()`, which **DOES NOT EXIST** in `circuit_breaker.py` (state assignments are inline at `:63, :91, :107` — grep-verified). Extract `_set_state` as a refactor is scope creep; instead place the `__debug__` invariant assertion at the THREE REAL state sites. Also add the missed `record_failure` invariant: `record_failure` clears `_probe_in_flight` unconditionally (`:103`) — a NON-probe caller's failure during HALF_OPEN can falsely free the probe slot mid-probe. Add the "sibling-failure does not free the probe slot" test. Lower the 200-concurrency stress to 100 (symmetric with INV-10, less CI-flaky) and seed the failure order.

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| C1 | Write `tests/unit/sources/test_circuit_breaker_invariants.py` covering: (a) `reset()` clears `_state`, `_failure_count`, `_success_count`, `_probe_in_flight`, `_last_state_change_at`; (b) `reset()` is idempotent (two calls in a row do not raise); (c) `_probe_in_flight` invariant: when state is `HALF_OPEN`, exactly one probe is in flight under high-concurrency stress (100 concurrent `try_acquire()` calls after the failure threshold); (d) **NEW (architect §3b missed invariant)**: "sibling-failure does not free the probe slot" — drive the breaker to HALF_OPEN, hold one in-flight probe via a slow `try_acquire()` call, then have a non-probe caller fail and verify `_probe_in_flight` remains `True` (the unconditional `_probe_in_flight=False` in `record_failure` at `:103` is the bug surface the test pins). | none | New test file exists; all 4 invariants hold; sibling-failure test asserts `_probe_in_flight is True` after the non-probe failure. |
| C2 | Add a high-concurrency stress test: spawn **100** concurrent (lowered from 200 — symmetric with INV-10, less CI-flaky per architect §3b) `try_acquire()` / `record_failure()` interleavings against a CircuitBreaker configured with `failure_threshold=5`, `probe_interval_ms=100`. **Seed the failure order** (deterministic — pass `random.seed(...)` or arrange the call sequence in the test) so the interleaving is reproducible. Assert (a) no `RuntimeError` ("probe already in flight") leak; (b) state machine stays in {CLOSED, HALF_OPEN, OPEN} — no rogue states; (c) `success_count` monotonic per HALF_OPEN probe cycle. | C1 | Stress test runs under 5-min pack timeout; passes; failure order is seeded. |
| C3 | Add `_probe_in_flight` invariant assertion as a debug-only check (`if __debug__:` block) at the **THREE REAL state-assignment sites** in `circuit_breaker.py` — `:63, :91, :107` — **NOT** in a non-existent `_set_state()` (which does not exist; extracting it would be scope creep). The `__debug__` assertion is acceptable: ensemble runs default Python, so it fires in prod; free under `python -O`. Production builds (no `-O`) get the assertion; `python -O` skips it. | C1, C2 | Debug-mode assertions exist at the three line refs; production code path unchanged; no `_set_state` extraction. |
| C4 | Run `concurrency_atomic_unit_test` pack to verify no regressions in adjacent source-layer concurrency tests. | C1-C3 | Pack PASSES. |

**Sub-slice C exit criterion**: Test file present, all 4 invariants asserted (including sibling-failure probe-slot test), no regression in source pack.

### Sub-slice D — Core Worker (INV-7)

> **D0 vocabulary freeze (architect §2b; decisions D7.2):** The original sweep in A′3 used `terminal_reason='orphaned_no_task'` — but that value is NOT in `_STATUS_CANONICAL_MAP` (`work_status.py:66-122`); `_derive_legacy_status` (`:256`) would fall through to the lossy `done → completed` mapping, producing a runtime regression on the very operator signal INV-5 exists to restore. **LEADER RULING: use `reason='failed'` (already canonical). `orphaned_no_task` is NOT added to the canonical map.** The redesigned A′3r sweep (`AbortTurn(reason='failed')`) honors this ruling. Consequence for sub-slice D: the test D3(a) "known values" list MUST remove `orphaned_no_task` — it was never canonical. This also **dissolves** the D↔A′ semantic coupling that sub-slices D and A′ shared via the `orphaned_no_task` value; per the coupling matrix below, the matrix's previous "independent" entry is now GENUINELY independent (no shared vocabulary contract).

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| D1 | In `_derive_legacy_status` at `daemon/services/work_status.py:192-272`, add a `terminal_reason` validation branch that returns `(None, "unknown_terminal_reason")` (or raises a typed `LegacyStatusDerivationError`) when the input `terminal_reason` is not a member of `_STATUS_CANONICAL_MAP`. The existing canonical-map membership check (F16 fix) stays — this is additive. | none | Validation branch present; type-narrowed to keep mypy / pyright clean. |
| D2 | Add structured telemetry on the unknown-admission fallback path (the else-branch when neither `admission_state` nor `terminal_reason` resolve): `logger.warning("work_status.unknown_admission_fallback", extra={"admission_state": ..., "terminal_reason": ..., "job_id": ..., "work_id": ...})`. Keep cost flat (single log line, no remote call). | D1 | Log line exists; grep `unknown_admission_fallback` returns 1+ hits after a force-unknown-state unit test. |
| D3 | Write `tests/unit/services/test_work_status_terminal_reason_validation.py` covering: (a) **CORRECTED per D7.2** — known `terminal_reason` values pass through (**4 cases: `cancelled`, `failed`, `completed`, `timed_out`** — `orphaned_no_task` is NOT a known value; it is NOT in `_STATUS_CANONICAL_MAP` and the redesigned A′3r sweep uses `reason='failed'` instead); (b) unknown `terminal_reason` triggers validation branch with proper error/log; (c) unknown-admission fallback logs telemetry; (d) F16 truthy-check path is unchanged (regression guard). | D1, D2 | All test cases PASS; "known values" list contains exactly the 4 canonical reasons. |

**Sub-slice D exit criterion**: Validation + telemetry + test pass; F16 truthy-check behavior preserved; `orphaned_no_task` is NOT in the test's "known values" list (it is never canonical — confirmed by reading `_STATUS_CANONICAL_MAP` at `work_status.py:66-122`); the D↔A′ semantic coupling is dissolved (no shared vocabulary contract).

### Sub-slice A' — Repo Worker (INV-5, sequential after B — soft same-file order only)

> **Design amendment (architect §2a; decisions D7.6):** The original A′1/A′2/A′3 fight the `reconcile_turn_mirror` authority on three axes: (i) raw terminal UPDATEs bypass `MIRROR_SET = ALL_8_MIRRORS` (`turn_transitions.py:298, 351, 393` are the only sanctioned writers) — 7 of 8 mirror tables go stale; (ii) in-transaction reconcile re-introduces the SQLite file-lock reentrance that `task/repository.py:1854-1859` documents removing (the resume path already calls `reconcile_turn_mirror` post-commit at `instance_lifecycle.py:3870-3874` — A′2 was duplicative and riskier); (iii) the bulk sweep bypasses `dependency_bus` (no `emit_terminal` → `dependency_watchers` rows stay PENDING at `repository.py:887-927`), recreating the idle-gate deadlock INV-5 is supposed to fix.
>
> **Replacement design (architect §2a):** (A′1r) POST-COMMIT `AbortTurn(reason='failed')` per stuck Task, gated on `status='paused' AND NOT EXISTS live JobItem`; `reconcile_turn_mirror(work_id)` then handles all 8 mirrors exactly as `cancel_task` does (`repository.py:3242-3253`). (A′3r) Replace bulk UPDATE with `SELECT … FOR UPDATE SKIP LOCKED` candidate query that **invokes the named transition** per row, bounded by `limit=100`. A′5 (document, don't remove, the defensive NOT-EXISTS clauses) survives — but **fix the path typo**: idle-gates live in `task/repository.py:2199, 2430` with the NOT-EXISTS defense at `:2519-2570`, **NOT** `instance_lifecycle.py:2474-2518` (those lines are the watchover crash-recovery block).

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A'1r | After the **existing** `ResumeTurn` loop in `_resume_cascade_db_sync` (`daemon/services/instance_lifecycle.py:3693+`) completes its DB write and the transaction has **committed**, POST-COMMIT: query Tasks in `status='paused'` whose `work_id` has `NOT EXISTS` a live JobItem (`status IN ('queued', 'active')`); for each such Task, invoke `AbortTurn(reason='failed').run(session)` (the named transition — `reason='failed'` is canonical in `_STATUS_CANONICAL_MAP` per `work_status.py:66-122`; `orphaned_no_task` is NOT added to the map per D7.2 leader ruling). `reconcile_turn_mirror(work_id)` runs as part of the named-transition path and handles all 8 mirror tables exactly as `cancel_task` does (`repository.py:3242-3253`). | A'1r prerequisite: `_resume_cascade_db_sync` already calls `reconcile_turn_mirror` post-commit at `instance_lifecycle.py:3870-3874` (no new reconcile call added inside the transaction). | `_resume_cascade_db_sync` invokes `AbortTurn(reason='failed')` post-commit; transaction boundary respected (no reconcile inside the tx); mirrors remain consistent. |
| A'2r | DELETE the original A′2 (in-transaction reconcile call inside `_resume_cascade_db_sync`). The resume path already calls `reconcile_turn_mirror` post-commit at `instance_lifecycle.py:3870-3874` — adding a second reconcile call is duplicative AND reintroduces the SQLite file-lock reentrance that `task/repository.py:1854-1859` documents removing. | n/a | No reconcile call inside `_resume_cascade_db_sync`'s transaction. |
| A'3r | Replace the bulk-UPDATE sweep with a candidate-query sweep method `sweep_stuck_paused_tasks_with_dead_jobitems(limit: int = 100)` in `daemon/repositories/task/repository.py`. **(a) Candidate query** (PG + SQLite both): `SELECT work_id FROM tasks WHERE status='paused' AND NOT EXISTS (SELECT 1 FROM job_items WHERE job_items.work_id = tasks.work_id AND job_items.status IN ('queued', 'active')) LIMIT :limit FOR UPDATE SKIP LOCKED` (PG dialect; SQLite uses `LIMIT :limit` without `FOR UPDATE` — the SKIP LOCKED semantics are PG-only, SQLite relies on transaction isolation; verify both dialects against the test). **(b) Per-row transition**: for each `work_id` returned, invoke `AbortTurn(reason='failed').run(session)` (the named transition — same vocabulary as A′1r). The transition's own `transition._write()` triggers `reconcile_turn_mirror(work_id)` and `emit_terminal()` for `dependency_watchers` (matching `cancel_task`'s behavior at `:3242-3253`). **(c) Bound**: `limit=100` per call (no age predicate — the NOT-EXISTS is age-free, per architect §5). **(d) Schedule**: low-frequency (every Nth resume, N=10 default; flagged `# TODO(post-deploy-tune)`); non-blocking via `asyncio.create_task` (do NOT block the cascade). | A'1r | Sweep method exists; idempotent within one tick; named-transition writes only (no raw terminal UPDATE); emits terminal events (no `dependency_watchers` deadlock). |
| A'4 | (Survives from original plan with rewritten semantic.) Hook the sweep into `_resume_cascade_db_sync` post-commit: every Nth resume (N=10 by default, flagged `# TODO(post-deploy-tune): tune from production frequency — INV-5 known gap`). The sweep is async-fired and non-blocking on the resume path (`asyncio.create_task` acceptable; do NOT block the cascade). | A'3r | Sweep runs after the resume write; resume path not blocked on sweep completion. |
| A'5 | (Path-typo fix.) Document (not remove) the defensive `NOT EXISTS terminal-JobItem` clauses in `has_active_non_deferred_work` and `has_active_non_background_work` — these live in `daemon/repositories/task/repository.py:2199, 2430` with the NOT-EXISTS defense at `:2519-2570`. **(NOT** `daemon/services/instance_lifecycle.py:2474-2518` — that region is the watchover crash-recovery block, unrelated to the idle-gate predicates.) Add a docstring note: "Defensive predicate — INV-5 closes the root cause via post-commit `AbortTurn(reason='failed')` reconcile on resume; this clause remains as defense-in-depth. Removal deferred until INV-5 ships to ≥1 production cycle with zero stuck-Task reports." | A'1r–A'4 | Docstring added at the correct file:line refs; no predicate removal. |
| A'6r | Write `tests/unit/repositories/task/test_task_jobitem_reconcile_resume.py` covering the new contract: **(a)** Resume of a paused Task with a dead JobItem marks the Task `CANCELLED` via `AbortTurn(reason='failed')` **post-commit** (rollback case: simulate a failure in the reconcile path — assert the Task remains `paused`); **(b)** `AbortTurn` writes are reflected across the 8 mirror tables (assert each of the 8 mirror tables reflects the terminal status — pins the named-transition authority); **(c)** `emit_terminal` fires — `dependency_watchers` rows for the swept Task transition out of `PENDING` (asserts the dependency-bus wiring; no idle-gate deadlock); **(d)** sweep test: 5 paused Tasks, 2 with dead JobItems, 3 with live JobItems — assert only the 2 are swept to `CANCELLED` via `AbortTurn` (not raw UPDATE); **(e)** `SKIP LOCKED` candidate query excludes rows currently held by another transaction under PG (dialect-matrix test); **(f)** defensive predicates still hold (regression guard at the correct file:line refs). | A'1r–A'5 | All test cases PASS under `concurrency_atomic_unit_test`. |

**Sub-slice A' exit criterion**: `AbortTurn(reason='failed')` post-commit reconcile on resume works; `SELECT … FOR UPDATE SKIP LOCKED` sweep invokes the named transition per row (no raw UPDATE); `emit_terminal` fires (no `dependency_watchers` deadlock); defensive predicates documented at the **correct** file:line refs; test coverage spans (a) reconcile-on-resume, (b) rollback case at the post-commit boundary, (c) sweep selectivity, (d) 8-mirror-table consistency, (e) `dependency_watchers` cancellation, (f) defensive-predicate preservation. INV-5 finalized — `AbortTurn` + post-commit-reconcile vocabulary is now frozen for Phase 4 INV-13 (no longer a hard INV-5 gate; vocabulary freeze replaces it — see Dependency Notes §Downstream).

---

## Dependency Notes

### Upstream (Phase 1)

- **INV-4 (sub-slice A) is gated on Phase 1 INV-1 landing**. INV-1 removes the silent exception swallow at `job_processor.py:1263-1271`; once removed, hidden failures (including SKIP-path contention) become visible. Running INV-4 before INV-1 merges risks the new metric (`skip_backoff` log line) being itself swallowed by the unfixed inner `except Exception: pass`. Sequence: Phase 1 closes → sub-slice A starts. The dispatcher MUST confirm INV-1 commit SHA on `feature/schedule-review-improve` HEAD before A1 begins (task A5).

### Downstream (Phase 4)

- **INV-5 ↔ INV-13 vocabulary freeze, NOT a hard gate** (architect §5; decisions D7.7). The original `decisions.md §D2` claim "INV-13 must follow INV-5" was based on a now-stale premise (architect §2c: the three call-sites — `cancel_task`, `complete_task`, `fail_task` — are ALREADY migrated to named transitions; see `repository.py:3102, 1746, 1874` calling `transition._write()` per `:1813-1827, :1930-1941, :3169-3175`; docstrings say "THIN WRAPPER"). With INV-13 re-scoped to **verify-and-document + regression pin**, the constraint becomes: the **named-transition + post-commit-reconcile vocabulary** is frozen before both INV-5's redesigned sweep (A′3r) and Phase-4 INV-13-as-verification land — NOT that INV-5 finishes first. **No sequencing edge; INV-13 can run in parallel with INV-12 / INV-14 / Phase-3 INV-9 + INV-11.**

### Lateral (Phase 3)

- **No lateral coupling from Phase 3 into Phase 2.** Per `decisions.md §D5`, Phase 3 INV-9 and INV-11 are test-after mixes — they read Phase 1 + Phase 2 commits but do not feed back. INV-10 (rate-limiter) is fully parallel and orthogonal.

### Within Phase 2

- **INV-6 ⟶ INV-5 hard edge DISSOLVES** (architect §5). The soft same-file ordering inside the repo-worker (B before A′) is preserved as a hygiene convention; the redesign of A′3r does not consume B's `_age_seconds_sql` helper (the sweep is age-free NOT-EXISTS).
- **INV-4 ∥ INV-6 ∥ INV-7 ∥ INV-8** (sub-slices A ∥ B ∥ C ∥ D): four parallel workers on disjoint files. Per the parallelization discipline above, each commits to a separate commit on `feature/schedule-review-improve`. **Stagger `concurrency_atomic_unit_test` runs via dispatcher-level queue** (13 files, 280s internal cap, timing-sensitive `gate_threading_serialization` — self-contained tmp fixtures but contending).

---

## Coupling

- **Tight with Phase 1 (INV-1)**: INV-4 (sub-slice A) — Phase 1 INV-1 must land before A1 starts. The semantic reason: INV-1's swallow fix is the prerequisite for INV-4's new metric being observable.
- **Loose (vocabulary freeze) with Phase 4 (INV-13)**: INV-5 (sub-slice A') — both INV-5's redesigned sweep and Phase-4 INV-13-as-verification use the same `AbortTurn(reason='failed') + reconcile_turn_mirror + emit_terminal` vocabulary (architect §5; decisions D7.7). The constraint is doc-level (vocabulary frozen before either lands), NOT a hard phase gate. INV-13 can run ∥ INV-5.
- **Loose with Phase 3 (INV-9, INV-10, INV-11)**: Phase 3 tests read Phase 2 commits for assertions on the fixed paths. No code coupling; pure test-after mix per `decisions.md §D5`.
- **Independent of**: INV-12 (Phase 4 dead-code removal in `job_feedback_observer.py`), INV-14 (Phase 4 docstring fix in `job_state_machine.py:3`), INV-13 (Phase 4 verification + regression pin; vocabulary freeze above).
- **Pre-flight (not Phase 2)**: INV-15 quarantine of `mock_job_queue_test` pack — runs BEFORE Phase-1 dispatch (architect §5; decisions D7.7); no Phase 2 dependency.

### Intra-Phase Coupling Matrix

|         | INV-4 (A) | INV-5 (A') | INV-6 (B) | INV-7 (D) | INV-8 (C) |
|---------|-----------|------------|-----------|-----------|-----------|
| INV-4   | —         | independent | independent | independent | independent |
| INV-5   | independent | — | **independent (D7.7 — soft same-file order only; A′3r is age-free)** | **independent (D7.2 — `orphaned_no_task` never enters the map; semantic coupling dissolved)** | independent |
| INV-6   | independent | independent | — | independent | independent |
| INV-7   | independent | independent | independent | — | independent |
| INV-8   | independent | independent | independent | independent | — |

> **Coupling amendments (architect §2b §5; decisions D7.2, D7.7):** (i) the A′↔B coupling is dissolved — A′3r no longer consumes B's `_age_seconds_sql` (the sweep's NOT-EXISTS filter is age-free); soft same-file order is preserved as a hygiene convention only; (ii) the A′↔D semantic coupling is dissolved — the redesigned A′3r uses `AbortTurn(reason='failed')` (canonical in `_STATUS_CANONICAL_MAP`), so the shared vocabulary contract that previously made A′ and D "tight" no longer exists; sub-slices D and A′ are now genuinely independent.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | INV-4 set-aside window constants (`base_ms=50`, `cap_ms=2000`) cause queue throughput regression on hot queues before empirical tuning | Medium | Medium | Constants flagged `# TODO(post-deploy-tune)` per dispatch note "known gap"; metric (`skip_backoff` log line) gives ops signal; ship with defaults + monitor |
| 2 | INV-5 sweep fires too aggressively (every Nth resume) and contends with active JobItem state under load | High | Low | A'4 invokes sweep every 10th resume by default; bounded by `limit=100`; non-blocking via `asyncio.create_task`; defaults flagged for empirical tuning |
| 3 | INV-5's repository-side sweep blocks on `dependency_bus.emit_terminal` under `dependency_watchers` write contention, slowing the resume path | Medium | Low | A'3r invokes the named transition (which calls `emit_terminal` as part of `transition._write()`); sweep is async-fired via `asyncio.create_task` and bounded by `limit=100`; if `dependency_bus` contention surfaces, follow-up Cycle-3 work decouples the emit from the critical path |
| 4 | INV-6 dialect-aware helper breaks in mixed environments (e.g., test runs against PG but dialect detection returns SQLite) | Medium | Low | B1 helper reads `self._dialect` consistently with existing repository methods; B6 dialect-matrix test catches divergence at PR time; each test asserts non-`Decimal` returns |
| 5 | INV-8 high-concurrency stress test (100 goroutines, **lowered from 200**) is flaky on underprovisioned CI runners, causing intermittent pack failures | Medium | Medium | C2 stress test runs under the 5-min pack timeout; failure order is seeded for reproducibility; if flaky, mark `xfail(strict=False)` with link to re-tune; lower concurrency to 50 if needed |
| 6 | INV-5 documentation of defensive predicates (`has_active_non_deferred_work` etc.) at the **correct** file:line refs (`task/repository.py:2199, 2430`; NOT-EXISTS `:2519-2570`) gets misread as "remove them" by a future reviewer | Low | Medium | A'5 docstring explicitly states "defense-in-depth, removal deferred until INV-5 ships to ≥1 production cycle with zero stuck-Task reports"; cross-reference `decisions.md §D2` (now D7.6); the path typo from the original plan (`instance_lifecycle.py:2474-2518`) is fixed in this amendment |
| 7 | Four parallel workers create commit ordering that hides the soft same-file B→A′ ordering (reviewer sees A′ commit before B in `git log`) | Low | Medium | Parallelization discipline section mandates "commit to separate commits" but the repo-worker MUST reference the B commit SHA in the A′ commit message body (`Refs: <B-SHA>`) for reviewer traceability. The hard INV-6→INV-5 edge is dissolved (D7.7); the soft same-file order is hygiene, not correctness |
| 8 | INV-7 unknown-admission fallback telemetry leaks PII if `admission_state` / `terminal_reason` carry user-derived content | Low | Low | D2 structured log uses `extra={...}` and avoids string interpolation in the message; values restricted to the enum members and job/work UUIDs |
| 9 | INV-4 lease re-entry mechanics (how a set-aside queue rejoins the scan after window expiry) varies by implementation and may not match the per-cycle SKIP counter semantics assumed by operators | Medium | Low | Direction is firm (per-queue state + set-aside list); re-entry trigger (lazy scan-entry check vs. event-driven wake) is an open question flagged for the executing worker per architect §10 |

---

## Verification (e2e Gate per `.agents/tester/rules/ensure.md`)

Quoting `.agents/tester/rules/ensure.md`:

> Pack-mapped: requirements reference packs in PACKS.md (or static checks), NOT bare `pytest` commands.
> Scoped by blast radius: validate only requirements relevant to the change set.
> Run as packs: every validation executes as a pack (or ad-hoc pack) with the dual-layer 5-min timeout — NEVER as a bare, unbounded `pytest` command.
> Quarantine-aware: tests in `.agents/tester/QUARANTINE.md` are skipped and do not fail a requirement.
> No `-x`: never use pytest `-x` (stop-on-first-failure) for suite runs.

### Gate Selection Per INV

| INV | E2E Gate | Reason (from research-findings.md) |
|-----|----------|------------------------------------|
| INV-4 | **Release gate** (Core + Release) | Touches `claim_pending_task` (SKIP path) — listed in gate |
| INV-5 | **Release gate** (Core + Release) | Touches `reconcile_turn_mirror`, `turn_transitions`, `instance_lifecycle` |
| INV-6 | **Core gate** (repo pack) | `task/repository.py` only — outside gate surface |
| INV-7 | **Core gate** | `work_status.py` only |
| INV-8 | **Core gate** (source pack) | `circuit_breaker.py` only |

### INV-4 + INV-5 — Full Core + Release Gate (per `ensure.md`)

#### Core gate (always-on)

- [ ] No regressions in changed packs — `concurrency_atomic_unit_test`, `claim_guard_locks_unit_test`, `admission_starvation_unit_test` return PASS
  - Validation: `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` (or the matching pack wrapper); ALL PASS
- [ ] Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS (includes `test_deadlock_fix.py`, cascade races, observer race, instance/project atomic locks)
  - Validation: pack PASS (same invocation as above)
- [ ] No sync DB calls on the asyncio event loop — covered by `concurrency_atomic_unit_test` (thread-identity tests verify `asyncio.to_thread` wrapping for all DB helpers)
  - Validation: pack PASS
- [ ] `dev.sh` includes `--timeout-graceful-shutdown 10`
  - Validation: static file check (grep `dev.sh`) — fast, no pytest
- [ ] All callers of converted async functions properly await (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`)
  - Validation: grep / static check — INV-4 + INV-5 do not introduce new awaitable sites, so the existing inventory must remain clean
- [ ] Original deadlock scenario (parent→child→complete) works without blocking
  - Validation: covered by `concurrency_atomic_unit_test`

#### Release gate (big/critical — INV-4 + INV-5 qualify)

Prerequisites (per `ensure.md`):
- Daemon running: `./dev.sh` (health at `localhost:8079`)
- SSL certs clean: `unset SSL_CERT_FILE SSL_CERT_DIR` before each run
- Timeout override: `PYTEST_TIMEOUT=280` + `--override-ini="timeout=280"` (pyproject default `timeout=30` kills E2E prematurely)
- **Queue cleanup before each test**: check `GET /api/jobs?status=pending` for leftover jobs; clean any up before running (leftover pending jobs block defer queue admission and cause false failures)
- Run tests **one by one** (each makes real LLM calls; combined exceeds 5-min cap)

Items:
- [ ] Full non-integration suite green (excluding `.agents/tester/QUARANTINE.md`)
  - Validation: run ALL non-integration packs (see PACKS.md) in parallel, each with the 5-min cap; quarantined tests skipped. NOT a bare `pytest tests/` — run via the packs.
- [ ] E2E: Normal parent→child workflow completes (happy path)
  - Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` or `PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_parent_child_workflow_happy_path" --tb=short -q`
- [ ] E2E: Pause after spawn, then resume works correctly
  - Validation: same pattern, `-k "test_pause_after_spawn_then_resume"`
- [ ] E2E: Terminate after spawn, then revive documented
  - Validation: same pattern, `-k "test_terminate_after_spawn_then_revive"`
- [ ] E2E: 3-level cascade (leader→tester→staggered workers): reports delivered, no premature completion, no stuck completion, state switching
  - Validation: same pattern, `-k "test_three_level_cascade_reports"`

### INV-6, INV-7, INV-8 — Core Gate Only (Scoped Packs)

#### Core gate

- [ ] No regressions in changed packs — scoped to repo / work-status / source packs:
  - INV-6: `concurrency_atomic_unit_test` (repo-tier threads) + dialect-matrix test from B6 PASS
  - INV-7: `work_status_unit_test` (or the matching pack covering `daemon/services/work_status.py`) PASS
  - INV-8: `concurrency_atomic_unit_test` (source-tier threads) + new circuit-breaker invariant tests from C1-C2 PASS
- [ ] Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS (where repo/queue touched: INV-6 + INV-8; INV-7 is pure function — skip)
  - Validation: `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` for INV-6 + INV-8
- [ ] No sync DB calls on the asyncio event loop — covered by `concurrency_atomic_unit_test` (INV-6 + INV-8)
- [ ] `dev.sh` includes `--timeout-graceful-shutdown 10` — static check
- [ ] All callers of converted async functions properly await — INV-6 + INV-8 do not introduce new awaitable sites; INV-7 pure function — no change
- [ ] Original deadlock scenario (parent→child→complete) works without blocking — covered by `concurrency_atomic_unit_test` (INV-6 + INV-8)

### Pack Discipline (all five INVs)

- Every validation executes as a pack (or ad-hoc pack) with the dual-layer 5-min timeout — NEVER bare `pytest`.
- No `pytest -x` anywhere.
- Pre-existing failures (`.agents/tester/QUARANTINE.md`) are skipped and do not fail a requirement. **INV-15 quarantine is owned by Cycle Pre-flight (HOISTED per architect §5; decisions D7.7) — this phase does NOT add to QUARANTINE.**
- After every `edit_file` batch, re-verify the target pattern with grep/sed (per `.agents/shared/conventions.md` and Repo & Dev Environment Conventions blueprint).

### Per-Worker Validation Sequence

For each sub-slice worker (A, B, C, D, A'):

1. Run scoped Core gate packs — all PASS.
2. Run any new tests added by the worker (`tests/unit/.../*.py`) under the relevant pack wrapper — all PASS.
3. Run full Core gate for the worker's tier — all PASS.
4. For A and A' (INV-4 and INV-5): also run Release gate (one-by-one E2E pattern above) — all PASS.
5. `git diff --stat feature/schedule-review-improve` shows the expected file surface only; no scope creep.

---

## Phase 2 Exit Criterion

Phase 2 closes when **all five conditions hold**:

1. INV-4, INV-5, INV-6, INV-7, INV-8 each have a passing test or static-check proof at the e2e gate level their tier requires (Core / Core+Release per the table above).
2. INV-5 finalized: redesigned `AbortTurn(reason='failed')` post-commit reconcile on resume in `_resume_cascade_db_sync`; `SELECT … FOR UPDATE SKIP LOCKED` candidate-query sweep invoking the named transition per row; defensive-predicate docstring added at the **correct** file:line refs (`task/repository.py:2199, 2430`, NOT-EXISTS defense `:2519-2570`); tests cover (a) reconcile-on-resume, (b) rollback case at the post-commit boundary, (c) sweep selectivity, (d) 8-mirror-table consistency, (e) `dependency_watchers` cancellation via `emit_terminal`, (f) defensive-predicate preservation at the correct file:line refs.
3. INV-6 finalized: `_age_seconds_sql` helper exists with full docstring (session-TZ invariant + julianday millisecond precision + `Decimal`-coercion warning); four call sites migrated; dialect-matrix tests pass and assert non-`Decimal` returns; grep clean of naive-TIMESTAMP age math on those sites; **recommended follow-up (not in-cycle, document only):** add `SET TIME ZONE 'UTC'` at connection time to make the session-TZ invariant contractual.
4. INV-7 finalized: validation branch + telemetry + 4-case truthy-table test (canonical reasons only — `cancelled`, `failed`, `completed`, `timed_out`; **`orphaned_no_task` is NOT in the list**) in place; F16 truthy-check behavior preserved.
5. INV-8 finalized: `reset()` invariant + 4 invariant tests (including the NEW "sibling-failure does not free the probe slot" test) + 100-goroutine seeded stress test + `__debug__` assertion at the THREE REAL state sites (`:63, :91, :107`) in place.

**Phase 2 closes independent of Phase 4 INV-13.** The original "INV-5 finalization unblocks INV-13" clause is DELETED (premise false — architect §2c; decisions D7.7). The new constraint is a vocabulary freeze: both the redesigned A′3r sweep and Phase-4 INV-13-as-verification use the same `AbortTurn(reason='failed') + reconcile_turn_mirror + emit_terminal` shape; the dispatcher freezes the vocabulary before either lands (doc-level concern, not a phase-gate).

---

## Per-Sub-Slice Commit Hygiene

Per Repo & Dev Environment Conventions blueprint and the multi-edit write-verification discipline:

- Each sub-slice commits a separate change on `feature/schedule-review-improve`.
- INV-5 commit message body MUST reference INV-6's commit SHA (`Refs: <INV-6-SHA>`) so reviewers see the dependency even though the commits may interleave with other parallel work.
- Default INV-6 commit message subject: `INV-6: SQL-side DB-time convention in task/repository.py (4 sites)`.
- Default INV-4 commit message subject: `INV-4: per-queue SKIP counter + jittered backoff in job_processor`.
- Default INV-5 commit message subject: `INV-5: resume-transition reconciliation + stuck-Task sweep`.
- Default INV-7 commit message subject: `INV-7: terminal_reason validation + unknown-admission telemetry in work_status`.
- Default INV-8 commit message subject: `INV-8: circuit-breaker invariant tests + debug assertion`.
- After every `edit_file` batch, re-verify the target pattern with grep/sed; on silent edit failure, re-apply or fall back to a small python heredoc read-modify-write.
- Final test suite runs after the last edit; final `git diff` is read before reporting.

---

## References (Source-of-Truth Locations)

- Frozen inventory + line refs: `research-findings.md` Partition 1 (INV-4), Partition 2 (INV-5/6/7), Partition 3 (INV-8); Evidence Index row for each INV.
- Architectural decisions: `decisions.md §D2` (INV-13 bounded — superseded by D7.3/D7.7), `decisions.md §D5` (test-after mix), `decisions.md §D7` (cycle-2 amendments — **D7.2 canonical-map `reason='failed'`**, **D7.3 INV-13 re-scoped to verify-and-document**, **D7.4 INV-6 session-TZ invariant + now-aware UTC**, **D7.5 INV-4 per-queue lease**, **D7.6 INV-5 redesign: AbortTurn + SKIP-LOCKED sweep**, **D7.7 sequencing dissolution + INV-15 hoisted**).
- Amendment authority: `architecture-recommendation.md §1b (INV-4), §2a/§2b/§2c (INV-5/INV-7 coupling/INV-13), §3b (INV-8), §5 (sequencing), §6 (INV-6), §8 (checklist items 2/3/4/6/8/10/11/12)`.
- Coupling: `plan-overview.md §Phase Index` and `§Coupling Map`.
- Tier mappings: `plan-overview.md §Phase Index` row for Phase 2; e2e-gate applicability table in `research-findings.md §Cross-Partition Synthesis`.
- Quality rules: `.agents/tester/rules/ensure.md` (Core gate + Release gate, pack discipline, no `-x`, timeout override).

---

## Open Questions

The following items are flagged for the executing worker (direction is firm; the details are implementation-level):

1. **INV-4 lease re-entry mechanics** (architect §10): how a set-aside queue rejoins the scan after its window expires. Direction is firm (per-queue state + set-aside list + outer `_process_loop` continues other queues); the re-entry trigger is an open implementation choice — lazy at scan-entry check vs. event-driven wake. Decide at A2 implementation time based on `JobProcessor._process_loop` structure.
2. **INV-6 `SET TIME ZONE 'UTC'` recommendation** (architect §6): documented in the helper docstring as a recommended follow-up; **not in-cycle**. Out of Cycle 2 scope; flag for Cycle-3 if session-TZ correctness becomes a hot spot.
3. **INV-3 `_run_start_time` object identity** (architect §3a secondary check, OUT OF PHASE 2 — proxy only here): whether the attribute lives on the adapter or the supervisor record; depends on adapter-restart object identity. Out of Phase 2 scope (INV-3 is Phase 1); cross-reference Phase 1 plan.

**Resolved by amendment (no longer open):**
- D2 INV-13 ↔ INV-5 hard gate — DISSOLVED (architect §5; D7.7); replaced by vocabulary freeze (doc-level, no phase gate).
- D0 canonical-map micro-commit for `orphaned_no_task` — NOT NEEDED; redesigned sweep uses `reason='failed'` (canonical) per D7.2.
- Idle-gate path typo (`task/repository.py:2199, 2430`; NOT `instance_lifecycle.py:2474-2518`) — FIXED in A'5 acceptance criteria.