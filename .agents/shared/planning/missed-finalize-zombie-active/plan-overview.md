# missed-finalize / zombie-ACTIVE JobItem close path — Plan Overview

Board: `wi-78967dba` · Lane worktree: `/home/nea/ensemble-src-wt-missed-finalize` (branch
`fix/missed-finalize-zombie-active`, base `22fe83317`) · Plan date: 2026-10-30 ·
**Architect fold: 2026-10-31 @ `9657be93a`** (all four held-open decisions RATIFIED;
amendments A1–A7 folded into the plan set; this file and the phases carry the results).
Companion files (this directory): `research-findings-core.md`, `research-findings-repair-riders.md`,
`technical-analysis.md` (the design, §1–§12 + fold annotations), `decisions.md` (D-1..D-13 with
statuses incl. RATIFIED verdicts), `architecture-recommendation.md` (the architect's authoritative
verdict record — read-only input).
This plan set is the IMPLEMENTATION half — it phases the DECIDED design; it does not re-litigate it.

---

## 1. Objective

Close the missed-finalize / zombie-ACTIVE JobItem gap: a `job_type='task'` JobItem that stays
`admission_state='active'` after ALL backing Tasks are terminal, while its driving instance remains
non-terminal (RUNNING-stalled). Today no admission writer ever terminalizes it (technical-analysis
§2), its `job_locks` row is immortal (`_ACTIVE_JOB_IDS_SUBQUERY` excludes it from both lock sweeps,
`lock_repository.py:18-34`), and with `concurrency_limit=1` legacy queues a single zombie blocks the
whole queue indefinitely. The fix adds a fail-closed pattern-(h) leg to the drift reconciler that
terminalizes exactly this shape (admission DONE + three provenance tokens + same-transaction
job-scoped lock release), plus the R-10 wedge-capture census rider — staged as `v0.27.16`
(expected; re-derived at lane-end stage), promote-ready, NO promote.

## 2. Scope

**In scope**
- Pattern-(h) leg inside `JobRecoveryService.reconcile_drift_states` riding the existing drift
  asyncio task (D-1, D-10, D-12).
- `discount_instance_id` optional param on `evaluate_mission_live` (D-2) — default `None` =
  zero change for every existing caller.
- Finalize semantics: always admission DONE + `terminal_reason ∈ {zombie_active_completed,
  zombie_active_failed, zombie_active_cancelled}` registered in `_STATUS_CANONICAL_MAP` (D-3).
- Write seam: reuse census-registered `force_finalize_orphan` — guarded UPDATE + same-transaction
  job-scoped lock DELETE, finalize-FIRST/release-LAST (D-4).
- Notify through the existing `notify_watchers` → partition/CAS path; zero-watcher WARN reuse;
  held `mission_terminal` rows deliberately survive (§7 of the design).
- Audit = structured log lines + drift-stats counters, NO new table (D-5).
- Knobs on the strand-heal template: kill-switch + age-bound 7200s + batch-cap 25, default-ON
  posture per recommendation (D-6), wired via the api.py:1446-1487 kwargs precedent.
- P1/observability visibility line for the class (today: ZERO P1 warnings — findings-core Q6).
- Rider R-10: wedge-capture store-census `pg_class.n_live_tup` fix + real-PG pack re-point (D-8).
- Full mutation-verified pin pack family, functional gate, house gates, giter stage (D-13).

**Out of scope (hard)**
- The aborted-transaction subclass — RETIRED by the live v0.27.15 pool fixes (stacked lineage;
  wi-78967dba split verdict). This lane covers ONLY the pool-independent RUNNING-stalled /
  zombie-ACTIVE class.
- R-9 engine idle-in-txn backstop enablement — EXCLUDED, registered as follow-up with the exact
  one-edit site and mandatory preconditions (D-9).
- Any instance-status writes / instance-level heals (D-11: admission + lock ONLY; instance heals
  stay with the strand-healer family + a disclosed follow-up for FAILED/CANCELLED-root and
  non-root stalled instances; missed reports belong to the RDRS lanes).
- Message-kind JobItems (D-7: defer to the F-1 mirror backstop that already runs every drift
  cycle, no age bound).
- Any UI work; any changes to the F3 job-lock sweep (ALWAYS-ON hard policy, `config.py:2437-2438`);
  any new sweep service (D-1 rejected option (a)); any `watch_reconcile_sweep` writer conversion
  (rejected option (b)).

## 3. Recommended shape (one paragraph; full matrix in technical-analysis §3)

A new **pattern-(h) leg inside `JobRecoveryService.reconcile_drift_states`** (option (c)),
riding the existing drift asyncio task (`api.py:1477-1487`, loop `:3878+`) — zero new scheduling,
zero new service wiring, zero thread bridges. Per candidate (one pre-filtered SQL round-trip,
`LIMIT batch_cap`, remainder next tick): zombie predicate (work_id Task terminal + instance-scoped
inflight probe empty, `job_recovery_service.py:4779` shape) AND monotone age bound keyed on
`MAX(task terminal stamps)` (NOT `last_activity_at`) AND `bus_pending==0` (fail-safe skip on
bus-unavailable) AND discounted E1 — `evaluate_mission_live(discount_instance_id=job.instance_id)`
must return `live=False` CLEAN; any unprovable leg ⇒ SKIP (fail-closed, deliberate divergence from
the f-arms' fail-open). The write is the census-registered `force_finalize_orphan`
(`repository.py:4210`, F5 body `:4288-4347`): guarded UPDATE `active→done` + job-scoped
`job_locks` DELETE in ONE transaction (loser no-ops including lock side). Notify through the
existing `notify_watchers` partition/CAS path. Audit = log lines + counters, no table →
**migration delta stays EMPTY**.

## 4. Phases

| # | Name | File | Objective | Exit criteria | Depends on |
|---|------|------|-----------|---------------|------------|
| 0 | Baseline & evidence | phase0-plan.md | Reproduce the zombie-ACTIVE signature at BASE on the dev PG fixture; prove the gap (no writer touches it); capture the TRUE pre-fix base FAILED list for the net-new gate; fences proven | Fixture shows JobItem still `active` + lock row immortal across ≥3 drift cycles at BASE; base FAILED list captured with verbatim commands; fence evidence recorded | — |
| 1 | Pattern-(h) implementation | phase1-plan.md | `discount_instance_id` guard param; the (h) predicate + decision chain; token registration; knobs + kill-switch + boot log; observability; api.py wiring | Leg runs under knobs; fixture from Phase 0 closes at HEAD; existing E1 callers unchanged; migration delta EMPTY | 0 |
| 2 | Rider R-10 | phase2-plan.md | Wedge-capture store-census `LEFT JOIN pg_stat_user_tables` fix + real-PG pack re-point; static pins survive unchanged | Census populated on real PG; static pin pack green unchanged; PG pack re-pointed and green | — (parallelizable with 1) |
| 3 | Test packs | phase3-plan.md | Full mutation-verified pin family for every decision leg + negatives + races + kill-switch + knob wiring + real-PG trigger leg; PACKS.md entries | Every pin plant→RED→GREEN with digests; collected-count fences set; PACKS.md keep-both entries written | 1, 2 |
| 4 | Functional gate | phase4-plan.md | net-new=EMPTY: full FAILED-list diff vs the Phase-0 TRUE base, same commands both sides; order/load-sensitive reds isolated 3× per side | `net_new=∅` AND previously-red-green-at-HEAD check done; gate report written | 3 |
| 5 | House gates + stage | phase5-plan.md | Code council → fix cycles → tidier → tester functional gate; giter stage with version RE-DERIVED at lane end; migration delta EMPTY adjudication | STAGED release (expected v0.27.16) with 4-way sha match, staged verification PASS; STAGE-ONLY — no promote, no live contact | 4 |
| 6 | Lane closeout | phase6-plan.md | Evidence sweep, RESULTS doc, board wi-78967dba update, handoff report surfacing the four RATIFIED decision statuses (verdicts + flip costs + the two non-blocking leader notes) + R-9 follow-up registration | Handoff report delivered with decision statuses, follow-ups registered, lane artifacts dispositioned | 5 |

## 5. Coupling map

| | P0 | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|---|
| P0 | — | fixture reused by P1 smoke; base FAILED list consumed by P4 | — | — | **strong**: the diff baseline | — | — |
| P1 | — | — | none (disjoint files) | **strong**: every pack pins P1 legs | medium: HEAD side of the diff | medium: review + stage payload | — |
| P2 | — | — | — | medium: R-10 pack re-point | medium: included in gate corpus | medium: rides the same staged binary | — |
| P3 | — | — | — | — | **strong**: green packs are the gate input | medium: tester consumes packs | — |
| P4 | — | — | — | — | — | **strong**: gate verdict is a stage precondition | — |
| P5 | — | — | — | — | — | — | **strong**: stage record feeds the handoff |

Shared-surface watchpoints: `mission_live_guard.py` is touched by P1 and pinned by P3 while
EVERY existing E1 consumer depends on it (risk R2); `reconcile_drift_states` is already hot —
P1 adds a leg, P3 pins the leg, P4 re-runs the whole corpus; `db_capture.py` (P2) is disjoint
from P1 but shares the P4/P5 gates.

## 6. Risks (top-5 first; drawn from technical-analysis §12)

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | Mis-predicate premature finalize — closing a job that would have legitimately continued (imminent retry mint, deferred re-dispatch) | High | Zombie predicate leg (b) instance-scoped inflight probe catches pending retry children (fresh work_id, inherited instance_id); bus gate; hours-wide monotone age (7200s) on task terminal stamps; discounted-E1 still holds on ANY other member's liveness; batch cap 25; kill-switch; mutation-verified pin pack (plant→RED→GREEN per leg, digest-proven) |
| R2 | E1 surface regression — `discount_instance_id` leaking into existing consumers' verdicts | High | Default `None` = byte-identical behavior; discount conjunct added ONLY at the two sites already carrying `skip_root` (`mission_live_guard.py:896`, `:982`); pin: existing-caller verdicts unchanged with param default |
| R3 | Concurrent-actor race anomaly — double finalize / double lock release / double notify vs f-arms, observer, strand-healer, operator | Medium-High | Single census-registered write seam with guarded UPDATE + rowcount (loser no-ops INCLUDING lock side, `repository.py:4325-4329`); same-transaction finalize+release (no crash window); notify exactly-once via claim-CAS; concurrent-race pin pack |
| R4 | Drift-loop latency / event-loop regression — the (h) scan adding sync-DB time on the loop (the v0.27.14 incident class) | Medium | Candidate pre-filter as ONE SQL round-trip (JOIN + NOT EXISTS + age filter + LIMIT); per-candidate E1 via existing off-loop discipline; all repository access off-loop (D-12); round-trip counts reviewed in council |
| R5 | PG trigger interplay on the write — deferred `trg_job_locks_active_guard` / active-set guards rejecting the close | Medium | Reuse `force_finalize_orphan` verbatim — its shape is documented AGAINST the live trigger suite (`repository.py:4237-4257`) and mutation-proven (F5); real-PG leg in the pin pack |
| R6 | Watcher double-notify or silent loss on the (h) fire | Medium | Route ONLY through `notify_work_watchers` partition/CAS (no new notify-then-claim site, `work_notifier.py:401-409`); zero-watcher WARN reused verbatim; held `mission_terminal` rows deliberately survive |
| R7 | Scope creep into instance-status heals | Low-Medium | Explicit scope boundary (D-11); residual disclosed in the handoff, not hidden |
| R8 | Token vocabulary churn — new `terminal_reason` tokens surprising status-filter consumers | Low | Tokens collapse onto EXISTING canonical statuses — `is_terminal` unchanged, status-filter surfaces unchanged; `terminal_reason_variants_for` auto-derives; documented in release notes |

## 7. Success criteria (measurable)

- **SC1 (core close):** the Phase-0 zombie fixture (task-kind `active` JobItem + terminal backing
  Task + non-terminal instance + age ≥ bound) is terminalized to admission DONE with
  `terminal_reason='zombie_active_<task-status>'` and its `job_locks` row deleted in the SAME
  transaction, within one drift cycle of eligibility at HEAD; the IDENTICAL fixture at BASE is
  untouched across ≥3 drift cycles.
- **SC2 (healthy trees never close):** zero terminalizations across the negative suite — the five
  verified starvation vectors (root self-count, childless-root fallback, missing-anchor disarm,
  non-root activity re-roll, member-alive fail-MISS) are NON-triggers for healthy trees (predicate
  leg 1 fails: a non-terminal task exists).
- **SC3 (E1 zero-change):** existing-caller verdicts byte-identical with `discount_instance_id`
  default None — pinned, mutation-verified.
- **SC4 (race safety):** two concurrent actors on one candidate ⇒ exactly one finalize; loser
  no-ops INCLUDING the lock side — pinned, mutation-verified.
- **SC5 (fail-closed):** guard error/timeout ⇒ skip + `error_skips` counter, zero writes — pinned,
  mutation-verified.
- **SC6 (kill-switch):** OFF ⇒ COMPLETE no-op (no candidate query, no probe, no write) — pre-fix
  behavior restored — pinned, mutation-verified.
- **SC7 (schema invariant):** `daemon/migrations/versions/` delta EMPTY (zero new/changed `*.sql`;
  expected count 115==115 vs base `22fe83317`) — no new SQLModel table exists in this design.
- **SC8 (net-new):** full FAILED-list diff vs the Phase-0 TRUE base, same commands both sides ⇒
  `net_new=∅` (and `fixed=BASE_FAILED−FIX_FAILED` accounted).
- **SC9 (pins):** every regression pin in Phase 3 mutation-verified (plant→RED→GREEN with digests).
- **SC10 (stage):** staged release (expected `v0.27.16`, re-derived at stage per standing rule)
  passes staged verification with 4-way sha match (disk==dist==manifest==sidecar), live fence
  intact (current symlink + state.json promote fields untouched); NO promote.

## 8. Assumptions, invariants & ratified items for the dispatcher

**Assumptions.**
- The drift 300s cadence suffices (the class is hours old by definition; the 7200s age bound
  dominates) and the drift loop remains an asyncio task at implementation time (true at base,
  `api.py:1477`).
- The lane base `22fe83317` remains merge-relevant at lane end; if `origin/latest` advances,
  the standing FINALIZE discipline applies (fetch + verify, STOP and report if moved).

**Invariants (house discipline baked into every phase).**
- **Migration delta EMPTY is an INVARIANT of this design** (D-5: log lines + counters, no table).
  If ANY element ever introduces a new SQLModel table, the full `*.sql` ship-checklist applies
  (findings-riders §4): model + own `CREATE TABLE IF NOT EXISTS` `.sql` (full-line comments only
  where semicolons appear) + api.py late-import BEFORE create_all (`api.py:961-967` seam) +
  declared audit tier + NO `_ensure_postgres_columns` mirror for a brand-new table (mandatory
  mirror only if ALTERing an existing table). Until then: zero `.sql` changes.
- Net-new=EMPTY vs TRUE pre-fix base, same commands both sides; order/load-sensitive reds
  isolated 3× per side before classifying.
- Regression pins MUST be mutation-verified (plant→RED→GREEN, digest-proven).
- GNU grep trap: plain `|` under `-E`, NEVER `\|`; paste-execute every gate grep before freezing
  it as an acceptance criterion; concept tokens, both spellings + wildcard for post-fold greps.
- Always pass explicit `tests/` lists; pytest console floods ⇒ `tee LOG >/dev/null` + `tail`;
  real-PG legs may need explicit `--timeout` (the 24.5-26s wall vs 30s ini precedent).
- `env -u POSTGRES_*` is a documented SILENT NO-OP — scrub per-variable; fences:
  `POSTGRES_DB=ensemble_dev`, `PORT=8079`, never resolve `ensemble_prod`; also check the
  untracked `data/ensemble.json` database pin in the run root (2nd poison vector).
- Worktree has no `.venv` — certified run path = cd-worktree + main-venv interpreter by absolute
  path; PG packs: `PG_TEST_*` DSN (localhost:5432/ensemble_test), SERIAL-only under xdist.
- PACKS.md conflicts keep-both newest-first.

**Ratified items (history — each was held open, then ratified by the architect @ `9657be93a`
with the recorded verdicts; each phases the RECOMMENDED path; contingencies retained as priced
flip paths in the phase plans; see `architecture-recommendation.md` §0/§12):**
- **D-1 shape** (RATIFIED 3.95/5 vs (a) 2.85 / (b) 2.35; implemented: pattern-(h) leg) → priced
  flip path in phase1-plan.md §7.
- **D-3 token set** (RATIFIED; pass-through provably harmful via `work_status.py:401-416`
  fall-through misreporting failed/cancelled as completed; implemented: three `zombie_active_*`
  tokens) → flip foreclosed by the proof of harm; record in phase1-plan.md §7.
- **D-6 default-ON posture** (RATIFIED 4.15 vs 3.20; non-blocking leader note: one-Field flip if
  first-release caution is weighed differently) → priced flip path in phase1-plan.md §7.
- **D-8 R-10-as-rider** (RATIFIED 4.35 vs 3.05; non-blocking leader note: overrule = route to a
  nonexistent owner lane and accept the census staying born-broken) → priced flip path in
  phase2-plan.md §6/§7.
- R-9 follow-up registration (D-9, decided-exclude, exclusion ENDORSED @ `9657be93a` §9 with the
  both-or-neither warning addition) — executed in Phase 6 (T6.4).

## 9. Authorization envelope (restated)

Autonomous lane under the standing overnight-build authorization class. **HARD boundaries: NO
promote, NO nonce ceremony, NO live daemon contact, NO daemon restart — stage-only at lane end.**
The user's 3-factor nonce ceremony owns the flip. All testing on dev/scratch PG only
(`POSTGRES_DB=ensemble_dev` / `PG_TEST_*` DSN); the live install, live `state.json` promote
fields, and `:9797` are untouched throughout. Writes confined to the lane worktree; the main
checkout `/home/nea/ensemble-src` is NEVER touched.

## 10. File index

- `plan-overview.md` (this file)
- `phase0-plan.md` — Baseline & evidence
- `phase1-plan.md` — Pattern-(h) implementation
- `phase2-plan.md` — Rider R-10 (wedge-capture census fix)
- `phase3-plan.md` — Test packs
- `phase4-plan.md` — Functional gate (net-new=EMPTY)
- `phase5-plan.md` — House gates + stage (STAGE-ONLY)
- `phase6-plan.md` — Lane closeout & handoff
- Inputs: `research-findings-core.md`, `research-findings-repair-riders.md`,
  `technical-analysis.md`, `decisions.md`
- **`architecture-recommendation.md`** — the architect's verdict record @ `9657be93a`
  (authoritative input; read-only): §0 verdicts, §1–§9 decision confirmations, §10 amendments
  A1–A7 (folded into the plan set 2026-10-31), §12 non-blocking leader notes, §13 open questions
  routed to gates
