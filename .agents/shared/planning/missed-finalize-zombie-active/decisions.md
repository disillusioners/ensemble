# Decisions — missed-finalize / zombie-ACTIVE JobItem close path (wi-78967dba)

Companion to `technical-analysis.md` (same directory). Status legend:
**decided** = design-fixed, planner phases it as-is · **RATIFIED** = was open-for-architect-review,
architect confirmed @ `9657be93a` (`architecture-recommendation.md`) — implementation proceeds on
the recommended path; flip costs remain priced in the phase-plan contingencies ·
**architect-confirmed** = was decided, architect re-verified at source.

> **Fold record (2026-10-31):** D-1/D-3/D-6/D-8 RATIFIED with matrix verdicts (§0 of the
> architect doc); D-2/D-4/D-5 re-confirmed SOUND at source; R-9 exclusion ENDORSED; amendments
> A1–A7 folded into `technical-analysis.md` home sections + the phase plans; the two non-blocking
> leader-ratification notes (architect §12) recorded under D-6/D-8 below.
>
> **Second fold pass — reviewer council fold (W1/W2/W3/CF-3/A3/S4/S5/A6), 2026-10-31, verdict
> APPROVE-WITH-ADVISORIES 0C/3W/7A:** W1 notify-status fidelity (canonical
> `canonicalize_status(token)`, never raw token / f2-literal — D-4 addendum + T1.3 + Pack G);
> W2 A2 in-txn re-check parameterized (`recheck_instance_inflight=False` default — second caller
> `job_queue_service.py:2180`; retry-rearm orphan corner named on any fallback — D-4 addendum +
> T1.9 + Pack J(v)); W3 A7 rationale corrected (f2 helper's SEPARATE-step lock release is the
> real differentiator; `extra_updates` CAN carry terminal_reason, `repository.py:1692-1775` —
> false ground truth reworded at every site); CF-3 streak state = service-level dict
> (`child_reports.py:292` precedent, reset-on-close, evict-on-disappear); A3 citation fix
> (reconciled>0 gate = `api.py:3942-3948`, boot twin `:2456` — the earlier `:3926-3935` was
> wrong); S4 NULL-instance finalize-without-lock-release pin (Pack H); S5 deterministic ORDER BY
> oldest-terminal-first (T1.2 + D-12); A6 operator-race wording + self-surgery scope note
> (`ens_db_tools.py:50-57` — refusals do NOT cover `job_queue_items`/`job_locks`).

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
infra, E1 wiring (`:2666-2699`, `:2970-3001`), the f2 helper's NOTIFY half (fire-then-cancel
composition, `:4103-4234` — A7: notify half only; the write seam is `force_finalize_orphan`),
the census-registered F5 writer (D-4), the knob-passing precedent
(`api.py:1446-1487` → loop kwargs → `reconcile_drift_states` args), and the densest pack home
(`tests/job_queue/`). (a) duplicates all wiring for isolation that does not materialize; (b)
breaks a self-declared non-writer's charter (`watch_reconcile_sweep.py:64`, `:109-115`). Full
matrix in technical-analysis §3.

**Status:** **RATIFIED @ 9657be93a** — architect CONFIRMED (c), weighted matrix **3.95 vs 2.85
(a) / 2.35 (b)** (Complexity 20% · Scalability 20% · Maintainability 25% · Risk 20% inverted ·
Cost 15% inverted); dominant axes Cost + Maintainability; confidence High; premise checks all
passed (arm enumeration, letter-(h) free, knob precedent verified end-to-end `api.py:1446-1487` →
`:1481-1489` → loop `:3931-3940` → signature `:925-934`). (b) disqualified by its own charter
docstring; (a)'s wiring surface carries the WS3 miss precedent (`api.py:1465-1470` patch).
Process note carried: keep the leg strictly additive, land early (merge pressure on the
4,946-line file). A7 precision folded: helper reuse = notify half only.

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

**Root-cause premise — git evidence (verified this lane):** commit `47b69bf00` ("fix(strand-healer):
the running-zombie anchor, the widened tree leg, the re-arm", giter, 2026-10-21, branch
`fix/running-zombie-sweep`) touched `mission_live_guard.py` (anchor scope: root stamp collected
separately; childless-root fallback fires only when `member_count==0`, `:869-870`, `:900-925`) and
`mission_strand_healer.py` (tree-leg widening onto the canonical guard with `skip_root=True`).
It fixed **only the backstop CLOCK** — leg (c)'s non-terminal COUNT was never touched and remains
unconditional for existing consumers (`:893-898` with `skip_root=False` in the f-arms). What
remains open after that commit is exactly "non-terminal status with no provable staleness" = the
zombie-ACTIVE shape pattern (h) closes. Premise holds; confirmed by architect §2.

**Status:** **architect-confirmed SOUND** (§3: discount sites verified — exactly TWO
non-terminal-count sites `:893-898`/`:978-982`, no third writer of `any_non_terminal`; liveness
is conditional-not-absolute, disclosed as designed; fail-closed polarity "exactly right for an
autonomous finalizer").

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

**Status:** **RATIFIED @ 9657be93a** — three synthetic tokens CONFIRMED; **pass-through is
provably harmful** (not merely weaker): `_derive_legacy_status` gates on map membership
(`work_status.py:401-416`) — an unregistered token on a `done` row falls through to
`_ADMISSION_TO_LEGACY_STATUS['done']='completed'`, i.e. pass-through would MISREPORT
`zombie_active_failed` as completed, and `_job_to_record` (`work_resolver.py:1765-1770`) would
leak the raw token as `record.status`. Architect consumer census (§5): zero consumers need a code
change the plan missed; `mission_resolver` zero contact ((h) writes DONE never DEAD; epochs
hard-return 1). **A4 folded:** JobRetryEngine reads `failed_at` not `terminal_reason`
(`job_retry_engine.py:183, 287`) — healed `zombie_active_failed` rows get no `failed_at` ⇒
`atomic_retry` FORECLOSED (disclosed; NEVER stamp `failed_at` on heal — it would re-arm 2h+-stale
work). **A5 folded:** attribution queries use the explicit IN-list, not prefix-LIKE (PG btree
prefix-blind without `text_pattern_ops`).

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
**W2 addendum (reviewer council):** the seam has a LIVE SECOND CALLER — the
`cleanup_non_terminal_jobs` orphan reaper (`job_queue_service.py:2180`) — so the A2 in-txn
inflight re-check ships as a PARAMETER `recheck_instance_inflight: bool = False` (existing
callers byte-identical by default; the (h) leg always True). Fallback to pin+disclose only on
demonstrated contract disturbance, naming the RETRY-REARM ORPHAN CORNER explicitly (bounded by
the 7200s gate). **W3 addendum (reviewer council):** the A7 rationale is corrected —
`atomic_transition`'s `extra_updates` DOES map to JobItem columns incl. potential
`terminal_reason` (`repository.py:1692-1775`); the f2 helper's write half is not reused because
it releases the job lock in a SEPARATE step 2 (`job_recovery_service.py:4188-4235`, own crash
window) vs this seam's single-transaction pairing. **W1 addendum (reviewer council):** the
(h) notify status is the canonical verdict string via `canonicalize_status(token)` — never the
raw token (absent from `ALL_TERMINAL_STATES`, `watcher_models.py:13` ⇒ terminal-cleanup
misroute) and never the f2-literal `'completed'`.

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

**Status:** **RATIFIED @ 9657be93a** — default-ON CONFIRMED, weighted matrix **4.15 vs 3.20**;
dominant axis Risk ("the riskiest thing is the disease, not the heal" — immortal lock slots,
limit=1 queues blocked indefinitely). House policy codified: `watch_reconcile_sweep.py:~56-60`
("a kill-switch defaulting OFF that gates a fix is an unacceptable deliverable"); strand-heal
shipped default-ON with the identical containment profile (fail-closed, 7200s, cap 25), clean
since 2026-10-21. **NON-BLOCKING (architect §12):** if the leader weighs first-release caution
differently, the flip is ONE Field default + pin re-point, no redesign — priced in
phase1-plan.md §7.

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

**Status:** **RATIFIED @ 9657be93a** — INCLUDE CONFIRMED, weighted matrix **4.35 vs 3.05**;
dominant axes Cost + Maintainability. The architect verified: born-broken at
`db_capture.py:374-386`; fix-shape precedent `ens_db_tools.py:1218-1229`; static pins
(`test_dbpool_wedge_capture_static.py:100-137`) assert NONE of the touched forms — the LEFT-JOIN
shape survives unchanged; and the PG pack's own STOP item documents owner=wedge-capture lane
with **no active owner lane existing** — routing out means lane startup overhead ≫ the fix.
**NON-BLOCKING (architect §12):** an overrule means routing R-10 to a (currently nonexistent)
owner lane and accepting the census staying born-broken — priced in phase2-plan.md §7.

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

**Status:** **ENDORSED @ 9657be93a (exclusion)** — 4.35 vs 3.00; "the disable rationale is a
measurement argument, not inertia"; adapter agets 73-99s EXCEED the 60s constant (enabling would
reap legitimate checkpoint saves mid-flight); v0.27.15's live pool knobs already address the
class at pool level. Registration judged ADEQUATE with ONE addition per architect §9: carry the
**"template-alone edit is NOT enablement" both-or-neither warning** (`constants.py:337-341`)
explicitly in the follow-up ticket — folded into phase6-plan.md T6.4.

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
(terminal) + NOT EXISTS instance-scoped non-terminal task + age filter, **deterministic ORDER BY
oldest-terminal-first `MAX(task terminal stamp) ASC` with `job_id` tiebreak (S5, reviewer
council — LIMIT without ORDER BY is non-deterministic across ticks)**, `LIMIT batch_cap`;
per-candidate E1 via the existing `asyncio.to_thread` tree walk
(`mission_live_guard.py:803-825`); all repository access off-loop (v0.27.14 eventloop-lane
discipline — sync-DB-on-loop is the incident class this daemon just cured). Remainder next tick
(`config.py:2684-2690` semantics). Stats fold into the drift stats dict the loop already logs.

**Status:** decided (S5 ORDER BY folded by the reviewer council pass).

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

**Fold addition (A1/A3/A5/A6, per architect §10):** the pack gains **Pack J — architect-amendment
pins** (phase3-plan.md T3.12): A1 counter-block-logged-when-examined>0 + escalating WARN ≥K
consecutive skips; A3 `notify=<outcome>` on every close + surviving-watcher follow-up ticket
token; A5 attribution IN-list (never prefix-LIKE); A6 two defensive E1 pins — (i) the
member-alive candidate builder never re-arms fail-MISS on the discounted instance
(`mission_live_guard.py:605-612` invariant), (ii) the childless-root `member_count==0` fallback
(`:925-930`) is never on the (h) close path (anti-"unify the anchor" regression pin).

**Status:** decided (pack contents extended by the architect fold).
