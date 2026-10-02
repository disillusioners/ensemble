# Architecture Recommendation — Scheduled Tasks Plan Enrichment

Date: 2026-10-01
Author: architect (controller) — aggregation of 3 dispatched verification workers
Workers: `architect-worker-d4-idempotency` (resilience-design), `architect-worker-cancel-seam` (data-flow-design), `architect-worker-decisions-tz` (trade-off-analysis)
Repo state: working tree byte-identical to `feature/scheduled-tasks` (tip `e431251d`) for all daemon files analyzed — the current checkout (`feature/checkpoint-cleanup-ui-redesign`) is a descendant branch; `git diff --stat` empty across the 12 key files. All file:line citations below were verified against this tree.

**How to read this doc:** each focus area gets a verdict (CONFIRM / ADJUST / REJECT). "ADJUST" items are mandatory corrections the developer must fold into the phase plans before/at implementation. Section 8 is the consolidated correction list mapped to phases — build from that.

---

## 0. Executive Summary

The plan is **structurally sound and ~90% implementation-ready**. The D4 idempotency mechanism is verified sound at the substrate level (real unique index, atomic claim, correct dead-job bypass) but has **two mandatory adjustments** — the key's time source and a cron-path guard — without which the fix is a **no-op on restart** or causes **silent cron schedule death**. The CANCELLED design needs a new atomic repository method (the plan's "same transaction" claim is **false today**) plus a clobber guard in `stop_adapter`. The service seam must own stop→mutate→start itself — the plan cites a precedent that does not exist (today's PUT is DB-only and leaves the live adapter stale). All six open decisions are closed below; the OD-1 stretch migration is **dropped, permanently**. One new critical tz finding: naive one-shot times in the spring-forward gap are **silently mis-anchored today** and need a phase-1 helper.

| Focus area | Verdict | One-line reason |
|---|---|---|
| 1. D4 idempotency fix | **CONFIRM with 2 mandatory ADJUSTs** | Substrate verified; key source must be `self._run_at`, and key emission must be gated to one-time schedules |
| 2. Six open decisions | **CLOSED** (§2) | Each with recommendation + rationale + D1–D10 compatibility |
| 3. CANCELLED status | **ADJUST** | "Same transaction" claim false; `stop_adapter` clobbers cancelled→stopped; REST list unfiltered |
| 4. Tz chain (D2) | **CONFIRM with ADJUSTs** | Chain + croniter semantics verified empirically; one_time gap handling missing (🔴); fallback hardcode must be `timezone.utc` |
| 5. Service seam | **ADJUST** | Plan's stop/rebuild/start precedent citation is wrong; per-source lock + evict-before-stop required |
| 6. Crash-point matrix | **DELIVERED** (§6) | C1/C2 unsafe today, closed by the fix; C3/C4/C5 already safe |

---

## 1. Focus 1 — D4 Double-Dispatch Fix: CONFIRM with Two Mandatory Adjustments

### 1.1 The substrate is sound (verified)

Every load-bearing claim about the dedup machinery held up under tracing:

- **DB-level enforcement is real.** `daemon/repositories/job_queue/models.py:282-306` defines `idx_job_idempotency` as a **partial UNIQUE index** (`WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL`); migration `20260619_120000_fix_idempotency_index_include_deleted_at.sql` confirms PG/SQLite alignment.
- **The claim is atomic.** `daemon/repositories/job_queue/repository.py:476-489` uses `INSERT ... ON CONFLICT DO NOTHING` keyed on that index + `commit()` in one transaction; follow-up SELECT at `:494-498`. Concurrent same-key enqueues **cannot** double-insert — single round trip.
- **Dispatch gating is correct.** `daemon/services/job_queue_service.py:1232-1236` fires `dispatch_bus.notify_new_job` only `if created and job is not None` — replay returns the existing `JobItem` without re-notifying. At-most-once **dispatch**, not just at-most-once-row. ADR-004's claim is CONFIRMED.
- **Dead-job resurrection works.** `job_queue_service.py:1217-1230`: if the existing job's `admission_state ∈ {DONE, DEAD}`, the service bypasses dedup with a synthetic suffix (`{key}#{uuid8}`) and inserts fresh. A one-shot whose first JobItem dead-letters still re-fires on retry — correct recovery semantics, not a double-dispatch. Known edge: a job that races to DONE before a 5s retry also gets a fresh row (requires enqueue-success + callback-failure + sub-5s completion — narrow; document, don't change).
- **TTL note.** `_idempotency_key_ttl_hours` (default 24h, `job_queue_service.py:1202-1211`) expires dedup; a re-fire >24h later mints a synthetic-suffix row. Cosmetic for one-shots (the disable-write has long since landed); document only.
- **Kwarg blast radius is clean (ADR-011 CONFIRMED).** Grep-verified: zero positional-after-`metadata` callers across `scheduler.py:762-769`, `registry.py:1084-1091`, `routers/messages.py:687-704`, `job_queue_service.py:2311`, `tools/job_queue.py:1993`, and test facades. Key length ≈79 chars vs `max_length=255` (`models.py:377`). No collision with existing key producers (SHA-256 hex / empty).
- **The bug is real (all 4 plan claims CONFIRMED).** In-memory latch (`scheduler.py:150`, set `:434`, no DB write); fire-and-forget callback (`registry.py:482-496`, no await/error propagation); no unique constraint on `schedule_executions` (`repositories/source/models.py:143-149` — both indexes non-unique; `SKIPPED` in enum at `:38`); 5s retry loop (`scheduler.py:447-450`, `constants.py:236`).

### 1.2 🔴 MANDATORY ADJUST 1 — the key's time source must be `self._run_at`

`_get_next_trigger_time()` returns **`now`** for past-due one-shots (`scheduler.py:486-487`: `if run_at <= now: return now`). If the implementer threads that `next_trigger` value as `planned_run_at` (the natural reading of the plan's "planned_run_at kwarg :430"), the key **mutates on every adapter cycle and every boot** → the dedup never matches across restart → **the D4 fix is a no-op for its primary purpose**.

The key MUST be composed from `self._run_at` — the configured `run_at`, parsed once in `__init__` (`scheduler.py:114`, re-parsed identically every boot at `:232-235`), immutable per config row:

```python
idempotency_key = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
```

Note `scheduler.py:683` already writes `self._run_at.isoformat()` into the JobItem metadata — the key can be derived inside `_route_via_job_queue` without threading a new kwarg through `_emit_scheduled_message`/`_execute_run` at all (simpler diff; either shape is acceptable, the VALUE is what is mandatory). Phase-1 Task 3 must pin the value source explicitly, and the phase-5 restart test must assert key equality across a simulated reboot.

### 1.3 🔴 MANDATORY ADJUST 2 — gate key emission to one-time schedules

`_route_via_job_queue` (`scheduler.py:689-690`) is **shared by the cron and one-shot paths** (gated only by `trigger_type == "scheduled"`). If the key is emitted unconditionally, recurring schedules collide with themselves across days whenever `planned_run_at` repeats — and worse, a cron key derived from any stable per-schedule value would collapse **all future fires into one JobItem → silent schedule death**. Emission must be guarded:

```python
if self._schedule_type == SCHEDULE_TYPE_ONE_TIME:
    kwargs["idempotency_key"] = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
```

Cron fires intentionally keep minting fresh JobItems (each fire is distinct work). Phase-5 needs `test_cron_not_affected_by_idempotency_key`.

### 1.4 Focus 1(d) — do the disable-write / execution-record also need crash-safety? **No — idempotency_key alone suffices.**

The two disable writes (`registry.py:506-507`) are separate transactions, but the boot filter tolerates the asymmetry (crash between them leaves either `enabled=False` → skipped, or `status` still non-STOPPED but the extended CANCELLED/STOPPED filter catches the terminal cases — see §6 C3). The execution-record is pure audit (a stray `triggered` row for a duplicate attempt is observable but harmless). OD-1's unique `(schedule_id, planned_run_at)` row would add nothing reachable (see §2 OD-1). Optional one-line win, not required: consolidate the two writes into a single-transaction `disable_one_time(source_id)` repository method.

### 1.5 Collateral

`tests/job_queue/test_idempotent_enqueue_atomic.py:365` already uses `source="scheduler"` — audit fixtures that pre-set `idempotency_key` when phase-1 lands, or the new semantics will trip stale assertions.

---

## 2. Focus 2 — Six Open Decisions: CLOSED

| # | Decision | Closure | Rationale (evidence) | D1–D10 |
|---|---|---|---|---|
| OD-1 | `planned_run_at` column + unique partial index on `schedule_executions` | **DROP — permanently, not a fast-follow** | The only failure mode it closes that `idempotency_key` does not is a *different* schedule row claiming the same `(schedule_id, planned_run_at)` — structurally unreachable today (one adapter = one config row = one `run_at`). Both workers independently converged on drop; it solves a problem D4 does not pose. Zero migration cost on the critical path (D1). | ✅ D1, D4 |
| OD-2 (leader's "dict-vs-Pydantic") | Service function params | **Pydantic at the service boundary, accepting dict via `model_validate`** | `async def create_schedule(payload: ScheduleCreatePayload \| dict, *, caller_instance_id, caller_agent_id)` — one-line `model_validate` bridge covers the phase-3 REST caller (which passes `model_dump()` dict, `phase3-plan.md:186`) and gives tools+REST one validated contract (`phase2-plan.md:78-142,256,325-332` names contract drift as the load-bearing risk). Dict-only scores 3.05 vs 4.30 weighted on Maintainability+Risk. | ✅ D1, D6 |
| OD-3 | `task_schedule` agent existence check | **FAIL-FAST at create time** | `enqueue_message_job` (`manager.py:7854-7887`) does NOT validate agent_id — failure surfaces deep in `_process_message_with_tracking` after the schedule is persisted. `AgentRegistry.exists(agent_id)` (`daemon/registry.py:1173`) + `get_registry()` (`:1189`) make it a one-line create-time guard that catches typos before a stale `source_configs` row exists. Redundant with enqueue-time resolution but load-bearing for UX. | ✅ D1, D7 |
| OD-4 | Suppress tz-fallback warning for "now-ish" one-shots | **KEEP the warning — never suppress** | Relative times are NOT tz-independent from the user's perspective: "in 5 minutes" anchors to the daemon clock, and an undetected local tz means the echo comes back at an unexpected wall-clock time — the warning is the only signal. Suppressing for one phrasing creates inconsistent warning behavior that is harder to debug than a consistent loud one. Fleet operators kill the warning wholesale via `ENSEMBLE_SCHEDULING_DEFAULT_TZ`. | ✅ D2, D6 |
| OD-5 / ADR-012 | `DELETE /api/sources/{id}` guard for scheduler rows | **ADOPT NOW in phase-3 — with a placement correction** | The DELETE handler (`sources.py:374-405`) has ZERO scheduler guard today and unconditionally purges `schedule_executions` (`repository.py:281-284`) — one curl destroys history; this is exactly the D5 hazard class. `_reject_scheduler_lifecycle` exists (`sources.py:44-66`, used at `:429` start / `:530` stop). **Placement:** insert the call AFTER the get-or-404 at `:382` and before `:394`, so unknown ids still 404 rather than 400 (code `SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`). ~3 lines + one test. | ✅ D1, D5, D6 |
| Phase-4 merge gating | (a) merge 2+4 together / (b) xfail / (c) land-then-green | **Option (a) — merge phases 2 and 4 as one PR** | The drift test (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) is **bidirectional** and runs in CI: phase-4's meta.json allow-list fails it until phase-2's `KNOWN_TOOL_NAMES` regen lands. Option (c) is fiction (a red CI merge is not mergeable); option (b) parks an accepted-red signal over the exact drift surface. Combined PR = one green gate proving ADR-009's static posture. If leader/jober registration is deferred past the merge, parameterize their registration tests over only the agents actually registered — do not xfail. | ✅ D9 |

---

## 3. Focus 3 — CANCELLED Terminal Status: ADJUST

### 3.1 Verified foundation

Dual enum CONFIRMED: `daemon/models/source.py:8-14` (lowercase str-Enum) and `daemon/repositories/source/models.py:20-25` (uppercase) — **both** must gain `CANCELLED`, and `SourceStatus.is_valid()` gates the write path (`repository.py:250` raises `ValueError` on unknown values) → the enum lands BEFORE any service code writes `'cancelled'` (ordering constraint on phase-1).

### 3.2 🔴 ADJUST — ADR-007's "same transaction" claim is FALSE today

`update_source_config` (`repository.py:98-145`) and `update_source_status` (`:237-261`) each open their own session and commit — **two independent transactions; no existing method writes both fields atomically** (grep-verified). The plan's claim requires a NEW repository method:

```python
def cancel_source_config(self, source_id) -> SourceConfig:  # one session, enabled=False + status='cancelled', one commit
```

Update ADR-007's wording accordingly. Crash-window analysis (§6 C3) shows either write order is boot-safe under the extended filter, but the atomic method is the honest implementation of the plan's own contract and costs ~10 lines.

### 3.3 🔴 ADJUST — `stop_adapter` clobbers CANCELLED → STOPPED

`registry.py:662` unconditionally writes `SourceStatus.STOPPED.value` on stop. Any later `stop_adapter` call touching a cancelled row (e.g., a future stop-all sweep, or the cancel flow itself calling stop after the status write) makes the row **resumable via `/start`** — resurrecting a "terminal" cancel. Guard: in `stop_adapter` (`registry.py:652-664`), skip the status persist when `config.status == 'cancelled'` (return success — the row is already terminal), and/or make `cancel_schedule` the sole writer of `'cancelled'`. Phase-5: `test_stop_adapter_does_not_clobber_cancelled`.

### 3.4 🟡 ADJUST — REST surfaces and consumers

- **`GET /api/schedules` list (`schedules.py:39-79`) has NO status filter** (`:47-50` iterates `list_source_configs()` unconditionally) → cancelled rows **pollute the default list**, contradicting ADR-005's hidden-by-default semantics. Add default exclusion + `?include_cancelled=true` query param (mirror of the tool flag).
- **New `GET /schedules/{id}` SHOULD return cancelled rows** (that is the "did I actually cancel X?" path) — pin it in the phase-3 spec.
- **`GET /api/sources`** serializes raw status (`_source_to_info`, `sources.py:69-83`) → `"cancelled"` reaches the frontend, which maps only `running/stopped/error` (grep `frontend/src`). Render-as-string fallback is acceptable; note it in `docs/scheduling.md`. SSE status events pass through likewise — informational.
- **Boot filter** (`registry.py:281-294`): three sequential `continue`s (`not enabled`, `not autostart`, `status == STOPPED.value` at `:292`). The single equality is not extensible in place — rewrite `:292` to `status in {SourceStatus.STOPPED.value, SourceStatus.CANCELLED.value}`. Add a boot smoke test asserting cancelled rows never auto-start.
- Collateral: `tests/migration/test_data_factory.py:570` comment "all 4 SourceStatus values" goes stale — reword.

### 3.5 What does NOT need changing

Adapter-internal equality checks (`scheduler.py:256` etc., 15+ sites across adapters comparing against RUNNING/STOPPED/STARTING/ERROR) are benign: the adapter is evicted on cancel, and M8 (in-memory `_is_one_time_executed` reset on stop→start re-fire) is closed by the idempotency key. No sweeping enum audit beyond the sites above is required — the 60-match consumer sweep found no other misreading class.

---

## 4. Focus 4 — Tz Chain (D2): CONFIRM with Adjustments

### 4.1 Verified (code + empirical probes on this host)

- ZoneInfo call sites (`scheduler.py:10,123,126`; `tools/time.py:26-28`), the silent naive-anchor trap (`scheduler.py:228-237` — `tzinfo is None → replace(tzinfo=utc)`, zero warning), and the dead re-anchor branch (`:483-484` — unreachable, `:234-235` already anchored): all CONFIRMED.
- `pyproject.toml` has no tzdata dep on Linux (`uv.lock` carries `tzdata` only for `win32`); **this host resolves `ZoneInfo('America/New_York')` from `/usr/share/zoneinfo` without the pip package** — `/etc/localtime → /usr/share/zoneinfo/Etc/UTC`. The "no tzdata dependency" claim is robust on Linux; Windows is already covered by the lock marker.
- `croniter>=3.0.0` declared (`pyproject.toml:25`), locked at **6.0.0**. Empirical probes: spring-forward `30 2 * * *` from 2026-03-08 → fires `03:30 EDT` (**skips the gap** ✓); fall-back `30 1 * * *` from 2026-11-01 → `01:30 EDT` (**first occurrence** ✓). Pinned ADR-008 semantics match observed behavior, and the phase-5 test dates (2026-03-08 / 2026-11-01) are the correct 2026 US transition dates.

### 4.2 🔴 ADJUST (new, critical) — one_shot naive times in the DST gap are silently WRONG

Python's stdlib anchor (`replace(tzinfo=tz)` on a naive local time) applies the **pre-transition offset** to a nonexistent wall-clock time: naive `2026-03-08 02:30 America/New_York` becomes `07:30Z` (EST) — an instant that corresponds to no real local time and fires an hour "early" relative to the skip-the-gap intent. Fold behavior is silently fold=0 (first occurrence — matches croniter by luck, not by design).

**Required phase-1 deliverable** (before phase-2's `create_schedule` can be safe): `daemon/utils/tz.py::anchor_local_to_utc(naive_local, tz)` implementing the pinned rule —
1. already-aware → trust caller;
2. ambiguous (fold) → **fold=0, first occurrence** (matches croniter's default and ADR-008 — one DST rule for the whole feature);
3. nonexistent (gap) → **shift forward by the gap** (02:30 → 03:30 EDT) **+ loud `tz_warning`** echoed in the response (`"shifted-forward from nonexistent local time …"`).

Without this, the feature ships a D2 violation on day one. Phase-5: parameterize the gap/fold tests over BOTH the cron path (croniter) and the one_shot path (this helper) so the two paths can't drift.

### 4.3 🟡 ADJUST — terminal fallback must be `datetime.timezone.utc`, and no third detector

Normalize the detection ladder to exactly the plan's two mechanisms + hardcode:
1. `/etc/localtime` symlink walk → 2. `TZ` env → 3. **`timezone.utc` constant + warning**.

Two corrections to worker-proposed detail: (a) do NOT use `ZoneInfo('UTC')` at the ladder bottom — on a truly stripped container (no system tzdb, no pip tzdata) it raises `ZoneInfoNotFoundError`; `datetime.timezone.utc` is C-level and cannot fail. (b) do not introduce a `tzlocal()`-style third probe — `tzlocal` is not a declared dependency and adding one contradicts the zero-dep posture of ADR-002.

### 4.4 Caching

Keep the plan's `host_local_tz_cache_seconds=300` for positive results; add a **shorter negative cache (~60s)** for failed detection so an operator fixing `/etc/localtime` mid-process isn't hidden for 5 minutes. (`negative_cache_seconds=60` on `SchedulingConfig`, or `min(60, host_local_tz_cache_seconds)`.)

---

## 5. Focus 5 — Service Seam (`scheduling_service`): ADJUST

### 5.1 🔴 ADJUST — the plan's stop/rebuild/start precedent citation is WRONG

The plan (ADR-005/ADR-006 and phase files) cites `PUT /schedules/{id}` at `schedules.py:251-339` as the stop/rebuild/start precedent. **That range is `POST /{id}/start`.** The actual PUT is `schedules.py:83-172`, and it performs a **DB-only config merge (`:135-143`) with no adapter rebuild** — meaning today, after a PUT, the live adapter keeps stale `__init__`-captured `_agent`/`_timezone`/`_cron_expression` until the next daemon restart (pre-existing latent defect in the current surface, per the registry invariant at `registry.py:666-686`).

Consequence for the plan: the service's `update_schedule` **cannot "reuse" a precedent — it must implement** the sequence itself: `stop_adapter → mutate config (DB) → create_adapter_from_config → register → start_adapter`, under a **per-source `asyncio.Lock`** (keyed by `source_id`, held across the whole sequence). The lock is required because `SourceRegistry` has **no internal per-source lock** (grep-verified): two concurrent updates on the same id can interleave stop/register such that one register raises `ValueError` on a duplicate slot (`registry.py:170-185`).

### 5.2 🟡 ADJUST — evict before awaiting stop

Registry eviction happens at `registry.py:661` **after** `adapter.stop()` completes. Between `stop_event.set()` (`scheduler.py:287`) and eviction, a concurrent trigger lookup can still reach the adapter, and a wait-timeout return can still run `_emit_scheduled_message` (`:418→:423→:430`) — a genuine fire-during-cancel/update window. For `cancel_schedule` and `update_schedule`: remove the registry entry FIRST, then `await adapter.stop()` (the 30s grace period, `constants.py:235`, still drains in-flight `_execute_run`). Missed fire during a brief update gap is acceptable and should be documented as such (a rebuild gap of <1s vs. a config change the user explicitly requested).

### 5.3 🔴 Document (don't "fix") — an in-flight JobItem is NOT cancellable

A trigger that already enqueued (`scheduler.py:690/716` → JobQueueService) is **outside the adapter's cancel surface** — `cancel_schedule` cannot recall it, and the job runs to completion for a now-cancelled schedule. This is inherent to the architecture (jobs are independent once admitted) and acceptable IF documented: echo the last `execution_id` from `cancel_schedule` so the operator can cancel the job directly, and note in `docs/scheduling.md` that a cancel racing a due fire may still see one final execution (its `triggered` row lands in history per M5 — executions are read by `schedule_id` with no status join, `schedules.py:391-458`, which is correct: the history SHOULD show the race outcome).

### 5.4 Single-writer status

There is no optimistic locking / version column on `source_configs`; adapter status-writes (`registry.py:595,662,714,725,765`) and service config-writes are last-write-wins and can interleave (a status write landing between the service's read-modify-write of the config dict). The per-source lock (§5.1) plus evict-before-stop (§5.2) shrinks the window to the adapter's own writes, which are the disable/status writers D4 already tolerates. Do NOT add a version column for this feature (D1, cost >> residual risk). Document the residual in `scheduling_service.py`'s module docstring.

---

## 6. Focus 6 — Crash-Point Matrix

Landing state per crash point (one-shot path; "current" = pre-fix):

| Crash point | Description | Current design | idempotency_key only (planned fix, with §1.2/§1.3 adjusts) | Verdict |
|---|---|---|---|---|
| **C1** | Daemon dies after `enqueue_message_job` returns, before execution-record/disable-write | ⚠️ **UNSAFE** — boot re-fires, second JobItem | **SAFE** — partial unique index returns the existing JobItem; `notify_new_job` suppressed (`created=False`) | Closed by fix |
| **C2** | After execution-record persists, before disable-write(s) | ⚠️ **UNSAFE** — same re-fire | **SAFE** — same key match; stray `triggered` audit row at worst (SAFE-WITH-ARTIFACT) | Closed by fix |
| **C3** | Between the two disable writes (status set, `enabled` not yet — or reverse) | SAFE — boot filter skips on either terminal signal once extended (§3.4); asymmetry tolerated | SAFE — same | Already safe; atomic `cancel_source_config`/`disable_one_time` removes the artifact |
| **C4** | Adapter task killed mid-cycle, before enqueue | SAFE — no JobItem exists; next boot = first attempt | SAFE | Already safe |
| **C5** | Restart between boot-filter read and adapter start | SAFE — no persisted dispatch state in that window | SAFE | Already safe |
| **C6** (new, from §5) | Cancel/update racing an in-flight trigger that already enqueued | n/a (no cancel today) | **SAFE-WITH-ARTIFACT** — one final execution runs for the cancelled schedule; documented + `execution_id` echo | Accepted, documented |

The 5s intra-lifetime retry: with the key sourced from `self._run_at` (§1.2), retries within one lifetime AND across restarts hit the same key → existing JobItem returned, no duplicate. With `next_trigger` sourcing, C1/C2 would REOPEN (key re-anchors to a new `now` per boot) — this is why §1.2 is mandatory, not advisory.

---

## 7. Risks the Plan Missed (consolidated, severity-ordered)

| # | Risk | Severity | Owner phase |
|---|---|---|---|
| R1 | Key-source ambiguity — `next_trigger` returns `now` for past-due one-shots (`scheduler.py:486-487`); naive implementation makes D4 fix a restart no-op | 🔴 | phase-1 |
| R2 | Cron-path co-dispatch — unguarded key emission in shared `_route_via_job_queue` (`scheduler.py:689-690`) collapses recurring fires into one JobItem | 🔴 | phase-1 |
| R3 | One_shot naive gap times silently mis-anchored (pre-DST offset on nonexistent local time) — D2 violation; needs `anchor_local_to_utc` | 🔴 | phase-1 (helper), phase-2 (use), phase-5 (tests) |
| R4 | `stop_adapter` clobbers `cancelled` → `stopped` (`registry.py:662`) — terminal cancel becomes resumable | 🔴 | phase-1 |
| R5 | ADR-007 "same transaction" is false — two commits; needs new `cancel_source_config` method | 🔴 (plan-defect; boot-safe either way) | phase-1 |
| R6 | Service seam has no precedent to reuse — PUT is DB-only (`schedules.py:83-172`); stop→mutate→start + per-source lock must be built new; current PUT leaves live adapter stale (pre-existing latent defect worth a one-line mention in phase-3 docs) | 🟡 | phase-2 |
| R7 | Eviction-after-stop window (`registry.py:661`) lets a trigger fire during cancel/update — evict first | 🟡 | phase-2 |
| R8 | `GET /api/schedules` unfiltered — cancelled rows pollute default list, contradicting ADR-005 hidden-by-default | 🟡 | phase-3 |
| R9 | DELETE guard placement — insert after get-or-404 (`sources.py:382`) or unknown ids 400 instead of 404 | 🟡 | phase-3 |
| R10 | In-flight JobItem not cancellable — document + `execution_id` echo | 🟡 | phase-2 (doc), phase-5 (test) |
| R11 | Fixture audit — `test_idempotent_enqueue_atomic.py:365` uses `source="scheduler"`; stale pre-set keys will trip | 🟢 | phase-5 |
| R12 | `test_data_factory.py:570` "all 4 values" comment goes stale | 🟢 | phase-5 |
| R13 | Frontend has no `cancelled` status mapping (renders raw string) — acceptable; note in docs | 🟢 | phase-4 (docs) |
| R14 | Idempotency TTL 24h + DONE-race synthetic-suffix — cosmetic; document | 🟢 | docs |

---

## 8. Consolidated Correction List (implementation-actionable)

**Phase 1 (foundation):**
1. Key formula pinned to `self._run_at.isoformat()` — state the value source in the task text; derive inside `_route_via_job_queue` (metadata precedent at `scheduler.py:683`) or thread explicitly (§1.2).
2. Guard key emission with `schedule_type == ONE_TIME` (§1.3).
3. Add `CANCELLED` to BOTH enum sites (`daemon/models/source.py:8-14`, `daemon/repositories/source/models.py:20-25`) — before any writer; `is_valid()` gate at `repository.py:250` then passes.
4. New `cancel_source_config(source_id)` repository method (one session, `enabled=False` + `status='cancelled'`, one commit) — §3.2; amend ADR-007 wording.
5. Guard `stop_adapter` (`registry.py:652-664`) against clobbering `cancelled` — §3.3.
6. Rewrite boot-filter line `registry.py:292` to `status in {STOPPED, CANCELLED}` + boot smoke test — §3.4.
7. `daemon/utils/tz.py::anchor_local_to_utc` with fold=0 / gap-shift+warning — §4.2.
8. Terminal tz fallback = `datetime.timezone.utc`; negative cache 60s — §4.3/§4.4.
9. Record OD-1 as DROPPED (not stretch) in `decisions.md`.

**Phase 2 (service + tools):**
10. `update_schedule` implements stop→mutate→start under a per-source `asyncio.Lock`; evict registry entry before awaiting `stop()` (§5.1/§5.2).
11. Pydantic-at-boundary signatures with `model_validate` dict-accept (§2 OD-2).
12. Create-time `get_registry().exists(agent)` fail-fast (§2 OD-3).
13. `cancel_schedule` echoes last `execution_id`; module docstring documents in-flight-job + single-writer residuals (§5.3/§5.4).

**Phase 3 (REST):**
14. `GET /schedules` default-excludes cancelled + `?include_cancelled=true`; `GET /schedules/{id}` returns cancelled rows (§3.4).
15. DELETE guard in `sources.py` after the get-or-404 (§2 OD-5).

**Phase 4/5:**
16. Merge phases 2+4 as one PR (§2); parameterize registration tests over actually-registered agents.
17. Tests: D4 restart key-equality, 5s-retry collapse, cron-unaffected, stop-doesn't-clobber-cancelled, cancelled-never-boot-starts, list-filter, one_shot gap/fold (helper + croniter, both paths), anchor warnings.

**Plan-document fixes (no code):** ADR-007 transaction wording (§3.2); PUT citation `:251-339` → actual PUT `:83-172` + note rebuild is NEW logic (§5.1); `test_data_factory.py:570` comment (R12).

---

## 9. Confidence & Assumptions

**Confidence: HIGH** on focus areas 1, 3, 5, 6 (substrate traced to index/transaction level; all claims file:line-verified). **HIGH** on tz code claims and host behavior; **MEDIUM** on cross-container tz robustness (probes ran on this host only — the `timezone.utc` hardcode and the win32 lock marker bound the residual).

**Assumption that would flip recommendations:** if `create_or_get_by_idempotency_key`'s terminal-state bypass (`job_queue_service.py:1217-1230`) were removed or changed to reuse DONE jobs, the dead-job recovery semantics in §1.1 would invert — re-verify that block if the job-queue service is touched by any concurrent commission. Similarly, if `SourceStatus` gains a second terminal value or the boot filter is restructured for another feature, re-run the §3 consumer sweep (60 grep matches were classified against this tree only).
