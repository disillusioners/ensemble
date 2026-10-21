# Running-zombie strand — design decision

**Commission:** "running-zombie instances" (third strand-family member)
**Date:** 2026-10-21
**Branch:** `fix/running-zombie-sweep` (base `origin/latest` @ `33e1ef513`)
**Status:** IMPLEMENTED — no promote (the v0.26.x ceremony owns that)

---

## 1. The incident shape

Live incident 2026-10-19/20, instance `432988b8` (GREP ANCHOR — kept
deliberately: the shipped daemon source carries a descriptive placeholder
instead):

| field | value |
|---|---|
| `instances.status` | `running` (stuck 25h) |
| `instances.parent_id` | `NULL` (mission root) |
| descendants | the DIRECT children were ALL terminal — the real blocker was a **depth-3 `idle` member** |
| root's latest `task` row | `completed`, `completed_at` set |
| `job_queue_items.admission_state` | already `done` (f2 finalized) |

### 1.1 PREMISE CORRECTION (2026-10-21, cycle 2) — the stamp was REFUSED, the event was NOT lost

The cycle-1 text above ("the completion **EVENT** was lost") is **REFUTED**
by the live logs. What happened:

* The turn ran clean — its task row completed at **2026-10-21T04:09:31Z**.
* The **PRE-STAMP RECURSIVE LIVENESS HOLD**
  (`daemon/services/child_reports.py:2746-2798`) fired and **refused the
  COMPLETED stamp entirely**. Live log line (2026-10-21):

      root_completion: pre-stamp recursive liveness holds for 432988b8...
      (non-terminal member present (live leg holds) and freshest tree
      last_activity_at is 22s old (<21600s threshold) — mission is plausibly
      alive; defer to the next tick)

* The "22s" figure **IS the ROOT'S OWN `last_activity_at`** — refreshed at
  the message enqueue (`daemon/services/instance_messaging.py:2455`). The
  guard's freshness anchor included the root, so a stranded-but-messaged
  root looked **permanently fresh** and the 6h backstop was unreachable
  for it.
* The hold repeated: **19 times on 2026-10-19**, **455 hold lines** across
  ~40 roots. Each hold was a refusal, and each refusal was terminal: the
  stamp never landed, so the instance row stayed `running`.
* The f2-finalize story ("f2 healed the `JobItem`, the `INSTANCE` row is a
  different table") is a **subset manifestation**, not the root cause: f2
  finalized the `JobItem` because the JobItem's own row was `done`
  already; the instance row was held by the pre-stamp hold.

**Cycle-1's design doc §1 premise is therefore corrected in place.** The
defect family is: *a root stranded at `running` because a non-terminal
member holds mission liveness AND nothing re-evaluates* — with three
concrete defects: (1) the ANCHOR included the root's own enqueue-refreshed
stamp, (2) there was NO RE-ARM (the hold waited for a signal that never
comes on a stranded tree), (3) the net was defanged by the later
`654d6cc9d` change.

**The mirror JobItem never stranded in this shape** — the inline JobItem
close (`daemon/services/task_processor.py:1148-1211`, "Fix B") closed it
in the same pass.

### 1.3 PROVENANCE — what is snapshot-derived and what is SYNTHESIZED (F9, 2026-10-21)

The read-only specimen snapshot
(``specimen-<live-root>-snapshot.md``, captured 2026-10-21 06:42Z,
**never committed**) is the fixture's only evidence source — but it
does NOT contain the whole shape, and claiming otherwise would be a
provenance lie. The exact split:

| fixture part | source | note |
|---|---|---|
| the root row's identity + its ``last_activity_at`` stamp (04:09:09) + the single task row (created 04:09:09 → started 04:09:10 → completed 04:09:31) | **snapshot-derived** | verbatim |
| the 7 DIRECT children (all terminal) | **snapshot-derived** | verbatim (7 rows, all ``completed``) |
| the root's ``running`` status | **log-derived** | the specimen row was captured **AFTER** the operator's ``POST /stop`` — it reads ``paused``. The pre-stop ``running`` posture is reconstructed from the LOG evidence (the stamp-refusal lines, §1.1), NOT from the snapshot |
| the **DEPTH-3 ``idle`` member** (the real blocker) | **SYNTHESIZED** | the specimen contains **NO depth-3 rows at all**. The blocker came from the LOG evidence + the operator's account, not from the snapshot ⇒ the fixture must NOT claim snapshot fidelity for it |
| the chain ages (26h / 25h) | **synthesized** | the snapshot carries no depth-3 timestamps to copy |
| the never-dispatched ghost (the strand-exclusion interaction) | **synthesized** | a shape-pin fixture, not an observed row |

The fixture ids are **descriptive only** — no live-derived 8-hex id
mnemonics are reproduced in the test pack (F10).

### 1.2 EPOCH semantics — there is NO persisted epoch

* **Start** = the read-time projection of `instances.last_activity_at`
  (`daemon/tools/missions.py:317,326-341`; `daemon/services/mission_resolver.py:869-875`).
* **End** ⇔ `terminal_reason` is set ⇔ the instance is terminal or its
  linked `JobItem` is dead.
* "The epoch never ended" is **not** a separate failure mode: it is **the
  same defect seen twice** — the projection still says "running" because
  the stamp that would have ended it was refused.


## 2. Adjudicated decisions (leader, 2026-10-21)

### 2.1 Home

**`daemon/services/mission_strand_healer.py` — a NEW strand shape in the
existing healer.** NOT:

* `daemon/services/job_recovery_service.py` (f2) — the lane boundary is
  preserved **deliberately**: f2 heals the `JobItem` for free once the instance
  row is terminal, so touching f2 would double-drive the same root.
* `daemon/services/stale_pause_sweep.py` — that module is a **RELEASER**,
  pinned by test to never finalize, with a **fail-OPEN** probe posture. Both are
  wrong for a terminalizing write.

### 2.2 Transition — the PRODUCTION completion chain

The heal invokes
`ChildReportsService._process_child_completion_and_notify_parent(root, …)`
(`daemon/services/child_reports.py:2633`). NEVER a raw status UPDATE, NEVER
`terminate_instance` on the root.

The chain, in order:

1. **Pre-stamp recursive liveness** (`child_reports.py:2746-2798`, `skip_root=True`)
   — all-terminal tree ⇒ `live=False` ⇒ falls through to the stamp.
2. **DB stamp** (`child_reports.py:3586-3600`) — the atomic
   `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
   (TOCTOU guard) flips `running → completed`.
3. **Emission-time gate** (`child_reports.py:2463-2615`) — re-verifies bus
   pending, tree liveness, pending messages and freshness. On a block the stamp
   is **downgraded** to `waiting_children` (`child_reports.py:4948-5000`) and the
   publish is suppressed — a **legitimate miss**: log it, never force. The
   downgrade MOVES the root out of the `running` posture, so it **exits this
   lane's candidate set** (the selection is anchored on `status == running`) and
   the `waiting_children` strand owns the root from then on — this lane does
   **not** retry it on the next tick.
4. **Lifecycle-event publish → observer finalize** (`child_reports.py:5153-5159`
   → `job_feedback_observer.py:1017` → `:1307`) — the chain publishes the
   `instance_lifecycle` event and the observer consumes it to run
   `_finalize_job`. The INSTANCE terminal row is written by the chain's own DB
   stamp (step 2), **not** by the observer. On the `job_id=None` context
   (post-D13 MESSAGE path) the observer skips Step 1 (no `JobItem` UPDATE) and
   Step 3 (lock release) is a job-scoped no-op — the virtual job holds no lock
   of its own (`job_feedback_observer.py:786-800`, `:1300-1306`).

Mission liveness is derived READ-TIME from `Instance.status`
(`mission_resolver.py:980-1006`) — there is no persisted epoch row. "Epoch ends,
outcome recorded" == this status write + outcome + lifecycle event, which the
chain provides.

### 2.3 Predicate — all legs must hold, every failure errs toward MISS

| # | leg | code |
|---|---|---|
| 1 | `status == 'running'` AND `parent_id IS NULL` (mission root only) | `_heal_running_zombie_locked` |
| 2 | latest task row terminal-`COMPLETED` with `completed_at`; NO non-terminal task row (no PENDING/RUNNING/PAUSED/PROCESSING) | `_probe_task_rows` |
| 3 | age bound on `task.completed_at` (default 7200s) — INCLUSIVE: age ≥ bound qualifies | `_age_seconds` |
| 4 | **WIDENED (cycle 2)** — the canonical mission-live guard says `live=False` (all-terminal short-circuit OR 6h staleness expiry on a non-terminal member) + the enumeration is neither breadth- nor depth-truncated | `evaluate_mission_live` + `_tree_is_closed` + `get_tree_ids_permanent` |
| 5 | NO pending question anywhere in the tree | `_pending_question_in_tree` |
| 6 | dependency-bus pending 0 AND the root's own queue empty | `_count_pending_messages` + `bus.count_pending_for_target_sync` |

**TWO PREDICATE CORRECTIONS vs the original commission draft** (both
adjudicated, both deliberate):

1. **The age keys on `task.completed_at`, NOT `instances.last_activity_at`.**
   `last_activity_at` is FROZEN BY DESIGN for a running root (only lifecycle
   writers bump it) — it cannot distinguish a zombie from a long-legitimate turn.
2. **Roots with NO task row are NEVER swept.** No completion proof ⇒ the shape is
   indistinguishable from a root that never started. A deliberate miss.

### 2.4 Fail-closed specifics

* **Tree truncation ⇒ SKIP (loud WARN).** The repo helper
  (`daemon/repositories/instance/repository.py:604-666`) only WARNs when its
  depth cap is hit — a WARN-only signal the caller cannot act on. The healer
  therefore detects truncation in **Python**:
  * breadth — `len(tree_ids) > _RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE` (256),
    mirroring `stale_pause_sweep.py:186-193`;
  * depth — a **closure check**: every member's permanent child list must be
    inside the enumerated set (`_tree_is_closed`). A missing child ⇒ truncated.
* **Pending-question probe ⇒ FAIL-CLOSED.** The stale-pause checker FAILS OPEN
  (it only un-pauses; the operator is the safety net). This strand
  TERMINALIZES, so the probe is re-implemented with INVERTED error semantics
  (`RunningZombieProbeError` ⇒ SKIP). Probes: RAM `QuestionManager` packs, the
  durable `question_pack_payload` metadata shadow, and the durable
  `awaiting_answer` task handle (the authoritative shape of
  `TaskRepository.find_suspended_turn_for_answer`,
  `daemon/repositories/task/repository.py:770-830`). A pack owned by a DEAD
  member is stale, not outstanding.
* **Bus probe ⇒ FAIL-CLOSED** (an unwired bus singleton is an invalid state —
  the drive chain raises on it).
* **Unreadable descendant row ⇒ SKIP** (the tree view is not complete).
* **Guard error ⇒ SKIP (fail-closed, cycle 2).** The mission-live guard is
  fail-OPEN by contract (`live=False, error=True` — the caller proceeds with
  the finalize). This lane **TERMINALIZES**, so an unevaluatable tree leg
  must SKIP, never proceed: `error=True` ⇒ `skip_class=guard-error`, a
  raising guard ⇒ `skip_class=tree-probe-error`.

### 2.5 Selection widening must NOT weaken the existing shapes

The existing `waiting_children` / ghost strands keep their exact invariants
(`_heal_instance_locked` — untouched apart from the lock-helper extraction).
The new strand is a **separate predicate over a separate posture** (`running`,
not `waiting_children`), with its own candidate selection, its own skip classes
and its own drive path. The `sweep_once()` stats keep their EXACT meaning; the
new strand reports under its own `running_zombie_*` keys.

The two strands **share the per-root lock map**
(`_acquire_root_lock` / `_release_root_lock`) so two strands can never drive the
same root concurrently.

### 2.6 Config surface

`daemon/config.py` → `ServicesConfig` (env prefix `SERVICES_`):

| knob | env | default |
|---|---|---|
| `mission_strand_heal_running_zombie_enabled` | `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED` | `True` |
| `mission_strand_heal_running_zombie_age_bound_seconds` | `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_AGE_BOUND_SECONDS` | `7200` |
| `mission_strand_heal_running_zombie_batch_cap` | `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_BATCH_CAP` | `25` |

**Kill-switch default = TRUE — justification.** The sibling strands are
ALWAYS-ON by HARD POLICY, but this is the one heal lane that performs a
TERMINALIZING WRITE through the production completion chain, so it ships with
an explicit operator kill-switch the others do not have. The default TRUE is
justified by (a) the fail-closed predicate — every leg must hold and any
unprovable leg SKIPS; (b) the 2h age bound, which keeps the lane away from
live turns; (c) the dev-staged + promote-gated rollout — the kill-switch exists
for the OPERATOR, not as the default posture. OFF ⇒ a COMPLETE no-op (no
selection, no probe, no drive).

**Batch cap = 25 — justification.** Deliberately SMALLER than the stale-pause
sweep's 100: terminalizing writes justify a tighter per-tick bound so the blast
radius of a mis-predicate stays small. A root beyond the cap is picked up on the
next tick (300s cadence).

**The cap bounds EXAMINATION of the ANCHORED candidate set (F1).** Selection is
anchored on the completion proof — a running root whose LATEST task row is
terminal-`COMPLETED` (with a `completed_at` stamp) — ordered oldest
`task.completed_at` first. Roots that can never satisfy the proof (no task row
at all, a latest task row that is not `COMPLETED`, a live turn in flight) are
NEVER selected, so they cannot occupy the head of the batch.

*Why the anchor (the F1 fix).* The cap bounds the candidates EXAMINED per tick,
and a candidate that is examined and SKIPPED keeps its sort key — so it is
examined again on the next tick, forever. Under the previous raw-
`status='running'` selection the head of the batch was therefore occupied
PERMANENTLY by roots that can never satisfy the proof. With more such roots than
the cap, a real zombie past position `cap` was NEVER examined — a permanent
starvation, and the old "no candidate starves" claim was false.

*The TRUE fairness property.* An anchored candidate that is skipped keeps its
anchor and is re-examined on the next tick — it re-surfaces as the candidates
ahead of it drain, and every skip cause that CAN clear clears (a live descendant
that resolves, a pending question that is answered, a young turn that ages, a
busy queue that empties). Only the STRUCTURAL skip causes — a permanent tree
over the probe bound and a permanently live descendant — hold a slot
indefinitely; each consumes one slot, so a backlog of such roots larger than
the cap is the only case where the cap, not the strand, bounds progress.

*Implementation note.* The age bound is applied in PYTHON, not in SQL: the
completion stamp is a naive-UTC `timestamp without time zone` column and the
SQLite dialect stores it as TEXT, so a SQL comparison against a bound datetime
is a STRING comparison there and is not chronologically sound. The filter uses
the same `_age_seconds` helper the predicate uses, so the two cannot disagree.

**W-B floor (review cycle 1).** Both knobs are bounded at the CONSTRUCTOR: a
value below 1 raises `ValueError` at construction — the same family style as
the class's `interval_seconds` check. The reviewer's MEASURED cases:
`batch_cap=-1` made the batch UNLIMITED (a negative LIMIT renders as "no
limit" — 5/5 healed at cap=-1 vs 3/3 at cap=3) and `age_bound_seconds=0` made
every turn instantly zombie-shaped. A silent clamp would HIDE that operator
error on the blast-radius bound of a TERMINALIZING lane, so the lane fails
LOUDLY instead. Production cannot reach these values — both config fields carry
`ge=1` (`daemon/config.py`).

### 2.7 Cost per sweep tick

**≈19.4k (derived) to ~45k (measured) queries per sweep tick.** The earlier
"≤6.4k queries/tick" figure is WITHDRAWN — it was a counting-method artifact.
Counting method: the DERIVED figure is a static count of the strand's own SQL
(probe + tree enumeration + closure check + the probes) multiplied by the batch
cap, while the MEASURED figure observes the DB layer during a live sweep — the
drive's production completion chain (content fetch, pre-stamp liveness eval,
stamp, emission gate, publish, observer finalize) is a multi-query path that the
static per-candidate count understates. Neither figure was re-measured in
review cycle 1 (re-measuring was optional).

**The shared guard's companion leg adds a PER-MEMBER cost — on the
staleness branch only.** The member-alive companion leg
(`daemon/services/mission_live_guard.py`, §2.10) runs per candidate member
and costs **≈2 queries each**: the repository's `has_live_work` probe + one
task read (the newest attempt stamp). It runs **ONLY on the staleness
branch** (the tree already looks quiet), so a **live tree pays nothing**;
the RZ sweep's per-tick cost therefore grows only with the number of
NON-ROOT, NON-TERMINAL members on trees that have been quiet for the 6h
horizon. (A-5 qualification: the `has_live_work` probe's **VALUE** is
log-only — it never changes the verdict; but a probe **EXCEPTION** DOES
force the hold, `live=True` + `error=True` (§2.10.3). The probe/evidence
DISAGREEMENT is surfaced at INFO, review C2 — now AGGREGATED to one line per
evaluation, A-3.)

## 2.8 Cycle-2 changes (2026-10-21) — the three defects, closed

Cycle 1 shipped the strand SHAPE. Cycle 2 closed the three defects the
forensics proved (§1.1). All three land on the branch; nothing ships
without the promote ceremony.

### 2.8.1 The ANCHOR fix — `daemon/services/mission_live_guard.py`

The zombie backstop's freshness anchor was the max over the WHOLE tree —
**the root's own `last_activity_at` INCLUDED**. The root's stamp is
refreshed on every message enqueue
(`daemon/services/instance_messaging.py:2455`), so a stranded-but-messaged
root looked permanently fresh and the 6h backstop was unreachable for it.

**Now:** the anchor is computed over the **NON-ROOT members only**
(`daemon/services/mission_live_guard.py:485-510`). A tree with NO members
(a **childless root**) falls back to the root's own stamp — there the root
IS the whole tree, so its activity is the tree activity and the historical
behaviour is preserved exactly.

**No new config knob** — single source of truth. The pre-stamp gate, the
healer and f2 all inherit the corrected anchor.

**f2 is NEUTRAL in CODE — but NOT in BEHAVIOUR (pinned, PIN-FIRST; the
pin stays green).** `job_recovery_service.py` is untouched, and its guard
CALL shape (no `skip_root` — for f2 the root's own status IS a live leg)
and its OUTCOME **on the pinned shape** (hold on a live member, finalize on
a quiet tree) are identical before and after the move. **The BEHAVIOUR
WIDENS, and that is a MATERIAL DISCLOSURE, not a hidden change:**

1. the anchor fix makes f2's 6h backstop **reachable for a messaged
   root** — before it, the root's own enqueue-refreshed stamp kept the
   backstop permanently out of reach (the live 2026-10-21 repro's "22s
   old" root);
2. the cycle-4 companion leg (F1, §2.10) can now **HOLD f2** on a
   member that owns a live (YOUNG) task row — f2 no longer
   force-finalizes that `JobItem`.

Both widen f2's HOLD direction (a MISS, never a wrong terminal), and both
are disclosed at promote time. The pin lives in
`tests/job_queue/test_pattern_f_mission_live_gate.py`
(`TestF1LaneGuardAnchorNeutralityPin` — renamed in the cycle-2 post-review
touch-up: the class exercises the **f1/E1 lane**
(`orphan_active_skipped_mission_live_f1`, `job_recovery_service.py:2674`);
f2's own lane (`orphan_active_skipped_mission_live`, `:2975`) is
behaviorally unasserted there and is covered by a STATIC source-text pin
instead). The shape that deliberately CHANGES (fresh root + stale member:
hold → finalize) is the defect the fix closes and is deliberately NOT pinned.

**Side effect the fix introduces — `anchor_missing` is now MEMBER-SCOPED**
(cycle-2 review, C4). The missing-anchor disarm
(`daemon/services/mission_live_guard.py:458`, `:602-613`) starts at
`anchor_missing=True` and is flipped to `False` by any parsed
`last_activity_at` seen during the walk. **Before** the fix the walk covered
the whole tree INCLUDING the root, so the root's own stamp flipped
`anchor_missing=False` for the whole tree. **Now** the root is `continue`d
before that line (`:505-507`), so only NON-ROOT members can flip it; the
root's stamp is used solely as the fallback for a tree with NO members
(`:512-516`). Consequence: a tree **WITH members whose members all carry a
NULL `last_activity_at` (while the root has one)** now reads as a data gap ⇒
`anchor_missing` stays `True` ⇒ the guard returns **`live=True`** (backstop
disarmed — "data gap, not zombie evidence; the live leg holds by default").
The direction is **CONSERVATIVE**: the guard HOLDS the mission live rather
than terminalizing it, so a stale-activity data gap can never cause a wrong
finalize — it can only delay one.

**MUST NOT regress (all pinned):** childless roots, the all-terminal
short-circuit (`daemon/services/mission_live_guard.py:572-591`), f2's 6h
backstop path.

#### 2.8.1.1 THE CONSUMER TABLE — who reaches the 6h backstop, and what the companion leg now does for them

**Read the table with the RULING-2 comment-site semantics folded in**
(the two comment-only sites — `daemon/services/job_queue_service.py`'s
`_mission_live_guard_holds` docstring and `daemon/services/job_recovery_service.py`'s
Gate-4 comment): the backstop's freshness anchor is the freshest
**NON-ROOT** member's `last_activity_at`; the root's own stamp counts
ONLY as the fallback for a CHILDLESS tree. So "the root looks fresh"
never keeps a stale member alive.

**PREAMBLE — what the table can and cannot say (W-1, corrected in
cycle 4).** **8 of the 10 consumers can HOLD** the mission. Two
exceptions, and they are DIFFERENT in kind:
* the **zombie-GC held-watch reconcile** (`job_queue_service.py:874`,
  `instance_id=None`) **NEVER RUNS the leg** — the guard raises before
  the walk (there is no tree to walk);
* the **orphan release** (`job_queue_service.py:1005`) **gates only a
  TOKEN** — its guard call is DIAGNOSTIC-ONLY (it feeds the
  `ORPHAN_RELEASED` token through `if _pre_verdict.timed_out:`), and
  the `notify_watchers(...)` release that follows is UNCONDITIONAL.
  A hold there suppresses the TOKEN, not the release.

The **fresh-root + stale-member** case is the incident shape: the root's
own stamp was refreshed by a message enqueue (22s old) while the member
had been quiet for 25h. **Every consumer below reaches its 6h backstop in
that case** (the anchor is the member's, and it is stale) — the
difference between them is what they DO with the verdict.

| # | consumer | call site | `skip_root` | reaches the 6h backstop (fresh root + stale member)? | what the companion leg (§2.10) now does for it |
|---|---|---|---|---|---|
| 1 | **pre-stamp gate** | `child_reports.py:2752` | **yes** | yes | a member with a live (YOUNG) task row ⇒ the COMPLETED stamp is **REFUSED** (no wrong completion) and the re-arm hands the root to the healer |
| 2 | **emission gate** | `child_reports.py:2590` | no | yes | ⇒ the gate's guard call returns `live=True` and the gate returns **False** ⇒ the publish is **BLOCKED** (a legitimate miss, not a wrong completion). The traced step is the gate's `False` return (`child_reports.py:2590-2600`) — NOT a stamp downgrade (that downstream step is not in this diff, so it is not claimed here) |
| 3 | **healer tree leg 4** | `mission_strand_healer.py:1163` | **yes** | yes | ⇒ the lane **SKIPS** with its own skip class `member-live-task` (the operator can tell this hold apart from an ordinary `live-tree` hold) |
| 4 | **f1** (drift reconciler) | `job_recovery_service.py:2666` | no | yes | ⇒ f1 **HOLDS** the `JobItem` (no force-finalize) — detail pattern `orphan_active_skipped_mission_live_f1` |
| 5 | **f2** (drift reconciler) | `job_recovery_service.py:2970` | no | yes | ⇒ f2 **HOLDS** the `JobItem` — detail pattern `orphan_active_skipped_mission_live`. **CODE untouched; BEHAVIOUR widened (material disclosure, above)** |
| 6 | **boot sweep** (`reconcile_terminal_watches`) | `job_queue_service.py:635` | no | yes | ⇒ the sweep does **NOT** fire the terminal for that watch (the watch survives to the next tick) |
| 7 | **zombie-GC held-watch reconcile** | `job_queue_service.py:874` | n/a — `instance_id=None` | **no** (the guard returns `live=False, error=True` on the unwired id — the fail-OPEN contract; the caller finalizes) | unchanged — the leg never runs (there is no tree to walk) |
| 8 | **orphan release** | `job_queue_service.py:1005` | no | yes | ⇒ the guard call is **DIAGNOSTIC-ONLY**: it gates the `ORPHAN_RELEASED` **TOKEN** (through `if _pre_verdict.timed_out:`). A hold **suppresses the token** — the `notify_watchers(...)` release that follows is **UNCONDITIONAL** and still fires (W-1 correction) |
| 9 | **stored-watch reconcile** | `job_queue_service.py:1231` | no | yes | ⇒ the hold falls through to the **loss-case leg**, which **REHYDRATES** a watcher on the ORIGINAL `work_id` — the stored watch is **NOT released**, and a watcher row is re-armed (W-1 correction) |
| 10 | **work notifier** | `work_notifier.py:687` | no | yes | ⇒ the watcher is **not notified** (the mission-liveness hold keeps the work row) |

**THE `skip_root=True` PAIR** (rows 1 and 3): in that mode the ROOT's own
non-terminal status is not a live leg (the stamp is about to flip it) —
so the companion leg, which is member-scoped anyway, is the ONLY thing
that can hold those two lanes' trees. Before cycle 4 they carried an
opt-in copy of the leg; now they carry the SAME guard-side leg as
everybody else — one implementation, no second copy.

**The cost of the widening** (§2.7): the companion leg's probes run ONLY
on the staleness branch — the rare path — and cost PER MEMBER (the
repository's `has_live_work` + one task read per candidate member).

### 2.8.2 The TREE-LEG widening — `daemon/services/mission_strand_healer.py`

The strict all-terminal tree leg was unreachable for the live shape: the
real blocker was a **depth-3 `idle` member** (the direct children were ALL
completed). The tree leg is now the **canonical mission-live guard** — the
SAME call the pre-stamp completion gate makes (`skip_root=True`):

* `live=False` covers BOTH the all-terminal short-circuit AND the 6h
  **staleness expiry** on a non-terminal member (reachable only because the
  anchor is now member-scoped);
* `live=True` ⇒ SKIP (`skip_class=live-tree`);
* the guard is fail-OPEN (`live=False, error=True`) but this lane
  **TERMINALIZES**, so `error=True` SKIPS (`skip_class=guard-error`).

Net effect: a **stale-member** zombie heals at ~6h member staleness (the
guard's own `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`) **without terminating
anyone**; an all-terminal zombie still heals at the 2h task-age bound. The
independent safety legs are unchanged: the root's latest task is
terminal-COMPLETED, the age bound keys on `task.completed_at` (≥2h), no
pending question in the tree, no live root task, the bus / own-queue gates,
and the truncation fail-closed probe.

**The safety argument, REWRITTEN (W2, cycle-4 review).** The earlier
text here claimed "a member mid-turn IS recently active" — **the live
forensics REFUTE that**: `instances.last_activity_at` is written on the
enqueue / child-report / observer paths and **NOT on turn progress**, so a
member inside a long turn reads STALE to the freshness anchor (the
frozen-activity pathology, the same finding the watchdog review landed).
**That freeze is precisely WHY task-row awareness is REQUIRED**: the
activity column cannot distinguish "working right now" from "crashed
three hours ago", and the guard's own anchor therefore mis-reads a
mid-turn member as a zombie candidate.

The cycle-3 answer (a member-task leg) was RIGHT in direction and WRONG in
shape (opt-in, unbounded). The cycle-4 shape (F1 + RULING-1b, §2.10) is
the reconciled one:

* the leg is **unconditional and guard-side** — every consumer inherits
  it, because the guard is the shared chokepoint and a leg carried by
  only two lanes leaves the others exposed to the same false completion;
* the leg is **time-bounded** — only a task row YOUNGER than the guard's
  own 6h horizon holds the tree, so a crashed/killed mid-turn row (the
  "lingering task row" that would otherwise strand a zombie forever)
  does NOT hold it;
* the leg is **ADDITIVE** — a stale member with NO live task row stays a
  zombie candidate (the observed shape still heals).

What the leg does NOT do (named, not hidden): a member inside a
legitimate >6h turn is still indistinguishable from stale (the horizon is
the system's own) — "heartbeat on turn progress" is a named follow-up
(§9).

### 2.8.3 The RE-ARM — `daemon/services/child_reports.py`

The pre-stamp hold waited for a "next message-completed signal" that
**provably never comes** on a stranded tree (455 hold lines across ~40
roots in the live repro). The hold now hands the root to the strand healer
through the **existing** `_schedule_strand_heal` scheduler
(`daemon/services/child_reports.py:2313`):

* **LANE DISPATCH by posture** — `running` ⇒ the running-zombie lane;
  `waiting_children` ⇒ the ghost lane; any other posture ⇒ no-op. The
  caller passes the posture it already knows; the task never blocks the
  event loop on a DB read.
* **COALESCED per root** — a second hold while a hand-off is in flight is
  dropped (logged at DEBUG), never queued. A hold storm cannot become a
  heal storm.
* **THE RECURSION BOUND** — the marker is held for the whole lifetime of
  the scheduled task, and the heal drive re-enters this same completion
  path: a hold fired from INSIDE a running heal can never schedule a
  second, concurrent heal for the same root.

**THE EVENT ARM BYPASSES THE BATCH CAP (F7, cycle-4 review — DISCLOSED,
not bounded by code).** The coalescing above is **per-root**, so N
DISTINCT stranded roots ⇒ N concurrent hand-offs — the event-driven arm is
NOT bounded by the healer's per-tick ``batch_cap`` (25). The amplification
is bounded in practice (every hand-off re-checks every leg and writes only
a provably dead root; a duplicate for the SAME root is dropped), but there
is **no global ceiling on in-flight event-arm hand-offs** in this train.
**The choice: DISCLOSE, not bound** — a ceiling is a new operator-visible
knob on a lane that is otherwise fail-closed, and the residual is a
fan-out of READ-ONLY probes over the stranded roots (the same roots the
300s sweep examines anyway). A per-process in-flight ceiling is a named
follow-up (§9).

**TEARDOWN CAVEAT (F8, cycle-4 review).** The in-flight marker is
released **only** by the scheduled task's done-callback. If the event loop
tears down before that callback fires, that root's event arm stays
**wedged** — no further hand-off is scheduled for it from the hold site.
The 300s sweep remains the net (the sweep does not use the marker).

### 2.8.4 The wc-strand `skip_repair=True` — `daemon/services/mission_strand_healer.py`

The waiting_children / ghost lane now opts out of the LLM report-repair
like the running-zombie lane — the **same stale-transcript hazard class**
(the heal lane's transcript can be 25h stale; the repair exists to fix a
TRUNCATED report for a human reader, while the lane only needs the content
as the completion payload).

> **⚠ FLAGGED FOR THE REVIEWER (leader ruling, cycle 2):** the
> **trade-off** is that a genuinely truncated report is carried through
> **un-repaired** into the completion payload. The same trade-off was
> already accepted for the running-zombie lane in cycle 1; this widens it
> to the wc lane.

**RULING, CYCLE 4 (Point 5 — KEEP the widening, with hardening).** The
widening STAYS (leader decision): the two heal lanes must not diverge on
the same hazard class (a stale transcript + the LLM repair), the harm is
bounded to rare stale-strand heals, and the truncation-repair loss is
disclosed and user-vetoable at the promote ceremony. The council's
hardening is adopted:

* **DISCLOSURE (this paragraph)** — the widening is a **deliberate
  behaviour change**, not a bug fix: with the repair skipped, a genuinely
  truncated report reaches the completion payload un-repaired.
* **PAYLOAD-CONTENT PIN** — the existing pin covered only the
  FLAG-FORWARD (`skip_repair=True` reaches the drive); it could not see
  what the drive then PUBLISHED, nor whether the 30s repair call was
  spent. The new pin
  (`tests/job_queue/test_running_zombie_sweep.py::TestRunningZombieHealPayloadIsRawAssistantMessage`)
  asserts the **published completion payload is the RAW assistant
  message** (with the repair ENABLED and the transcript shaped so the
  repair WOULD fire) and that the repair LLM is **never called** — plus a
  CONTROL that drives the same shape without the flag and shows the
  REPAIRED payload, so the pin is sensitive rather than vacuous.
* **OPERATOR RECORD** — the skip class log lines and this section are the
  operator-facing record: a heal that publishes a visibly truncated
  report is this lane's known trade-off (§8(a), §8b).

### 2.9 Cycle-3 changes (2026-10-21) — the MEMBER-TASK liveness leg

> **⚠ SUPERSEDED IN PART by §2.10 (cycle 4).** The RULING (§2.9.2 — a
> member with a live task row is ALIVE) STANDS. The ROUTE (§2.9.3 — an
> OPT-IN `task_engine` parameter) does **NOT**: the council rejected the
> opt-in shape (F1) and the leg is now unconditional and guard-side, and
> it is **time-bound** (RULING-1b). Read §2.9 for the ruling and the
> evidence trail; read **§2.10 for the shipped shape**.

#### 2.9.1 The hazard (leader-confirmed, cycle-2 review finding)

`instances.last_activity_at` is written on the enqueue / child-report /
observer paths — **NOT on turn progress**. So with the cycle-2 anchor
fix (the anchor is computed over the NON-ROOT members), a member
inside a >6h turn reads **STALE** to the backstop, and the backstop
would break the tree — finalizing the root while that member is still
working. That is a **FALSE COMPLETION**.

#### 2.9.2 The ruling — fail-MISS over false-completion

**A non-terminal member holding a LIVE (non-terminal) task row counts
as ALIVE, not stale: the root is NOT finalized.** The lane's core
invariant is "prefer a MISS over a wrong transition".

**RECORDED TRADE-OFF (leader, cycle 3):** the hung-task-member
sub-family (a member that owns a task row that never completes)
therefore stays **UNHEALED — DELIBERATE**: that member's own strand is
the root cause (the recursive case deferred by design); a persisting
miss is recoverable via the admin path (§8), a false completion is
not. The **OBSERVED** zombie family (member idle, NO task row) is
unaffected — still stale-eligible.

#### 2.9.3 The route — ROUTE 1 (the guard carries the leg)

Route 1 (the leader's preference): teach the **GUARD** that a member
with a live task row is not stale, so BOTH consumers inherit it — the
healer's tree leg (leg 4) AND the pre-stamp recursive-liveness gate.

* **Signature impact on the existing call sites: NONE.** The leg is
  OPT-IN through a new keyword-only parameter
  (`evaluate_mission_live(..., task_engine=None)`); the default
  `None` ⇒ the leg is **INERT**. f2 (`job_recovery_service.py`), f1,
  the boot sweeps and the emission gate call the guard WITHOUT the
  parameter — their behaviour is unchanged (the f2-neutrality pin
  stays green).
* **Cost: ONE batched query per TREE, not per member** — the member
  ids come from the walk the guard already does (no extra tree
  enumeration, no N+1). The query runs ONLY on the staleness branch
  (the rare path), so a live tree pays nothing. This is the shape
  §2.7's budget asks for (one extra query per candidate on the stale
  path).
* **Blast radius: TWO consumers** — the running-zombie healer's tree
  leg and the pre-stamp gate. Both are TERMINALIZE-capable paths on
  the mission-root completion shape.
* **Failure posture: fail-MISS.** A probe that cannot be evaluated
  **HOLDS** the tree (`live=True`, `error=True`) — the leg cannot
  prove the tree is stale, and these lanes terminalize. A mis-shaped
  or unwired task handle leaves the leg INERT (the historical
  behaviour) rather than guessing.

The live-task set is `daemon.constants.NON_TERMINAL_TASK_STATUSES`
(`pending`, `running`, `paused`, `processing`) — the canonical home,
now shared with the strand's root-level leg (one set; no silent
divergence — the codebase's own warning on the duplicated status sets
applies). It is a **SUPERSET** of the ruling's enumeration
(`pending`/`running`/`processing`): `paused` is included because a
suspended turn awaiting an answer is WORKING, not stuck — strictly
more conservative (it can only turn a wrong completion into a miss).

**The skip class.** The verdict carries a new field
`MissionLiveVerdict.member_task_hold`; the healer maps it to its own
skip class **`member-live-task`**, so the operator can tell this hold
apart from an ordinary "the tree is alive" hold (`live-tree`).

#### 2.9.4 The residual — NAMED, not hidden

* The **hung-task-member sub-family stays unhealed** (§2.9.2) — the
  admin path (§8) recovers it.
* The **emission-time gate** (the post-stamp defense-in-depth check
  in `child_reports.py`) does **not** carry the leg. The ROOT lane is
  protected UPSTREAM by the pre-stamp gate (it runs BEFORE the stamp
  and returns early on a hold), and the emission gate's fail-OPEN
  posture (the at-least-once contract) is deliberately not changed by
  this commission. A member that acquires a live task row BETWEEN the
  two evaluations falls to the emission gate's existing downgrade
  path — the pre-existing stamp→downgrade churn class, not a new
  wrong completion.
* The **root's own** live task row is not part of the member-task leg
  (the root is not a "member"). The healer's own root-level leg
  (`non-terminal-task`) covers the healer lane; the pre-stamp gate's
  root-task posture is pre-existing and unchanged.
* The **watchdog**'s signal quality is a separate follow-up (§9.5).

## 2.10 Cycle-4 changes (2026-10-21) — the council rework (F1 + RULING-1b)

The independent cycle-2 review (governor council, 2 models, run-proven)
landed AFTER cycle 3 had finalized. Its **F1 finding is CRITICAL** and
this section is the canonical record of the rework.

### 2.10.1 F1 — the leg becomes UNCONDITIONAL and GUARD-SIDE

Cycle 3 shipped the member-task leg **OPT-IN**: a `task_engine` parameter
on `evaluate_mission_live`, supplied by exactly TWO consumers (the
running-zombie healer's tree leg and the pre-stamp gate). The council
**rejected that shape**:

* the guard is the **shared chokepoint** — a leg carried by only the two
  terminalize-capable lanes leaves the other EIGHT consumers (f1, f2, the
  boot sweeps, the emission gate, the orphan release, the work notifier)
  exposed to the **same false completion** the leg exists to prevent (a
  root finalized while a member is mid-turn);
* the healer-side fallback was ruled **explicitly insufficient** — the
  leg has to live where every consumer already reads.

The shipped shape:

* the cycle-3 `task_engine` parameter, its threading and the batched
  `_members_with_live_tasks` probe are **GONE**; the guard's public
  signature is back to its pre-cycle-3 shape (`instance_repository`,
  `instance_id`, `bus_pending_count`, `timeout_seconds`, `skip_root`);
* the leg asks the instance repository the guard **already holds** —
  `SQLModelInstanceRepository.has_live_work(instance_id)`
  (`daemon/repositories/instance/repository.py:2112`) — **ZERO new
  plumbing** (no new parameter, no `Engine` import in the guard, no new
  repository method);
* the member's task rows are read through that same repository
  (`instance_repository.engine`) — the one local extension, and the
  reason the RULING-1b time bound can exist at all.

**The opt-in pin is OBSOLETE and was DELETED** (the leg is never inert
now); it is replaced by a pin that asserts the opposite — the guard's
signature carries **no** opt-in parameter and a consumer that passes
nothing extra still gets the hold.

### 2.10.2 RULING-1b — the leg is TIME-BOUNDED

An unbounded "any live task row ⇒ alive" leg trades one permanent strand
for another: a member whose task row never completes (a crash / kill
mid-turn leaves exactly that row behind) would hold its mission open
**forever** — the exact class this lane closes.

**The rule: a member's live work counts as ALIVE only if its task row is
YOUNGER than the guard's own staleness horizon.** The horizon is the
SAME constant (`MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`, 21600s) — one
source of truth, no new knob. The observable contract, pinned on BOTH
sides:

| member's live task row | verdict | root |
|---|---|---|
| **younger** than the horizon | the member is ALIVE → the leg HOLDS the tree (`live=True`, `member_task_hold=True`, `timed_out=False`) | **NOT** finalized |
| **older** than the horizon (crash / kill mid-turn) | the member is NOT alive → stale-eligible → the backstop fires (`live=False`, `timed_out=True`) | **CAN** heal |
| **present** but its stamp **undatable** | fail-MISS: the row EXISTS ⇒ the member is ALIVE and the leg HOLDS the tree — but the hold is **BOUNDED** by the row's own `created_at` (W-3b), so it can never hold forever. **REACHABILITY (W-3):** this state is NOT reachable through today's ORM read — `task.created_at` is NOT NULL and an unparseable stamp RAISES inside the read (so the probe-error path holds instead); the row is a HARDENING for a state a future schema/read change could expose | **NOT** finalized |
| **no** live task row | the member is stale-eligible (the ADDITIVE invariant) | **CAN** heal |

**Where the age is read:** the leg reads the member's non-terminal task
rows (the canonical set, `daemon.constants.NON_TERMINAL_TASK_STATUSES`)
and takes the **newest attempt stamp** — the row's `started_at` (when the
worker claimed the turn), falling back to `created_at` (minted but not yet
claimed). Naive-UTC digits per the repo's naive-UTC binding convention; an
unparseable stamp is DROPPED from the AGE comparison (never guessed) — but
the ROW's existence still HOLDS the tree (fail-MISS, review C1).

### 2.10.3 The two PINNED invariants (council)

* **ADDITIVE, never a replacement** — a member with stale activity AND
  no live task row **remains a zombie candidate** (the OBSERVED family:
  an idle member with no task row). The leg only ever ADDS a hold; it
  never removes the staleness expiry.
* **FAIL-MISS** — a member with a live (YOUNG) task row ⇒ the root is
  **NOT** finalized. A probe that cannot be evaluated also HOLDS
  (`live=True`, `error=True`): the leg cannot prove the tree is stale,
  and the lanes that terminalize a root must not break a tree they
  cannot see.

### 2.10.4 The cost (§2.7's shape) — PER-MEMBER, on the rare path

The leg runs **ONLY on the staleness branch** (the tree already looks
quiet), so a live tree pays nothing. On that branch the cost is
**per candidate member**: the repository's `has_live_work` + one task
read (≈2 queries per non-root, non-terminal member). The cycle-3 shape
was ONE batched query per tree; the cycle-4 shape is per-member, and the
difference is stated here rather than glossed: the per-member read is
what makes the **time bound** possible (the age is per member), and the
branch is the rare one (a tree that has been quiet for 6h).

### 2.10.5 What the leg does NOT do (named residuals)

* **A live task row with NO parseable stamp HOLDS the tree (fail-MISS).**
  The age of an undatable row cannot be read, so the row is treated as
  **YOUNG**: the member counts as ALIVE and the root is NOT finalized.
  The posture in one line — **rows exist ⇒ alive, unless the row is
  dateable AND older than the horizon; no dateable stamp ⇒ alive.**
  (Review C1: before this, an undatable row was indistinguishable from
  "no row" — the probe returned `None` for both — so the backstop could
  fire under a possibly-live member. That was fail-STALE against this
  lane's declared fail-MISS posture, and it was not stated in the doc.)
* **A member inside a LEGITIMATE >6h turn is still indistinguishable
  from stale** — the horizon is the system's own. The fix is a
  **heartbeat on turn progress** (the task rows already carry
  `last_heartbeat_at`), which would also close the watchdog's
  false-positive class (§9). **NO code for it this train.**
* **A member whose only live work is a JOBITEM row (no task row) is NOT
  held.** The task row is the only DATEABLE evidence class, and the
  repository's probe also counts a member's own non-terminal **CHILD** as
  "live work" — a `waiting_children` parent would otherwise hold the
  tree forever through its own stuck child. The tree's ordinary legs
  (status + the member-scoped anchor) judge such a member.
* **The ROOT's own live task row is not part of the leg** (the root is
  not a "member"): the healer's own root-level leg covers the healer
  lane, and the pre-stamp gate's root-task posture is pre-existing.
* **The emission-time gate's TREE-WALK fail-OPEN posture is unchanged** — the
  leg only ever HOLDS the gate (a miss), never forces a terminal through it.
  **W-2 (the OPPOSITE polarity, stated honestly):** a MEMBER-ALIVE probe
  error is NOT a tree-walk error — it yields `live=True` + `error=True`, so
  the emission gate **HOLDS the publish**. That hold is **not a permanent
  strand**: the observer / the next pass re-drives the completion, so the
  at-least-once guarantee still holds (a held publish is retried; a lost one
  is not).

### 2.10.6 The status-set rule (F11)

The leg's task read uses the **canonical** set
`daemon.constants.NON_TERMINAL_TASK_STATUSES` — one home, no divergent
local tuple. `processing` is **LEGACY task vocabulary**: it is NOT a
`TaskStatus` member (the enum carries PENDING / RUNNING / PAUSED /
COMPLETED / FAILED / CANCELLED), and the constant keeps it DELIBERATELY
for legacy rows, so a legacy in-flight turn still counts as live work.

**The repository's own live-task list is a DIFFERENT question.** The
instance repository's `has_live_work` probe carries only
`pending`/`running`/`paused` — it answers "does this instance have live
work **for the zombie scan**", not "is a turn in flight on it". The leg
therefore DECIDES on the canonical set (this §2.10.6 one), so the legacy
`processing` shape stays protected — the repository's probe answer never
vetoes a member whose only live signal is a legacy `processing` row.
That is why the coarse probe is not a hard gate: it is the repository's
canonical "live work" answer, while the HOLD is decided on the DATEABLE
task evidence (the `has_live_work` child arm would otherwise let a
`waiting_children` parent hold the tree forever through its own stuck
child — §2.10.5).

## 3. Known residuals (documented, not closed)
### 3.0 Idempotency of the drive against an already-finalized JobItem

The 25h incident shape has the `JobItem` ALREADY `done` (f2 healed it). The
drive chain handles that without harm:

* the child_reports DB stamp is an atomic
  `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
  (`child_reports.py:3586-3600`) — a second run against an already-terminal row
  matches 0 rows and returns `idempotency_skip` (no duplicate publish);
* the drive **DOES** reach the observer's `_finalize_job` — via the
  lifecycle-event publish (`child_reports.py:5153-5159` → the observer's
  `_process_event` at `job_feedback_observer.py:1017` → `_finalize_job` at
  `:1307`). The already-`done` `JobItem` is handled by the **stale-job defense**
  (`job_feedback_observer.py:799-812`): the freshest row is a non-PROCESSING
  terminal status, so the re-query finds no active row and the context resolves
  to `job_id=None` — Step 1 (the `JobItem` UPDATE) is **skipped**, so there is
  **no `InvalidTransitionError` and no raise**; Step 3 (lock release) is a
  job-scoped no-op on that path. The `InvalidTransitionError` → DEBUG absorb
  (`job_feedback_observer.py:3728-3733`) is real, but it belongs to the
  bus-callback / `PROCESS_REPORT` path (a CHILD's `JobItem`), not to the
  parentless-root drive this strand exercises;
* the strand is re-entrant: a root flipped back to `running` by a watcher
  (`instance_messaging.py:2375-2379`) simply becomes a candidate again — the
  post-drive status read is the verdict, and the drive is safe to repeat.

### 3.1 Watcher flip race

f2's `notify_watchers → enqueue_message` can flip an idle/waiting watcher — or
a just-healed root, if a `job_watchers` row points at it — back to `running`
(`daemon/services/instance_messaging.py:2375-2379`). Mitigations: the healer's
per-root refcounted locks + the drive chain's own TOCTOU re-checks (the atomic
status UPDATE, the in-transaction bus COUNT).

### 3.2 The `awaiting_answer` handle leg is DEFENSE-IN-DEPTH

With the predicate's leg order, an `awaiting_answer` handle always coexists
with a PAUSED (⇒ non-terminal) task row, so the earlier `non-terminal-task`
leg fires first. The probe is still exact and still exercised directly by a
test.

### 3.3 No schema migration

Nothing in this change needs one. (Had one been needed, the commission says
STOP and report — none was.)

### 3.4 The drive could trigger the LLM report-repair (APPLIED — closed in this follow-up)

The drive's content fetch (`child_reports.py:2654`) reaches
`_get_last_assistant_message`; WITHOUT `skip_repair` the 30s LLM report-repair
can fire on a 25h-stale transcript before the gates run.

**APPLIED** — commit `fix(child-reports): thread skip_repair through the
completion drive; heal lane opts out` on `fix/running-zombie-sweep`,
sha **`5edba8f7cdf2b5e10b5c11058b18e38323e99881`** (the literal sha, so
the reader never has to run `git log --grep`). The change:

* `daemon/services/child_reports.py:2633` — the drive takes an ADDITIVE
  keyword-only `skip_repair: bool = False`. Default False ⇒ every existing
  caller keeps its current behaviour. All 6 production call sites
  (`manager.py:6757`, `:9745`, `:9891`, `:13101`; `mission_strand_healer.py:760`,
  `:1249`) pass two POSITIONAL args, and NO caller uses `**kwargs` passthrough
  — the flag cannot conflict with any of them.
* `daemon/services/child_reports.py:2654` — the flag is forwarded into
  `_get_last_assistant_message`, which already accepts and propagates it
  (`:1317-1322` → the raw call at `:1350-1354` → `_get_last_assistant_message_raw`
  at `:1566`).
* `daemon/services/mission_strand_healer.py:1290` — the heal lane drives with
  `skip_repair=True`: the transcript can be 25h stale, and the repair exists
  to fix a TRUNCATED report for a human reader, while this lane only needs
  the content as the completion payload.

`daemon/manager.py:9731` was READ and deliberately NOT edited: it is a plain
forward of the drive call and is NOT on the heal path (the heal lane calls the
`ChildReportsService` directly), so exposing the flag there would add a
parameter no caller could use.

The flag precedent is the interim in-progress path
(`job_feedback_observer.py:1346-1355` → `manager._get_last_assistant_message_raw(
instance_id, skip_repair=True)`).

## 4. Files touched

| file | why | cycle |
|---|---|---|
| `daemon/services/mission_strand_healer.py` | the new strand shape (predicate, probes, drive, loud logging) + the shared per-root lock helpers | 1 |
| `daemon/config.py` | the three `ServicesConfig` knobs | 1 |
| `daemon/api.py` | wiring the knobs into the healer constructor | 1 |
| `tests/job_queue/test_running_zombie_sweep.py` | the new suite (predicate legs, end-to-end shape, negatives) | 1 |
| `tests/job_queue/test_terminal_write_census.py` | register the new terminal-write site (anti-rot registry — a new drive site MUST join) | 1 |
| `daemon/services/mission_live_guard.py` | **the ANCHOR fix** — the freshness anchor over the NON-ROOT members (the root's enqueue-refreshed stamp excluded; the childless-root fallback) | 2 |
| `daemon/services/mission_strand_healer.py` | **the TREE-LEG widening** (the guard call) + the wc-strand `skip_repair=True` | 2 |
| `daemon/services/child_reports.py` | **the RE-ARM** — the pre-stamp hold hands the root to the healer; the scheduler dispatches by posture and coalesces per root | 2 |
| `tests/job_queue/test_pattern_f_mission_live_gate.py` | the **f2-neutrality pin** (the call shape + the outcome, before and after the anchor move) | 2 |
| `tests/job_queue/test_running_zombie_sweep.py` | the cycle-2 suite: the incident fixture, the E2E heal for both shapes, the negatives, the anchor pins, the re-arm + coalescing, the wc skip_repair pin | 2 |
| `tests/job_queue/test_watcher_loss_terminal_strand_gap.py` | the mechanical line-citation refresh (the healer's and child_reports' line numbers moved) | 2 |
| `daemon/constants.py` | **the canonical non-terminal TASK status set** (`NON_TERMINAL_TASK_STATUSES`) — one home for the strand's root-level leg and the guard's member-task leg | 3 |
| `daemon/services/mission_live_guard.py` | **the MEMBER-TASK liveness leg** — the opt-in `task_engine` parameter, the batched live-task probe, the `member_task_hold` verdict field | 3 |
| `daemon/services/mission_strand_healer.py` | the leg-4 opt-in (`task_engine`) + the `member-live-task` skip class; the root-level task set now points at the constants SSOT | 3 |
| `daemon/services/child_reports.py` | the **pre-stamp gate** opt-in (`task_engine`) — the second consumer that inherits the leg | 3 |
| `daemon/services/job_recovery_service.py` | **COMMENT-ONLY** — the stale root-inclusive anchor prose (§9.4) | 3 |
| `daemon/services/job_queue_service.py` | **COMMENT-ONLY** — the stale root-inclusive anchor prose (§9.4) | 3 |
| `tests/job_queue/test_running_zombie_sweep.py` | the cycle-3 suite: the member-task battery (live pending/running/processing, no task row, terminal task row, the guard-level pins, the pre-stamp gate pin + its control) | 3 |
| `daemon/services/mission_live_guard.py` | **the MEMBER-ALIVE companion leg** — the opt-in `task_engine` parameter and the batched probe are REMOVED; the leg is unconditional and guard-side, time-bounded by the guard's own horizon, and reads the member's live task rows through the instance repository it already holds | 4 |
| `daemon/services/mission_strand_healer.py` | the leg-4 opt-in (`task_engine`) is REMOVED — the healer inherits the guard-side leg (its `member-live-task` skip class is unchanged) | 4 |
| `daemon/services/child_reports.py` | the **pre-stamp gate** opt-in (`task_engine`) is REMOVED — the gate inherits the guard-side leg (the re-arm is unchanged) | 4 |
| `daemon/services/child_reports.py` | the scheduler docstring — the F8 teardown caveat (the in-flight marker is released only by the done-callback) and the F7 event-arm/batch-cap disclosure | 4 |
| `tests/job_queue/test_running_zombie_sweep.py` | the cycle-4 suite: the companion-leg battery (the unconditional pin, the RULING-1b horizon matrix on BOTH sides, the additive invariant), the D4 anchor-gap disarm pin, and the D12b payload-content pin + its control | 4 |
| `tests/job_queue/test_u7_s15_wave3_pins.py` | the F5 stale docstring — DOCSTRING ONLY, zero behaviour | 4 |

**The census framing — CORRECTED in cycle 4 (A-7).** The circulating
"census MAIN gate clean (39==39)" framing is **WRONG**. The census's OWN
walker says: the branch base (`33e1ef513`) discovers **38** sites, of which
**31** are classified and **7** are unlisted; the head discovers **39**, of
which **32** are classified and **7** are unlisted. So the branch adds
exactly **ONE** discovered site **and classifies it** ⇒ **ZERO new unlisted
sites** — but the main gate **REDS AT BOTH SIDES** for the 7 pre-existing
unlisted ones (`daemon/manager.py:6757` + 6 `prompt_version` sites). "Same
count on both sides" is a **main-gate-RED** result, not a clean gate.

## 5. Evidence

* New suite: `tests/job_queue/test_running_zombie_sweep.py` — 34 tests
  (cycle 1) → **49 tests (cycle 2)** → **58 tests (cycle 3)** → **64
  tests (cycle 4)** → **65 tests** (the post-review batch) → **71
  tests (cycle 4 pre-merge: the A-1 pair + the W-3b bound test)**.
* **Cycle 4 FINAL, RE-MEASURED (W-4 — the numbers below supersede every
  earlier count in this document, which was one commit stale).** At the
  cycle-4 pre-merge tip:
  * the RZ pack collects **71 tests**;
  * whole `tests/job_queue/` base-vs-HEAD, IDENTICAL commands both
    sides (base `21a2be541` in a scratch worktree): base **2165 passed
    / 18 failed / 38 skipped / 3 deselected** (415.76s) → head **2171
    passed / 18 failed / 38 skipped / 3 deselected** (400.49s). The
    FAILED lists are **BYTE-IDENTICAL** — sha256
    `e3a3c74a04e34fd4d1c2ea5476721106d4565a55ccc47c5cc5be722a08cd58cf`
    on both sides — so the +6 passed is EXACTLY the RZ pack's growth
    (65 → 71) and there are ZERO net-new failures.
  * **MUTATION COUNTS (over the RZ pack + the f2 pack, 80 tests):**
    the companion leg's TERMINAL-CANDIDACY filter dropped
    (`mission_live_guard.py:572`) ⇒ **4 reds** (exactly the new A-1(b)
    parametrisation); the W-3b `created_at` bound removed ⇒ **1 red**
    (the new bound test); the H1 branch disabled (undatable row ⇒
    `None`) ⇒ **1 red** (the H1 test, failing with
    `assert 'gate-block' == 'member-live-task'` — the root was DRIVEN
    instead of HELD). The earlier council figures (8/2/1) were measured
    with a different mutation/pack scope and are superseded by these.
  * **G5 — the ABSOLUTE counts are environment-fragile; the digest is
    the invariant.** The independent reviewer's environment measured
    **20 failed / 2163 passed → 20 failed / 2169 passed** (vs our
    18/2165 → 18/2171): the +2 are **order-sensitive concurrency tests**
    (`test_watcher_repository_concurrent`) that are red at BOTH sides in
    both environments. The load-bearing invariants are (i) the
    **BYTE-IDENTICAL FAILED-list digest** across base-vs-HEAD within one
    environment — `e3a3c74a04e34fd4d1c2ea5476721106d4565a55ccc47c5cc5be7
    22a08cd58cf` in ours, `f53d151a…` in the reviewer's — and (ii) **zero
    net-new failures**. Do not compare absolute pass counts across
    environments.
* **Cycle 3 (2026-10-21)** — the suite grew to 58: 49 + the
  **member-task battery** — the three live task statuses
  (`pending`/`running`/`processing`) ⇒ NOT healed (skip class
  `member-live-task`, the drive NOT invoked, the root's status
  untouched); the member with NO task row ⇒ still healed (the observed
  shape); the member whose task is terminal-COMPLETED but whose
  instance row is non-terminal ⇒ still stale-eligible; the guard-level
  pins (with a task handle the tree is HELD and the backstop did NOT
  fire; WITHOUT a task handle the leg is INERT — the historical
  behaviour); the **pre-stamp gate** pin (a root whose member owns a
  live task row is NOT stamped, through the REAL gate) + its control
  (the same shape with a terminal member task row DOES stamp, so the
  hold is caused by the live task row and not by the shape).
* **Cycle 3 mutation proof** — the leg neutered
  (`_member_task_hold_verdict` short-circuited) ⇒ the member-task
  tests go **RED**; restored ⇒ **GREEN** (verbatim in the
  implementation report).
* **Cycle 3 whole-tree diff** — `tests/job_queue/` at the cycle-2
  HEAD and at the cycle-3 HEAD produce **IDENTICAL FAILED lists** (18
  pre-existing failures on both sides) ⇒ zero net-new failures.
* **Cycle 4 (2026-10-21) — the council rework.** The suite grew to 64:
  the obsolete opt-in pin DELETED (the leg is never inert now), and
  added — the **unconditional** pin (the guard's signature carries no
  opt-in parameter AND a plain call still gets the hold), the
  **RULING-1b horizon matrix on BOTH sides** (a task row younger than
  the horizon ⇒ held; older ⇒ stale-eligible ⇒ healable), the **D4
  anchor-gap disarm pin** (member-scoped NULL stamps ⇒ `live=True`,
  `timed_out=False`), and the **D12b payload-content pin** (the
  published completion payload is the RAW assistant message and the
  repair LLM is never called) **+ its control** (the same shape without
  the flag publishes the REPAIRED text — the pin is sensitive, not
  vacuous).
* **Cycle 4 mutation proofs (verbatim in the implementation report).**
  (1) the companion leg NEUTERED ⇒ **7 tests go RED** (the three
  member-task statuses, the guard-level hold pin, the unconditional pin,
  the RULING-1b younger side, the pre-stamp gate pin);
  (2) the TIME BOUND removed ⇒ **exactly the 2 RULING-1b older-side
  tests go RED** (the guard-level "older does not hold" pin and the
  healer-level "old task row is still healed" pin). Both mutants were
  restored from a byte-identical backup.
* **Cycle 4 whole-tree diff** — `tests/job_queue/` at the cycle-3 base
  (`8e45a62da`) and at the cycle-4 HEAD produce **IDENTICAL FAILED
  lists** (18 pre-existing failures on both sides) ⇒ zero net-new
  failures. **NUMERIC CORRECTION (D12a):** the base run is **2158
  passed / 18 failed / 38 skipped / 3 deselected** (426.56s) and the
  cycle-4 HEAD is **2164 passed / 18 failed / 38 skipped / 3
  deselected** (415.00s) — the +6 is exactly the RZ pack's growth
  (58 → 64). The earlier "2148/2149" figure was drift and is
  withdrawn; every number quoted in this document was re-measured
  against the run it describes.
* **The f2-neutrality pin** (`tests/job_queue/test_pattern_f_mission_live_gate.py`,
  9 tests) stays GREEN — f2's call site is untouched by the rework
  (its BEHAVIOUR widening is disclosed in §2.8.1).
* **Cycle 2 (2026-10-21)** — the suite grew to 49: 34 + the synthetic
  incident fixture (rebuilt from the READ-ONLY specimen snapshot; the
  specimen itself is NEVER committed) + the E2E heal for BOTH shapes
  (stale-member and all-terminal) through the REAL guard + the negatives
  (recently-active member, member with a live task, guard error, guard
  raise) + the anchor pins (a fresh root cannot hold a stale member open;
  the childless-root fallback; the member's activity is the anchor) + the
  re-arm wiring + the N-holds-coalesce proof + the wc-strand
  `skip_repair` pin + the kill-switch surface pin.
* **Cycle 2 f2-neutrality pin** (`tests/job_queue/test_pattern_f_mission_live_gate.py`):
  3 new tests — PASS **before** the anchor move and PASS **after** it
  (the pin-first proof).
* **Cycle 2 whole-tree diff**: `tests/job_queue/` at the base
  (`5edba8f7c`) and at the cycle-2 HEAD produce **IDENTICAL FAILED lists**
  (18 pre-existing failures on both sides — the 2 census failures among
  them, disclosed and NOT fixed) ⇒ **zero net-new failures, zero
  fixed-by-accident**.
* Review cycle 1 (2026-10-21) — the suite grew to 33: 24 base + the F1
  starvation regression + the `processing` parametrization (F3) + the W-B
  constructor floor (4 rejected cases + the admissible minimum) + the
  kill-switch log-once + the `skip_root=True` pin (sibling #4). The gap suite
  (`test_watcher_loss_terminal_strand_gap.py`) was re-run after the mechanical
  citation refresh (the healer's line numbers moved with the new edits).
* Sibling suites unchanged and green:
  `test_watcher_loss_terminal_strand.py`, `test_watcher_loss_terminal_strand_gap.py`,
  `test_mission_live_guard.py` (87 passed together with the new suite).
* Whole `tests/job_queue/` run: the failures are **pre-existing at base**
  (`33e1ef513`) — disclosed in the implementation report.
* The terminal-write census (`tests/job_queue/test_terminal_write_census.py`)
  gained a fixture entry for the new drive site; the census test still fails
  at BASE for unrelated pre-existing debt (the `prompt_version` lane) — the
  strand adds **zero** new unlisted sites.

## 6. Sibling-strand observations noticed while in there (NOT fixed here)

1. **`get_tree_ids_permanent` only WARNs on its depth cap**
   (`daemon/repositories/instance/repository.py:604-666`). A caller that needs a
   COMPLETE tree view has no way to act on that WARN. This strand adds a
   Python-side closure check (`_tree_is_closed`); the repo helper itself could
   grow a `truncated: bool` out-param instead — a named follow-up.
2. **Live-status sets — now TWO, not three** —
   `stale_pause_sweep._LIVE_STATUSES` and
   `daemon.constants.ALIVE_INSTANCE_STATUSES`. The `daemon/constants.py`
   comment explicitly warns that duplicated status sets are "a silent-divergence
   risk". The strand's own set
   (`mission_strand_healer._RUNNING_ZOMBIE_LIVE_STATUSES`) was **removed in
   cycle 2** — the widened tree leg delegates the liveness verdict to the
   canonical `mission_live_guard`, so the classification set had no consumer
   left (dead code). Consolidation of the remaining TWO into
   `daemon/constants.py` is still the right end-state. Named follow-up.
3. **The `stale_pause_sweep` pending-question probe FAILS OPEN** (its own
   docstring names this as follow-up W-B/W2). That posture is correct for a
   RELEASER and wrong for a TERMINALIZER — this strand re-implements the probe
   with inverted (fail-closed) semantics. If the stale-pause probe is ever
   hardened, the two should be unified rather than kept as parallel copies.
4. **The drive depends on the child_reports PRE-STAMP recursive liveness eval**
   (`child_reports.py:2746-2798`, `skip_root=True`). For a `running` root with
   all-terminal descendants it returns `live=False` — which is exactly WHY the
   running-zombie drive can stamp. A future change to that eval's `skip_root`
   semantics would silently stop the running-zombie strand from stamping (the
   heal would report `gate-block` every tick). **PINNED (review cycle 1,
   sibling #4):**
   `tests/job_queue/test_running_zombie_sweep.py::TestRunningZombieSkipRootPin`
   asserts the semantics the lane depends on — `skip_root=True` ⇒ `live=False`
   for a running root with terminal descendants, and `skip_root=False` ⇒
   `live=True` (the root's OWN status IS a live leg), so a semantic change
   cannot silently disarm the lane.
5. **The lock lifecycle on the already-`done` `JobItem` path is NOT f2's.**
   (Corrected in review cycle 1 — the earlier text here claimed "f2 released
   the locks when it finalized the JobItem"; it does not.) f2 explicitly does
   NOT release locks in its already-transitioned branch
   (`job_recovery_service.py:828-831`: "the actor that transitioned the job
   already released its lock" — re-releasing would be a no-op or, with the
   buggy `release_by_instance`, a sibling-lock wipe), and f2's blanket
   instance-scoped `release_by_instance` was REMOVED (the C1 fix — it was the
   sibling-lock-deletion bug). The lock is JOB-SCOPED: it is released by the
   actor that TRANSITIONED the `JobItem` — the job-queue service's
   `_finalize_terminal` (`job_recovery_service.py:838-845`). On the observer's
   `job_id=None` path the Step-3 release is a no-op (the virtual job holds no
   lock of its own; locks keyed to OTHER jobs' `job_id` MUST survive —
   `job_feedback_observer.py:1300-1306`). A variant where the `JobItem` is
   `done` but a lock is still held would leak that lock to the
   `JobLockSweepService`. Observed, not fixed.
6. **`daemon/services/mission_strand_healer.py` is now ~1.6k lines** and carries
   two strands with different postures. A decomposition (predicate module +
   per-strand modules) is a legitimate follow-up, but it is NOT in this
   commission's scope (the commission's home decision is explicit).

## 7. Sequencing

HOLD per the leader's shared KV: **no merge to `latest`, no stage, no
promote** — the v0.26.x promote ceremony (user nonce gate) owns that. This
commission lands as branch commits only.

## 8. ADMIN RECOVERY RUNBOOK (2026-10-21) — a stranded mission root

Three sanctioned paths. **The order matters**: prefer (a); use (b) only
when the work is provably done; **never** use (c).

### (a) AUTO-HEAL — the fix itself (preferred)

The running-zombie strand heals a provably-dead mission root with **no
termination of anybody**:

* the sweep (`MissionStrandHealer.sweep_running_zombies_once`, every 300s)
  selects a `running` root whose latest task row is terminal-COMPLETED and
  ≥2h old, drives the **production completion chain**, and the chain's own
  gates decide;
* the event-driven arm fires when the pre-stamp hold refuses a stamp
  (the re-arm, §2.8.3) — **but the heal it schedules REFUSES a young
  root** (the lane's own age bound: the root's latest task row must be
  terminal-`COMPLETED` for ≥2h). So the REAL heal lands via the 300s
  sweep, **≥2h after the root's task completed**. The earlier wording
  ("zero sweep latency") was **OVERSTATED** and is withdrawn: what the
  event arm buys is a **prompt attempt** (and the re-arm guarantee that
  the root is never left waiting for a signal that never comes), not a
  sub-second heal;
* the heal is **non-destructive**: it finalizes the ROOT through the
  production chain; no descendant is terminated, no `message_queue` /
  `task` row is removed.

Operator visibility: the heal logs
`mission_strand_healer|running-zombie: instance=… transitioned=completed
reason=finalize_gap`, and every skip logs a loud, greppable
`SKIP class=… detail=…` line.

> **CYCLE-3 SKIP CLASS (operator note).** A skip with
> `class=member-live-task` means a **non-terminal member of the tree
> owns a LIVE task row** — that member is WORKING, so the root is
> deliberately NOT finalized (fail-MISS over a false completion,
> §2.9). Such a root stays `running` until the member's turn
> settles; if that member's task never completes, the root is in the
> hung-task-member sub-family (§9.6) and needs the admin path
> (§8b), not a retry.

### (b) TERMINATE via DELETE — for done-work rows the heal cannot prove

When the work is provably finished but the heal's legs cannot prove it
(e.g. a permanent tree over the probe bound, a permanently live
descendant), the sanctioned operator path is the **`DELETE` API**, whose
terminate cascade:

* removes the `message_queue` + `task` rows of the tree;
* cascades to the **non-terminal** descendants and **skips** the terminal
  ones;
* **does NOT clear the `paused_at` vestige** on a paused row (a known
  vestige — the row keeps the vestige column after the terminate).

### (c) ⛔ NEVER `POST /stop` on a zombie

**Verified live (2026-10-21):** `POST /stop` is a **PAUSE CASCADE**, not
a terminate. On a zombie root it produces:

* a paused root + paused idle descendants;
* a **ghost-skip** for the never-dispatched rows;
* ~35 terminal descendants skipped;
* **zero** job/task side effects.

The result is a **paused-no-task row** that the v0.26.1 stale-pause
sweeper will **NOT** release (that sweeper is **question-marker-only**)
⇒ the mission ends up **parked permanently**. `DELETE` (§b) is the
sanctioned operator path; `POST /stop` is for a live mission the operator
wants to pause, never for a zombie.

## 9. Sibling strands observed (NOT fixed on this branch)

1. **PP1 — post-termination watcher-loss (routed ELSEWHERE, not this
   branch).** In the same incident, the watcher row for the root was
   removed in the DELETE window (06:38:42 → 06:44:21, actor unverified),
   which produced a **zero-watcher terminal fire** at 06:44:21 ("terminal
   report NOT being delivered") and **no `job_completed` event row** for
   the root (only the f2-synthetic one) ⇒ the watcher side was never woken
   (the watcher was active, holding a job lock since 06:40:52). The fix is
   **NOT** in this branch — it is routed as a follow-up commission.
2. **Operator-stop strand.** The `POST /stop` behaviour documented in §8(c)
   is its own strand family (pause-cascade semantics on a row that should
   have been terminated). Observed, not fixed here.
3. **Member retirement (auto-terminate stale idle members) — DEFERRED.**
   It is only needed for the sub-family "a member stays active forever and
   never goes terminal" (**unobserved** in the forensics). Its own root
   cause would be the member's recursive strand. **No auto-termination
   ships this train** — the anchor fix + the tree-leg widening already
   close the observed shapes, and member retirement would be a
   TERMINALIZING write over a member the heal cannot prove is dead.
4. **C1 (cycle-2 review) — stale root-inclusive anchor PROSE in two
   sibling files — APPLIED in cycle 3 (COMMENT-ONLY).** The comment
   blocks at `daemon/services/job_queue_service.py` (the
   `_mission_live_guard_holds` docstring) and
   `daemon/services/job_recovery_service.py` (the Gate-4 comment) used
   to describe the freshness anchor as the max over the whole tree
   (the root INCLUDED). The cycle-2 anchor fix changed that semantic
   (the anchor is the freshest `last_activity_at` over the NON-ROOT
   members; the root's own stamp is the fallback for a CHILDLESS tree
   only). Both sites now describe the NEW semantics — **comments and
   docstring only, zero behaviour change**, isolated in their own
   commit so the reviewer can verify it mechanically.
5. **Watchdog signal quality (cycle-2 finding, cycle-3 follow-up) —
   NO CODE THIS TRAIN.** The watchdog's hang notice can be a FALSE
   POSITIVE because it keys on `instances.last_activity_at`, which is
   **not written on turn progress**: a child's `last_activity_at` froze
   at 07:17:47 while its transcript ran on to 08:19 — the same
   frozen-activity-metadata pathology family as the anchor defect.
   The watchdog should key on the **transcript / heartbeat**, not the
   frozen column. **Documentation only in this train** — the fix is a
   follow-up commission (the watchdog's own lane, outside this
   branch's fence).
6. **The hung-task-member sub-family — RESHAPED by RULING-1b, not
   closed.** A member that owns a task row which never completes held
   its mission open forever under the cycle-3 (unbounded) leg. Under the
   cycle-4 leg it holds the mission only while that row is YOUNGER than
   the 6h horizon — after that the member is stale-eligible again and
   the root heals. The residual that REMAINS is the member inside a
   **legitimate >6h turn** (§2.10.5) — indistinguishable from stale,
   because the activity column is not written on turn progress. Its own
   root cause is that member's recursive strand (deferred by design,
   §9.3); the admin path (§8) is the recovery.
7. **Heartbeat on turn progress — NO CODE THIS TRAIN (named
   follow-up).** The `task` rows already carry `last_heartbeat_at`
   (the worker's liveness signal). Keying the member-alive leg — and
   the watchdog's hang notice — on the heartbeat instead of the frozen
   `instances.last_activity_at` would close the ">6h legitimate turn"
   residual (§2.10.5) AND the watchdog's false-positive class (§9.5) in
   one change. It needs its own commission (it touches the watchdog's
   lane, outside this branch's fence).
8. **An in-flight ceiling for the event-driven arm — NAMED, NOT
   IMPLEMENTED (F7).** The event arm's coalescing is per-root, so N
   distinct stranded roots ⇒ N concurrent hand-offs, bypassing the
   healer's per-tick `batch_cap`. The choice this train is **disclose**
   (§2.8.3); a per-process ceiling is the candidate hardening.

## 10. PROMOTE-DISCLOSURE MATERIAL (cycle 4)

Carry these into the promote ceremony — they are the honest statements the
merge decision rests on.

### 10.1 The census framing (A-7)

The census's own walker: **base `33e1ef513` = 38 discovered / 31 classified
/ 7 unlisted**; **head = 39 / 32 / 7**. The branch adds exactly ONE
discovered site AND classifies it ⇒ **zero new unlisted sites**. The main
gate **REDS AT BOTH SIDES** for the 7 pre-existing unlisted ones
(`daemon/manager.py:6757` + 6 `prompt_version` sites). The "39==39 clean"
framing is withdrawn — the anti-rot test was already red at base.

### 10.2 Line-pin attribution (W-5)

Of the census's 17 line-pins, **14 are PRE-EXISTING** and **3 were
introduced (or broken) by this branch**: `child_reports.py:5173` → `:5321`
(two entries — the notify moved) and `job_recovery_service.py:4255` →
`:4261` (the granted comment-only commit is CONTENT-inert but NOT
line-inert). The three are re-pointed in the census TEST file;
`job_recovery_service.py` itself is untouched.

### 10.3 D11 scope — live-derived ids (A-4)

The base tree carried **no** live-derived instance id in `daemon/` — the
three this branch added (`mission_strand_healer.py` ×2, `config.py` ×1)
are **this branch's debt** and are now scrubbed to descriptive
placeholders. The pre-existing repo convention (live ids in daemon
comments elsewhere in the base tree) is **not** this branch's debt. This
design doc keeps its incident ids deliberately: the §1 anchor and the
verbatim live log line are **grep aids** and are marked as such.

### 10.4 The four earlier disclosures (still true at cycle 4)

1. **f2's BEHAVIOUR widens** (its CODE is untouched) — §2.8.1.1's
   consumer table, row 5.
2. **The probe-error hold posture across consumers** — a MEMBER-ALIVE probe
   error HOLDS (`live=True` + `error=True`) at every consumer; the
   tree-walk error path is the only fail-open one (W-2, §2.10.3/§2.10.5).
   The hold is not a permanent strand: the observer / the next pass
   re-drives it. All four consumers now LOG it (A-2).
3. **A member inside a legitimate >6h turn is still indistinguishable from
   stale** — the horizon is the system's own; the fix is a **heartbeat on
   turn progress** (the task rows carry `last_heartbeat_at`), a named
   follow-up (§2.10.5).
4. **A member whose only live work is a JOBITEM row (no task row) is NOT
   held** (the task row is the only DATEABLE evidence class) — §2.10.5;
   and the **event arm's `batch_cap` bypass is unbounded** (N stranded roots
   ⇒ N concurrent hand-offs), disclosed in §2.8.3 — a per-process ceiling
   is the candidate hardening.

