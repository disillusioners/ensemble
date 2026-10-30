# Decisions — missed-finalize / zombie-ACTIVE JobItem close path (wi-78967dba)

Companion to `technical-analysis.md` (same directory). Status legend:
**decided** = design-fixed, planner phases it as-is · **open-for-architect-review** = firm
recommendation attached, genuinely contestable, flip is cheap and non-redesigning.

---

## D-1 — Shape: pattern-(h) leg in the drift reconciler (option c)

**Decision:** implement as a new pattern-(h) leg inside
`JobRecoveryService.reconcile_drift_states`, riding the existing drift asyncio task
(`api.py:1477-1487`, loop `:3878+`). NOT a new dedicated sweep service (a); NOT an extension of
`watch_reconcile_sweep` (b). No hybrid.

**Correction to the brief:** "pattern-(g)" is already taken —
`_pattern_g_defer_self_witness_watchdog` (`job_recovery_service.py:3456`; kill-switch
`ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`, boot log `api.py:1400-1410`). Grep-verified no Pattern (h)
exists. The new leg is **pattern (h)**.

**Rationale:** the heal object is the JobItem admission state — the drift reconciler's charter,
whose (a)–(g) family already enumerates missed-finalize shapes. (c) inherits the candidate-loop
infra, E1 wiring (`:2666-2699`, `:2970-3001`), the f2 notify fire-then-cancel helper
(`:4103-4234`), the census-registered F5 writer (D-4), the knob-passing precedent
(`api.py:1446-1487` → loop kwargs → `reconcile_drift_states` args), and the densest pack home
(`tests/job_queue/`). (a) duplicates all wiring for isolation that does not materialize; (b)
breaks a self-declared non-writer's charter (`watch_reconcile_sweep.py:64`, `:109-115`). Full
matrix in technical-analysis §3.

**Status:** open-for-architect-review (the brief holds the shape open; recommendation is firm).

---

## D-2 — Liveness / no-self-hold: discounted E1 via `discount_instance_id` + monotone task-age bound

**Decision:**
1. Add optional `discount_instance_id: str | None = None` to `evaluate_mission_live`
   (`mission_live_guard.py:327-360`); the non-terminal count skips the named instance at the two
   sites already carrying the `skip_root` conjunct (`:896`, `:982`). Default None = zero change
   for every existing caller. This generalizes the existing pre-stamp discount (`skip_root`,
   used by `mission_strand_healer.py:1175` and `child_reports.py:2884`).
2. The (h) decision chain: zombie predicate (work_id Task terminal + instance inflight probe
   empty, `job_recovery_service.py:4779` shape) AND age ≥ bound keyed on MAX(task terminal stamps)
   on the instance (monotone; NOT `last_activity_at` — `config.py:2670-2673` precedent) AND
   `bus_pending==0` (fail-safe skip on bus-unavailable, f2 Gate 1 `:2844-2868`) AND
   `evaluate_mission_live(discount_instance_id=job.instance_id)` returns live=False CLEAN.
3. Fail-CLOSED on guard error/timeout (skip + counter) — deliberate divergence from the f-arms'
   fail-open WARN (`:2700-2711`, `:3002-3010`); strand-healer fail-closed-all-legs invariant
   (`config.py:2640-2644`).

**Rationale:** every verified starvation vector (root self-count `:893-898`; childless-root
enqueue-refreshed fallback `:924-930`; missing-anchor disarm `:1016-1028`; non-root activity
re-roll; member-alive fail-MISS `:638-676`) is either removed by the discount or never consulted
(the anchor/backstop machinery plays no role in this decision path). Legitimate missions still
hold via every OTHER member's status + bus + any non-terminal task anywhere on the instance. The
age bound applies ONLY to the zombie-shape predicate — healthy trees never enter the candidate
set. Full vector-by-vector table in technical-analysis §4.

**Status:** decided.

---

## D-3 — Finalize: always admission DONE; `terminal_reason` = `zombie_active_{completed,failed,cancelled}`

**Decision:** admission DONE for the whole class (the backing Task carries an organic verdict;
DEAD remains f1's no-Task shape, `job_recovery_service.py:3396-3420`). Stamp one of three
provenance tokens mirroring the Task's terminal status; register each in `_STATUS_CANONICAL_MAP`
(`work_status.py:67-142`) collapsing onto `completed` / `failed` / `cancelled` respectively. The
reverse index `_CANONICAL_TO_SOURCES` builds from the map at import (`:181-205`) — no other
registration; NO `_TERMINAL_STATUSES` change (targets already terminal, unlike the
`orphaned_no_task` self-map case `:130-142`).

**Rationale:** the brief's `orphaned_no_task` belongs to `reconcile_turn_mirror`'s no-Task arm
(`task/repository.py:2149-2160`; findings correction #1) and means "work never delivered" — false
here. Because the public status is DERIVED from `terminal_reason` (`work_status.py:319-367`;
JobItem `status` column no longer written, `:97-100`), a single token collapsing to `cancelled`
(the `orphan_retired` pattern) would misrepresent completed work; a self-mapped token would mint a
new canonical status and hide the organic outcome. Three provenance tokens keep the public status
truthful AND make every heal query-attributable via `idx_job_queue_terminal_reason`
(`models.py:307-313`). PAUSED protections are preserved untouched (paused tasks are non-terminal
⇒ predicate fails before any write; f1's strict skips `:2474-2546` upstream and unchanged).

**Status:** open-for-architect-review (public vocabulary surface; the pass-through-the-task-reason
alternative with observability-only attribution is the fallback if the reviewer prefers zero new
tokens).

---

## D-4 — Write seam + lock ordering: reuse `force_finalize_orphan`; finalize-FIRST/release-LAST in ONE transaction

**Decision:** the (h) write is `JobRepository.force_finalize_orphan(job_id, terminal_reason=token)`
(`repository.py:4288-4347`) — an already census-registered writer (`constitution.py:113-144`, zero
census churn). Its shape IS the required ordering: guarded `UPDATE … WHERE admission_state='active'`
(rowcount==0 ⇒ race lost ⇒ no-op INCLUDING lock side, `:4325-4329`) then job-scoped
`DELETE FROM job_locks WHERE job_id=:job_id` in the SAME `engine.begin()` transaction (W1 scoping
`:4258-4269`; NULL-instance skip `:4270-4273`; PG trigger-suite compliance `:4237-4257`).
Double-admission impossible: the job leaves the active set at/before the same commit as the
release. The boot sweep (`api.py:933-944`) and the ALWAYS-ON 90s F3 sweep
(`config.py:2428-2462` hard policy — untouched) remain the residual-crash backstop behind the
no-crash-window same-txn pairing. Canonical ordering rule cited: `job_recovery_service.py:1400-1416`.

**Status:** decided.

---

## D-5 — Audit surface: log lines + counters, NO new table

**Decision:** strand-healer precedent — structured `reconcile_drift_states: Pattern (h) …` log
lines + drift-stats counters (examined / age_qualified / mission_live_holds / error_skips /
finalized / race_lost_noop / zero_watcher_warns) + skip reason tokens. No audit table, no
migration.

**Rationale:** the durable record IS the job row (`terminal_reason` token, indexed) — the
notify_audit precedent (refusals with no other trace, `20261025_000001_create_notify_audit.sql:9-10`)
does not transfer. Keeps the v0.27.16 migration delta EMPTY = cleanest standing-rule rollback
adjudication ("delta-empty-legitimate ⇒ ENSEMBLE_ROLLBACK_SAFE=1", v0.27.13/14 precedent) and
avoids the full table ship checklist (model + IF-NOT-EXISTS `.sql` + api.py late-import
`api.py:961-967` + declared tier; findings-riders §4).

**Status:** decided.

---

## D-6 — Knobs + kill-switch posture: strand-heal template, default-ON

**Decision:** three `ServicesConfig` Fields (env `SERVICES_` auto-prefix, pydantic FAIL FAST,
wired via the api.py drift block like `f1_tree_activity_max_age_seconds`):
- `drift_reconcile_zombie_close_enabled: bool = True`
- `drift_reconcile_zombie_close_age_bound_seconds: int = 7200` (`ge=1`)
- `drift_reconcile_zombie_close_batch_cap: int = 25` (`ge=1`)
No separate interval knob (rides `drift_reconcile_interval_seconds` = 300s; the 2h age bound
dominates latency). Default-ON per the strand-healer justification pattern
(`config.py:2640-2644`): fail-closed predicate + hours-wide monotone age + small batch cap on
terminalizing writes (`:2684-2689`) + guarded single write seam; the kill-switch is the
operator's escape hatch. Modern ServicesConfig shape chosen over the legacy env-only F1 resolver
(`job_recovery_service.py:115-158`; findings-riders §5 template guidance).

**Status:** open-for-architect-review (automated terminalizing writer defaulting ON is the single
most operator-visible posture call in this design; default-OFF-first-release is the cheap
alternative if the reviewer weighs first-release caution over the demonstrated unbounded harm of
the status quo — immortal lock slots, limit=1 queues blocked indefinitely).

---

## D-7 — Message-kind scope: DEFER to F-1; (h) is task-kind only

**Decision:** the (h) candidate query filters `job_type='task'`. Message-kind stays with F-1
`_reconcile_terminal_message_mirrors`, which ALREADY runs every drift cycle with no age bound
(`job_recovery_service.py:851-919`, `:1135`; findings correction of brief claim (i)).

**Rationale:** bounded reconcile for message-kind exists; message rows never hold job_locks
(`docs/architecture/job-as-front-primitive-invariants.md:137-139`) so there is no slot pressure to
cure; a second admission writer over F-1's rows is pure double-writer contention. The f-wide
message skip (`:2392-2423`) is left as-is.

**Status:** decided.

---

## D-8 — Rider R-10 (wedge-capture `pg_class.n_live_tup` census): INCLUDE as rider

**Decision:** include the minimal fix — `LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid`,
select `s.n_live_tup::bigint` (precedent `ens_db_tools.py:1223`), keep everything else in
`_read_store_census` (`db_capture.py:374-386`) verbatim. Re-point the real-PG pack's census
expectation (`tests/postgres/test_dbpool_wedge_capture_pg.py:26-46`) from born-broken to
populated; static pins survive unchanged (`tests/unit/tools/test_dbpool_wedge_capture_static.py:100-137`).

**Rationale:** confirmed born-broken at base on real PG (UndefinedColumn → census always-failed →
InFailedSqlTransaction cascades into read-3); one-function, PG-only, strictly-a-repair blast
radius; it restores the observability instrument for exactly this incident class. Counter-weight
(scope discipline: wedge-capture is a foreign subsystem) — hence review-flagged.

**Status:** open-for-architect-review (include-as-rider vs route to the wedge-capture owner lane).

---

## D-9 — Rider R-9 (engine idle-in-txn backstop): EXCLUDE — register follow-up

**Decision:** exclude from this lane. Register as follow-up with: the one-edit enablement site
`factory.py:278` (`MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED`, template `:281-289`, constant
60_000ms `constants.py:314`); the precondition = trustworthy post-F8 measurement of the manager
engine's legitimate hold on the real fetch path (DR-11), bound sized ≥4× that figure
(`constants.py:329-342`); the MANDATORY `SET LOCAL statement_timeout = 0` carve-out in
`_ensure_postgres_columns` if ever flipped (`:342-344`); and the explicit note that the adapter-
pool reap (legit checkpoint agets 73-99s, `db_capture.py:407-409`) needs a minutes-scale bound and
its own commission — 60s there would reap legitimate saves.

**Rationale:** not trivially safe — the disable rationale on record (`constants.py:315-344`) is a
measurement gap, not inertia; enabling now risks server-side backend kills of legitimate
transactions.

**Status:** decided.

---

## D-10 — Pattern letter and naming

**Decision:** the new leg is **pattern (h)**; knob/log/counter namespace
`drift_reconcile_zombie_close_*` / `Pattern (h) zombie-active close`. (g) is taken — see D-1.

**Status:** decided.

---

## D-11 — Scope boundary: admission + lock only; NO instance-status writes

**Decision:** (h) finalizes the JobItem and releases its lock; it does NOT transition the stalled
instance's status. Instance-level heals remain the strand-healer family (running-zombie strand
covers COMPLETED-task roots via chain-drive, `mission_strand_healer.py:86-93`) with a DISCLOSED
follow-up for FAILED/CANCELLED-root and non-root stalled instances; missed report delivery belongs
to the RDRS lanes (completed-child detection signature, RDRS blueprint).

**Rationale:** blast radius — instance status transitions carry revival/watcher/cascade semantics
owned by `instance_lifecycle`; the f-arm family precedent finalizes the JOB only; the commission
charter is the JobItem close path. The residual is disclosed, not hidden.

**Status:** decided.

---

## D-12 — Candidate query shape and loop discipline

**Decision:** one pre-filtered SQL round-trip per drift pass —
`job_type='task' AND admission_state='active' AND deleted_at IS NULL` JOIN task-on-work_id
(terminal) + NOT EXISTS instance-scoped non-terminal task + age filter, `LIMIT batch_cap`;
per-candidate E1 via the existing `asyncio.to_thread` tree walk
(`mission_live_guard.py:803-825`); all repository access off-loop (v0.27.14 eventloop-lane
discipline — sync-DB-on-loop is the incident class this daemon just cured). Remainder next tick
(`config.py:2684-2690` semantics). Stats fold into the drift stats dict the loop already logs.

**Status:** decided.

---

## D-13 — Test-surface contract (for the planner)

**Decision:** packs land in `tests/job_queue/` (`test_zombie_active_close_*.py` neighborhood):
mutation-verified pins per decision leg (plant→RED→GREEN, digest-proven); an
existing-E1-callers-unchanged pin for the `discount_instance_id` default; concurrent-race pins
(two actors, one winner, loser no-ops incl. lock side); a real-PG leg for the trigger suite +
same-txn lock release; knob-wiring pins (api.py config reads); zero-watcher WARN pin;
net-new=EMPTY via full FAILED-list diff vs true pre-fix base, same commands both sides
(worked example: `.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md`). PACKS.md entry
per convention.

**Status:** decided.
