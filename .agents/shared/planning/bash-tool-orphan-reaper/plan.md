# Implementation Plan: Bash-Tool Orphan Reaper

Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-bash-tool-orphan-reaper`
Branch: `feature/bash-tool-orphan-reaper` (base `cbafcc8d9`)
Date: 2026-10-31 (revision pass 2026-10-31: F1-F6 + m1-m6 applied; anchors re-verified
in this tree). Companion: `plan-overview.md` (constraints, discriminator design, risks).
All `file:line` anchors verified against THIS tree on 2026-10-31.

---

## 0. Design Decisions (explicit, with citations)

### D1 — Discriminator: durable bash-spawn registry + env stamp
- **Primary:** new table `bash_spawn_registry` (SQLModel in
  `daemon/repositories/bash_spawn/models.py`), written synchronously at the bash spawn
  seam. ONE write-site (F4): the bash tool body, AFTER the RAM append returns and
  OUTSIDE the registry `asyncio.Lock` — never inside `register()`
  (`bash.py:431-437`): `register()` runs under the global registry lock
  (`bash.py:433`), and `asyncio.shield` protects against cancellation but NOT against
  a wedged PG socket (plausible on shared `ensemble_dev`), so an in-lock insert would
  serialize ALL bash spawns on DB latency. The call-site sits after the
  command-sanitize block (`bash.py:844-846`) so the already-computed
  `sanitized_command` is reused (no hoist into `register`, no double computation) —
  call-site ≈ `bash.py:876`, just before the timeout try. Form:
  `asyncio.wait_for(asyncio.shield(asyncio.to_thread(repo.insert, row)), timeout≈1.5 s)`
  → on timeout: WARNING ("durable stamp late") and the tool proceeds — the detached
  inner task still lands a best-effort late tombstone; on exception: WARNING, bash
  tool unaffected. Attributes are captured eagerly before any cancellable await (D4
  rationale, `bash.py:697-708`), so a mid-write cancellation loses nothing the RAM
  registry doesn't already know.
- **Secondary:** env stamp `ENSEMBLE_BASH_TRACKING_ID=<tracking_id>` (uuid4 hex,
  minted pre-spawn because the env must be stamped before spawn). Injection point:
  after secret/KMS resolution of `assembled_env` (`bash.py:771-782`), just before the
  spawn calls (`:784-792` exec, `:794-802` shell):
  `assembled_env = (assembled_env or os.environ.copy()); assembled_env[VAR] = tid`.
  When no overlay was passed this materializes the inherit-env copy — byte-identical
  semantics plus one var. Verification reuses the proc_tools Layer-3 backends
  (`proc_tools.py:288-365` Linux exact-entry; `:368-503` macOS `ps eww`, fail-open).
- **Restart-lane exclusion is structural** (no row: only the bash seam registers —
  `upgrade_journal.py:2913-2915`; no stamp: allowlist rebuild
  `upgrade_journal.py:2157-2188` strips it; nohup fallback `lib.sh:5179` inherits the
  scrubbed env; next daemon env from the launcher `.env` loader
  `launcher.sh:1645-1649`). Pinned in P1-T6.

### D2 — Substrate: new table, NOT `resource_pid_map`
As argued in `plan-overview.md` §Substrate decision: `resource_pid_map` is an
exit-history table fed by the async flusher (`pid_registry.py:1-54`;
write site `flusher.py:811` — `:462` is the 7-day sweep CALL, not a write, MINOR-2),
its partial UNIQUE is exit-keyed
(`resource_monitor.py:258-276`), and its retention is monitor-owned
(`flusher.py:171-177`). The reaper needs a spawn-synchronous live registry whose rows
OUTLIVE the direct child. Model registered before manager `create_all`
(`daemon/manager.py:885-907` pattern — add the import in the block ending `:909`);
brand-new table ⇒ **no `_ensure_postgres_columns` mirror** (`api.py:975-976`);
SQLite via one `.sql` migration (naming: `daemon/migrations/versions/20261031_HHMMSS_create_bash_spawn_registry.sql`).

### D3 — Row schema (bash_spawn_registry)
`id` PK autoincrement; `pid` int; `pgid` int; `start_time` int nullable (Linux jiffies,
captured at `bash.py:830`; None on macOS per `:702-708`) — LOAD-BEARING: drives the
D9 hard start-time gate, not mere corroboration; `tracking_id` str unique;
`instance_id` str; `command_sanitized` str (reuse the D14 sanitized value computed at
`bash.py:844-846` — executable + argv-shape, NEVER the env overlay; the durable
write-site is placed AFTER this block so the value needs no hoist); `boot_id` str
(uuid4 hex minted once per daemon process, module constant in the reaper service);
`daemon_pid` int (os.getpid() at write time — concurrent-daemon guard, D8);
`created_at` TEXT aware-UTC ISO, `+00:00` suffix (`now_utc_iso()`,
`daemon/services/timestamps.py:63-72` — the sanctioned TEXT-timestamp producer;
m1 label fix: aware, not naive); `reaped_at` TEXT nullable; `reaped_by` str nullable
(cleanup point id + sweeping daemon: `terminal-tier1|terminal-tier2|terminate|
shutdown|boot-reconcile|timeout-survivor|gc` suffixed `@<sweeping-daemon-pid>` —
adjudication 1: when a sweep reaps a dead sibling's rows, the row records WHO swept
it while the WARNING/INFO log line carries BOTH the writer daemon (`row.daemon_pid`)
and the sweeping daemon pid — no extra schema); `reap_reason` str nullable (diagnostic
when a kill was refused or the group was already dead). Indexes (m6 decision):
PARTIAL over the open subset — `(instance_id) WHERE reaped_at IS NULL` and
`(boot_id) WHERE reaped_at IS NULL` (dual-dialect `sqlite_where`/`postgresql_where`,
precedent `resource_monitor.py:258-276`) + plain `(pgid)` for kill-site dedup.
Justification: open rows are the small hot subset and post-F2 every lane scans open
rows, so the index is shaped to the open subset — a `(boot_id, reaped_at)` composite
cannot serve the `reaped_at IS NULL AND boot_id != X` reconcile predicate anyway.
State machine (column pair `reaped_at` NULL/not): open → reaped. Rows are NEVER
deleted by the reaper (audit trail); open rows found dead are closed with
`reap_reason='gc'` (idempotent). **Writer-unset ordering (adjudication 2):** bash
calls landing BEFORE the lifespan wiring injects the durable writer produce NO row
(stamped but unrecoverable-by-reconcile — the window is boot-instant to wiring, and
pre-wiring callers are direct-coroutine/test lanes); defensively, any row that
somehow lands with `boot_id` NULL/empty (a raced writer) is treated as previous-boot
by the reconcile — pinned in the reconcile candidate-selection tests.

### D4 — Path A semantics: tombstone-until-cleanup-point
Success arm (`bash.py:926-957`) keeps its exact current behavior (pid_registry
exit-mark `:933-939`, RAM unregister `:948-951` — N-1 fix intact). The durable row
stays OPEN through completion. Reach restoration: `BashProcessRegistry.cleanup_instance`
(`bash.py:460-475`) and `cleanup_all` (`:477-501`) are EXTENDED to consult open durable
rows for the instance (dedup against RAM pgids) and run the verify-and-kill against
`row.pgid`. All downstream callers get the reach without call-site changes:
terminal tiers (`job_feedback_observer.py:3328-3356`, `:3362-3379`), terminate
(`instance_lifecycle.py:2770-2794`), chart belt-and-braces
(`chart_tools.py:663-679`), shutdown (`manager.py:15229-15244`).
Kill-on-completion was REJECTED: it would break deliberate intra-session
backgrounding and the no-hang pins (`tests/test_tools.py:122-173`) would need
behavior rewrites — violates constraint 6.

### D5 — Timeout arm: no inline change
The timeout arm (`bash.py:883-901`) keeps `_kill_process` + unregister exactly as-is
(constraint 3). A setsid escapee that detaches mid-grace (Path D) is reaped at the
NEXT cleanup point via the Phase-4 stamp sweep — explicitly NOT by adding a survivor
scan inside the tool call (no new kill site in the hot path; event-driven per
constraint 4).

### D6 — Boot reconcile: one-shot, awaited, non-fatal
`reconcile_stale_bash_spawns()` in the new reaper service, awaited in the lifespan
next to the `recover_stale_job_locks` block (`daemon/api.py:924-944` — copy that
shape: try/except → WARNING on failure, boot continues). Candidates: open rows with
`boot_id != CURRENT_BOOT_ID` that ALSO pass the D8 daemon_pid live-sibling guard
(D9 start-time gated — applies in this lane like every other). Predicate
and guards per `plan-overview.md` §Verification predicate. Kill ladder: TERM →
`ENSEMBLE_BASH_REAPER_TERM_GRACE_SECONDS` (default 5.0, clamp [0.5, 30.0] — mirror
`boot_subprocess_sweep.py` knob conventions) → KILL.

### D7 — Kill-switches (rollback story, §6 below)
- `ENSEMBLE_BASH_ORPHAN_REAPER` — master. **Default ON in ALL lanes (F6 leader
  ruling).** Explicit `0/false/no/off` (case-insensitive) = OFF everywhere; explicit
  anything else = ON everywhere; ABSENT = ON (no lane test, no `is_prod_lane()`
  branch). Rationale: the copied precedent's dev-OFF default
  (`boot_subprocess_sweep.py:63-98`) exists because THAT sweep's candidate set
  over-broadens in dev login-session cgroups — a rationale that does not transfer:
  this reaper is row-scoped with a positive structural discriminator and its
  candidate set is exactly the open rows this daemon wrote. The 2026-10-31 incident
  (~93 CPU-hrs) happened IN THE DEV LANE — a dev-OFF default would ship the fix
  inert where it actually fires. OFF = COMPLETE no-op: no stamps, no row writes,
  no sweeps, AND ZERO DB QUERIES at every cleanup point (m3 — disabled sweeps never
  consult the repository), one INFO line at boot.
- `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP` — leg knob: set `0` disables ONLY the boot
  reconcile even when master is ON.
- Phases are independently revertible by landing order (each phase one PR); with
  master OFF the shipped code is inert, so any phase can ride a release disabled.

### D8 — Boot id + concurrent-daemon guard (EVERY lane — F2)
`CURRENT_BOOT_ID = uuid4().hex` minted at import of the reaper service module
(one per daemon process). Rows carry `daemon_pid`. The live-sibling guard applies to
EVERY lane — terminal Tier-1/Tier-2, terminate step 2.56, the shutdown sweep, the
Phase-4 enumeration candidate selection, and the boot reconcile — NOT just boot:
skip a row when a live process OTHER THAN this daemon holds `row.daemon_pid` AND
(D9 gate) its process-start-time ≤ `row.created_at` (it IS a concurrent sibling
daemon — its bash children are LIVE, not leaked); a holder that started after the
row is a recycled pid → writer presumed dead → proceed. **Self-exemption (MAJOR-1):**
this daemon's OWN rows — `daemon_pid == os.getpid()`, i.e. every current-boot row —
ALWAYS proceed through every lane; without the self-exemption the guard would skip
all same-boot rows and neuter Path A closure in the terminal/terminate/shutdown
lanes. Real exposure closed: a dev daemon on :8094 sharing `ensemble_dev` with the
main daemon can no longer have its live backgrounded bash processes reaped by the
other daemon's shutdown sweep or Phase-4 enumeration. Single-daemon-per-DB remains
the deployment contract; the guard is defense-in-depth for dev setups (risk 2).

### D9 — Hard start-time gate (F3 — what makes pgid-only two-signal)
`process_start_wallclock(pid) -> datetime | None` helper: Linux via
`/proc/<pid>/stat` field-22 jiffies + `/proc/uptime` (wall conversion, no schema
change); macOS via `ps -o lstart= -p <pid>` (absolute start) or `etime`-derived
start on the Phase-4 scan (which already carries etime). Applied as a HARD predicate
in three places — failing it means NO kill (fail-closed direction):
1. **Group-member liveness:** ≥1 member with `pgid == row.pgid` must satisfy
   `member_start ≤ row.created_at + tolerance` (default 10 s,
   `ENSEMBLE_BASH_REAPER_START_TOLERANCE_SECONDS` — covers shell fork/exec latency:
   the direct child and immediate grandchildren predate the row write; a recycled
   group's members CANNOT). If every member is younger than the row + tolerance:
   refuse. Note the direct-child-alive case auto-satisfies (spawn precedes row
   write), and killpg then still reaches late-forked young members via the group
   signal. For the direct-child check itself, `os.kill(row.pid, 0)` +
   (Linux) jiffies == `row.start_time` is the exact PID-recycle defense (pid_registry
   lookup pattern).
2. **daemon_pid guard (D8):** holder start ≤ `row.created_at` ⇒ live sibling (skip);
   started after the row ⇒ recycled pid ⇒ writer dead ⇒ proceed.
3. **pgid-only attribution (F1 unified rule, identical wording at all three sites —
   overview §Verification, overview Risk 3, this plan's residual 3):** when member
   env is unreadable (macOS ordinary CLI children), pgid-only attribution IS
   permitted — SAME-BOOT OR PREVIOUS-BOOT, the spawn-synchronous row IS the
   attribution — but ONLY in combination with gate 1, which makes it two-signal and
   defeats pgid recycling. On Linux, env exact-match remains the primary signal;
   readable-but-mismatched env vetoes regardless of the start-time gate.

---

## 1. Files to Touch (complete list, file:line anchored)

### New modules
| File | Purpose |
|---|---|
| `daemon/repositories/bash_spawn/__init__.py` | lane package (custom_agent_configs precedent, `daemon/repositories/custom_agent_configs/`) |
| `daemon/repositories/bash_spawn/models.py` | `BashSpawnRecord` SQLModel + indexes (D3) |
| `daemon/repositories/bash_spawn/repository.py` | sync repo, `engine.begin()` blocks (flusher pattern `daemon/services/resource_monitor/flusher.py:372`): `insert_open_row`, `list_open_by_instance`, `list_open_previous_boot(current_boot_id, my_pid)`, `mark_reaped(id, reaped_by, reason)` |
| `daemon/services/bash_spawn_reaper.py` | `CURRENT_BOOT_ID`; kill-switch parse (`kill_switch_from_env`, mirroring `boot_subprocess_sweep.py:63-98` semantics); `verify_and_kill(row)` predicate+ladder; `sweep_instance(instance_id)`; `sweep_open_rows_for_shutdown()`; `reconcile_stale_bash_spawns()`; `_read_member_env(pid)` platform dispatch (proc_tools backends) |
| `daemon/migrations/versions/20261031_HHMMSS_create_bash_spawn_registry.sql` | SQLite DDL (PG = create_all) |

### Modified files
| File | Site | Change |
|---|---|---|
| `daemon/tools/bash.py` | `:697-708` neighborhood | mint `tracking_id` pre-spawn alongside existing eager-capture locals |
| `daemon/tools/bash.py` | `:771-782` | inject stamp after secret/KMS resolution (D1) |
| `daemon/tools/bash.py` | `:844-846` | no change to the sanitize block itself — the durable write-site (row below) reuses its `sanitized_command` output (m2: sanitize sits AFTER `register()` at `:832`, so the write must be a separate call-site, not inside `register`) |
| `daemon/tools/bash.py` | ≈`:876` (after the pid_registry stamp block, before the timeout try) | F4 durable-write call-site: bounded `wait_for(shield(to_thread(insert)))` — OUTSIDE the registry lock, after the RAM append; register() itself is UNTOUCHED |
| `daemon/tools/bash.py` | `:460-475` (`cleanup_instance`) / `:477-501` (`cleanup_all`) | Phase 2: consult open durable rows, verify-and-kill, mark reaped |
| `daemon/tools/bash.py` | `:480-486` | update known-limitations docstring (setsid + crash gaps closed by phases 3-4; Path E residual stays) |
| `daemon/manager.py` | `:885-909` | import model module before `SQLModel.metadata.create_all(self._engine)` |
| `daemon/manager.py` | after `:15244` (before `steps = [` at `:15246`) | Phase-3 shutdown block: reaper `sweep_open_rows_for_shutdown()` — best-effort try/except, mirrors the Phase-2 bash block |
| `daemon/api.py` | after `:944` (lifespan, post manager.initialize at `:753`) | Phase 3: `create_all` not needed here (manager covers PG) but reaper service construction + wiring onto `get_bash_process_registry()` + awaited `reconcile_stale_bash_spawns()` in the `recover_stale_job_locks` shape; service-construction precedent = the lifespan service-loader block at `api.py:1645-1649` (JobLockSweepService construction `:1652-1676`) (m4 anchor) |
| `daemon/services/job_feedback_observer.py` | `:3301-3309` | comment-only: known-limitations note updated per phase |
| `daemon/services/instance_lifecycle.py` | `:2784-2786` | comment-only: leak note updated (rows now durable) |
| `daemon/tools/chart_tools.py` | `:663-679` | no code change — inherits reach via extended `cleanup_instance`; verify in P2 test |

Wiring pattern for the durable writer: lifespan constructs the repository from
`manager.engine` and injects it into the registry singleton
(`set_durable_writer(repo)` on `BashProcessRegistry`), following the
`manager._watcher_repo = watcher_repo` storage pattern (`api.py:962`). Tools never
import the repo directly (no import cycle; `bash.py` header note `:20-22`). When the
writer is unset (unit tests, writer disabled, or a bash call landing before the
lifespan wiring — adjudication 2), the bash tool behaves exactly as today and NO
durable row is written (the writer-unset ordering contract is pinned in D3 and in
the reconcile candidate-selection tests).

### Test files
| File | Kind |
|---|---|
| `tests/repositories/test_bash_spawn_repository.py` | NEW — repo CRUD, index/dialect, retention-mark |
| `tests/unit/services/test_bash_spawn_reaper.py` | NEW — predicate, guards, ladder, switch parse, env backends (monkeypatched platform) |
| `tests/unit/services/test_bash_spawn_reaper_boot.py` | NEW — one-shot reconcile legs |
| `tests/tools/test_bash_spawn_stamp.py` | NEW — stamp injection contract at the env-assembly seam (`tests/tools/test_bash_secret_env.py` family shape). RELOCATED to `tests/tools/` (reviewer-adjudicated): family consistency with the other bash-tool packs, AND `tests/tools/conftest.py` carries the autouse `_reap_spawned_subprocesses` safety net — a test verifying the orphan-leak fix must not itself forfeit the orphan-reap safety net |
| `tests/tools/test_auto_kill_integration.py` | EDIT — Path A/B/C real-process legs; m8 supersession in Phase 4 |
| `tests/unit/tools/test_agent_restart.py` | EDIT `:188-193` — allowlist purity extension |
| `tests/test_manager_shutdown.py` | EDIT `:287-346` — Phase-3 durable sweep legs |
| `tests/job_queue/test_job_feedback_observer.py` | EDIT `:2492-2640` — Tier-1/Tier-2 durable-reach mirror legs |
| `tests/test_resource_pid_registry.py` | EDIT — ADD durable-lifecycle sibling pins (`:275-312` RAM pin UNCHANGED) |
| `tests/test_bash_reaper_switches.py` (or fold into reaper tests) | NEW — switch matrix |

---

## 2. Phases, Tasks, Gates

### Phase 1 — Durable substrate + write-side discriminators (no kill behavior)
| # | Task | Depends | Acceptance |
|---|------|---------|------------|
| 1.1 | Create `daemon/repositories/bash_spawn/` (models D3 + repository), manager import-before-create_all (`manager.py:885-909`), SQLite migration | none | repo unit pack green on SQLite+PG fixture; table exists after manager init on both dialects |
| 1.2 | Reaper service module: `CURRENT_BOOT_ID`, switch parse (explicit-override semantics of `boot_subprocess_sweep.py:63-98`, lane default FLIPPED to ON — F6), `verify_and_kill` skeleton (guards + D9 start-time gate only, kill deferred to P2), env read-backends ported from `proc_tools.py:288-503` | 1.1 | unit pack: switch matrix 3×3 (absent ⇒ ON in ALL lanes), platform dispatch (linux/darwin/else fail-open), D9 gate unit pins |
| 1.3 | Writer injection: lifespan wiring (`api.py` lifespan after `:944`; construction precedent `api.py:1645-1649`), `set_durable_writer` on the registry; durable write implemented as the F4 call-site in the bash tool body (≈`bash.py:876`, after the sanitize block, outside the registry lock): `asyncio.wait_for(asyncio.shield(asyncio.to_thread(insert)), timeout≈1.5s)` → WARNING "durable stamp late" on timeout (best-effort late tombstone via the detached inner task), WARNING on exception | 1.1 | successful bash call ⇒ exactly one open row (pid/pgid/boot_id/tracking_id/instance_id/daemon_pid populated); DB-fault injection ⇒ WARNING, tool returns normally; **stalled-engine leg (S6)**: `engine.begin()` blocks past the bound (not raising) ⇒ tool returns within the bound, no hang, WARNING logged, late row may land — either outcome tool-safe |
| 1.4 | Env stamp injection at `bash.py:771-782` (gated: instance_id present AND master switch ON — ON is the default in ALL lanes per F6) | 1.3 | ON-lane byte-contract assertion (m6): stamp present in the spawned-child env when ON (assert in dev lane too — the default); env=None lane materializes inherit-copy +1 var; explicit OFF ⇒ byte-identical env |
| 1.5 | Success/timeout/cancel arms verified UNTOUCHED: success leaves row OPEN; timeout/cancel arms do not write/close rows (rows close only via reapers/GC) | 1.3 | new pins + existing packs `tests/tools/test_bash.py`, `tests/tools/test_bash_cancel.py`, `tests/test_resource_pid_registry.py:275-312` green |
| 1.6 | Restart-lane purity pins: extend `tests/unit/tools/test_agent_restart.py:188-193` — assert `ENSEMBLE_BASH_TRACKING_ID ∉ EXECUTOR_ENV_ALLOWLIST` and not matched by `EXECUTOR_ENV_PREFIXES` | 1.4 | pin red if anyone adds the stamp to the allowlist |

**Phase gate:** all existing bash/registry/observer/shutdown packs green with zero
edits (constraint 3); new write-side pack green; no kill code reachable.

### Phase 2 — Cleanup-point reach, same boot (closes Path A)
| # | Task | Depends | Acceptance |
|---|------|---------|------------|
| 2.1 | `verify_and_kill(row)`: predicate per overview §Verification (liveness via `os.kill(pid,0)` + Linux jiffies==`row.start_time` for the direct child, OR member-scan pgid match + D9 HARD start-time gate on ≥1 member; attribution Linux-env exact / darwin positive-only / **pgid-only fallback permitted in ANY boot scope but two-signal via the D9 gate — F1 unified rule**; every-lane daemon_pid live-sibling guard (D8); hard guards `pgid>1`, not `os.getpgid(os.getpid())`, not self, no kernel threads/zombies); TERM→grace→KILL ladder; ESRCH swallowed (`tests/integration/test_service_tool_kill_site_exemption.py:176-281` shape) | 1.2 | guard-rail unit pins: poisoned rows (pgid=1, daemon pgid, self pid) refuse with `reap_reason`; too-young-member rows refuse (D9 gate); sibling-alive rows skip (D8); ladder order asserted via record-and-forward os.killpg census (`tests/integration/test_service_reconciliation_e2e.py:736-805` shape) |
| 2.2 | Extend `BashProcessRegistry.cleanup_instance`/`cleanup_all` (`bash.py:460-501`) to consult open durable rows (dedup vs RAM pgids), verify-and-kill, `mark_reaped(reaped_by='terminal-tier*'|'shutdown'|'gc'@<pid>)`; GC: open rows verified dead → closed `reason='gc'` | 2.1 | real-process Path A leg: bash spawns same-group burner, tool returns (no-hang pins still green), then `cleanup_instance` ⇒ burner dead + row reaped; RAM zero-growth pin still green; sibling-alive leg: row with live foreign `daemon_pid` (start ≤ row) skipped by this lane |
| 2.3 | Terminal tiers inherit reach (observer `:3328-3379`, terminate `:2770-2794`, chart `:663-679`): call sites unchanged; add mirror legs to `tests/job_queue/test_job_feedback_observer.py:2492-2640` | 2.2 | Tier-1/Tier-2/terminate/chart legs green with real rows; sibling-alive skip asserted at the Tier-1 lane too; existing mocked-registry legs untouched-green |
| 2.4 | Shutdown Phase-3 block in `manager.shutdown` after `manager.py:15244`: `sweep_open_rows_for_shutdown()` — all open rows **whose writer daemon is dead or is us** (F2/D8+D9: live-sibling rows are skipped here exactly as in every other lane; **self rows always proceed** — `daemon_pid == os.getpid()` is exempt, MAJOR-1), best-effort try/except, count-logging mirroring `:15235-15243` | 2.2 | shutdown legs in `tests/test_manager_shutdown.py:287-346` extended: durable sweep awaited, failure-isolated, logged; **sibling-alive sweep legs**: live-foreign-`daemon_pid` rows (start-time gated) skipped; dead-writer rows swept; **self-owned current-boot rows swept** (self-exemption leg); executor-shaped child NOT swept (3.4 pin, written here, green already) |
| 2.5 | Update known-limitation comments (`bash.py:480-486`, observer `:3301-3309`, lifecycle `:2784-2786` block, manager `:15207-15213`) | 2.2 | doc-only diff; doc-pin needles updated together with text (per audit-method §7.3 pin-maintenance) |
| 2.6 | Kill-switch enforcement: sweeps run only when master ON; OFF ⇒ complete no-op incl. no row writes from P1 seam AND zero DB queries at every cleanup point (m3 — disabled sweeps never consult the repository) | 1.2 | switch-matrix pins: OFF ⇒ sweeps return 0, no signals, **zero repo calls asserted** (spy/mock on the repo), fenced `os.kill` hard-raise harness (`tests/test_opt_o8_stale_subprocess_sweep.py:58-77` shape) |

**Phase gate:** Path A real-process leg green; no-regression matrix legs N1-N9 green;
m8 (`tests/tools/test_auto_kill_integration.py:1675-1749`) STILL green —
setsid escapees are not yet reached, proving no accidental reach.

### Phase 3 — One-shot boot reconcile (closes Path F) + switch defaults
| # | Task | Depends | Acceptance |
|---|------|---------|------------|
| 3.1 | `reconcile_stale_bash_spawns()`: list open previous-boot rows (`boot_id != CURRENT_BOOT_ID` + D8 daemon_pid live-sibling guard, D9 start-time gated; writer-unset/NULL-boot_id rows treated as previous-boot — adjudication 2), verify-and-kill, mark reaped (`reaped_by='boot-reconcile@<pid>'`); close surviving-but-dead rows (`gc`) | 2.1 | unit: candidate selection (foreign boot only; sibling-daemon rows skipped via start-time gate; current-boot rows never listed; NULL-boot_id rows ARE listed) |
| 3.2 | Wire in lifespan after the stale-lock block (`api.py:924-944` shape): awaited, non-fatal, WARNING on failure; switch `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP` leg knob | 3.1 | boot-reconcile leg pack: kills previous-boot row process (real process, simulated boot_id); DB-fault ⇒ boot proceeds; OFF ⇒ no-op |
| 3.3 | Switch default wiring: master defaults ON in ALL lanes when absent (F6 — no `is_prod_lane()` branch, unlike the copied precedent's `boot_subprocess_sweep.py:140-180`); explicit values override in both directions; document the ruling + rationale in the module header KNOBS section | 1.2 | matrix pins: absent-env ⇒ ON (assert in dev lane too); `0` ⇒ OFF in every lane; truthy ⇒ ON in every lane |
| 3.4 | Restart-supervisor-not-reaped leg: use the spawn_executor harness (`tests/mocks/upgrade_tools_live_safety_mock.py:791-885` — real `spawn_executor` with fixture payload): child owns its session, carries NO stamp, has NO row ⇒ survives `cleanup_instance`, shutdown Phase-3 sweep, boot reconcile; then test-own teardown kills it | 2.4 + 3.1 | 3/3 sweeps leave executor alive; pin named `test_restart_executor_child_survives_all_reaper_sweeps` |
| 3.5 | Daemon-never-targeted integration: poisoned rows (row.pgid == daemon group; row.pgid == 1; row.pid == os.getpid()) through all three kill legs | 2.1 | zero signals delivered; refusals logged with reason; rows closed `reason='refused:<guard>'` |

**Phase gate:** Path F leg green; executor-survival + daemon-guard legs green;
activation doc note added (restart-class, constraint 5).

### Phase 4 — Enumerated stamp sweep (closes Paths B/C + timeout-race survivors)
| # | Task | Depends | Acceptance |
|---|------|---------|------------|
| 4.1 | `_enumerate_stamped_members(open rows)` — NEW code, no repo precedent (flagged): fresh `ps -axo pid,ppid,pgid,etime,command` scan (POSIX lanes only); candidate pre-filter BEFORE any env read (m6): pgid/daemon_pid (D8) + D9 start-time-from-etime gates applied first so the macOS per-pid `ps eww` fan-out is bounded to the few surviving candidates (batch reads where a backend allows); per-candidate attribution: Linux `/proc/<pid>/environ` exact-entry match on `ENSEMBLE_BASH_TRACKING_ID` ∈ open-row tracking ids; macOS `ps eww` positive-only (unreadable ⇒ skip+WARNING — never kill-unverified); excludes self/daemon/kernel threads; pgid>1 guard | 1.2 | unit pack with monkeypatched scanners: exact-match kill selected; mismatch (readable, not ours) ⇒ refused; unreadable darwin ⇒ skipped; sibling-alive candidates skipped pre-env-read (D8, F2); start-time pre-filter proven to shrink the env-read set; performance bounded (single scan per sweep) |
| 4.2 | Hook the enumeration into the existing sweep methods (cleanup_instance extension, shutdown Phase-3 block, boot reconcile) — same event sites, no new kill sites; every lane applies the D8 sibling guard to enumeration candidates (F2) | 4.1 | Path B leg (fork+`os.setsid()`+execlp burner): reaped at terminal sweep; Path C leg (`set -m; burner &` — own group): reaped at shutdown sweep; sibling-owned stamped processes NOT enumerated by a foreign daemon's sweep |
| 4.3 | Timeout-arm survivor closure: Path D escapee (setsid during timeout grace) reached at next cleanup point — test proves reach WITHOUT touching `bash.py:883-901` | 4.2 | timeout pins (`tests/test_tools.py:175-207`) untouched-green; escapee killed by subsequent terminal sweep |
| 4.4 | **m8 supersession:** replace `TestSetsidOrphanSurvivesCleanup` (`test_auto_kill_integration.py:1675-1749`) with `TestSetsidOrphanReapedByStampSweep` (POSIX; darwin leg asserts fail-open skip+WARNING per 4.1); docstring records the lineage (original characterization `:1688-1690`) | 4.2 | new test green; old "survives" assertion gone; the invariant now pinned is "setsid escapees ARE reaped where attribution is possible" |
| 4.5 | Enumeration behind its own switch? NO — rides the master switch (per-phase disable = phase landing order); document in rollback story | 4.1 | rollback section consistency check |

**Phase gate:** Path B/C/D legs green on Linux lane; darwin fail-open legs green;
m8 superseded explicitly; full no-regression matrix still green.

### Phase 5 — Matrix completion, platform legs, docs, activation
| # | Task | Depends | Acceptance |
|---|------|---------|------------|
| 5.1 | Platform legs finalized: Linux /proc verification (synthetic procfs fixtures — `test_opt_o8_stale_subprocess_sweep.py:58-77` fencing); macOS fail-open branches asserted via monkeypatched platform dispatch (`proc_tools.py:275-285` test family shape) | 4.x | both platform lanes green in CI matrix where available |
| 5.2 | Docs: module header KNOBS sections (switches, grace knob, lane defaults); known-limitation comments final state; planning dir decisions.md addendum | 4.x | doc-pins (negative/positive needles separated — `test_mcps_gateway_docs.py:13-16` discipline) green |
| 5.3 | Required test matrix complete (§3 below) — census: collected/passed/skipped triple recorded in the PR body | all | matrix table fully checked off |
| 5.4 | Activation note: restart-class (rebuild+restart; PG `create_all` lands the table; rows begin flowing at first bash spawn post-restart; boot reconcile sweeps pre-restart rows on the SECOND restart with rows present). Record in project history + `plan-overview.md` status flip | 5.3 | activation line written; success criteria table re-verified |

---

## 3. Required Test Matrix

### Leak-class legs (new)
| # | Leg | Escape path | Shape | Phase |
|---|-----|-------------|-------|-------|
| M1 | Burner-orphan reaped, same-group, tool already returned | A | real process: bash spawns `python3 -c` fork burner in group → returns exit 0 → terminal Tier-1 sweep → burner dead; row reaped | 2 |
| M2 | Burner-orphan reaped, setsid escapee | B | fork+`os.setsid()`+execlp burner (m8 fixture shape) → terminal sweep with enumeration → dead (Linux lane); darwin: skip+WARNING | 4 |
| M3 | Burner-orphan reaped, job-control group | C | `set -m; burner &` → shutdown sweep → dead | 4 |
| M4 | Timeout-race escapee reached later | D | burner setsid during timeout grace → timeout pins untouched → next terminal sweep kills | 4 |
| M5 | Restart-stranded burner reaped at boot | F | real process + row with simulated previous `boot_id` → one-shot reconcile → dead; current-boot rows untouched | 3 |
| M6 | Restart-supervisor pattern NOT reaped | — | spawn_executor harness child (own session, unstamped, unregistered) survives cleanup_instance + shutdown + boot sweeps (3/3) | 3 (pin written in 2.4) |
| M7 | Daemon PID never targeted | — | poisoned rows (daemon pgid / pgid=1 / self pid / kernel-thread member) → zero signals, refusal reasons logged | 2-3 |
| M8 | Unstamped unregistered TTY-style process untouched | — | plain `Popen(sleep)` (no stamp, no row) survives every sweep | 4 (enumeration only ever matches stamp) |
| M9 | Sibling-daemon rows skipped in EVERY lane | — (F2) | row with live foreign `daemon_pid` (holder start ≤ row — D9-gated) skipped by terminal sweep, terminate, shutdown sweep, Phase-4 enumeration, boot reconcile; same row with a recycled holder (started after the row) IS swept | 2-4 |

### No-regression legs (existing packs; zero re-baselines — the ONLY sanctioned supersession is N9/m8 in Phase 4)
| # | Leg | Existing pin |
|---|-----|--------------|
| N1 | Timeout killpg reaches same-group grandchildren | `tests/test_tools.py:175-207` |
| N2 | Cancel path kills + unregisters | `tests/tools/test_bash_cancel.py`; `bash.py:990-1030` behavior |
| N3 | Terminal tiers kill tracked pgids (mocked + real nohup) | `tests/tools/test_auto_kill_integration.py:116/:177-475`, `:514-622`; `tests/job_queue/test_job_feedback_observer.py:2492-2640` |
| N4 | Shutdown calls bash cleanup_all (+failure isolation, logging) | `tests/test_manager_shutdown.py:146-346` |
| N5 | No-hang returns with backgrounded children | `tests/test_tools.py:122-133`, `:135-173` |
| N6 | RAM zero-monotonic-growth (BOTH registries) — UNCHANGED, green as-is | `tests/test_resource_pid_registry.py:275-312` |
| N7 | Chart child sweep | `tests/test_chart_tools.py:1423-1532` |
| N8 | Kill-site ESRCH exemption semantics | `tests/integration/test_service_tool_kill_site_exemption.py:176-281` |
| N9 | setsid orphan SURVIVES pgid-only cleanup (superseded in Phase 4 ONLY by M2's stronger pin) | `tests/tools/test_auto_kill_integration.py:1675-1749` |

### Platform legs
| # | Leg | Shape | Phase |
|---|-----|-------|-------|
| P1 | Linux `/proc/<pid>/environ` exact-entry attribution | synthetic procfs fixtures; fenced os.kill | 2-4 |
| P2 | macOS `ps eww` fail-open: unreadable env ⇒ skip+WARNING, never kill-unverified | monkeypatched platform dispatch (`proc_tools.py:275-285` family) | 4 |
| P3 | Start-time gate platform paths: Linux `/proc` jiffies+uptime wall conversion (hard D9 gate; direct-child jiffies==`row.start_time`); macOS `ps -o lstart=`/etime-derived start; None/unparseable ⇒ refuse (fail-closed) | jiffies capture `bash.py:830`; `:702-708` None lane; D9 | 2-4 |

### Activation / switch legs
| # | Leg | Shape | Phase |
|---|-----|-------|-------|
| S1 | Master switch 3×3 matrix (explicit-off everywhere / explicit-on everywhere / absent ⇒ **ON in ALL lanes** — F6 flip, asserted in dev lane too) | explicit-override parsing per `boot_subprocess_sweep.py:63-98`, lane default flipped; `tests/test_opt_o8_stale_subprocess_sweep.py` pin family | 2-3 |
| S2 | Boot-leg knob: master ON + `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP=0` ⇒ boot reconcile no-op | | 3 |
| S3 | Boot reconcile non-fatal: engine fault ⇒ WARNING, lifespan continues | `api.py:924-944` shape | 3 |
| S4 | Writer fail-open: DB down at spawn ⇒ WARNING, bash tool result unchanged | fault-injected repo | 1 |
| S5 | OFF ⇒ complete no-op incl. no stamps, no row writes, AND zero DB queries at every cleanup point (m3 — repo access asserted via spy/mock) | | 1-2 |
| S6 | Stalled-engine at the spawn seam (F4): `engine.begin()` blocks past the ≈1.5s bound (not raising) ⇒ tool returns within the bound, WARNING logged, late tombstone may land; no hang, no lock serialization of concurrent spawns | wrap a sleeping engine fixture | 1 |

---

## 4. No-Regression Guard Summary (constraint 3)

- `bash.py:883-901` (timeout) and `:990-1030` (cancel) bodies are NEVER edited in any
  phase; legs N1-N2 are green in every phase gate.
- Success arm `:926-957` body is NEVER edited; N5-N6 stay green by design (tombstone
  semantics, D4).
- Existing killpg of tracked pgids (`bash.py:460-501` RAM buckets) runs FIRST and
  unchanged; durable-row reach is strictly additive after it, dedup'd by pgid.
- All new kill legs are `sys.platform != "win32"`-gated and behind the master switch;
  OFF = provable no-op (S5, fenced-kill harness).

## 5. Residuals (explicitly open, documented in code comments + docs)

1. **Path E** — `instance_id=None` spawns (`bash.py:819`, `:876-877`) remain untracked.
   Production wiring always injects (`daemon/tools/instance.py:2454-2458`); reachability
   is direct-coroutine callers only.
2. **Path G tail** — cancellation landing between spawn return and the register entry
   (no row yet, fast-exiting shell) leaves an unattributable group. Window identical to
   today's; the cancel arm's own killpg covers the alive-shell case.
3. **macOS env-unreadable escapees** — B/C/D escapees whose env cannot be read survive
   the Phase-4 sweep on macOS (positive-only policy). **F1 unified rule:** Paths A/F
   ARE closed on macOS — via pgid-only attribution that is permitted in ANY boot scope
   (same-boot or previous-boot: the spawn-synchronous row IS the attribution) but is
   two-signal through the D9 hard start-time gate. The residual is ONLY the
   env-unreadable/unscrubbed setsid escapee class (B/C/D) on macOS.
4. **Windows** — reaper legs are POSIX-only; Windows keeps today's taskkill fallback.
5. **Agent-launched launcher lineage (m5-i)** — an agent invoking `./launcher.sh` (or
   `restart.sh` chains) through the bash tool stamps that lineage: the row's
   `daemon_pid` is the OLD daemon, so after the new daemon boots it self-excludes from
   that daemon's rows correctly (D8), but the still-running launcher parent process
   itself carries the old row's stamp and could be Phase-4 enumerated at a terminal
   sweep. Mitigations already in the lane: `restart.sh` refuses without an armed
   pending_op (exit 78), so bash-launched restarts are unsupported-by-design; the
   residual is documented, and the D8+D9 guards keep the NEW daemon's own children
   out of reach. Operator ramp: master switch OFF.
6. **Stamp-scrubbed escapees (m5-ii)** — descendants that scrub their env
   (`env -i`, `sudo` default env policy, containers) lose `ENSEMBLE_BASH_TRACKING_ID`
   and are unreachable by the Phase-4 enumeration (no positive match possible — and
   the design deliberately refuses signature/cmdline matching). pgid-reach still
   covers same-group scrubbed children; cross-group scrubbed escapees are a
   documented residual.

## 6. Rollback Story

- **Instant off-ramp:** set `ENSEMBLE_BASH_ORPHAN_REAPER=0` (explicit wins over the
  default in BOTH directions — `boot_subprocess_sweep.py:63-98` parsing semantics,
  lane default flipped per F6) ⇒ the feature is a complete no-op: no stamps at spawn,
  no row writes, no sweeps, no READS (zero DB queries at every cleanup point, m3),
  boot reconcile skipped with one INFO line. Effective on next restart; nothing to
  unpersist (rows already written are inert data the disabled sweeps never read —
  never even query).
- **Per-phase disable:** phases land as separate PRs in order 1→5; any phase can be
  reverted independently (2-4 add kill reach, 1 adds inert bookkeeping). The boot leg
  can be disabled alone via `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP=0` while
  event-driven sweeps stay on (and vice versa by disabling at the master).
- **Schema rollback:** table is inert when unused; drop is optional housekeeping
  (`DROP TABLE bash_spawn_registry` on PG; SQLite migration downgrade row). No
  `_ensure_postgres_columns` mirror exists to unwind (brand-new-table convention,
  `api.py:975-976`).
- **Worst-case bad kill:** TERM-first ladder gives graceful processes a grace window;
  verification-first + hard guards bound mis-targeting; every kill logs
  pid/pgid/reason/row-id for forensics; **forensics attribution (adjudication 1):**
  `reaped_by` records the lane AND the sweeping daemon (`<lane>@<pid>`), and the log
  line carries BOTH the writer daemon (`row.daemon_pid`) and the sweeping daemon, so
  a cross-daemon sweep of a dead sibling's rows is fully explainable from the row +
  log alone; the switch is the operator's immediate mitigation while the row audit
  (`reaped_by`, `command_sanitized`) explains what happened.

## 7. Open Questions for the Implementer

1. Confirm `ps -axo` output parsing against macOS + Linux column layouts in CI
   (Linux CI + local darwin manual leg) — the enumeration helper (4.1) is the only
   genuinely new parsing surface.
2. Decide whether `reap_reason='refused:*'` rows should also be pruned by GC (plan
   says yes — rows stay audit-only until GC closes them; keep GC conservative).
3. If the prod host turns out to run multiple daemons against one PG in practice
   (dev/staging sharing), elevate the `daemon_pid` guard from skip+log to a hard
   refusal with a dedicated reason token.
