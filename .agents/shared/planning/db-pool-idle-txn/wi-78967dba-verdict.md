# Verdict — `wi-78967dba`: does DB transaction hygiene + pool hardening retire the missed-finalize defect class?

- **Lane:** `fix/db-pool-idle-txn` · job `634c38f2` · branch `fix/db-pool-idle-txn`
- **BASE anchor:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-dbpool-idletxn`)
- **Companion artifacts (same directory, written in parallel — cross-reference by filename only):**
  `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md`
  · `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `test-strategy.md` · `audit-method.md`
- **Status:** Draft for review — planning only, zero code changes.

---

## 1. The question, verbatim

> The missed-finalize wedge reconciliation: **does transaction hygiene + pool hardening RETIRE the defect class,
> or does missed-finalize need its own commission?**

Three sub-questions are mandatory:
1. Does the shipped `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*` lane cover the **RUNNING-stalled in-lane
   worker-turn shape** (the zombie-ACTIVE `JobItem`), or only mission-tree roots?
2. Weigh the PROVEN facts (single-transaction finalize cascade; `watch_reconcile_sweep` never touches
   `admission_state`; `orphan_watcher_sweep` covers only PENDING bus watchers; drift-reconcile needs no-Task or
   TERMINAL-Task while E1 holds both; restart-mid-turn recovery is boot-only).
3. Disclose the evidence gap for the Oct-27 5h15m explorer-turn wedge.

---

## 2. Executive verdict (one paragraph)

**SPLIT VERDICT — the lane retires the ABORTED-TRANSACTION SUBCLASS of missed-finalize, and does NOT retire the
missed-finalize DEFECT CLASS; the RUNNING-stalled / zombie-ACTIVE `JobItem` gap needs its OWN commission.**
The finalize cascade is a single all-or-nothing transaction, so a healthy pool deterministically converts a
rolled-back finalize into a committed one — that subclass is genuinely closed by F2/F3/F4/F6 hygiene + hardening.
But the *coverage* gap is structural and pool-independent: no periodic sweeper has a finalize leg for a
`JobItem` whose backing Task is non-terminal-but-stalled, the E1 mission-live hold is **self-holding** in exactly
that shape, and the shipped `mission_strand_healer` RUNNING-zombie strand **provably does not cover it**
(roots-only candidate set + a completion-proof anchor that a stalled turn can never satisfy). Separately, the
attribution of the specific Oct-27 5h15m wedge to pool poisoning is **INFERRED, not PROVEN** — no on-disk primary
evidence exists.

---

## 3. Sub-question 1 — does `mission_strand_healer` cover the RUNNING-stalled in-lane worker-turn shape?

### 3.1 What was read

`daemon/services/mission_strand_healer.py` (1634 lines) — the module docstring (`:1-141`), the RUNNING-zombie
constants (`:197-219`), the config plumbing (`:247-314`), the sweep entry point
(`sweep_running_zombies_once`, `:801-881`), the candidate selection (`_find_running_root_ids`, `:883-959`),
and the heal predicate (`_heal_running_zombie_locked`, `:980-1144`).

### 3.2 The answer: **NO — it covers only mission-tree roots, and it never writes `job_queue_items.admission_state`.**

**(a) The candidate set is mission-tree ROOTS ONLY.** The selection query filters
`Instance.status == InstanceStatus.RUNNING.value` **AND** `Instance.parent_id.is_(None)`
(`mission_strand_healer.py:943-944`). The docstring names the anchor explicitly:
*"root.status == RUNNING (parent_id IS NULL) — the zombie posture"* (`:84`). The heal predicate repeats it as a
fail-closed skip: a non-root returns skip class **`not-root`** — *"parent_id is not NULL — non-root running shapes
are out of scope"* (`:998-1003`).
⇒ **PROVEN: an in-lane (non-root) worker turn is out of scope by construction.**

**(b) The anchor requires a COMPLETION PROOF the stalled shape can never satisfy.** The selection inner-joins the
root's LATEST task row and requires `Task.status == COMPLETED` **AND** `Task.completed_at IS NOT NULL`
(`:945-946`). The predicate re-checks it (`:1029` → skip class `latest-task-not-completed`; `:1035` →
`latest-task-no-completed-at`). The docstring is explicit:
> *"A root that can never satisfy the proof (no task row at all, a latest task row that is not COMPLETED, **a live
> turn in flight**) is NOT a candidate: it is **INVISIBLE to this lane**"* (`:891-893`).

⇒ **PROVEN: a RUNNING-stalled turn (non-terminal backing Task) is invisible to this lane.** Even for a root, the
heal skips with class **`non-terminal-task`** — *"N non-terminal task row(s) exist for the root — a turn is still in
flight"* (`:1041-1046`). The age bound cannot rescue it either: the age keys on `task.completed_at`
(`:1063`, predicate correction #1 at `:124-133`), and a stalled turn has **no** `completed_at` stamp at all.

**(c) The heal writes the INSTANCE row, not the `JobItem`.** The module imports only `Instance`, `MessageQueue`
and `Task` models (`:150-161`) — **there is no `JobItem` import and no `admission_state` reference anywhere in
the module** (grep-verified: the only `JobItem` hits are in prose at `:9`, `:76-77`, `:139` and a comment at
`:1268`). The drive path is the production completion chain
`ChildReportsService._process_child_completion_and_notify_parent(root, …)` — *"NEVER a raw status UPDATE and NEVER
`terminate_instance`"* (`:106-109`). The `JobItem` release is a **side effect**: the chain publishes the
lifecycle event that the observer consumes (`job_feedback_observer.py:1017`) to run the `_finalize_job` cascade
(`:110-116`).
⇒ **PROVEN: the strand healer does not, and cannot, write `job_queue_items.admission_state` directly.** It also
explicitly preserves the lane boundary with f2: *"f2 — the lane boundary is preserved deliberately; f2 heals the
JobItem for free once the instance row is terminal"* (`:135-140`).

**(d) The shapes it does heal are the *post-completion* ones.** The docstring's live shape is a root parked in
`running` whose completion EVENT was lost — *"the root's own latest task row terminal-COMPLETED — and the row
stuck for 25h"* (`:73-75`) — and it explicitly states the division of labour:
> *"The f2 drift finalizer heals only the JobItem (the mission-live guard's all-terminal short-circuit at
> `mission_live_guard.py:509-524` releases the held JobItem); the INSTANCE row is a different table and stays
> `running` forever."* (`:76-78`)

⇒ **PROVEN conclusion for sub-question 1: the shipped RUNNING-zombie strand heals the *missing-instance-terminal*
shape for mission ROOTS whose completion proof exists. It does NOT cover the RUNNING-stalled in-lane worker-turn
shape (zombie-ACTIVE `JobItem`), and it does not touch `admission_state` at all.**

**Consequence for the census open question (research-audit-census.md:220-222):** the answer is **NO** — mode
(c)-1 of the census's "failure modes that REMAIN with a healthy pool" is **NOT** already partially closed by
`SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*`. That open question is now **CLOSED by direct code read**.

---

## 4. Sub-question 2 — weighing the PROVEN facts

### 4.1 The finalize write is a single all-or-nothing transaction

`JobFeedbackObserver._finalize_job_db_sync` (`daemon/services/job_feedback_observer.py:3664`) consolidates the
5-step terminal cascade into **ONE** `WriteGuardSession` transaction: (1) the JobItem PROCESSING→COMPLETED/FAILED
in-session UPDATE (guarded by `WHERE admission_state == 'active'`), (2) the instance status UPDATE, (3) the
`job_locks` release DELETE. The docstring is explicit: *"If any step raises, none of them commit (the
`WriteGuardSession.__exit__` rolls back via the underlying `Session.close`)"* (`:3700-3702`); the single-transaction
intent is stated at `:3675` (*"Consolidates the 5-step terminal cascade into a SINGLE `WriteGuardSession`
transaction so partial failures cannot leave inconsistent state"*). The async wrapper is `_finalize_job` (`:1620`).

⇒ **PROVEN: pool health directly determines missed-finalize *when the write fires*.** An aborted transaction on
the finalize connection rolls back the JobItem transition, the instance terminal write **and** the lock release
together, leaving `admission_state='active'` — the missed-finalize signature.

### 4.2 The sweep coverage map (PROVEN, code-cited)

| Sweeper | Cadence | Covers the zombie-ACTIVE `JobItem`? | Citation |
|---|---|---|---|
| `watch_reconcile_sweep` | 300 s | **NO** — watcher rows only; *"The sweep does NOT touch the `admission_state` of any JobItem"* | `daemon/services/watch_reconcile_sweep.py:109-110` |
| `orphan_watcher_sweep` | 90 s | **NO** — cancels PENDING **dependency-bus** watchers whose `source_task_id` is not an active task | `daemon/services/orphan_watcher_sweep.py:5-11` |
| Drift-reconcile f1 | periodic | **PARTIAL** — ACTIVE + **no** Task → DEAD; the leg is **E1-gated** (`orphan_active_skipped_mission_live_f1`) | `daemon/services/job_recovery_service.py:2666-2674` |
| Drift-reconcile f2 | periodic | **PARTIAL** — ACTIVE + **COMPLETED** Task → DONE; **E1-gated** (`orphan_active_skipped_mission_live`); the backstop fires iff a non-terminal tree member exists AND its freshest activity is older than `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` | `job_recovery_service.py:1008-1017`, `:2955-2999` |
| Boot recovery `recover_on_startup` | **boot-only** | Only across a restart | `daemon/api.py:1395` (`recovery_stats = await job_recovery.recover_on_startup()`) |
| P1 pending-watcher warning | periodic | **NO** for RUNNING-stalled — counts only PENDING watchers > 300 s | blueprint + code predicate |

**The healthy-shape exclusion is explicit in the code.** `job_recovery_service.py:1017-1019`:
> *"Healthy shapes (active JobItem + pending/running Task) are **EXPLICITLY EXCLUDED**"* — encoded as a guard.

⇒ **PROVEN: the RUNNING-stalled shape matches NEITHER f1 (needs no Task) NOR f2 (needs a terminal Task).**

### 4.3 The E1 hold is self-holding in exactly this shape

`evaluate_mission_live` (`daemon/services/mission_live_guard.py:327`) is a three-leg guard (bus-pending /
descendant-non-terminal / root-non-terminal) plus a zombie backstop anchored on the freshest tree signal. A
**stalled RUNNING descendant is non-terminal** ⇒ it makes the mission evaluate **live** ⇒ the f1/f2 legs skip
(*"finalize + notify skipped, watcher rows untouched this sweep"*, `job_recovery_service.py:2981-2999`) ⇒ the
JobItem stays `active` ⇒ the guard keeps saying "live" ⇒ **the hold is self-sustaining**.

⇒ **PROVEN: the E1 hold does not merely delay the finalize in the stalled shape — it structurally prevents the
sweeper from ever firing, because the thing that would make the sweep eligible is the very thing the sweep would
have to fix.**

### 4.4 Restart-mid-turn recovery is boot-only

`recover_on_startup` is invoked from `daemon/api.py:1395` at boot. There is no periodic equivalent for the
mid-turn case. The auto-continue boot pass (`daemon/api.py` arm-notify wake sweep + `continue_running_instances_after_restart`)
resumes *instances*, but the `JobItem` finalize for a turn that died mid-flight is still recovered only by the
boot-time orphan finalize.

⇒ **PROVEN: a wedge formed between restarts is not healed until the next restart.**

### 4.5 The synthesis

| Question | Answer | Basis |
|---|---|---|
| Does a healthy pool guarantee a *fired* finalize commits? | **YES** | single-transaction cascade (`:3664`, `:3700-3702`) |
| Does a healthy pool make the sweeps cover the RUNNING-stalled shape? | **NO** | f1/f2 predicates (`:1017-1019`, `:2955-2999`) + `watch_reconcile_sweep.py:109-110` + `orphan_watcher_sweep.py:5-11` |
| Does the E1 hold release in that shape? | **NO — self-holding** | non-terminal descendant ⇒ mission live ⇒ sweep skips (`:2981-2999`) |
| Does the strand healer cover it? | **NO** | §3.2 — roots-only (`:943-944`), completion-proof anchor (`:945-946`, `:891-893`, `:1041-1046`), never writes `admission_state` |
| Does a restart fix it? | **YES, but only at the next boot** | `api.py:1395` |

---

## 5. Sub-question 3 — the evidence gap (disclosed)

**The Oct-27 5h15m explorer-turn wedge has NO on-disk primary evidence.** The census established (and this
verdict re-confirms from the same sources):

- `.agents/tester/RESULTS/` has **no 2026-10-27 or 2026-10-28 file** (newest is
  `2026-10-26-v0278-staged-verification.md`).
- `wi-78967dba`, `5h15`, `five hours` → **zero hits** across `/home/nea/ensemble-src/.agents/**` and the worktree
  `.agents/**` (board items are ari-only and are not on disk in readable form).
- The board item itself was **not read** — the lane's fence forbids board tools (ari-only).
- `.agents/approver/wedge-capture-tracking.md:32-50` (operational note 2026-10-26) rules that the
  aborted-transaction / missing-relation wedge-capture errors are **"inconclusive evidence, not evidence of
  absence"** of a wedge.

**What IS on disk (adjacent evidence):**
- the RCA (`incident-20261026-db-pool-idle-in-transaction.md`): 5 backends `idle in transaction`; the journal
  lines *"Wedge-capture: store census failed: current transaction is aborted, commands ignored until rollback"*
  and *"resume-history read failed: current transaction is aborted"* (`:40-43`); readiness degraded **45×/12 min**
  (`:44-47`); relapse **0 → 6** idle-in-txn backends in ~3 min after restart (`:77-87`); the fix list (`:109-128`).
- `analysis-20261026-v0278-soak-failure.md`: the Oct-26 promote soak rollback — checkpoint `AsyncConnectionPool`
  saturation under the boot/resume burst; degradation began **21 s BEFORE** the resumes; the same binary served
  green ~90 s later when the burst eased.
- the soak-gate planning dir: the pool leak is a CONSTRAINT, and *"the healthy-release rollback CAN RECUR for that
  signature until the leak is repaired"* (`requirements.md:1426`, `risk-safety-register.md:78`,
  `plan-overview.md:84-103`).

⇒ **The pool-causality of THAT SPECIFIC wedge is INFERRED.** Adjacent timing and mechanism match, but no
on-disk artifact records the failing write, its SQLSTATE, or the recovery path for that turn. Absence of captured
evidence in that window is **inconclusive, not exculpatory** (`wedge-capture-tracking.md` ruling).

---

## 6. VERDICT (explicit)

### 6.1 Verdict statement

> **VERDICT: SPLIT — NOT a full retirement.**
>
> **(V1) RETIRED — the aborted-transaction subclass of missed-finalize.**
> A finalize write that FIRES and errors mid-transaction is closed by this lane. The finalize cascade is a single
> all-or-nothing transaction (`job_feedback_observer.py:3664`, `:3700-3702`), so under a healthy pool the write
> COMMITS instead of rolling back whole. Transaction hygiene (F3 BEGIN-gap, F6 mint idempotency, F5 table-name)
> plus pool hardening (F2 manager-engine knobs, F4 probe retention) remove the aborted-transaction trigger, and
> therefore remove this subclass of missed-finalize. **This subclass is genuinely retired by this lane.**
>
> **(V2) NOT RETIRED — the missed-finalize defect class.**
> The **RUNNING-stalled / zombie-ACTIVE `JobItem`** gap survives *regardless of pool health*, because the gap is
> in **sweep coverage**, not in transaction mechanics:
> - `watch_reconcile_sweep` never touches `admission_state` (`watch_reconcile_sweep.py:109-110`);
> - `orphan_watcher_sweep` covers only PENDING dependency-bus watchers (`orphan_watcher_sweep.py:5-11`);
> - drift-reconcile f1 requires **no** Task and f2 requires a **terminal** Task; the healthy shape
>   (active JobItem + pending/running Task) is *explicitly excluded* (`job_recovery_service.py:1017-1019`), and
>   both legs are **E1-gated**, with the hold **self-holding** in this shape (`:2981-2999`);
> - restart-mid-turn recovery is **boot-only** (`api.py:1395`);
> - the shipped `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*` strand **does not cover it**: roots-only candidate
>   set (`mission_strand_healer.py:943-944`), a completion-proof anchor a stalled turn can never satisfy
>   (`:945-946`, `:891-893`, `:1041-1046`), and it never writes `job_queue_items.admission_state` at all.
>
> **⇒ missed-finalize NEEDS ITS OWN COMMISSION.** It should be scoped as a *new periodic finalize leg* (a
> "g" leg in `job_recovery_service`'s drift-reconcile, or a dedicated sweep) for the RUNNING-stalled shape — NOT
> bolted into `mission_strand_healer` (whose lane boundary with f2 is deliberate, `:135-140`, and whose
> completion-proof anchor is the correct fail-closed design for its own shape).
>
> **(V3) INFERRED, NOT PROVEN — the attribution of the Oct-27 5h15m wedge.**
> This lane cannot claim to have prevented THAT wedge: its pool-causality is inferred (§5). The lane's honest
> claim is *"we closed the mechanism class we could prove"*, not *"we closed the Oct-27 incident"*.

### 6.2 Why the verdict is not simply "retired"

The tempting but **wrong** argument is: *"a healthy pool ⇒ no aborted transactions ⇒ no missed finalize."* That
argument breaks in two places, both PROVEN:

1. **A finalize write can fail for reasons unrelated to the pool** (a constraint violation, a lock timeout, a
   serialization failure, a mid-transaction connection drop). The single-transaction design means **any** of
   those rolls back the JobItem transition *and* the instance terminal write *and* the lock release together
   (`:3700-3702`). The pool fix removes one *source* of such failures; it does not add a **retry**, a **partial
   commit**, or a **re-drive** for the finalize. **A finalize that fails for a non-pool reason today produces the
   same zombie-ACTIVE `JobItem` as the Oct-27 wedge did** — and nothing sweeps it.
2. **Pool health does not create coverage.** The RUNNING-stalled shape is a *predicate* gap: no sweeper has a leg
   that matches it (§4.2). A healthy pool cannot make a non-existent leg fire.

### 6.3 What the lane DOES close (and the honest scope statement)

Closed by this lane, with a PROVEN mechanism:
- the **aborted-connection** trigger for missed-finalize (F2/F3/F4/F6);
- the **self-sustaining** shape (leak → pool pressure → probe timeout → retry → more leak) that made the Oct-26
  incident recur in ~3 min;
- the **wedge-capture blind spot** (F5) that made the incident window un-diagnosable — a direct diagnostic
  enabler for *future* missed-finalize forensics.

Not closed by this lane, and must be routed:
- the **RUNNING-stalled / zombie-ACTIVE `JobItem`** finalize gap → **own commission**;
- the **E1 self-holding** interaction (a stalled descendant keeps the mission live, blocking its own repair) →
  **own commission** (a repair that must also be allowed to act *while* the mission evaluates live);
- the **restart-mid-turn** finalize gap (boot-only recovery) → a candidate for the same own-commission scope;
- the **Oct-26 checkpoint-pool saturation** mechanism (a *different* pool, saturation without aborted
  transactions) → already owned by the soak-gate lane's follow-up (`analysis-20261026-v0278-soak-failure.md`).

### 6.4 Recommendation to the dispatcher

1. **Record (V1)-(V3) verbatim** in `decisions.md` — the split is the deliverable; a bare "retired" claim would
   be false and a bare "not retired" would under-claim the real closure.
2. **Dispatch the own-commission** for the RUNNING-stalled / zombie-ACTIVE `JobItem` finalize gap, with the
   scope anchored on this verdict's §4 evidence (the two legs f1/f2's exclusions, the E1 self-holding, the
   boot-only recovery) and the §3 evidence (why the strand healer is not the home for it).
3. **Attach the evidence-gap disclosure** to the lane's final report: the Oct-27 wedge is not independently
   attributable; the lane's closure claim is class-level, not incident-level.
4. If the dispatcher wants a **single** statement for the user: *"The pool leak that made the Oct-26 incident
   self-sustaining is being fixed, and with it the aborted-transaction failures that silently rolled back job
   finalizations. A separate, already-known gap — a job stuck 'active' because its worker turn stalled — was never
   caused by the pool and is not fixed by it; it needs its own pass."*

---

## 7. Fact ledger — PROVEN vs INFERRED

| # | Fact | Status | Citation |
|---|---|---|---|
| 1 | The strand healer's RUNNING-zombie candidate set is RUNNING **roots** only (`parent_id IS NULL`). | **PROVEN** | `daemon/services/mission_strand_healer.py:943-944`; docstring `:84` |
| 2 | A non-root instance is skipped with class `not-root` ("out of scope"). | **PROVEN** | `mission_strand_healer.py:998-1003` |
| 3 | The strand healer's anchor requires the LATEST task row to be terminal-**COMPLETED** with a `completed_at` stamp. | **PROVEN** | `mission_strand_healer.py:945-946`, `:1029-1040` |
| 4 | A live turn in flight makes the instance INVISIBLE to the lane. | **PROVEN** | `mission_strand_healer.py:891-893` (docstring) |
| 5 | A root with a non-terminal task is skipped with class `non-terminal-task`. | **PROVEN** | `mission_strand_healer.py:1041-1046` |
| 6 | The strand healer never writes `job_queue_items.admission_state` (no `JobItem` model import). | **PROVEN** | grep-verified; imports `:150-161`; prose-only mentions `:9`, `:76-77`, `:139`, comment `:1268` |
| 7 | The strand healer's drive path is the production completion chain; the `JobItem` release is a side effect via the observer's `_finalize_job`. | **PROVEN** | `mission_strand_healer.py:106-116` |
| 8 | The f2 lane boundary with the strand healer is deliberate. | **PROVEN** | `mission_strand_healer.py:135-140` |
| 9 | The finalize cascade is ONE `WriteGuardSession` transaction; "if any step raises, none of them commit". | **PROVEN** | `daemon/services/job_feedback_observer.py:3664`, `:3675`, `:3700-3702` |
| 10 | `watch_reconcile_sweep` does NOT touch any `JobItem.admission_state`. | **PROVEN** | `daemon/services/watch_reconcile_sweep.py:109-110` |
| 11 | `orphan_watcher_sweep` covers only PENDING dependency-bus watchers. | **PROVEN** | `daemon/services/orphan_watcher_sweep.py:5-11` |
| 12 | Drift-reconcile f1 = ACTIVE + no Task → DEAD; E1-gated (`orphan_active_skipped_mission_live_f1`). | **PROVEN** | `job_recovery_service.py:2666-2674` |
| 13 | Drift-reconcile f2 = ACTIVE + COMPLETED Task → DONE; E1-gated (`orphan_active_skipped_mission_live`); the backstop requires a non-terminal tree member whose freshest activity is older than `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`. | **PROVEN** | `job_recovery_service.py:1008-1017`, `:2955-2999` |
| 14 | "Healthy shapes (active JobItem + pending/running Task) are EXPLICITLY EXCLUDED". | **PROVEN** | `job_recovery_service.py:1017-1019` |
| 15 | A stalled non-terminal descendant keeps the mission live ⇒ E1 holds ⇒ the sweep skips ⇒ the hold is self-holding. | **PROVEN** (mechanism) | `job_recovery_service.py:2981-2999` + `mission_live_guard.py:327` |
| 16 | Restart-mid-turn finalize recovery is boot-only (`recover_on_startup` at `api.py:1395`). | **PROVEN** | `daemon/api.py:1395` |
| 17 | The Oct-27 5h15m wedge has no on-disk primary evidence (no tester RESULTS for 10-27/10-28; zero hits for `wi-78967dba` / `5h15` / `five hours` in `.agents/**`). | **PROVEN** (the absence) | research-audit-census.md:141-148; re-verified |
| 18 | The wedge-capture errors from that window are **inconclusive**, not exculpatory. | **PROVEN** | `.agents/approver/wedge-capture-tracking.md:32-50` |
| 19 | The pool-poisoning mechanism was OPEN and relapsing on Oct-26 (~3 min re-formation under load). | **PROVEN** | `incident-20261026-db-pool-idle-in-transaction.md:77-87` |
| 20 | That the SPECIFIC Oct-27 5h15m wedge was triggered by a pool-caused finalize-write failure. | **INFERRED** | no on-disk artifact records the failing write / SQLSTATE / recovery path for that turn |
| 21 | That a healthy pool reduces but does not zero the trigger side (R2 retention can still starve the pool under burst; the Oct-26 soak rollback was checkpoint-pool saturation *without* aborted txns). | **INFERRED** | `analysis-20261026-v0278-soak-failure.md`; research-audit-census.md:196-198 |
| 22 | That the missed-finalize class is retired by this lane. | **REFUTED** — see §6.1 (V2) | §4 evidence |

---

## 8. Open questions for the dispatcher

1. **Scope of the own-commission.** Should the RUNNING-stalled finalize gap be (a) a new `g` leg inside
   `job_recovery_service.reconcile_drift_states` (keeps the drift-reconcile cadence and the E1 plumbing in one
   place, but must solve the E1 self-holding interaction), or (b) a dedicated sweep service (cleaner boundary,
   new cadence knob, new config surface)? This verdict recommends **(a)**, because the E1 self-holding problem
   must be solved in the same place, but the choice is the dispatcher's.
2. **The E1 interaction needs a ruling.** A repair that finalizes a stalled `JobItem` will, by definition, run
   while `evaluate_mission_live` says "live" (the stalled descendant is what makes it live). The commission needs
   an explicit ruling on whether a *bounded, evidence-gated* exception to E1 is acceptable for this shape, and
   what the evidence gate is (age bound? progress-stall detection? both?).
3. **Evidence recovery.** If the board item `wi-78967dba` text or the daemon journal for Oct-27 can be pulled
   (ari-only / operator-only), fact #20 would move from INFERRED to PROVEN and could change V3 from "class-level
   closure" to "incident-level closure". The dispatcher may want to commission that pull before the final report.
4. **Interaction with the runnable-zombie commission.** The v0.26.4 lane recorded sibling strands routed elsewhere
   (PP1 zero-watcher terminal-fire; operator-stop permanent park; watchdog false positives; heartbeat-on-turn-progress).
   The RUNNING-stalled finalize gap is a **fifth** sibling and should be dispatched as such, so the strands are
   closed as a family rather than one at a time.

---

## 9. References

- `research-audit-census.md` — §2a sweep coverage map, §2b finalize-write sites, §2c the Oct-27 evidence gap, §2d the verdict inputs
- `research-leak-sites.md` — the six-site verification and the RCA premise corrections
- `daemon/services/mission_strand_healer.py` — the RUNNING-zombie strand (read in full for this verdict)
- `daemon/services/job_feedback_observer.py:3559-3710` — the finalize cascade (single transaction)
- `daemon/services/job_recovery_service.py:925`, `:1008-1019`, `:2660-2680`, `:2955-2999` — the drift-reconcile legs and their E1 gates
- `daemon/services/watch_reconcile_sweep.py:100-118` — "does NOT touch the admission_state of any JobItem"
- `daemon/services/orphan_watcher_sweep.py:1-20` — PENDING bus watchers only
- `daemon/services/mission_live_guard.py:327-352`, `:500-530` — the three-leg guard + zombie backstop
- `daemon/api.py:1395` — boot-only `recover_on_startup`
- `/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` — the RCA (read-only; main checkout)
- `/home/nea/ensemble-src/.agents/approver/wedge-capture-tracking.md:32-50` — the "inconclusive, not exculpatory" ruling
- `.agents/shared/planning/soak-gate-mcp-warmup/requirements.md:1426`, `risk-safety-register.md:78`, `plan-overview.md:84-103` — the pool leak as a constraint; the recurrence clause
