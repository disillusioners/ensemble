# Decisions: `service` Tool Category for agents-ensemble

Date: 2026-09-15
Author: planner[v2] via technical-analysis worker (re-dispatch; first author)
Status: **Phase-0 gate PASSED — D4 RESOLVED (Option A, leader-ratified 2026-09-15)** — 14 architect amendments folded in (`architecture-recommendation.md` §3); A1/A2/A5 BLOCKING for Phase 1
Feature: New daemon tool category `service` letting agents start long-lived processes (dev servers, databases, watchers) that survive instance lifecycle and daemon restart, are NEVER reaped by daemon cleanup, and are NOT OS services (no launchd/systemd/launchctl — purely daemon-managed detached processes).
Companion artifacts: `technical-analysis.md`, 3 research files, `architecture-recommendation.md` (binding amendments) in the same directory.
Verified at SHA: `f6ca8791` (branch `latest`; `feature/service-tool` branched from this commit; worktree currently on `latest` with a foreign session's dirty state — verified file:line ranges on `latest`).

**BRANCH NOTE (amended per leader ratification 2026-09-15):** `feature/service-tool` is being synced to current `latest` by `giter` in parallel. Implementation targets the **synced branch**, not the stale dirty worktree. Implementers MUST re-locate every file:line anchor by SYMBOL, not line number — anchor drift caveat applies throughout (architect A10). Plan-time anchors verified on `latest` at `f6ca8791`; several have drifted (see `architecture-recommendation.md` header + A10 for the corrected anchor list).

---

## Summary of recommendations (one-line each)

| ID | Question | Recommendation |
|----|----------|----------------|
| D1 | Detach mechanism on darwin | `subprocess.Popen(start_new_session=True, close_fds=True, stdin=DEVNULL, stdout=log_fh, stderr=STDOUT)` — direct precedent `upgrade_journal.spawn_executor` |
| D2 | Tracking store | New DB table `service_tracking` (SQLModel + dual-dialect .sql migration + PG `_ensure_postgres_columns`); TEXT ISO-8601 timestamps; partial UNIQUE index `idx_service_tracking_name_active` |
| D3 | Tool surface | 5 tools: `service_start` / `service_stop` / `service_status` / `service_list` / `service_logs`; name-keyed (not pid-keyed); `service_stop` signals the process GROUP via `os.killpg` (A1) async (A2) |
| D4 | Security/privilege model | **RESOLVED (Option A, leader-ratified 2026-09-15)** — Add `service` to `PRIVILEGED_TOOL_CATEGORIES` (default-closed, explicit `tools.allow` only); update THREE pin tests same-PR (A14) |
| D5 | Abuse guards | Cap=10 + name-unique partial index (A11) + PID-reuse defense (pid + start_time) + stop-idempotent + atomic-guard UPDATEs (A13); NO command allowlist (out of scope) |
| D6 | Daemon-restart reconciliation | Boot sweep: PID liveness + start-time match + eternal-`starting` reaper (A3) → mark EXITED on mismatch; NO re-spawn; mount via `getattr(manager, ...)` (A5) + guaranteed boot pass (A6) |
| D7 | Observability | `[ServiceTool] <flag>=%s (env ENSEMBLE_…)` boot probe + per-call INFO log; kill-switch `ENSEMBLE_SERVICE_TOOL_ENABLED` (default ON) + 2 knob envs; recon key renamed `service_tool_reconcile_interval_seconds` (A8) |

**14 amendments folded in (per `architecture-recommendation.md` §3, leader-ratified 2026-09-15):** A1 (killpg), A2 (async stop), A3 (eternal-`starting` reaper), A4 (`exit_code=None` docs), A5 (mount fix), A6 (boot pass), A7 (CI grep-gate), A8 (config naming + stop semantics + None-guard), A9 (inject `ServiceRepo` directly), A10 (anchor refresh), A11 (partial UNIQUE index), A12 (TEXT ISO-8601 timestamps), A13 (atomic-guard UPDATEs), A14 (D4 pin-list 2→3 + comment rewrite).

Open questions (CLOSED by architect): OQ#1 (Option A; default_open NOT pre-committed), OQ#2 (document single-daemon-only), OQ#3 (operator-side log rotation), OQ#4 (no command allowlist v1), OQ#5 (`ps`-parse acceptable), OQ#6 (keep 5s wait even on `force=True`), OQ#7 (plain text tail).

---

## D1 — Detach mechanism on darwin (and prod)

### Question
Double-fork + setsid vs `subprocess.Popen(start_new_session=True)` vs `asyncio.create_subprocess_exec(start_new_session=True)`. Which spawn primitive for the `service` category?

### Decision
**`subprocess.Popen(start_new_session=True, close_fds=True, stdin=DEVNULL, stdout=log_fh, stderr=STDOUT)`.**

### Rationale

- **`start_new_session=True` IS the Python equivalent of the C double-fork+setsid pattern.** All three options put the child in a brand-new session and process group (verified for `subprocess.Popen` on Linux and macOS in CPython 3.10+; `bash.py:256-258` and `proc_tools.py:1131-1133` use the same flag with `asyncio.create_subprocess_*`). The naming "double-fork + setsid vs start_new_session=True" is a false trichotomy — they are equivalent at the syscall level (`setsid(2)` after the child fork).
- **Popen vs asyncio.create_subprocess_exec** is the real choice. Popen wins on:
  - **Resource footprint**: no reader/exit/timeout tasks per service (precedent at `proc_tools.py:1183-1190` shows the 3-task overhead); the child owns its stdio once Popen returns.
  - **Precedent**: the ONLY existing daemon code that achieves daemon-death-survival is `upgrade_journal.spawn_executor` (`daemon/tools/upgrade_journal.py:1010-1034`), which uses Popen. Its docstring explicitly states the child "must survive BOTH tool-harness teardown and daemon death" — that is the binding contract we need to satisfy.
  - **Simplicity**: one file (`service_spawner.py`), ~50 LOC, mirrors spawn_executor verbatim except for the `argv` source.
- **stdio MUST be a file, not a pipe** — bash.py:243-255 documents the failure mode (backgrounded child holding a pipe write-end → `communicate()` hangs forever). The log path is `data/services/<name>.log`; the daemon creates the directory on first start.
- **stdin=DEVNULL, close_fds=True** prevent fd-inheritance leaks from the daemon's own file descriptors (open LangGraph checkpoints, sqlite handles, PG sockets). `close_fds=True` is the Python way to set the FD_CLOEXEC flag on all inherited fds before the child execs.
- **stdio → log file ownership**: the log file is opened by the daemon (parent), passed to Popen as `stdout=log_fh`, and closed by the parent after Popen returns (so the parent doesn't hold the write-end open after spawn — see bash.py:243-255 comment for the same discipline).

### Alternatives rejected

- **`asyncio.create_subprocess_exec(start_new_session=True)` (Option B)**: same survival semantics, but adds 3 async reader tasks per service (reader + exit watcher + optional timeout killer — `proc_tools.py:1183-1197`). For a long-lived service that the agent queries on-demand, the reader task is dead weight (the agent reads via `service_logs` tool, not via a daemon-side pipe). Adds ~200 LOC of event-loop tasks per service for zero functional gain.
- **Manual `os.fork()` + `os.setsid()` + `os.fork()` (raw C-style double-fork)**: equivalent semantics but reinvents stdlib. Popen already does this in C; using fork() directly is a code-smell that survives only in legacy daemon code, none of which exists today.
- **Third-party libraries (`python-daemon`, `pexpect`, `sarge`)**: adds a dependency for a 1-call-site concern. Existing daemon spawn surfaces are all stdlib-based; introducing a dependency breaks the convention.

### Evidence (file:line)

- Precedent: `daemon/tools/upgrade_journal.py:1010-1034` (full Popen invocation; docstring with the survival contract).
- `start_new_session=True` flag on bash/proc spawns: `daemon/tools/bash.py:256-258`; `daemon/tools/proc_tools.py:1131-1133`.
- Stdio-to-file discipline: `daemon/tools/bash.py:243-275` (file-backed stdout/stderr to avoid pipe-hang); `daemon/tools/bash.py:74-79` (registry limitations, setsid-detached orphans unreachable).

### Reversibility
Easy — swap Popen for `create_subprocess_exec` inside `service_spawner.spawn`; tool API unchanged. Cost: medium if reader tasks become desired later (would require an event-loop rewrite). The Popen path is the default; the async path is the migration target if event-loop integration becomes a problem.

---

## D2 — Tracking store

### Question
New DB table vs file-based JSON registry vs reuse the `task` table.

### Decision
**New DB table `service_tracking`** in `daemon/repositories/service_tool/{models.py, repository.py}` with the 3-site index pattern.

### Rationale

- **DB is the only stateful substrate that survives daemon restart and is queryable from periodic sweeps.** A file-based registry leaves us with two sources of truth (filesystem + OS process table) and forces the reconcile sweep to do `stat()` + JSON parse instead of a SELECT.
- **Reusing the `task` table pollutes two concerns**: `task` is the work-in-flight primitive (backed by `daemon/services/job_state_machine.py`); `service_tracking` is the long-lived-process primitive. Mixing them forces `task.kind = 'service'` discriminators everywhere and entangles the work-queue scheduler with process lifecycle.
- **Direct pattern fit**: `daemon/repositories/task/` is the named exemplar (research checklist item 1). New domain = new directory under `daemon/repositories/`; sync `sqlmodel.Session` methods; `engine.connect()` for reads, `engine.begin()` for writes (`task/repository.py:77-130`).

### Table schema (proposed) — amended per A11 (partial UNIQUE index) + A12 (TEXT ISO-8601 timestamps, repo-side bumps)

```python
# daemon/repositories/service_tool/models.py
class ServiceStatus(str, enum.Enum):
    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"

class ServiceTracking(SQLModel, table=True):
    __tablename__ = "service_tracking"
    __table_args__ = (
        # A11: partial UNIQUE index IS the D5 same-name guard.
        # Drop the redundant `unique=True, index=True` on `name` below.
        # Precedent: report_injection/models.py:227-235 (dual-dialect sqlite_where/postgresql_where).
        Index("idx_service_tracking_name_active",
              "name",
              unique=True,  # A11: this is the actual D5 partial-unique guard
              sqlite_where=text("status IN ('starting','running')"),
              postgresql_where=text("status IN ('starting','running')")),
        Index("idx_service_tracking_pid", "pid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)  # SQLModel autoincrement
    name: str  # A11: NO `unique=True, index=True` here — that emits a FULL unique constraint
               # that kills name-reuse-after-EXITED. The partial unique above is the only guard.
    command: str  # JSON array of argv (NOT shell string — no shell injection)
    pid: Optional[int] = Field(default=None)  # nullable: pre-spawn and post-exit
    start_time: Optional[int] = Field(default=None)  # kernel starttime jiffies (Linux) / epoch (macOS via ps parse)
    cwd: str  # absolute path; validated at insert time
    # A12: str with .value default (storage-equivalent; convention per report_injection/models.py:308).
    status: str = Field(default=ServiceStatus.STARTING.value)
    started_by_instance_id: str  # UUID4, agent-instance owner
    started_by_agent_id: str  # agent name
    log_path: str  # absolute path to data/services/<name>.log
    exit_code: Optional[int] = Field(default=None)
    # A12: TEXT ISO-8601 via `_now_iso()` factory (30+ call-site convention:
    # skill/models.py:50-51, report_injection/models.py:304-306, job_queue/models.py:357).
    # Drop `sa_column_kwargs={"onupdate": text("now()")}` — non-portable (SQLite has no `now()`).
    # `updated_at` bumps live in the repository methods.
    # If `daemon/services/timestamps.py` (`now_utc_naive`/`now_utc_iso` from merge f6ca8791) is
    # visible at the implementation SHA, swap the factory to that helper (A12 handles both branches).
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
```

Notes:
- `command` is a JSON-encoded array of argv strings (NOT a shell string). No shell expansion = no shell injection. Pattern: `daemon/services/vscode_server_manager.py` uses argv form.
- `start_time` semantics differ by platform (OQ#5) — Linux uses jiffies since boot (parse `/proc/<pid>/stat` field 22), macOS uses parsed `ps -o lstart` epoch seconds. Helper `get_process_start_time(pid) -> int` lives in `service_spawner.py`.
- `name` partial unique via the partial UNIQUE index (status filter) — lets the same name be re-used after EXITED (a new service with the same name replaces a dead one). A11 fixed: name field itself has NO `unique=True` (that would kill reuse-after-EXITED); the partial UNIQUE inside the index is the actual D5 guard.
- **A13 (atomic-guard UPDATEs):** every status-mutating UPDATE in the repository MUST be guarded `WHERE id=? AND status IN ('starting','running')` so sweep↔stop races are idempotent. Precedent: `report_injection/models.py` guarded-claim pattern.

### Migration — amended per A11 + A12

`daemon/migrations/versions/YYYYMMDD_HHMMSS_create_service_tracking.sql`:

```sql
-- UP
-- Plain CREATE TABLE / CREATE INDEX are valid on PostgreSQL AND SQLite
-- (unlike 20260714_000001, which was PG-only). See LESSONS/2026-09-04.
-- A12: timestamps are TEXT ISO-8601 (portable across SQLite + PG).
-- A11: partial UNIQUE index IS the D5 same-name guard.
CREATE TABLE IF NOT EXISTS service_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- SQLite; PG tolerates this for create_all tests
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    pid INTEGER,
    start_time INTEGER,
    cwd TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting',  -- text, NOT boolean
    started_by_instance_id TEXT NOT NULL,
    started_by_agent_id TEXT NOT NULL,
    log_path TEXT NOT NULL,
    exit_code INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- A12: ISO-8601 TEXT
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- A12: ISO-8601 TEXT
);

-- 3-site index registration: this name MUST be byte-identical in:
--   1. THIS FILE (.sql / SQLite)
--   2. daemon/repositories/service_tool/models.py __table_args__
--   3. daemon/manager.py _ensure_postgres_columns (PG only)
-- A11: this partial UNIQUE index is the actual D5 same-name guard.
CREATE UNIQUE INDEX IF NOT EXISTS idx_service_tracking_name_active
    ON service_tracking (name)
    WHERE status IN ('starting','running');  -- SQLite supports partial indexes since 3.8.0

CREATE INDEX IF NOT EXISTS idx_service_tracking_pid
    ON service_tracking (pid);

-- DOWN
DROP TABLE IF EXISTS service_tracking;
```

### PG mirror (`_ensure_postgres_columns`) — amended per A11

Idempotent block at `daemon/manager.py:4787+` (new clause) — `CREATE TABLE IF NOT EXISTS service_tracking (...)` + `CREATE UNIQUE INDEX IF NOT EXISTS idx_service_tracking_name_active ON service_tracking (name) WHERE status IN ('starting','running')` + `CREATE INDEX IF NOT EXISTS idx_service_tracking_pid ON service_tracking (pid)`. Index name byte-identical to .sql + models.py. **A11: the PG-side index MUST be `CREATE UNIQUE INDEX`** — mirroring the partial uniqueness declared in `models.py` `__table_args__` and the `.sql`.

### Alternatives rejected

- **File-based JSON registry** (`data/services/<name>.json`): split-brain with the OS process table; reconcile requires `stat()` + JSON parse per file; log file already lives in `data/services/` so two-file-per-service state is confusing.
- **Reuse `task` table** with discriminator: pollutes the `task` domain; the `task` model's status enum doesn't have a `'starting'` state that means "process spawned, awaiting pid confirmation"; the write guard at `task/repository.py:39-58` would have to learn about services.

### Evidence (file:line)

- Per-domain convention: `daemon/repositories/task/{models.py, repository.py}` (entire files). Booleans as `sa.Column(server_default=text("false"), index=True)` precedent at `task/models.py:97-117`; composite indexes in `__table_args__` at `:141`.
- 3-site index pattern: `daemon/migrations/versions/20260906_192100_add_task_claim_wake_lane_index.sql:35-49`; `daemon/repositories/task/models.py:141`; `daemon/manager.py:5409-5412`.
- Migration runner: `daemon/migrations/runner.py:325-406` (transactional application); `:472-491` (PG NO-OP rationale).
- 20260714 trap: `.agents/tester/LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md:8-11`.

### Reversibility
Hard — the migration has been applied by the time this is in production. To roll back: write a DOWN migration that DROPs the table + indexes; revert the SQLModel class; remove from CATEGORY_MODULES. Easy in code, hard in data.

---

## D3 — Tool surface

### Question
Which tool names, which args, which return shapes. Name-keyed vs pid-keyed.

### Decision
**5-tool surface: `service_start`, `service_stop`, `service_status`, `service_list`, `service_logs`.** Name-keyed (not pid-keyed) for all callers; `service_stop` internally verifies `(pid, start_time)` matches the row before signaling (PID-reuse defense).

### Rationale

- **Name-keyed is the only safe option for agents.** PIDs are recycled by the kernel; the agent has no memory of PIDs across restart or even across calls. The unique service name is the agent-visible identifier and is the stable handle. `service_stop(name)` internally re-loads the row, verifies the PID is alive AND the start-time matches the stored value, then signals.
- **`service_logs` is necessary** because it is the only way the LLM can diagnose a service that crashed silently. Without it, the LLM must `read_file` the log via the filesystem tool — possible but breaks the service-tool abstraction and adds a permissioning hop (filesystem tools may be excluded by the agent's `tools.allow`).
- **`service_restart` is REJECTED** — adding a stop-then-start composite creates half-restart states when stop succeeds vs fails (process killed but new spawn fails → service gone with no retry path). The LLM composes `service_stop` + `service_start` and inspects each result. Composability is better than convenience.
- **`service_list` is necessary** — without it, the LLM has no way to enumerate services across restart, which is a common ask ("what services do I have running?"). Returns rows ordered by created_at DESC.
- **`service_status` is the lightweight liveness check** — `service_list` could fold this in, but `service_status` returns ONE row with full detail (including liveness-reconciled state) for the common case where the agent knows the name.

### Tool signatures

```python
# daemon/tools/service_tools.py

@register_tool_category("service")
@tool
def service_start(
    name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")],
    command: Annotated[list[str], Field(min_length=1, description="argv array, NOT shell string")],
    cwd: Annotated[Optional[str], Field(default=None, description="absolute path; defaults to daemon cwd")],
) -> dict:
    """Start a long-lived service. Survives instance lifecycle and daemon restart.
    Returns: {name, pid, status: "running", log_path}."""

@register_tool_category("service")
@tool
def service_stop(
    name: Annotated[str, Field(min_length=1)],
    force: Annotated[bool, Field(default=False, description="Skip SIGTERM, go straight to SIGKILL")],
) -> dict:
    """Stop a service by name. Idempotent: already-exited returns the stored exit shape.
    Returns: {name, pid, status: "exited", exit_code} or {status: "not_found"}."""

@register_tool_category("service")
@tool
def service_status(
    name: Annotated[str, Field(min_length=1)],
) -> dict:
    """Live status of one service. Reconciles pid liveness on call.
    Returns: {name, pid, status, exit_code?} or {status: "not_found"}."""

@register_tool_category("service")
@tool
def service_list() -> list[dict]:
    """List all tracked services (any status) ordered by created_at DESC.
    Reconciles each row's pid liveness inline.
    Returns: [{name, pid, status, started_by_instance_id, ...}, ...]."""

@register_tool_category("service")
@tool
def service_logs(
    name: Annotated[str, Field(min_length=1)],
    tail_lines: Annotated[int, Field(default=200, ge=1, le=10000)],
) -> str:
    """Tail the log file for a service. No reaping; stdio is owned by the OS.
    Returns: the last `tail_lines` lines as a single string."""
```

### PID-reuse defense in `service_stop` — amended per A1 (os.killpg) + A2 (async, no busy-wait) + A13 (atomic guard) + **F1 (poll-loop start_time re-verify + re-verify-before-SIGKILL escalation) + F8 (spawn_failed row write)**

```python
# Pseudocode for the kill branch (A1+A2+F1+F8 — corrected from sync os.kill to
# async os.killpg + poll-loop start_time equality + escalation re-verify +
# spawn_failed synchronous path)
async def service_stop(name: str, force: bool) -> dict:
    # A13: atomic-claim pattern — only one transition wins the row.
    row = repo.get_by_name_active(name)  # status IN ('starting','running')
    if row is None:
        # Already exited or never existed — idempotent return
        existing = repo.get_by_name_any_status(name)
        if existing:
            return {"name": name, "status": "exited", "exit_code": existing.exit_code}
        return {"status": "not_found"}

    # PID-reuse defense
    if row.pid is None:
        return {"name": name, "status": "starting", "reason": "pid_not_yet_assigned"}

    current_start_time = await asyncio.to_thread(
        service_spawner.get_process_start_time, row.pid
    )
    if current_start_time is None:
        # PID is dead
        await asyncio.to_thread(repo.mark_exited, row.id, exit_code=row.exit_code)
        log.info("[ServiceTool] service_stop pid_dead name=%s pid=%s", name, row.pid)
        return {"name": name, "pid": row.pid, "status": "exited", "reason": "pid_dead"}

    if current_start_time != row.start_time:
        # PID was recycled — different process now owns this pid
        await asyncio.to_thread(repo.mark_exited, row.id, exit_code=None)
        log.warning("[ServiceTool] service_stop pid_recycled name=%s pid=%s expected_start=%s got=%s",
                    name, row.pid, row.start_time, current_start_time)
        return {"name": name, "pid": row.pid, "status": "exited", "reason": "pid_recycled"}

    # PID-and-start-time match — safe to signal the WHOLE GROUP.
    # A1: `os.killpg(row.pid, sig)` — a setsid'd service IS its own session+group leader
    # (pgid == pid); killing the group reaches fork-children (e.g., `npm run dev` workers)
    # with zero added reachability beyond the existing `proc_tools.py:1010` precedent.
    # F7: `service_spawner.stop` is NEVER called without (pid, start_time) ownership
    # verification here — the re-verify on each poll (F1) and re-verify-before-SIGKILL
    # escalation (F1) are the same invariant.
    sig = signal.SIGKILL if force else signal.SIGTERM
    await asyncio.to_thread(os.killpg, row.pid, sig)
    if not force:
        # A2: async polling — no blocking 5s busy-wait. Mirrors `proc_tools.stop_process`
        # (`proc_tools.py:1264-1400`) which uses `await asyncio.wait_for(...)`.
        # F1: each poll re-reads `get_process_start_time(row.pid)` and compares
        # EQUALITY with `row.start_time`. A mismatch ⇒ the PID was recycled during
        # the grace window ⇒ mark EXITED, NEVER escalate to SIGKILL (the recycled
        # process is now some unrelated user process and a SIGKILL would be a stray
        # signal — the exact hazard F1 closes). Precedent for re-verify-before-
        # escalation: `proc_tools.stop_process:1380-1386` re-reads ownership
        # before the 5s SIGKILL escalation.
        deadline = time.monotonic() + 5.0  # mirrors _STOP_GRACE_SECONDS=5 (proc_tools.py:106)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            current_start = await asyncio.to_thread(service_spawner.get_process_start_time, row.pid)
            if current_start is None:
                # Process exited cleanly within grace — done.
                break
            if current_start != row.start_time:
                # F1: PID recycled during grace — STOP, do NOT escalate. Mark EXITED,
                # log WARNING with the mismatch, return pid_recycled without
                # sending SIGKILL. The recycled process is now some unrelated user
                # process; a SIGKILL would be a stray kill against an unrelated PID.
                await asyncio.to_thread(repo.mark_exited, row.id, exit_code=None)
                log.warning("[ServiceTool] service_stop grace_recycle name=%s pid=%s expected_start=%s got=%s grace_elapsed=%.2fs",
                            name, row.pid, row.start_time, current_start,
                            time.monotonic() - (deadline - 5.0))
                return {"name": name, "pid": row.pid, "status": "exited", "reason": "pid_recycled_during_grace"}
        else:
            # Loop completed without break — process still alive past grace.
            # F1: re-verify (pid, start_time) immediately before SIGKILL escalation.
            # If recycled in the last 100ms before the deadline, mark EXITED instead
            # of escalating. This is the second F1 layer (poll-loop + escalation-edge).
            final_start = await asyncio.to_thread(service_spawner.get_process_start_time, row.pid)
            if final_start is not None and final_start != row.start_time:
                await asyncio.to_thread(repo.mark_exited, row.id, exit_code=None)
                log.warning("[ServiceTool] service_stop pre_kill_recycle name=%s pid=%s expected_start=%s got=%s",
                            name, row.pid, row.start_time, final_start)
                return {"name": name, "pid": row.pid, "status": "exited", "reason": "pid_recycled_pre_kill"}
            # A1: SIGKILL also via killpg for the same reason as SIGTERM above.
            await asyncio.to_thread(os.killpg, row.pid, signal.SIGKILL)
    await asyncio.to_thread(repo.mark_exited, row.id, exit_code=None)
    log.info("[ServiceTool] service_stopped name=%s pid=%s force=%s", name, row.pid, force)
    return {"name": name, "pid": row.pid, "status": "exited"}
```

### F8 — `service_start` synchronous spawn-failure path

```python
# F8: synchronous spawn failure → immediate EXITED row write (no A3-reaper 30s
# slot block). The A3 eternal-`starting` reaper is reserved for the
# crash/interrupt window that client-side code cannot close — the synchronous
# Popen failure path is closed here in 1.B.5 (NOT deferred to sweep).
async def service_start(name: str, command: list[str], cwd: Optional[str]) -> dict:
    # 1.B.5 prior steps: cap-check, name-pattern validate, name-uniqueness pre-check.
    # F8: spawn → if Popen fails synchronously, write EXITED row immediately and
    # return spawn_failed. The A3 reaper (Phase 2) only covers crash/interrupt
    # windows (e.g., Popen returned but the daemon died between Popen and INSERT);
    # it does NOT cover synchronous spawn failures — those are a client-side
    # problem and must be closed in 1.B.5.
    try:
        pid, start_time = service_spawner.spawn(command, log_path=log_path, cwd=cwd)
    except OSError as exc:
        # Synchronous spawn failure: child never existed; no PID, no kill cleanup.
        # Write EXITED row immediately to release the cap slot and surface the
        # failure to service_status / service_list.
        failed_row = repo.insert_with_status(
            name=name, command=json.dumps(command), pid=None, start_time=None,
            cwd=cwd, status="exited", started_by_instance_id=...,
            started_by_agent_id=..., log_path=log_path, exit_code=None,
            reason="spawn_failed",  # recorded in a new `reason` column or log
        )
        log.error("[ServiceTool] service_start spawn_failed name=%s err=%s", name, exc)
        return {"name": name, "status": "spawn_failed", "reason": str(exc)}

    # F2: concurrent same-name race. INSERT may raise IntegrityError (the
    # partial UNIQUE index idx_service_tracking_name_active — see D2 schema) if
    # a concurrent caller won the race. The IntegrityError loser MUST killpg
    # the just-spawned child (it's an orphan otherwise — setsid'd, never
    # registered, kill-exempt). Without F2 the loser leaks a live, untracked,
    # kill-exempt OS process that nothing can ever reap.
    try:
        row = repo.insert(...)
    except IntegrityError:
        await asyncio.to_thread(os.killpg, pid, signal.SIGKILL)
        log.warning("[ServiceTool] service_start concurrent_race name=%s pid=%s killed",
                    name, pid)
        return {"name": name, "status": "name_in_use", "reason": "concurrent_start_won_race"}

    log.info("[ServiceTool] service_started name=%s pid=%s", name, pid)
    return {"name": name, "pid": pid, "status": "running", "log_path": log_path}
```

**F7 — async/sync seam layering:** sync reconciliation code (the sweep's `repo.list_active`/`mark_exited` SQL calls, ~100ms wall-time at the default cap of 10 rows) is wrapped in `await asyncio.to_thread(...)` so it NEVER blocks the event loop. Pattern precedent: `EligiblePendingSweepService.sweep_once` at `daemon/services/eligible_pending_sweep.py:212-215`. `service_spawner.stop(pid, force, grace_seconds)` is never called without `(pid, start_time)` ownership verification (the F1 re-verify on each poll + the F1 re-verify-before-SIGKILL escalation are the same invariant applied at two points in the kill path).

**A13 — atomic-guard UPDATEs (folded into repo methods, not the tool layer):**
- `repo.mark_exited(id, exit_code=None)` MUST be guarded `WHERE id=? AND status IN ('starting','running')` and return whether the row was actually transitioned (0 rows updated = a sweep↔stop race lost; the caller treats this as idempotent success).
- The same guard applies to any future `update_status` paths.
- Precedent: `report_injection/models.py` guarded-claim pattern; the row count is the implicit "did I win the race?" signal.

### Alternatives rejected

- **Pid-keyed API**: unsafe (PIDs recycled); verbose for the LLM (must remember PIDs across calls).
- **Composite `service_restart`**: half-restart hazard; composability preferred.
- **4-tool surface (no `service_logs`)**: forces LLM to use `read_file` for log inspection, breaks abstraction, requires filesystem tool to be in `allow`.

### Evidence (file:line)

- Existing tool doc precedent: `daemon/tools/proc_tools.py:2154-2169` (`proc_stop` full_doc pattern); `daemon/tools/bash.py:415-433` (`bash._full_doc_` pattern).
- 3-layer PID-ownership precedent: `daemon/tools/proc_tools.py:228-242` (`_verify_pid_ownership`); `:918-1035` (`_attempt_kill_signal`); `:114-118` (`ENSEMBLE_PROC_TRACKING_ID` env tag).
- SIGTERM→5s→SIGKILL contract: `daemon/tools/proc_tools.py:106` (`_STOP_GRACE_SECONDS=5`); `daemon/tools/proc_tools.py:1264-1400` (stop_process orchestration).
- Stop-idempotency precedent: `daemon/services/vscode_server_manager.py:458-471` (code-server stop with SIGTERM→5s→SIGKILL).

### Reversibility
Easy for tool names (renames + new tool adds are backward-compatible if old names deprecated with a warning). Hard for arg semantics — once agents learn `service_stop(name, force=True)`, the contract is sticky.

---

## D4 — Security / privilege model

### Status (amended per leader ratification 2026-09-15)
**RESOLVED — Option A.** Add `service` to `PRIVILEGED_TOOL_CATEGORIES`. Update **THREE** pin tests in the same PR (A14 corrected from 2→3 — see "Critical discovery" below). Rewrite the frozenset's comment to the **behavioral** criterion (not "daemon-internal"). A5 (mount fix) and A2 (async stop) are blocking for Phase 1 but not for D4 itself.

### Question (original)
(a) Default-open non-privileged, (b) Explicit `tools.allow` only (non-privileged), (c) Full privileged category (frozen opt-in).

### Decision
**(b)/(c) — Add `service` to `PRIVILEGED_TOOL_CATEGORIES`** (architect-verified 2026-09-15).

### Rationale (architect-verified 2026-09-15)

- **The frozenset's real semantics are behavioral, not "daemon-internal."** The membership criterion that has actually governed the set is *"never default-granted; reachable only via explicit `tools.allow`."* The D7/attestation precedent (which kept `attestation` OUT of the frozenset) turned on *"does this category need filter-level default-deny?"* — not on daemon-internality. `service` mints persistent, daemon-escaping OS authority that no registry-scoped kill site can reach; it clearly needs filter-level default-deny. "Privileged = daemon-escaping authority" is a defensible reading, and the comment rewrite makes it the *documented* reading.
- **Option A's blast radius is naming-only — verified, not assumed.** Production consumers of `PRIVILEGED_TOOL_CATEGORIES` are confined to three behavioral sites implementing one identical semantic — the empty-allow filter (`instance.py:331`), the strip helper `_strip_privileged_category_tools` (~`instance.py:4743-4760`, called from `:4793`; the plan's `:4667-4684` anchor is stale), and the docs mirror (`help.py:56`, transitive through `loader.py:144/184/199`). Zero frontend, UI-badge, telemetry, boot-probe, or docs consumers. Nothing treats the set as a closed internal trio.
- **The pin net EXTENDS to `service` under A.** Three exact-equality asserts (architect-verified §1.3 of `architecture-recommendation.md`) scream on both silent removal (fail-open regression) and silent addition. This is the strongest existing protection story for a trust-tier boundary in the repo.
- **The only critical-path failure mode is shipping `service` NOT in the frozenset** — it auto-leaks to every empty-allow agent (canonical example: `watcher`, per the category comment at `_tool_registry.py:110-113`). Default-closed is non-negotiable; A delivers it with zero new mechanism.
- **User requirement fit:** agents CAN be granted it (explicit `tools.allow=["service"]`), it is default-closed (frozenset strip), and the "not daemon-internal like the trio" objection is a vocabulary concern fully mitigated by the comment rewrite (§1.2 of `architecture-recommendation.md`) — not a behavioral one.

### Required changes (v1, Option A) — amended per A14

| File | Change |
|---|---|
| `daemon/tools/_tool_registry.py:124-128` | Add `"service"`; rewrite comment `:105-123` to the behavioral criterion ("never default-granted; explicit `tools.allow` only") + one-line service rationale; fix the pin-checklist comment to name **THREE** pin files (A14) |
| `tests/unit/tools/test_upgrade_registration.py:105` | Equality set + docstring (A14) |
| `tests/unit/tools/test_attestation_registration.py:158` | Equality set + docstring (the `:152` NOT-in assert stays green) (A14) |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py:292` | **The missed third pin (A14)** — equality set + "exactly four" docstring |
| `daemon/tools/instance.py:~4747`, `daemon/tools/help.py:41` | Opportunistic stale-docstring fixes |
| Standard 3-step seam | `CATEGORY_MODULES` entry + `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` regen + factory/extend in `create_instance_tools` + loader warm-list + cold-boot doc test (identical under every option) |

### **CRITICAL DISCOVERY — A14: the frozenset is TRIPLE-pinned, not double**

The original D4 enumerated two pin files. There are **THREE** exact-equality asserts (architect-verified by grep on the latest tree):

```
tests/integration/test_maintenancer_spawn_resolves_tools.py:292
tests/unit/tools/test_attestation_registration.py:158
tests/unit/tools/test_upgrade_registration.py:105
```

Missing the integration pin is the one concrete way this PR ships red late. **The D4 same-PR pin list MUST be THREE files, not two, before implementation.** (A fourth family — `tests/unit/tools/test_privileged_category_system_log.py:301-310`, asserting `_baby_template` allow ∩ privileged = ∅ — stays green under A since v1 grants no agent `service`.)

### Riders (cheap, security-relevant — adopt in Phase 1.C / 3)

1. **Innate-skill negative pin:** assert no `INNATE_SKILL_TOOL_CATEGORIES` value ever intersects `PRIVILEGED_TOOL_CATEGORIES` (`instance.py:157-164`, `:186-197` append to `allow` regardless of frozenset membership — a pre-existing bypass seam that `service` makes worth pinning). Adopt in **Phase 3** (security test pack).
2. **SC-6 behavioral test** (already specified at `plan-overview.md`): default-configured agent resolves zero `service_*` tools; `tools.allow=["service"]` resolves all five. Adopt in **Phase 1.C.7** acceptance (the cold-boot fixture verifies the negative case).
3. **Runtime authority riders (optional):** PID-ownership verification in `service_stop` may layer the `ENSEMBLE_PROC_TRACKING_ID` env-tag check (`proc_tools.py:228-242`) on top of the `(pid, start_time)` match (defense in depth, optional); the schema already carries `started_by_instance_id` + `started_by_agent_id` audit columns (repudiation gap adequately closed for v1; `daemon_instance_id`/`work_id` additive later if OQ#2 ever reopens).

### OQ#1 disposition (per `architecture-recommendation.md` §1.5 — OVERRULES planner lean)

Planner leaned "YES, ship `default_open` follow-up." **OVERRULED.** The `default_open` refactor is NOT pre-committed as a follow-up. It lands only if per-category metadata earns its keep independently (e.g., a second non-privileged default-closed category actually arrives), as its own tidy PR on a green baseline, carrying its own exact-equality pin family. Do not couple it to this feature.

### Original caller-stated rationale (preserved)
- **Caller-stated lean is (b)**: persistent unmanaged processes are risky enough to be opt-in, but not "daemon-internal trust escalation" like the existing 3 privileged categories (which are break-glass / direct-DB / restart authority).
- **Default-closed protects against the blast radius**: an unmanaged detached process spawned by a compromised or buggy agent can fork, bind ports, fill disk, exfiltrate data. Default-granting this to any agent with no `tools` config is too permissive.
- **`tools.deny` is NOT used** — the `_strip_privileged_category_tools` helper strips the entire category from the universe, not adds a deny rule. Deny rules are for surgical exclusions within an allowed category; this is the opposite (excluded category entirely).

### Reversibility (architect-confirmed)
Easy — remove `"service"` from the frozenset; update **THREE** pin tests. Net effect: `service` becomes default-open. Cost: zero code changes, but every existing agent that relied on `tools.allow: ["service", ...]` keeps working. (Nothing breaks.)

---

## D5 — Abuse guards

### Question
Max concurrent services per daemon, name uniqueness, PID-reuse detection, zombie/orphan handling, stop-idempotency. Plus: command allowlist?

### Decision
- **Max concurrent services per daemon**: `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT` (default 10, env > yaml > default-10, `Field(ge=1)`).
- **Name uniqueness**: partial unique index `idx_service_tracking_name_active` filtered by `status IN ('starting','running')` — lets a new service reuse a dead one's name.
- **PID-reuse detection**: `(pid, start_time)` triple stored at start; verified at `service_stop` time. See D3 pseudocode.
- **Zombie/orphan handling**: NONE — the kernel reparents detached procs to launchd/init. The daemon does NOT reap. `ServiceReconciliationService` only marks rows EXITED; it never signals.
- **Stop-idempotency**: `service_stop` on an already-exited row returns the stored exit shape (not error). Mirrors the precedent in `vscode_server_manager.stop` exit path.
- **Command allowlist**: NOT in v1. See rationale below.

### Rationale

- **Max concurrent = 10** matches the proc precedent (`MAX_PROCESSES_PER_INSTANCE = 10` at `proc_tools.py:85`). Same scale, same family of resource concerns (RAM for process state, file descriptors, port bindings).
- **Name uniqueness via partial index** lets operators re-use names after a service dies (`service_start` with the same name as an EXITED row succeeds, replacing the old row). Simpler than a full unique index on `name` (which would prevent reuse forever).
- **PID-reuse detection via start-time** is the only safe non-env-tag approach. The existing `ENSEMBLE_PROC_TRACKING_ID` env tag (`proc_tools.py:114-118`) requires injecting an env var into the child, which we can do for services too, but start-time is a stronger signal (kernel-recorded, not user-space). Linux: `/proc/<pid>/stat` field 22 (starttime jiffies since boot). macOS: `ps -o lstart` parse. Cross-platform helper in `service_spawner.get_process_start_time(pid) -> int | None`.
- **No reaping**: `start_new_session=True` reparents the child to launchd/init. The daemon has no `/proc` walk (research finding: `killpg(0)` and ppid traversal absent from `daemon/`). Adding one would violate the architectural constraint. The kernel reaps zombies; the daemon just updates DB rows.
- **Stop-idempotency** matches the operational expectation: "stop this thing, I don't care if it's already stopped" is a sensible primitive. Erroring on already-stopped is a foot-gun.
- **NO command allowlist in v1** because:
  - The agent already has `bash` and `proc_run` which can run ANY command. Adding a command allowlist to `service` while `bash` and `proc_run` remain unrestricted is security theater.
  - If the operator wants sandboxing, that is a separate feature (out of scope for `service` v1; tracked as OQ#4).
  - The risk surface is bounded by the max-concurrent cap (10 services × worst-case resource usage) — that's the practical limit, not a per-command filter.

### Alternatives rejected

- **Per-instance cap instead of per-daemon**: per-daemon is simpler (single counter on `ServiceManager`), matches operator mental model ("how many services is this daemon running total"), and per-instance cap is artificial (a service is by-design not owned by an instance).
- **Command allowlist**: see above (security theater without sandboxing).
- **Reaping detached procs**: violates architectural constraint; not necessary (kernel handles it).

### Evidence (file:line)

- Per-instance proc cap precedent: `daemon/tools/proc_tools.py:85` (`MAX_PROCESSES_PER_INSTANCE = 10`).
- PID-reuse defense precedent: `daemon/tools/proc_tools.py:228-242` (`_verify_pid_ownership` reads env tag); `:918-1035` (`_attempt_kill_signal` 3-layer defense).
- Stop-idempotency precedent: `daemon/services/vscode_server_manager.py:458-471` (code-server stop with exit handling).
- Orphan-reparent behavior: documented in `daemon/tools/bash.py:74-79` and `daemon/tools/proc_tools.py:1629-1634` ("setsid-detached grandchildren unreachable").
- Partial index SQL: `CREATE INDEX IF NOT EXISTS idx_service_tracking_name_active ON service_tracking (name) WHERE status IN ('starting','running');` — SQLite supports partial indexes since 3.8.0; PG supports them since 9.5.

### Reversibility
Easy for cap (env var flip). Medium for uniqueness policy (partial index can be replaced with full unique index via migration; migration risk). Hard for PID-reuse defense (removing it re-introduces a known hazard class — must coordinate with caller).

---

## D6 — Daemon-restart reconciliation

### Question
How does the daemon re-discover services after restart? Re-spawn? Re-attach? Mark-dead?

### Decision
**Boot sweep**: scan `service_tracking` rows in `status IN ('starting','running')`; for each, verify `(pid, start_time)` matches a live process; mark EXITED on mismatch. **NO re-spawn.** Mount as `ServiceReconciliationService` periodic service in lifespan boot, mirroring `EligiblePendingSweepService`.

### Rationale

- **Re-spawning is racy**: the old process may still be alive (the new daemon has no way to know without a sweep first); the command may have side effects (running `npm install` twice); the cwd may no longer exist; the env may have changed; the agent that started the service may not exist anymore (revived but not respawned).
- **Re-attach is implicit**: the daemon doesn't NEED to attach to the process — the process is detached by `start_new_session=True`, owned by launchd/init. The daemon's only job is to know which PIDs are "ours" so we can `service_stop` them later. A row with a live PID + matching start-time IS the re-attachment — the daemon holds no in-memory handle.
- **Mark-dead is the safe default**: a row whose PID is gone or whose PID was recycled (start-time mismatch) gets `status='exited'`. Operators see `[ServiceTool] reconcile_reaped name=X pid=Y reason=pid_recycled` in the boot log and can decide what to do (probably nothing — the service is gone, the row is historical).
- **Periodic sweep (default 90s)** matches the `EligiblePendingSweepService` cadence (`DEFAULT_SWEEP_INTERVAL_SECONDS=90` at `eligible_pending_sweep.py:64`). Same interval for symmetry; the sweep cost is trivial (N rows × 2 syscalls = milliseconds at default cap of 10).

### Mount point — amended per A5 (mount fix) + A6 (guaranteed boot pass) + A8 (config naming + stop semantics + None-guard)

In `daemon/api.py` lifespan boot, **re-locate by symbol** (architect A10 — anchor drift; current anchors: eligible `:636-682`, orphan `:698-733`, JobLockSweep `:735-780` MISSING from plan's precedent set, shutdown `:1512-1556`, vscode boot `:1167-1230`; plan's `:1400-1406` is LiveEventHub shutdown). After the existing three sweep services and before vscode boot:

```python
# New block — A5: getattr off the MANAGER (the manager singleton holds the
# ServiceManager per D4/decision §"Factory closure needs a shared ServiceManager"),
# NOT off app.state (app.state holds the SERVICE instance for shutdown only;
# cf. api.py:648-662 reads manager._task_repo/_worker_pool; api.py:675 stores
# app.state.eligible_pending_sweep — the same house pattern). Without this fix
# the reconciliation service is born receiving None and NEVER runs.
# A6: awaited guaranteed boot pass BEFORE yield so restart-survival cannot race
# the first tick (precedent api.py:397 recover_stale_job_locks; orphan_watcher_sweep.py:92-94).
# A8: config key renamed to service_tool_reconcile_interval_seconds (house
# {name}_interval_seconds convention; ServicesConfig Field(ge=1) fail-fast).
try:
    from daemon.services.service_reconciliation import ServiceReconciliationService
    # A8: read the field directly (house pattern); no getattr fallback.
    interval_seconds = config.services.service_tool_reconcile_interval_seconds
    # A5: get the manager — ServiceManager is on InstanceManager, NOT on app.state.
    service_tool_manager = getattr(manager, "_service_tool_manager", None)
    # A8: None-manager guard — skip start + log DISABLED (template DEBUG no-op
    # precedent eligible_pending_sweep.py:308-320).
    if service_tool_manager is None:
        logger.warning("ServiceReconciliationService DISABLED (no service_tool_manager)")
    else:
        svc = ServiceReconciliationService(
            repo=service_tool_manager.repo,  # A9: inject ServiceRepo directly (narrowest collaborator)
            interval_seconds=interval_seconds,
        )
        if svc.interval_seconds < 1:
            logger.error("ServiceReconciliationService DISABLED (interval < 1)")
        else:
            await svc.start()
            # A6: awaited guaranteed boot pass — prevents restart-survival from
            # racing the first tick. If the first sweep fails, log loudly and
            # continue (do not abort boot).
            try:
                boot_counters = await svc.sweep_once()
                logger.info(
                    "[ServiceTool] reconcile_boot_sweep alive=%s reaped=%s errors=%s",
                    boot_counters["alive"], boot_counters["reaped"], boot_counters["errors"],
                )
            except Exception:
                logger.exception("ServiceReconciliationService boot sweep failed; continuing")
            # A5: store the SWEEP SERVICE on app.state (for shutdown only); the
            # underlying ServiceManager lives on the InstanceManager facade.
            app.state.service_reconciliation_service = svc
            logger.info(
                "ServiceReconciliationService started: interval=%ss",
                svc.interval_seconds,
            )
except Exception:
    logger.exception("Failed to start ServiceReconciliationService; continuing without reconciliation")
```

Lifespan shutdown (`api.py:1512-1556` pattern — A10 re-anchored):

```python
svc = getattr(app.state, "service_reconciliation_service", None)
if svc is not None:
    try:
        # A8: template `set()` → `cancel()` → `await` semantics; CancelledError swallowed.
        await svc.stop(timeout=5.0)
        logger.info("ServiceReconciliationService stopped")
    except Exception:
        logger.exception("ServiceReconciliationService shutdown error")
```

### Sweep pseudocode — amended per A3 (eternal-`starting` reaper) + A8 (drop dead `max_concurrent` param) + A9 (inject `ServiceRepo` directly) + A12 (TEXT ISO-8601 repo-side bumps) + A13 (atomic-guard UPDATEs)

```python
# daemon/services/service_reconciliation.py
class ServiceReconciliationService:
    DEFAULT_SWEEP_INTERVAL_SECONDS = 90
    # A8: max_concurrent is on ServiceManager, not the reconcile service.
    # A3: starting-row grace window — a crash between INSERT and Popen leaks a
    # row that counts against the cap forever; reap after grace elapses.
    DEFAULT_STARTING_GRACE_SECONDS = 30

    def __init__(self, repo, interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
                 starting_grace_seconds=DEFAULT_STARTING_GRACE_SECONDS):
        # A9: narrowest collaborator — ServiceRepo directly, not via ServiceManager.
        self._repo = repo
        self._interval_seconds = interval_seconds
        self._starting_grace_seconds = starting_grace_seconds
        self._stop_event = asyncio.Event()
        self._task = None
        self.errors = 0

    async def start(self):
        if self._task and not self._task.done():
            return  # idempotent
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="service-reconciliation")

    async def stop(self, timeout=5.0):
        # A8: template `set()` → `cancel()` → `await`; CancelledError caught + swallowed.
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                await self.sweep_once()
            except Exception:
                self.errors += 1
                logger.exception("ServiceReconciliationService.sweep_once failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def sweep_once(self) -> dict:
        """Returns counters: {alive: N, reaped: M, errors: K, starting_reaped: S?}."""
        counters = {"alive": 0, "reaped": 0, "errors": 0, "starting_reaped": 0}
        active_rows = await asyncio.to_thread(self._repo.list_active)
        for row in active_rows:
            try:
                # A3: eternal-`starting` row reaper. A crash between INSERT and
                # Popen (or a paused spawn interrupted by a long-lived CancelledError)
                # leaves `pid IS NULL` rows that count against the cap forever.
                # After grace_seconds, reap with reason=spawn_failed_or_interrupted.
                if row.pid is None:
                    age = await asyncio.to_thread(_row_age_seconds, row)
                    if age >= self._starting_grace_seconds:
                        await asyncio.to_thread(self._repo.mark_exited, row.id, exit_code=None)
                        logger.warning(
                            "[ServiceTool] reconcile_reaped name=%s reason=spawn_failed_or_interrupted "
                            "age=%ss grace=%ss",
                            row.name, age, self._starting_grace_seconds,
                        )
                        counters["starting_reaped"] += 1
                    # else: still inside grace window — leave alone, spawn may be in progress
                    continue
                current_start = await asyncio.to_thread(service_spawner.get_process_start_time, row.pid)
                if current_start is None:
                    # A13: repo.mark_exited guarded `WHERE id=? AND status IN (...)`
                    await asyncio.to_thread(self._repo.mark_exited, row.id, exit_code=None)
                    logger.info("[ServiceTool] reconcile_reaped name=%s pid=%s reason=dead", row.name, row.pid)
                    counters["reaped"] += 1
                elif current_start != row.start_time:
                    await asyncio.to_thread(self._repo.mark_exited, row.id, exit_code=None)
                    logger.warning("[ServiceTool] reconcile_reaped name=%s pid=%s reason=pid_recycled expected_start=%s got=%s",
                                   row.name, row.pid, row.start_time, current_start)
                    counters["reaped"] += 1
                else:
                    counters["alive"] += 1
            except Exception:
                counters["errors"] += 1
                logger.exception("Reconcile error for row id=%s name=%s", row.id, row.name)
        if counters["reaped"] > 0 or counters["starting_reaped"] > 0:
            logger.info("[ServiceTool] reconcile_swept alive=%s reaped=%s starting_reaped=%s errors=%s",
                        counters["alive"], counters["reaped"], counters["starting_reaped"], counters["errors"])
        return counters
```

### Alternatives rejected

- **Re-spawn on boot**: see rationale above (racy, side-effectful, env-dependent).
- **Read-only sweep (mark nothing)**: leaves stale RUNNING rows forever; operator gets no signal that a service is dead.
- **No periodic sweep (only inline liveness in `service_status`)**: `service_list` would also need inline liveness for every row (acceptable but doubles syscalls for the common case where the agent just wants a list, not a status).

### Evidence (file:line)

- Sweep template: `daemon/services/eligible_pending_sweep.py:60-215` (canonical pattern).
- Lifespan boot: `daemon/api.py:600-654` (EligiblePendingSweep); `:656-696` (OrphanWatcherSweep).
- Lifespan shutdown: `daemon/api.py:1367-1397` (getattr-guarded stops).
- Orphan-reparent: documented in `daemon/tools/bash.py:74-79` and `daemon/tools/proc_tools.py:1629-1634`.

### Reversibility
Easy — disable via `ENSEMBLE_SERVICE_TOOL_ENABLED=0` (kills the sweep); the table still works, just no auto-reconcile.

---

## D7 — Observability

### Question
Start/stop/reconcile log lines, boot-probe per convention, kill-switch env flag.

### Decision

- **Per-call INFO logs**: `[ServiceTool] service_started name=… pid=…`; `[ServiceTool] service_stopped name=… pid=… exit_code=…`; `[ServiceTool] reconcile_reaped name=… pid=… reason=…`; `[ServiceTool] reconcile_swept alive=… reaped=… errors=…` (only when reaped > 0).
- **Boot probe** at `load_config` resolution time (NOT lazily) — `logger.info("[ServiceTool] service_tool_enabled=%s (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=%s, reconcile_interval=%ss")`. Mirrors `config.py:3513-3518`, `:3529-3536`, `:3555-3565`, `:3581-3591`.
- **Kill-switch**: `ENSEMBLE_SERVICE_TOOL_ENABLED` (bool, default ON, `_resolve_*` resolver wired per `config.py:2702-2770`).
- **Knob envs**:
  - `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT` (int, default 10, `Field(ge=1)`).
  - `ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL` (int seconds, default 90, `Field(ge=1)`).

### Rationale

- **Boot probe at resolution time** is mandatory: lazy first-call emit makes cold-boot log greps false-fail (per `config.py:3502-3509` S13 reviewer gate rationale — multiple restart-pending merges have shipped with the bug, costing hours of "did the feature activate?" forensics).
- **`_resolve_*` wiring is mandatory**: without it, pydantic-settings treats the init kwarg as beating env, silently defeating operator kill-switches (`config.py:3421-3444` trap). Follow `config.py:2702-2770` (precedence pattern: env > yaml > default-ON, empty-string safe).
- **Section-absent YAML handling**: per `config.py:3474-3496` review MAJOR-2 finding, configs omitting the YAML section must still honor the env kill-switch. Read env at TOP LEVEL of `load_config`, outside the `"section" in config_data` guard.
- **Per-call INFO logs are sufficient for v1.** Structured counters + telemetry is a v2 ticket (post-merge) — the per-call logs carry enough signal to grep.

### Config wiring

```python
# daemon/config.py (additions near the existing service-area configs)

def _resolve_service_tool_enabled(ens_value: Optional[str], yaml_value: Optional[bool]) -> bool:
    """Precedence: ENSEMBLE_SERVICE_TOOL_ENABLED env > yaml > default True.
    Empty-string safe (config.py:2738-2747)."""
    cleaned = _clean_env_value(ens_value)
    if cleaned is not None:
        return cleaned.lower() in ("1", "true", "yes", "on")
    if yaml_value is not None:
        return yaml_value
    return True  # default ON

# In load_config, at TOP LEVEL (outside section guard):
ens_service_enabled = os.environ.get("ENSEMBLE_SERVICE_TOOL_ENABLED")
resolved_service_enabled = _resolve_service_tool_enabled(ens_service_enabled, None)
_install_service_tool_enabled(resolved_service_enabled)  # module cache
logger.info("[ServiceTool] service_tool_enabled=%s (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=%s, reconcile_interval=%ss",
            resolved_service_enabled,
            os.environ.get("ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT", "10"),
            os.environ.get("ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL", "90"))

# In ServicesConfig (or new ServiceToolConfig):
class ServiceToolConfig(BaseModel):
    enabled: bool = True
    max_concurrent: int = Field(default=10, ge=1)
    reconcile_interval_seconds: int = Field(default=90, ge=1)
```

### Alternatives rejected

- **Lazy emit**: forbidden by the S13 reviewer gate. Always emit at resolution time.
- **Bare pydantic bool field without `_resolve_*`**: forbidden by the init-kwarg trap (`config.py:3421-3444`).
- **Telemetry counters in v1**: deferred (no shipped operator dashboard consumer; per-call logs are enough).

### Evidence (file:line)

- Boot probe patterns: `daemon/config.py:3513-3518`; `:3529-3536`; `:3555-3565`; `:3581-3591`.
- Resolver template: `daemon/config.py:2702-2770` (`_resolve_proactive_enabled`); `:3097-3139` (strict-parse flavor).
- Trap: `daemon/config.py:3421-3444` (pydantic init-kwarg > env).
- Section-absent handling: `daemon/config.py:3446-3496` (review MAJOR-2).
- Default-ON contract: `daemon/config.py:3543-3544, :3572-3574` (OFF = byte-identical legacy).

### Reversibility
Easy — kill-switch flip; knob envs can be unset. To permanently remove, delete from config.py + delete the boot-probe log line.

---

## Open Questions (architect escalation — ALL RESOLVED per `architecture-recommendation.md` §4, leader-ratified 2026-09-15)

### OQ#1 — Default-closed non-privileged category mode → **RESOLVED: Option A**
The `default_open` per-category attribute refactor is **NOT** pre-committed as a follow-up. The `default_open` mechanism lands only if per-category metadata earns its keep independently (e.g., a second non-privileged default-closed category actually arrives), as its own tidy PR on a green baseline, carrying its own exact-equality pin family. Do not couple it to this feature. (Planner lean was YES; **OVERRULED** by architect per `architecture-recommendation.md` §1.5.)

**Question (original):** Today, the only way to gate a category out of default-allow is `PRIVILEGED_TOOL_CATEGORIES`. For `service`, this triggers the deny-rule scaffolding in `_strip_privileged_category_tools` even though we don't want deny rules. Should we introduce a per-category `default_open: bool = True` attribute (default-true for backward compat) so `service` can be opt-in without going through the privileged frozenset? Cost: ~30 LOC in `_tool_registry.py` + `_apply_tool_filter`. Reversible.

### OQ#2 — Multi-daemon against the same DB → **RESOLVED: document single-daemon-only in v1**
The `service_tracking` audit columns (`started_by_instance_id`, `started_by_agent_id`) suffice for v1; add `daemon_instance_id` only if multi-daemon becomes a real ask. Document the single-daemon-only constraint in the OPS note + `_full_doc_` of every tool.

### OQ#3 — Log rotation → **RESOLVED: operator-side**
Cap `MAX_LOG_TAIL_BYTES=10MB` in `service_logs`; document disk-fill risk in `_full_doc_` + ops note. Matches the project's "no daemon-managed rotation" pattern (`data/logs/ensemble.log` is operator-rotated).

### OQ#4 — Command sandboxing → **RESOLVED: out of scope v1**
No command allowlist in v1 — security theater while `bash`/`proc_run` are unrestricted; risk bounded by cap=10 + default-closed category (D4). Revisit only alongside a broader sandbox story.

### OQ#5 — Cross-platform start-time verification → **RESOLVED: ps-parse acceptable**
`ps`-based macOS start-time parse acceptable (precedent `_verify_pid_ownership` uses `ps eww` at `proc_tools.py:355`); document locale-format risk in a comment; `NotImplementedError` on Windows.

### OQ#6 — `service_stop` semantics on `force=True` → **RESOLVED: keep 5s wait**
Keep the 5s wait after SIGKILL even on `force=True` (consistency with `proc_stop`); `force` only skips the SIGTERM phase. (Implemented async per A2 — the wait is no longer a busy-wait.)

### OQ#7 — `service_logs` output format → **RESOLVED: plain text tail**
No format-aware parsing in v1; if the LLM wants structured output from a JSON-writing service, it post-processes.

---

## Cross-module risks (mentioned in caller prompt)

### Factory closure needs a shared ServiceManager on the facade

**Pattern decision:** `ServiceManager` is **manager-held** (not module singleton), following the `_mcp_service` and `_task_repo` precedent (`manager.py:690-693`, `instance_lifecycle.py:2324-2326`).

**Rationale:**
- The max-concurrent cap and reconcile-sweep coordination need a single source of truth across all `service_*` tool calls and the lifespan sweep. A module singleton would split the source of truth and break pause-first-then-quiesce coordination.
- The factory `create_service_tools(manager, current_instance_id, agent_id, version_tag)` takes the manager as the first arg, matching the `create_system_log_tools` / `create_upgrade_tools` precedent (`instance.py:4583, :4593`).
- Inside the factory, the manager singleton is grabbed via `manager._service_tool_manager` (a property exposed for the lifespan boot's `getattr` access). The tools dereference the manager only at CALL time so the loader's `None`-manager warm stubs still work (`daemon/loader.py:76-90`).
- The actual spawn helper (`service_spawner.spawn`) is a pure module-level function — no state, no manager dependency. Mirrors `upgrade_journal.spawn_executor` exactly.

### New table = no dual-write concerns

The new `service_tracking` table is the only source of truth for service state. There is no in-memory state to mirror (no registry like `BashProcessRegistry`). Reconcile sweeps compare DB rows against the OS process table; that's the only "two sources of truth" and the sweep reconciles them.

### Interaction with pause

Pause cancels the graph_task (`instance_lifecycle.py:3151-3173`); it does NOT sweep the service registry. **This is intended** — services are by-design not owned by the instance. An in-flight `bash` call inside `service_start` would be killed by the pause (CancelledError → `_kill_process`); but `service_start` is fast (Popen returns in ms), so the window is tiny. Document in `service_start`'s full_doc that pause can interrupt an in-progress spawn, leaving the row in `status='starting'`.

**A3 amendment (folded in):** the eternal-`starting` row hazard is closed by the reconcile sweep's grace reaper — after `service_tool_starting_grace_seconds` (default 30s), rows still in `status='starting'` with `pid IS NULL` are marked EXITED with `reason=spawn_failed_or_interrupted`. See D6 sweep pseudocode (amended per A3).

### What happens if a service process dies without `service_stop`

The row stays `status='running'` until either:
1. The next `service_status` / `service_list` call (inline liveness check).
2. The next reconcile sweep (every 90s default).

When detected, the row is marked `status='exited'` with `exit_code=None` (we don't have the exit code — the process is detached and we never waited on it). The log shows `[ServiceTool] reconcile_reaped name=X pid=Y reason=dead`.

This is "eventually consistent" — operator-visible state lags reality by up to 90s. Acceptable for a background-process tracker; document in the tool's full_doc.

### Test strategy shape (brief; the plan worker phases this)

- **Unit tests** under `tests/unit/services/test_service_reconciliation.py` — sweep kills nothing, marks EXITED on liveness mismatch, idempotent start/stop.
- **Unit tests** under `tests/unit/tools/test_service_registration.py` — pins both PRIVILEGED_TOOL_CATEGORIES exact-equality assertions AND the factory + CATEGORY_MODULES + extend seam.
- **Unit tests** under `tests/unit/repositories/test_service_tool_repository.py` — file-backed SQLite (tmp_path + NullPool + WAL + busy_timeout=10000, per `test_chart_tools_reuse_integration.py:80-109`); INSERT / SELECT / UPDATE / partial-unique-index / status transitions.
- **Schema-pin test** mirroring `tests/unit/test_ensure_deferred_schema_pin.py:477` (create_all-vs-migration-chain gap).
- **Boot-probe pack** mirroring `test/packs/boot_probes_unit_test.sh` — `[ServiceTool] service_tool_enabled=` regex in cold-boot log.
- **PG smoke pack** mirroring `test/packs/ensure_deferred_pg_smoke_integration_test.sh` — proves prod-shaped legacy DDL + `_ensure_postgres_columns` self-heal.
- **Flag-ON-real-service / flag-OFF-byte-identical pins** — the OFF state must produce zero `service_*` tool surface in any agent's resolved tool list (privilege strip is identical to the system_upgrade precedent).
- **PID-reuse defense test** — spawn service A, kill PID from outside (so the slot is free), spawn service B (gets same PID), call `service_stop(A.name)`. Expect: `service_stop` returns `reason=pid_recycled`, does NOT signal service B, marks A row EXITED.

Run via `uv run python -m pytest` from worktree root (the bare `pytest` Homebrew PATH trap per Testing & QC blueprint).

---

## ADR-tie (architectural decisions referenced)

This decisions.md ties back to the following ADRs and architectural decisions (per `decisions.md` precedent at `.agents/shared/planning/spawn-intelligence-override/decisions.md`):

- **Pause-first then quiesce convention** (Core blueprint) — used for the `ServiceReconciliationService` boot (`api.py:619-654` pattern).
- **Repository pattern + dual-dialect migrations** (Production-DB blueprint) — used for the new `service_tool` domain and the 3-site index pattern.
- **Registry-scoped process kill invariant** (kill-site research) — the design rests on NOT registering services in any registry.
- **`start_new_session=True` precedent** (`upgrade_journal.py:1010-1034`) — D1.
- **Tool registration 3-step seam** (`_tool_registry.py:131-156 + :479-525 + instance.py:4392-4649`) — D4 + D7.
- **Boot-probe convention** (`config.py:3502-3591`) — D7.
- **Periodic service loop template** (`eligible_pending_sweep.py:60-215`) — D6.
- **`PRIVILEGED_TOOL_CATEGORIES` double-pin rule** (`tests/unit/tools/test_upgrade_registration.py:105-109` + `tests/unit/tools/test_attestation_registration.py:158-162`) — D4.

---

## Status tracker

| Decision | Owner | Architect review needed | Open questions |
|----------|-------|-------------------------|----------------|
| D1 | implementation | NO (precedent-bound) | OQ#5 (start-time platform) |
| D2 | implementation + schema reviewer | NO (pattern-bound) | OQ#2 (multi-daemon) |
| D3 | implementation | NO (API surface) | OQ#6 (force=True), OQ#7 (log format) |
| D4 | **architect** | **YES (mechanism gap OQ#1)** | OQ#1 (default-closed mode) |
| D5 | implementation | NO (precedent-bound) | OQ#4 (sandboxing) |
| D6 | implementation + boot reviewer | NO (template-bound) | OQ#3 (log rotation) |
| D7 | implementation + config reviewer | NO (convention-bound) | — |

---

## As-Built / Close-out (2026-09-15 — service-tool docs track, plan tasks 3.B.3 / 3.B.3a / 3.B.7 F4 / 3.B.4)

**OQ fold-in verification (3.B.4 precondition):** the §"Open Questions (architect escalation — ALL RESOLVED …)" section above carries all seven OQs with dispositions (OQ#1 Option A default_open NOT pre-committed; OQ#2 single-daemon-only documented; OQ#3 operator-side rotation; OQ#4 no command allowlist v1; OQ#5 ps-parse acceptable; OQ#6 keep 5s wait, async per A2; OQ#7 plain-text tail). The fold-in is **intact** — nothing re-derived, nothing rewritten by this close-out.

### Phases shipped (commit range base `ebd57cfc`; 19 commits through `b9d642f0` observed at authoring time)

Base `ebd57cfc` = "Merge latest (f6ca8791: critical-notes-retrieval + fix-job-queue-timestamps-tz) into feature/service-tool". `git log --oneline ebd57cfc..HEAD` is the live authoritative list; **a concurrent session kept landing Phase-3 test commits while this close-out was authored** (e.g. `962ce42b` resolver/boot-probe coverage, `0a63e1f8` innate-skill negative pin 3.B.3b, `dee1766a` F10 exit_code doc pins) — commits after `b9d642f0` are not individually mapped in the table below.

| Phase | Commits (short SHAs, oldest → newest) |
|---|---|
| 1.A — `service_tracking` store, repository, dual-dialect migration | `9d2ab405`, `a005f6dc` |
| 1.B — `ServiceToolManager` + spawner + 5 service tools (incl. D4 frozenset add, review follow-ups, docstring folds) | `d5001641`, `1cb570d0`, `a9e7699c`, `83abbe1f` |
| 1.C — config knobs + resolution-time boot probe, reconciliation skeleton, manager wiring + PG mirror, A7 grep-gate pack, 3-step registration seam, lifespan mount, seam/SC-6 pins, empty-yaml guard fix | `ea4ae0d2`, `27b0b2b8`, `61ae2af3`, `d687bbde`, `540859f0`, `8ca43a9e`, `c1571a29`, `e0ed1afc` |
| Phase 2 — reconciliation sweep (2.A), 13-site kill-exemption matrix + F9 cascade (2.B), reconcile e2e + A13 race (2.C), review fixes | `b742e2d7`, `5aad0a25`, `5905d995`, `b07c84ab` |
| Phase 3 — tests + CI gate landed with the phases above; docs track closes the 3.B docs items (3.B.3 / 3.B.3a architecture doc, 3.B.7 F4 OPS note, this close-out) | `b9d642f0` (3.MG.1 merge-gate runbook, assembled by a concurrent session) + the docs-track commit(s) on top |

### Deviations from plan

Deviations are documented **in-code and in commit messages** (each commit message names the plan task and any amendment it folds in — A1–A14, F-series). This close-out deliberately does not re-narrate them; `git log ebd57cfc..HEAD` is the authoritative record.

### Docs deliverables (this track)

- `docs/architecture/instance-lifecycle.md` — the kill-exemption invariant (plan 3.B.3 + 3.B.3a): causal statement, by-construction proof shape over K1–K13, regression net (the parametric matrix), the A7 CI grep-gate allowlist and same-PR rule, the STANDING registry-scoped rule for future kill-site authors, the registry limitation `service` solves, and the CODEOWNERS-TODO note.
- `docs/service-tool.md` — feature/ops note (plan 3.B.7 incl. F4 Deployment/Activation): verified knob/env surface, restart-pending semantics, rebuild-vs-restart activation, boot-probe verification recipe, kill-switch-vs-activation distinction, post-deploy manual restart-survival recipe, cap-exceeded operator recovery, and the accepted-limitations table (F18 TOCTOU advisory cap, CODEOWNERS pending, F14/OQ#1 revisit trigger, OQ#2 single-daemon, F15/F16/F22, OQ#3).
- **3.A.7 amendment (review-found):** W2 `list_all` OFF-gate was a real byte-identical-contract gap found and fixed in review — `daemon/services/service_tool_manager.py` `list_all` was performing inline `mark_exited` writes under the OFF path; commit `ebd8a5e6` gates `list_all` on the enabled flag (byte-identical OFF contract restored).

### Packaging note

This file was **untracked** at the time the docs track ran. The docs-track commit stages **exactly this path** (`git add .agents/shared/planning/service-tool/decisions.md`) and nothing else under the plan directory — the remaining plan-package files (plan-overview, phase1/2-plan, research-\*, architecture-recommendation, technical-analysis) stay untracked for the separate plan-package commit owned by another session. `phase3-plan.md` was committed separately by that session (commit `b9d642f0`) while this track was in flight.
