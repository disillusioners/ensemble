# Revive-wedge terminalization — sweep/healer machinery map

**Scope:** read-only code research for a rider that (a) makes the revive-wedge class SELECTABLE and (b) STOPS the pointless drive (or converts it to a cure).
**Worktree:** `/home/nea/ensemble-src-wt-revive-wedge` (branch `fix/revive-wedge-terminalization`, HEAD `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` == `origin/latest`).
**NOT read:** `/home/nea/ensemble-src` (detached HEAD >1000 commits behind `origin/latest` — stale).
**No files modified except this one.**

---

## 0. TL;DR of the defect mechanism (verified in code)

The revive-wedge child is stuck because of the **per-child PREFIX mint-site guard** in `daemon/services/child_reports.py`:

```python
4677:                        existing_report = session.exec(
4678:                            select(MessageQueue)
4679:                            .where(
4680:                                MessageQueue.source.startswith(
4681:                                    f"internal_report:{instance_id}:",
4682:                                    autoescape=True,
4683:                                )
4684:                            )
4685:                            .where(MessageQueue.status.in_([
4686:                                MessageStatus.READY.value,
4687:                                MessageStatus.PROCESSING.value,
4688:                                MessageStatus.COMPLETED.value,
4689:                            ]))
4690:                        ).first()
4691:                        if existing_report is not None:
4692:                            should_send = False
4693:                            skip_reason = "idempotency_skip"
4694:                        else:
4695:                            should_send = True
4696:                            skip_reason = "all_checks_passed"
4697:
4698:            if not should_send:
4699:                logger.info(f"Instance {instance_id[:8]}... completion report skipped: reason={skip_reason}")
4700:                return _ChildCompletionDbResult(
4701:                    outcome="idempotency_skip",
```

* The guard is **CHILD-KEYED and LIFETIME**: any parent-side row whose `source` starts with `internal_report:{child}:` (READY / PROCESSING / COMPLETED) suppresses the mint. The `:` terminator keeps the exact boundary (a sibling id that *extends* the child's id does not match — `internal_report:{child_id}2:...`).
* The **terminal stamp sits DOWNSTREAM of that guard**. For a non-root child the stamp is the atomic CAS at `child_reports.py:4901-4917` (`status=COMPLETED`, `status NOT IN (paused, completed, error)`) — reached only when `should_send` is True. So a child that **already delivered a report** and is then **revived** (terminal→RUNNING, `instance_messaging.py:2952`) and re-completes can NEVER terminalize: the chain returns `idempotency_skip` before the stamp.
* Consequence: the instance row stays `status='running'` forever, with every task/message row terminal → the Shape-B stale-running sweep selects it (after the 7200 s age bound), drives the SAME chain, gets `idempotency_skip`, and logs `class=drive-no-effect`. The observed live log line `completion report skipped: reason=idempotency…` is `child_reports.py:4699` — the exact proof that the drive re-enters the suppressed chain.

---

## Q1 — `stale_running_sweep.py` full structure

File: `daemon/services/stale_running_sweep.py` (1336 lines).

### 1.1 Knobs / env reads

```python
169: KILL_SWITCH_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP"
170: INTERVAL_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_INTERVAL_SECONDS"
171: AGE_BOUND_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_AGE_BOUND_SECONDS"
172: BATCH_CAP_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_BATCH_CAP"
174: DEFAULT_INTERVAL_SECONDS = 300
181: DEFAULT_AGE_BOUND_SECONDS = 7200
184: DEFAULT_BATCH_CAP = 25
190: _MAX_TREE_PROBE_SIZE = 256
195: _TERMINAL_MESSAGE_STATUSES = frozenset({"completed", "failed"})
200: _NEVER_TOUCHED_STATUSES = frozenset({"waiting_children", "paused"})
237: TERMINAL_REASON_METADATA_KEY = "terminal_transition_reason"
240: TERMINAL_REASON_VALUE = "finalize_gap_steady_state"
```

* Kill switch (`258-263`): default ON; `=0/false/no/off` disables → `sweep_once` returns `mode="off"` with the log `"[STALE_RUNNING_SWEEP] OFF: %s=0 — lane skipped"` (`:753-756`).
* `_env_int` (`:244-255`) — non-numeric env falls back to the default with a WARNING `[STALE_RUNNING_SWEEP] non-numeric %s=%r — using default %d`.
* Cap / age bound read per tick at `:768-769`: `cap = max(1, _env_int(BATCH_CAP_ENV, DEFAULT_BATCH_CAP))`, `age_bound = max(1, _env_int(AGE_BOUND_ENV, DEFAULT_AGE_BOUND_SECONDS))`.

### 1.2 Immediate-first-tick-at-boot wiring

```python
474:    async def _run(self) -> None:
475:        # First tick IMMEDIATE (house fast-on-boot catch-up).
476:        while not self._stopping:
477:            try:
478:                await self.sweep_once()
...
487:                await asyncio.sleep(self._interval_seconds)
```

Started from the FastAPI lifespan (`daemon/api.py:884 async def lifespan(app)`):

```python
2416:    stale_running_sweep = StaleRunningSweepService(
2417:        manager,
2418:        interval_seconds=stale_running_sweep_interval,
2419:    )
2420:    stale_running_sweep.start()
2421:    app.state.stale_running_sweep = stale_running_sweep
2422:    manager._stale_running_sweep = stale_running_sweep
```

Shutdown at `daemon/api.py:5335-5346`.

### 1.3 Candidate selection (SQL anchor + Python legs)

Selection SQL builder: `_candidate_selection_statement()` (`:278-400`). Projected evidence aliases + the anchored predicates:

```sql
322:                (SELECT t2.completed_at FROM task t2
323:                 WHERE t2.instance_id = i.instance_id
324:                   AND t2.status = 'completed'
325:                   AND t2.completed_at IS NOT NULL
326:                 ORDER BY t2.completed_at DESC, t2.id DESC LIMIT 1
327:                ) AS task_completed_at,
328:                (SELECT m2.completed_at FROM message_queue m2
329:                 WHERE m2.instance_id = i.instance_id
330:                   AND m2.status = 'completed'
331:                 ORDER BY m2.completed_at DESC LIMIT 1
332:                ) AS message_completed_at,
...
337:            WHERE i.status = 'running'
338:              AND NOT EXISTS (          -- leg 2: no non-terminal task
339:                  SELECT 1 FROM task t
340:                  WHERE t.instance_id = i.instance_id
341:                    AND t.status IN ({non_terminal_tasks})
342:              )
343:              AND NOT EXISTS (          -- leg 3: no non-proven-terminal message
344:                  SELECT 1 FROM message_queue mq
345:                  WHERE mq.instance_id = i.instance_id
346:                    AND mq.status NOT IN ({terminal_msgs})
347:              )
348:              AND (                    -- leg 8: POSITIVE completion evidence
349:                  EXISTS (
350:                      SELECT 1 FROM task t3
351:                      WHERE t3.instance_id = i.instance_id
352:                        AND t3.status = 'completed'
353:                        AND t3.completed_at IS NOT NULL
354:                  )
355:                  OR EXISTS (
356:                      SELECT 1 FROM message_queue m3
357:                      WHERE m3.instance_id = i.instance_id
358:                        AND m3.status = 'completed'
359:                  )
360:              )
```

* Ordering = **newest completion evidence** (portable two-column CASE, `:391-397`), `LIMIT :cap` (`:398`).
* `NON_TERMINAL_TASK_STATUSES` comes from `daemon/constants.py` (`:158` import).
* Read-only: `_select_candidates` (`:515-537`) opens a raw connection and returns dicts.

**Age keys (Q2 cross-link):** the age leg keys on the **completion evidence** — `task.completed_at` and `message_queue.completed_at` — **never** on `instances.last_activity_at` (which is frozen, see Q2):

```python
1076:        # ── Leg 8/9: the age bound on the NEWEST completion evidence ──
1077:        now_naive = await asyncio.to_thread(self._now_naive)
1078:        stamps = [
1079:            candidate.get("task_completed_at"),
1080:            candidate.get("message_completed_at"),
1081:        ]
1082:        ages = [
1083:            self._age_seconds(stamp, now_naive)
1084:            for stamp in stamps
1085:            if stamp is not None
1086:        ]
...
1096:        newest_age = min(ages)  # the NEWEST evidence = the smallest age
1097:        if newest_age < age_bound:
1098:            result.skipped_young += 1
1099:            logger.info(
1100:                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=young "
1101:                "detail=newest completion evidence is %ds old (< bound %ds)",
1102:                str(instance_id)[:8], int(newest_age), age_bound,
1103:            )
1104:            return
```

The **young skip** therefore clears once the newest completion stamp passes 7200 s — this is exactly why the revive-wedge victim becomes selectable at ~2 h even though `last_activity_at` is frozen at the revive stamp.

**Python legs** (`_process_candidate`, `:823-1107`), all all-must-hold and **fail-closed**:

| Leg | Check | Code | Skip counter / log class |
|---|---|---|---|
| 1 | fresh re-read; `row.status != "running"` → never-touched set | `:837-868` | `skipped_posture_changed` / `class=posture-changed` |
| 2 | no non-terminal task row (`_count_non_terminal_tasks`) | `:870-892` | `skipped_non_terminal_task` / `class=non-terminal-task` |
| 3 | no message outside `{completed, failed}` (`_holding_message`, three-valued sentinel `NO_HOLDING_MESSAGE`, `:203-232`, `:550-578`) | `:894-917` | `skipped_holding_message` / `class=holding-message` |
| 4 | no open report injection (PENDING/DEFERRED, `_count_open_report_injections`) | `:919-941` | `skipped_open_injection` / `class=open-report-injection` |
| 5 | no fulfillable-pending bus watcher (`_bus_pending`) | `:943-961` | `skipped_bus_pending` / `class=bus-pending` |
| 6 | tree enumeration + closure check (`_tree_ids` `:616-625`, `_tree_is_closed` `:627-654`, bound 256) | `:963-991` | `skipped_tree_truncated` / `class=tree-truncated` |
| 6b | mission-live guard over the tree, `skip_root=True` (`_guard_verdict` `:656-675`) | `:993-1023` | `skipped_live_tree` / `class=live-tree`, `skipped_member_live_task` / `class=member-live-task` |
| 7 | no pending question anywhere in the tree (RAM packs / metadata shadow / `awaiting_answer`; `_pending_question_in_tree` `:677-743`) | `:1025-1074` | `skipped_pending_question` / `class=pending-question` |
| 8/9 | age bound on newest completion evidence | `:1076-1104` | `skipped_young` / `class=young` |

Per-candidate isolation via an `asyncio.Lock` per instance (`_acquire_lock` `:506-511`); sweep-level + loop-level isolation (`:776-803`, `:479-485`).

### 1.4 The drive call — what it invokes and what it does step by step

```python
1173:    async def _drive(
1174:        self,
1175:        instance_id: str,
1176:        candidate: dict[str, Any],
1177:        result: SweepResult,
1178:    ) -> None:
1179:        """Drive the production completion chain (the healer's seam).
1180:
1181:        NEVER a raw status UPDATE and NEVER ``terminate_instance``: the
1182:        chain re-runs every gate authoritatively and writes the terminal
1183:        row itself (the guarded status flip IS the exactly-once CAS —
1184:        the N3 rationale).
1185:        """
...
1198:        latest_msg_id = await asyncio.to_thread(
1199:            self._latest_completed_message_id, instance_id,
1200:        )
1201:        try:
1202:            # C5 PIN — ``completed_message_id`` MUST stay the EMPTY
1203:            # STRING, never None (the pending-messages gate filters
1204:            # ``message_id != completed_message_id``; in SQL
1205:            # ``!= NULL`` is never true, which would silently disable
1206:            # the gate).
1207:            await child_reports._process_child_completion_and_notify_parent(
1208:                instance_id, latest_msg_id or "", skip_repair=True,
1209:            )
```

Step-by-step inside `ChildReportsService._process_child_completion_and_notify_parent` (`daemon/services/child_reports.py:3283`):

1. `:3316-3319` — read the instance row (`self._instance_repository.get`) → `agent_id`.
2. `:3320-3325` — **`_get_last_assistant_message(instance_id, agent_id, skip_repair=True)`** → this is the **checkpoint read**: `child_reports.py:2076-2077`
   ```python
   2076:        if self._checkpointer:
   2077:            messages = await get_instance_messages(self._checkpointer, instance_id)
   ```
   `daemon/persistence.py:627 get_instance_messages` → `state = await time_saver_op("aget", instance_id, saver.aget(config))` (`:703`) — ONE full checkpoint read of the thread's latest state (all messages) + the `message_metadata` side-table lookup. **This is the 73-message / 82 KB read.** It happens **BEFORE** the transaction and therefore **BEFORE** the mint-site guard decides to skip → the wasted work.
3. `:3337-3364` — best-effort `child_outcome` marker lookup (bus).
4. `:3366+` — recursive-tree liveness evaluated **before** the COMPLETED stamp (asymmetry fix).
5. `:3642` `_process_child_completion_db_sync` — the whole DB half inside one `WriteGuardSession` (runs on a worker thread):
   * `:3673-3683` — instance fetch.
   * `:3719-3729` — report-recipient resolution (commission → audit-recency → structural parent).
   * `:3731+` — terminal-state short-circuit (Fix 2 idempotency).
   * `:4579-4696` — pending-message gate + **mint-site guard** (the per-child PREFIX check quoted above) → for the revive-wedge child this returns `outcome="idempotency_skip"` at `:4700-4705` and **never reaches the terminal stamp**.
   * `:4819-4828` — active-children guard; `:4858-4878` — pending-task guard → `outcome="child_still_running_defer"` (no status transition).
   * `:4901-4917` — the non-root **terminal stamp** (guarded CAS):
     ```python
     4901:            update_result = session.execute(
     4902:                sa_update(Instance)
     4903:                .where(Instance.instance_id == instance_id)
     4904:                .where(
     4905:                    Instance.status.notin_([
     4906:                        InstanceStatus.PAUSED.value,
     4907:                        InstanceStatus.COMPLETED.value,
     4908:                        InstanceStatus.ERROR.value,
     4909:                    ])
     4910:                )
     4911:                .values(
     4912:                    status=InstanceStatus.COMPLETED.value,
     4913:                    updated_at=now_iso,
     4914:                    last_activity_at=now_dt,
     4915:                    version=Instance.version + 1,
     4916:                )
     4917:            )
     ```
   * `:4959-4974` — report mint (`source=f"internal_report:{instance.instance_id}:{completed_message_id}"`, `type=COMPLETION_REPORT`, `status=READY`).
   * Root branch (non-root-skip) is at `:4325-4353` (`pending_count>0 → WAITING_CHILDREN`) and `:4355-4375` (root COMPLETED / PAUSED guard).
6. Post-commit side effects (SSE / CompletionRegistry / lifecycle event / bus) are dispatched by the async caller (`:5800+`).

### 1.5 Outcome classification + exact log lines

`SweepResult` counters (`:403-432`): `candidates, healed, gate_downgraded, drive_no_effect, skipped_non_terminal_task, skipped_holding_message, skipped_open_injection, skipped_bus_pending, skipped_live_tree, skipped_member_live_task, skipped_pending_question, skipped_young, skipped_posture_changed, skipped_tree_truncated, skipped_probe_error, skipped_kill_switch, errors, duration_seconds, mode`.

Post-drive classification (`:1222-1264`):

```python
1229:        status = getattr(drove, "status", None)
1230:        if status in ("completed", "error", "terminated", "failed"):
1231:            result.healed += 1
...
1236:            await self._persist_terminal_reason(
1237:                instance_id, TERMINAL_REASON_VALUE,
1238:            )
1239:            logger.warning(
1240:                "stale_running_sweep|shape-b: instance=%s task=%s "
1241:                "message=%s transitioned=%s reason=%s",
...
1247:        if status == "waiting_children":
...
1251:            result.gate_downgraded += 1
1252:            logger.info(
1253:                "[STALE_RUNNING_SWEEP] instance=%s class=gate-downgraded "
1254:                "detail=post-drive status=waiting_children — the emission "
1255:                "gate blocked the publish (legitimate miss; bus-owned)",
...
1259:        result.drive_no_effect += 1
1260:        logger.info(
1261:            "[STALE_RUNNING_SWEEP] instance=%s class=drive-no-effect "
1262:            "detail=post-drive status=%r",
1263:            str(instance_id)[:8], status,
1264:        )
```

**Exact log-line formats (all in this file):**

| Marker / prefix | Line | Level |
|---|---|---|
| `[STALE_RUNNING_SWEEP] OFF: %s=0 — lane skipped` | 753 | INFO |
| `[STALE_RUNNING_SWEEP] non-numeric %s=%r — using default %d` | 251 | WARNING |
| `[STALE_RUNNING_SWEEP] summary: candidates=%d healed=%d downgraded=%d no_effect=%d skips(task=%d msg=%d inj=%d bus=%d tree=%d member_task=%d question=%d young=%d posture=%d truncated=%d probe_err=%d) errors=%d duration_s=%.3f mode=%s` | 807-820 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=posture-changed detail=row gone` | 853 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=posture-changed detail=status=%r (never-touched set: %s)` | 862 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=non-terminal-task detail=%d non-terminal task row(s) — a turn is in flight (legitimate running)` | 886 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=holding-message detail=status=%r is not proven-terminal (conservative: an unknown or NULL status never satisfies 'nothing holding')` | 911 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=open-report-injection detail=%d open PENDING/DEFERRED injection(s)` | 935 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=bus-pending detail=bus_pending=%d` | 956 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=tree-truncated detail=tree size %d exceeds the probe bound %d (PROTECTED)` | 975 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=tree-truncated detail=%s` | 986 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=live-tree\|member-live-task detail=%s` | 1016 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=pending-question detail=an outstanding question in the tree still owns the mission` | 1068 | INFO |
| `[STALE_RUNNING_SWEEP] SKIP instance=%s class=young detail=newest completion evidence is %ds old (< bound %ds)` | 1099 | INFO |
| `[STALE_RUNNING_SWEEP] instance=%s class=drive-no-effect detail=post-drive status=%r` | 1260 | INFO |
| `[STALE_RUNNING_SWEEP] instance=%s class=gate-downgraded detail=post-drive status=waiting_children — …` | 1252 | INFO |
| `stale_running_sweep\|shape-b: instance=%s task=%s message=%s transitioned=%s reason=%s` | 1239 | WARNING |
| `[STALE_RUNNING_SWEEP_DRIVE_ERROR] instance=%s detail=…` | 1191 / 1215 | ERROR |
| `[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=<leg> detail=…` | 845/878/903/927/948/967/996/1005/1035/1046/1060/1090 | ERROR |
| `[STALE_RUNNING_SWEEP_REASON_PERSIST_ERROR] instance=%s detail=…` | 1150 / 1165 | WARNING |
| `[STALE_RUNNING_SWEEP_LOOP_ERROR] %s: %s` | 482 | ERROR |
| `[STALE_RUNNING_SWEEP_DB_ERROR] pass=%s errors=%d %s: %s: %s` | 496 | ERROR |
| `[STALE_RUNNING_SWEEP_CENSUS_ERROR] %s: %s` | 1317 | ERROR |

Read-only census: `census_stale_running()` (`:1279-1321`) — **capped sample, not a population** (docstring `:1282-1304`).

---

## Q2 — The activity clock

### 2.1 `instances.last_activity_at` — every writer in the tree

| Site | Line | What it stamps |
|---|---|---|
| `daemon/services/instance_messaging.py` | 3090-3093 | **the revive / auto-resume to RUNNING**: `instance.last_activity_at = now_utc_naive()` + `version` bump |
| `daemon/manager.py` | 12671-12676 | `_revive_terminal_instance` (terminal→RUNNING): `inst_row.last_activity_at = now_utc_naive()` |
| `daemon/services/child_reports.py` | 1510, 5527 | parent bookkeeping after a report / error report |
| `daemon/services/child_reports.py` | 4457, 4505, 4748, 4914 | the chain's own terminal writes (`values(last_activity_at=now_dt)`) |
| `daemon/services/error_reporting.py` | 245 | parent bookkeeping on the error-report path |
| `daemon/services/job_feedback_observer.py` | 3678, 4556 | observer finalize |

**The revive path DOES stamp `last_activity_at`** — quote (`instance_messaging.py`):

```python
2942:                is_terminal_revival = previous_status in (
2943:                    InstanceStatus.COMPLETED.value,
2944:                    InstanceStatus.TERMINATED.value,
2945:                    InstanceStatus.ERROR.value,
2946:                    InstanceStatus.FAILED.value,
2947:                )
2948:                if instance.status in (
2949:                    InstanceStatus.IDLE.value,
2950:                    InstanceStatus.WAITING_CHILDREN.value,
2951:                ) or is_terminal_revival:
2952:                    instance.status = InstanceStatus.RUNNING.value
...
3090:                # Naive-UTC digits (DC-A fix): last_activity_at is a
3091:                # tz-naive column on PostgreSQL.
3092:                instance.last_activity_at = now_utc_naive()
3093:                instance.version = (instance.version or 1) + 1
```

and `manager.py:12652-12676`:

```python
12652:        # Auto-transition terminal → RUNNING. Mirrors the
12653:        # ``send_message`` revival block at
12654:        # ``instance_messaging.py:1486-1510`` (the
12655:        # ``is_terminal_revival`` branch sets
12656:        # ``instance.status = InstanceStatus.RUNNING.value`` +
12657:        # bumps ``version`` + ``last_activity_at``).
...
12671:                    inst_row.status = InstanceStatus.RUNNING.value
12672:                    # Naive-UTC digits (DC-A fix): last_activity_at
12673:                    # is a tz-naive column on PostgreSQL.
12674:                    inst_row.last_activity_at = now_utc_naive()
12675:                    inst_row.version = (inst_row.version or 1) + 1
```

### 2.2 The "freeze"

There is **no dedicated epoch-start freeze write** for `instances.last_activity_at`. The freeze is a **byproduct of the writer set**: `last_activity_at` is written ONLY on lifecycle events (enqueue/revive, terminal stamps, parent bookkeeping, observer finalize) — **never on turn progress**:

```python
daemon/services/mission_live_guard.py
355:    2026-10-21 (council F1 + RULING-1b): the MEMBER-ALIVE companion
356:    leg. ``last_activity_at`` is NOT written on turn progress, so a
357:    member inside a long turn reads stale and the backstop would break
358:    a tree that is still working.
```

Consequences for a revived child:
* The revive stamps `last_activity_at` **once** (`instance_messaging.py:3092`) and then **nothing** writes it again — the child is frozen at the revive stamp for the rest of its (wedged) life.
* `job_recovery_service.py` treats this as a known property, e.g. `job_recovery_service.py:3389`:
  ```
  # ``last_activity_at`` FREEZES on a
  # waiting_children parent for 16min+ while a
  # grandchild streams mid-LLM.
  ```
  and `job_recovery_service.py:3092-3101` — the f2 backstop anchor is deliberately **tree activity**, *not* per-work `completed_at`:
  ```
  3092:                    the freshest
  3093:                    ``last_activity_at`` across the tree's NON-ROOT
  3094:                    members (NOT the per-work ``completed_at`` — that
  3095:                    was the pre-U7 anchor and the wave-3 misfire
  3096:                    class; and NOT the root's own stamp …)
  ```
* The sweep's own age leg does **not** consult it (it keys on completion evidence) — so the freeze does not block selection; it is the *age key choice* that made the class selectable at 7200 s.

### 2.3 Which timestamps actually advance for a revived child

| Timestamp | Advances? | Where |
|---|---|---|
| `task.completed_at` | **YES** — the revived turn's Task row completes with a stamp | the sweep's `task_completed_at` alias (`:322-327`) |
| `message_queue.completed_at` | **YES** — the revived message row completes | the sweep's `message_completed_at` alias (`:328-332`) |
| `instances.last_activity_at` | once at the revive, then frozen | `instance_messaging.py:3092` / `manager.py:12674` |
| `instances.updated_at` | at the revive (status flip) and on terminal writes | `manager.py:12676`, `child_reports.py:4913` |
| `task.last_heartbeat_at` | only while a turn is RUNNING (worker heartbeat thread) | used by the WC watchdog's liveness gate (`waiting_children_watchdog.py:1624-1648`) |
| `message_queue.last_activity_at` | while the message is PROCESSING | `message_queue/repository.py:1051-1066` |
| `instances.updated_at` (per-turn) | NO — not written on turn progress | `mission_live_guard.py:356` |

---

## Q3 — Terminalization entry points callable WITHOUT the LLM parent

### 3.1 `child_reports.py` landing ladder (the production chain)

Function: `ChildReportsService._process_child_completion_and_notify_parent` (`:3283`) → DB half `_process_child_completion_db_sync` (`:3642`).

* **Preconditions**: instance row exists (`:3673`); not already terminal (Fix-2 short-circuit `:3731+`); no pending PROCESSING/RETRYING own-queue message other than the anchor (`:4592-4601`); report recipient resolvable (`:3719-3729`); the mint-site guard passes (`:4677-4696`).
* **Ladder (non-root child)**:
  * `active_children > 0` (`:4819-4828`) or `pending_tasks > 0` (`:4858`) → `outcome="child_still_running_defer"` (`:4873-4878`) — **no status transition, no report**.
  * else → atomic CAS to `COMPLETED` (`:4901-4917`), then report mint (`:4959-4974`) and the parent cascade.
  * Root branch: `pending_count > 0` → `WAITING_CHILDREN` (`:4325-4353`); else root `COMPLETED` (`:4355+`).
* **Invariants**:
  * *Idempotency / no double-terminalize*: the terminal write is a **guarded CAS** (`UPDATE … WHERE status NOT IN (paused, completed, error)`); `rowcount == 0` → `idempotency_skip` (`:4918-4936`).
  * *One obligation per child*: the mint-site guard is the per-child PREFIX (`:4677-4696`) — the **only** thing that keeps the parent from receiving two reports for one child.
  * *Mirror reconciliation*: this chain writes `instances.status` directly; the **task-side** mirror reconciliation is `TaskRepository.reconcile_turn_mirror(work_id)` (`daemon/repositories/task/repository.py:2919`), the SOLE reconciliation path over `ALL_8_MIRRORS` (`daemon/services/turn_transitions.py:26-29`:
    ```python
    26: ALL_8_MIRRORS: frozenset[str] = frozenset({
    27:     "task", "job_queue_items", "message_queue", "job_locks",
    28:     "dependency_watchers", "report_injections", "instances", "job_watchers",
    29: })
    ```
    ). The chain does not call it; the observer's finalize does.
* **Appropriateness for a revive-wedge CHILD victim**: **NO** — the chain is structurally blocked for this child. The per-child prefix guard fires (`idempotency_skip`, `:4691-4705`) *before* the terminal stamp, so driving it can only ever return "no effect". The chain is the right seam for *minting* a report, not for *re-terminalizing* an already-reported child.

### 3.2 `terminate_instance(terminal_reason=...)` — `daemon/services/instance_lifecycle.py:2648`

```python
2648:    async def terminate_instance(
2649:        self,
2650:        instance_id: str,
2651:        terminal_reason: str = "aborted",
2652:        audit_sink: "Callable[[Session], None] | None" = None,
2653:    ) -> bool:
```

* **Preconditions**: enumerate-first snapshot of the whole permanent lineage (`:2704-2746`); per-node re-entrancy guard over `TERMINAL_STATUSES`.
* **DB half** `_terminate_instance_db_sync` (`:5401-5468`): single `WriteGuardSession`; `UPDATE instances SET status='terminated'` (`:5418-5420`); cancel PROCESSING/PENDING/FAILED JobItems (`:5421-5429`); `DELETE job_locks` (`:5430`); **`DELETE message_queue WHERE instance_id=:id` (`:5431`)**; `DELETE task WHERE instance_id=:id` (`:5432`); `DELETE instance_hierarchy` parent links (`:5443-5447`); COMMIT (`:5448`).
* **Idempotency**: `if instance.status in TERMINAL_STATUSES: … skip=True` (`:5486-5500`) — "the authoritative guard for terminate re-entrancy".
* **Semantics**: stamps `terminated` (not `completed`), `terminal_reason="aborted"`-style. The child's own `message_queue`/`task` rows are **deleted** — the child's completion evidence (its own task row with `completed_at`) disappears, which destroys the sweep's own selection evidence and the audit trail of the wedge.
* **Appropriateness for a revive-wedge child**: **POOR**. It is a *cascade-abort* semantic (the child did NOT abort — its work completed and was reported), it deletes the child's evidence rows, it does not reconcile the 8 mirrors, and it fires parent-facing side effects (SSE / lifecycle event / bus) that would re-notify a parent that already received the report. It is the right tool for "retire a never-dispatched orphan" (`orphan_retired`), not for "this child finished its work".

### 3.3 Sweep-side precedents that land/terminalize directly

**(a) `boot_consistency_reconciler.py` — the guarded terminalizer for a NON-ROOT** (the closest precedent):

```python
daemon/services/boot_consistency_reconciler.py
 46: * ``completed_task`` ROOT (``parent_id IS NULL``) → DEFER to
 47:   ``mission_strand_healer`` (skip + ``deferred_to_healer`` log). No double
 48:   disposition.
 49: * ``completed_task`` NON-ROOT → finalize the instance to COMPLETED via the
 50:   guarded terminalizer pattern (no resume).
```

```python
455:                        finalized = await asyncio.to_thread(
456:                            task_repo.finalize_boot_consistency_instance,
457:                            instance_id,
458:                            candidate.get("version"),
459:                        )
460:                        if not finalized:
461:                            result.skipped_disposition_refused += 1
462:                            logger.info(
463:                                "[BOOT_RECOVERY] SKIPPED finalize instance=%s: "
464:                                "CAS declined (concurrent terminalizer won)",
```

```python
daemon/repositories/task/repository.py
2195:    def finalize_boot_consistency_instance(
2196:        self, instance_id: str, expected_version: int | None
2197:    ) -> bool:
2198:        """Finalize-gap non-root: flip a RUNNING instance to COMPLETED.
2199:
2200:        Guarded CAS on ``status='running'`` (+ ``version`` when supplied)
2201:        so a concurrent terminalizer wins cleanly. Terminal-status writer
2202:        used by the strand-healer precedent. Returns ``True`` iff one row
2203:        flipped.
2204:        """
2205:        sql = (
2206:            "UPDATE instances SET status = 'completed', "
2207:            "updated_at = :updated_at, version = version + 1 "
2208:            "WHERE instance_id = :iid AND status = 'running'"
2209:        )
```

* Preconditions: selected as a boot-consistency candidate; the CAS itself is the guard (`status='running'` + optional `version`); the lane is **BOOT-ONLY** (`boot_epoch` frame, `boot_consistency_reconciler.py:23-25`: "**boot_epoch only.** No ``datetime.now`` in this module").
* Invariants: exactly-once via CAS rowcount (`return result.rowcount == 1`); **no mirror reconciliation**; no report mint; no parent cascade; marks the instance with `stamp_boot_re_drive`.
* **Appropriateness for the revive-wedge child**: **HIGH** — this is exactly the "land a non-root child that has terminal completion evidence" primitive, minus the boot-only frame. It needs NO LLM, NO parent, NO report mint. Reused as a steady-state sweep disposition it becomes the natural **cure** branch.

**(b) `mission_strand_healer.py` — orphan retirement + chain drive**

```python
714:            for orphan in orphans:
715:                ok = await lifecycle.terminate_instance(
716:                    orphan.instance_id,
717:                    terminal_reason="orphan_retired",
718:                )
...
758:        latest_msg_id = await asyncio.to_thread(
759:            self._latest_completed_message_id, root_id
760:        )
...
772:        await child_reports._process_child_completion_and_notify_parent(
773:            root_id, latest_msg_id or "", skip_repair=True,
774:        )
```

The healer uses **the same chain** (`:772`, and `:1316` for the running-zombie strand) — so it is subject to the same per-child prefix guard.

**(c) `job_recovery_service.py` orphan-active finalize (pattern f, f1/f2)**

* f1/f2 stamp `orphaned_no_task` on the JobItem/Task (see `daemon/repositories/task/repository.py:2971`, `:3067-3081`, `:3359`; `daemon/services/work_status.py:187-201`).
* The instance side retires via `terminate_instance(terminal_reason="orphan_retired")` (see `daemon/services/mission_strand_healer.py:715-718`; `daemon/services/auto_continue_boot_pass.py:1384`).
* Anchors on **live-tree activity** (not the frozen `last_activity_at` of a root) — `job_recovery_service.py:3092-3101`, `:3381-3401`.

**(d) `job_feedback_observer._finalize_instance` (`:3145`)** — the other terminalizer; **event-driven** (fires from the report/message Task publish, or the JobItem linkage). Idempotent no-op when the instance is already terminal (`:3180-3183`); it also updates `last_activity_at`/`version` (`:3186-3187`) and fires SSE / CompletionRegistry / lifecycle event.

---

## Q4 — Why the other lanes cannot cure a revive-wedge child victim

**`mission_strand_healer.py` — root-only + same blocked chain.** Both of its strands are anchored on a ROOT (`parent_id IS NULL`): the WC strand at `mission_strand_healer.py:28` (`root.status == WAITING_CHILDREN (parent_id IS NULL) [the wedge posture]`) and the running-zombie strand at `:86` (`root.status == RUNNING (parent_id IS NULL) [the zombie`). Its running-zombie candidate set is a `JOIN task` on the root's latest terminal-COMPLETED task (`:88`, `:1056`: "completed_at — NOT ``instances.last_activity_at``, which is [frozen]"), so a non-root child victim is invisible to it; and even if it selected one, its cure is the **same chain** (`:772`, `:1316`) that the per-child prefix guard blocks → `idempotency_skip` → no terminal stamp. The module's own docstring states it drives the root "through the EXISTING emission machinery" and never `terminate_instance`s the root (`:108-111`).

**`waiting_children_watchdog.py` — notify-only + wrong status.** It enumerates **only** instances in `WAITING_CHILDREN` (`waiting_children_watchdog.py:17`: "Enumerates instances whose status is ``WAITING_CHILDREN``"; `:614`: "Detect parents stuck in ``WAITING_CHILDREN`` and nudge them"; repo helper `list_waiting_children_parents`), and it skips WC *children* (`:27`). A revive-wedge victim sits in `running`, so it is never enumerated; and even if it were, the lane's disposition is a **nudge notice** (`:305 _build_hang_notice`, `:369 _build_escalation_notice`, `:424 _build_release_notice`, `:486 _build_wedge_notice`), not a terminalization. Its liveness gate is `task.last_heartbeat_at` (`:1624-1648`), which is frozen once the turn ends.

**`report_delivery_recovery.py` (RDRS) — re-arms a *missing* report; the wedge child's report is NOT missing.** The lane's no-row backstop is `find_completed_children_without_delivery` (`daemon/repositories/report_injection/repository.py:1245-1360`), and its exclusion is exactly the wedge child's shape:
```
1276:        The corrected semantics (delivery evidence is PER CHILD,
1277:        never per anchor; one obligation per child):
1279:        * A child is excluded when ANY parent-side
1280:          ``internal_report:{child}:%`` message row exists (prefix
1281:          match — any anchor, any status). This recognizes the
1282:          terminal report as already delivered (or in flight)
1283:          regardless of which anchor the delivering path used.
```
So the revive-wedge child (report **already delivered**) is *excluded* by the RDRS selector — the lane exists to deliver a report that never arrived, and the wedge child's problem is the opposite (the report arrived, the terminal stamp did not). RDRS's own header also documents the prohibition on direct calls to the chain (`:63-119`).

**The boot reconciler — boot-only.** `daemon/services/boot_consistency_reconciler.py:23-25`: "**boot_epoch only.** No ``datetime.now`` in this module — the epoch is the sole time frame"; the lane runs once at boot (`daemon/api.py` lifespan) and its selector is gated on the per-epoch CAS `(boot_re_drive_at IS NULL OR boot_re_drive_at < :boot_epoch)` (`daemon/repositories/task/repository.py:1987-1988`). In the steady state it never runs → no cure between boots. (`auto_continue_boot_pass.py` — same boot-only frame, `boot_epoch=None` → skip, `:568-572`.)

**`job_feedback_observer._finalize_job` / `_finalize_instance` — event-driven.** It fires from the report/message Task publish or the JobItem linkage; the revive-wedge child's completion event already fired (that is how the report got minted), and no new event arrives for the wedged instance → the observer never re-enters.

---

## Q5 — Drive cost, short-circuit points, and a selection-time discriminator

### 5.1 What the drive reads

| Step | Function | Cost |
|---|---|---|
| 1 | `_latest_completed_message_id` (`stale_running_sweep.py:1266-1275`) | 1 indexed SELECT on `message_queue` |
| 2 | `_get_last_assistant_message` → `get_instance_messages(self._checkpointer, instance_id)` (`child_reports.py:2076-2077`) | **1 full checkpoint `aget`** (`daemon/persistence.py:703`) → the 73-message / 82 KB blob + the `message_metadata` side-table lookup |
| 3 | `_process_child_completion_db_sync` (`:3642`) | 1 `WriteGuardSession` transaction: instance fetch, report-recipient resolution (audit + commission reads), pending-message count, **the per-child PREFIX mint-site query**, and (for the wedge) an early `idempotency_skip` return |
| 4 | post-drive status re-read (`stale_running_sweep.py:1222-1229`) | 1 SELECT |

The **waste**: the expensive step (2, the checkpoint read) happens **before** the cheap decisive step (3, the mint-site guard). Measured: 2–7 s per drive, tick duration 3.653 s → 4.954 s → 13.265 s as candidates rose 5 → 7.

### 5.2 Where a short-circuit / cure can be inserted (minimal change)

**Insertion point A — pre-flight discriminator in the sweep's `_drive` (before the chain call).**
`stale_running_sweep.py:1198` currently reads the latest completed message id and then calls the chain. A one-query pre-flight can decide "the report obligation for this child is already DISCHARGED ⇒ the chain can only return `idempotency_skip` ⇒ do not pay the checkpoint read". The query already exists as a **repository helper**:

```python
daemon/repositories/message_queue/repository.py
277:    def find_wake_already_delivered_evidence(
278:        self, parent_id: str, child_id: str
279:    ) -> bool:
280:        """Queue-side PREFIX ledger check — operative cross-path dedup.
...
284:        ``instance_id = :parent_id AND source LIKE
285:        'internal_report:{child_id}:%' AND status IN
286:        ('ready', 'processing', 'completed')``.
...
311:        with Session(self.engine) as session:
312:            stmt = (
313:                select(MessageQueue.message_id)
314:                .where(MessageQueue.instance_id == parent_id)
315:                .where(
316:                    MessageQueue.source.like(
317:                        f"internal_report:{child_id}:%"
318:                    )
319:                )
320:                .where(
321:                    MessageQueue.status.in_([
322:                        MessageStatus.READY.value,
323:                        MessageStatus.PROCESSING.value,
324:                        MessageStatus.COMPLETED.value,
325:                    ])
326:                )
327:                .limit(1)
328:            )
329:            row = session.exec(stmt).first()
330:            return row is not None
```

It is **indexed on `instance_id`** (`message_queue/models.py:61 instance_id: str = Field(index=True)`), so the scan is over the *parent's* rows only — cheap. (`source` itself is **not** indexed: `models.py:64 source: str | None = Field(default=None)`.)

**Insertion point B — convert the drive into a cure.**
Instead of "skip the drive", the sweep can land the child with the **existing guarded terminalizer** (§3.3a):

```python
daemon/repositories/task/repository.py:2195 finalize_boot_consistency_instance(instance_id, expected_version)
→ "UPDATE instances SET status = 'completed', … WHERE instance_id = :iid AND status = 'running'"
```

(plus the sweep's own provenance stamp — `repo.set_metadata(instance_id, TERMINAL_REASON_METADATA_KEY, reason)`, `stale_running_sweep.py:1156-1162`). The same CAS shape used by the chain itself (`child_reports.py:4901-4917`) guarantees exactly-once.

**Insertion point C — fix the chain itself (guard scope).**
The root cause is that the *mint* guard and the *terminal stamp* are fused. Splitting them — "the report obligation is discharged (per-child PREFIX) BUT the instance is still `running` ⇒ stamp it terminal WITHOUT minting a second report" — is a change inside `child_reports.py` at `:4698-4705`: instead of returning `idempotency_skip` with no state change, return a distinct outcome (e.g. `already_reported_terminalize`) and let the caller perform the guarded terminal CAS. This preserves the **one-obligation-per-child** invariant (no second report is minted) while unblocking the terminal stamp.

### 5.3 Is there an existing selection-time discriminator for the wedge class?

**YES — three available, all already present in the codebase:**

1. **`internal_report` marker exists for the child** (the "report already delivered" evidence):
   * in SQL: `EXISTS (SELECT 1 FROM message_queue r WHERE r.instance_id = <recipient/parent> AND r.source LIKE 'internal_report:' || i.instance_id || ':%' AND r.status IN ('ready','processing','completed'))` — the RDRS exclusion predicate (`report_injection/repository.py:1301`, `:1351`), the mint-site guard (`child_reports.py:4677-4690`), and the repository helper (`message_queue/repository.py:277`).
   * Note the **colon boundary** rule (a sibling id extending the child's id must not alias): `child_reports.py:4941-4958` and the regression pin `tests/repositories/test_report_injection_prefix_boundary.py`.
2. **The instance is `running`** — already the sweep's anchor (`stale_running_sweep.py:337`).
3. **Its task/message evidence is terminal** — already the sweep's legs 2/3/8 (`status='completed'` task row with `completed_at`, or a `completed` message row).

So the wedge class is expressible as a **selection-time predicate**: *running ∧ no non-terminal task ∧ no holding message ∧ terminal completion evidence ∧ an `internal_report:{child}:%` row already exists*. The last conjunct is the missing piece — it is what distinguishes "the chain can still mint" from "the chain is structurally blocked".

A subtlety for the rider: the sweep's candidate set is currently built **without** any knowledge of the parent, and the report row's `instance_id` is the **recipient** (parent or commissioner), not the child. So the discriminator must either (i) join on `source LIKE 'internal_report:' || i.instance_id || ':%'` across `message_queue` (unindexed on `source` — a scan), or (ii) use the per-parent helper form (`instance_id = parent_id AND source LIKE …`, indexed by `instance_id`) once the parent id is known (`candidate["parent_id"]` is **already projected** by the selection SQL — `stale_running_sweep.py:316 parent_id`). **Option (ii) is the cheap one and needs no new SQL shape.**

---

## Q6 — Change surface

### 6.1 Rider variant A — "selectable + stop the pointless drive" (sweep-side only, no chain change)

**Source:**
1. `daemon/services/stale_running_sweep.py`
   * `_candidate_selection_statement()` (`:278-400`) — optionally project the parent's `parent_id` (already projected) and add the "already reported" flag.
   * `_process_candidate` (`:823-1107`) — new leg / new branch before `_drive` (`:1107`).
   * `_drive` (`:1173-1264`) — pre-flight + (optional) cure branch + a new outcome class (e.g. `skipped_already_reported` / `cured_already_reported`) and the matching log line; add the counter to `SweepResult` (`:403-432`) and to the summary format string (`:807-820`).
2. `daemon/repositories/task/repository.py` — **optional**: reuse `finalize_boot_consistency_instance` (`:2195`) for the cure; or add a sibling CAS that also stamps `last_activity_at`/`metadata`.
3. `daemon/repositories/instance/repository.py` — `set_metadata` already exists (used by `_persist_terminal_reason`, `stale_running_sweep.py:1156-1162`) → **no change** unless the reason vocabulary is extended (then `TERMINAL_REASON_VALUE` at `:240`).
4. `daemon/api.py` — **no change** (the service is already started at `:2416-2420`); only if a new env knob is introduced (then `daemon/api.py:2402-2414` coercion pattern + the knob constant in the sweep module).

**Tests:**
5. `tests/unit/services/test_stale_running_sweep_pins.py` — add a class for the new class (e.g. `TestReviveWedgeClass`): an already-reported running child is (a) selected or (b) short-circuited, and the drive is not issued.
6. `tests/unit/services/test_stale_running_sweep_rider_pins.py` — the existing `TestNit1MintSitePrefixGuard` pins must stay GREEN (the mint-site prefix guard must keep blocking the **second mint**).
7. `tests/unit/services/test_stale_running_sweep_pg_selection_pins.py` — if the selection SQL changes, the PG-executed pin (`test_the_production_selection_sql_executes_on_postgres`, `:207`) must still execute the real production SQL on PG.

### 6.2 Rider variant B — "fix the chain" (guard/stamp split in `child_reports.py`)

**Source:**
1. `daemon/services/child_reports.py` — split the mint guard from the terminal stamp at `:4698-4705` (new outcome branch + the guarded CAS at `:4901-4917` reused for the "already reported" case); the post-commit dispatcher (`:5800+`) needs the new outcome; `_ChildCompletionDbResult` (`:185`) may need a new outcome value.
2. `daemon/services/stale_running_sweep.py` — the `drive_no_effect` classification at `:1259-1264` may become `healed` for this class (the sweep would then report `healed` instead of `no_effect`).

**Tests:**
3. `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestNit1MintSitePrefixGuard` (`:411-513`) — the "second mint is blocked" pin stays; **add** a pin that the *same* shape now terminalizes the child.
4. `tests/job_queue/test_fp4_premature_terminal_pins.py` (multiple `_process_child_completion_and_notify_parent` call sites, e.g. `:543`, `:723`, `:822`, `:932`, `:960`, `:1068`) and `tests/job_queue/test_round2_council_fixes.py` (`:454`, `:522`, `:564`, `:612`, `:659`, `:741`) and `tests/job_queue/test_job_result_summary_and_gate.py` (`:466`, `:506`, `:526`, `:553`, `:617`, `:746`) — these exercise the chain's terminal-stamp paths and would catch a regression in the split.
5. `tests/repositories/test_report_injection_prefix_boundary.py` — the sibling-prefix boundary pin (must stay GREEN).

### 6.3 Is SQL touched?

**NO — for both variants.** Evidence:

* The cure reuses an **existing** guarded CAS on existing columns (`UPDATE instances SET status='completed', updated_at, version … WHERE instance_id AND status='running'`) — `daemon/repositories/task/repository.py:2205-2219`.
* The discriminator reuses **existing** columns (`message_queue.source`, `message_queue.status`, `instances.status`, `instances.metadata`) — no new column, no new table, no new index.
* The provenance stamp reuses `instances.metadata` (`set_metadata`, `stale_running_sweep.py:1156-1162`).
* The house rule (Core Architecture blueprint) is "**Every new SQLModel table requires its own `*.sql` file** in `daemon/migrations/versions/`" — a rider that adds **no** table/column touches **no** migration. If a rider *did* introduce a per-episode marker column on `instances`, it would then need: `daemon/migrations/versions/<date>_0000NN_add_<col>_to_instances.sql` (UP `ALTER TABLE instances ADD COLUMN …;` / DOWN `ALTER TABLE instances DROP COLUMN …;` — precedent `daemon/migrations/versions/20260516_000002_add_paused_at_to_instances.sql:8,15`, `20260412_000003_enhance_instance_for_worker_pool.sql:13-22`) **plus** the SQLModel field in `daemon/repositories/instance/models.py:72`. **Recommended: avoid that — no column is needed.**
* Governance note: `daemon/job_state/constitution.py` governs **admission-state** writers (`KNOWN_ADMISSION_STATE_WRITERS`, `:113`), not `instances.status`; there is no instance-status-writer registry to extend. A rider that stamps `instances.status` via the existing repository CAS adds no constitution entry.
* Mirror-reconciliation caveat: `TaskRepository.reconcile_turn_mirror(work_id)` (`daemon/repositories/task/repository.py:2919`) is the SOLE reconciliation path over `ALL_8_MIRRORS` (`daemon/services/turn_transitions.py:26-29`). A rider that stamps `instances.status` directly (like `finalize_boot_consistency_instance` does) does **not** reconcile the other seven mirrors — acceptable only because the wedge child has no live task/JobItem/watcher rows (legs 2/3/5 of the sweep guarantee exactly that).

### 6.4 Existing tests that pin sweep behavior (files + test names)

`tests/unit/services/test_stale_running_sweep_pins.py`
* `TestShapeBPositive` (`:432`): `test_completed_task_root_transitions_out_of_running` (`:434`), `test_no_task_row_completed_message_specimen_heals` (`:456`), `test_non_root_shape_heals` (`:477`), `test_kill_switch_is_a_complete_noop` (`:502`)
* `TestShapeBTraps` (`:519`): `test_pending_task_stays_running` (`:548`), `test_running_task_stays_running` (`:567`), `test_ready_message_stays_running` (`:583`), `test_unknown_message_status_stays_running` (`:598`), `test_live_subtree_member_stays_running` (`:618`), `test_pending_bus_watcher_stays_running` (`:642`), `test_open_report_injection_stays_running` (`:659`), `test_waiting_children_is_never_touched` (`:674`), `test_paused_is_never_touched` (`:694`), `test_young_evidence_stays_running` (`:713`), `test_failed_only_shape_is_out_of_scope` (`:729`)
* `TestStructural` (`:758`): `test_proven_terminal_predicate_is_closed` (`:759`), `test_never_touched_statuses_are_documented` (`:769`), `test_default_knobs_mirror_the_house_bounds` (`:774`), `test_kill_switch_defaults_on` (`:780`), `test_terminal_statuses_import_is_the_constant` (`:786`)

`tests/unit/services/test_stale_running_sweep_rider_pins.py`
* `TestNit1MintSitePrefixGuard` (`:411`): `test_prior_pending_report_under_a_different_anchor_blocks_a_second` (`:413`), `test_a_genuinely_new_child_report_still_mints` (`:454`), `test_a_sibling_child_id_does_not_alias_onto_the_child` (`:477`)
* `TestNit2NullStatusHolds` (`:515`): `test_a_null_status_result_holds_the_candidate` (`:517`), `test_probe_reports_no_holding_with_the_sentinel` (`:551`), `test_probe_returns_none_for_a_null_status_row` (`:565`)
* `TestNit4OrderByNewestEvidence` (`:606`): `test_a_young_evidence_row_does_not_starve_an_eligible_row` (`:608`)
* `TestNit6TypedReasonPersisted` (`:656`): `test_the_terminal_reason_is_persisted_on_the_instance_row` (`:658`), `test_the_reason_metadata_key_is_content_free` (`:677`)
* `TestNit3DocstringHonesty` (`:690`): `test_the_census_docstring_states_the_cap` (`:691`), `test_the_module_docstring_states_the_chain_is_the_net` (`:698`)

`tests/unit/services/test_stale_running_sweep_pg_selection_pins.py`
* `test_the_production_selection_sql_executes_on_postgres` (`:207`)

Adjacent pins that a rider touches indirectly: `tests/repositories/test_report_injection_prefix_boundary.py` (sibling-prefix boundary), `tests/job_queue/test_fp4_premature_terminal_pins.py`, `tests/job_queue/test_round2_council_fixes.py`, `tests/job_queue/test_job_result_summary_and_gate.py`, `tests/job_queue/test_instance_lifecycle_events.py`.

---

## 7. Rider design notes (evidence-backed, for the planner)

1. **The class discriminator already exists in the codebase** — "the child's report was already delivered" = `internal_report:{child}:%` row on the parent side in READY/PROCESSING/COMPLETED. Use `MessageQueueRepository.find_wake_already_delivered_evidence(parent_id, child_id)` (`message_queue/repository.py:277`) — indexed by `instance_id`, no scan. `parent_id` is already projected by the sweep's selection SQL (`stale_running_sweep.py:316`).
2. **The cheapest stop-the-drive rider**: pre-flight the discriminator in `_drive` (`stale_running_sweep.py:1198`) → if the obligation is discharged, **skip the chain call entirely** (no checkpoint read, no transaction) and count it as a new class (e.g. `skipped_already_reported`) instead of `drive_no_effect`. This removes the 2–7 s per-victim cost and the tick-duration growth, with **no** chain change.
3. **The cure rider**: same pre-flight, then land the child with the **existing** guarded CAS `finalize_boot_consistency_instance` (`task/repository.py:2195`) — the same primitive the boot reconciler uses for a non-root (`boot_consistency_reconciler.py:455-459`) — plus the sweep's provenance stamp (`stale_running_sweep.py:1125-1171`). No new SQL, no new column, no LLM, no parent.
4. **The chain-level rider (variant B)**: split the mint guard from the terminal stamp in `child_reports.py` so that "obligation discharged" ⇒ terminal stamp **without** a second report. This is the only variant that also gives the parent a *correct* second report for the revived episode; if the rider instead only terminalizes (variant A), the parent keeps the FIRST episode's report and never learns the revived episode's outcome — a semantic decision the planner must make explicitly.
5. **Invariant to preserve in every variant**: the parent must never receive two reports for one child (`child_reports.py:4654-4696`, pin `TestNit1MintSitePrefixGuard`), and the sibling-prefix boundary must not regress (`child_reports.py:4941-4958`, `tests/repositories/test_report_injection_prefix_boundary.py`).
6. **Do not use `terminate_instance`** for this class: it stamps `terminated` (not `completed`), **deletes** the child's `message_queue` + `task` rows (`instance_lifecycle.py:5431-5442`) — destroying the completion evidence the sweep's own selection depends on — and fires parent-facing abort side effects.

---

## 8. Open questions for the planner (not answerable read-only)

* Does the revived episode's outcome need to reach the parent (variant B) or is a terminal-stamp-only cure acceptable (variant A)? The current per-child lifetime guard says "no second report, ever" — that is the policy that created the wedge, and reversing it is a policy decision, not a code detail.
* Should the cure stamp `completed` (the child's work did complete) or `terminated`/`failed`? The boot reconciler precedent uses `completed` (`task/repository.py:2206`); the chain's own CAS uses `completed` (`child_reports.py:4912`).
* Should the cure fire the parent-facing side effects (SSE / CompletionRegistry / lifecycle event) that the chain's terminal path would have fired? The chain's post-commit dispatcher (`:5800+`) is keyed on the `_ChildCompletionDbResult` outcome, so a variant-A cure (outside the chain) would need its own side-effect story.
