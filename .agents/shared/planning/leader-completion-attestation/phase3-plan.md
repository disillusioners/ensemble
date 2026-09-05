# Phase 3: Ledger + Bound + Escalation (no recovery injector)

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (revised in reconciliation pass)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase1-plan.md`](./phase1-plan.md), [`phase2-plan.md`](./phase2-plan.md), [`phase4-plan.md`](./phase4-plan.md), [`phase5-plan.md`](./phase5-plan.md), [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md), [`research-findings.md`](./research-findings.md)

---

## Objective

Ship the **per-instance denied-count ledger** (DB columns `attestation_denied_count`, `completion_gate_escalated`), **reset-on-allow semantics** (architect addition; row-scoped columns survive revive), **bound enforcement** (default 3), **terminal fallback** (allow + `completion_gate_escalated=true` flag + `gate_terminal_after_bound` event when bound exceeded), and **C3 fail-open at the ledger DB seam**. **NO recovery injector** — that work is RELOCATED to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) per R1/C1b (forbidden dual-delivery; the MVP deny path is the in-graph nudge). This phase is **D1-independent** for the trunk but **D5-dependent** for the bound default.

Entry criterion: Phase 2 (gate decision signature + `manager.count_pending_children(instance_id)` + `manager.get_queued_or_expected_wakeups(instance_id)` facade methods) is stable; D5 is decided by the architect. Default behavior on unresolved: counter = DB column on instance row (`attestation_denied_count`); bound = 3; reset triggers (per the leader ruling, CLOSED-by-leader 2026-09-05 — four triggers ONLY, supersedes prior wording including the removed "instance-revive-from-TERMINATED") = (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation; terminal fallback = flag-only + log event + counter reset.

Exit criterion: AC-4.1–AC-4.4 (MVP, verified in Phase 5); AC-4.5 (durable path) relocated to Phase 6. AC-6.1 (counter increments on deny), AC-6.2 (allow at bound: terminal + escalation event + flag + counter reset), AC-6.3 (no infinite loop), AC-6.4 (fresh instance starts with `attestation_denied_count = 0`), AC-6.5 (counter resets to 0 on **attested allow only** — leader ruling 1 supersedes the prior "every allow" wording; ``allowed_legitimate_pending_wakeup`` MUST NOT reset) all pass; C3 fail-open at the ledger DB seam verified.

---

## Entry Criteria

- Phase 2 (gate decision signature + `manager.count_pending_children(instance_id)` + `manager.get_queued_or_expected_wakeups(instance_id)` facade methods) is stable
- D5 (retry bound, counter location, terminal fallback, reset triggers) is decided by the architect
- Default behavior on unresolved: counter = DB column `attestation_denied_count` on instance row; bound = 3; reset triggers (per the leader ruling, CLOSED-by-leader 2026-09-05 — four triggers ONLY, supersedes prior wording) = (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation (column default 0); terminal fallback = flag-only + log event + counter reset

---

## Tasks

> **RE-SCOPE 2026-09-05**: this phase ships **ledger + bound + escalation ONLY**. The recovery injector (formerly task 3.1) and the D1=C sweep backstop (formerly task 3.6) are RELOCATED to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md). The crash-recovery test (formerly 3.7) and JAFP compliance test (formerly 3.8) follow the recovery injector to Phase 6. The in-memory-dict cleanup task (formerly task 3.4, modeled on `_loop_breaker_state` precedent) is **DROPPED** per C1c — row-scoped DB columns do not need in-memory pop cleanup (the `_loop_breaker_state` precedent is for in-memory dicts, not row-scoped columns). New tasks added: **3.3 Reset triggers + O2 enumeration** and **3.6 C3 fail-open at DB seam**.

### 3.1 — (RELOCATED — Recovery injector) Durable `manager.enqueue_message` recovery

> **RELOCATED to Phase 6 (fast-follow).** The MVP deny path is the in-graph nudge (R1). The durable `manager.enqueue_message` recovery injector (D6: `source="attestation_recovery"`, JAFP no-JobItem, facade-forwarding tests, crash-recovery chaos test) ships with Phase 6 as a post-soak backstop. DO NOT IMPLEMENT IN PHASE 3. See [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) for full spec.

### 3.2 — Schema migration: add `attestation_denied_count` + `completion_gate_escalated` columns to instance row

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/migrations/2026XXXX_xxxxxx_attestation_columns.py` (new); `daemon/repositories/instance/` |
| **Description** | Add two columns to the `instances` table: `attestation_denied_count: int = 0` (counter; persists across revives); `completion_gate_escalated: bool = False` (terminal-after-bound flag; persists for postmortem). **DB column semantics** (not in-memory dict): the row survives revive, so the counter MUST be reset via DB UPDATE at every reset trigger (Phase 3 task 3.3). New Alembic / SQL migration per the existing migration discipline (`daemon/migrations/` ordered + checksummed + transactional). PG+SQLite-safe (fresh-SQLite boot trap is a live hazard — see LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md; avoid `DROP CONSTRAINT IF EXISTS` patterns that broke under SQLite). |
| **Decision tags** | [D5] (counter location: DB column — RESOLVED) |
| **Test notes** | Migration test applies + rolls back on both PG and SQLite; integration test asserts columns exist after migration; test asserts default values (`0` / `False`) on existing instance rows. |

### 3.3 — Implement the attempt ledger repository methods + reset triggers (O2)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/repositories/instance/repository.py` |
| **Description** | Four methods: (a) `increment_attestation_denied_count(instance_id, denial_epoch) -> int` (idempotent upsert — O4: `INSERT ... ON CONFLICT (instance_id, denial_epoch) DO UPDATE SET count = count + 1` keyed by `(instance_id, denial_epoch)` where `denial_epoch` is a monotonic counter incremented on each new leader mission / revive); (b) `reset_attestation_denied_count(instance_id) -> None` (set to 0); (c) `set_completion_gate_escalated(instance_id) -> None` (terminal-after-bound marker, persists for postmortem); (d) `get_attestation_denied_count(instance_id) -> int`. **Reset triggers (O2 — per the leader ruling, CLOSED-by-leader 2026-09-05, FOUR triggers ONLY — supersedes prior wording including the removed "instance-revive-from-TERMINATED")**: `attestation_denied_count` resets to 0 on EXACTLY the following events — the in-graph deny-nudge is NOT a reset (it is the loop-protection reason the counter accumulates within a mission): **(1) attested allow** — counter increments only on deny; allow-with-attest resets to 0 (architect addition; without reset, a revived leader's next mission starts pre-burdened). Wired via `reset_attestation_denied_count(instance_id)` in the gate node on `Decision.allowed`. **(2) `terminal_after_bound` finalization** — counter resets to 0 alongside setting `completion_gate_escalated=true` (otherwise an escalated→revive→next-mission insta-escalates because the bound check fires immediately on the first new-mission deny). Wired via `reset_attestation_denied_count(instance_id)` in the gate node on `Decision.terminal_after_bound`. **(3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode)** — fires at instance-state transition (NOT inside the gate node), invoked from the `send_message`-revive path per `daemon/services/instance_messaging.py:1867-1909`. The "fresh episode" qualifier is what distinguishes this trigger from a generic revive; only user-driven re-dispatch (new mission message) resets the counter, NOT a routine checkpoint reload. **(4) instance creation** — fires at instance-row creation; the migration default value `attestation_denied_count: int = 0` (per Phase 3 task 3.2) implements this trigger. **Drift disclosure (per leader ruling)**: the previous enumeration named "instance-revive-from-TERMINATED" as a trigger — REMOVED. Instance creation is ADDED. Counter does NOT auto-reset on PAUSED → RUNNING or on checkpoint reload (per leader ruling). The actual `_loop_breaker_state.pop` sites are at `daemon/manager.py:3734/:3798/:8548` (3 sites — NOT 5; architect correction); those are IN-MEMORY dict cleanup and are **NOT a precedent** for row-scoped column resets. **O3 — stale pre-revive attestation watermark diagnostic**: `get_attestation_denied_count` returns the persisted count; the gate node's `evaluate()` (Phase 2 task 2.3) computes `attest_seen_outside_window: bool` (was the tool call present anywhere in the full message list but NOT in the last N?) and emits this in the log schema (Phase 4 task 4.5) for O3 diagnosis. **O4 — per-denial-epoch idempotent upsert (CHOSEN)**: `increment_attestation_denied_count(instance_id, denial_epoch)` is idempotent across concurrent deny paths — keyed by `(instance_id, denial_epoch)`. Documented inflation alternative (NOT chosen): a naive `UPDATE count = count + 1` may double-increment under concurrent deny paths during a pause-mid-gate cycle, causing the counter to exceed the bound within a single cycle but never causing a job strand. The chosen approach is preferred for predictable behavior. Each method uses defense-in-depth WHERE clauses to avoid clobbering concurrent normal completions. |
| **Decision tags** | [D5] (counter behavior on success, on `terminal_after_bound`, on revive; counter location), [O2] (reset-site enumeration), [O3] (stale watermark diagnostic), [O4] (idempotent upsert CHOSEN; inflation documented as fallback) |
| **Test notes** | Unit test `tests/unit/test_attestation_ledger.py` exercises (per the leader ruling, CLOSED-by-leader 2026-09-05 — triggers enumerated to match the canonical four): (a) atomicity — concurrent updates on the same `(instance_id, denial_epoch)` do not lose increments (idempotent); (b) increment; (c) reset on **attested allow** (trigger #1); (d) reset on `terminal_after_bound` finalization (trigger #2); (e) reset on **revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode)** (trigger #3); (f) reset on **instance creation** via column default (trigger #4); (g) no-reset on PAUSED → RUNNING AND on checkpoint reload (per leader ruling); (h) reset op clears BOTH `attestation_denied_count` AND `completion_gate_escalated` in one UPDATE (leader ruling 2 — both columns share the per-mission lifecycle); (i) integration test asserts counter survives revive and is reset correctly. |

### 3.4 — (DROPPED — C1c) In-memory-dict ledger cleanup

> **DROPPED 2026-09-05 per C1c (architect correction).** The cleanup task previously referenced the `_loop_breaker_state` in-memory dict (`daemon/manager.py:3734/:3798/:8548` — actual count is **3 sites**, not the older imprecise framing). In-memory dict cleanup is NOT a precedent for row-scoped DB column resets — the columns survive revive and require DB UPDATE to reset. The reset triggers are specified in Phase 3 task 3.3 (O2 enumeration — superseded by the leader ruling 2026-09-05): four triggers ONLY — (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation. There is no separate cleanup task modeled on the `_loop_breaker_state` precedent. **No grep residue for that prior task framing should remain.**

### 3.5 — Wire the gate → ledger call (no recovery injector)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (Phase 2 file, extended); `daemon/repositories/instance/repository.py` (Phase 3 file) |
| **Description** | The gate's `evaluate()` function (Phase 2 task 2.3) returns a `Decision`. On `denied` decision (canonical enum per Phase 4 task 4.5), the gate node calls `increment_attestation_denied_count(instance_id, denial_epoch)` (Phase 3 task 3.3) and **injects the in-graph nudge** (Phase 2 task 2.5). On `terminal_after_bound`, the gate node calls `set_completion_gate_escalated(instance_id)` AND `reset_attestation_denied_count(instance_id)` (O2 reset trigger #2). On `allow-with-attest`, the gate node calls `reset_attestation_denied_count(instance_id)` (O2 reset trigger #1). On `mode="dry"`, NO counter change (zero side effects). On `mode="off"` or `attestation_enabled=False`, NO counter change. The order of operations (per leader ruling 1, SUPERSEDES the prior prose that listed `allowed_legitimate_pending_wakeup` under both "reset counter" and "no counter change"): (1) gate evaluates → decision; (2) if `denied` → increment counter → inject in-graph nudge; if `terminal_after_bound` → set escalation flag + reset counter (atomic single UPDATE — leader ruling 2) → proceed to terminal (the original `should_continue` returns END); if `allow-with-attest` (canonical `Decision.ALLOWED` with `attestation_present=True` per the scanner verdict) → reset counter → proceed to terminal; if `allow-without-attest` (R2: pending children / wakeups pending — canonical `Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`) → **NO counter change** (leader ruling 1: this non-reset IS the loop protection) → proceed to terminal. **No `manager.enqueue_message` is called from any path here** (R1/C1b). **Reset-on-allow DB-write tradeoff (resolves the yellow-note ambiguity on phase3:50/:63) — pick ONE and note the tradeoff vs NFR-1 P95 20 ms**: **(a) synchronous DB UPDATE in the routing hot path** (CHOSEN — implemented Phase 3 task 3.5) — the reset-on-allow DB UPDATE runs synchronously via the gate node's `safe_reset` wrapper; on DB error the wrapper emits `event=leader_completion_gate_db_error` and the gate's deny/terminal outcome degrades to `allow` per C3/AC-6.6 (the leader mission NEVER errors). The choice is synchronous because (i) the in-graph nudge path is already on the routing hot path, so the reset piggybacks on existing latency budget; (ii) the documented bounded risk (extra increment carries into the next mission) is acceptable — the next allow resets it again, and the recovery injector (Phase 6) is the durable backstop. **(b) fire-and-forget with idempotency** (REJECTED — would require a thread + queue and add operational complexity for a sub-20 ms win; the fail-open synchronous path is sufficient). | — schedule the DB UPDATE asynchronously via a daemon-internal background task AFTER the allow write commits; the reset is idempotent (`UPDATE instances SET attestation_denied_count = 0 WHERE instance_id = X` is naturally idempotent); a crash between the allow commit and the reset leaves the counter stale — bounded risk because the next deny+allow cycle resets it, and the gate's `attestation_denied_count < bound` check tolerates a stale count up to `bound`. **The MVP ships with (a) + benchmark gate**; (b) is the explicit fallback if the benchmark fails. NFR-1 (P95 20 ms) is the binding constraint; either choice must satisfy it (with (a) the DB UPDATE must be ≤ 5 ms under nominal PG load; with (b) the scheduling overhead must be ≤ 1 ms). |
| **Decision tags** | [D5], [R2], [R1] (in-graph nudge only; no enqueue), [C3] (fail-open at the ledger DB seam — see task 3.6) |
| **Test notes** | Integration test `tests/integration/test_attestation_ledger_flow.py` (Phase 5) verifies the full sequence: (a) deny path increments + nudges + does NOT enqueue; (b) `terminal_after_bound` sets flag + resets counter + proceeds to terminal; (c) allow-with-attest resets counter; (d) allow-without-attest (R2) does NOT change counter; (e) mode="dry" does NOT change counter. |

### 3.6 — C3 fail-open wrapper at the ledger DB seam

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (Phase 2 file); `daemon/repositories/instance/repository.py` (Phase 3 file) |
| **Description** | The W4 precedent at `graph.py:2663-2688` uses a narrow exception set that does NOT cover SQLAlchemy `OperationalError` (connection drop, deadlock, etc.). The C3 fail-open wrapper at the **ledger DB seam** widens to `except Exception`: any exception in `increment_attestation_denied_count` / `reset_attestation_denied_count` / `set_completion_gate_escalated` ⇒ (a) the gate's deny/terminal decision is preserved as a LOG-ONLY outcome (no DB write, no nudge injection in dry; in enforce, the gate falls through to `allow` rather than failing the leader); (b) structured error log `event=leader_completion_gate_db_error` with `instance_id`, `error_class`, `error_message`. The fail-open at this seam is in ADDITION to the fail-open around the scanner/gate itself (Phase 2 task 2.3). **Ordering**: DB write is wrapped in `try/except Exception`; on success the deny proceeds normally; on failure the deny becomes an `allow` with an error log. This guarantees one scanner bug or one transient DB error does NOT error every leader mission (D2's outage class). |
| **Decision tags** | [C3] (fail-open at ledger DB seam; widens W4 precedent) |
| **Test notes** | Integration test `tests/integration/test_attestation_fail_open_db.py` (Phase 5): inject a SQLAlchemy `OperationalError` on `increment_attestation_denied_count`; assert (a) deny decision becomes allow (no nudge injected, no terminal transition denied); (b) `event=leader_completion_gate_db_error` log line emitted; (c) leader mission does not error. |

---

## Coupling

- **Tight with:** Phase 2 (gate reads/writes counter via the ledger repository; gate→reset path on allow; C3 fail-open wrapper around the gate→ledger call); Phase 4 (counter is the source for observability events; O3 `attest_seen_outside_window` field).
- **Loose with:** Phase 6 (counter feeds the durable recovery injector's bound check; sweep reads the same columns).
- **Independent of:** Phase 1 (recovery text is referenced in the prompt contract, but Phase 6 owns the constant; Phase 3 only owns the in-graph nudge text as a literal constant in the gate node).
- **Independent of:** Phase 5 (tests).

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | **C3 — DB seam exception crashes leader mission**: any unhandled exception in `increment_attestation_denied_count` / `reset_attestation_denied_count` / `set_completion_gate_escalated` (e.g., SQLAlchemy `OperationalError` on connection drop) errors the leader. | High | **Phase 3 task 3.6**: `try/except Exception` widens W4 precedent's narrow set (which does NOT cover `OperationalError`); on DB exception the gate's deny/terminal outcome becomes `allow` with `event=leader_completion_gate_db_error` log. Integration test asserts. |
| 2 | **O2 — Counter stale-trip across `escalated→revive→next-mission`**: without reset on `terminal_after_bound`, a revived leader's first new-mission deny insta-escalates. | High | Phase 3 task 3.3 spec: `reset_attestation_denied_count(instance_id)` is called BOTH on `allow-with-attest` AND on `terminal_after_bound` (alongside setting `completion_gate_escalated=true`). Test asserts the escalation cycle resets on the next mission. |
| 3 | **O3 — Stale pre-revive attestation watermark**: post-revive window scanning may miss a pre-revive attestation if the window semantics differ across compaction boundaries. | Medium | Phase 4 log schema carries `attest_seen_outside_window: bool` (Phase 3 task 3.3 emits it via `evaluate()`); if dry-log rate exceeds threshold, O3 mitigation flips on (rebuild window from full history). |
| 4 | **O4 — Pause-mid-gate double-increment**: a pause between gate deny and counter update could double-increment under concurrent deny paths. | Medium | Phase 3 task 3.3 chooses **idempotent per-denial-epoch upsert** (`INSERT ... ON CONFLICT DO UPDATE SET count = count + 1` keyed by `(instance_id, denial_epoch)`); the documented inflation alternative (NOT chosen) is preserved as fallback. Test asserts idempotency under concurrent denies. |
| 5 | **Migration fails on fresh-SQLite boot**: PG-only `DROP CONSTRAINT IF EXISTS` patterns broke under SQLite (live hazard per `LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md`). | High | Phase 3 task 3.2 migration uses PG+SQLite-safe patterns (no PG-only DDL); migration test runs on BOTH backends. |
| 6 | **Counter increment races with terminal stamp**: the gate's `terminal_after_bound` path sets the flag AND resets the counter; under concurrent deny paths during a `terminal_after_bound` window, race could yield inconsistent state. | Medium | `terminal_after_bound` is a single atomic UPDATE on the instance row (set flag + reset counter in one statement); defense-in-depth WHERE clause `WHERE admission_state NOT IN (...)`; integration test asserts no lost updates. |
| 7 | **Reset on revive-from-COMPLETED creates a feedback loop**: if the gate re-denies immediately on revive, the new mission starts pre-burdened. | Low (architect addition resets on allow-with-attest too; first deny of new mission increments to 1, NOT to bound) | Reset semantics documented; test asserts `attestation_denied_count` after revive-from-COMPLETED is 0 before any new gate evaluation. |

---

## Rollback Story

This phase is reversible:

1. **Schema migration rollback:** reverse migration drops `attestation_denied_count` + `completion_gate_escalated` columns. Counter is no longer persisted; gate's terminal-after-bound path can no longer set the flag.
2. **Repository method rollback:** remove the four ledger methods (`increment_attestation_denied_count`, `reset_attestation_denied_count`, `set_completion_gate_escalated`, `get_attestation_denied_count`). Gate's allow/deny paths no longer reference the counter.
3. **Fail-open wrapper rollback:** remove the `try/except Exception` around the ledger DB seam. Gate becomes brittle to transient DB errors (NOT recommended — the W4 precedent's narrow exception set does NOT cover `OperationalError`).
4. **Reset-trigger removal:** remove the four reset triggers (per the leader ruling, CLOSED-by-leader 2026-09-05 — verbatim): (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation. Counters accumulate forever; risk of insta-escalation on next mission.

**Restart-read:** all changes require daemon restart. The reset triggers (Phase 3 task 3.3) require careful sequencing — if a counter increment is in flight when a reset trigger fires, the reset may clobber the increment. Standard defense-in-depth: defer reset until next instance boundary OR use the idempotent upsert (Phase 3 task 3.3 O4) which makes this race impossible by keying on `(instance_id, denial_epoch)`.

---

## Exit Criterion

This phase is done when:

- [x] `tests/unit/test_attestation_ledger.py` passes (atomicity, increment, reset, escalation, idempotent upsert)
- [x] `tests/unit/test_attestation_gate.py` passes (R2 inputs, mode tri-state, C2 `attestation_enabled`, fail-open wrapper)
- [x] `tests/integration/test_attestation_ledger_reset.py` passes (full flow: deny path increments + nudges + does NOT enqueue; `terminal_after_bound` sets flag + resets counter; allow-with-attest resets counter; R2 allow does NOT change counter; mode="dry" does NOT change counter)
- [x] `tests/integration/test_attestation_fail_open.py` passes (DB OperationalError → allow + error log)
- [x] AC-13.2 (OperationalError is NOT in the bootstrap exception set — emits `leader_completion_gate_db_error`, no silent allow/inflation; C7 carve-out) verified
- [x] Migration `tests/migration/test_attestation_migration.py` applies + rolls back cleanly on both PG and SQLite
- [x] AC-6.1 (counter increments on deny) verified
- [x] AC-6.2 (terminal after bound: terminal + escalation event + flag + counter reset) verified
- [x] AC-6.3 (no infinite loop: bound-N+1 attempts → terminal after bound, then counter reset on next mission) verified
- [x] AC-6.4 (fresh instance starts with `attestation_denied_count = 0` — per requirements.md:340-344) verified
- [x] AC-6.5 (counter resets to 0 on **attested allow only** — per leader ruling 1 / requirements.md:346-350; `allowed_legitimate_pending_wakeup` MUST NOT reset) verified
- [x] **No `manager.enqueue_message` reference exists in any phase-3 code** — grep guard verified (recovery injector is Phase 6 only)
- [x] **No "in-memory-dict ledger cleanup" task framing remains** — grep guard verified (C1c)

The phase is the precondition for Phase 4 (the resolver reads the counter for observability) and Phase 5 (the integration test matrix). The recovery injector + sweep backstop + JAFP tests are Phase 6's scope.