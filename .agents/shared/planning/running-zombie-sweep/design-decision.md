# Running-zombie strand — design decision

**Commission:** "running-zombie instances" (third strand-family member)
**Date:** 2026-10-21
**Branch:** `fix/running-zombie-sweep` (base `origin/latest` @ `33e1ef513`)
**Status:** IMPLEMENTED — no promote (the v0.26.x ceremony owns that)

---

## 1. The incident shape

Live incident 2026-10-19/20, instance `432988b8`:

| field | value |
|---|---|
| `instances.status` | `running` (stuck 25h) |
| `instances.parent_id` | `NULL` (mission root) |
| descendants | ALL terminal |
| root's latest `task` row | `completed`, `completed_at` set |
| `job_queue_items.admission_state` | already `done` (f2 finalized) |

The completion **EVENT** was lost. f2 (`job_recovery_service`) healed only the
`JobItem` — the mission-live guard's all-terminal short-circuit
(`daemon/services/mission_live_guard.py:509-524`) releases the held row — but
the **INSTANCE** row is a different table and stayed `running` forever. Nothing
in the tree ever re-arms: the mission looks live to every reader.

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
(`daemon/services/child_reports.py:2517`). NEVER a raw status UPDATE, NEVER
`terminate_instance` on the root.

The chain, in order:

1. **Pre-stamp recursive liveness** (`child_reports.py:2600-2645`, `skip_root=True`)
   — all-terminal tree ⇒ `live=False` ⇒ falls through to the stamp.
2. **DB stamp** (`child_reports.py:3463-3495`) — the atomic
   `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
   (TOCTOU guard) flips `running → completed`.
3. **Emission-time gate** (`child_reports.py:2363-2510`) — re-verifies bus
   pending, tree liveness, pending messages and freshness. On a block the stamp
   is **downgraded** to `waiting_children` (`child_reports.py:4826-4850`) and the
   publish is suppressed — a **legitimate miss**: log it, never force. The
   downgrade MOVES the root out of the `running` posture, so it **exits this
   lane's candidate set** (the selection is anchored on `status == running`) and
   the `waiting_children` strand owns the root from then on — this lane does
   **not** retry it on the next tick.
4. **Lifecycle-event publish → observer finalize** (`child_reports.py:5021-5027`
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
| 4 | whole permanent tree terminal + enumeration neither breadth- nor depth-truncated | `_tree_is_closed` + `get_tree_ids_permanent` |
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

## 3. Known residuals (documented, not closed)

### 3.0 Idempotency of the drive against an already-finalized JobItem

The 25h incident shape has the `JobItem` ALREADY `done` (f2 healed it). The
drive chain handles that without harm:

* the child_reports DB stamp is an atomic
  `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
  (`child_reports.py:3463-3495`) — a second run against an already-terminal row
  matches 0 rows and returns `idempotency_skip` (no duplicate publish);
* the drive **DOES** reach the observer's `_finalize_job` — via the
  lifecycle-event publish (`child_reports.py:5021-5027` → the observer's
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

### 3.4 The drive can still trigger the LLM report-repair (OPEN — one-file follow-up)

The drive's content fetch (`child_reports.py:2547`) calls
`_get_last_assistant_message` WITHOUT `skip_repair`, so the 30s LLM
report-repair can fire on a 25h-stale transcript before the gates run. The
reviewer's remedy — pass `skip_repair=True` on the drive — is **NOT applied**:
`_process_child_completion_and_notify_parent(instance_id, completed_message_id)`
(`child_reports.py:2517`) has NO `skip_repair` parameter, and the wrapper
`_get_last_assistant_message` (`child_reports.py:1317-1322`) only forwards the
flag when it is GIVEN. Threading it through needs a three-line change in
`daemon/services/child_reports.py` (add the keyword-only parameter to the
drive, forward it at `:2547`, and — for the manager seam — to
`manager.py:9733`), which is OUTSIDE the review-cycle-1 file fence. The
precedent for the flag is the interim in-progress path
(`job_feedback_observer.py:1346-1355` → `manager._get_last_assistant_message_raw(
instance_id, skip_repair=True)`). A one-file follow-up closes it.

## 4. Files touched

| file | why |
|---|---|
| `daemon/services/mission_strand_healer.py` | the new strand shape (predicate, probes, drive, loud logging) + the shared per-root lock helpers |
| `daemon/config.py` | the three `ServicesConfig` knobs |
| `daemon/api.py` | wiring the knobs into the healer constructor |
| `tests/job_queue/test_running_zombie_sweep.py` | the new suite (predicate legs, end-to-end shape, negatives) |
| `tests/job_queue/test_terminal_write_census.py` | register the new terminal-write site (anti-rot registry — a new drive site MUST join) |

## 5. Evidence

* New suite: `tests/job_queue/test_running_zombie_sweep.py` — 33 tests.
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
2. **Three live-status sets now exist** —
   `stale_pause_sweep._LIVE_STATUSES`,
   `daemon.constants.ALIVE_INSTANCE_STATUSES` and
   `mission_strand_healer._RUNNING_ZOMBIE_LIVE_STATUSES`. The `daemon/constants.py`
   comment explicitly warns that duplicated status sets are "a silent-divergence
   risk". The strand's set is a CLASSIFICATION set (see the code comment), not a
   liveness authority — but consolidation into `daemon/constants.py` is the right
   end-state. Named follow-up.
3. **The `stale_pause_sweep` pending-question probe FAILS OPEN** (its own
   docstring names this as follow-up W-B/W2). That posture is correct for a
   RELEASER and wrong for a TERMINALIZER — this strand re-implements the probe
   with inverted (fail-closed) semantics. If the stale-pause probe is ever
   hardened, the two should be unified rather than kept as parallel copies.
4. **The drive depends on the child_reports PRE-STAMP recursive liveness eval**
   (`child_reports.py:2600-2645`, `skip_root=True`). For a `running` root with
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

