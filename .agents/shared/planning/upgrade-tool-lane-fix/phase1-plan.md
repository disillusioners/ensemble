# Phase 1 Plan: Tool-Lane Live Promote — Python Subsystem Fix (v0.15.3)

**Worktree:** `/home/nea/ensemble-src` (branch `feature/upgrade-tool-lane-fix`, base `latest @ 139ba352` / v0.15.2)
**Workdir constraint:** READ-ONLY on source — ONLY write is `.agents/shared/planning/upgrade-tool-lane-fix/phase1-plan.md`
**Ground truth:** `.agents/shared/planning/upgrade-tool-lane-fix/research-findings-python.md` (branch-verified, drift-corrected). Citations below are from THAT file. Shell research is in `research-findings-shell-e2e.md` (read in full).
**Date:** 2026-09-26
**Author:** planner[v2] via plan-creation worker
**Status:** Ready for Review

---

## Objective

Make the tool-lane live promote **structurally functional** (arm → executor → gate → commit) with **loud failures** so that the 3-factor-verified arm:
1. actually reaches the gate (today: child exits 78 invisibly — see `research-findings-python.md` §1b).
2. is observed end-to-end (today: child exit invisible to daemon, no journal event, `upgrade_status` reports TERMINAL on a non-terminal state).
3. re-arms cleanly after a stale arm (today: ~20min starvation — `reconcile_pending_op` tool-entry-only).
4. closes F2 via the **registered-source nonce-echo attestation** the user ratified on 2026-09-26 (single ratified branch; ADR-017 tool-lane posture SUPERSEDED for verified arms only).

Single-sentence completion test: *"A 3-factor-verified live arm, with no F2 forge, completes arm → spawn → gate → commit → TERMINAL, with the in_flight journal stamped `f2_verified_closed: true` + `f2_verified_note: <source>:<run_id>`; an unverified arm exits 78 at `require_live_guard` and the failure is journaled."*

---

## Scope

### In Scope (P1 — Python subsystem ONLY)

- **ITEM 1 — ENABLER.** argv slot + env passthrough at the drain seam (`manager.py:3808-3815` + `manager.py:3795-3799`).
- **ITEM 2 — LOUD EXIT JOURNALING.** Async reaper task watching executor child; journals exit code + bounded `upgrade.log` tail (`manager.py:3823-3846` + `daemon/tools/upgrade_journal.py:1012-1037`).
- **ITEM 3 — ARM PREFLIGHT BEFORE NONCE BURN.** Verify child env contract + argv flag construction + scripts resolvability pre-lock or pre-burn (`upgrade_tools.py:2672-2688` slot).
- **ITEM 4 — RECONCILE SWEEP + PENDING_ACTIONS GC + REAPER QUEUE/WORKER.** Boot-time + periodic timer reusing `reconcile_pending_op` (`upgrade_journal.py:909-980`) + extending `_gc_pending_actions` (`:789-821`) to a scheduled call + **owns the reaper queue + worker** (consumed by Item 2). Pattern source: `daemon/services/job_lock_sweep.py:128-188` (per `research-findings-shell-e2e.md` §6(a)).
- **ITEM 5 — _TERMINAL_OUTCOME EVENT-CLASS FILTER.** Filter `:1021-1036` to `_TERMINAL_EVENTS` (`:890`); `nonce_consumed` label demoted to awaiting-executor / pending (`:1047`).
- **ITEM 7 — RATIFIED BRANCH.** Decision table; NO additional auth layer; ADR-017 tool-lane posture superseded for verified arms only. (No code change — policy record.)

### Out of Scope (and explicitly fenced)

- **P2 — shell side / e2e packs / residual ops gates.** Bundling validation (FL-23), 3 fresh ari cycles on bundled release (runbook §8.4(ii)(3)), promote.sh live-rung gates already in tree — separate workstream. Per `research-findings-shell-e2e.md` "VERIFIER-VERDICT FOLD-IN B", these are runbook / evidence obligations.
- **promote.sh argv case** (already handles `--f2-verified-closed` at `:72-77`; the unknown-flag trap `:79` stays dormant — zero promote.sh edit per §5(D) recommendation).
- **lib.sh F2 note logger** (already first-class at `:584-589`).
- **F2 unauth loopback forge closure** (jobs_crud.py:275-278 + messages.py:391 — separate commission; merge 4db90d74 explicitly leaves this open, see ADR-032 supersession note in `upgrade_journal.py:1041-1050`).
- **live restart refusal posture** (already enforced; the verified-arm passthrough does NOT bypass it — `ENSEMBLE_SELF_ENV` derivation unchanged).
- **job-source anti-forgery drift** (drifted to `job_queue.py:1256-1257 / :1993` per research §2d — out of scope for this commission; cited in risks for awareness).
- **Any change to `EXECUTOR_ENV_ALLOWLIST`** (ambient stays fenced — extras passthrough IS the seam).

---

## Phases (single phase; item-level sequencing)

P1 is one cohesive Python subsystem change with 5 implementation items + 1 ratified design decision. Internal sequencing is item-numbered to group work for a single implementer instance (same-subsystem work together; no mid-flight dispatch).

| Item | Name | Objective | Tasks | Coupling | Status |
|------|------|-----------|-------|----------|--------|
| 1 | ENABLER (argv + env passthrough) | Verified-arm reaches the gate | 4 | tight w/ Item 2 (same drain seam) | pending |
| 2 | LOUD EXIT JOURNALING | Child exit (incl. 78) journaled + visible | 5 | tight w/ Item 1 (same drain seam) | pending |
| 3 | ARM PREFLIGHT PRE-NONCE-BURN | Doomed arms refuse pre-burn | 4 | tight w/ Item 1 (same verified-arm predicate) | pending |
| 4 | RECONCILE SWEEP + PENDING_ACTIONS GC | Stale rows cleared boot+timer; live stale `r-20260926-100210-53ca` family prunable | 6 | independent (new periodic service) | pending |
| 5 | _TERMINAL_OUTCOME EVENT-CLASS FILTER | `nonce_consumed` no longer masquerades as TERMINAL | 2 | independent (one fn + label map) | pending |
| 7 | RATIFIED BRANCH (decision table) | Policy recorded; ADR hook minted | 2 | independent (doc + ADR) | pending |

### Coupling Map

|  | Item 1 | Item 2 | Item 3 | Item 4 | Item 5 | Item 7 |
|--|--------|--------|--------|--------|--------|--------|
| Item 1 | — | tight (drain seam) | tight (verified predicate) | independent | independent | independent |
| Item 2 | tight | — | independent | tight (reaper queue — Item 2 enqueues; worker lives in Item 4) | independent | independent |
| Item 3 | tight | independent | — | independent | independent | independent |
| Item 4 | independent | tight (reaper queue — Item 2 enqueues; worker lives in Item 4) | independent | — | independent | independent |
| Item 5 | independent | independent | independent | independent | — | independent |
| Item 7 | independent | independent | independent | independent | independent | — |

**Tight coupling detail.** Items 1, 2, 3 all touch `daemon/manager.py:3795-3846` (drain_pending_system_execution + spawn site) and the verified-arm predicate (`op.kind == "promote" AND op.nonce_consumed AND op.confirmed_by_human AND op.confirmed_source AND op.env == "live"`). **Helper home (R7ii — circular-import proof):** `is_verified_arm` and `_verified_arm_extras` are **DEFINED in `daemon/tools/upgrade_journal.py`**. Manager.py already imports that module as `_uj` (verified at the spawn site `_uj.spawn_executor`); both helpers consume `PendingOp` only and reference nothing from manager — no circular import risk. **MANDATORY shared helper `_verified_arm_extras(op) -> tuple[list[str], dict[str, str]]`** (name-frozen; tests pin the helper name) returning `(argv_extension, extra_env_extension)` — used across all 3 consumption sites (argv flag construction, extra_env construction, arm preflight) to prevent drift. The 5-conjunct predicate `is_verified_arm(op) -> bool` (public name, no underscore prefix per R7iii — also name-frozen; tested separately via truth table including `env=demo` row) gates the call: explicit `if is_verified_arm(op):` at every manager.py site — unverified path stays BYTE-IDENTICAL.

---

## Item-Level Design

### ITEM 1 — ENABLER (core)

**File change map (research-verified anchors):**

| Site | Current | Change |
|------|---------|--------|
| `manager.py:3795-3799` (extra_env dict) | hardcoded `INSTALL_DIR`, `PORT` only | Inside `if is_verified_arm(op):` block (explicit gating — unverified path stays BYTE-IDENTICAL): extend with `ENSEMBLE_UPGRADE_LIVE="1"` and `F2_VERIFIED_NOTE=f"{op.confirmed_source}:{op.run_id}"`. |
| `manager.py:3808-3815` (argv list literal for promote) | `["bash", promote.sh, env, "--version", str(spec.get("target"))]` | Inside `if is_verified_arm(op):` block (explicit gating — unverified path stays BYTE-IDENTICAL): extend with `"--f2-verified-closed"`. |
| `manager.py:3832` (read_pending_op — ALREADY read for owner stamp) | `op = _uj.read_pending_op(install_dir)` then `op.owner_pid = child_pid` at :3833-3837 | **MOVE the read BEFORE spawn** at :3823 (per task dispatch directive; preserves owner-stamp write). Use the loaded `op` for BOTH owner stamp AND argv/env construction. |
| **Pre-spawn guard (R2)** at `manager.py:3823` (spawn seam) | (no pre-spawn check today — spawn fires unconditionally if marker spec is present) | **2-line guard immediately before `spawn_executor`:** `if op is None: return _journal_orphan_refusal(spec, install_dir)`. When `read_pending_op` returns None (write-op failed, journal torn, post-recovery state): **refusal path** (do not spawn — child would be unverifiable + dangerous) + **journal event `"executor_orphaned"`** with payload `{spec_kind, install_dir, ts}` (emit-NOTHING ordinary-event style per `:380-383`; NOT in `_TERMINAL_EVENTS`). Pattern: `_journal_refusal_event` (`:1056+`) detail format `"<msg> (reason=<token>)"`; refusal token `executor-orphaned` (per-call-site string per D-FA2.2). The reaper queue is NOT enqueued (no pid to reap) — this is a clean skip, not a benign-detach. |

**Verified-arm predicate (M-11 — `op.env == "live"` adopted as 5th conjunct; safe-by-construction, not safe-by-coincidence):**

```python
def is_verified_arm(op: PendingOp) -> bool:
    return (
        op is not None
        and op.kind == "promote"
        and op.nonce_consumed
        and op.confirmed_by_human
        and bool(op.confirmed_source)
        and op.env == "live"  # M-11: safe-by-construction — env must be live
    )
```

**Helper home (R7ii):** defined in `daemon/tools/upgrade_journal.py` (alongside `_verified_arm_extras`). Manager.py calls it as `_uj.is_verified_arm(op)`; circular-import-proof — the helper consumes `PendingOp` only, no manager-side symbols.

**Truth-table includes `env=demo` row (returns False):**

| `op.kind` | `nonce_consumed` | `confirmed_by_human` | `confirmed_source` | `op.env` | result |
|-----------|-------------------|----------------------|--------------------|----------|--------|
| `"promote"` | True | True | `"my-discord-bot:123"` | `"live"` | **True** |
| `"promote"` | True | True | `"my-discord-bot:123"` | `"demo"` | **False** (env guard) |
| `"promote"` | True | True | `"my-discord-bot:123"` | `"dev"` | **False** (env guard) |
| `"promote"` | True | True | `"my-discord-bot:123"` | `"sandbox"` | **False** (env guard) |
| `"promote"` | True | True | `None` | `"live"` | **False** (no source) |
| `"promote"` | True | False | `"my-discord-bot:123"` | `"live"` | **False** (not confirmed) |
| `"promote"` | False | True | `"my-discord-bot:123"` | `"live"` | **False** (no nonce) |
| `"restart"` | True | True | `"my-discord-bot:123"` | `"live"` | **False** (not promote) |
| `None` | — | — | — | — | **False** (op None) |

**Unverified path = today's behavior exactly.** No flag appended, no env extras → child hits `require_live_guard` → exits 78 (now journaled per Item 2). F2 fence intact (verified at `upgrade_journal.py:988-991` allowlist + `:994-1005` executor_env; `:1003-1004` merges extras last and unconditionally — passthrough rides this, no allowlist widening).

**Promote.sh: zero edits.** The flag case `:72-77` already exists; the unknown-flag trap `:79` stays dormant. The F2 gate `:103-105` remains the only gate (env + argv pair). The `journal_mark_f2_verified` call `:198` reads `F2_VERIFIED_NOTE` from env via lib.sh `:584-589` — first-class already.

**Marker spec** (`upgrade_tools.py:2711-2723`): **NO change** — the drain already loads the pending_op (`:3832`), so it has `op.confirmed_source` + `op.run_id` directly. No spec extension.

**Test pins (sibling tests, zero existing pins flip):**

The 3 existing pins at `test_upgrade_tools.py:3388-3391` (argv equality), `test_upgrade_tools.py:3439-3444` (allowed-set), and `test_upgrade_journal.py:902` (extras set) **DO NOT FLIP and are NOT parametrized** — they stay exactly as-is (they ARE the unverified side, already green today). The verified side lands as **NEW SIBLING test functions standing alone** in their respective test files.

- New sibling test (verified side of argv): `test_promote_argv_includes_f2_flag_when_verified` — exact-list equality with `--f2-verified-closed` appended; sits NEXT to the existing unverified pin, does not modify it.
- New sibling test (verified side of allowed-set): `test_extra_env_allowed_when_verified_includes_live_and_note` — `allowed = EXECUTOR_ENV_ALLOWLIST ∪ {"INSTALL_DIR","PORT","ENSEMBLE_UPGRADE_LIVE","F2_VERIFIED_NOTE"} ∪ PG-prefixed-os.environ`; sits NEXT to the existing unverified pin.
- New sibling test (verified side of extras purity): `test_executor_env_extras_passthrough_when_verified` — IF F2_VERIFIED_NOTE flows through `executor_env` in the purity test's purview; sits NEXT to the existing extras pin (or in a new test if the purity test does not cover this).

**New tests for Item-1 pin package (W2):**
- `test_spawn_executor_has_sole_production_caller_manager_drain` — **SOLE-CALLER PIN**: AST/source-grep `daemon/tools/upgrade_journal.py` for `_uj.spawn_executor(` / `uj.spawn_executor(` callers; assert exactly 1 production caller = `manager.py:3823` (drain_pending_system_execution). Guards new unguarded spawn sites. Test fails if any new call site appears outside manager.py drain.
- `test_is_verified_arm_truth_table` — parameterized over the 9 rows of the truth table above; each row asserts the expected `True`/`False`. Includes the `env=demo` row (returns `False`) and the `op=None` row (returns `False`).
- `test_verified_arm_extras_helper_returns_empty_for_unverified` — direct test of `_verified_arm_extras(op)` returning `([], {})` for any unverified predicate result; covers `env=demo`, `env=dev`, `op=None`, `kind="restart"`, `nonce_consumed=False`, etc.
- `test_verified_arm_extras_helper_returns_expected_for_verified` — direct test of `_verified_arm_extras(op)` returning `(["--f2-verified-closed"], {"ENSEMBLE_UPGRADE_LIVE": "1", "F2_VERIFIED_NOTE": "<source>:<run_id>"})` for the canonical verified row.
- `test_verified_arm_extras_helper_name_frozen` — `inspect.getsource` confirms the helper symbol is `_verified_arm_extras` (not e.g. `_make_verified_args`, `_build_arm_extras`, etc.); grep guard.

**Gating directive (S2):** every manager.py consumption site uses explicit `if is_verified_arm(op):` — the unverified path stays BYTE-IDENTICAL (no `else` branch needed; the helper returns `([], {})` and the code skips extension via the gate).

### ITEM 2 — LOUD EXECUTOR-EXIT JOURNALING (spawn seam → reaper queue enqueue)

**Architecture (C3c):** Item 2's reaper logic is **owned by Item-4's `UpgradeJournalSweepService`**. Item 2's scope = the **spawn seam enqueue** + the journal-history-append contract. The worker that awaits the child exit lives in Item 4.

**Pattern source (per `research-findings-shell-e2e.md` §6(b) + Item-2's shield-wrapping reference):**
- `instance_messaging.py:1572-1597` — **drain shield-wrapping pattern** at exact turn-end: the armed executor handoff is `asyncio.shield`-wrapped so a [failure] leaves the journal pending_op as the fallback. Item 2's spawn-seam enqueue sits inside this same shielded path (verified by import audit — no claim/dispatch/notify code touched).
- `manager.py:4148-4149` — `await asyncio.wait_for(asyncio.shield(task), timeout=timeout)` — the bounded wait pattern; reused by Item 4's `_reaper_worker` (`asyncio.wait_for(asyncio.shield(...), timeout=660)`).
- `message_processing_pipeline.py:83,587,850,972-979` — `asyncio.create_task(asyncio.shield(asyncio.to_thread(...)))` — the reaper convention; Item 4 follows this shape for `_waitpid_blocking`.
- TEST GOTCHA (R7ii-adjacent — applies here too): patch the spawn SEAM (`daemon.tools.upgrade_journal.spawn_executor`), NEVER `subprocess.Popen` — shared module; patching Popen breaks the test's own subprocess use.

**File change map:**

| Site | Current | Change |
|------|---------|--------|
| `manager.py:3823` (`_uj.spawn_executor(argv, install_dir, extra_env)` — sync, returns pid) | pid-only; no wait, no exit capture | Capture pid + minimal argv-summary (list of argv items, truncated to 80 chars each to bound the reaper queue entry); enqueue `(pid, argv_summary, install_dir, run_id)` into the sweep-service-owned reaper queue (a thread-safe `asyncio.Queue` injected via constructor or `app.state`). **NO wait, NO exit capture at the spawn site** — the sweep-service worker (Item 4) consumes and awaits. |
| `manager.py:3843-3846` (catch-all warning — "never raises") | logs warning, leaves marker consumed | Augment with `try: sweep_service.enqueue_reaper(pid, argv_summary, install_dir, run_id) except Exception: log warning; do not raise` — keeps the never-raises contract; reaper enqueue failure is logged but does not fail the arm. |
| `upgrade_journal.py:301-316` (`journal_history_append`) | additive durable write | Add a new event class `"executor_exit"` with payload `{exit_code, run_id, log_tail, ts}` (journaled by the sweep-service worker on successful wait). Add a second event class `"executor_still_running"` with payload `{pid, run_id, ts, timeout_s}` (journaled by the sweep-service worker on timeout per benign-detach semantics). |
| `daemon/services/<new>` (Item 4's `UpgradeJournalSweepService` reaper queue + worker) | does not exist | New: `reaper_queue: asyncio.Queue[ReaperJob]` + `reaper_worker_task: asyncio.Task` consuming items, awaiting child exit, journaling events (full design in Item 4). |

**Gating directive (S2):** the spawn-seam enqueue at `manager.py:3823` is unconditional (every arm — verified or unverified — gets reaped; an unverified arm that exits 78 is the most useful case for observability). No `if is_verified_arm(op):` here.

**Event classes:**
- `"executor_exit"` — child exited within timeout; payload `{exit_code, run_id, log_tail, ts}`. Emit-NOTHING per `:380-383` ordinary-event style (observability, NOT a lifecycle class; NOT added to `_TERMINAL_EVENTS` :890).
- `"executor_still_running"` — timeout fired before child exit; payload `{pid, run_id, ts, timeout_s}`. Same emit-NOTHING style.

**Log tail.** Bound at 4KB or last 40 lines (whichever smaller). Read from `data/upgrade.log` via `upgrade_journal.py`'s log path resolution (research-verified: the file is parent-created before Popen at `:1024-1030`). Never `O(n)` over the whole file — `seek -4KB` + truncate-at-newline.

**Daemon-restart durability:** The reaper queue is in-memory; if the daemon restarts mid-promote, the queue is lost (acceptable — the child is in its own process group via `start_new_session=True` at `:1034`, reaped by the OS). Boot-time sweep (Item 4) clears the journal. The user observation is "promote hung at arm" → operator investigates via upgrade.log (now visible thanks to the `"executor_still_running"` event).

### ITEM 3 — ARM PREFLIGHT BEFORE NONCE BURN

**File change map:**

| Site | Current | Change |
|------|---------|--------|
| `upgrade_tools.py:2672-2676` (run_id carry) | `run_id = confirmed_action.run_id if confirmed_action else mint_run_id()` | **Insert preflight block BEFORE this line.** Preflight does NOT depend on run_id; it's a static check. |
| `upgrade_tools.py:2677` (lock acquire) | lock acquired THEN burn :2685-2688 | Preflight runs BEFORE :2677 (cheaper: no lock held while we evaluate). |
| `upgrade_tools.py:2685-2688` (nonce burn) | `uj.consume_pending_action(install_dir, confirmed_action, confirmed_msg_id)` | Already after-lock (correct). Preflight ensures burn never happens for a doomed arm. |

**Preflight checks (each = one refusal token):**

1. `scripts_resolvable` — `scripts_dir` from spec OR `_resolve_scripts_dir` (per `research-findings-shell-e2e.md` "Scripts-bundling validation" §1). Refusal token: `preflight-scripts-unresolvable`.
2. `argv_construction_works` — call the shared `is_verified_arm` predicate (Item 1) with a synthetic `PendingOp` populated from confirmed_action to confirm the argv/env construction DOES NOT raise (e.g. `op.confirmed_source` must be a string; `op.run_id` must be mintable format). Refusal token: `preflight-argv-unconstructable`.
3. `f2_flag_present_in_parsed_argv` — invoke `promote.sh --help` or a dry parse via `bash -n` to confirm the argv list is syntactically valid for promote.sh. **Cheaper alternative:** regex-check argv against the known flag set from `promote.sh:64-80` (no `--f2-verified-closed`-adjacent typo). Refusal token: `preflight-argv-malformed`.

**Refusal format** — `"<msg> (reason=<token>)"` per `_journal_refusal_event` (`:1056+`); detail format `:1090-1093`. Token style: per-call-site string (no central enum). Pin test: regex-equality on `reason=<token>` fragment per D-FA2.2.

**Order constraint:** preflight BEFORE `uj.consume_pending_action` so a refused preflight does NOT burn the nonce. The arm returns `pipeline-busy` (or a new `arm-preflight-refused:<token>` token — confirm with caller) with no journal mutation.

**NOTE** — burn is at `:2685-2688` (research §2c). Preflight refuses BEFORE `:2677` (lock acquire) OR between `:2677` and `:2685-2688` (after-lock, pre-burn) — recommend between (lock held = correct serialization; refuses still return cleanly without burning).

### ITEM 4 — RECONCILE SWEEP + PENDING_ACTIONS GC + REAPER QUEUE/WORKER

**File change map:**

| Site | Current | Change |
|------|---------|--------|
| `upgrade_journal.py:909-980` (`reconcile_pending_op`) | `def reconcile_pending_op(install_dir: Path) -> str \| None` — never-raises, READ-FIRST :915-917, returns None if op None or kind=="restart" or in_flight live, closes on terminal event ≥ armed_at, expiry-closes past `expires_at + RECONCILE_GRACE_S` | **NO change** — this is the SINGLE source. The sweep calls it. |
| `upgrade_journal.py:789-821` (`_gc_pending_actions`) | write-triggered (called from `:831` `store_pending_action`, `:871` `consume_pending_action` only) | **Extend to a scheduled callable** — extract the GC core into a public `gc_pending_actions(install_dir: Path, keep_run_id: str | None = None) -> int` returning pruned-count; `_gc_pending_actions` becomes its existing internal caller (write-triggered) PLUS the sweep. |
| `daemon/api.py:845-868` (`JobLockSweepService` lifespan pattern) | ALWAYS-ON, default interval 90s, `ServicesConfig.job_lock_sweep_interval_seconds Field(ge=1)` fail-fast | **NEW service**: `UpgradeJournalSweepService(install_dir: Path, reconcile_interval_seconds: int, gc_interval_seconds: int, reaper_timeout_seconds: int = 660)` — same pattern. |
| `daemon/services/__init__.py` (or wherever sweep services are registered) | — | Register new service class. |
| `daemon/api.py:1847` reference (sibling periodic services) | — | Mirror `tmp_image_cleanup` registration style. |
| `daemon/services/<new>` (reaper queue + worker) | does not exist | New: `reaper_queue: asyncio.Queue[ReaperJob]` + `reaper_worker_task` (single asyncio task per service instance) + `enqueue_reaper(pid, argv_summary, install_dir, run_id)` method (consumed by Item 2's spawn seam). Worker: `asyncio.shield(asyncio.to_thread(os.waitpid, pid, 0))` wrapped in `asyncio.wait_for(..., timeout=660)` — benign-detach on timeout (NO kill, NO fail). |

**Pattern source** (per `research-findings-shell-e2e.md` §6(a) verbatim):
- `JobLockSweepService` (daemon/services/job_lock_sweep.py:128-188 — `__init__` :128, `start` :144, `stop` :168, `_run` :222) — ALWAYS-ON, no kill-switch, HARD POLICY.
- Default interval 90s (match `job_lock_sweep_interval_seconds`).
- `task.cancel()` + CancelledError handler on shutdown.
- Constructed in `api.py` lifespan, `.start()`, stored `app.state.<service>`, shutdown at `api.py:1864`.

**Reaper queue + worker design (FM-11 compliant — sweep-service-owned):**
```python
# In UpgradeJournalSweepService (daemon/services/<new>)
@dataclass(frozen=True)
class ReaperJob:
    pid: int
    argv_summary: tuple[str, ...]  # bounded, truncated per item
    install_dir: Path
    run_id: str

class UpgradeJournalSweepService:
    def __init__(
        self,
        install_dir: Path,
        *,
        reconcile_interval_seconds: int = 90,
        gc_interval_seconds: int = 90,
        reaper_timeout_seconds: int = 660,  # C2: livez 60 + readyz 120 + soak 300 + overhead ≈ 490s minimum → 660s headroom
    ):
        self._install_dir = install_dir
        self._reaper_timeout_s = max(60, int(reaper_timeout_seconds))
        self._reaper_queue: asyncio.Queue[ReaperJob] = asyncio.Queue()
        self._reaper_worker_task: asyncio.Task[None] | None = None

    def enqueue_reaper(self, pid: int, argv_summary: list[str], install_dir: Path, run_id: str) -> None:
        """Spawn-seam entry point (Item 2 calls this). Bounded argv_summary ≤ 80 chars/item."""
        self._reaper_queue.put_nowait(ReaperJob(
            pid=pid,
            argv_summary=tuple(s[:80] for s in argv_summary),
            install_dir=install_dir,
            run_id=run_id,
        ))

    async def _reaper_worker(self) -> None:
        """Consume reaper_queue. Await child exit with timeout. Benign-detach on timeout.
        NO kill, NO fail — child is in its own process group, OS will reap it eventually.
        """
        while True:
            job = await self._reaper_queue.get()
            try:
                # FM-11: shield inside async def, never await a cancelled task.
                # Use a thread to run blocking os.waitpid (Popen children are sync-spawned).
                exit_code = await asyncio.wait_for(
                    asyncio.shield(
                        asyncio.to_thread(self._waitpid_blocking, job.pid)
                    ),
                    timeout=self._reaper_timeout_s,
                )
                await self._journal_executor_exit(job, exit_code)
            except asyncio.TimeoutError:
                # C2 BENIGN-DETACH: do NOT kill, do NOT fail. Detach the child.
                await self._journal_executor_still_running(job, self._reaper_timeout_s)
                # Child is in its own process group (start_new_session=True at spawn_executor :1034).
                # The OS will reap it; we do NOT track it further.
                continue
            except ChildProcessError:
                # Popen died before we got to it (race) — journal + continue
                await self._journal_executor_exit(job, exit_code=-1)
                continue
            except Exception as e:
                await self._journal_executor_error(job, e)
                continue

    async def _journal_executor_exit(self, job: ReaperJob, exit_code: int) -> None:
        """R1: wrap journal_write in try/except OSError — log WARNING with run_id + pid + exception.
        NO retry (journal_write raises per `upgrade_journal.py:276-283`; retry could mask I/O faults
        and create journal-write storms). Fall-through: the reaper loop continues to the next job.
        """
        try:
            self._journal_history_append(
                event="executor_exit", run_id=job.run_id,
                exit_code=exit_code, pid=job.pid,
                argv_summary=list(job.argv_summary), ts=_now_iso(),
            )
        except OSError as e:
            logger.warning(
                f"UpgradeJournalSweepService: journal_write failed for "
                f"executor_exit (run_id={job.run_id} pid={job.pid} exit_code={exit_code}): "
                f"{type(e).__name__}: {e} — no retry, continuing reaper loop"
            )
            return

    async def _journal_executor_still_running(self, job: ReaperJob, timeout_s: int) -> None:
        """R1: same OSError-wrapping contract — log WARNING, NO retry, continue reaper loop."""
        try:
            self._journal_history_append(
                event="executor_still_running", run_id=job.run_id,
                pid=job.pid, timeout_s=timeout_s, ts=_now_iso(),
            )
        except OSError as e:
            logger.warning(
                f"UpgradeJournalSweepService: journal_write failed for "
                f"executor_still_running (run_id={job.run_id} pid={job.pid} timeout_s={timeout_s}): "
                f"{type(e).__name__}: {e} — no retry, continuing reaper loop"
            )
            return

    async def _journal_executor_error(self, job: ReaperJob, exc: BaseException) -> None:
        """R1: same OSError-wrapping contract — log WARNING with the original exception context, NO retry."""
        try:
            self._journal_history_append(
                event="executor_error", run_id=job.run_id,
                pid=job.pid, error_type=type(exc).__name__,
                error_msg=str(exc), ts=_now_iso(),
            )
        except OSError as e:
            logger.warning(
                f"UpgradeJournalSweepService: journal_write failed for "
                f"executor_error (run_id={job.run_id} pid={job.pid} original_exc={type(exc).__name__}): "
                f"{type(e).__name__}: {e} — no retry, continuing reaper loop"
            )
            return
```

**Reaper timeout = 660s default (C2):** livez 60s + readyz 120s + soak 300s + overhead ≈ 490s minimum observed for a live promote; **660s provides headroom** above that floor. Configurable via `ServicesConfig.upgrade_journal_reaper_timeout_seconds Field(ge=60)`. Floor `ge=60` rejects nonsensical values < 1 minute.

**Benign-detach semantics (C2):** at timeout the worker does NOT kill the child, does NOT raise, does NOT fail the sweep loop. It journals an `"executor_still_running"` event and moves on to the next queue item. The child is in its own process group (`start_new_session=True`); the OS reaps it on eventual exit. The journal entry is the audit trail — operators can correlate `executor_still_running` against the live `data/upgrade.log` and either let the child complete or escalate.

**Lock safety.** Per research §4 (lock is NOT taken by `reconcile_pending_op` itself): the sweep MUST first check `in_flight is a live dict` (`:926-927` already returns None in that case). ADDITIONAL sweep-side guard (proposed): if the pending_op `owner_kind == "executor"` and `owner_pid > 0`, use `os.kill(pid, 0)` + a **TIME-BOUND predicate** (e.g. `os.kill(pid, 0) succeeds AND owner_heartbeat_at within SWEEP_STALENESS_WINDOW_S` — bounded staleness, NOT unbounded process-existence) — refuse to sweep while the executor child is alive-recent. The time bound is the load-bearing check; `kill(pid, 0)` alone is not (PIDs recycle across long-lived daemons). (GNU/BSD consistency: `os.kill(pid, 0)` is portable; the time-bound predicate uses POSIX ISO timestamps.)

**Boot-time call.** In `api.py` lifespan AFTER `_boot_db_preflight()` (per `run_app.py` DR-1 seam per `daemon/__main__.py:104` + `:212`) — call `reconcile_pending_op(install_dir)` once before the periodic loop starts. This is the launcher's "boot-sweep fallback" that `manager.py:3846` references but never invokes today (the open defect).

**PendingActions GC expansion.** Today `_gc_pending_actions` is only called from `store_pending_action` + `consume_pending_action` (per `research-findings-shell-e2e.md` "EXTRA ASK 2" quote). Refactor: extract core to public `gc_pending_actions(install_dir, keep_run_id=None) -> int`; both write-triggered callers and the new sweep call the public fn. Returns pruned count for SSE/log visibility.

**Test plan:**
- New test: `test_boot_sweep_clears_stale_pending_op` — fixture writes a journal with armed pending_op + no in_flight + past `expires_at + RECONCILE_GRACE_S`; call boot sweep; assert op cleared.
- New test: `test_periodic_sweep_skips_live_executor` — fixture writes a journal with armed pending_op + `owner_pid=<alive>`; call sweep; assert op NOT cleared; assert `os.kill(pid, 0)` was issued.
- New test: `test_pending_actions_gc_prunes_expired_unconsumed` — fixture writes a journal with one consumed (kept — audit), one expired-unconsumed (pruned), one unexpired-unconsumed (kept); call `gc_pending_actions(install_dir, keep_run_id=None)`; assert pruned count == 1; assert remaining map == 2 entries.
- New test: `test_pending_actions_gc_keep_run_id_exemption` — sweep passes `keep_run_id=None` (NOT from arm paths); direct call with explicit `keep_run_id=<in_flight>` preserves the row; confirms the helper's exemption is operator-driven, not arm-path-driven.
- Existing test stays green: `test_pending_actions_gc_keeps_unknown_shapes` (per research §789-821 "fail-safe: GC only deletes what it can prove is dead").
- New reaper test: `test_reaper_journals_exit_code_on_child_exit_78` — patch `daemon.tools.upgrade_journal.spawn_executor` seam (NOT `subprocess.Popen` — P2.2 testing gotcha); induce exit 78 via mock; assert journal event `executor_exit` with exit_code=78 + log_tail length ≤ 4096.
- New reaper test: `test_reaper_benign_detaches_and_journals_executor_still_running_on_timeout` — mock child that never exits; enqueue a ReaperJob with a tiny `reaper_timeout_seconds=1`; assert worker returns within ~1s, journals `"executor_still_running"` event, does NOT raise, does NOT fail the worker loop. Confirms benign-detach semantics (C2).
- New reaper test: `test_reaper_default_timeout_is_660s` — default-constructed `UpgradeJournalSweepService` exposes `reaper_timeout_seconds == 660`; `ServicesConfig.upgrade_journal_reaper_timeout_seconds` `Field(ge=60)` rejects `<60` at boot.

### ITEM 5 — _TERMINAL_OUTCOME EVENT-CLASS FILTER

**File change map:**

| Site | Current | Change |
|------|---------|--------|
| `upgrade_tools.py:1021-1036` (`_terminal_outcome`) | `for entry in reversed(history): if isinstance(entry, dict) and entry.get("event"): return str(entry["event"]), entry` — last of ANY type | Filter to `_TERMINAL_EVENTS = ("commit", "rollback", "halt", "sweep_rollback", "sweep", "quarantine")` (`:890`). Walk reversed; return first match. **NO match** (still-armed) → return `(None, None)`. |
| `upgrade_tools.py:1039-1048` (`_OUTCOME_LABELS`) | `nonce_consumed → "live-confirmation nonce consumed"` at `:1047` | Replace with `"nonce_consumed": "awaiting executor (pending)"`. Update consumers that expect the old label — search for `"live-confirmation nonce consumed"` to find consumers (likely `status.sh:88-90` + `tests/test_release_journal.sh`). |

**New label:** `awaiting executor (pending)` — `nonce_consumed` is a transition event (arm completed, drain not yet run OR drain ran but child not yet exited). The user's mental model: "I armed the upgrade, it's queued for the executor."

**Test plan:**
- New test: `test_terminal_outcome_filters_nonce_consumed` — fixture writes history `[commit, rollback, nonce_consumed]`; assert outcome == `commit` (latest terminal wins, not `nonce_consumed`).
- New test: `test_terminal_outcome_returns_none_when_armed` — fixture writes history `[nonce_consumed]` only; assert outcome == `(None, None)`.
- New test: `test_outcome_label_nonce_consumed_awaiting` — fixture writes history with nonce_consumed; assert label lookup yields `"awaiting executor (pending)"`.
- Existing tests stay green: anything that pinned `"live-confirmation nonce consumed"` must be updated to `"awaiting executor (pending)"`.

### ITEM 7 — RATIFIED BRANCH (decision table)

**No code change.** Policy record only.

#### Decision Table

| Q | Verdict | Rationale |
|---|---------|-----------|
| **Q1.** Does merge 4db90d74 (registry-backed `classify_user_origin` at `upgrade_journal.py:1130-1202`) close F2? | **NO** | The unauth loopback forge lane remains open: `jobs_crud.py:275-278` (POST /jobs body.source pass-through) and `messages.py:391` (source="api" stamp). ADR-032's textual disclaimer (upgrade_journal.py:1060-1073) is explicit: anti-forgery structural at the stamp site does NOT close F2 loopback forge. (Per `research-findings-shell-e2e.md` "EXTRA ASK 1 (i) Verifier-verdict evidence".) |
| **Q2.** Is the live rung otherwise policy-gated for live rung promotes (user-executed-only, PRE-LIVE checklist, ADR-017)? | **YES** | Runbook §9 (per `research-findings-shell-e2e.md` "EXTRA ASK 1"): "the live rung remains USER-EXECUTED — automation stops at demo permanently (ADR-017); promotion-ladder.md S4–S6 are USER rows." Four-part PRE-LIVE checklist = runbook §8.4(ii) :484 + §9 ledger + f2 gate + user-executed. |
| **User ratification 2026-09-26 (applied to TOOL LANE ONLY):** Does the 3-factor-verified arm (user-confirmed + nonce via registered chat source + nonce/action binding) constitute F2-equivalent attestation for the tool-lane live promote? | **YES — RATIFIED** | User nonce-echo via registered source = accepted attestation; the 3-factor arm ceremony itself is the attestation source; NO additional auth layer; rationale "feature-first, revisit if threat model changes." (Per `research-findings-shell-e2e.md` "EXTRA ASK 1 (ii)" + project_history 2026-09-26 + critical note "POLICY RATIFIED".) |
| **[COLLAPSED — superseded by user ratification 2026-09-26]** Alternative: add an extra auth layer (HMAC, signed challenge, or extra factor) on the tool-lane live promote arm. | **COLLAPSED** | Superseded by the user ratification above. Rationale preserved: feature-first; revisit if threat model changes. ADR-017 tool-lane enforcement posture is superseded FOR VERIFIED ARMS ONLY (user-confirmed + nonce via registered chat source + nonce/action binding). |

**ADR mint is OUT OF SCOPE for P1** — P2 owns ADR-035 per `research-findings-shell-e2e.md` §"EXTRA ASK 1" (P2 verifies no higher number first; cite research §8 caveat). P1 cites the recommended ADR-035 content here for P2 to mint.
- File: `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`
- Number: **ADR-035** (highest minted = ADR-034 per `research-findings-python.md` §8; verify before minting).
- Format template: ADR-017 `:27-36` (Context. → Options. → Decision. → Recommended default / If the user picks otherwise.).
- Content: cite ADR-017 as the policy superseded for the tool lane; cite user-ratified 2026-09-26; cite the verifier-verdict (Q1 NO + Q2 YES); revisit anchor = ADR-017 + F2 fences (executor env allowlist + --f2-verified-closed + ledger §9).
- Style: full `## ADR-035` section after the ADR-032 block or appended after Standing Rulings (`:243`).

---

## Test Plan (two-sided contract matrix)

### Two-sided contract (matrix)

| Test site | Pin style | Unverified side (TODAY + POST) | Verified side (NEW SIBLING) | Action |
|-----------|-----------|-------------------------------|----------------------------|--------|
| `test_upgrade_tools.py:3388-3391` | argv exact-list equality | `["bash", promote.sh, env, "--version", "1.2.3"]` | append `"--f2-verified-closed"` | **STAYS GREEN** — existing pin IS the unverified side; new sibling `test_promote_argv_includes_f2_flag_when_verified` carries the verified case |
| `test_upgrade_tools.py:3439-3444` | allowed-set subset | `{allowlist} ∪ {INSTALL_DIR, PORT} ∪ PG-prefix` | append `{ENSEMBLE_UPGRADE_LIVE, F2_VERIFIED_NOTE}` to allowed | **STAYS GREEN** — existing pin IS the unverified side; new sibling `test_extra_env_allowed_when_verified_includes_live_and_note` carries the verified case |
| `test_upgrade_journal.py:902` | extras set (allowlist purity) | extras set = `{RUN_ID}` | extras set = `{RUN_ID, F2_VERIFIED_NOTE}` (if in purview) | **STAYS GREEN** — existing pin IS the unverified side; new sibling `test_executor_env_extras_passthrough_when_verified` (or in-place addition if purity test covers it) |
| `test_upgrade_journal.py:935 / :962` | poison strip | `setenv ENSEMBLE_UPGRADE_LIVE=1` → child env must NOT have it | unchanged (unverified path still strips ambient) | **STAYS GREEN** |
| `test_upgrade_tools.py:3423-3434` | poison strip | `setenv ENSEMBLE_UPGRADE_LIVE=1` → composed-exclusion | unchanged | **STAYS GREEN** |
| `test_release_journal.sh:188 / :200` | string-contains on promote output | `--f2-verified-closed` refusal/proceed text | unchanged (gate text unchanged) | **STAYS GREEN** |

**C3b constraint:** no existing pin is flipped, parameterized, or otherwise mutated. The verified side lands as new sibling test functions standing alone.

### New tests (per item)

| Item | New test | Outcome |
|------|----------|---------|
| 1 | `test_promote_argv_includes_f2_flag_when_verified` | SIBLING to existing `:3388-3391` pin (unverified); verified case standalone; exact-list equality with `--f2-verified-closed` appended |
| 1 | `test_extra_env_allowed_when_verified_includes_live_and_note` | SIBLING to existing `:3439-3444` pin; verified allowed-set includes `{ENSEMBLE_UPGRADE_LIVE, F2_VERIFIED_NOTE}` |
| 1 | `test_executor_env_extras_passthrough_when_verified` | SIBLING to existing `:902` extras-set pin (if applicable); verified extras passthrough |
| 1 | `test_spawn_executor_has_sole_production_caller_manager_drain` | **SOLE-CALLER PIN**: AST/source-grep `daemon.tools.upgrade_journal.spawn_executor(` callers across the daemon package; assert exactly 1 production caller = `manager.py:3823` (drain_pending_system_execution). Fails if any new call site appears outside manager.py drain. |
| 1 | `test_is_verified_arm_truth_table` | Parameterized over the 9 rows of the truth table (incl. `env=demo` → False, `op=None` → False); each row asserts expected `True`/`False`. **Pin name-frozen to `is_verified_arm`.** |
| 1 | `test_verified_arm_extras_helper_returns_empty_for_unverified` | Direct test of `_verified_arm_extras(op)` returning `([], {})` for any unverified predicate result; covers `env=demo`, `env=dev`, `op=None`, `kind="restart"`, `nonce_consumed=False`, etc. **Pin name-frozen to `_verified_arm_extras`.** |
| 1 | `test_verified_arm_extras_helper_returns_expected_for_verified` | Direct test of `_verified_arm_extras(op)` returning `(["--f2-verified-closed"], {"ENSEMBLE_UPGRADE_LIVE": "1", "F2_VERIFIED_NOTE": "<source>:<run_id>"})` for canonical verified row. |
| 1 | `test_verified_arm_extras_helper_name_frozen` | `inspect.getsource` confirms helper symbol is `_verified_arm_extras`; grep guard. |
| 2 | `test_spawn_seam_enqueues_into_sweep_reaper_queue` | Patch `daemon.tools.upgrade_journal.spawn_executor` seam (NOT `subprocess.Popen` — P2.2 gotcha); induce any exit (verified or unverified); assert the manager.py spawn site enqueues `(pid, argv_summary, install_dir, run_id)` into the sweep service's `reaper_queue`. |
| 4 | `test_reaper_journals_exit_code_on_child_exit_78` | Patch the spawn SEAM; induce exit 78 via mock; assert journal event `executor_exit` with exit_code=78 + log_tail length ≤ 4096. |
| 4 | `test_reaper_benign_detaches_and_journals_executor_still_running_on_timeout` | Mock child that never exits; enqueue a ReaperJob with tiny `reaper_timeout_seconds=1`; assert worker returns within ~1s, journals `"executor_still_running"` event, does NOT raise, does NOT fail the worker loop. Confirms benign-detach semantics (C2). |
| 4 | `test_reaper_default_timeout_is_660s` | Default-constructed `UpgradeJournalSweepService` exposes `reaper_timeout_seconds == 660`; `ServicesConfig.upgrade_journal_reaper_timeout_seconds Field(ge=60)` rejects `<60` at boot. |
| 3 | `test_arm_preflight_refuses_unresolvable_scripts` | fixture with `scripts_dir=None`; arm refuses with `reason=preflight-scripts-unresolvable`; nonce NOT burned; journal NOT mutated |
| 3 | `test_arm_preflight_refuses_unconstructable_argv` | fixture with malformed `confirmed_source`; refuses with `reason=preflight-argv-unconstructable`; nonce NOT burned |
| 4 | `test_boot_sweep_clears_stale_pending_op` | fixture past `expires_at + RECONCILE_GRACE_S` + no in_flight; sweep clears op |
| 4 | `test_periodic_sweep_skips_live_executor` | fixture armed + `owner_pid=<alive>`; sweep refuses to clear; assert `os.kill(pid, 0)` was issued |
| 4 | `test_pending_actions_gc_prunes_expired_unconsumed` | fixture with consumed/expired/unexpired; `gc_pending_actions(install_dir, keep_run_id=None)` returns 1; remaining == 2 |
| 4 | `test_pending_actions_gc_keep_run_id_exemption` | direct call with explicit `keep_run_id=<in_flight>` preserves the row; sweep path passes `keep_run_id=None` |
| 5 | `test_terminal_outcome_filters_nonce_consumed` | history `[commit, rollback, nonce_consumed]` → outcome == `commit` |
| 5 | `test_terminal_outcome_returns_none_when_armed` | history `[nonce_consumed]` → `(None, None)` |
| 5 | `test_outcome_label_nonce_consumed_awaiting` | label map[`nonce_consumed`] == `"awaiting executor (pending)"` |
| 4 | `test_reaper_continues_after_journal_write_oserror` | R-M5-1/R1: inject OSError from journal_history_append; assert WARNING log carries run_id + pid + exception repr; reaper worker loop continues to next job (NO retry, NO raise) |
| 1/2 | `test_spawn_seam_refuses_when_pending_op_missing` | R-M5-2/R2 + Item-1 pre-spawn guard (:86): mock _uj.read_pending_op → None at the drain seam; assert NO spawn_executor call + journal event "executor_orphaned" with payload {spec_kind, install_dir, ts} (emit-NOTHING class, NOT in _TERMINAL_EVENTS) |

### Pack extensions

- `test/packs/upgrade_tool_interlock_unit_test.sh` (per `research-findings-shell-e2e.md` Q4 "Extending for the TWO-SIDED contract"):
  - Add verified-arm tests (fake marker + /tmp fixture, same style as the existing LIVE-gate PASS case).
  - Add refusal-token tests for the new arm-preflight-before-nonce-burn (each token = own test per pack header).
  - Add child-exit watcher tests — patch the spawn SEAM (`daemon.tools.upgrade_journal.spawn_executor`), NEVER `subprocess.Popen` (P2.2 gotcha).
- `test/packs/upgrade_registration_unit_test.sh` — no extension needed (registration unchanged).

### Lane intersection (per `research-findings-shell-e2e.md` Q3)

| Planned change | Lane modules touched | Inside lane task execution? | Verdict |
|----------------|---------------------|----------------------------|---------|
| (i) `manager.py` executor spawn + async reaper | NO (manager.py is not a lane module) | ADJACENT (post-turn finally path, shielded) | N/A — outside lane |
| (ii) `upgrade_tools.py` boot-time + periodic `reconcile_pending_op` sweep | NO | NO (timer/boot-driven loop, JobLockSweep-style) | N/A — outside lane |
| (iii) `upgrade_tools.py` argv/env passthrough at spawn seam | NO | NO (same post-turn context as (i)) | N/A — outside lane |
| (iv) `upgrade_journal.py` pending_actions GC expansion | NO (journal-file helpers only) | NO (`_gc_pending_actions` callers: store/consume/any new sweep — none in lane modules) | N/A — outside lane |
| (v) `upgrade_tools.py` _terminal_outcome class filter | NO | NO (pure fn refactor) | N/A — outside lane |

**Per-change e2e verdict: N/A.** Lane intersection empty by import audit (per research convention; cf. 2026-09-26 snapshot v1 lesson). The integrator's call stands; inputs say no e2e required for lane intersection.

---

## Security Invariants (preserve; cite in risks)

| Invariant | Anchor | Preserved by |
|-----------|--------|--------------|
| Job-source anti-forgery | `job_queue.py:1256-1257 / :1993` (drifted from caller's `:526-538`) | OUT OF SCOPE — no change; cited in risks |
| Registry-backed user-origin classification | `classify_user_origin` `:1130-1202`, merge 4db90d74 | NO change to classification; verified-arm reads `op.confirmed_source` (already validated at arm time via Factor 2 in `:2465-2512`) |
| Nonce instance/action binding | `upgrade_tools.py:2514-2620` (factor 3 + nonce validation) | NO change; verified arm requires `confirmed_action is not None` AND nonce not expired/already-used/instance-mismatched/action-mismatched |
| `pending_op` as durable serializer | `PendingOp` `:695-729`, `write_pending_op` `:732-737` | The verified-arm predicate reads from pending_op literal fields (`:2701-2704` write site; `:3832` drain read); no new path bypasses pending_op |
| Live restart refusal | `upgrade_tools.py` (env-self-match fail-closed; live restart refused via PRE-LIVE gate) | UNCHANGED — the verified-arm passthrough applies to promote, NOT to restart; restart argv `:3801-3807` is untouched |
| EXECUTOR_ENV_ALLOWLIST purity | `:988-991` (allowlist + PG prefix) | **NO widening**; extras passthrough via `:1003-1004` is the seam |
| Fence: ambient env never reaches child for unverified arms | `executor_env` `:994-1005` allowlist + PG prefix only | UNCHANGED — verified-arm passthrough is per-call-site, NOT ambient |
| Fence: child exit path stays in upgrade.log | `spawn_executor` `:1024-1030` parent-creates the file before Popen | UNCHANGED — reaper reads the same file, no new write path |
| Two-sided test contract | argv + allowed-set equality pins (`:3388-3391 / :3439-3444`) | Both sides pinned; ambient-strip side stays green |

**NO new path** where an unverified context reaches the child env or argv flag.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Verified-arm predicate drift: helper `is_verified_arm(op)` (5-conjunct incl. `env=="live"`) used in Items 1, 2, 3 — drift breaks all three | High | Medium | Single shared helper in `daemon/tools/upgrade_journal.py` (per R7ii); truth-table unit test covers all 9 rows incl. `env=demo`/`env=dev`/`op=None`; `_verified_arm_extras` name-frozen via inspect test |
| 2 | Reaper task leaks under daemon shutdown — child not reaped → stale pending_op persists past `_reaper_worker` cancel | Medium | Medium | `task.cancel()` + `CancelledError` handler in sweep service (`UpgradeJournalSweepService.stop()` at `:168`); shutdown sequence: `app.state.upgrade_journal_sweep.cancel()` THEN `await task`; child reaped by OS via `start_new_session=True` either way. Benign-detach semantics (C2): the worker never holds a hard reference to the child after timeout. |
| 3 | Boot sweep races arm — sweep clears a fresh arm because `expires_at + RECONCILE_GRACE_S` was crossed mid-flight | High | Low | Sweep MUST check `op.armed_by_instance == current_instance_id` (DEFENSE-IN-DEPTH ONLY — instance ids go stale across daemon reboots; NOT the primary safety) OR `owner_pid > 0` + time-bound liveness (primary safety); locked-safe per `:925-927` |
| 4 | Pending_actions GC deletes an in-flight nonce that hasn't been consumed yet | High | Low | `gc_pending_actions` accepts `keep_run_id` exemption (already in signature); the background sweep calls `gc_pending_actions(install_dir, keep_run_id=None)` — TTL + consumed-state logic IS the safety (expired-unconsumed pruned; unexpired-unconsumed kept); NO in-flight exemption lookup from arm paths. (research: `_gc_pending_actions` `:789-821` "keep_run_id" semantics) |
| 5 | Job-source anti-forgery drift awareness — `job_queue.py:1256-1257 / :1993` is the live cite; the prompt's `:526-538` is stale | Low | N/A | Cited in invariants for awareness; out of scope; separate commission |
| 6 | `nonce_consumed` label rename breaks `status.sh` or external scripts that grep `"live-confirmation nonce consumed"` | Medium | Medium | Grep before commit; update consumer or use additive mapping (keep old label, add new) — preferable since shell grep is brittle |
| 7 | Reaper's `asyncio.wait_for(asyncio.shield(...))` swallows CancelledError → child left orphan (FM-11 violation) | High | Low | Test: induce CancelledError; assert reaper returns cleanly + child reaped by OS; pattern from `manager.py:4148-4149` is FM-11-compliant |
| 8 | `F2_VERIFIED_NOTE` could leak the `confirmed_source` string (which is a chat-source id, not a secret — research §6 "no secrets… safe to embed") — low risk but documented | Low | Low | Cite research verbatim; the note IS the audit trail; bounded `<source>:<run_id>` format |
| 9 | P2 (shell/e2e) is NOT in scope — bundling validation (FL-23), 3 fresh ari cycles, runbook §8.4(ii)-(iii) obligations remain ops-side | High | High | Out-of-scope fence in plan; ADR-035 documents the residual ops gates; integrate checklist item into next live-rung promote runbook |
| 10 | Daemon self-derivation to "live" on post-1b3f0795 build means a future restart onto a v0.15.3 build could auto-promote onto the LIVE rung if not yet user-confirmed | Medium | Low | Verified-arm passthrough is per-CALL-SITE (Item 1) — the 3-factor gate (:2452-2620) is unchanged and refuses `user_confirmed=false` (Factor 1) regardless of self-derivation |
| 11 | Live stale row `r-20260926-100210-53ca` is a `pending_actions` (nonce registry) row, NOT a `pending_op` row — Item 4 covers BOTH but coder may conflate | Medium | Medium | Cite research §7 verbatim in task description; separate code path for each; test asserts both cleared by one sweep tick |

---

## Internal Sequencing (one implementer instance reuse — same-subsystem work grouped)

Recommended ordering for a single worker instance (minimizes context reload, groups tight-coupled edits):

1. **ITEM 7 (decision table — cites the recommended ADR-035 content for P2 to mint)** — documentation-only first; ratifies the policy; no code; gives the implementer the contract.
2. **ITEM 1 (ENABLER)** — `is_verified_arm` 5-conjunct predicate + `_verified_arm_extras` shared helper (mandatory across Items 1/2/3; defined in `daemon/tools/upgrade_journal.py` per R7ii) + argv + env + tests (incl. sole-caller pin + env=demo truth table + name-freeze pins).
3. **ITEM 3 (ARM PREFLIGHT)** — uses `is_verified_arm` predicate + `_verified_arm_extras` helper from Item 1; pre-burn block at `:2672-2688`.
4. **ITEM 5 (_TERMINAL_OUTCOME EVENT-CLASS FILTER)** — independent; small; clears the observability defect that masks the reaper events.
5. **ITEM 4 (RECONCILE SWEEP + PENDING_ACTIONS GC + REAPER QUEUE/WORKER)** — new `UpgradeJournalSweepService` owning (a) reconcile sweep tick, (b) pending_actions GC tick, AND (c) the **reaper queue + worker** (660s default timeout; benign-detach semantics). Item 2's reaper logic lives HERE — the spawn seam will call `enqueue_reaper(...)`.
6. **ITEM 2 (LOUD EXIT JOURNALING — spawn seam enqueue)** — wiring only: manager.py spawn seam captures pid + argv-summary + install_dir + run_id and calls `sweep_service.enqueue_reaper(...)`. The worker that awaits the child exit was built in step 5.

Rationale: Items 1→3 share `is_verified_arm` + `_verified_arm_extras` (tight coupling — name-freeze). Items 1→2 share the argv/env passthrough at the drain seam (tight coupling). **Item 2 depends on Item 4 (I2→I4 dependency edge — reaper queue+worker owned by the sweep service; the spawn seam cannot enqueue into a queue that does not exist yet)**, so Item 4 MUST land BEFORE Item 2. Items 5, 7 are independent. Grouping tight-coupled work minimizes instance state; landing Item 4 before Item 2 ensures the reaper has a queue+worker to consume into. Isolating independent items minimizes blast radius if a single item regresses.

---

## Dependencies on P2 (out of scope; coordination only)

| Dependency | Direction | Coordination needed |
|------------|-----------|---------------------|
| ADR numbering | P1 → P2 (P1 cites the recommended content; P2 owns the mint per `research-findings-shell-e2e.md` "EXTRA ASK 1") | P1 NEVER mints **ADR-035** — P2 mints it (P2 verifies no higher number first; cite research §8 caveat). P1 exit criterion cites the mint as P2's deliverable. |
| Promote.sh argv case | P1 uses existing case `:72-77` | NO coordination needed (already in tree) |
| lib.sh F2 note logger | P1 uses existing `:584-589` | NO coordination needed (already in tree) |
| Ledger F2-state gate | P1 arm requires ledger `f2_state=closed` | Per ledger_check.py :277-296; already enforced upstream of arm at `:2620+` |
| P2 e2e sizing (pack extensions) | P1 tests cover unit layer; P2 covers pack layer | P1 tests must be designed to be pack-runnable (per `research-findings-shell-e2e.md` Q4 pack structure) |
| P2 ops gates (FL-23 bundling, 3 fresh ari cycles) | Out of scope for both P1 and P2 code; ops-side | Documented in ADR-035 as "remaining ops gates" |

---

## Constraints Honored

- **Journal format compatibility** — `journal_history_append` (`upgrade_journal.py:301-316`) is additive; new event class `"executor_exit"` follows the `:380-383` "emit-NOTHING ordinary event" comment style. Python twin asserts semantically (per blueprint: same event names, same field shapes; shell parsers tolerate hand-edit divergence per ADR-034).
- **py3.13** — patterns used: `dict[str, str]` (PEP 585), `str | None` (PEP 604), `tuple[...]` generics (PEP 585). All in current tree (research verified at `upgrade_journal.py:994`, `upgrade_tools.py:2271`, `manager.py:3795`).
- **PEP 735 dev deps** — plain `uv sync` INCLUDES dev deps by default (per critical note); no `uv sync --extra dev` (obsolete).
- **Read-only on source** — only `.agents/shared/planning/upgrade-tool-lane-fix/phase1-plan.md` written.
- **Read-only on live** — nobody touches live during this mission (per task directive); the live stale `r-20260926-100210-53ca` row clears itself once v0.15.3 runs on live.
- **BSD/GNU portability** — no shell changes in P1 (Python only). When P2 lands, follow the `lib.sh:1147-1171` `atomic_flip` uname-dispatch pattern (per `research-findings-shell-e2e.md` "BSD PORTABILITY").
- **Lane intersection** — all items N/A (per Q3 per-change classification; research-verified by import audit).

---

## Open Questions (for caller input)

1. **Refusal token for preflight:** use existing `pipeline-busy` or mint a new `arm-preflight-refused:<token>`? The preflight BLOCKS the arm before burn; the caller has not yet armed. New token is more honest. **Recommend: mint a new `arm-preflight-refused:<sub-token>` family; pin each sub-token individually in tests.** Awaiting caller confirmation.
2. **`nonce_consumed` label rename scope:** rename `"live-confirmation nonce consumed"` → `"awaiting executor (pending)"` everywhere, or additive (keep old, add new with deprecation)? Grep `status.sh:88-90` and any external script that consumes this string. **Recommend: rename + grep-verify + update consumers in the same commit.** Awaiting caller confirmation.
3. **Reaper timeout value:** fixed at 660s default per C2 (livez 60 + readyz 120 + soak 300 + overhead ≈ 490s minimum → 660s headroom). Configurable via `ServicesConfig.upgrade_journal_reaper_timeout_seconds Field(ge=60)`. Floor `ge=60` rejects nonsensical values < 1 minute. (RESOLVED — leader-ratified 2026-09-26.)
4. **Sweep service interval:** match `job_lock_sweep_interval_seconds` (90s default) or different? Per research, "the only periodic sweeps today" all use 90s default. **Recommend: 90s default, own knob `upgrade_journal_sweep_interval_seconds` for operator override.** Awaiting caller confirmation.
5. **Boot-time sweep placement:** in `api.py` lifespan BEFORE or AFTER `_boot_db_preflight()` (DR-1 seam at `daemon/__main__.py:104`)? Research says "after preflight" so DB-backed reads are safe. **Recommend: AFTER preflight + AFTER app.state construction but BEFORE first request.** Awaiting caller confirmation.

---

## Exit Criterion

This phase is DONE when:
- All 5 implementation items + the ratified-branch decision table are merged to `latest` (or staged for merge per the giter's role per the meta KV directive).
- **Two-sided test contract holds:** zero existing pins flip (the 3 pins at `test_upgrade_tools.py:3388-3391` + `:3439-3444` + `test_upgrade_journal.py:902` remain the unverified side and stay green unchanged); the verified side lands as ~20 sibling test functions standing alone (sibling tests, no parametrize, no flip).
- New tests per item pass.
- Packs (`upgrade_tool_interlock_unit_test.sh`) extended and pass.
- ADR-035 minted in `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` (P2-owned per `research-findings-shell-e2e.md` "EXTRA ASK 1") with both halves of the verifier verdict (Q1 NO + Q2 YES + user ratification 2026-09-26).
- No code touched on live (per task directive); release v0.15.3 ready for promote per the established ladder (PRE-LIVE gate: F2 closed + scripts-bundling validated + 3 fresh ari cycles on bundled release + `--f2-verified-closed` flag + user-executed only — per `research-findings-shell-e2e.md` "VERIFIER-VERDICT FOLD-IN B").

Next phase (P2): shell / e2e pack extensions + residual ops gates (separate commission; out of P1 scope).
