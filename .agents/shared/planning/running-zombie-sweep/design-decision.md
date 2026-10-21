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
2. **DB stamp** (`child_reports.py:3462-3495`) — the atomic
   `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
   (TOCTOU guard) flips `running → completed`.
3. **Emission-time gate** (`child_reports.py:2363-2510`) — re-verifies bus
   pending, tree liveness, pending messages and freshness. On a block the stamp
   is **downgraded** to `waiting_children` (`child_reports.py:4826-4850`) and the
   publish is suppressed — a **legitimate miss**: log it, retry next tick, never
   force.
4. **Observer finalize** (`job_feedback_observer.py:1620/:3664`) — transitions the
   `JobItem` (atomic, `job_queue/repository.py:1692`), writes the instance
   terminal row, releases the job locks.

Mission liveness is derived READ-TIME from `Instance.status`
(`mission_resolver.py:980-1006`) — there is no persisted epoch row. "Epoch ends,
outcome recorded" == this status write + outcome + lifecycle event, which the
chain provides.

### 2.3 Predicate — all legs must hold, every failure errs toward MISS

| # | leg | code |
|---|---|---|
| 1 | `status == 'running'` AND `parent_id IS NULL` (mission root only) | `_heal_running_zombie_locked` |
| 2 | latest task row terminal-`COMPLETED` with `completed_at`; NO non-terminal task row (no PENDING/RUNNING/PAUSED/PROCESSING) | `_probe_task_rows` |
| 3 | age bound on `task.completed_at` (default 7200s) | `_age_seconds` |
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

## 3. Known residuals (documented, not closed)

### 3.0 Idempotency of the drive against an already-finalized JobItem

The 25h incident shape has the `JobItem` ALREADY `done` (f2 healed it). The
drive chain handles that without harm:

* the child_reports DB stamp is an atomic
  `UPDATE … WHERE status NOT IN (paused, completed, error, waiting_children)`
  (`child_reports.py:3462-3495`) — a second run against an already-terminal row
  matches 0 rows and returns `idempotency_skip` (no duplicate publish);
* the observer's Step 1 (`job_feedback_observer.py`) is guarded by
  `WHERE admission_state = 'active'` — an already-`done` JobItem matches 0 rows
  and raises `InvalidTransitionError`, which the async caller absorbs at DEBUG
  (documented idempotency, `job_feedback_observer.py:3726-3735`). The INSTANCE
  row is already terminal by then (the child_reports stamp wrote it), so
  nothing is lost;
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

## 4. Files touched

| file | why |
|---|---|
| `daemon/services/mission_strand_healer.py` | the new strand shape (predicate, probes, drive, loud logging) + the shared per-root lock helpers |
| `daemon/config.py` | the three `ServicesConfig` knobs |
| `daemon/api.py` | wiring the knobs into the healer constructor |
| `tests/job_queue/test_running_zombie_sweep.py` | the new suite (predicate legs, end-to-end shape, negatives) |
| `tests/job_queue/test_terminal_write_census.py` | register the new terminal-write site (anti-rot registry — a new drive site MUST join) |

## 5. Evidence

* New suite: `tests/job_queue/test_running_zombie_sweep.py` — 24 tests.
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
   heal would report `gate-block` every tick). Worth a pin.
5. **The observer's Step 1 raises `InvalidTransitionError` for an already-`done`
   JobItem**, which aborts the 3-step transaction — including the lock release
   (Step 3). In the running-zombie shape nothing is lost (the instance row is
   already terminal; f2 released the locks when it finalized the JobItem). A
   variant where the JobItem is `done` but a lock is still held would leak that
   lock to the `JobLockSweepService`. Observed, not fixed.
6. **`daemon/services/mission_strand_healer.py` is now ~1.4k lines** and carries
   two strands with different postures. A decomposition (predicate module +
   per-strand modules) is a legitimate follow-up, but it is NOT in this
   commission's scope (the commission's home decision is explicit).

## 7. Sequencing

HOLD per the leader's shared KV: **no merge to `latest`, no stage, no
promote** — the v0.26.x promote ceremony (user nonce gate) owns that. This
commission lands as branch commits only.

