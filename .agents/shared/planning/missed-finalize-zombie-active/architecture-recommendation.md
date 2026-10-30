# Architecture Recommendation — missed-finalize / zombie-ACTIVE JobItem close path (wi-78967dba)

- **Date:** 2026-10-31 · **Reviewer:** Architect (controller) — evidence fan-out, 3 skill-equipped workers
- **Workers:** `trade-off-analysis` (760c68ad) · `resilience-design` (de984002) · `data-flow-design` (3ef8bb90)
- **Reviewed:** plan set @ `94d0254a6` in this directory, against worktree source @ base `22fe83317`
  (branch `fix/missed-finalize-zombie-active`, worktree `/home/nea/ensemble-src-wt-missed-finalize`)
- **Method:** every load-bearing claim re-verified at source with file:line; decisions scored on the five
  fixed axes (Complexity 20% · Scalability 20% · Maintainability 25% · Risk 20% inverted · Cost 15% inverted;
  tie-breakers Risk → Complexity → Maintainability).

---

## 0. Executive verdict

**The plan's premise and design are SOUND. No BLOCKING flaw was found.** All four held-open decisions are
**validated as the planner recommended**; the load-bearing safety claims (root cause, no-self-hold,
fail-closed polarity, race posture, migration-delta-empty) are **confirmed at source**. Seven amendments
(A1–A7, §10) must be folded before implementation freeze — all are pins, log-shape, disclosure, or
query-form changes; **none redesigns the approach**.

| Held-open item | Verdict | Score |
|---|---|---|
| D-1 shape | **CONFIRM (c)** — pattern-(h) leg in `JobRecoveryService.reconcile_drift_states` | 3.95 vs 2.85 (a) / 2.35 (b) |
| D-3 provenance | **CONFIRM three synthetic tokens** (pass-through is provably harmful) | qualitative — see §5 |
| D-6 posture | **CONFIRM default-ON** | 4.15 vs 3.20 |
| D-8 rider R-10 | **CONFIRM include in this lane** | 4.35 vs 3.05 |
| R-9 exclusion | **ENDORSE** (registration adequate + 1 addition) | 4.35 vs 3.00 |

---

## 1. D-1 — Shape: pattern-(h) leg in the drift reconciler (option c) — CONFIRMED

**Premise checks (all pass):** `reconcile_drift_states` at `job_recovery_service.py:925` takes the kwargs
triplet; arms (a)–(g) enumerated; existing arms match only *pending*-Task rows (a) or are log-only (c) ⇒
the zombie shape (terminal Task) has **no writer today** — gap confirmed. Pattern letter **(g) is taken**
(`_pattern_g_defer_self_witness_watchdog` `:3456`; grep `_pattern_h_`/`Pattern (h)` = 0 hits ⇒ D-10 naming
correct). Knob precedent verified end-to-end: `api.py:1446-1477` → `create_task(_periodic_drift_reconcile_loop)` `:1481-1489`
→ loop forwards kwargs `:3931-3940` → signature `:925-934`.

**Matrix (weighted totals):**

| Approach | Cmplx | Scal | Maint | Risk | Cost | Total |
|---|---|---|---|---|---|---|
| (a) dedicated sweep service | 2 | 4 | 3 | 3 | 2 | **2.85** |
| (b) extend `watch_reconcile_sweep` | 2 | 3 | 2 | 2 | 3 | **2.35** |
| **(c) pattern-(h) leg** | 4 | 4 | 4 | 3 | 5 | **3.95** |

- **(b) disqualified by its own charter** — "does NOT touch the `admission_state` of any JobItem"
  (`watch_reconcile_sweep.py:~109-115`); converting a self-declared non-writer into an admission writer
  falsifies the docstring every future reader relies on.
- **(a)'s isolation benefit does not materialize** (the leg calls the same repositories/helpers either way)
  and its wiring surface carries a **demonstrated miss precedent**: WS3 shipped a kill-switch but left the
  lifespan wiring out (patched later at `api.py:1465-1470`). Sibling precedents quantify the cost:
  OrphanWatcherSweep wiring `api.py:1573-1600`; MissionStrandHealer `:1701-1747` (+ manager wiring);
  RDRS additionally needs a thread→loop bridge (`report_delivery_recovery.py:873-897`).
- **Dominant axes: Cost + Maintainability** — (c) inherits the candidate loop, E1 wiring (`:2666-2699`,
  `:2970-3001`), notify helper, census-registered writer (`repository.py:4210`; `daemon/job_state/constitution.py:129`),
  and the exact knob pattern, keeping the missed-finalize taxonomy in one file with the densest pack home.
- Confidence **High**. Flips only on process risk (a concurrent lane merge-contending the 4,946-line
  `job_recovery_service.py`), not design — keep the leg strictly additive and land early.

**Precision note (A7):** "reuses the f2 helper" must mean the **notify half only** —
`_pattern_f_finalize_done` (`:4103`) writes via `atomic_transition`, which cannot carry `terminal_reason`;
the write seam is `force_finalize_orphan` per D-4 (already correctly specified in §6 of the design).

## 2. Root-cause claim — E1 leg (c) circular bookkeeping: CONFIRMED

- Leg (c) counts the walk root's own status into `any_non_terminal` **unconditionally** for existing
  consumers (`mission_live_guard.py:893-898`, `skip_root=False` in the f-arms) ⇒ a RUNNING-stalled root
  keeps `evaluate_mission_live` True with no clock to expire it.
- The 2026-10-21 anchor exclusion (**commit `47b69bf00`**, "fix(strand-healer): the running-zombie anchor…")
  fixed **only the backstop clock**: the root stamp is collected separately and the fallback fires only when
  `member_count==0` (`:869-870`, `:900-925`) — deliberately preserved for E1's own backstop (`:925-930`).
  Leg (c)'s *count* was never touched. What remains open is exactly "non-terminal status with no provable
  staleness" = **the zombie-ACTIVE shape (h) closes**. Premise holds; git evidence on record.

## 3. No-self-hold + fail-closed core (D-2) — SOUND; liveness conditional (disclosed)

**Discount sites verified:** exactly TWO non-terminal-count sites exist (`:893-898` in-loop, `:978-982`
post-strand-exclusion recompute); `any_non_terminal` has no third writer (init `:868`; `:939`/`:991` are
readers). Default `None` = zero change for every existing caller.

**Five starvation vectors — each neutralized (evidence):**

| Vector | Verdict | Evidence |
|---|---|---|
| Root self-count | Neutralized — discount removes the job's own instance ⇒ C1 all-terminal branch fires; anchor never consulted | `:893-898`, `:991-1006` |
| Childless-root enqueue-refreshed fallback | Neutralized — feeds only the anchor branch, unreachable when `any_non_terminal=False`; `completed_at` not re-rolled by enqueues (`repository.py:2369` COALESCE) | `:924-930` |
| Missing-anchor disarm | Neutralized for the decision — requires a non-terminal member present ⇒ (h) holds on that member anyway | `:1016-1028` |
| Non-root activity re-roll | Neutralized — feeds only anchor/backstop, consulted only when a member is non-terminal ⇒ hold | `:1030-1045` |
| Member-alive fail-MISS | Neutralized — candidate set excludes root + non-terminal-only (`:605-612`); empty ⇒ leg returns before any probe; no fail-MISS possible in the close-eligible shape | `:638-676` |

**Fail-closed polarity: correct.** The wrapper returns `live=False, error=True` on guard exceptions
(`:403-426`); the (h) chain treats `error or timed_out` as SKIP — the deliberate inversion of the f-arms'
fail-open (`:2700-2711`, `:3002-3010`) is exactly right for an autonomous finalizer: **every unprovable leg
lands on skip, never on a write.**

**Liveness is conditional, not absolute (disclosed residual):** a stalled non-terminal member *inside the
job's own subtree* (walk = `get_tree_ids_permanent(job.instance_id)`, `repository.py:838-867`) holds the
close until its own lane heals it — strand-healer covers COMPLETED-task roots only; non-root stalls are the
D-11 disclosed follow-up. This is **not a new vector** (the identical hold binds the f-arms today) and it is
fail-closed-direction. Likewise the candidate query's NOT-EXISTS sibling arm defers co-instanced zombies
while any sibling task on the instance stays non-terminal — conservative, claim-gate case-b-consistent.

## 4. Double-finalize race posture (D-4) — exactly-once under all six interleavings

`force_finalize_orphan` verified verbatim (`repository.py:4288-4347`): one `engine.begin()`; guarded UPDATE
`WHERE admission_state='active' AND deleted_at IS NULL`; `rowcount==0 ⇒ return None` **before** any lock
action; job-scoped lock DELETE; no intermediate commit/savepoint ⇒ mid-txn crash = full rollback, retried
next cycle.

| Interleaving | Verdict | Evidence |
|---|---|---|
| vs f2 helper | Candidate sets overlap but exactly-once holds: f2's `atomic_transition` raises on race-lost ⇒ early return **before lock release AND notify**; (h) loser no-ops incl. notify | `job_recovery_service.py:4197-4209`, `repository.py:4337-4341` |
| vs F-1 mirrors | Disjoint by `job_type` (message-kind only) | `job_recovery_service.py:851-863` |
| vs S15 claim-first CAS | `DELETE…RETURNING` before enqueue; enqueue-throw ⇒ UPSERT restores only this call's rows — no cross-actor resurrection | `work_notifier.py:346-380`, `:1117-1189` |
| vs claim/dequeue belts | PG INSERT-side trigger blocks lock INSERT unless an active job exists; claim picks PENDING tasks only — none in the close shape. Residual TOCTOU: retry child minted probe→commit not re-checked in-txn (A2) | `manager.py:8199-8206`, `task/repository.py:2686-2690`, `lock_repository.py:18-34` |
| operator ordering rule | Same-txn finalize-FIRST/release-LAST **compliant**; no committed boundary shows active-without-lock | `repository.py:4237-4257`, `job_recovery_service.py:1400-1416` |
| crash windows | Mid-txn safe (atomic). Post-commit-pre-notify: watcher rows orphan, no WARN (notify never ran), no event row — f2-precedent exposure class (A3) | `work_notifier.py:515-545`, `job_recovery_service.py:4235-4252` |

**7200s vs 21600s coherence — no hazard.** Age keys on `task.completed_at`, the ONLY terminal stamp column
(`models.py:275`), stamped via COALESCE on ALL terminal transitions incl. fail/cancel
(`repository.py:2369,3641,3754,3842,4638,4776,5210`) ⇒ monotone, never re-rolled, single-host clock; NULL
rows excluded by the MAX filter ⇒ fail-closed skip. (h)'s close path is the C1 all-terminal branch —
anchor/backstop-independent, so the 21600s backstop never gates (h). A non-discounted sibling view (held
`mission_terminal` rows) legitimately still says live=True after the close and re-fires via
`watch_reconcile_sweep` — correct by design.

## 5. D-3 — Provenance: three synthetic tokens CONFIRMED; pass-through is provably harmful

`_derive_legacy_status` **gates on map membership** (`work_status.py:401-416`): an unregistered token on a
`done` row falls through to `_ADMISSION_TO_LEGACY_STATUS['done']='completed'` — i.e. pass-through would
**misreport `zombie_active_failed` as completed**, and `_job_to_record` (`work_resolver.py:1765-1770`) would
leak the raw token as `record.status`. Registration in `_STATUS_CANONICAL_MAP` is the single sufficient
site; everything else derives at import (reverse index `work_status.py:181-205`; SQL filter IN-lists
`repository.py:195-197`/`:1308`; list_work union `work_resolver.py:480`/`:1367`;
`_JOB_CANONICAL_TO_ADMISSION` `:502-520`). No `_TERMINAL_STATUSES` change (targets already terminal
`:168-177`; contrast `orphaned_no_task` self-map `:130-142`).

**Consumer census — no consumer requires a code change the plan missed:**

| Consumer | With registration | Pass-through | Δ needed |
|---|---|---|---|
| `_derive_legacy_status` `:401-416` | canonicalizes correctly | **misreports failed/cancelled as completed** | none |
| `_job_to_record` `work_resolver.py:1765-1770` | canonical status | raw token leaks to API/FE | none |
| status filters `repository.py:195-197,1308` | IN-lists auto-extend | vocab drift (2026-09-10 defect class) | none |
| work_notifier (`:529` terminal check, `:770` events, `:625` display, PP1 WARN) | consumes canonical `record.status` — fires identically to organic reasons; `mission_terminal` held as planned | same | none |
| mission_resolver | **zero contact** — (h) writes DONE, never DEAD; W4 hazard keys on `dead` only; epochs hard-return 1 (`:1005`) | zero contact | none |
| FE (`job.model.ts:499` pass-through; no `.html` renders `terminal_reason`) | unaffected | unaffected | none |
| schema (`terminal_reason` TEXT, no CHECK — `20260629_000003:23`; idx `models.py:313`) | tokens stored freely | same | none |
| dead_letter / stored_watch / watcher models | no `terminal_reason` vocabulary (0 hits) | — | none |
| **JobRetryEngine** (`job_retry_engine.py:183,287`) | reads `failed_at` NOT `terminal_reason`: healed failed rows get **no `failed_at`** ⇒ atomic_retry foreclosed | identical | **A4 disclosure** |

## 6. D-5 — Audit surface & migration-delta-EMPTY: SOUND, with one observability amend

No hidden schema need surfaces: idempotency = the guarded UPDATE; the drift loop is stateless per tick (no
journal row); the P1 visibility line is a log line; the R-10 rider is a read-only SELECT change (no DDL).
The correct precedent is the **strand-healer** (`config.py:2625-2689`: knobs+counters+logs, no table, no
migration — shipped v0.26.4), NOT `notify_audit`/`instance_ops_audit`, which are agent-actuated
privileged-tool ledgers — a background reconciler is different in kind. **Delta-EMPTY is sound for v1.**

Two durability facts to carry: (i) the stats dict is in-memory per tick and the loop logs **only when
`reconciled>0`** (`api.py:3926-3935`) — a stuck-closed heal is otherwise invisible (→ A1); (ii) "when the
close happened" is log-only (JobItem has `created_at`/`failed_at`, no finalized-at column) — acceptable for
v1, disclosed.

## 7. D-6 — Posture: default-ON CONFIRMED

| Approach | Cmplx | Scal | Maint | Risk | Cost | Total |
|---|---|---|---|---|---|---|
| **Default-ON** | 4 | 4 | 4 | 4 | 5 | **4.15** |
| Default-OFF | 3 | 4 | 3 | 3 | 3 | **3.20** |

- Status-quo harm verified structurally: `_ACTIVE_JOB_IDS_SUBQUERY` (`lock_repository.py:18-34`) excludes
  zombie jobs' locks from **both** bulk sweeps — immortal rows; limit=1 legacy queues blocked indefinitely.
- House policy is codified, not vibes: `watch_reconcile_sweep` carries the rule that "a kill-switch
  defaulting OFF that gates a fix is an unacceptable deliverable" (`watch_reconcile_sweep.py:~56-60`);
  strand-heal shipped default-ON with the identical containment profile (fail-closed, 7200s, cap 25) and ran
  clean since 2026-10-21; RDRS stuck-wake is default-ON.
- **Dominant axis: Risk — the riskiest thing is the disease, not the heal.** Confidence High.

## 8. D-8 — Rider R-10: INCLUDE CONFIRMED

Born-broken confirmed at `db_capture.py:374-386` (`c.n_live_tup` selected from `pg_class`; column exists
only on `pg_stat_user_tables` ⇒ `UndefinedColumn` on every real-PG capture, cascading
`InFailedSqlTransaction` into read-3). The exact fix shape has in-repo precedent (`ens_db_tools.py:1218-1229`).
Static pins survive: `tests/unit/tools/test_dbpool_wedge_capture_static.py:100-137` asserts `FROM pg_class c`,
namespace join, schema filter, sizes, containment — **none reference `c.n_live_tup`**. The PG pack itself
documents this as a disclosed STOP item with owner=wedge-capture lane — **no active owner lane exists**;
routing out means lane startup overhead ≫ the fix. 4.35 vs 3.05; dominant axes Cost + Maintainability.
Note the wedge-capture F5 "no other change" contract amendment in the commit message + tester evidence.

## 9. R-9 — Engine idle-in-txn backstop exclusion: ENDORSED

The disable rationale is a **measurement argument, not inertia** (`constants.py:315-344`): max legitimate
model-injected hold measured 1059.9ms ⇒ 4× = 4.24s < the 30s floor; the floor coincides with legitimate
`pool_timeout` waits; and the server **terminates the backend** on this timeout. Critically, adapter-pool
legitimate agets are **73–99s** (`db_capture.py:407-408`) — they EXCEED the 60s constant: enabling now would
reap legitimate checkpoint saves mid-flight. v0.27.15's live pool knobs already address the class at pool
level. D-9 registration is **adequate** (one-edit site `factory.py:278`; measurement precondition; ≥4×
bound rule `constants.py:329-342`; `SET LOCAL statement_timeout=0` carve-out `:342-344`; minutes-scale
adapter note). **Addition for the follow-up ticket:** carry the "template-alone edit is NOT enablement"
both-or-neither warning (`constants.py:337-341`) explicitly.

## 10. Amendments to fold before implementation freeze

- **A1 🟡 (converged, two workers)** Stuck-closed observability: log the (h) counter block
  (examined / age_qualified / mission_live_holds / error_skips / finalized / race_lost_noop) whenever
  `examined>0` — **not** gated on `reconciled>0` (`api.py:3926-3935`); surface non-zero `error_skips` in the
  drift-stats WARN line; add an escalating WARN when the same job accumulates ≥K consecutive
  error_skips/mission_live_holds (K≈40 ≈ 3.3h @300s cadence). Counters alone are forensic, not operational.
- **A2 🟡** Probe/commit TOCTOU: a retry child minted between the instance inflight probe and the finalize
  UPDATE is not re-checked inside the txn. Preferred cheap closure: re-run the inflight probe (one SELECT)
  inside `force_finalize_orphan`'s transaction — if that disturbs the census-registered seam's contract,
  instead pin the accepted sub-second window with a mutation-verified test + disclose (the f-arms share the
  same window).
- **A3 🟡** Post-commit notify loss: emit the (h) log line with explicit `notify=outcome`; route
  "terminal job with surviving watcher rows" into a `watch_reconcile_sweep` follow-up ticket. Precedent-consistent
  with f2's best-effort notify.
- **A4 🟡** Retry foreclosure disclosure: healed `zombie_active_failed` rows carry no `failed_at` ⇒
  `JobRetryEngine` atomic_retry foreclosed. State it explicitly in D-3. Do **NOT** stamp `failed_at` —
  that would re-arm 2h+-stale work for retry.
- **A5 🟢** Attribution queries use `IN ('zombie_active_completed','zombie_active_failed','zombie_active_cancelled')`,
  not `LIKE 'zombie_active_%'` (PG btree is prefix-blind without `text_pattern_ops`).
- **A6 🟢** Two defensive pins in the D-13 pack: (i) member-alive candidate builder never re-arms fail-MISS
  on the discounted instance (`mission_live_guard.py:605-612` invariant); (ii) the childless-root
  `member_count==0` fallback (`:925-930`) is never on the (h) close path — prevents a future
  "unify the anchor" regression.
- **A7 🟢** Precision: "reuses the f2 helper" = notify half only (§1); pre-filter SQL pinned through
  `asyncio.to_thread` per D-12 (house off-loop discipline, existing arms `:539,:554,:628,:873,:1101,:1143`).

**Plan-set correction (fold in next doc pass):** the RDRS stuck-wake kill-switch precedent cites
`config.py:1628-1650` — actual location is `config.py:2098` (the cited range is compaction token params).
Substance unaffected.

## 11. Risks

- 🔴 **None found.** No premise broken; no unsafe-close path identified; every error leg lands on skip/hold;
  exactly-once holds under all enumerated interleavings.
- 🟡 A1–A4 above (observability, TOCTOU pin-or-close, notify-loss ticket, retry disclosure).
- 🟡 Process: concurrent-lane merge pressure on the 4,946-line `job_recovery_service.py` — keep the leg
  strictly additive, land early.
- 🟢 "When" forensics are log-only (no finalized-at column) — acceptable v1; revisit if forensics demand it.
- 🟢 SQLite-side lock INSERT guard absence (triggers are PG-only, `manager.py:8199-8206`) — dev-only +
  admission belt; accepted.

## 12. Decisions pending (leader ratification — none blocking)

1. **D-6 default-ON** — confirmed by this review per codified house policy; if the leader weighs
   first-release caution differently, the flip is one Field default + no redesign (planner priced this).
2. **D-8 rider inclusion** — confirmed; overrule = route R-10 to a (currently nonexistent) owner lane and
   accept the census staying born-broken.

## 13. Open questions / unverified

- Live incidence of zombie-ACTIVE rows in prod today — not measured (read-only pass, no DB contact).
- FE runtime rendering of raw status strings — moot with registration (canonicalized upstream); static read only.
- Exact candidate-SQL NULL-handling shape — implementation-stage, pinned per D-12/D-13.
- Un-discounted E1 inside notify's mission-live guard (`work_notifier.py:~703`) — expected hold, matching
  plan §7; runtime confirmation belongs to the functional gate.

---

*Adjudication: D-1 CONFIRMED (c) · D-2 confirmed sound (liveness conditional, disclosed) · D-3 CONFIRMED
tokens + A4 · D-4 confirmed exactly-once + A2/A3 · D-5 CONFIRMED delta-EMPTY + A1 · D-6 CONFIRMED default-ON ·
D-8 CONFIRMED rider · R-9 EXCLUSION endorsed. Proceed to implementation with A1–A7 folded into the plan and
the D-13 pin pack.*
