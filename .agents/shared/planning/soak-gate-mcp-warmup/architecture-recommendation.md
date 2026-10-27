# Architecture Recommendation — soak-gate-mcp-warmup

**Commission:** promote soak gate fix + MCP warmup degradation fix
**Branch / worktree:** `feature/soak-gate-mcp-warmup` @ `d5a4e4ad9` · `/home/nea/ensemble-src-wt-soak-gate`
**Date:** 2026-10-27 · **Status:** COMPLETE (3/3 analysis reports fanned in)
**Inputs:** plan set (`plan-overview.md`, `decisions.md` §3a–3h + CONSTRAINT 1/2, `requirements.md` §4/§6, `phase1-3-plan.md`, `risk-safety-register.md`) + three code-reading analysis passes (resilience-design: A1–A4 · structural-design: B1+C1 · data-flow-design: C2–C4).
**Role of this file:** the code-level, implementable layer UNDER the plan set. `decisions.md` remains the design authority; where this file adds placement, fields, formulas, or rulings, it is binding for implementation. Where it disagrees with a plan anchor, §10 records the disagreement with evidence.

---

## 0. Standing constraints (restated, all preserved)

1. **C-1 never-green rollback survives** — proven branch-by-branch in §2.4.
2. **Six non-goals untouched** (design-around, never fix): `ens_db_tools.py:1479/:1491` BEGIN-outside-try; `/api/instances` ORM read-path leak; `queue_freshness` probe transaction leak; `mission_records` mint error-path leak; pool hardening knobs (`pool_pre_ping`/`reset_on_return`/`pool_recycle`); wedge-capture `tasks`→`task` table-name bug.
3. **Reuse over invention** — new concepts are enumerated in §9 with justification.
4. **No new SQLModel tables, no migrations** in this lane. Quarantine/grace state is in-memory per-boot; durable history rides logs/journal. (A new table would need its own `.sql` in `daemon/migrations/versions/`, and fresh-SQLite full-ledger boot is blocked by pre-existing prefix `20260714_000001` — avoided entirely.)
5. No live contact: nothing in this design touches the live daemon, install `.env`, `state.json`, `releases/`, or the `current` symlink.

---

## 1. A1 — grace window: the precise state machine

### 1.1 Where the state lives

Two layers, because the leak predicate needs cross-tick history and `ReadinessComposite` is per-tick:

- **Per-tick projection** — `ReadinessComposite` (`daemon/services/readiness.py:172-226`) gains additive fields, mirroring the `inflight_turns` precedent (`readiness.py:198-205`):
  `grace_active: bool = False`, `grace_reason: Optional[str]`, `grace_expires_at: Optional[datetime]`, `grace_scope: str = "load"`, `grace_holds: int = 0`, `leak_signal: {detected: bool, checkedout_rising: bool, streak: int}`, `pool_state: {sqlalchemy: {checkedout, size, overflow}, psycopg: {pool_size, pool_available, requests_waiting}}`.
> Normative source: requirements.md §4.2 (P2 — the class vocabulary and the aggregation the projection encodes) and §4.4 (P4 — the leak-signal semantics). The **field NAMES and their additive shape** are this layer's; the **semantics each field carries** are §4's.

- **Cross-tick tracker** — a stateful `GraceTracker` stored at `app.state.readiness_grace_tracker`, updated inside `refresh_readiness_composite`. It holds the 3-tick leak windows (per-pool counters + same-component streak). Placed on `app.state` (not a local) so a refresher-task restart does not reset the leak predicate mid-observation. **This is a NEW concept** (the plan places fields on the composite but does not say where history lives) — see §10.1.

### 1.2 Failure-class table (daemon-side classification)

The daemon classifies each degraded tick (from `_guarded` timed-out flags vs exception paths, `readiness.py:477-521` — never from free-text reasons, which are stale: `readiness.py:336-338` still names the replaced `check()` probe).

**The class list, each class's grace eligibility, the multi-component aggregation rule, and the counter effect are `requirements.md` §4.2's** — this layer does not restate them (iteration 003: a restated predicate that drifts from the normative one is a fold failure).

> Normative source: requirements.md §4.2 (P2 — grace eligibility classes, the NORMATIVE multi-component aggregation rule, and the counter observation kinds).

**Architect position (reasoning, not a rule):** the classification must come from the probe's own **structured** signal, never from parsing reason free-text — the reason strings are known-stale and the daemon already has the timeout/exception distinction at the probe boundary.

### 1.3 Leak predicate — measured from pool objects, never `pg_stat_activity`

**The leak signature, the ORACLE/ADVISORY pool split, and the streak-arm qualification are `requirements.md` §4.4's** — this layer does not restate them.

> Normative source: requirements.md §4.4 (P4 — leak signature, ORACLE pool = P2, ADVISORY pool = P1, streak arm only when pool objects are UNAVAILABLE).

**Architect position (reasoning, not a rule):** all signals come from **in-process pool objects** — an extra query on the poisoned pool would amplify the disease. Two consequences this layer must respect: (a) the **P1 SQLAlchemy pool is ADVISORY** (its signals stay visible in `detail.pool_state` but MUST NOT arm the leak class, because `pool_size=5, max_overflow=10` makes `overflow() > 0` non-zero on a busy-but-healthy pool by design) — arming from P1 would refuse the grace on the incident's own busy shape; and (b) the **streak arm is qualified to the pool-objects-UNAVAILABLE case only**, so a sustained busy burst is never classified as a leak. The **P2 psycopg saver pool** is the oracle; it is read through the existing `getattr(checkpointer.raw_saver, "conn", None)` + `hasattr(conn, "get_stats")` shape (`readiness.py:675-681`).

### 1.4 Arming, duration, reset, expiry

**The grace ceiling, its clock, the reset conditions, and the relationship to the soak's absolute bound are `requirements.md` §4.3's** — this layer does not restate them.

> Normative source: requirements.md §4.3 (P3 — grace ceiling, its single observation-relative clock, no boot-relative ceiling), §4.6 (P6 — the AC thresholds those bounds carry).

**Architect position (reasoning, not a rule):** the grace is armed on the **first grace-eligible degraded observation** and the clock is **observation-relative** — a boot-relative clock would expire the grace before the burst it exists to absorb, which is the incident's shape. **Boot age and burst state are published CONTEXT only**, never arming preconditions: the **2026-10-09 v0.18.5 environmental class** is covered by the same class-driven rule, so a boot-age/burst conjunction would have been *narrower* than the incident class. The **boot-age signal** is `uptime_seconds` from the `/livez` body (`daemon/api.py:4805`) — already plumbed; no new surface.

### 1.5 Observability contract — exact `ReadyzResponse.detail` additions (additive only)

(The **field NAMES and the additive shape** are this layer's; the **class/eligibility semantics** each field carries are `requirements.md` §4.2's and §4.4's — this layer does not restate them.)

```json
"detail": {
  "grace":        {"active": true, "reason": "pool_busy", "expires_at": "ISO", "scope": "load", "holds": 1},
  "leak_signal":  {"detected": false, "checkedout_rising": false, "streak": 0},
  "_normative":   "field NAMES are this layer's; the class / eligibility SEMANTICS are requirements.md §4.2 (P2) and §4.4 (P4) — the normative home",
  "pool_state":   {"sqlalchemy": {"checkedout": 2, "size": 5, "overflow": 0},
                   "psycopg":    {"pool_size": 10, "pool_available": 8, "requests_waiting": 0}},
  "mcp": { "<server>": { "state": "ok|degraded|quarantined", "reason": "…|null", "since": "ISO|null",
             "init_attempts": 0, "last_error_class": null, "breaker_state": "closed|open|half_open|null",
             "recoverable": true } }
}
```

**Hard invariants while grace is held:** `/readyz` still answers **503**; `status` stays `"degraded"`; failing `components` stay `false`; `detail.reasons` unchanged — a suppressed reason string is a defect (pinned). `detail.grace` absent (older daemon) ⇒ class `unknown` ⇒ fail-closed, no grace, no extension — pre-change gate behavior preserved verbatim.

### 1.6 Gate-side consumption

`gate_soak` reads the body on the transient path (today rc=2 reads the body only on green, `lib.sh:3005`) and parses `detail.grace` / `detail.leak_signal` via the existing `_json_sub`/`_json_field` helpers (`lib.sh:3148-3150`).

### 1.7 Ruling: the rc=3 path is NOT extended

`_boot_grace_active` (`lib.sh:3196`) suppresses all transients during a fixed post-restart window — exactly the time-gated shape CONSTRAINT 1 forbids. The class-gated grace is a **parallel, additive mechanism** consumed via the readyz body; rc=3 keeps its existing semantics and pins (an rc=3 observation is a RESET at the counter, never a HOLD — an ignored probe is not a green). The existing rc=3 path: `_probe_once_classified` folds 503s into `transient` (`lib.sh:2986-3001`), and the suppression block silences transients on age alone (`:3022-3028`).

---

## 2. A2 — soak counter semantics in `scripts/upgrade/lib.sh gate_soak`

### 2.1 New shell constants (beside `ADAPTIVE_SOAK_*`, `lib.sh:146-149`; deliberately not env-tunable first cut)

(The **values** are `requirements.md` §4.6's; the **placement** — beside `ADAPTIVE_SOAK_*` in `lib.sh`, deliberately not env-tunable on the first cut — is this layer's.)

`GRACE_HORIZON_S=120` · `GRACE_EXTENSION_CAP_S=180` · `MAX_HOLDS_PER_GREEN=2`.

### 2.2 Counter semantics (three observation kinds)

**The observation kinds, their preconditions, the GREEN reset-on-green, the livez-hard-green HELD precondition, and the bound on consecutive holds are `requirements.md` §4.2's** — this layer does not restate them (iteration 003, blocking #2: the restated bullets here previously **omitted** the GREEN reset-on-green and the livez-HARD-green HELD precondition, and the omission made a livez outage launder into a HOLD; pointing at the normative text makes the omission impossible to repeat).

> Normative source: requirements.md §4.2 (P2 — counter observation kinds GREEN / HELD / RESET, including the GREEN `consecutive_hold = 0` reset and the livez-HARD-green HELD precondition).

**Architect position (reasoning, not a rule):** a HELD advances nothing, so a transient-only stream can never reach 3 greens; the **bound on consecutive holds** is what stops a *sustained* transient stream from satisfying the 3-green requirement, and the **unconditional** cap-reach trigger is what makes the end state a **rollback** rather than a wait. The "3 greens over 9 observations" arithmetic is exact only **with** the GREEN reset-on-green, which is why that reset is normative rather than incidental.

### 2.3 Window extension formula (CONSTRAINT 2)

**The cap arithmetic, the extension cap, the re-arm recompute timing, and the soak's absolute bound are `requirements.md` §4.3's / §4.6's** — this layer does not restate them.

> Normative source: requirements.md §4.3 (P3 — the grace ceiling, its clock, and the relationship to the soak's absolute bound), §4.6 (P6 — the AC thresholds: soak extension cap, soak absolute bound).

**Architect position (reasoning, not a rule):** the extension is **entry-anchored**, not grant-anchored — a grant-anchored clock would let a repeatedly re-arming grace walk the ceremony's wall-clock budget, and the extension is **spent, not refunded**.

### 2.4 Never-green proof (the C-1 survival proof)

**The never-green trigger is `requirements.md` §4.1's** — **UNCONDITIONAL**: at cap-reach, `consecutive_green < 3` ⇒ return 1 ⇒ ADR-005 auto-rollback (+ quarantine); a cap-reach has exactly **two** outcomes and **none** returns 0. This layer does not restate it.

> Normative source: requirements.md §4.1 (P1 — the never-green trigger; the ceremony-truncation case is the PRE-SOAK deferral at `lib.sh:5088-5093`, not a cap-reach outcome; any fairness diagnostic is DIAGNOSTIC ONLY and must not influence the return value).

**Architect position (reasoning, not a rule):** the proof walks every class branch to cap-reach — a hard red resets, a never-eligible class resets, an unknown class fails closed — so **any** branch that never greens ends at cap-reach below 3. A **grace-eligible daemon that never greens still rolls back**: the grace arms, the counter still reaches cap-reach below 3, return 1. The withdrawn "fair window" conditional (iteration 002, blocking #1) is recorded as a **REJECTED alternative** in `decisions.md` §3b's alternatives table and in §10.1 below.

### 2.5 The `_PROBE_STATUS` aliasing fix (lands in the SAME change)

> Normative source: requirements.md §4.2 (P2 — the counter observation kinds; the aliasing fix is the MECHANISM that realises them, not a rule of its own).

Today the counter reads the global `_PROBE_STATUS` twice (`lib.sh:5216-5221`) and the readyz probe overwrites it (`:5181`) — livez busy-amnesty soft-green + readyz hard green currently INCREMENTS. Fix: capture `livez_status="$_PROBE_STATUS"` immediately at `:5162` and `readyz_status="$_PROBE_STATUS"` at `:5181`; the counter evaluates the locals. Phase-1 exit grep: `_PROBE_STATUS` no longer referenced at the counter site. The conservative block (`ENSEMBLE_SOAK_CONSERVATIVE=1`, `lib.sh:5113-5149`) stays byte-identical.

---

## 3. A3 — promote-txn hold (auto-continue defers, never drops)

### 3.1 Hold predicate

New thin helper `promote_txn_live()`, reusing existing readers — the daemon never writes `in_flight kind="promote"` (shell-owned) and this design adds no writer:

> **Normative source: requirements.md §4.7 (P7 — A3 release rule). The predicate as stated there is binding.**

```python
def promote_txn_live() -> bool:
    # Two-leg predicate — requirements.md §4.7 (normative). Do NOT narrow this.
    in_flight = _journal_read_install_dir_in_flight()          # read-only; daemon never writes kind="promote"
    if not (isinstance(in_flight, dict) and in_flight.get("kind") == "promote"):
        return False
    # LEG 1 (post-boot): the shell's heartbeat is fresh
    if journal_owner_heartbeat_fresh(install_dir):             # upgrade_journal.py:281-319, window 60s (:156)
        return True
    # LEG 2 (BOOT WINDOW): the daemon is young, the txn exists and is inside the ceremony envelope.
    # Exists because the shell writes in_flight BEFORE the restart (promote.sh:327 -> :413 -> :426) and the
    # next heartbeat refresh happens only inside gate_livez's retry loop (lib.sh:3074/:3230/:3286), which
    # runs only after the daemon can serve /livez — the observed daemon was ~75s old at first green readyz,
    # so the 60s heartbeat window can already be expired at seam 1 (api.py:2703).
    return (daemon_boot_age_s() < BOOT_LIVENESS_S              # 120.0 s — §4.7
            and in_flight.get("started_at") is not None
            and (now_utc() - started_at) < PROMOTE_TOTAL_DEADLINE_S)   # 900 s — §4.7
```

- `journal_owner_heartbeat_fresh`: `daemon/tools/upgrade_journal.py:281-319`, freshness window `OWNER_HEARTBEAT_FRESH_S = 60` (`:156`) — comfortably inside the 15s soak cadence because `gate_soak` heartbeats every iteration (`lib.sh:5156`).
- Install-dir resolution reuses `_install_root_candidates` / `_canonical_install_root` (`upgrade_tools.py:388-431`).
- Vocabulary reuses the existing `"pipeline-busy"` refusal family (`upgrade_tools.py:2669-2683`).

### 3.2 FOUR dispatch seams (all existing retry paths, one new driver)

**CORRECTION (council C-1) — the pre-fold "three seams, re-evaluate on their next natural pass" was WRONG and is withdrawn.** The boot pass runs **exactly once per boot** — its only call site is `daemon/api.py:2703` (the other reference, `:2681`, is the import) — and a promote **restarts the daemon before the gates**, so the pass *always* runs with the promote txn live, defers every candidate, and **there is no next pass this boot**. Deferred boot-pass candidates would fall to StaleTaskRecovery's `force_cancel_and_schedule_retry` (`daemon/repositories/task/repository.py:5591`) at `stale_task_recovery_threshold_minutes = 10` (`daemon/config.py:1821-1823`) — **losing the checkpoint resume the pass exists to provide**. A fourth seam was also missing: the task-less-orphan sweep covers a sub-class StaleTaskRecovery **cannot** reap.

| # | Dispatcher | Seam | Hold behaviour | Release behaviour |
|---|---|---|---|---|
| 1 | Boot auto-continue pass | `daemon/services/auto_continue_boot_pass.py:272`, called at `daemon/api.py:2703` | Candidates **deferred** into the hold module's seam-1 list; reported as deferred in `ContinueResult` | On predicate flip-to-false, released through the boot pass's own **checkpoint-resume** path |
| 2 | Runtime resume dispatch | `daemon/manager.py:13671` (`asyncio.create_task(self._resume_processing_background(...))`) | Task **not created**; the resume is parked in the seam-2 list | The **original** `create_task(...)` is made — the checkpoint-resume path, not a cold dispatch |
| 3 | Eligible-pending sweep | `daemon/services/eligible_pending_sweep.py:399` (`await self.sweep_once()`; the loop body is `:387-402`, the tick is immediate) | The tick **skips the wake** and parks the eligible rows in the seam-3 list; a skip count is recorded | The parked rows are re-notified through the sweep's own wake path |
| 4 | Task-less-orphan sweep | `daemon/services/auto_continue_boot_pass.py:726-729` (`sweep_orphaned_live_turns`), wired at `daemon/api.py:2708` | Task-less orphans **deferred** into the seam-4 list | Released through the sister pass's own resume primitive |

### 3.3 New module: `daemon/services/promote_txn_hold.py`

> Normative source: requirements.md §4.7 (P7 — the promote-txn LIVE predicate, the release rule, the hysteresis, and the driver shape the module implements).

Owns the predicate, the per-seam deferred-candidates lists, and the release driver. It imports the existing journal readers — not the MCP or upgrade-tool surfaces.

### 3.4 Release, drain, crash

**The release rule, the LIVE predicate (with its POST-BOOT and BOOT-WINDOW legs), the release HYSTERESIS, the driver shape, and the deferral semantics are `requirements.md` §4.7's** — this layer does not restate them.

> Normative source: requirements.md §4.7 (P7 — the promote-txn LIVE predicate; the boot-window leg and `BOOT_LIVENESS_S = 120.0` s; the boot-window HOLD-only release rule; the two-consecutive-false release hysteresis; the lazily-created module-held driver; and "the hold DEFERS, never drops").

**Architect position (reasoning, not a rule) — the two safety legs this layer must not lose:**

- **SAFETY #3 (blocking #3) — the BOOT-WINDOW leg is REQUIRED.** The heartbeat-freshness proof covers the **SOAK** (the shell's `lock_heartbeat` runs inside `gate_soak`'s loop, `lib.sh:5156`), but it does **not** cover **seam 1**, which fires during the daemon-BOOT window: the shell writes `in_flight` **before** restarting the daemon (`promote.sh:327` open → `:413` stamp → `:426` restart) and the next heartbeat refresh happens only inside `gate_livez`'s retry loop (`lib.sh:3074`; `_probe` `:3230`/`:3286`), which runs only **after** the daemon can serve `/livez`. The observed daemon was ~75 s old at its first green readyz, so a 60 s heartbeat window can already be **expired at seam 1** — the hold would be **silently inert** at the seam with the largest RCA contribution. The boot-window leg closes that hole; during it the hold is **HOLD-only** (release requires `in_flight` to be **ABSENT**, not merely stale).
- **SAFETY #4 (blocking #4) — the RELEASE HYSTERESIS is REQUIRED.** The accessor returns False on `JournalTorn`/`OSError` (`daemon/tools/upgrade_journal.py:300-303`), so a **single torn read** would flip the predicate false and **release the deferred resumes mid-promote** — the exact burst A3 exists to suppress. The release sweep therefore fires only after **two consecutive false** reads, at least one driver tick (5 s) apart. The risk register named this guard (R-LIVE-13) before the design did; the register entry now **references §4.7 rather than owning the rule**.


---

## 4. A4 — contention-vs-pool: the honest split

### 4.1 The arithmetic

Effective concurrent-writer ceiling = `min(llm_concurrency=10, WORKER_POOL_SIZE 5 + CHAT_WORKER_POOL_SIZE 2) = 7` — `llm_concurrency` is acquired around the entire `graph.astream(...)` turn (`instance_messaging.py:5407-5408`); worker pools at `constants.py:91/:103` cap it further. The sentinel's `aget_tuple` takes the same single `asyncio.Lock` (langgraph checkpoint `aio.py:59`, taken at `:374`) that serializes ALL checkpoint writes — **pool enlargement cannot shorten that queue**.

### 4.2 Sizing formula + config SSOT (house pattern)

```
checkpoint_pool_max_size = clamp(concurrent_writers + 1 probe + 2 headroom, min=5, max=20) = clamp(7+1+2) = 10
```

SSOT in `LimitsConfig` (`daemon/config.py:587`, `env_prefix="LIMITS_"`, pattern of `llm_concurrency` at `:595`):

| Field | Default | Env | config.yaml |
|---|---|---|---|
| `limits.checkpoint_pool_max_size: int = Field(default=10, ge=5, le=20)` | 10 | `LIMITS_CHECKPOINT_POOL_MAX_SIZE` | `limits:` block |
| `limits.checkpoint_pool_timeout_s: float = Field(default=5.0, ge=1.0)` | 5.0 | `LIMITS_CHECKPOINT_POOL_TIMEOUT_S` | `limits:` block |

### 4.3 Ghost-waiter bound

> Normative source: requirements.md §4.6 (P6 — the acceptance-criterion thresholds this bound carries).

The psycopg `AsyncConnectionPool` (`persistence.py:273-294`) accepts `timeout` (verified in psycopg_pool 3.3.1; library default 30.0s) and `max_waiting`. **Set `timeout=5.0`** — a timed-out probe's ghost lifetime drops 30s→5s; at the 10s probe cadence at most one live ghost exists. **Leave `max_waiting=0` (unlimited) initially** — bounding it changes the failure mode to `PoolTimeout`, which would require re-classifying that exception as `pool_busy` in the §1.2 table; revisit only if the A1 `requests_waiting` surfacing shows unbounded waiter growth.

### 4.4 Sentinel contention-free path (the piece sizing cannot deliver)

1. **Non-blocking lock OBSERVATION (D1a) — one mechanism, and it is the observation.** The probe **reads** the saver lock's state; it **never acquires** it. The resolvable expression is **`checkpointer.raw_saver.lock.locked()`** — a **non-blocking** boolean read.
   - **SAFETY #5 (iteration 002, blocking #5) — the acquire-and-read variant is DELETED and must not ship.** The literal reading of "acquire the saver lock, then read while holding it" **self-deadlocks**: the sentinel read at `langgraph/checkpoint/postgres/aio.py:374` takes the **same** lock (declared `:43`, assigned `:59`), and `self.lock` is a plain **non-reentrant** `asyncio.Lock` — so the read could never complete (`hard_component_red`, grace-INELIGIBLE) and every production checkpoint write would queue behind the probe. The two mechanisms are **incompatible**; the non-blocking one is pinned.
   - **Classification:** lock **held** at observation time ⇒ `pool_busy` (grace-eligible). Lock **not held** ⇒ proceed with the sentinel read; read timeout ⇒ `read_timeout` (grace-eligible); read raises ⇒ `hard_component_red` (never eligible). The class vocabulary and the eligibility are `requirements.md` §4.2's.
   - > Normative source: requirements.md §4.2 (P2 — the class vocabulary and the grace eligibility that the classification maps onto).
   - **The probe's RETURN CONTRACT changes** (this is the design authority's call; the class semantics are §4.2's): the probe callable's signature goes from `Callable[[], bool]` to a **`(bool, class)` pair**, so the orchestrator consumes the class without re-deriving it from free-text reasons. This layer carries the signature because the binding arch is where the probe's surface lives.
   - **PIN (N-a + SAFETY #5):** (i) the expression **resolves against the real adapter surface** — `daemon/checkpoint_adapter.py:190-195` ("Non-method access (e.g. `wrapper.conn`, `wrapper.lock`) passes through unchanged") and `:227-229` (`lock` among the attributes `__getattr__` covers); the underlying `AsyncPostgresSaver` declares `lock: asyncio.Lock` (`aio.py:43`) and assigns `self.lock = asyncio.Lock()` (`:59`) — a test asserts `checkpointer.raw_saver.lock` IS an `asyncio.Lock` and `.locked()` is callable and returns a bool on a real PG-backed saver; and (ii) **the probe never holds the saver lock across `aget_tuple`** — a test asserts the lock is not held by the probe when the sentinel read is issued. An unresolvable expression must never be able to disable the grace silently.
2. **Probe future cancellation**: explicitly cancel the scheduled `_sentinel_cycle` future when the budget expires (`readiness.py:732`). CAVEAT: whether cancelling a task blocked in `pool.getconn` removes the waiter from psycopg_pool's internal queue is library behavior — **verify, do not assume**; acceptance criterion = waiting-queue length does not grow across N timed-out probes. The AC thresholds it carries are `requirements.md` §4.6's. Fallback if cancellation does not dequeue: a dedicated probe connection bypassing the pool queue.
   - **RECONCILIATION (council N-c) — the dedicated-connection fallback is a MITIGATION, never a DETECTOR.** A dedicated connection that bypasses the pool's acquisition queue **cannot observe queue growth**: it never queues, so it does not disturb `requests_waiting` on the shared pool and cannot report the ghost-waiter accumulation it was meant to detect. If the V4 AC fails, the dedicated connection is adopted **only** as a *contention mitigation* (a timed-out probe stops occupying a shared slot), **not** as a replacement for the shared-pool probe. **Pool-starvation detection stays with D1a's lock classification (`pool_busy` vs `hard_component_red`) plus the `detail.pool_state` observables** — swapping the shared probe for a dedicated one would make the pool **unobservable** at the exact moment it matters.
3. The asyncpg adapter pool (`persistence.py:313-314`, topology P3) gets the same knob only if measured to share contention — P2 is the pool the sentinel rides.

### 4.5 Honesty statement (binding)

Sizing does **NOT** cure the SQLAlchemy-side (P1) leak — non-goals #1–#4 are owned by the follow-up DB-transaction-discipline commission. This lane sizes headroom against the concurrent-writer arithmetic, bounds acquisition, and makes the poison VISIBLE via `detail.pool_state` (§1.5) without attaching another query to the poisoned pool.

**A4 sizes P2 only.** The P1 mechanism — the `queue_freshness` probe runs on **P1** (`daemon/api.py:3779`, `make_queue_probe(manager.engine, …)`) — is **not** addressed by A4, and per §4.4 the P1 signals are **ADVISORY**: visible in `detail.pool_state`, never arming the leak class. That is why the healthy-release rollback **can recur** for the P1-leak signature: the grace engages on it (it presents as `probe_timeout`, grace-eligible) and the leak repair — not the grace — is the mitigation.

---

## 5. B1 — MCP warmup off the event loop

### 5.1 Mechanism ruling: NO new supervisor thread/process

The warmup stays an asyncio task on the daemon loop; the three bounded sync blocks move to **two dedicated `ThreadPoolExecutor`s**; every wait gets a bound. Rejected: a warmup supervisor thread (warmup is async end-to-end; cross-thread result plumbing for no benefit) and a subprocess supervisor (the spawn must stay in the daemon's process group for pid-registry + cgroup-sweep attribution). Results re-enter only as the `Future` from `loop.run_in_executor(...)` awaited on the loop — no `call_soon_threadsafe` anywhere.

**Why dedicated executors are REQUIRED, not stylistic**: `asyncio.to_thread` / `run_in_executor(None, …)` share the default pool, which carries the daemon's LLM calls (`graph.py:1952-1957`) and compaction (`compaction.py:4647`); because the scan is awaited, a queued scan would stall the handshake behind a 120s LLM call. Precedent for dedicated pools: `plane_sync_service.py:253` (max_workers=2), `question_pause_set.py:137-139`.

### 5.2 Site 1 — the `/proc` child-stamp scan (`stdio_wrapper.py`)

- `_PROC_SCAN_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-procscan")`; `_stamp_children` stays sync (unit-testable core); new `async def _stamp_children_off_loop` awaits it.
- `_run` (`:401-409`) widens `on_ready` from `Callable[[], None]` to `Callable[[], Optional[Awaitable[None]]]` (`:266`) — call it, `if inspect.isawaitable(result): await result`. Backwards compatible (no test references `on_ready`).
- **Ordering is load-bearing (plan correction)**: the scan must complete **BEFORE `_ready.set()`**. The plan's "eventually consistent" deferral is unsafe — `_mark_children_exit` (`on_close`) runs at `:423-430`, AFTER the inner `__aexit__` at `:414` kills the child ⇒ a deferred scan finds a dead child ⇒ `scan_mcp_child_pids` returns `[]` ⇒ the child is never registered (no exit-mark, no pid attribution, no reap handle). The ordering is pinned by an existing test: `tests/test_resource_pid_registry.py:505-510` (registry row exists immediately after `await cm.__aenter__()`). The hop moves the WORK; the await keeps the guarantee.
- **Optional (own pin, skippable)**: read `/proc/<pid>/task/<tid>/children` (one read, direct children) with fallback to the current O(all-pids) scan on non-Linux — turns O(host pids) into O(children).

### 5.3 Site 2 — secret/KMS resolution (two spawn paths)

`resolve_env` (`kms_resolver.py:101-178`) is pure in-memory — NOT a blocker; and context7 has **no env** (`context7.py:33-46`), so this site contributed nothing to the incident. The blocker is `resolve_secret_markers` **with markers present** → `SecretService.resolve_marker` → `engine.begin()` + SELECT + audit INSERT (`secret_service.py:1854-1899`) — a real blocking PG round-trip. Fix: `_SECRET_RESOLVE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-secret")` in `warmup_pool.py`; hoist the composed call via `functools.partial`. Fail-closed preserved — the typed envelope propagates out of the Future; the spawn aborts before the child exists. **Scope extension: `daemon/mcp/connection_manager.py:229-237` gets the same hoist** — it is absent from phase 2's touched-file list and it is the incident's ×4-instances cold path.

### 5.4 Site 3 — unbounded handshake + timeout scope (pool-only defect)

- **Precision the plan misses**: the cold path is already bounded — `connection_manager.py:254-255` has `__aenter__` inside `asyncio.timeout(timeout)` with `STDIO_DEFAULT_TIMEOUT=30.0` (`:40`). The unbounded handshake is **pool-only** (`warmup_pool.py:275` sits outside the 60s timeout opened at `:278`).
- `stdio_wrapper.py` gains `HANDSHAKE_WAIT_TIMEOUT_S: float = 30.0` — the **value** is `requirements.md` §4.6's (P6, **normative**); the **placement** (the `stdio_wrapper.py` module constant, and the `await asyncio.wait_for(self._ready.wait(), ...)` shape) is this layer's; `__aenter__` uses `await asyncio.wait_for(self._ready.wait(), HANDSHAKE_WAIT_TIMEOUT_S)`; expiry hits the existing `except BaseException` (`:307-314`) which cancels the background task and re-raises into the caller's cleanup.
- `warmup_pool.py` names the literal `WARMUP_CREATE_BUDGET_S: float = 60.0` and **opens the budget BEFORE the handshake**. Arithmetic (state it in the code comment): 30s handshake + 2s sleep + 3×10s initialize + 2s/4s backoff = 68s > 60 ⇒ the outer budget wins and cancels the 3rd initialize retry — **do not widen the outer budget** (that re-opens the hang).
- **G1 (orphan path) ships with the bound, not after it**: the wrapper never invokes the SDK's own shutdown on the cancellation path — `finally#3` (`stdio_wrapper.py:412`) is attached to `try#3` (`:410`), not `try#2` (`:384`), so the re-raise at `:386-388` skips the killpg shutdown at `:412-414`. Fix the try-scope so a cancellation mid-handshake runs the inner `__aexit__`. The SDK's pinned shutdown exists (`mcp==1.27.1`, `client/stdio/__init__.py:190-216`, killpg at `:209`) — whether its generator unwind completes under in-flight cancellation is anyio-timing-dependent (§13), so the wrapper-level guarantee is required regardless. Pin: cancellation mid-handshake ⇒ inner `__aexit__` ran ⇒ 0 surviving children.

### 5.5 Hang-wedge guarantee

After 5.2–5.4 every spawn path is bounded ⇒ `pool.warmup()` returns within `WARMUP_CREATE_BUDGET_S` ⇒ `start_health_check()` (`manager.py:3161`) and `eager_warm_schemas()` (`:3170`) always start ≤~60s after boot — a single hung spawn can no longer wedge the two starters (today `manager._warmup_and_report` awaits `pool.warmup()` at `:3158` before them, and `except Exception` at `:3162` cannot fire on a hang). A `_replenish` semaphore slot (`:108`, `:569`) is held ≤60s, never forever. Belt-and-braces: `WARMUP_TOTAL_BUDGET_S: float = 90.0` (`asyncio.wait_for`) around the per-server gather (`:190-193`, currently uncapped) — **lands after G1**. Raise the replenish failure log from DEBUG (`:581-582`) to WARNING (a C4 precondition: today the forever-retry is invisible).

### 5.6 Stays on the loop (bounded residuals — do NOT move)

SDK JSON-RPC parse/validate (inside the SDK read task, unforkable) · `load_mcp_tools` + `adapt_mcp_tools` (`warmup_pool.py:315-323`) · `session.initialize()` (`:292` — the CPU burn is in the CHILD) · `send_ping` (`:624`) · the timeout budgets. Dead-knob decision (phase-2 file scope): `McpPoolConfig.health_check_timeout` (`config.py:2900`) is declared but never read; the ping timeout is hard-coded `5.0` — wire it or delete it.

### 5.7 No-new-starvation argument

No new thread kind (2 executors × 2 workers = 4 lazy, process-lifetime threads, house precedent). Not the shared default executor (§5.1). Bounded queue — submissions = spawn count, bounded **only by C1's quarantine** ⇒ **B1 must not ship without C1** (phase-2 exit gate). No lock convoy — the only `threading.Lock` (`_claimed_children_lock`, `:64`) guards a set add/discard, never across an await. Nothing posts to the loop from a foreign thread; no module-level asyncio primitives are introduced.

### 5.8 Loop-lag ACCEPTANCE CRITERION (council BLOCKING 2)

**The threshold, the measurement method and the pin are `requirements.md` §4.6's** — this layer does not restate them.

> Normative source: requirements.md §4.6 (P6 — acceptance-criterion thresholds; the event-loop lag bound and its measurement under the R-1 concurrent burst).

**Architect position (reasoning, not a rule):** moving work off the loop is a hypothesis until the loop's health is **measured under the incident's own load**; a pin with no number is not a pin, and an idle-loop measurement would trivially pass and prove nothing about the incident.


---

## 6. C1 — circuit breaker, wiring, per-boot quarantine

### 6.1 ONE new home: `daemon/mcp/init_guard.py` (leaf module)

Owns (a) per-server `CircuitBreaker` instances, (b) the quarantine registry, (c) `classify_failure()`. Imports ONLY `daemon.sources.circuit_breaker` + stdlib — importable from `base.py` with zero cycle risk. **Collapse ruling** (the plan's two homes — registry in `builtin_servers/__init__.py` + guard elsewhere — is one concept too many): two homes = two state stores = a coherence race, and `base.py` consulting a parent-package registry is an order-fragile import cycle that breaks silently on reordering. One module updates breaker state and quarantine entry in the same event-loop turn.

### 6.2 Quarantine store + `is_available()` split (DECISION: both, in a split)

- **Store**: `_QUARANTINE: dict[str, QuarantineEntry]` module-level in `init_guard.py` ⇒ per-process ⇒ **per-boot** (restart clears it — the requirement's "for THIS boot" semantics). Entry: `{server, reason, since (monotonic + ISO), attempts, last_error_class, breaker_state, recoverable}`. The registry is a **projection of breaker state** — `is_quarantined(name)` is True iff an entry exists and `breaker_state != CLOSED`. No table, no migration.
- **Predicate**: surfaced through `definition.is_available()` so the four consultation sites need no edits — `manager.py:2813` (bootstrap row), `manager.py:3057` (pool registration — verified on disk: the consult IS at `:3057`; `:3058` is the following `logger.debug`), `mcp_service.py:579` (eager warm), `mcp_service.py:1130` (instance preload gate). Only the latter two are dynamic (post-boot); the first two are inherently pre-boot ⇒ **`is_available()` alone cannot stop spawns** — hence the seams in §6.4.
- **Two defects in the plan's literal spec, both fixed here**: (a) `cls.name` is a **property object**, not a string (`base.py:27-30` `@property @abstractmethod`) — the plan's `if is_quarantined(cls.name)` never matches; use `cls().name` (definitions are stateless, already instantiated at `builtin_servers/__init__.py:79-81`). **Verified by execution** (iteration 003): `Context7ServerDefinition.name` is a **property object** (base.py:27-30 is `@property @abstractmethod`), so `C7.name == "context7"` is **False** while `C7().name == "context7"` is **True**; `cls.name` would make `is_quarantined(cls.name)` **never match** and silently disable the quarantine. The approver's N-d request to invert this was **not** applied, on this evidence (see §10.1). (b) `PlaneServerDefinition` **wholly overrides** `is_available()` without `super()` (`plane.py:262-289`) — a consult added only at `base.py:143` is silently bypassed for plane; add one line at `plane.py:263`: `if not super().is_available(): return False`. **`plane.py` joins phase 3's touched files.**

### 6.3 Breaker parameters (separate from the tool-call path)

Init-path breakers are **new instances with their own params** — `ResilienceManager`'s breakers (`resilience.py:493/:552`) guard TOOL CALLS and exist only for servers with a `ResilienceConfig` (plane only; context7/webfetch register none, `mcp_service.py:1277-1283`).

- `failure_threshold = 3` **spawn attempts per boot** (not the 3 inner initialize retries).
- `recovery_timeout = 300.0s` (≥5× the 60s health tick, `config.py:2899`, so the half-open probe isn't attempted every tick).
- **No backoff field invented** — the `CircuitBreaker` primitive has none (`circuit_breaker.py:26-27`); the existing 2s/4s chain (`warmup_pool.py:299-304`) + 3×10s initialize chain IS the backoff; inter-failure pacing is the health tick.
- **`reset()` (`circuit_breaker.py:114-124`) is never called** (the only unsynchronized mutator; its latent race in `ResilienceManager` is out of scope). The guard uses lock-protected `record_success`/`record_failure` (`:78-108`) only. Manual re-arm **replaces the breaker instance** (fresh `CircuitBreaker(...)`, mirroring `resilience.py:552`).

### 6.4 Failure taxonomy (merged with C2; `classify_failure` in `init_guard.py`)

| Class | Trigger | Breaker failure? | Quarantine? |
|---|---|---|---|
| `init_timeout` | `asyncio.TimeoutError` from the 60s budget or 30s handshake | yes | yes (threshold) |
| `handshake_failed` | 3× `session.initialize()` failure (`warmup_pool.py:286-309`) | yes | yes (threshold) |
| `spawn_failed` | `OSError` from the transport spawn (mcp 1.27.1 `client/stdio/__init__.py:131-137`) | yes | yes (threshold) |
| `dns_nxdomain` | C2 pre-check NXDOMAIN | yes | **IMMEDIATE (threshold-1)** — see ruling below |
| `dns_servfail` | C2 pre-check SERVFAIL/AGAIN | yes (counts) | only via threshold (not first occurrence) |
| `resolver_timeout` | C2 pre-check 2s timeout (normative: requirements.md §4.5) | no | no — observability only |
| `config_error` | marker resolution raised (`SecretMarkerResolutionError`/`KMSMarkerResolutionError`) | no | no — a config error must not mute a healthy server; fail-closed spawn abort preserved |
| `cancelled` | `CancelledError` | no | no — drain/shutdown is not a server failure |

**Normative source: requirements.md §4.5 (P5 — DNS-refusal thresholds and classification).** The table below is this layer's **index** of the classification; the bound and the class semantics are §4.5's.

**Architect ruling — `dns_nxdomain` is threshold-1:** for a pinned host, `EAI_NONAME` is a definitive signal (the name does not exist), not noise; waiting 3×60s=180s to quarantine a dead host defeats C1's fail-fast purpose. First NXDOMAIN ⇒ quarantine immediately; recovery remains the 300s half-open probe. This also closes the "C1 without C2 is slow" gap (§11.2). Flagged for leader visibility as a deviation from uniform threshold-3.

### 6.5 Wiring — FIVE seams (the plan lists three)

1. **Pool spawn seam**: `warmup_pool._create_pooled_connection` (`:228`) — before `StdioServerParameters` (`:253`): `await init_guard.may_attempt(server_name)`; False ⇒ raise `InitGuardRefused` (caught by the existing `except BaseException` cleanup `:333-344`); success ⇒ `record_success` + `clear_quarantine`.
2. **Cold spawn seam**: `connection_manager._create_stdio_session` (`:200`) — **omitted by the plan**, and it is the incident's ×4-instances path (`mcp_service.py:154-173`): an instance that preloaded BEFORE the quarantine would respawn the dead server on its next tool call (the tool is already in the instance cache).
3. **`_replenish`** (`warmup_pool.py:552-583`) — consult the guard **BEFORE taking the semaphore** (`:569`) so a quarantined server never consumes a slot; raise the failure log to WARNING.
4. **`health_check`** (`:584-641`) — the replenish trigger (`:640-641`) is covered by (3)'s gate.
5. **`base.py::is_available()`** (`:125-152`) — the four-site predicate + the `plane.py` `super()` guard (§6.2).

### 6.6 Recovery without a daemon restart

- **Primary (automatic)**: after `recovery_timeout` (300s) the breaker flips OPEN→HALF_OPEN and `can_execute()` (`circuit_breaker.py:60-68`) grants exactly ONE caller the probe (single-probe guard). The pool's `_replenish` (fires every health tick) is that caller: probe granted → one spawn → success ⇒ `record_success` (CLOSED) + `clear_quarantine` ⇒ the four sites resume; failure ⇒ OPEN again. No restart, no config reload, no new tool.
- **Secondary (manual, in-process)**: `init_guard.rearm(server_name)` → fresh breaker + drop the entry — a module function for tests and a FUTURE operator tool; **no new tool category in this lane** (category/allow-list pin ceremony out of scope; `infra.py` untouched).
- Day-one mitigations keep exact behavior — with the disclosure that `MCP_POOL_ENABLED=false` (`manager.py:3022-3024`) does **not** stop cold-path spawns (the pool is skipped; `mcp_service.py:154-173` still runs), so for this incident class the effective env mitigation is `MCP_DISABLE_BUILT_IN_CONTEXT7=true` (and it needs a restart) — C1 is the only in-process stopper, which is why it is not optional.

---

## 7. C2 — DNS pre-check (fail fast, no new hazards)

### 7.1 Mechanism

`asyncio.to_thread(socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP)` bounded by `asyncio.wait_for(..., DNS_PRECHECK_TIMEOUT_S=2.0)` — the **bound** is `requirements.md` §4.5's (P5, **normative**); the **call shape** (off-loop `getaddrinfo` in a thread, bounded by `wait_for`) is this layer's, invoked per spawn attempt from the pool/cold spawn seams (§6.5) BEFORE any child exists. Reuses the daemon's existing `to_thread` pattern (`readiness.py:470`, `mcp_servers.py:559`); no `getent` subprocess, no third-party resolver. `getaddrinfo` is the stdlib call the child itself relies on.

### 7.2 Target derivation (the load-bearing correction)

**The DNS thresholds, the classification, the off-loop requirement, the "resolve the EFFECTIVE post-heal base" rule and the "never pin the address" rule are `requirements.md` §4.5's** — this layer does not restate them.

> Normative source: requirements.md §4.5 (P5 — DNS-refusal thresholds and classification).

The daemon env does NOT reach the child (SDK 1.27.1 passes only HOME/LOGNAME/PATH/SHELL/TERM/USER when env=None) — and the plan's `${CONTEXT7_BASE_URL}/api` fallback is **wrong at the package level** (§8.2): the package hardcodes `https://context7.com`. Therefore the pre-check target is:

```
host = urlparse(CONTEXT7_API_URL).hostname   if CONTEXT7_API_URL is set in the merged row env
     else urlparse("https://context7.com/api").hostname   # the PACKAGE's hardcoded const — NOT the daemon's CONTEXT7_BASE_URL field
```

This guarantees the pre-check always targets the host the child will actually hit, even if an operator repoints the daemon-side base field.

### 7.3 Classification

- `socket.gaierror` with `errno in (EAI_NONAME, EAI_NODATA)` (Linux −2/−5; `h_errno` 1/4) ⇒ `dns_nxdomain` ⇒ immediate quarantine (§6.4).
- Other `gaierror` (`EAI_AGAIN`, `EAI_FAIL`) ⇒ `dns_servfail` ⇒ breaker failure (threshold), no first-occurrence quarantine.
- `asyncio.TimeoutError` ⇒ `resolver_timeout` ⇒ **skip the check, proceed to spawn** (the child's own timeout is the backstop) — observability only. The **classification and the bound** are `requirements.md` §4.5's (P5, **normative**); the *skip-and-proceed* reaction is this layer's.
- Success ⇒ `record_success`; the resolved address is **DISCARDED** (no IP pinning — never written to config, env, cache, DB, or logs beyond the hostname).

### 7.4 Refusal shape

No spawn, no `_replenish` arming, no child process; one WARN + one journal line **per state transition** (never per attempt); feeds the `init_guard` breaker bucket per §6.4. Per-server opt-out: definition-level `dns_precheck: bool = True` property — servers with no network dependency set False.

---

## 8. C3 — context7 template pin

### 8.1 Verified chain

`context7.get_config_schema()` → `mcp_servers.py:486` (template list) → POST `/api/mcp-servers/configure-builtin` (`:491`) → `validate_config_values` → `build_config` → row `config.env` (DB) → boot heal on schema-version drift (`manager.py:2854-2866` — the heal is the `config_schema_version != schema_version` branch; `:2775-2779` is the bootstrap **docstring**) → `build_pooled_stdio_config` (`warmup_pool.py:852`, row env WINS over definition env at `:933-936`) → `resolve_env`/`resolve_secret_markers` (`connection_manager.py:229-242`) → `StdioServerParameters` → child. PUT config on a built-in stays 403 `BUILTIN_SERVER_PROTECTED` (`mcp_servers.py:760-768`); the sanctioned write path is `/configure-builtin` only.

### 8.2 PREMISE CORRECTION (verified in package source, `constants.js:7-10`, cached v4.4.4)

The package derives `process.env.CONTEXT7_API_URL || \`${CONTEXT7_BASE_URL}/api\`` where `CONTEXT7_BASE_URL` is a **hardcoded const `"https://context7.com"`, NOT read from env**. Consequence: a `CONTEXT7_BASE_URL` value in the row env is **inert to the package** — only `CONTEXT7_API_URL` is an effective lever. A future host move requires setting `CONTEXT7_API_URL` (or a package version change). The daemon's base field remains a daemon-side mirror (used by operator docs; NOT by the C2 derivation — §7.2).

### 8.3 Schema shape (ruling: keep BOTH fields per plan G1, document inertness)

| key | section | type | default | role |
|---|---|---|---|---|
| `CONTEXT7_BASE_URL` | env | text | `https://context7.com` | daemon-side mirror; **inert to the package** — field description must say so |
| `CONTEXT7_API_URL` | env | text | None (unset) | **the only effective override**; unset ⇒ package hits its hardcoded const |

- Validation (webfetch precedent `webfetch.py:40-56`): `build_config` override raises `McpConfigValidationError` for non-`http(s)` scheme or embedded userinfo; router translates to 422 (`mcp_servers.py:526-549`). **No hostname allow-list** — it would pin a host (violates the no-IP-pinning spirit).
- Defaults live in the template schema (not a code constant); `base.py:213` skips None/empty, `:222-223` upper-cases into env, `:234-242` sets env only if non-empty.
- `schema_version` `"2"` → `"3"` (`context7.py:29-31`); boot path heals stale rows preserving `is_active`; `/configure-builtin` refuses across drift with 409 (`mcp_servers.py:576-592`) until boot heals. Disclosed + pinned.
  - **BLOCKING 3 — the heal MUST be ENV-PRESERVING, not a whole-config replace.** The boot heal is a **whole-config replace**: `daemon/manager.py:2829` computes `default_config = definition.build_config({})` and `daemon/manager.py:2855-2861` writes it wholesale (`update_mcp_server(existing.id, config=default_config, ...)`, logging "config reset to defaults due to schema version change" at `:2862-2867`). The codebase **documents this exact hazard** at `daemon/mcp/warmup_pool.py:867-876` ("row `config.env` wins over `definition.build_config({})` env on every key ... A stale definition would silently drop those keys").
    - **Consequence if left as-is:** the operator-set env is **DROPPED on the first boot after the upgrade** — the pinned `CONTEXT7_API_URL` and any KMS-marker keys. C3's entire purpose is to make the base URL settable and durable; a heal that deletes the setting on the first post-upgrade boot defeats it **silently**. Worse, **C2's DNS pre-check would then validate the DEFAULT host, not the configured one** — the pre-check would pass against a host the child never uses.
    - **DECISION (adjudicated): ENV-PRESERVING MERGE.** The heal must **preserve the operator-set env keys and the KMS-marker keys** across the schema-version change; **only the template-derived defaults are rebuilt**. Concretely: the new config is `definition.build_config({})` with the row's existing `config.env` **merged over it** (row env wins — the same precedence `warmup_pool.py:867-869` already pins), and any key the row carries that the new schema does not declare is **retained** so a schema narrowing cannot silently discard a KMS marker.
    - **REJECTED alternative — disclosed-discard** (keep the replace, disclose that the operator pin is lost on upgrade). Rejected: the pin would be silently deleted on the first post-upgrade boot and the operator would re-apply it every upgrade; disclosure is necessary but not sufficient.
    - **C2's derivation MUST read the EFFECTIVE post-heal env** — the merged `definition.build_config({})` env overlaid with the row's surviving `config.env` (the same merge `build_pooled_stdio_config` performs, `warmup_pool.py:852-897`), **not** the pre-heal row and **not** the bare template defaults.
    - **PIN (BLOCKING 3):** `config.env` **survives the 2 → 3 heal with the operator value intact** — a test writes a marker-bearing operator env onto the row, drives the heal, and asserts the post-heal `config.env` still contains the operator's key with its value, that the KMS-marker key is still present, and that the template-derived defaults were added **alongside** (not instead of) them.
- **INERT-LEVER HAZARD (council N-d) + REMEDY.** The `CONTEXT7_BASE_URL` field is **inert to the package** (§8.2) and stays inert if the package's derivation changes or the package's hardcoded base moves and our template default goes stale. An operator who sets it believing it moves the package is **silently wrong** and the wedge recurs. The hazard must be stated **in the field description itself**, and the **remedy** is the **schema-version bump**: any change to the package's derivation or to the effective base is a `schema_version` change, which forces the 409-then-boot-heal cycle (§8.3) and re-surfaces the field description to the operator. A package base change NOT accompanied by a schema bump is exactly the silent-wedge class this lane exists to prevent.
- **Loud degradation by construction**: unset `CONTEXT7_API_URL` + dead pinned host ⇒ C2 NXDOMAIN ⇒ immediate quarantine + `detail.mcp.context7.reason="dns_nxdomain"` — visible, not wedged.

### 8.4 Ruling on `mcp_set_env` (`daemon/tools/infra.py:1527`)

**LEFT OPEN as the sanctioned operator escape hatch** — it is the remediation route and the BYOK self-provisioning channel (`kms_attach`); closing it in this lane breaks both. Recorded as a separate defect (tool-surface consistency: writes `mcp_servers.config.env` with no `is_builtin` guard). If ever closed, the shape is an allow-list of operator-pinned keys for built-ins — never a blanket ban. **No change to `infra.py` in this lane.**

---

## 9. C4 — degraded-MCP observability surface

### 9.1 Payload (final merged shape — see §1.5 JSON)

`detail.mcp` publishes **all built-ins** (context7, webfetch, plane), healthy ones included, so absence is never ambiguous: `{state: ok|degraded|quarantined, reason, since, init_attempts, last_error_class, breaker_state, recoverable}`. `recoverable` is **computed at read time from the class table** (a DNS refusal is recoverable — the host may move; a config error is not auto-recoverable) — never stored, so it cannot go stale. Advisory only: never feeds `status`/`components`/`reasons` (B2 pin).

### 9.2 Surfaces

1. `/readyz` `detail.mcp` — additive key in `readiness.py` `to_payload` (the ONLY readiness change besides §1's keys).
2. **`/api/mcp-servers` list response gains an additive, read-only `health_state`/`quarantined` field** sourced from the registry (architect ruling on the plan gap: `/readyz` alone leaves the operator surface incomplete).
3. One WARN + one journal line per state TRANSITION (not per attempt) — the per-attempt noise is part of what made the incident hard to read. Journal entry shape follows `journal_history_append` (verify signature at implementation).
4. Logs: WARN on quarantine, INFO on recovery.

### 9.3 BLOCKING 4 — the daemon-side journal write is SUPPRESSED while a promote txn is live

**The `promote_txn_live()` predicate itself is `requirements.md` §4.7's** — this layer consumes it and does not restate it.

> Normative source: requirements.md §4.7 (P7 — the promote-txn LIVE predicate).

The C4 state-transition journal write (§9.2 item 3) is a **daemon-side write to the promote-owned `releases/state.json`**, and the write path is **unlocked**: `daemon/tools/upgrade_journal.py:331-371` (`journal_write`) performs a read → mutate → **whole-document write**, and its own comment at `:352-353` states the discipline ("P2.3 caller discipline still serializes journal writes externally; this is the inner ring") — the serialisation is the CALLER's job, and the daemon-side C4 writer is not a ceremony participant.

**The failure sequence (must be prevented):** MCP warmup runs at daemon boot; a promote **restarts the daemon before the gates**; an MCP init failure fires the C4 state transition while `in_flight.kind == "promote"` is live; the daemon-side write takes a **stale snapshot** and writes it back whole, **DROPPING `in_flight`**; the A3 hold predicate (§3.1) then reads `in_flight` as absent, the predicate flips false, and the deferred resumes are **RELEASED MID-PROMOTE** — the exact burst A3 exists to suppress.

**DECISION (adjudicated): SUPPRESSION**, with these precise rules:

- **The writer is the C4 / `init_guard` state-transition journal emit** — the *only* daemon-side writer this lane introduces. The suppression gate lives at that writer, not inside `journal_write` (the generic writer serves ceremony-owned flows too; gating it there has a much wider blast radius).
- **A suppressed write is BUFFERED in-memory**, bounded: **one entry per `server` + `state`**, **newest wins**. The buffer cannot grow without bound.
- **The buffer is FLUSHED** to the journal when the predicate flips **false** (txn close) **or at graceful shutdown** (the ordered-shutdown path).
- **A buffered entry is NEVER dropped.** If the daemon dies before the flush, the entry dies with the process — which is why the flush is registered on the ordered-shutdown path and why the transition is ALSO visible in the in-memory `detail.mcp` surface for the whole boot.
- **REJECTED alternative — lock-acquire** (the daemon-side writer takes the ceremony's journal lock before writing). Rejected: `journal_lock` belongs to the **shell ceremony**; a daemon-side acquire mid-promote is a **new cross-process contention point** that risks **ceremony interference** (it could block the ceremony's own writer, or hold the lock across the ceremony's commit path). The MCP state transition is **deferrable observability, not correctness**, so deferring it is strictly cheaper than contending for the ceremony's lock.

**PIN (BLOCKING 4):** a daemon-side MCP state transition during a live promote txn must **NOT** drop `in_flight` — a test drives a state transition while `promote_txn_live()` is true, asserts the journal's `in_flight` is **byte-identical** before and after, asserts the entry is **buffered** (one per server+state, newest wins), and asserts the buffer is **flushed** once the predicate flips false (and, separately, on graceful shutdown).

### 9.4 H7 — the `upgrade_tools.py` reasons-reader bug RIDES THIS LANE### 9.3 H7 — the `upgrade_tools.py` reasons-reader bug RIDES THIS LANE

`upgrade_tools.py:2088-2092` reads top-level `reasons` while the payload nests it under `detail` (renders `reasons=[]` during promote flows). Fix (Python, no helper — the plan's `_json_sub` reference is a shell function, not Python):

```python
reasons = readyz.get("detail", {}).get("reasons", readyz.get("reasons", []))
```

The fallback for older daemons is kept (one `.get`, zero cost). Pin: `test_upgrade_reasons_reader.py`. Justification: the commission's thesis is "a degraded daemon must stay visible"; a consumer rendering `reasons=[]` for a degraded daemon contradicts it, and deferring leaves a known-broken reader for no benefit.

---

## 10. Disagreements with the planner (all evidence-cited)

1. **Grace state holder gap** (A1): the plan puts fields on `ReadinessComposite` but never says where the 3-tick history lives ⇒ `GraceTracker` on `app.state` (NEW concept, §1.1).
2. **rc=3 ruling** (A1.8/A2): do NOT extend `_boot_grace_active` — it suppresses all transients on age alone, the exact shape CONSTRAINT 1 forbids. Class-gated grace is additive alongside (§1.7).
3. **E1 "eventually consistent" scan is unsafe** (B1): `_mark_children_exit` runs after the inner `__aexit__` kills the child; a deferred scan returns `[]` and the child is never registered; existing test `test_resource_pid_registry.py:505-510` pins registration-before-return ⇒ await the scan before `_ready.set()` (§5.2).
4. **`asyncio.to_thread` names the wrong executor** (B1): the default pool carries LLM calls + compaction; a queued awaited scan would stall behind a 120s LLM call ⇒ dedicated executors (§5.1).
5. **The 55s handshake example is arithmetically incompatible** with the 60s budget (93s total) ⇒ 30s bound (§5.4).
6. **The unbounded handshake is pool-only** — the cold path already bounds at 30s (`connection_manager.py:254-255`, `:40`) (§5.4).
7. **`resolve_env` is not a loop blocker** and context7 has no env — the secret-resolution site is real but was not an incident contributor (§5.3).
8. **The plan's `is_available()` consult is buggy** (`cls.name` is a property object) **and bypassed by plane's override** ⇒ `cls().name` + `plane.py` super() guard (§6.2).
9. **`is_available()` alone does not stop spawns** — the cold path never consults it ⇒ fifth seam (§6.5.2).
10. **Two quarantine homes collapse to one** — `init_guard.py` (§6.1).
11. **Phase file lists are short**: phase 2 += `daemon/mcp/connection_manager.py`; phase 3 += `daemon/mcp/builtin_servers/plane.py` (§5.3, §6.2).
12. **G1 confirmed against the pinned SDK**: the wrapper never invokes the SDK shutdown on the cancellation path (`finally#3` attached to the wrong try) (§5.4).
13. **Package env premise half-wrong**: `CONTEXT7_BASE_URL` is a hardcoded const, not env-read — only `CONTEXT7_API_URL` is effective (§8.2); the C2 derivation must use the package const (§7.2).
14. **`_json_sub` is a shell helper, not Python** — the H7 fix is a plain nested `.get` (§9.3).
15. **`/api/mcp-servers` status field unspecified in the plan** — added additively (§9.2).

### 10.1 Approver-fold corrections recorded (iteration 002 prep)

| # | Finding | Correction | Where |
|---|---|---|---|
| 16 | **C-1 (CRITICAL)** — the plan's "three seams, re-evaluate on their next natural pass, no dedicated drain queue" is **FALSE**: the boot pass runs **once per boot** (`daemon/api.py:2703` is its only call site) and a promote restarts the daemon **before** the gates, so a "next pass" never arrives and deferred candidates are **dropped** to StaleTaskRecovery (~10 min, `config.py:1821-1823`), losing the checkpoint resume. The task-less-orphan sweep was also missing. | **FOUR seams** + a per-seam deferred-candidates list in the new `daemon/services/promote_txn_hold.py` + a bounded **5s** re-consult + release through the **ORIGINAL** seams (checkpoint-resume path). Withdrawn options recorded: (a) 90s sweep-tick re-consult (couples two concerns, up to 90s latency), (c) one-shot delayed boot-pass re-run (double-processing risk). | §3.2 — §3.4, §3.3 |
| 17 | **N-b** — the release trigger had no named driver | The driver is a **lazily-created asyncio task** in `promote_txn_hold.py` (created on the first deferral, module-held, 5s tick, cancelled when the lists empty, registered on the ordered-shutdown path; a crash falls back to the bounded release timeout). | §3.4 |
| 18 | **BLOCKING 3** — the schema-version heal is a **whole-config replace** (`manager.py:2829`, `:2855-2861`) and would **drop the operator's env** (the pinned `CONTEXT7_API_URL` + KMS markers) on the first post-upgrade boot; C2's pre-check would then validate the default host | **ENV-PRESERVING MERGE** in the heal (row env wins over the rebuilt defaults; undeclared row keys retained). Rejected: disclosed-discard. C2's derivation reads the **effective post-heal** env. | §8.3 |
| 19 | **BLOCKING 4** — the C4 state-transition journal write is a daemon-side write to the promote-owned `state.json` and the write path is **unlocked** (`upgrade_journal.py:331-371`, discipline at `:352-353`); a stale-snapshot whole-document write would **drop `in_flight`** and make the A3 hold **release mid-promote** | **SUPPRESSION** while `promote_txn_live()`: the C4/init_guard emit is buffered in-memory (one entry per server+state, newest wins) and flushed on txn close or graceful shutdown, **never dropped**. Rejected: lock-acquire (the ceremony owns `journal_lock`; a daemon-side acquire is a new cross-process contention point). | §9.3 |
| 20 | **BLOCKING 2** — the B1 loop-lag AC had no number | **p95 event-loop scheduling lag ≤ 250ms AND max ≤ 500ms**, measured with a periodic heartbeat while R-1's concurrent burst runs. Rationale recorded verbatim. | §5.8 |
| 21 | **N-a** — D1a's lock expression was unresolvable as written (`saver_conn.lock` does not exist) | The resolvable form is **`checkpointer.raw_saver.lock.locked()`** (the retry proxy passes non-method attributes through unchanged, `checkpoint_adapter.py:190-195`, `:227-229`; `aio.py:43`/`:59`). A pin asserts it resolves, because an unresolvable expression **silently disarms the grace**. | §4.4 item 1 |
| 22 | **N-c**
### 10.2 Iteration-003 corrections recorded (mechanism change: `requirements.md` §4 is the SINGLE NORMATIVE HOME)

**The mechanism change.** Every cross-cutting predicate now lives in `requirements.md` §4.1–§4.7. This layer and `decisions.md` **REFERENCE** those predicates by explicit section anchor instead of **restating** them — a restated predicate that drifts from the normative one is a **fold failure**. The DESIGN REASONING (why, alternatives rejected, blast radius, reversal) stays in the two layers.

| # | Finding | Correction | Where |
|---|---|---|---|
| 23 | **[SAFETY] #1 — the never-green trigger was answered CONDITIONALLY ("no green observed AND no fair observation window") with a THIRD cap-reach outcome (ceremony truncation ⇒ `soak_deferred`, return 0)** | **DELETED.** The trigger is **UNCONDITIONAL**: cap-reach with `consecutive_green < 3` ⇒ return 1 ⇒ auto-rollback (+ quarantine); exactly **two** cap-reach outcomes, neither returns 0. The ceremony-truncation case is the **PRE-SOAK deferral** (`lib.sh:5088-5093`), not a cap-reach outcome. The withdrawn conditional is preserved in `decisions.md` §3b's alternatives table as a **REJECTED** alternative. A fairness diagnostic survives as a **DIAGNOSTIC-ONLY** field, pinned as not a success path. | §2.4, §3b B4, §4 CONSTRAINT 2 |
| 24 | **[SAFETY] #2 — the arch counter semantics omitted the GREEN reset-on-green and the livez-HARD-green HELD precondition, and a livez transient appeared in NEITHER the HELD nor the RESET list (so a livez outage became a HOLD)** | The restated bullets are **replaced by a pointer** to §4.2, so the two rules cannot be omitted again; **both legs added to the counter pack list** (§16). | §2.2, §16 |
| 25 | **[SAFETY] #3 — the A3 hold's liveness leg was proven fresh only for the SOAK, but seam 1 fires during the daemon-BOOT window** | The **BOOT-WINDOW leg** is added to §3.4 and to `decisions.md` §3c C3a/C3c, with the justification and `BOOT_LIVENESS_S = 120.0` s; a seam-1 timing case is added to the A3 hold pin. The predicate itself is §4.7's. | §3.4, §4.7 |
| 26 | **[SAFETY] #4 — the release driver's torn-read guard (a two-consecutive-false hysteresis) was named in the risk register and ABSENT from the design** | The **release hysteresis** is added to §3.4 and to `decisions.md` §3c; the register entry now **references §4.7** rather than owning the rule. | §3.4, §4.7 |
| 27 | **[SAFETY] #5 — D1a named two incompatible probes; the acquire-and-read variant SELF-DEADLOCKS** (the sentinel read takes the same non-reentrant `asyncio.Lock`) | **ONE mechanism pinned in BOTH layers: the non-blocking `.locked()` observation only.** The acquire-and-read variant is **DELETED**. A pin asserts the probe **never holds the saver lock across `aget_tuple`**. The resolution pin is kept (it is what proves the attribute exists on the retry-proxy surface). The probe's return-contract change (`Callable[[], bool]` ⇒ a `(bool, class)` pair) is stated in the **binding arch**. | §4.4 item 1, `decisions.md` §3d D1a |
| 28 | **#7 — the grace ceiling had two incompatible clocks (a valueless boot-relative `grace_abs_ceiling_s` vs. the observation-relative one)** | **ONE clock, ONE number** (§4.3's). The boot-relative ceiling is **deleted**; the relationship to the soak's absolute bound is stated. | §1.4, §2.3, §4.3 |
| 29 | **#8 — the foundations layer hard-ANDed boot-age + burst while the design authority requires class-driven generality; the 10-09 class was invisible** | **This position GOVERNS**: the AND is dropped; boot age / burst are **published context only**. The **2026-10-09 v0.18.5** prior art is now cited in both layers' context recaps. | §1.4, `decisions.md` §1 + §3a A1.2 |
| 30 | **#9 — C3's schema instruction contradicted itself and the AC carried a derived-base contract the G1 correction falsifies** | §8.3 carries the **G1-corrected** contract: `CONTEXT7_API_URL` = the **effective lever** (no default); `CONTEXT7_BASE_URL` = a **daemon-side MIRROR** (default `https://context7.com`) whose field description states it is **INERT to the package** (verified: `@upstash/context7-mcp` v4.4.4 `dist/lib/constants.js:7-10` hardcodes it) and which is used as the C2 derivation input. | §8.3, §7.2 |
| 31 | **N-B1 — premise 10 (the readiness probe's per-tick leak is "not supported by the code as written") contradicted the interim-efficacy disclosure ("still live", "correctly fires `pool_leak`")** | **Reconciled by naming the CONFIRMED mechanism**: the four code-confirmed leak **sites** + the readiness probe's **timeout-path connection retention**; the RCA's fingerprint attribution to the readiness probe is withdrawn (its SQL is `FROM task`). The interim-efficacy disclosure is **rebased on §4.4**: P1 is **ADVISORY** and cannot arm `pool_leak`, so a P1 leak surfaces as `probe_timeout` and the grace **engages** — correct per §4.4, and the reason the healthy-release rollback **can recur** for that signature is that the grace was never its mitigation. | `decisions.md` premise 10 + §3a INTERIM EFFICACY, §1.3 |
| 32 | **N-B2 — the `queue_freshness` non-goal's mitigation pointed at the ghost-waiter analysis scoped to P2, but that probe runs on P1** | Corrected: the probe runs on **P1** (`daemon/api.py:3779`) and the **P1 mechanism is NOT addressed by A4** (A4 sizes P2 only). | `decisions.md` §8, §4.5 |
| 33 | **N-B3 — the P1 leak clause cannot distinguish busy from leaking** | Reflected: P1 is **ADVISORY** per §4.4 (`overflow() > 0` is non-zero on a busy-healthy pool by design); P2 + the S9-scoped streak are the **oracle**. | §1.3, §4.5 |
| 34 | **N-B4 / N-B5 / N-B7 / N-B10 + Worker C anchor drift** | Fixed: the `is_available()` consult sites are `:579` / `:1130`; the sweep's defense is **two** implemented signals (`:2014-2016`), **not** three; the probe's `(bool, class)` return contract is stated in §4.4; the duplicated `lock_heartbeat` clause is gone; the heal anchor is `manager.py:2854-2866` (`:2775-2779` is the docstring), the warmup-pool consult IS `manager.py:3057` (`:3058` is the following `logger.debug`), the boot-sweep predicate is enforced at `boot_subprocess_sweep.py:472`, and the version-drift pin is `lib.sh:5204-5208` (`:4258` is the boot-grace comment). | §6.2, §4.4, both files |
| 35 | **N-A4 — the `mcp_set_env` supersession was not stamped at the decision record** | Stamped: `mcp_set_env` is **NOT closed in this lane** — routed defect, named owner (agent-tool-surface consistency lane). | `decisions.md` §3g G6 |
| 36 | **[SAFETY] final-fold B1 — §3.1 restated the hold predicate in narrowed form** (the boot-window leg absent, no §4.7 pointer inside §3.1), contradicting §4.7 and this file's own §3.4 / SAFETY #3; a developer implementing from §3.1 would build a hold that is silently inert at seam 1 (boot ages 60–120 s) and releases deferred resumes mid-promote | **Replaced with the full two-leg predicate**, citing §4.7 by anchor (§3.1). No redesign; the three bullets under §3.1 are unchanged. | §3.1 |

**N-d anchor reconciliation (iteration 003) — what was verified on disk.** Adopted: `warmup_pool.py:333-344` **is** the `except BaseException:` cleanup block (the approver is right; `:353-367` is the `acquire()` method, not a cleanup block — and that stale citation lives in `plan-overview.md:226`, outside this fold's authority, and is routed to its owner); the `sweep_once()` call is at `eligible_pending_sweep.py:399` (the approver is right; `:397` is the `while` line); `boot_subprocess_sweep.py:472` is the **enforced** predicate (the docstring is `:36-45`); `lib.sh:5204-5208` is the version-drift block (`:4258` is the boot-grace comment — note the shell source at `:5206` still cites `:4258` itself, an in-code stale reference); `base.py:222-223` (env-section set) and `:241-242` (env omission) are correct as we had them. **Ours right, approver's number off:** `QUEUE_PROBE_TIMEOUT_S` is at `daemon/services/readiness.py:59` (the approver's `:60` is the blank line) and the warmup-pool consult IS at `daemon/manager.py:3057` (the approver's `:3058` is the following `logger.debug`).

**N-d anchor reconciliation (approver numbers vs observed on disk).** Observed and adopted: `warmup_pool.py:333-344` **is** the `except BaseException:` cleanup block (the approver is right; `:353-367` is the `acquire()` method, not a cleanup block); the `sweep_once()` call is at `eligible_pending_sweep.py:399` (the approver is right; `:397` is the `while not self._stop_event.is_set():` line); `QUEUE_PROBE_TIMEOUT_S` is at `daemon/services/readiness.py:59` (**ours was right**; the approver's `:60` is the blank line); the env-section set is at `daemon/mcp/builtin_servers/base.py:222-223` and the env omission at `:241-242` (**ours were right**; the approver's `:224-227` does not match the `section == "env"` branch). The stale `:353-367` citation lives in **`plan-overview.md:226`**, which is outside this fold's edit authority and is **routed to its owner** rather than edited here.

**`cls().name` vs `cls.name` — NOT changed, on verified evidence.** The approver's N-d asked to change `cls().name` → `cls.name`. Executed against the real classes: `Context7ServerDefinition.name` is a **property object** (`base.py:27-30` is `@property @abstractmethod`), so `C7.name == "context7"` is **False** while `C7().name == "context7"` is **True**. `cls.name` would therefore make `is_quarantined(cls.name)` **never match** — the quarantine would never engage, **silently** disabling C1. The arch doc's existing `cls().name` (§6.2) is the verified-correct form and is retained unchanged; the observation is recorded here so the audit trail shows the instruction was considered and rejected on evidence, not overlooked.

## 11. New risks the plan missed (deduped; each carries its mitigation)

1. **`max_waiting=0` (unlimited waiters) under extreme burst** — monitor via `detail.pool_state.psyscopg.requests_waiting` (the pool-state field semantics are `requirements.md` §4.4's, P4 — **normative**); if unbounded growth appears, set a small `max_waiting` AND re-classify `PoolTimeout` as `pool_busy` in the §1.2 table.
2. **Time-to-quarantine without C2 = 3×60s = 180s** — closed by the `dns_nxdomain` threshold-1 ruling (§6.4); ship C1+C2 together regardless.
3. **Bounding the handshake without G1 converts an infinite hang into a periodic orphan** (a new npx child every 30s) — the bound and G1 ship in the same change (§5.4).
4. **B1's executor queue is safe only with C1** (submissions = spawn count) — phase-2 exit gate requires C1 landed or the dns threshold-1 rule active (§5.7).
5. **Scan ordering is also the teardown/reaper guarantee** — a scan landing after child death loses the pid and the scoped reaper has nothing to reap (§5.2).
6. **A module-level quarantine registry leaks across tests** — `reset_for_tests()` hook is mandatory (§6.1).
7. **`McpPoolConfig.health_check_timeout` is a dead knob** (`config.py:2900`; ping hard-coded 5.0 at `warmup_pool.py:624`) — wire or delete within phase 2.
8. **The 3 initialize retries sit inside the 60s budget** — with a 30s handshake the effective initialize budget is ~28s and the 3rd retry may never run; the comment must state the worst case so nobody widens the outer budget (§5.4).
9. **Inert `CONTEXT7_BASE_URL` false confidence** — mitigated by field-description documentation + the §7.2 derivation rule (§8.3).
10. **`_replenish` DEBUG-swallow hides two loops** (spawn-hang blocks `pool.warmup()`; handshake-fail re-spawns every 60s swallowed) — raise to WARNING + registry state + journal per transition (§5.5, §9.2).
11. **D1a lock-observation semantics** — the probe reads `checkpointer.raw_saver.lock.locked()` (**non-blocking**); the acquire-and-read variant is **DELETED** (it self-deadlocks against the same non-reentrant lock the sentinel read takes, §4.4 item 1). The residual library-behaviour question is the **probe-future cancellation** under in-flight cancellation — the **AC + the dedicated-connection fallback** (§4.4 item 2); the fallback is a **contention mitigation only**, never a detector.
12. **GraceTracker survives refresher restarts only on `app.state`** — a local variable resets the leak predicate mid-observation (§1.1).
13. **Older-gate/newer-daemon is safe by construction** (gate ignores the grace block ⇒ no extension ⇒ conservative) — pinned as the degraded-mode compat case, not left to chance.

## 12. Narrowings that preserved safety properties

Grace never renders healthy (503/status/components/reasons invariant) · leak class never eligible, disengages · unknown fails closed · holds bounded `MAX_HOLDS_PER_GREEN=2` (the bound is `requirements.md` §4.2's, P2 — **normative**) · extension spent-not-refunded, horizon never extended · A4 sizes P2 only, never claims to cure P1 · A3 hold is fail-open, covers only the FOUR auto-continue dispatchers, never human resumes· `max_waiting` left default (failure mode unchanged) · `detail.mcp` advisory only (readiness never gated on MCP) · `circuit_breaker.py` unmodified; `reset()` never called; re-arm replaces the instance · no new tool category (no pin ceremony) · `mcp_set_env` untouched (operator/BYOK route preserved) · A3 release re-enters the ORIGINAL seam (checkpoint path), never a cold dispatch · daemon-side journal writes SUPPRESSED during a live promote txn (buffered, flushed, never dropped) · C3's heal ENV-PRESERVING (operator pin + KMS markers survive the 2 → 3 bump)· day-one env mitigations byte-identical · no new tables/migrations · conservative soak block byte-identical.

## 13. Could not ground in code (verify at implementation)

1. `QueuePool.checkedout()/overflow()/size()` against the **installed** SQLAlchemy 2.0.48 (cited from impl.py:243/252/255).
2. Whether cancelling a task blocked in psycopg `pool.getconn` removes the internal waiter (§4.4) — pinned acceptance criterion decides; fallback specified.
3. Whether the pinned SDK's generator unwind completes under in-flight cancellation (G1) — anyio-timing-dependent; the wrapper-level guarantee is required regardless (§5.4).
4. `reconcile_pending_op` (`upgrade_journal.py:1959-1961`) — cited by the plan, consistent with surrounding readers, not independently re-read.
5. `journal_history_append` exact signature (§9.2).
6. The context7 package version the daemon actually spawns (cached copy inspected was v4.4.4) — verify the pinned spawn resolves the same source shape (decisions §9 Q5).
7. Measured `/proc` scan cost on the live host — the executor hop is justified by the O(N) shape, not a number.
8. psycopg_pool `timeout` kwarg verified against 3.3.1 in `uv.lock` — confirm the installed version at build time.

## 14. Config/knob SSOT summary

> **Normative source: requirements.md §4.6 (P6 — acceptance-criterion thresholds); §4.3 (P3 — grace ceiling); §4.5 (P5 — DNS thresholds); §4.4 (P4 — the leak oracle / breaker parameters).** The **VALUES** below are the same numbers — the normative home is §4; this table is an **index of where each knob lives**, not a second statement of the rules. The **placement** (which module owns the constant, and whether it is env-tunable) is this layer's.

| Knob | Value | Home | Env | Notes | Normative (requirements.md) |
|---|---|---|---|---|---|
| `limits.checkpoint_pool_max_size` | 10 (5–20) | `config.py` `LimitsConfig` | `LIMITS_CHECKPOINT_POOL_MAX_SIZE` | yaml `limits:` block | §4.6 (P6) |
| `limits.checkpoint_pool_timeout_s` | 5.0 (≥1.0) | `config.py` `LimitsConfig` | `LIMITS_CHECKPOINT_POOL_TIMEOUT_S` | ghost-waiter bound | §4.6 (P6) |
| `GRACE_HORIZON_S` / `GRACE_EXTENSION_CAP_S` / `MAX_HOLDS_PER_GREEN` | 120 / 180 / 2 | `lib.sh` beside `ADAPTIVE_SOAK_*` | — (not env-tunable first cut) | | §4.3 (P3) + §4.2 (P2) + §4.6 (P6) |
| `HANDSHAKE_WAIT_TIMEOUT_S` | 30.0 | `stdio_wrapper.py` module const | — | pin `< WARMUP_CREATE_BUDGET_S` | §4.6 (P6) |
| `WARMUP_CREATE_BUDGET_S` / `WARMUP_TOTAL_BUDGET_S` | 60.0 / 90.0 | `warmup_pool.py` module consts | — | budget opens BEFORE handshake | §4.6 (P6) |
| `DNS_PRECHECK_TIMEOUT_S` | 2.0 | `init_guard.py` module const | — | timeout ⇒ skip + proceed | §4.5 (P5) |
| init breaker `failure_threshold` / `recovery_timeout` | 3 / 300.0s | `init_guard.py` | — | dns_nxdomain = threshold-1 | §4.4 (P4) + §4.6 (P6) |
| `McpPoolConfig.health_check_timeout` | wire-or-delete | `config.py:2900` | existing | dead knob decision, phase 2 | §4.4 (P4) |

## 15. Shipping-order couplings (binding)

1. **Handshake bound + G1 together** — else periodic orphans (§11.3).
2. **B1 not without C1** — phase-2 exit gate (§11.4).
3. **C1+C2 together** — fail-fast value (§11.2), already closed by threshold-1 ruling.
4. **`_PROBE_STATUS` aliasing fix lands in the SAME change as the counter semantics** — an unreviewable counter poisons the new logic.
5. **`connection_manager.py` (phase 2) and `plane.py` (phase 3) join the touched-file lists.**

## 16. Pin index (new tests/packs referenced above)

`test_readiness_failure_class.py` (every class-table row, both arms; the **§4.2 counter legs**: the GREEN reset-on-green leg and the livez-HARD-green HELD precondition — **both** must be pinned, the second is what stops a livez outage launder into a HOLD; plus the P2-oracle / P1-advisory leak arms per §4.4, replacing the old synthetic-monotonic-`checkedout()` P1 arm which §4.4 no longer permits) · `test_readyz_grace_detail.py` (503 while `grace_active`; reasons non-empty with real component reason) · `tests/packs/promote_soak_grace_counter_unit_test.sh` (streams: green/held×3 never 3; soft-green livez + hard readyz no-increment; hard livez + soft readyz reset; no-`detail.grace` body = pre-change path; conservative mode v0.18-identical) · `test_stdio_wrapper_bounded_wait.py` (scan off-loop + registry row after `__aenter__`; 30s bound + `<60` invariant; G1 cancellation ⇒ inner `__aexit__` ran) · `test_mcp_warmup_loop_lag.py` (lag bound; BOTH starters started within window) · `test_mcp_child_liveness.py` (0 surviving children; pool-census delta 0) · `test_mcp_init_guard.py` (threshold-3 ⇒ OPEN ⇒ quarantined ⇒ no further spawns via sentinel; half-open admits exactly ONE; other servers unaffected) · `test_mcp_quarantine_registry.py` (4 consult sites incl. `cls().name` leg + plane super() leg; `reset_for_tests` hygiene) · `test_mcp_quarantine_spawn_seam.py` (cold seam refuses for a pre-quarantine-preloaded instance) · `test_upgrade_reasons_reader.py` (nested + fallback) · `test_readiness_mcp_independence.py` + `tests/integration/test_mcp_degrade_alone.py` (MCP never feeds readiness).

---

**Confidence:** HIGH on architecture shape, placement, and every seam (all code-cited). MEDIUM on four library-behavior items (§13.1–13.3, §13.6) — each carries a stated fallback or acceptance criterion that decides it empirically rather than by assumption. The recommendation would flip only if (a) cancelling a psycopg-waiting task provably strands waiters AND the dedicated-connection fallback is rejected, or (b) the pinned context7 package version changes its env contract (verify §13.6 first).
