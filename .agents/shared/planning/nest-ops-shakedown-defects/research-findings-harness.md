# nest-ops shakedown patch — research findings (healers / test harness / doc surfaces)

**Tree:** `/home/nea/ensemble-src-wt-nestops-shakedown` (branch `fix/nest-ops-shakedown-defects`, base `origin/latest b257842c9`, v0.27.1-era).
**Method:** read-only inspection of this worktree. All citations are file:line from THIS tree unless stated otherwise. No file outside this one was written.

---

# PART A — HEALER / DETECTOR FAMILY

## A.0 The file/detector inventory

| # | Component | File | Role |
|---|---|---|---|
| 1 | `MissionStrandHealer` (WC/ghost lane + running-zombie lane) | `daemon/services/mission_strand_healer.py` (1634 lines) | WRITE side: terminalizes provably-dead shapes |
| 2 | `evaluate_mission_live` + member-alive companion leg | `daemon/services/mission_live_guard.py` (1055 lines) | READ side: the shared liveness chokepoint |
| 3 | `OrphanWatcherSweepService` | `daemon/services/orphan_watcher_sweep.py` (313 lines) | 90s sweep over the **dependency-bus watcher table** |
| 4 | Pre-stamp recursive-liveness hold + re-arm | `daemon/services/child_reports.py` (5997 lines) | root turn-end gate; hands held roots to the healer |
| 5 | Boot orphan sweep `sweep_orphaned_live_turns` | `daemon/services/auto_continue_boot_pass.py` (880 lines) | boot-only; task-less **RUNNING** rows |
| 6 | Zombie reaper `_build_zombie_scan_sql` | `daemon/repositories/instance/repository.py` (3667 lines) | scan targets non-terminal rows with a `last_activity_at` |
| 7 | `instance_health` six detectors | `daemon/tools/instance_read.py` (1415 lines) | read-only wedge report |

---

## A.1 Each healer's DETECTOR / PREDICATE, with thresholds and config keys

### 1. `MissionStrandHealer` — `daemon/services/mission_strand_healer.py`

Module docstring states the two shapes and their invariants: `:1-65` (watcher-loss terminal strand, 2026-10-14) and `:67-98` (running-zombie strand, 2026-10-21).

**(a) `waiting_children` / ghost lane — `heal_instance` (:505), `_heal_instance_locked` (:531).**

HEAL INVARIANT (all legs must hold; ANY miss ⇒ skip, fail-closed), quoted from `:24-38`:

```
  root.status == WAITING_CHILDREN (parent_id IS NULL)          [the wedge posture]
  AND zero pending dependency-bus watchers for the root        [no pending children]
  AND zero READY/PROCESSING/RETRYING messages for the root     [queue drained]
  AND zero PENDING tasks for the root                          [no in-flight turn]
  AND zero PENDING/DEFERRED report_injections obligated to the root ...
  AND every non-root non-terminal tree member matches the
      terminal-ancestry-orphan signature: never-dispatched
      (idle + version 1 + zero message_queue rows + zero task
      rows — the canonical ghost signature) with a TERMINAL
      direct parent and ``updated_at`` older than
      ``ORPHAN_MIN_AGE_SECONDS`` (race guard ...)
```

Safety bound, `:54-58`: *"the healer NEVER terminalizes a never-dispatched row under a LIVE direct parent (the legitimate spawn-then-message ritual), NEVER touches PAUSED roots (operator domain), and aborts the whole heal when any tree member fails the strand signature."*

Cadence/knobs: ALWAYS-ON, no kill-switch, interval `ServicesConfig.mission_strand_heal_interval_seconds` default 300s (`:60-65`).

**(b) running-zombie lane — `sweep_running_zombies_once` (:801), `_find_running_root_ids` (:883), `_heal_running_zombie_locked` (:980).**

Selection IS the anchor (completion proof). SQL at `:931-949`:

```python
latest_task_id = (select(Task.id).where(Task.instance_id == Instance.instance_id)
                  .order_by(Task.created_at.desc(), Task.id.desc()).limit(1)
                  .correlate(Instance).scalar_subquery())
rows = session.exec(select(Instance.instance_id, Task.completed_at)
    .join(Task, Task.id == latest_task_id)
    .where(Instance.status == InstanceStatus.RUNNING.value)
    .where(Instance.parent_id.is_(None))
    .where(Task.status == TaskStatus.COMPLETED.value)
    .where(Task.completed_at.isnot(None))
    .order_by(Task.completed_at.asc())
    .limit(self._running_zombie_batch_cap)).all()
```

Age bound applied in PYTHON, not SQL (`:950-958`), because the naive-UTC stamp is TEXT on SQLite (`:921-929`): `if age is None or age < self._running_zombie_age_bound_seconds: continue`.

Predicate legs, in order (every one is a fail-closed early return):

| Leg | Condition | Skip class | Line |
|---|---|---|---|
| repo unwired | `repo is None` | `unwired` | `:988-992` |
| root exists | `root is None` | `root-missing` | `:993-997` |
| roots only | `root.parent_id is not None` | `not-root` | `:998-1003` |
| posture | `root.status != RUNNING` | `not-running` | `:1004-1009` |
| task exists | `not task_probe["has_any"]` | `no-task-row` | `:1015-1022` |
| latest COMPLETED | `latest_task.status != COMPLETED` | `latest-task-not-completed` | `:1029-1034` |
| stamp present | `latest_task.completed_at is None` | `latest-task-no-completed-at` | `:1035-1040` |
| no live turn | `task_probe["non_terminal"] > 0` | `non-terminal-task` | `:1041-1046` |
| age | `completed_age < age_bound` | `young` | `:1063-1069` |
| tree read | exception | `tree-probe-error` | `:1072-1080` |
| tree size | `len(probe_ids) > _RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE` | `tree-truncated` | `:1087-1092` |
| tree closure | not `_tree_is_closed(...)` | `tree-truncated` | `:1097-1109` |
| descendants readable | any missing status | `tree-incomplete` | `:1122-1130` |
| guard verdict | `tree_verdict.error` | `guard-error` | `:1182-1188` |
| member alive | `tree_verdict.live and member_task_hold` | `member-live-task` | `:1189-1198` |
| member alive | `tree_verdict.live` | `live-tree` | `:1199-1202` |
| question | pending question in tree | `pending-question` | `:1211-1223` |
| bus | `bus is None` | `bus-unwired` | `:1227-1237` |
| bus | `bus_pending > 0` | `bus-pending` | `:1247-1251` |
| queue | READY/PROCESSING/RETRYING root messages | (continues `:1253+`) | `:1253+` |

Thresholds / config:
- `DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS = 7200` — `:197`
- `DEFAULT_RUNNING_ZOMBIE_BATCH_CAP = 25` — `:204`
- `_RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE = 256` — `:210`
- `_RUNNING_ZOMBIE_NON_TERMINAL_TASK_STATUSES` — `:219`, sourced from `daemon.constants.NON_TERMINAL_TASK_STATUSES` at `:1467`
- constructor floors `>= 1` raise `ValueError` on `batch_cap`/`age_bound` — `:300-308`
- kill-switch: `self._running_zombie_enabled` `:280`, short-circuit BEFORE candidate selection `:815-837` (1 WARN then DEBUG repeats; `[warned once per healer instance]`), flag `_running_zombie_kill_switch_warned` `:314`
- env names (from the kill-switch message, `:827`): `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED`; siblings `..._AGE_BOUND_SECONDS`, `..._BATCH_CAP` (config layer `daemon/config.py:2417-2485`, wired `daemon/api.py:1638-1657` per project record)

**Hard invariant (age key):** `task.completed_at`, NEVER `instances.last_activity_at` — `:1048-1052`:

```
# PREDICATE CORRECTION #1: the age keys on the TASK's
# completed_at — NOT ``instances.last_activity_at``, which is
# frozen-by-design for a running root and cannot distinguish a
# zombie from a long-legitimate turn.
```

**Roots with NO task row are never swept** — `:1015-1022`: *"a root with NO task row has no completion proof — indistinguishable from a root that never started. NEVER swept (deliberate miss)."*

### 2. `mission_live_guard.evaluate_mission_live` — `daemon/services/mission_live_guard.py`

- Fail-open contract for the TREE WALK (`:40-43`): any exception → `live=False, error=True`.
- OPPOSITE polarity for the member-alive companion leg (`:45-50`): a probe that cannot be evaluated HOLDS the tree (`live=True, error=True`).
- Leg 0 zombie backstop anchor = **max `last_activity_at` over NON-ROOT members only** (`:62-102`, implementation `:831-875`). The old whole-tree max (root included) is named as the defect the running-zombie strand closed (`:66-80`).
- Horizon constant: `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` (21600s) consumed at `:1170` (strand healer) and `child_reports.py:2594` / `:2750`.
- Strand exclusion (never-dispatched ghost under a TERMINAL direct parent, age-mirrored) — `:790-830`; batched query via `filter_never_dispatched_ids` `:896-925`.
- Decision tree documented at `:723-741` (all-terminal → live=False immediately; non-terminal + stale anchor → timed_out; non-terminal + fresh → defer; no anchor → live=True data-gap disarm).

**Member-alive companion leg** (`:104-145` prose, code `:424-708`):
- `_member_live_task_attempt_at` (`:424-516`) reads `Task` rows with `Task.status.in_(sorted(NON_TERMINAL_TASK_STATUSES))` (`:485-491`), stamp = `started_at` else `created_at` (`:494-502`), undateable-row fallback `now_utc_naive()` (`:511-516`).
- `_member_alive_companion_verdict` (`:519-708`): candidates = non-root, non-terminal, not stranded-excluded (`:568-574`); `has_live_work` probe (`:598-600`); probe error → HOLD (`:601-617`, `:623-639`); `attempt_at is None` → stale-eligible, NOT held (`:640-671`, "NO non-terminal task row at all ⇒ no dateable evidence of an in-flight turn ⇒ NOT held (the ADDITIVE invariant)" `:507-510`); young task row → HOLD with `member_task_hold=True` (`:672-696`); older than horizon → stale-eligible (`:697-706`).
- Aggregate note: `_flush_undatable_note()` (`:580-594`) called at `:608`, `:630`, `:685`, `:707`. NOTE: at `:680-685` the young-task HOLD now flushes ("P1: this HOLD path is an evaluation exit too — flush the aggregate here"), so the previously-recorded "hold discards the aggregate INFO" defect (RAG record from branch `9b2068369`) appears already addressed in this tree.
- Two pinned invariants (`:133-139`): ADDITIVE (never a replacement) and FAIL-MISS.

### 3. `OrphanWatcherSweepService` — `daemon/services/orphan_watcher_sweep.py`
- Job: cancel PENDING **dependency-bus watchers** whose `source_task_id` no longer corresponds to an active task (`:3-11`). Keys on the BUS table — not on instance rows at all.
- Always on, no env flag (`:22-28`); interval `DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS = 90` (`:75`); grace `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS = 30` (`:82`); single atomic conditional UPDATE, idempotent (`:30-36`); class `:85`.

### 4. Pre-stamp hold + re-arm — `daemon/services/child_reports.py`
- Pre-stamp recursive-liveness eval for ROOT lanes only (`instance_meta.parent_id is None`) — `:2746-2795`. Eval at `:2752-2765` with `skip_root=True` (`:2760-2764`); `bus_pending_count=None` (`:2758`).
- On `live`: log + `self._schedule_strand_heal(instance_id, posture=instance_meta.status)` then bare `return` — no stamp, no gate, no publish (`:2766-2790`).
- The re-arm rationale is quoted in-code (`:2773-2786`): *"the historical contract was 'the next message-completed signal re-checks' — but on a STRANDED tree that signal provably never comes (…455 hold lines across ~40 roots and the root stayed `running` for 25h)"*.
- `_schedule_strand_heal` (`:2340-2447`): per-root COALESCING via `self._strand_heal_inflight` (`:2392-2403`); posture dispatch `RUNNING → heal_running_zombie` (`:2429-2432`), `WAITING_CHILDREN → heal_instance` (`:2433-2435`); any other posture is a no-op (`:2436-2441`).
- Disclosed bounds: F7 — N distinct stranded roots ⇒ N concurrent hand-offs, NOT bounded by `batch_cap` (`:2365-2372`); F8 — teardown can wedge a root's event arm, the 300s sweep is the net (`:2374-2379`).
- Emission-gate tree-liveness leg (defence in depth) — `:2585-2614`, fail-OPEN on exception (`:2602-2614`).

### 5. Boot orphan sweep — `daemon/services/auto_continue_boot_pass.py`
- `sweep_orphaned_live_turns(manager, boot_epoch)` `:651-:719+`. Docstring `:655-676`: **"Boot-time task-less orphan sweep … covers the disjoint sub-class of RUNNING instances whose `task` row was lost."**
- Kill-switch shared with AutoContinue `:680-689`; `boot_epoch is None` → skip, `StaleTaskRecovery backstop will catch orphans at boot+10min` `:691-699`; selection `task_repo.find_orphaned_live_turns(boot_epoch=...)` `:710-714`.

### 6. Zombie reaper + coarse liveness probe — `daemon/repositories/instance/repository.py`
- `_build_zombie_scan_sql` `:1898`; its Not-Exists clause comment `:1868`.
- `_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN` `:1883` (pending/running/paused only — the coarse set, deliberately missing `processing`).
- `has_live_work` `:2112`, docstring `:2133-2169`.
- **SQL requires a non-NULL activity stamp**: `AND last_activity_at IS NOT NULL` `:3451`; comment `:3488` — *"``last_activity_at IS NULL`` rows are skipped by the SQL"*; `:3493` — "later tick after `last_activity_at` is populated".

### 7. `instance_health` six detectors — `daemon/tools/instance_read.py:1055-1300`
- Scan source: `manager.list_instances(limit=FLEET_SCAN_LIMIT, offset=0)` `:1066-1068`; `FLEET_SCAN_LIMIT = 1000` `:133`; truncation flag `:897-900`, `:1060-1062`.
- `stale-running` `:1074-1107` — requires `info.get("status") != "running" → continue` (`:1080`), age from `_lineage_max_activity` (`:1083`).
- `waiting-on-a-dead-parent` `:1109-1137` — requires `status == "waiting_children"` (`:1112`) and parent not in `ALIVE_INSTANCE_STATUSES` (`:1117-1119`).
- `question-paused-without-task-row` `:1169-…`; `zombie-pending` `:1222-1234`.
- `orphans` `:1236-1269` — requires a **non-null parent** and a non-alive parent:
  ```python
  parent_id = info.get("parent_id") or None
  if not parent_id:
      continue                       # :1246-1247
  parent_status = _parent_status_of(repo, by_id, parent_id)
  if parent_status in ALIVE_INSTANCE_STATUSES:
      continue                       # :1249-1250
  ```
- `failed-with-unreported-result` `:1271-1298` — `status in ("failed","error")` and has children (`:1274-1278`).
- Every detector row carries `disagreement_pin` (`:920-922`), `null` where no observable counterpart exists (honest gap) — e.g. `:1103-1105`, `:1295-1296`.

### 8. Shared SSOT constants — `daemon/constants.py`
- `TERMINAL_INSTANCE_STATUSES = {completed, terminated, error, failed}` — `:702-707`
- `ALIVE_INSTANCE_STATUSES = {idle, running, paused, queued, waiting_children}` — `:760-766`
- `NON_TERMINAL_TASK_STATUSES = {pending, running, paused, processing}` — declaration `:803`, rationale `:769-797` (`processing` is legacy task vocabulary kept deliberately)

---

## A.2 Why each is structurally UNABLE to match a husk (no artifacts, NULL last_activity_at)

**The husk's exact row shape** (see A.5): `status = idle`, `version = 1`, zero `message_queue` rows, zero `task` rows, `last_activity_at = NULL`, `created_at = updated_at = spawn time`.

| Healer / detector | The specific guard that skips it |
|---|---|
| running-zombie lane | Never a **candidate**: the anchor join requires `Instance.status == RUNNING` + a `Task` row `COMPLETED` with `completed_at` (`mission_strand_healer.py:931-949`). Even if forced in, `has_any == False → "no-task-row"` (`:1015-1022`). |
| WC/ghost lane | Requires the ROOT to be `waiting_children` (`:24-26`). As a member it must match the ghost signature **and** sit under a TERMINAL direct parent **and** be older than `ORPHAN_MIN_AGE_SECONDS` (`:32-38`, safety bound `:54-58`). A husk that IS the root, or whose parent is live, is never touched. |
| mission-live guard | The member-alive leg keys on **non-terminal task rows**; `attempt_at is None` ⇒ NOT held ⇒ "no dateable evidence of an in-flight turn" (`mission_live_guard.py:507-510`, `:640-671`). The guard therefore never *blocks* on a husk — but it also never *cleans* it. Its strand exclusion only excludes ghosts under TERMINAL parents (`:790-830`). |
| Zombie reaper | SQL demands `last_activity_at IS NOT NULL` (`repository.py:3451`, `:3488`). A husk's NULL stamp is skipped forever. |
| Boot orphan sweep | Selection is `RUNNING` task-less rows only (`auto_continue_boot_pass.py:655-663`), and it runs ONLY at boot. |
| `stale_pause_sweep` | Target class is PAUSED rows (release is question-marker-only). Uses `repo.get_statuses` (`repository.py:795-808`) to test `waiting_children`; no idle branch. |
| `orphan_watcher_sweep` | Operates on the dependency-bus watcher table — it never reads `instances` (`orphan_watcher_sweep.py:3-11`). |
| `instance_health` — `stale-running` | `status != "running" → continue` (`instance_read.py:1080`). |
| `instance_health` — `orphans` | `if not parent_id: continue` (`:1246-1247`) ⇒ a **root husk** is invisible; live parent ⇒ `parent_status in ALIVE_INSTANCE_STATUSES → continue` (`:1249-1250`) ⇒ a **husk under a live parent** is invisible. Only a husk under a *terminal/missing* parent is flagged — and nothing repairs it. |
| `instance_health` — the other four | Keyed on `waiting_children`, question pauses, `pending` jobs, or `failed/error` (`:1112`, `:1171`, `:1224`, `:1274`). |
| Census / capacity (F2) | `_liveness_for("idle")` = `_STATUS_CANONICAL_MAP["idle"] = "processing"` (`daemon/services/work_status.py:87`, map `:67-109`), consumed by `counts_by_liveness` (`instance_read.py:1311-1318`). A never-dispatched husk is bucketed as **"processing" — "work is happening"** — the exact F2 census-idle-bucket claim. |
| `_ghost_child_filter` (parent completion gate) | Matches the husk signature but is used only to *exclude* ghosts from an active-children count (`child_reports.py:2875-2901`), never to retire them. |

**Class conclusion (evidence-backed):** the husk class is a **NEW shape** — every existing healer requires at least one of (a) a task row, (b) a message row, (c) a `waiting_children`/`running` root posture, (d) a terminal reviewer parent, or (e) a non-NULL activity stamp. A row with *none* of these is invisible to all seven components simultaneously. There is no predicate in the tree that keys on "spawned, never dispatched, aged".

---

## A.3 The two mechanisms named in the project history

**"Guard-side member-alive companion leg"** — `daemon/services/mission_live_guard.py:519-708`.
- Shipped **UNCONDITIONAL and guard-side** (not opt-in), so every consumer inherits it (`:104-121`): *"the leg lives HERE, in the guard — the shared chokepoint — so EVERY consumer inherits it (the pre-stamp gate, the emission gate, the running-zombie healer's tree leg, f1, f2, the boot sweep, the orphan release, the work notifier)."*
- Time-bounded by the guard's own `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` (RULING-1b, `:123-131`).
- Holds on: young dateable task row (`:672-696`), `has_live_work` probe error (`:601-617`), task-age probe error (`:623-639`).
- Known residuals, stated in-code: a member whose only live work is a **JobItem** row is NOT held (`:558-560`); the coarse `has_live_work` probe is blind to `processing` (`:553-556`, `repository.py:1883`).

**"Pre-stamp-hold re-arm"** — `daemon/services/child_reports.py:2773-2790` (the hold) + `_schedule_strand_heal` `:2340-2447`.
- Before: the hold relied on "the next message-completed signal", which nothing schedules on a stranded tree.
- Now: the hold hands the root to the healer immediately, coalesced per root, and the posture selects the lane (`:2429-2441`).

---

## A.4 Spawn-without-dispatch cleanup — DEFINITIVE ANSWER: **it does not exist**

Searches run (this worktree):
- `never_dispatched` (all files, incl. `*.py`) → the predicate + its three consumers + docs only.
- `InstanceStatus.IDLE` across `daemon/services/**/*.py` → exactly 5 hits: `child_reports.py:2897` (ghost filter), `instance_lifecycle.py:5606` (spawn INSERT), `:5741` (a payload field), `instance_messaging.py:2376`/`:2381` (idle→running flip). **Not one is a sweep selection.**
- `sweep_orphaned_live_turns` → `daemon/api.py:2630`/`:2637` (boot wiring) + its definition `auto_continue_boot_pass.py:651`.
- No periodic service selects `status == 'idle'`.

**`filter_never_dispatched_ids` exists but is a classifier, not a cleaner** — `daemon/repositories/instance/repository.py:735-793`:

```python
stmt = (select(Instance.instance_id)
        .where(Instance.instance_id.in_(instance_ids))
        .where(Instance.status == InstanceStatus.IDLE.value,
               Instance.version == 1,
               no_messages, no_tasks))
```

Its three consumers:
1. `daemon/services/instance_lifecycle.py:3627-3641, 3702` — pause cascade **skips** such nodes (documented reason: pausing them "sets up the wedged-tree class", `:751-756`).
2. `daemon/services/mission_live_guard.py:896-925` — excludes them from liveness, **only** under a terminal direct parent.
3. `daemon/services/mission_strand_healer.py:635` — the WC lane's orphan signature, again gated by posture + terminal parent + age.

The canonical SQL twin is `ChildReportsService._ghost_child_filter` (`child_reports.py:2888-2901`) — used only to **not count** ghosts (`:2877-2883`), never to terminate them.

Also relevant: `daemon/services/attestation_scanner.py:527` mentions "``spawn_instance`` before/without a corresponding…" in a **read-only** attestation context — not a cleanup.

**Verdict:** There is **no** component anywhere that ages off, retires, or even lists a never-dispatched instance under a LIVE parent or a never-dispatched ROOT. The pause cascade actively avoids touching them; the guard excludes them from liveness; the WC healer can only reach them through a terminal parent and a `waiting_children` root. A husk is immortal by construction.

**Second-order hole (seam):** the predicate demands `version == 1` (`repository.py:786`). `Instance.version` is a `version_id_col` that auto-increments on ANY committed UPDATE (`instance_lifecycle.py:5595-5597` explains the insert-time `version=1`). A husk that received any write (e.g. a metadata marker, a rolled-back-then-retried enqueue) has `version >= 2` and escapes even the classifier.

---

## A.5 Spawn row creation, and where a first dispatch would be observable

**Spawn writes the row here** — `daemon/services/instance_lifecycle.py:5595-5612`:

```python
new_instance = Instance(
    instance_id=instance_id, project_id=project_id,
    agent_id=resolved_agent_id, agent_tag=version_tag,
    agent_dir=resolved_agent_dir, agent_name=agent_name,
    parent_id=parent_id,
    status=InstanceStatus.IDLE.value,
    instance_metadata=effective_metadata,
    version=1,
    created_at=now_iso,
    updated_at=now_iso,
)
session.add(new_instance)
```

then the hierarchy row if `parent_id` (`:5614-5622`), commit + refresh (`:5624-5633`). `manager.spawn_instance` (`daemon/manager.py:9226-9286`) is a thin delegate to `self._lifecycle_service.spawn_instance`.

**No `last_activity_at` is written at spawn.** `daemon/repositories/instance/models.py:72`: `last_activity_at: datetime | None = Field(default=None)`. Only the enqueue path writes it.

**First dispatch = four observable artifacts, same transaction** (`daemon/services/instance_messaging.py`):
1. `Task` row mint — `session.add(task)` `:2330-2335`
2. `message_queue` row (the enqueue that produced the task) — same block
3. status flip `IDLE → RUNNING` — `:2375-2381` (`if instance.status in (IDLE, WAITING_CHILDREN) or is_terminal_revival: instance.status = RUNNING`)
4. `instance.last_activity_at = now_utc_naive()` — `:2453-2455`

**Therefore:** "first dispatch landed" ⟺ `message_queue` row exists ∧ `task` row exists ∧ `last_activity_at IS NOT NULL` (∧ `version >= 2`). A husk exhibits **none** of the three; `created_at` is its only timestamp. A detector for the class should key on exactly that absence set — and must NOT key on `last_activity_at` age (NULL rows have no age), which is why `created_at` is the only usable age column.

---

## A.6 F-numbered defect seams touched by this part

| Defect (commission numbering) | Seam |
|---|---|
| F3 — husk-blind health scan | `daemon/tools/instance_read.py:1055-1300` (six detectors; add a 7th, or widen `orphans` at `:1246-1250`); doc `:903-940` |
| F2 — census idle bucket | `daemon/services/work_status.py:87` (`"idle": "processing"`) + `instance_read.py:1311-1318` (`counts_by_liveness`) + `_full_doc_` at `:554-601` (documents `liveness` as "a RETURNED FIELD") |
| F11 — unreachable protected-target delegation gate | `daemon/tools/instance_ops.py:326-385` (`_protected_gate` four conditions), verdict source `_provenance_for` `:242-258` → `daemon/tools/provenance.restart_provenance`, call site `:557-580`. Condition 4 is `if verdict.pclass != "inherited"` (`:369`). The delegated-forge residual is **pinned as truth** in `tests/unit/test_instance_ops_behaviour.py:10-15` (AC-23b — "DO NOT 'fix' this test to assert a refusal"). DF-4 (`fix/df4-provenance-gate @ 9da51b2e1`) is an in-flight protected lane per the shared meta KV — do not collide. |

---

# PART B — TEST HARNESS + PIN CONVENTIONS

## B.1 Existing nest-ops tests: location and shape

| File | Shape |
|---|---|
| `tests/unit/test_instance_ops_registration.py` | **Static / wiring pin.** `READ_CATEGORY="instance_read"`, `ACT_CATEGORY="instance_ops"`, `READ_MODULE`, `ACT_MODULE` (`:53-56`); source-scan assertions that `create_instance_read_tools(` / `create_instance_ops_tools(` are imported and called in order (`:135-150`); provenance-import pin (`:222-245`); kill-switch env pin `io.KILL_SWITCH_ENV == "ENSEMBLE_INSTANCE_OPS_ENABLED"` (`:336-357`); category-set membership `:91-92`; `instance_read.py` source pin `:365`. |
| `tests/unit/test_instance_ops_behaviour.py` (881 lines) | **Behavioural, FAKE manager + SCRATCH SQLite audit ledger.** Header maps AC ids → assertions (`:1-29`) incl. AC-16 "the doc surface states CURRENT semantics" (`:22`) and AC-23b pinned residual (`:10-15`). SAFETY FENCE: "the audit ledger runs on a `tempfile` SQLite database — no HOME-derived or live-shaped path is ever resolved" (`:27-28`). Fixtures: `FakeRow` dataclass (`:53-85`, `last_activity_at` default None), `FakeRepo` with `get` / `set_metadata` / `list_child_ids_permanent` / `get_tree_ids_permanent` / `get_max_last_activity_in_instances` (`:88-120`). Factory call `io.create_instance_ops_tools(...)` (`:197`). Real audit repo on scratch engine (`InstanceOpsAudit`, `InstanceOpsAuditRepository`, `:44-45`). |
| `tests/unit/test_instance_ops_dismiss_service.py` | Service-level tests of the extracted `daemon/services/instance_ops_service.py::dismiss_question_pack` (header `:3-8`; exception classes `DismissWritePaused` / `DismissInstanceNotFound` / `DismissNoPendingQuestion` / `DismissPackNotPending` at `:357-447`). |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | Allow-resolution pin: asserts the maintenancer's resolved tool set includes `instance_read` + `instance_ops` (`:125`, `:140-141`, `:403-404`; rationale comment `:310-311`). |
| `tests/integration/test_service_tool_flag_off_byte_identical.py:883-884` | Byte-identical flag-off pin (the "no behavior change when the flag is off" family). |
| Privileged-set count pins (the "SAME-PR" family) | `tests/unit/test_board_registration.py:82-83`, `tests/unit/test_self_alarm_registration.py:88-89`, `tests/unit/tools/test_attestation_registration.py:239-240`, `tests/unit/tools/test_secrets_registration.py:268`. `_tool_registry.py:301-315` names `test_instance_ops_registration.py` as the nest-ops pin and states the count assertion is **14 today**, with an explicit instruction to grep the tree rather than trust the count. |

**No nest-ops test pack exists.** `grep -rn "instance_ops|instance_read|nest.?ops" test/packs/*.sh` → **zero matches**. The nearest analog pack is `test/packs/instance_tools_unit_test.sh` (the LEGACY `instance`-category tool surface, 207 tests).

## B.2 Real-PG test setup — the pattern to copy

**Helper (canonical disposable DB)** — `tests/helpers/checkpoint_prune_pg.py`:

```python
async def create_disposable_db() -> tuple[str, str]:          # :203
    name = f"ensemble_blob_prune_{uuid.uuid4().hex[:12]}"      # :207
    conn = await asyncpg.connect(ADMIN_DSN)                    # :208
    await conn.execute(f'CREATE DATABASE "{name}"')            # :210
    dsn = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{name}"   # :213
    return name, dsn

async def drop_database(name: str) -> None:                    # :217
    ... f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'       # :222-224
    # fallback for older PG: pg_terminate_backend(pid) ... DROP DATABASE   :227-232
```

plus `require_postgres()` / `require_postgres_async()` loud-skip probes (`:170-200`) and the context-manager fixture `real_pg_checkpointer(dbname, dsn, *, pool=False)` (`:239-259`, mirrors `daemon/persistence.py::create_postgres_checkpointer`).

**Working integration example (full lifecycle)** — `tests/integration/test_secrets_v1_basic.py:95-204`: maintenance engine in AUTOCOMMIT (`:95`), "probe maintenance connection → CREATE DATABASE → … → terminate stray backends → DROP DATABASE. Skips loudly" (`:148-151`, `CREATE DATABASE "{scratch_db}"` `:170`, `DROP DATABASE IF EXISTS "{scratch_db}"` `:198`). Same shape in `tests/integration/test_secrets_v1_endtoend.py:115-204` and `tests/migration/test_secrets_audit_context_parity.py:125-265`.

**Schema-on-scratch-PG pattern** — `tests/test_prompt_approval_service.py:165-207`: `CREATE DATABASE` → `SQLModel.metadata.create_all(engine)` (PG truth) → DROP; skips loudly with `pytest.skip(f"PG CREATE DATABASE failed: {exc}")` (`:180`).

**House rules that apply** (repo conventions, confirmed by the above files): local PG16 on `127.0.0.1:5432`, throwaway LOGIN role + `createdb -O`, `DROP` at cleanup; scrub ambient `POSTGRES_*` **per variable** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT …`) — the `env -u POSTGRES_*` glob idiom is a silent no-op; see `tests/postgres/test_symptom_repair_ladder_pg.py:11` for the canonical unset list and the project critical note on the idiom.

## B.3 The fresh-SQLite boot blocker — why the plan must mandate real-PG-only

**File:** `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` (45 lines).

UP block, `:33-37`:

```sql
ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type;
ALTER TABLE job_queues ADD CONSTRAINT ck_job_queues_queue_type
  CHECK (queue_type IN ('fifo', 'parallel', 'defer', 'background'));
```

Header claim `:24-27` says SQLite supports `ALTER TABLE ... DROP CONSTRAINT` since 3.35.0. It does **not** (SQLite's ALTER TABLE supports RENAME/ADD COLUMN/DROP COLUMN — never DROP CONSTRAINT), so a fresh SQLite apply raises a syntax error and the lifespan boot fails. The project record states this as a standing defect: *"migration 20260714_000001 breaks EVERY fresh-SQLite boot … runner is SQLite-NO-OP so PG never hits it"*.

**Why PG is unaffected:** `daemon/migrations/runner.py:815-840` —

```
# PostgreSQL is the DEFAULT dialect from v0.5.2+. The runner is
# intentionally a NO-OP for non-SQLite engines:
if "sqlite" not in str(self.engine.url):
    ... "Skipping migrations for non-SQLite database ..."
```

**Why it cannot self-skip:** `MigrationFile.parse` (`runner.py:92-159`) sets
- `manual_only = bool(re.search(r"--\s*MANUAL:\s*TRUE", content, re.IGNORECASE))` → `:133` — this file has **no** such marker;
- `precondition = ... r"--\s*PRECONDITION:\s*([A-Za-z]+>=[\d.]+)"` → `:144-147` — this file has **no** such token.

The N4 escape hatch exists and is documented (`:135-147`; evaluation `:469-508`, `sqlite_version()` probe `:502`, fail records a skip-ledger row instead of failing boot `:605-615`), but this migration does not use it. The documented workaround is ledger-pre-marking it (a `skip_reason` row).

**Consequence for the patch:** any e2e/integration proof MUST run on a **real PG scratch DB** (B.2); a fresh SQLite boot is pre-blocked independent of this change. (Separately, the duplicate-prefix class is now loud-guarded — `runner.py:41-44` `DuplicateMigrationVersionError` precedent.)

## B.4 PIN conventions

**Content-sha256 tripwire (the primary pin idiom)** — `tests/test_chart_image_delivery_e2e.py`:

- Dict `SEALED_SHA_BASELINES: dict[str, str]` `:1412-1476` — 35 rows of `rel_path → sha256(file bytes)`.
- Check loop `:1496-1528`:
  ```python
  current_sha = hashlib.sha256(abs_path.read_bytes()).hexdigest()
  if current_sha != baseline_sha:
      mismatches.append((rel_path, baseline_sha, current_sha))
  ...
  pytest.fail("Phase D modified sealed Phase A/B/C artifacts:\n" + ...)
  ```
- Re-pin **ceremony in comment form** — the convention is: state the date, the authorizing decision, the recomputation source ("recomputed from real file bytes"), the exact delta, and "only this ONE row changed; the other 34 keep their values". Precedents: `:1402-1411` (ari user ruling), `:1438-1447` (running-zombie `NON_TERMINAL_TASK_STATUSES` → `daemon/constants.py` re-pin with the identical sha at staged tree and origin/latest), `:1424-1429` (O4 re-pin).
- `agents/maintenancer/rule.md` **is sealed** — `:1467` (`"agents/maintenancer/rule.md": "abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec"`). `agents/maintenancer/tools_note.md` is **NOT** in the 35-row set.

**Prompt-artifact baseline (the --check authority)** — `scripts/protection/generate_prompt_artifact_baseline.py`:
- Header `:1-28`: pins `agents/.prompt-artifacts.sha256`, single-sourced from `daemon/tools/prompt_artifacts.py::VERSIONED_PROMPT_ARTIFACTS` (`:4-7`, `:48-58`), wired at stage time (read-only, journaled) and at daemon boot (`api.py` lifespan WARN) (`:8-12`).
- Usage/exit contract `:18-27`: `--check --tree <dir>`; **exit 0 = clean or baseline absent, 1 = drift**; output is `sha256sum -c` compatible.
- Precedent test: `tests/test_prompt_artifact_baseline.py:11`, `:38` (`GENERATOR = REPO_ROOT / "scripts" / "protection" / "generate_prompt_artifact_baseline.py"`).

**Category / count pins + SAME-PR rule** — `daemon/tools/_tool_registry.py:296-315`:
- named pin files: `tests/unit/test_board_registration.py`, `tests/unit/test_self_alarm_registration.py`, `tests/unit/test_instance_ops_registration.py`;
- "*ALSO carry a `len(PRIVILEGED_TOOL_CATEGORIES) == N` count assertion (14 today). Derive the authoritative pin set by GREPPING THE TREE — do NOT trust this count*";
- the SAME-PR RULE (restated at `:434-437`, `:456-459`, `:478-481`, `:499-501`, `:520-523`): a category entry ships in the SAME commit as its pin insertions — "*the set must never be red between two commits*".

**What makes a pin "mutation-verified" (house practice):** plant the exact regression (old text / old behavior), confirm the pin goes RED **exactly as predicted**, revert, confirm GREEN. Recent precedents: the `TestDocSurfaceWakeTheCaller` 6-node doc-surface pin (planted old doc text → 2 RED exactly as predicted → restore → GREEN), and the running-zombie gate's mutation cycles (`tests/...::Test...` 6/6 "mutation meaningful"). Regression pins MUST be mutation-verified (repo discipline: "regression pins MUST be mutation-verified").

## B.5 House testing contract for net-new-failure claims

The rule as written in-tree:

- **Method statement** — `.agents/shared/planning/designer-od-lane-fix/gate-surface-scope.md:8`:
  > "The G2b no-regression method is a symmetric A/B: run the **identical pytest command** on both sides of the diff (base vs HEAD), collect the sorted FAILED test-id lists, and diff them with `comm`: `comm -13 base-sorted head-sorted` = **net_new** … `comm -23` = **fixed** … Set-theory note: **tests green at the comparison base can never appear in `fixed`**"
  Invocation shape `:28`: `timeout 300 .venv/bin/python -m pytest <chunk files> -q --tb=short`, per-state (base, then HEAD), sorted FAILED ids, then `comm`.
- **Regression plan** — `.agents/tester/RESULTS/2026-10-21-hrr-p4-regression-plan.md:7` ("Method (net-new-failure discipline)"), `:17` ("A 'zero net-new' claim is only valid when the FAILED-list diff was computed over the **whole** [scope]"), `:77` ("The scrub must be IDENTICAL on both legs — that is part of 'identical commands'").
- **Executed-leg standard** — `.agents/tester/LESSONS/2026-10-19-agent-restart-builder-stage-lessons.md:6`: both legs executed from a detached base worktree, `uv sync --frozen`, interpreter byte-matched, "*diffed failing SETS, not counts*".
- **Quarantine ledger** — `.agents/tester/QUARANTINE.md:10` (consolidated base-parity rows, "EXECUTED legs, not derived").
- **ensure.md** — `.agents/tester/rules/ensure.md` (53 lines): pack-mapped (`:4`), no bare `pytest tests/` (`:6`), quarantine-aware (`:7`), **no `-x`** (`:8`), dual-layer 5-min timeout (`:16-20`), release-gate prerequisites incl. `PYTEST_TIMEOUT=280 --override-ini="timeout=280"` (`:36-41`).

The repo-level summary of the same contract: `fixed = BASE_FAILED − FIX_FAILED` is structurally empty for tests green at base; the gate is **net_new = ∅ AND previously-red-now-green**, proven separately; probe-order/load-sensitive reds need 3× isolated runs per side.

## B.6 Acceptance test-pack convention (what a new pack must contain)

Anatomy from `test/packs/instance_tools_unit_test.sh` (56 lines, the nearest analog):

1. `#!/usr/bin/env bash`
2. Header block: pack name, **scope** (exact pytest file(s) + test count), **branch pin** + **base** (`:5-11`), `TEST-ENV ONLY. No production code changes, no daemon boot, no ports.` (`:13`), dual-layer timeout policy (`:16-20`), exit-code contract `0 PASS / 1 FAIL / 124 TIMEOUT` (`:22-25`)
3. `set -euo pipefail` + `IFS=$'\n\t'` (`:26-27`)
4. `SCRIPT_DIR` / `PROJECT_DIR` derivation and `cd` (`:30-33`)
5. Layer-2 cap: `timeout 210s .venv/bin/pytest <explicit files> --tb=short -q --override-ini="addopts=" --timeout=210 -p no:cacheprovider` (`:38-43`) — note the **explicit file list** (never `pytest tests/`) and the `addopts` override (which deselects the `-m 'not integration…'` default)
6. `RC` mapping to `RESULT: TIMEOUT|PASS|FAIL` + exit (`:44-56`)

Registration: add the row to `.agents/tester/PACKS.md` (keep-both on conflict, newest-first) — per `ensure.md:4` ("requirements reference packs in PACKS.md").

---

# PART C — DOC SURFACES

## C.1 How tool docstrings reach the agent-visible surface

**Mechanism (three layers):**

1. The `@tool`-decorated function carries a ONE-LINE docstring that points at `tool_help`. Examples:
   - `daemon/tools/instance_read.py:620` — `"""Deep detail for ONE instance (hydration). Use tool_help("instance_get") for details."""`
   - `:869` — `"""Wedge report — six read-only detectors. Use tool_help("instance_health") for details."""`
   - `:949` — `"""How's the nest — counts, queue depth, pool, DLQ. Use tool_help("fleet_capacity_summary") for details."""`
2. After the decorator, the full text is attached as an attribute:
   ```python
   instance_list._full_doc_ = f"""Fleet census — one cheap row per instance.
   ...
   Side effects:
       NONE. Read-only; no audit row.
   Examples:
       instance_list(status="running", limit=100)          # live fleet
   """                                                     # :554-601
   ```
   Others: `instance_get._full_doc_` `:816`, `instance_health._full_doc_` `:903-940`, `fleet_capacity_summary._full_doc_` `:960-987`.
3. Registration on scan: `scan_tools_for_full_docs(tools)` (`daemon/tools/_tool_registry.py:818-869`):
   ```python
   if hasattr(tool_func, '_full_doc_'):
       register_full_doc(tool_name, tool_func._full_doc_)      # :831-832
   short_doc = tool_func.description.split('\n')[0]  ...      # :836-839
   category = getattr(tool_func, '_tool_category', None)      # :842-844
   ```
   `tool_help` then resolves it (`daemon/tools/help.py:146` "Scan tools for _full_doc_ attributes"; `get_full_doc` `_tool_registry.py:605-614`).

**`args_schema` layer** — every tool gets a pydantic input model with `Field(description=...)`, so per-argument semantics are agent-visible independently of the doc string. Examples: `InstanceGetInput` (`instance_read.py:604-615`), `FleetCapacitySummaryInput` (`:943-947`), stacked decorators `@register_tool_category("instance_read")` + `@tool(args_schema=…)` (`:462-463`, `:617-618`, `:866-867`, `:946-947`; `instance_ops.py:495-496`, `:893-894`, `:1046-1047`, `:1197-1198`, `:1445-1446`, `:1600-1601`).

**instance_ops `_full_doc_` inventory (the act surface):** `instance_terminate` `:798`, `instance_reclaim` `:991`, `instance_nudge` `:1139`, `instance_mark` `:1368`, `instance_answer` `:1552`, `instance_dismiss` `:1731`. `instance_terminate._full_doc_` begins *"Terminate an instance — the DESTRUCTIVE act (audited, gated)."*

**Seam for F1 (alias/docs) and F8 (behavior doc):** edit the `_full_doc_` string literals + `Field(description=…)` text in `daemon/tools/instance_read.py` / `daemon/tools/instance_ops.py`; the surface needs no other wiring. An existing test already asserts doc currency: `tests/unit/test_instance_ops_behaviour.py:22` — *"AC-16 — the doc surface states CURRENT semantics"* — extend that test rather than inventing a new harness.

## C.2 The tool DOC/CATEGORY registry

`daemon/tools/_tool_registry.py`:
- `register_tool_category(category)` `:529-554` — sets `func._tool_category` and `func._tool_category_first_party = True` (`:545-552`); the provenance marker exists so a spoofed MCP tool cannot re-categorize a first-party tool (`:546-551`, honored in the scan at `:855-870`).
- `register_tool(tool_name, category, short_doc, full_doc)` `:557-590` → writes `_tool_metadata[tool_name]` and `_full_docs[tool_name]`.
- `register_full_doc` `:593-602`; `get_full_doc` `:605-614`; `get_tool_metadata` `:617-626`; `list_tools` `:629+`; `clear_registry` `:812-815`.
- `PRIVILEGED_TOOL_CATEGORIES` `:316-526` — 14 entries. The nest-ops pair is the last two:
  ```python
      # nest-ops (2026-10-21, D-13): TWO single-owner categories for the
      # maintenancer — ``instance_read`` (#13: the four fleet reads) and
      # ``instance_ops`` (#14: the six audited acts). ...
      "instance_read",
      "instance_ops",
  ```
  (`:503-525`; rationale: act surface destructive, read surface fleet-wide telemetry, structural read/act separation, per-tool allow REJECTED by D-13.)
- Enforcement pair referenced in-code: `_strip_privileged_category_tools` (`daemon/tools/instance.py:6169`) and `resolve_tool_filter`'s empty-allow branch (`daemon/tools/instance.py:373`) — see `:375-378`.
- Factory entry points: `create_instance_read_tools` (`instance_read.py:397`, returns the 4 tools at `:989-994`), `create_instance_ops_tools` (`instance_ops.py:398`).
- In-body authorization: `_is_instance_read_authorized(caller_agent_id)` (`instance_read.py:621`, `:950`); instance_ops kill switch `KILL_SWITCH_ENV == "ENSEMBLE_INSTANCE_OPS_ENABLED"` (`_resolve_instance_ops_enabled`, pinned at `tests/unit/test_instance_ops_registration.py:336-357`).

## C.3 The agent prompt surface for the tool's owner

`agents/maintenancer/` contains: `README.md`, `ROLLOUT.md`, `growth.md`, `knowledge/`, `meta.json`, `rule.md`, `skill-set.yaml`, `skills-template/`, `soul.md`, `tools_note.md`, `workflow.md`.

**Only two files mention the fleet tools** (grep of `agents/maintenancer/**/*.md` for `instance_read|instance_ops|fleet_capacity|instance_health|instance_list|instance_get`):

- `agents/maintenancer/tools_note.md:15-16`:
  ```
  | `instance_read` | Fleet census + health — list/get/health/summary reads over every instance in the daemon |
  | `instance_ops` | Gated fleet surgery — the six audited acts: terminate/reclaim/nudge/mark/answer/dismiss (the reads live in `instance_read` above) |
  ```
  and the dedicated section `:26-59` — `## `instance_read` + `instance_ops` — the fleet surfaces`, whose sub-paragraphs are: reads always available (`:30-32`), acts audited (`:34-36`), "The destructive act previews first. `dry_run` defaults to on …" (`:38-42`), "A protected target needs a named confirmation and a genuine user turn above me … `confirmed_target_id` AND the turn must carry a user-origin provenance inherited from a delegation chain. A direct turn on my own session is refused on purpose — the request has to come down a chain from the user-facing turn." (`:44-50`), "I never act on my own session." (`:52`), kill switch (`:54-56`), "The wedge report never repairs." (`:58-59`).
  **This is the F1 (alias) + F9 (copy) target.** Note `:49-50` currently describes the protected-target provenance as reachable via a delegation chain — the fact set F11 disputes.
- `agents/maintenancer/ROLLOUT.md:51` (the allow-list assertion / regression check):
  ```bash
  test "$(jq -r '.tools.allow | join(",")' agents/maintenancer/meta.json)" = "system-log,ens-db,knowledge,system_upgrade,system_restart,db,instance_read,instance_ops"
  ```
  (i.e. the exact allow set is: `system-log, ens-db, knowledge, system_upgrade, system_restart, db, instance_read, instance_ops`.)
- `rule.md`, `soul.md`, `workflow.md`: **zero** matching lines — no fleet-tool prose there today.
- Deny-list table `tools_note.md:18-22` (`git_commit`, `edit_file`, `write_file` → dispatch to `coder`).

**Pin exposure for doc edits:**
- `agents/maintenancer/rule.md` is sealed (sha in `tests/test_chart_image_delivery_e2e.py:1467`) — editing it forces a re-pin with the attribution comment.
- `agents/maintenancer/tools_note.md` is not in that 35-row dict, so it does not trip the sealed-sha tripwire; it *may* be in `VERSIONED_PROMPT_ARTIFACTS` (check `daemon/tools/prompt_artifacts.py`) — if so, re-stamp `agents/.prompt-artifacts.sha256` in the same commit (`scripts/protection/generate_prompt_artifact_baseline.py`).

## C.4 Doc/runbook convention for this subsystem

**Where this subsystem's docs live today:**
- `docs/runbooks/` — 15 files; **none** covers instance_ops / nest-ops / fleet / capacity (listing: checkpoint-blob-prune-restore, hard-reboot-recovery, install-dir-protection-layer1-arm, live-view-passcode-deployment, live-views, maintenance-console, oauth2-auth, post-restart-arm-notify, prod-channel-versions-evidence, resource-cost-control, resource-hygiene-operator-checklist, systemd-adoption, upgrade-drills, upgrade-overhaul-watch, v0.19.0-dev-demo-e2e-gate).
- `docs/architecture/` — 10 files, none on instance ops (completion-authority, concurrency-model, execution-gate-threading-model, instance-lifecycle, job-as-front-primitive-invariants, job-as-queue-proxy-invariants, job-task-pause-resume, message-processing-and-correlation, message-queue-problems, message-queue-redesign, unified-dispatch-architecture).
- **Canonical home = `.agents/shared/planning/nest-ops/`** — 5 files: `plan-overview.md` (1113 lines), `decisions.md` (860 lines), `implementation-plan.md`, `risk-register.md`, `architecture-recommendation.md`.

**House layout of a design/decision doc (from those files):**

`plan-overview.md`
- Header block `:1-11`: `# <slug> — <one-line scope>`, then `**Commission:**`, `**Base:** origin/latest @ <sha>`, `**Branch:**`, `**Status:**` (incl. amendment history), `**Doc set:**` (file list).
- `## 0. Ground-truth corrections` — a table with columns `# | Correction | Evidence | Effect on this doc` (`:20-26`), each row citing exact file:line and naming the premise it refutes (GT-1…GT-5). This is the precedent for recording a **verified absence** as a ground-truth row.
- Then numbered sections (§2.10, §2.11, §6.3-§6.6, §7.2 …).

`decisions.md`
- Header `:1-9` ("Every decision states: **what**, **why** (with evidence), and **what it forecloses**").
- One `## D-n — <title>` block per decision, each with `**What.**` / `**Why.**` / `**Forecloses.**` (e.g. `:13-33` D-1, `:36-88` D-2).
- Deviations are stated plainly, not buried: `:80-83` — *"**Deviation, stated plainly.** The commission brief said 'reuse existing tables'. This decision deviates from that line specifically."*

`design-decision.md` (sibling precedent — `.agents/shared/planning/running-zombie-fix/design-decision.md`), section skeleton:
`## 1. The incident shape` (`:10`) + premise corrections (`:24`) + provenance (`:64`) + epoch semantics (`:84`) → `## 2. Adjudicated decisions` (`:95`) with per-cycle subsections (2.8, 2.9, 2.10 …) → `## 3. Known residuals (documented, not closed)` (`:816`) → `## 4. Files touched` (`:897`) → `## 5. Evidence` (`:936`) → `## 6. Sibling-strand observations noticed while in there (NOT fixed here)` (`:1077`) → `## 7. Sequencing` (`:1132`) → `## 8. ADMIN RECOVERY RUNBOOK` (`:1138`) → `## 9. Sibling strands observed (NOT fixed on this branch)` (`:1208`) → `## 10. PROMOTE-DISCLOSURE MATERIAL` (`:1274`).

**ADR convention:** there is **no** `docs/adr/` directory (docs/ and docs/architecture/ listings contain no ADR files). ADRs are numbered rulings recorded inside the owning commission's `decisions.md` and cited from other docs:
- `ADR-005` home = `.agents/shared/planning/auto-restart-upgrade/decisions.md` (quoted at `promotion-ladder.md:31`);
- `.agents/shared/planning/self-restart-upgrade-phase2/promotion-ladder.md` cites ADR-004/005/007/012/017/018/020/021/025 across `:18-96`;
- `docs/runbooks/upgrade-drills.md:174`, `:515`, `:569` and `docs/self-upgrade-live-rung-parked-decision.md:21`, `:29` cite ADR-009 / ADR-017;
- `docs/incidents/2026-09-28-promote-cgroup-teardown.md:29` cites ADR-009.

So a doc that says "ADR-n" must point at a `decisions.md` block in the owning planning dir, not a global registry.

## C.5 Where the three class-level findings must live

House precedents for "class-level finding" records:
- **`design-decision.md` §1 + §2.x + §6/§9`** (running-zombie-fix) — the class statement, the adjudicated legs, and a "sibling strands observed, NOT fixed here" section.
- **`decisions.md` D-n blocks** (nest-ops) — what / why / forecloses, deviations stated plainly.
- **`plan-overview.md` §0 GT-n table** — for a *verified absence* ("there is nothing to compose" is literally GT-4, `plan-overview.md:25`).
- **`docs/incidents/<date>-<slug>.md`** — timeline + ADR refs (`:29` pattern).
- **`.agents/tester/RESULTS/<date>-<slug>.md`** — executed evidence.
- **RAG experience + project critical notes** — the cross-agent durable surface (the "frozen `last_activity_at`" / "6h backstop" family is recorded there). Note the critical-note tier is capped: an eviction occurred ~10h before this research ("evicted 3beb8c16 … to admit the new F1 stage-isolation convention"), and the evicted note's text is preserved in project history — so a new class note competes for a capped budget.

Specific recommended homes:

**(a) "the husk class is a NEW shape the existing healers cannot match by construction"**
→ `.agents/shared/planning/nest-ops-shakedown-defects/` as a `design-decision.md` §1 (class shape) + §2 (predicate) + a `decisions.md` D-n — mirroring `running-zombie-fix/design-decision.md:10-…` and `nest-ops/decisions.md` D-block format. Evidence column must cite the early exits in A.2 above (they are the "by construction" proof). Cross-record as a RAG experience for retrieval.

**(b) "spawn-side attribution is unrecoverable once log rotation passes it"**
→ same `design-decision.md` `## 5. Evidence` section (the running-zombie doc uses §5 for exactly this: what the logs/DB proved and what is inference), plus a `docs/incidents/` record if it is incident-shaped. Cite the log-rotation boundary explicitly (the running-zombie precedent leans on "455 hold lines across rotated logs" — the rotation boundary is where attribution dies).

**(c) "spawn-without-dispatch cleanup: exists or needed"**
→ `plan-overview.md` §0 as a GT row (verified absence) **and** a `decisions.md` D-n whose **Forecloses** names the alternatives (e.g. "forecloses relying on the boot sweep / reaper / guard to cover this class"). The GT-4 precedent (`plan-overview.md:25`) is the exact shape: "*The wedge sweeps expose NO reusable read-only predicates … There is nothing to compose.*"

---

# APPENDIX — one-line seam index for the patch

| Need | Seam |
|---|---|
| New husk detector (read-only) | `daemon/tools/instance_read.py:1055-1300` (+ `_full_doc_` `:903-940`) |
| Census bucket correction (F2) | `daemon/services/work_status.py:87` **or** a separate `counts_by_status`-derived bucket in `instance_read.py:1309-1320` (do NOT fork the canonical map silently) |
| New sweep / cleanup (write side) | model on `MissionStrandHealer` + register in `daemon/services/pool_orchestrator.py` (see `:348`, `:375` for the existing service wiring pattern) |
| Husk ghost predicate reuse | `daemon/repositories/instance/repository.py:735-793` (add an `allow_version_gt_1` / live-parent variant rather than editing the pinned twin) + `child_reports.py:2888-2901` (semantic-equivalence test exists: `tests/unit/test_pause_never_dispatched_ghosts.py`) |
| Protected-target gate (F11) | `daemon/tools/instance_ops.py:326-385`, `:557-580`; provenance `:242-258` |
| Doc text (F1/F8/F9) | `_full_doc_` strings in `instance_read.py` / `instance_ops.py`; `agents/maintenancer/tools_note.md:15-16, 26-59` |
| New tests | `tests/unit/` (fake-manager + scratch-engine pattern from `test_instance_ops_behaviour.py:53-120`) + a NEW `test/packs/nest_ops_*_unit_test.sh` (anatomy from `instance_tools_unit_test.sh`) + a `PACKS.md` row |
| Real-PG proof | `tests/helpers/checkpoint_prune_pg.py:203-236` (create/drop) + `tests/integration/test_secrets_v1_basic.py:148-204` (lifecycle) |
| Pins | content-sha256 dict `tests/test_chart_image_delivery_e2e.py:1412-1476` + loop `:1496-1528`; count pins `test_instance_ops_registration.py`; prompt baseline `scripts/protection/generate_prompt_artifact_baseline.py --check` |
