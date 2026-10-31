# Decisions Addendum — Bash-Tool Orphan Reaper (implementation)

Companion to `plan.md` / `plan-overview.md`. Records implementation-time
decisions that refine (never contradict) the approved plan.

## 0. Activation (restart-class)
Activation is RESTART-CLASS: rebuild + restart the daemon. The
`bash_spawn_registry` table lands via `SQLModel.metadata.create_all` at
manager init on the first boot after activation; rows start flowing at the
FIRST bash spawn post-restart. The one-shot boot reconcile sweeps
pre-restart rows only on the SECOND restart that has rows from a previous
boot — the first boot after activation has ZERO previous-boot rows (the
table was just created), so the reconcile is a no-op there by construction.

## 1. Named-unique-index choice (SQLite anonymous autoindex rationale)
`tracking_id` uniqueness is a NAMED unique index (`uq_bash_spawn_registry_tracking_id`,
`Index(..., unique=True)` in `__table_args__`), NOT a field-level
`unique=True` UniqueConstraint. Rationale: SQLite renders an unnamed
UniqueConstraint as an ANONYMOUS autoindex (`sqlite_autoindex_...`), so
`create_all` (PG/manager path) and the companion `.sql` migration (SQLite
path) would produce differently-named objects, breaking the 3-site name
lockstep (model / migration / storage test pack). A named `Index(unique=True)`
renders identically on both dialects. The query-relevant indexes remain the
two partial open-subset indexes plus the plain `(pgid)` index.

## 2. `verify_and_kill` status/reason tuple contract
`verify_and_kill(row, ...) -> (status, reason)` where status is one of:
- `refused` — hard guard / D9 start-time gate / readable-env mismatch /
  platform refusal. Row is CLOSED by the caller with `reap_reason=reason`.
- `skip` — D8 live-sibling guard (live foreign daemon owns the row). Row
  stays OPEN.
- `reaped` — ladder executed (TERM → grace → KILL). Row closed `reaped`.
- `proceed` with `proceed:group-dead-gc` — group already dead; row GC-closed.
Additional refused tokens added in Phase 5 routed fix (B):
  - `refused:direct-child-unprobeable` — signal-0 probe raised an unexpected
    exception (fail-closed, never "not alive").
  - D8 holder unprobeable ⇒ `skip:live-sibling-daemon (unprobeable holder probe)`.

## 3. Darwin `lstart` local→UTC fix
`ps -o lstart=` emits LOCAL wall time. The naive `strptime` result is converted
via `.astimezone(timezone.utc)` (interpreting it as local) instead of being
stamped `tzinfo=timezone.utc`. Stamping UTC directly shifted every start by the
local offset (e.g. +07) and defeated both the D8 skip and the D9 gate.

## 4. Refused-row GC policy (plan §7 Q2)
Refused rows (`reap_reason='refused:*'`) are audit-only until the conservative
GC closes them: when a later sweep observes the row's group fully dead, the
row is closed with `reaped_by='gc@<pid>'`, `reap_reason='gc'`. GC never
re-arms a refused row for retry; refused is terminal for that row's identity.

## 5. Self-exemption (MAJOR-1)
Rows whose `daemon_pid == os.getpid()` (i.e. every current-boot row) ALWAYS
proceed through every lane — terminal tiers, terminate, shutdown, enumeration,
boot reconcile. Without this, the D8 guard would skip all same-boot rows and
neuter Path A closure. Implemented in `_daemon_pid_guard` as the first check.

## 6. Enumeration rides the master switch
`_enumerate_stamped_members` returns `[]` immediately when the master switch
is OFF (zero process-table scan, zero env reads). There is no separate
enumeration switch; per-phase disable is achieved by phase landing order /
the boot-leg knob. Documented in plan §4.5 and the rollback story.

## 7. Deviations
- Routed quality fix (B): narrowed the production `os.kill` probe handlers
  from `except (ProcessLookupError, OSError, Exception)` to explicit
  `ProcessLookupError` / `PermissionError` / `Exception` branches so that
  unexpected errors (including the test fence's hard-raise) are no longer
  silently swallowed as "not alive". `PermissionError` (EPERM) is treated as
  ALIVE per the plan's conservative rule. New outcome tokens:
  `refused:direct-child-unprobeable` and
  `skip:live-sibling-daemon (unprobeable holder probe)`.
- Routed quality fix (A): added the missing M8 leg
  (`TestM8UnstampedUntouched`) as a real-process test with teardown.
- Test fence (`test_bash_spawn_reaper_boot.py` TestDaemonNeverTargeted)
  updated so `os.kill` only raises for `sig != 0` (signal-0 liveness probes
  pass through); zero-signals-delivered assertion retained.
- Matrix completion (5.3 audit): added the missing real-process legs
  M3 (`TestPathCJobControlGroup`) and M4 (`TestPathDTimeoutRaceEscapee`)
  in `tests/tools/test_auto_kill_integration.py`, and the Phase-4
  enumeration unit legs (`TestEnumeratedStampSweep`, 8 nodes) plus the
  Phase-5 platform legs (`TestPlatformLegs`, 8 nodes) in
  `tests/unit/services/test_bash_spawn_reaper.py`.
## Review round 1 findings disposition

Disposition of the constraint review (REQUEST_CHANGES: 1 required + minors),
applied 2026-10-31 on `feature/bash-tool-orphan-reaper` (base `cbafcc8d9`).

- (a) **`reaped_by` labels coarser than D3's vocabulary** — `terminate` /
  `chart` / Tier-1 / Tier-2 lanes inherit the default `terminal-tier` label
  instead of a per-lane token. ACCEPTED: the plan sanctioned a
  zero-call-site-change reach for the reaper, so per-lane labels would have
  required touching each caller; the label remains diagnostic-only.
- (b) **Darwin fail-open pin location** — the setsid-escapee skip+WARNING
  (fail-open on `os.getpgid` failure) is pinned in the monkeypatched
  platform pack (`TestPlatformLegs`) rather than inline in the m8 darwin
  real-process leg. ACCEPTED as a letter-of-plan-§4.4 deviation: the
  substance (skip + WARNING, never kill-unverified) is covered by the pin;
  the m8 leg exercises the real-process path around it.
- (c) **Operational note (accepted, bounded)** — the per-row sequential
  TERM grace (default 5s) means N leaked rows can add up to N×grace to
  shutdown latency. Bounded by the row count and matches the plan's
  TERM→grace→KILL ladder; no batching was introduced (would change signal
  semantics per row).
- (d) **Review fixes applied to code** — Fix 1: `_enumerated_sweep` now
  awaits the detail pass off-loop via
  `await asyncio.to_thread(_enumerate_stamped_member_details, open_now)`
  (a slow/hung `ps` can no longer stall the daemon event loop; mirrors the
  row-level lane's `to_thread` wrapping of `verify_and_kill`). Fix 2:
  `_enumerate_stamped_member_details` is fail-closed on unparseable/missing
  row `created_at` — the row's candidates are NOT selected and a WARNING
  names the row id, mirroring the row-level `refused:start-time-unparseable`
  refusal (reachable only via a corrupted row; the writer always stamps
  `now_utc_iso`).

## Review round 2 (REJECT verdict) fix round — 2026-10-31

Applied on `feature/bash-tool-orphan-reaper` @ `b585503a5`, in the
reviewer's exact ordering (C1 → C2 → M1 → M2 → M3 → minors). plan.md /
plan-overview.md untouched (spec of record); this file is the sanctioned
extension surface.

### Residual (minor-2): spawn→insert crash window
A crash between the bash spawn returning (fork+exec complete) and the
durable `insert_open_row` landing leaves a permanently untracked orphan:
no row ⇒ no tombstone ⇒ no reaper reach, ever. Window size ≈ ms–1.5 s
(the bounded `wait_for(…, 1.5)` around the insert — larger only when the
engine is wedged, in which case the late tombstone still lands
best-effort). NOT closed structurally: closing it would require writing
the row BEFORE the spawn, which cannot know `(pid, pgid)`. The
cancel-arm `killpg` covers the ALIVE-shell case (a bash tool that is
cancelled while the spawn is still tracked in RAM is killed by the RAM
tier regardless of row state); the residual is the narrow
crash-mid-window case only. Accepted.

### Residual (minor-7, m5-i expansion): launcher-lineage carry-over
A bash-launched LAUNCHER lineage (a process the bash tool spawned that
itself launches the daemon) carries the OLD daemon's stamp + row. On
restart, the NEW daemon self-excludes via D8 only for rows whose
`daemon_pid` probe resolves to the new process — the still-running
launcher PARENT of the old boot could be enumerated by a terminal sweep
against the stale row (its env still carries the old tracking id). The
structural backstop is `restart.sh`'s exit-78-without-armed-op gate (a
restart lane that died before arming never reaches the reaper-armed
state). OPERATOR RAMP: set the master switch
`ENSEMBLE_BASH_ORPHAN_REAPER=0` if a launcher-lineage carry-over is
suspected — OFF is a complete no-op (zero reads, zero signals).

### minor-3 (reaped_by vocabulary): sanctioned coarseness
The per-lane tier distinction (tier1/tier2/terminate/chart) is
unreachable without touching every cleanup caller — the plan sanctioned
a zero-call-site-change reach. The coarse `terminal-tier` label is KEPT;
`models.py` now documents EXACTLY the values that can occur
(`terminal-tier` | `shutdown` | `boot-reconcile` | `gc`, each suffixed
`@<pid>`) and the exact `reap_reason` set (`enumerated` |
`ram-tier-dedup` | `gc` | `refused:<guard>` | NULL).

### minor-5 (durable insert executor)
The spawn-seam durable stamp now runs on a dedicated BOUNDED
executor (`bash._DURABLE_INSERT_EXECUTOR`, `max_workers=2`) instead of
the loop default executor: during a PG outage a spawn burst queues
instead of pinning default-executor threads for the 1.5 s bounded wait
each (late-tombstone tasks keep threads busy after the wait gives up).
Late-tombstone semantics unchanged — the detached insert still lands.

### minor-10 (macOS lstart DST)
`process_start_wallclock` on darwin/BSD treats a DST-AMBIGUOUS local
`lstart` (fall-back window — the wall clock occurs twice, `ps` carries
no fold) as UNPARSEABLE ⇒ the D9 gate fails closed. Where etime is
available it is preferred and offset-free: the Phase-4 enumeration scan
derives start as `now_utc − etime` (already the case — no wall-time
parse on that path).

### M1(c): boot reconcile moved OFF the startup critical path
`reconcile_stale_bash_spawns` is no longer awaited in the lifespan — it
runs as a post-startup background task (`asyncio.create_task`, strong
module-level reference, non-fatal WARNING on any fault). Still ONE-SHOT
(per-boot), still honors the boot leg knob, still S3-pinned (DB fault ⇒
WARNING, boot proceeds). The `test_db_fault_propagates` pin is unchanged
— the raise is the FUNCTION's contract; the background shape is the
CALLER's (api.py) contract.

### M3: env-exact stamp tolerance (delayed-detach reach)
New knob `ENSEMBLE_BASH_REAPER_STAMP_TOLERANCE_SECONDS` (default 6 h =
21600 s, clamp [60, 86400]). Applied ONLY to env-EXACT-matched Phase-4
enumeration candidates (positive stamp match); pgid-only attribution
keeps the 10 s `..._START_TOLERANCE_SECONDS` D9 gate everywhere. The m2
pre-filter is reconciled so it NEVER excludes what M3 admits: its UPPER
bound uses the stamp tolerance (M3 widens only the delayed/upper side);
its m2 LOWER bound keeps the 10 s tolerance (a process predating every
row by >10 s cannot carry a stamp whose uuid4 was minted later).
