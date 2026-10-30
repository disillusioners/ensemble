# Technical Analysis: missed-finalize / zombie-ACTIVE JobItem close path (wi-78967dba)

Date: 2026-10-30
Author: technical-analysis worker (planner dispatch), consumed `technical-analysis` skill v1.2.0
Analysis depth: deep-dive (design half of the plan set)
Status: Ready for plan-creation worker

Evidence base: `research-findings-core.md` + `research-findings-repair-riders.md` (this directory,
same-hour file:line-verified), plus independent source reads in the lane worktree
`/home/nea/ensemble-src-wt-missed-finalize` @ `22fe83317` (all `daemon/...` citations below are my
own reads unless attributed to a findings file). Read-only on source; the only writes in this lane
are the two planning deliverables.

---

## 1. Problem + verified signature

**The class.** A `job_type='task'` JobItem stays `admission_state='active'` after ALL backing Tasks
are terminal, while its driving instance remains non-terminal (RUNNING-stalled). The aborted-
transaction subclass is already retired by the live v0.27.15 pool fixes (stacked lineage) — this
design covers ONLY the pool-independent RUNNING-stalled / zombie-ACTIVE class.

**Linkage semantics (findings-core Q2, verified):**
- `Task.work_id == JobItem.job_id`; `work_id` is UUID4 `unique=True` — at most ONE Task row per
  work_id (`daemon/repositories/task/repository.py:671-696`).
- RETRY children mint a FRESH `work_id` and INHERIT `instance_id` — retry lineage is keyed by
  `instance_id`, not work_id (`task/repository.py:5095-5169`; lineage doc
  `job_recovery_service.py:2554-2571`).
- **"ALL backing Tasks terminal" in query terms** =
  (a) the row `WHERE task.work_id = job.job_id` has `status ∈ {completed, failed, cancelled}`
  (terminal set `task/repository.py:2108-2112`; non-terminal = pending/running/paused + legacy
  `processing`, `mission_live_guard.py:498-504`), AND
  (b) `NOT EXISTS (SELECT 1 FROM task t2 WHERE t2.instance_id = job.instance_id AND t2.status IN
  (non-terminal))` — the instance-scoped inflight probe, `_pattern_f_instance_has_inflight_task`
  (`job_recovery_service.py:4779`). Conjunct (b) is what makes a retry-about-to-mint shape
  ineligible: a pending retry child IS a non-terminal task on the instance.

**Symptom cost (why this matters):** while `admission_state='active'`, the job_id stays in the
active set → `_ACTIVE_JOB_IDS_SUBQUERY` (`lock_repository.py:18-34`) excludes its `job_locks` row
from BOTH the boot sweep and the 90s F3 sweep → the lock is IMMORTAL and permanently occupies one
of `concurrency_limit` slots per `(project_id, queue_id)` (`models.py:478-522` unique constraint
`uq_job_locks_slot`; `start_job_atomic_with_lock` returns `(None, False)` when slots are full,
`repository.py:3333-3444`). With limit=1 legacy queues, ONE zombie blocks the entire queue
indefinitely (findings-riders §1).

**Kind discriminator:** `job_type: str = Field(default="task")` — "task" (serial) vs "message"
(parallel mirror) (`models.py:372`). Message-kind rows NEVER insert job_locks
(`docs/architecture/job-as-front-primitive-invariants.md:137-139`, findings-riders §1) — the slot-
pressure symptom is task-kind only.

---

## 2. Coverage map — why the class falls through every existing admission_state writer

Authoritative census: `KNOWN_ADMISSION_STATE_WRITERS` frozen at 23 entries
(`daemon/job_state/constitution.py:113-144`). **No sweep service writes admission_state directly**;
all heals route through the registered seams (findings-core Q3). Per-writer coverage of THIS class:

| Writer / service | Trigger predicate | Covers this class? | Why not |
|---|---|---|---|
| `JobFeedbackObserver._finalize_job_db_sync` (W5, the SOLE finalize path, `job_feedback_observer.py:1017-1074`, `:3664-3748`) | event-driven lifecycle event with `bus_pending==0` | **No** | the event was MISSED — that is the incident; the observer never re-derives it |
| Pattern (a)/P1 (`job_recovery_service.py:1318-1349`) | PENDING tasks older than 300s | **No** | PENDING-driven by construction; this class has ZERO pending tasks → zero P1 warnings (findings-core Q6) |
| Pattern (b)/F10 (`:1139-1255`) | done JobItem + RUNNING Task | **No** | inverse shape |
| Pattern (e) (`:2031-2034`) | PENDING process_report + dead parent | **No** | different shape |
| F-1 mirror backstop `_reconcile_terminal_message_mirrors` (`:851-919`, every drift cycle `:1135`) | ACTIVE **message-kind** mirror + linked Task terminal, no age bound | **No** | message-kind only (see §5) |
| Pattern (f) f1 (`:3060-3454`) | `task is None` + instance alive + 900s graces | **No** | strict `task is None` predicate (`:2474-2477`); this class HAS a terminal Task row |
| Pattern (f) FAILED/CANCELLED arm (`:2547-2775`) | Task FAILED/CANCELLED + retry-child quiesced + **E1 gate** (`:2666-2699`) | **Partial — held** | E1 says live because the zombie's own non-terminal instance counts (leg b/c) |
| Pattern (f) f2 (`:2777-3058`) | Task COMPLETED + bus_pending==0 + 60s age + **E1 gate** (`:2970-3001`) | **Partial — held** | same E1 self-hold |
| `mission_strand_healer` (roots only, `:26`, `:84`) | running-zombie strand requires latest task **terminal-COMPLETED** (`:86-93`) + all descendants terminal | **No** | RUNNING-stalled roots with FAILED/CANCELLED task or no COMPLETED task are NEVER swept (`:131-133`); non-root jobs out of scope entirely |
| `watch_reconcile_sweep` (`:64`, `:109-115`) | held watcher rows | **No** | self-declared non-writer of admission_state; can only deliver AFTER something else terminalized |
| `report_delivery_recovery` (7 lanes) | report_injections / messages | **No** | repairs delivery, not admission; lane 7 holds on JobItem-live probe (findings-core Q3) |
| `job_lock_sweep` (F3) | stale locks | **No** | releases the lock symptom ONLY when the job left the active set — the zombie never does |

**The gap, precisely:** for a task-kind `active` JobItem whose backing Tasks are terminal and whose
instance remains non-terminal, the only paths that could terminalize it (f FAILED/CANCELLED arm,
f2) are held by E1 every 300s cycle because the tree walk counts the zombie's own non-terminal
instance as live evidence (findings-core Q3 verdict, Q4 self-hold mechanism). The exits are (1) the
6h tree-activity backstop — starvation-unsafe (verified vectors, §4), (2) the member-alive
companion leg — opens only when task rows age past the horizon, (3) external operator repair.
`job_locks` rows age with it (released only at finalize boundaries — observer step 3
`job_feedback_observer.py:3687-3698`, f1/f2 release-by-job) and the queue slot is held for as long.

---

## 3. SHAPE TRADE-OFF MATRIX — (a) dedicated sweep service / (b) extend watch_reconcile_sweep / (c) new leg in the drift reconciler

Naming correction first: the brief's "pattern-(g)" is WRONG — letter (g) is taken by the
defer-self-witness-watchdog (`_pattern_g_defer_self_witness_watchdog`,
`job_recovery_service.py:3456`; boot log + kill-switch `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` at
`api.py:1400-1410`). No Pattern (h) exists anywhere in the service (grep-verified). **The new leg
is pattern (h).** Option (c) below = a pattern-(h) leg inside `JobRecoveryService.reconcile_drift_states`.

| Criterion | (a) new dedicated sweep service | (b) extend `watch_reconcile_sweep` | (c) pattern-(h) leg in drift reconciler |
|---|---|---|---|
| Coverage of the signature | full (own candidate query) | full only if it grows a job-candidate scan — a layer violation | full (own candidate query folded into the drift pass) |
| E1 interplay / no-self-hold | needs the E1 param either way | needs it + couples watcher machinery to admission writes | needs the E1 param either way; E1 already wired at 3 f-arm seams (`:2666-2699`, `:2970-3001`) to copy from |
| Blast radius / regression risk | new api.py boot block, new service file, new shutdown path; LOW overlap with existing code but NEW wiring surface | HIGH — converts a self-declared non-writer (:64, :109-115) into an admission writer; touches watcher CAS machinery used by every notify | MEDIUM — additive leg in an already-hot file, behind its own knobs; existing f-arms untouched (their gates precede the leg) |
| Audit + idempotency fit | same primitives either way (guarded UPDATE + rowcount; §8) | same | same — PLUS direct reuse of the f2 helper's **notify half** (see A7 precision below) and the census-registered `force_finalize_orphan` writer (§6) |
| Scheduling fit | asyncio task + cancel/await (house pattern, `api.py:1709-1751` block) | already an asyncio task — but wrong layer | rides the EXISTING drift asyncio task (`_periodic_drift_reconcile_loop`, `api.py:1477-1487`, loop `:3878+`); zero new scheduling; NO thread+`run_coroutine_threadsafe` bridge (that is the RDRS thread pattern with its 28s stop budget, `report_delivery_recovery.py:873-897` — unnecessary here) |
| Kill-switch / knob fit | strand-heal template knobs, own constructor | would need knobs foreign to its charter | house precedent VERBATIM: `min_pending_age`/`min_orphan_age`/`f1_tree_activity_max_age` are read from `config.services` at `api.py:1446-1470` and passed loop→`reconcile_drift_states(...)` kwargs (`api.py:3878-3960`) — the (h) knobs copy this exact shape |
| Testability | new pack, new fixtures, new service harness | muddies the watcher-sweep packs | packs land in the DENSE existing home `tests/job_queue/` next to `test_orphan_active_job_recovery.py`, `test_pattern_f_mission_live_gate.py`, `test_running_zombie_sweep.py` (findings-riders §8); the f-arm test harness (bus mocks, E1 fixtures) is reusable |
| Precedent weight | strand-healer got a dedicated service — but its OBJECT is the instance/mission tree; ours is the JobItem admission = the drift reconciler's charter | anti-precedent (charter break) | the f-arm family IS the existing taxonomy for missed-finalize shapes; (h) extends it in place; pattern (g) precedent shows legs are added to this service routinely |
| Operational risk | one more always-on task to monitor | highest | lowest marginal: same loop, same logging namespace (`reconcile_drift_states:` prefix), same stats dict fold-in |

### Recommendation

**Pick (c): a pattern-(h) leg inside `JobRecoveryService.reconcile_drift_states`.**

> **RATIFIED @ 9657be93a** — architect confirmed (c), weighted matrix 3.95 vs 2.85 (a) / 2.35 (b);
> dominant axes Cost + Maintainability; premise checks all passed (arm enumeration, letter-(h)
> free, knob precedent verified end-to-end `api.py:1446-1487` → loop `:3931-3940` → signature
> `:925-934`). Contingent process note carried forward: keep the leg strictly additive and land
> early (concurrent-lane merge pressure on the 4,946-line file). See
> `architecture-recommendation.md` §1.

**Reasoning:** The heal object is the JobItem's admission state — exactly the drift reconciler's
charter, whose pattern family (a)–(g) already enumerates the missed-finalize shapes this class
belongs to. Option (c) inherits the candidate-loop infrastructure, the E1 gate wiring, the notify
half of the f2 helper (A7 precision — fire-then-cancel composition only), the census-registered
F5 writer, the knob-passing precedent
(loop kwargs → `reconcile_drift_states` args), and the densest test-pack home — at the cost of one
more leg in an already-hot file, which the knobs/batch-cap/fail-closed discipline bounds. Option
(a) duplicates all of that wiring for an isolation benefit that does not materialize (the leg would
still call the same repositories and helpers). Option (b) is the wrong layer by its own docstring.
**No hybrid**: the E1 parameter change (§4) lives in `mission_live_guard.py` regardless of shape —
it is shared surface, not a reason to split the sweep out.

**Assumptions:** the drift 300s cadence is sufficient (the class is hours old by definition — the
age bound ≥ 7200s dominates); the drift loop remains an asyncio task (true at this HEAD,
`api.py:1477`).

> **A7 precision (folded per architect §1/§10):** "reuse of the f2 helper" means the **NOTIFY HALF
> ONLY** — `_pattern_f_finalize_done` (`job_recovery_service.py:4103`) writes via
> `atomic_transition`, which cannot carry `terminal_reason`; the WRITE seam is
> `force_finalize_orphan` per D-4 (unchanged). Any plan text implying full-helper reuse reads as
> the notify composition only. The pre-filter SQL stays pinned through `asyncio.to_thread` per
> D-12 (existing arms `:539, :554, :628, :873, :1101, :1143`).

**Reversibility:** high — the leg is one method + one candidate query + three knobs + one E1 param;
kill-switch OFF is a complete no-op (strand-healer semantics, `config.py:2651-2653`).

---

## 4. No-self-hold liveness design

### The circularity, restated

E1 (`evaluate_mission_live`, `mission_live_guard.py:327-426`) decides "live" from (a) bus_pending,
(b)/(c) ANY non-terminal tree member or root status, refined by the anchor backstop + member-alive
companion leg. For our class the zombie's own instance is non-terminal PRECISELY BECAUSE the
completion chain that would terminalize it is the missed finalize — the root's (or member's)
non-terminal STATUS is circular bookkeeping evidence, not liveness evidence. The verified
starvation vectors (findings-core Q4) all keep that circular status alive forever.

### The discount: `discount_instance_id` (generalizing the existing `skip_root`)

`evaluate_mission_live` ALREADY has `skip_root: bool = False` — "pre-stamp mode: the root's own
non-terminal status is what the caller's stamp is about to flip — it is not a hold reason on this
evaluation" (`mission_live_guard.py:333`, discount conjunct `:890-896`, second site `:982`;
callers: `mission_strand_healer.py:1175`, `child_reports.py:2884`). Our sweep needs the SAME
discount keyed on the JOB'S OWN INSTANCE (for a root job that equals the root; for a child job it
is a member) — a strict generalization:

- Add optional `discount_instance_id: str | None = None` to `evaluate_mission_live`; inside the
  tree walk, the non-terminal conjunct gains `and not (discount_instance_id and tree_id ==
  discount_instance_id)` at the two sites that already carry the `skip_root` conjunct
  (`:896`, `:982`). Default `None` → **zero behavioral change for every existing caller**
  (f2, f-arms, boot sweeps, emission gate, strand-healer, child_reports — all keep current
  semantics).
- The pattern-(h) leg always passes `discount_instance_id=<job.instance_id>`.

### The decision chain (per candidate)

TERMINALIZE iff ALL hold (any leg unprovable ⇒ SKIP — fail-closed, see polarity below):

1. **Zombie predicate** (§1 query shape): `job_type='task'` AND `admission_state='active'` AND
   `deleted_at IS NULL` AND work_id Task terminal AND instance-scoped inflight probe empty.
2. **Age bound** (monotone clock): `now − max(terminal stamp over the instance's Task rows) ≥
   zombie_close_age_bound_seconds` (default 7200). Keyed on **task terminal stamps**
   (`completed_at` et al.), NOT `instances.last_activity_at` — the strand-healer precedent is
   explicit that activity stamps are frozen-by-design for a stalled instance and that age keys on
   `task.completed_at` (`config.py:2670-2673`; `mission_strand_healer.py:124-133`). A batch
   `(started_at, completed_at)`-per-work_id fetch helper already exists
   (`task/repository.py:701-718`) as the query-shape precedent.
3. **Bus gate**: `bus_pending == 0` for the job; bus unavailable ⇒ fail-safe SKIP (f2 Gate 1
   precedent, `job_recovery_service.py:2844-2868`).
4. **Discounted E1**: `evaluate_mission_live(..., discount_instance_id=job.instance_id,
   bus_pending_count=0)`. `live=True` ⇒ HOLD (skip + counter — a genuinely live tree still holds).
   `live=False` **with** `error or timed_out` ⇒ SKIP (fail-closed divergence, below).
   `live=False` clean ⇒ terminalize.
5. **Write + notify** (§6, §7).

> **A2 probe/commit TOCTOU (folded per architect §10):** a retry child minted between the
> instance inflight probe (leg 1) and the finalize UPDATE is not re-checked inside the
> transaction. Preferred cheap closure per the architect: **re-run the inflight probe (one
> SELECT) inside `force_finalize_orphan`'s transaction**; if that disturbs the census-registered
> seam's contract, the sanctioned fallback is to **pin the accepted sub-second window with a
> mutation-verified test + disclose** (the f-arms share the same window today —
> `architecture-recommendation.md` §4). Phased as a Phase-1 task with the decision rule spelled.

### Each verified starvation vector, addressed

| Vector (findings-core Q4 cite) | Under the (h) decision chain |
|---|---|
| Leg (c) counting the zombie root's own non-terminal status (`mission_live_guard.py:893-898`) | **Neutralized by construction** — `discount_instance_id` removes exactly the job's own instance from the non-terminal count; the same in-loop conjunct shape as `skip_root` `:896`/`:982` |
| Childless-root fallback to the enqueue-refreshed root stamp (`:924-930`) | **Neutralized** — the sweep never consults the anchor; a discounted childless root has no other members ⇒ all-terminal-minus-discount branch ⇒ `live=False` immediately (`:986-1006`, "anchor plays no role"); the 7200s key is task-stamp-based, and enqueue traffic cannot re-roll a task's `completed_at` |
| Missing-anchor disarm (`:1016-1028`) | **Neutralized for the decision** — disarm requires a non-terminal member present; with a non-terminal member the sweep HOLDS anyway (member status = E1-legitimate, §below); with all members terminal the disarm branch is unreachable |
| Non-root member activity re-roll | **Neutralized** — activity stamps only arm E1's own 6h backstop, which the sweep does not use; the sweep's hold rule is member STATUS + live-work evidence, not activity |
| Unbounded member-alive fail-MISS holds (`:638-676`) | **Neutralized for the decision path** — the companion leg probes non-root non-terminal members; in the terminalize-eligible shape there are none to probe ⇒ no fail-MISS is possible. With non-terminal members present we hold regardless (fail-MISS ⇒ hold ⇒ same outcome as the member's status holding) |

### What a legitimate long-running mission WAITS for — and never waits for

- **Waits for (holds the close):** any OTHER member of the mission tree with a non-terminal
  STATUS (plausibly working — its own heal is the f-arms'/strand-healer's charter); bus_pending > 0
  on the job; any non-terminal Task row anywhere on the job's instance (a retry about to run, a
  fresh dispatch); an unprovable leg (probe error ⇒ skip, next cycle retries).
- **Never waits for:** the job's OWN instance's stalled status (circular); tree STATUS stamps alone
  when every task in the tree is terminal and hours old; activity-stamp freshness (re-rollable by
  unrelated traffic); a data gap (missing anchor) as a *reason to close* — data gaps hold, never
  close (fail-closed).

**Subordination to E1's legitimate holds:** the discount removes ONLY the job's own instance's
circular status — every OTHER member's liveness still holds the close through the normal E1
verdict, and the age bound applies ONLY to the zombie-shape predicate (a healthy tree never enters
the candidate set: its work_id Task or an instance task is non-terminal ⇒ predicate leg 1 fails).
We do NOT weaken E1 for any existing consumer (default-None param).

### Error polarity — deliberate divergence from the f-arms

The f-arms finalize through guard errors (fail-open WARN, `job_recovery_service.py:2700-2711`,
`:3002-3010`; guard wrapper fail-open `mission_live_guard.py:410-426`) on the rationale that a
missing terminal is worse than a premature one on the HOT path. The (h) leg is a LAST-RESORT
backstop with an hours-wide age bound: on `error or timed_out` it SKIPS and retries next cycle —
the strand-healer fail-closed-all-legs invariant ("every leg must hold; any unprovable leg SKIPS",
`config.py:2640-2644` per findings-riders §2). Cost of fail-closed here: one 300s cycle; benefit:
no terminalizing write on unverifiable state.

### Boundary honestly drawn (disclosed residual)

External holds remain: a child job whose mission tree is otherwise stalled (e.g., a parent
WAITING_CHILDREN on the zombie's own missed report) still holds via the parent's non-terminal
status. That circle is broken by the REPORT side (RDRS lanes deliver missed reports for completed
children; their detection signature is `instances.status=running + task COMPLETED +
report_injections count=0` — RDRS blueprint), not by the admission side. The (h) leg's guarantee is
exactly: **the job's own instance's stalled status can never hold its own close.** Other members'
stalled statuses are healed by their own lanes (strand-healer for COMPLETED-task roots; a disclosed
follow-up for FAILED/CANCELLED-root and non-root instance heals — see D-11).

---

## 5. Finalize semantics

### DONE vs DEAD discrimination

**Always admission DONE.** The backing Task carries a concrete organic verdict
(completed/failed/cancelled); the work unit reached a terminal outcome and only the bookkeeping
finalize was missed. DEAD is the no-verdict shape: f1's `task is None` orphan DEAD
(`orphan_active_no_task_dead`, `job_recovery_service.py:3396-3420`) and the DLQ path — neither
applies (this class HAS a terminal Task by definition). This mirrors the family: f2 → done
(`:4193-4209`), FAILED/CANCELLED arm → its boundary preserves the task verdict ("NOT bare DEAD",
`:2712-2753`).

### terminal_reason token — brief's `orphaned_no_task` is WRONG here

Findings correction #1 stands: `orphaned_no_task` belongs to `reconcile_turn_mirror`'s no-Task arm
(`task/repository.py:2149-2160`) and is a distinct system-level state ("the work was never
delivered to a worker"); it does not appear anywhere in `job_recovery_service.py` (grep-verified,
findings-core discrepancy 1).

**Design constraint discovered in source:** the public work-record status is DERIVED from
`terminal_reason` — `admission_state='done' AND terminal_reason set → canonical status =
canonicalize_status(terminal_reason)` (`work_status.py:319-367`); the JobItem `status` column is no
longer written (Phase 4 cleanup, `work_status.py:97-100`). Therefore the token choice DETERMINES
the API-visible outcome: a single provenance token mapped to `cancelled` (the `orphan_retired`
pattern, `:117-124`) would MISREPRESENT a completed task; a token mapped to itself would mint a
new canonical status and HIDE the organic outcome.

**Proposal — a three-token provenance set, each collapsing onto the organic outcome:**

| Backing Task status | `terminal_reason` stamped | `_STATUS_CANONICAL_MAP` entry (work_status.py:67) | Public canonical status |
|---|---|---|---|
| completed | `zombie_active_completed` | `→ "completed"` | completed |
| failed | `zombie_active_failed` | `→ "failed"` | failed |
| cancelled | `zombie_active_cancelled` | `→ "cancelled"` | cancelled |

Registration surface: three entries in `_STATUS_CANONICAL_MAP` (`work_status.py:67-142`); the
reverse index `_CANONICAL_TO_SOURCES` builds FROM the map at import time (`:181-205`), so
`terminal_reason_variants_for` picks them up automatically — no other registration site. **No
`_TERMINAL_STATUSES` change** (`:168-177`): the canonical targets are already terminal (unlike
`orphaned_no_task`, which mapped to itself and needed the set addition, `:130-142`). The token is
query-attributable via the existing index `idx_job_queue_terminal_reason` (`models.py:307-313`) —
every heal findable by `terminal_reason IN ('zombie_active_completed','zombie_active_failed',
'zombie_active_cancelled')` *(A5, folded: attribution/forensics queries use the explicit IN-list,
NOT a prefix `LIKE 'zombie_active_%'` — PG btree is prefix-blind without `text_pattern_ops`;
architect §10)*.

> **RATIFIED @ 9657be93a (D-3)** — three synthetic tokens CONFIRMED. The architect proved
> pass-through is **provably harmful**, not merely weaker: `_derive_legacy_status` gates on map
> membership (`work_status.py:401-416`) — an unregistered token on a `done` row falls through to
> `_ADMISSION_TO_LEGACY_STATUS['done']='completed'`, i.e. pass-through would misreport
> `zombie_active_failed` as completed, and `_job_to_record` (`work_resolver.py:1765-1770`) would
> leak the raw token as `record.status`. Consumer census: zero consumers require a code change
> the plan missed (`architecture-recommendation.md` §5).
>
> **A4 disclosure (folded per architect §5/§10):** `JobRetryEngine` reads `failed_at`, NOT
> `terminal_reason` (`job_retry_engine.py:183, 287`) — healed `zombie_active_failed` rows carry
> no `failed_at` ⇒ `atomic_retry` is FORECLOSED for them. Disclosed as intended semantics. Do
> **NOT** stamp `failed_at` on heal — that would re-arm 2h+-stale work for retry.

**PAUSED protections preserved:** the cured semantics are untouched — the (h) predicate requires
the work_id Task TERMINAL and the instance inflight probe EMPTY; PAUSED tasks are non-terminal
(`mission_live_guard.py:498-504`) ⇒ paused shapes fail predicate leg 1 and never reach the write.
The f1 strict `task is None` + `orphan_active_skipped_paused` skips (`:2474-2546`) are upstream of
us and unchanged.

### Message-kind: DEFER to F-1 (decided)

The (h) candidate query filters `job_type='task'`. Rationale: (1) a bounded reconcile for message-
kind ALREADY EXISTS and runs every drift cycle with NO age bound — F-1
`_reconcile_terminal_message_mirrors` (`:851-919`, `:1135`; findings-core correction of brief claim
(i)); (2) message-kind rows never hold job_locks (§1) — no slot pressure to cure; (3) a second
admission writer over F-1's rows = double-writer contention and reviewer surface for zero
symptom relief. The pattern-(f) message skip is f-wide (`:2392-2423`) and stays as-is; the (h) leg
simply never selects message rows.

---

## 6. job_locks release — finalize FIRST, release LAST, one transaction

**Write seam: REUSE `force_finalize_orphan(job_id, terminal_reason)` — an already census-registered
writer.** Verified shape (`repository.py:4288-4347`): ONE `engine.begin()` transaction = guarded
`UPDATE job_queue_items SET admission_state='done', terminal_reason=:reason WHERE job_id=:job_id
AND admission_state='active' AND deleted_at IS NULL` → `rowcount==0` ⇒ concurrent writer won ⇒
return None, **no lock-side action** (`:4325-4329`) — else `DELETE FROM job_locks WHERE
job_id=:job_id` in the SAME transaction, job-scoped (W1: sibling/successor locks survive,
`:4258-4269`), skipped when `instance_id` is NULL (virtual-job rows, `:4270-4273`). The docstring
proves the shape against the live PG trigger suite: the deferred `trg_job_locks_active_guard`
inspects committed state and is satisfied once the UPDATE flips the row out of the active set
(`:4237-4257`).

Why reuse rather than a new writer: the constitution census is frozen at 23 writers
(`constitution.py:113-144`) — routing through an EXISTING registered writer means **zero
constitution churn**; the F5 same-commit pairing, the W1 scoping, and the race semantics are
already mutation-proven (findings-riders §1). The name says "orphan" but the semantics are exactly
"force-finalize an active JobItem + release its lock in one transaction" — the planner may add a
thin alias or pass-through; the census entry does not change.

**Ordering guarantee:** finalize-FIRST / release-LAST is intrinsic to the single-transaction shape
(JobItem UPDATE commits together with — never after — the lock DELETE). This is the canonical
in-source ordering rule (`job_recovery_service.py:1400-1416`: "finalize FIRST, then cancel… the
previous order was an unrecoverable wedge") and the operator-runbook form (findings-riders §2).
Crash window: NONE between the two writes (same transaction) — the entire reason F5 exists.

**Double-admission risk (brief's hard requirement):** a lock deleted while its job still claims
active would free a slot for a second admission of work the zombie "owns". Impossible here: the
lock DELETE only executes AFTER the guarded UPDATE won (rowcount==1), inside the same transaction —
the job is out of the active set before/at the same commit as the release. Belt-and-braces: every
OTHER bulk lock DELETE carries the `_ACTIVE_JOB_IDS_SUBQUERY` NOT-IN guard
(`lock_repository.py:18-34`), and `trg_job_locks_active_guard` enforces the INSERT side
(`manager.py:8201-8206`).

**Boot-sweep + 90s F3 interactions:** boot `recover_stale_job_locks()` runs before first acquire
(`api.py:933-944`); `JobLockSweepService` is ALWAYS-ON, interval-only knob, NO kill-switch BY HARD
POLICY (`config.py:2437-2438`, `:2456-2457`) — this design does NOT touch it (commission
constraint). Both sweeps key on the NOT-IN-active-set guard, so they ignore the zombie until (h)
flips it — after which they would clean any residual lock row within 90s (defense-in-depth for the
impossible-by-construction crash-between-writes case).

---

## 7. Watcher handling

1. **Notify through the existing partition/CAS path — never a new notify-then-claim site.** After
   the winning finalize, the (h) leg calls `JobQueueService.notify_watchers(job_id, status)`
   (`job_queue_service.py:377`, "safe to call even if no watchers exist", `:388`) →
   `WorkNotifier.notify_work_watchers` three-bucket classifier + claim-first CAS
   (`work_notifier.py:346-380`). The repo-level CAS (`claim_watchers_for_job_for_instances`,
   atomic DELETE...RETURNING BEFORE any enqueue) is the ONLY exactly-once enforcement —
   "Do not add new notify-then-claim call sites" (`work_notifier.py:401-409`, findings-riders §3).
   f2's `_pattern_f_finalize_done` helper already composes exactly this fire-then-cancel sequence
   (`job_recovery_service.py:4103-4234`) — reuse its NOTIFY composition shape (A7: the helper's
   write half is `atomic_transition` and cannot carry `terminal_reason`; the write seam is
   `force_finalize_orphan`, §6).
2. **Terminal cleanup:** on terminal status the notify path itself removes the rows — `if status in
   ALL_TERMINAL_STATES: remove_all_watches_for_job(job_id)` (idempotent DELETE,
   `job_queue_service.py:506-509`).
3. **Zero-watcher terminal-fire WARN:** routing through `notify_work_watchers` gets the PP1 WARN
   for free — "PP1 zero-watcher terminal fire: work_id=… status=… no watchers found, the terminal
   report is NOT being delivered…" + pointer to the durable `job_completed` event row
   (`work_notifier.py:515-545`; observer twin `job_feedback_observer.py:2273`). Since `job_watchers`
   keeps no history, this WARN is the only trace — the (h) leg's own log line should reference the
   same token family so the two lines correlate in forensics.
4. **Held `mission_terminal` rows:** after the (h) finalize the MISSION may still be live (we did
   not touch instance statuses) — `held_for_mission` bucket rows on the mission root survive the
   notify (they are skipped, not claimed, `work_notifier.py:363-366`) and re-fire later through
   `watch_reconcile_sweep` → `reconcile_held_watches_for_instance` (`job_queue_service.py:753-784`).
   Correct-by-design: our close is admission-scoped, not mission-scoped.
5. **stored_watches:** rows reference work_ids and the GC's own liveness predicates — no
   interaction with an admission finalize (findings-riders §3).

> **A3 post-commit notify loss (folded per architect §4/§10):** a crash between the finalize
> commit and the notify leaves watcher rows orphaned with NO WARN and no event row — the
> f2-precedent exposure class. Folded posture: the (h) log line carries an explicit
> `notify=<outcome>` field on every close (the outcome is recorded even when notify is lost), and
> "terminal job with surviving watcher rows" routes into a `watch_reconcile_sweep` follow-up
> ticket (its zombie-GC leg already retires rows whose job is terminal and liveness is terminal —
> `watch_reconcile_sweep.py` `remove_all_watches_for_job` at the :946 call site). Precedent-
> consistent with f2's best-effort notify.

---

## 8. Idempotency + concurrent-actor safety

**Epoch is unusable** — `_compute_epoch` literally `return 1` (`mission_resolver.py:1005`, module
docstring `:84-86`; epoch=None only when degraded). No epoch CAS exists to build on.

**The safety design is guarded-UPDATE + rowcount, at exactly one write seam:**

| Concurrent actor | Interplay with (h) | Why safe |
|---|---|---|
| f-arms (f2 / FAILED-CANCELLED arm) | both may target the same job in the same 300s cycle | both write via guarded `WHERE admission_state='active'` transitions (f2 `:4193-4209`; (h) via `force_finalize_orphan` `:4300-4329`); first commit wins, loser sees rowcount 0 ⇒ no-op INCLUDING no lock-side action (`:4325-4329`) |
| JobFeedbackObserver (production chain, e.g. strand-healer-driven) | observer finalize may land concurrently | same guarded seam (observer's atomic transition `:3680-3683`); whoever wins owns the lock release in-txn |
| F-1 mirror backstop | disjoint row set (message-kind vs task-kind filter) | no overlap by construction (§5) |
| mission strand-healer | drives instances, not admission; its running-zombie strand targets COMPLETED-task ROOTS | predicate key is the instance/task, not the JobItem — unaffected; if its chain-drive finalizes the job first, (h)'s guarded UPDATE loses and no-ops |
| operator repair lane (`ens_db_repair_execute`) | operator may terminalize manually | operator SQL racing (h) hits the same guarded predicate; audit-row + nonce ceremony unaffected (findings-riders §2); the (h) log line makes the automated side attributable |
| (h) re-run after its own success | next cycle re-selects candidates | admission no longer 'active' ⇒ candidate query misses ⇒ no-op (self-idempotent) |

Additional invariants: terminal-only writes are same-state no-ops at the state machine
(`job_state_machine.py:108-110`); the notify CAS is exactly-once per watcher row
(`work_notifier.py:368-380`); `remove_all_watches_for_job` is idempotent. The JobItem version-id
column (`models.py:410+`, SQLAlchemy `version_id_col`) adds ORM-level stale-detection on top.

---

## 9. Audit surface — DECIDED: structured log lines + counters, NO new table

**Decision: strand-healer precedent — no DB audit table.**

Rationale:
1. **The durable record already exists — the job row itself.** Every (h) act mutates
   `job_queue_items` with a query-attributable `terminal_reason` token (indexed,
   `models.py:307-313`) + admission transition; the strand-healer's equivalent "durable record is
   the production completion chain the heal drives" (`config.py:2687-2689`, findings-riders §4).
   The notify_audit table exists because its acts (refusals, sends) leave NO other durable trace —
   not our situation.
2. **Migration delta for v0.27.16 stays EMPTY.** A table = +1 migration file (rollback-safe
   adjudication needed; additive CREATE TABLE IF NOT EXISTS would very likely adjudicate
   rollback-safe per the notify_audit/board-tables/wedge-captures precedents, but empty-delta is
   the cleanest standing-rule class: "delta-empty-legitimate ⇒ ENSEMBLE_ROLLBACK_SAFE=1"
   v0.27.13/v0.27.14 precedent) AND carries the full ship checklist: model + IF-NOT-EXISTS `.sql`
   + api.py late-import before create_all (`api.py:961-967` seam, findings-riders §4 Q4) + declared
   audit tier. All ceremony, no marginal audit value here.
3. **Observability spec:** structured greppable log lines in the house shape —
   `reconcile_drift_states: Pattern (h) zombie-active close …` with
   `{job_id, work_id, instance_id, task_status, age_seconds, terminal_reason}` — folded into the
   drift stats dict the loop already logs (`api.py:3878+` returns stats per pass); counters:
   `examined`, `age_qualified`, `mission_live_holds`, `error_skips`, `finalized`,
   `race_lost_noop`, `zero_watcher_warns`. Skips log their reason token
   (`zombie_close_skipped_mission_live` / `_age_unmet` / `_bus_pending` / `_guard_error` /
   `_disabled`), mirroring the f-family detail-token style.

> **A1 stuck-closed observability (folded per architect §6/§10):** the drift loop logs stats only
> when `reconciled>0` today (`api.py:3926-3935`) — a stuck-closed heal (examined>0,
> finalized=0, holds/skips accumulating) would otherwise be INVISIBLE. Folded requirements:
> (i) the (h) counter block logs whenever **`examined>0`** — NOT gated on `reconciled>0`;
> (ii) non-zero `error_skips` surfaces in the drift-stats WARN line; (iii) an **escalating WARN**
> fires when the same job accumulates ≥K consecutive error_skips/mission_live_holds (K≈40 ≈ 3.3h
> at the 300s cadence). Counters alone are forensic, not operational.

---

## 10. Config knobs + kill-switch (strand-heal template, `config.py:2645-2695`)

New `ServicesConfig` Fields (env auto-prefix `SERVICES_`, pydantic FAIL FAST at boot on
out-of-range, wired by direct `config.services.*` read in the api.py drift block
`api.py:1446-1487` → loop kwargs → `reconcile_drift_states` args, same as
`f1_tree_activity_max_age_seconds`):

| Knob | Field | Default | Rationale |
|---|---|---|---|
| Kill-switch | `drift_reconcile_zombie_close_enabled: bool` | `True` (env `SERVICES_DRIFT_RECONCILE_ZOMBIE_CLOSE_ENABLED`) | False ⇒ COMPLETE no-op (no candidate query, no probe) — strand-healer disable semantics `config.py:2651-2653` |
| Age bound | `drift_reconcile_zombie_close_age_bound_seconds: int = Field(ge=1)` | `7200` | strand-healer running-zombie uses 7200 on the SAME evidence class (task terminal stamps, `config.py:2657-2679`); hours-wide by design — the leg is a last-resort backstop behind the f-arms' 300s cycles |
| Batch cap | `drift_reconcile_zombie_close_batch_cap: int = Field(ge=1)` | `25` | deliberately SMALL — the leg performs TERMINALIZING WRITES; tighter cap = smaller blast radius of a mis-predicate (`config.py:2684-2689`) |

No separate interval knob: the leg rides `drift_reconcile_interval_seconds` (300s default,
`config.py:2008-2015`) — at a 2h age bound a 300s cadence adds negligible latency. The candidate
SCAN itself is bounded by the batch cap (LIMIT on the pre-filtered SQL, remainder next tick —
`config.py:2684-2690` semantics).

**Default posture: default-ON** (kill-switch exists for the operator, not as the default posture —
`config.py:2640-2644` justification pattern). Why ON despite being an automated terminalizing
writer: fail-closed predicate (any unprovable leg skips), hours-wide monotone age gate, batch cap,
guarded single-seam write reusable from a mutation-proven writer, and the alternative is the
DEMONSTRATED unbounded harm (immortal lock slots; limit=1 queues blocked indefinitely, §1).
Rollout: dev-staged, promote-gated, kill-switch OFF = instant operator escape hatch.

> **RATIFIED @ 9657be93a (D-6)** — default-ON CONFIRMED, 4.15 vs 3.20; dominant axis Risk ("the
> riskiest thing is the disease, not the heal"). House policy is codified, not vibes:
> `watch_reconcile_sweep.py:~56-60` carries the rule that "a kill-switch defaulting OFF that
> gates a fix is an unacceptable deliverable"; strand-heal shipped default-ON with the identical
> containment profile and ran clean since 2026-10-21. NON-BLOCKING note (architect §12): if the
> leader weighs first-release caution differently, the flip is ONE Field default + no redesign
> (priced in phase1-plan.md §7). *(Citation correction verified: the RDRS stuck-wake kill-switch
> precedent lives at `config.py:2098` — NOT `config.py:1628-1650` (compaction token params); the
> wrong-range form appears NOWHERE in this plan set — the plan set's own RDRS cites are correct:
> `config.py:2098-2120` in findings-riders §5, thread-bridge `report_delivery_recovery.py:873-897`.)*

---

## 11. RIDER DISPOSITIONS

### R-10 — wedge-capture store-census defect (`pg_class.n_live_tup`): INCLUDE as rider (low blast radius)

- **Defect CONFIRMED at base:** `_read_store_census` selects `c.n_live_tup` FROM `pg_class`
  (`db_capture.py:374-386`) — the column exists only on `pg_stat_user_tables`; on real PG the
  census ALWAYS fails `UndefinedColumn` (broad except `:388-390` ⇒ `status: failed:UndefinedColumn`)
  and the aborted transaction cascades `InFailedSqlTransaction` into the third read. Independently
  measured on scratch PG16 (`tests/postgres/test_dbpool_wedge_capture_pg.py:26-46`, "BORN-BROKEN").
  v0.27.13's F5 fixed ONLY the `tasks`→`task` relation error — the census defect remains at this
  HEAD (findings-riders §6).
- **Minimal fix:** keep `FROM pg_class c JOIN pg_namespace n …` + `pg_total_relation_size(c.oid)`;
  replace `c.n_live_tup::bigint` with `s.n_live_tup::bigint` via
  `LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid` (NULL-safe — the consumer already handles
  None, `:392-397`). In-repo correct-query precedent: `ens_db_tools.py:1223`.
- **Blast radius:** ONE function, PG-only path (SQLite unaffected); behavior change = census goes
  from always-failed to populated + the read-3 cascade abort disappears (strictly a repair).
- **Test surface:** the existing static pins SURVIVE UNCHANGED — `FROM pg_class c` pin
  (`test_dbpool_wedge_capture_static.py:102`), pg_namespace join (`:105`), schema filter (`:108`),
  `pg_total_relation_size(c.oid)` (`:111`), own-function AST pin (`:115-130`), exactly-one
  `FROM pg_class c` + one `FROM task t` (`:132-137`) — a LEFT-JOIN-adding fix satisfies all of
  them (only a pg_class-dropping rewrite would redden). The real-PG pack
  `test_dbpool_wedge_capture_pg.py` needs its (ii) expectations RE-POINTED from "failure mode is
  not the F5 cascade" to "census populated" (`:42-44`).
- **Why include in this lane:** it repairs the observability instrument (wedge captures) that
  motivates and verifies exactly this incident class; the fix is one query + one pack re-point;
  no schema/migration surface. (Scope-discipline counter-argument acknowledged — flagged for
  architect review, D-8.)

### R-9 — engine idle-in-txn backstop enablement: EXCLUDE — register as follow-up with the exact one-edit site

- **NOT trivially safe.** The D-47 flag is `MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED = False`
  (`factory.py:278`) with the DISABLED rationale on record: the bound's own sizing rule (≥4×
  measured max legitimate hold, floor 30s) cannot be met — the only trustworthy measurement
  precondition (post-F8 re-measurement of the REAL fetch path, DR-11) has NOT been done, and the
  server TERMINATES the backend on this timeout (`constants.py:314-344`). The 60_000ms constant
  coincides with legitimate pool_timeout waits; enabling without the measurement risks reaping
  legitimate transactions (findings-riders §7).
- **Scope confusion to avoid:** the R-9 item named in the split verdict is the ADAPTER-pool reap —
  60s there would reap legitimate 73-99s checkpoint `aget`s (measured, `db_capture.py:407-409`);
  a safe adapter bound is minutes-scale and needs its own measurement. The D-47 manager-engine
  flag does not even reach that pool.
- **Register as follow-up:** one-edit site = flip `factory.py:278` to True — "Editing the template
  alone is NOT enablement" (`constants.py:334-342`); IF ever enabled, the
  `SET LOCAL statement_timeout = 0` carve-out inside `manager.py::_ensure_postgres_columns` becomes
  MANDATORY (`constants.py:342-344`). Evidence that would justify flipping later: a trustworthy
  post-F8 measurement of the manager engine's legitimate hold on the real fetch path with the
  bound sized ≥4× that figure.

---

## 12. Risks + mitigations (highest severity first)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Mis-predicate premature finalize** — closing a job that would have legitimately continued (imminent retry mint, deferred re-dispatch) | High | zombie predicate leg (b) instance-scoped inflight probe catches pending retry children (fresh work_id, inherited instance_id); bus gate; hours-wide monotone age (7200s) on task terminal stamps; discounted-E1 still holds on ANY other member's liveness; batch cap 25; kill-switch; mutation-verified pin pack (plant→RED→GREEN per leg, digest-proven — findings-riders §8 discipline) |
| 2 | **E1 surface regression** — `discount_instance_id` leaking into existing consumers' verdicts | High | default `None` = byte-identical behavior; discount conjunct added ONLY at the two sites already carrying `skip_root` (`mission_live_guard.py:896`, `:982`); pin: existing-caller verdicts unchanged with param default (f2 gate pack `test_pattern_f_mission_live_gate.py` neighborhood) |
| 3 | **Concurrent-actor race anomaly** — double finalize / double lock release / double notify vs f-arms, observer, strand-healer, operator | Medium-High | single census-registered write seam with guarded UPDATE + rowcount (loser no-ops INCLUDING lock side, `repository.py:4325-4329`); same-transaction finalize+release (no crash window); notify exactly-once via claim-CAS; pin pack planting concurrent legs (two actors, one winner) |
| 4 | **Drift-loop latency / event-loop regression** — the (h) scan adding sync-DB time on the loop (the v0.27.14 incident class) | Medium | candidate pre-filter as ONE SQL round-trip (work_id-terminal JOIN + instance NOT EXISTS + age filter + LIMIT); per-candidate E1 via existing `asyncio.to_thread` walk (`mission_live_guard.py:803-825`); all repository calls off-loop per the eventloop lane discipline (round-trip counts are a reviewed metric); loop-lag pins exist from that lane |
| 5 | **PG trigger interplay on the write** — deferred `trg_job_locks_active_guard` / active-set guards rejecting the close | Medium | reuse `force_finalize_orphan` verbatim — its shape is documented AGAINST the live trigger suite (`repository.py:4237-4257`) and mutation-proven (F5); real-PG leg in the test pack (trigger suite + lock release verification) |
| 6 | **Watcher double-notify or silent loss** on the (h) fire | Medium | route ONLY through `notify_work_watchers` partition/CAS (no new notify-then-claim site, `work_notifier.py:401-409`); zero-watcher WARN re-used verbatim; held mission_terminal rows deliberately survive (mission may still be live) |
| 7 | **Scope creep into instance-status heals** — expectation that (h) also terminalizes the stalled instance | Low-Medium | explicit scope boundary (D-11): admission+lock only; instance heals remain strand-healer family (+ disclosed follow-up for non-COMPLETED-root/non-root shapes); RDRS owns missed-report delivery |
| 8 | **Token vocabulary churn** — new `terminal_reason` tokens surprising status-filter consumers | Low | tokens collapse onto EXISTING canonical statuses (completed/failed/cancelled) — `is_terminal` unchanged, status-filter surfaces unchanged (`job_queue/repository.py:48-58`); `terminal_reason_variants_for` auto-derives; documented in the migration-free release notes |

---

## Open Questions

None blocking. ~~Four decisions are marked open-for-architect-review~~ **All four ratified by the
architect @ `9657be93a`** (D-1 shape 3.95/5 · D-3 tokens with pass-through proven harmful ·
D-6 default-ON 4.15 vs 3.20 · D-8 R-10 rider 4.35; R-9 exclusion endorsed) — verdicts recorded in
`decisions.md` and `architecture-recommendation.md` (§0, §12). Two NON-BLOCKING leader-ratification
notes stand (D-6 one-Field flip; D-8 overrule = census stays born-broken) — both priced in the
phase-plan contingencies, neither blocks implementation. Remaining open questions per the
architect §13 (live prod incidence unmeasured; exact candidate-SQL NULL-handling = implementation
stage; un-discounted E1 inside notify's mission-live guard = functional-gate confirmation) belong
to Phases 0/4/5 — none is a design input.

## References

- **`architecture-recommendation.md`** (this directory, architect @ `9657be93a`) — the ratified
  verdict record: §1 shape matrix, §2 root-cause git evidence (`47b69bf00`), §3 no-self-hold
  confirmation, §4 race-interleaving table, §5 D-3 consumer census, §10 amendments A1–A7 folded
  herein, §12 non-blocking leader notes, §13 open questions routed to gates
- Findings (this directory): `research-findings-core.md` (Q1-Q9 + discrepancies),
  `research-findings-repair-riders.md` (§1-§8 + discrepancies) — primary evidence base
- `daemon/services/job_state_machine.py:53-62` — admission transitions; same-state no-ops `:108-110`
- `daemon/job_state/constitution.py:113-144` — frozen 23-writer census
- `daemon/services/job_feedback_observer.py:1017-1074`, `:3664-3748` — sole finalize path
- `daemon/services/job_recovery_service.py` — f-family gates `:2392-2423`, `:2547-2775`,
  `:2777-3058`, `:3060-3454`; ordering rule `:1400-1416`; pattern-(g) taken `:3456`;
  f2 helper `:4103-4234`
- `daemon/services/mission_live_guard.py` — signature+skip_root `:327-360`, discount conjuncts
  `:890-898`/`:982`, anchor scope `:899-930`, disarm `:1016-1028`, companion leg `:556-745`,
  fail-open wrapper `:410-426`
- `daemon/repositories/job_queue/repository.py` — `force_finalize_orphan` `:4288-4347` (+ trigger
  docstring `:4237-4257`); `start_job_atomic_with_lock` `:3333-3444`
- `daemon/repositories/job_queue/lock_repository.py:18-34` — active-set NOT-IN guard
- `daemon/repositories/job_queue/models.py` — `job_type` `:372`, `terminal_reason` `:402`,
  index `:307-313`, lock slot unique `:504-507`
- `daemon/services/work_status.py` — `_STATUS_CANONICAL_MAP` `:67`, terminal set `:168-177`,
  derivation-from-terminal_reason `:319-367`
- `daemon/services/work_notifier.py` — buckets `:346-366`, claim-CAS `:368-380`, ban `:401-409`,
  zero-watcher WARN `:515-545`
- `daemon/services/job_queue_service.py` — `notify_watchers` `:377-388`, terminal remove `:506-509`
- `daemon/api.py` — drift wiring `:1381-1487`, loop `:3878+`; sweep-task pattern `:1709-1751`;
  boot lock recovery `:933-944`; late-import/create_all seam `:961-967`
- `daemon/config.py` — strand-heal template `:2645-2695`; F3 hard policy `:2428-2462`; drift
  knobs `:2008-2015`, `:2211-2219`, `:2236-2270`
- `daemon/services/mission_resolver.py:980-1005` — epoch hard-returns 1
- `daemon/services/wedge_capture/db_capture.py:374-397` — R-10 defect site
- `daemon/repositories/factory.py:278` + `daemon/constants.py:314-344` — R-9 flag + rationale
