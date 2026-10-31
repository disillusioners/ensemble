# Plan Overview: Bash-Tool Orphan Reaper

Date: 2026-10-31 (planned from worktree base `cbafcc8d9edf7d9be5b7216463903c109784cded`, branch `feature/bash-tool-orphan-reaper`)
Author: plan-creation worker (research by two explorers, spot-checked by planner)
Status: Implementation complete (Phases 1-5); activation = rebuild + restart (restart-class).

---

## Objective

Ensure no process spawned through the bash tool can outlive its sanctioned cleanup
opportunities: every bash-tool process group is durably attributable at spawn, and every
cleanup point (instance terminal transitions, terminate, daemon shutdown, one-shot boot
reconcile) reaps leak survivors — including groups whose direct child already exited
(Path A, the 2026-10-31 incident), setsid/job-control escapees (Paths B/C/D), and
groups stranded across a daemon restart (Path F) — while the sanctioned restart
supervisor lane remains structurally untouchable.

Testable completion sentence: a bash-tool-spawned CPU burner that escapes every
existing cleanup hook is reaped at the next cleanup point on both macOS and Linux,
and no code path in this feature can ever signal a process from the restart lane,
the daemon itself, or an unattributed process.

## Hard Constraints (verbatim restatement — any design choice below is evaluated against these)

1. **Restart lane untouched.** The detached restart supervisor (`spawn_executor`:
   `start_new_session=True` legacy branch / systemd transient unit;
   stop→sleep→launch via `scripts/upgrade/restart.sh`; `scripts/upgrade/lib.sh:5179`
   nohup launcher fallback) is spawned via the `system_restart` tool path — NEVER via
   the bash tool — and MUST survive daemon restarts by design. No session-end cleanup,
   boot sweep, or reaper may ever touch processes from that lane. The design uses the
   positive structural discriminator (durable bash-spawn registry row written at the
   bash spawn seam + env stamp); signature/cmdline matching alone is rejected.
2. **Never target the daemon or innocent processes.** Only signal pgids from verified
   registry rows; never `pgid == daemon's own group`, never `pgid == 1`, never self.
   macOS AND Linux both supported (macOS: no `/proc`, `ps eww` env-readback unreliable
   for ordinary CLI children — documented fail-open; NO psutil; zero new deps).
3. **No regression** to existing killpg cleanup of tracked pgids, the bash timeout path
   (`daemon/tools/bash.py:883-901`), or the CancelledError path (`:990-1030`).
4. **Event-driven cleanup is PRIMARY.** ONE-SHOT boot-time reconcile is the only
   boot-time mechanism (the `recover_stale_job_locks` awaited non-fatal lifespan shape,
   `daemon/api.py:924-944`). NO perpetual polling sweeps.
5. **Activation is restart-class** (daemon rebuild+restart): stamps/registry rows are
   written durable at spawn time (before any detach opportunity); the boot sweep reads
   them on the next boot.
6. **Scope tight:** fix the leak class. No bash-tool redesign, no restart-lane changes,
   no generic process management.

## Chosen Approach — Discriminator Design

### Positive discriminator (how a reaper decides "this is OURS")

**Primary: a new durable table `bash_spawn_registry`** — one row written synchronously
at the bash spawn seam for every bash tool spawn with an `instance_id`. The row carries
`pid, pgid, start_time (Linux jiffies), instance_id, tracking_id, command_sanitized,
boot_id, daemon_pid, created_at, reaped_at, reaped_by`. ONE write-site (F4): the bash
tool body, AFTER the RAM append returns and OUTSIDE the registry `asyncio.Lock`
(never inside `register()` — `bash.py:433` holds the global lock and a wedged PG socket
there would serialize ALL bash spawns), positioned after the command-sanitize block
(`bash.py:844-846`) so the already-computed `sanitized_command` is reused — call-site
≈ `bash.py:876`. Form: `asyncio.wait_for(asyncio.shield(asyncio.to_thread(insert)),
timeout≈1.5 s)` → on timeout a WARNING ("durable stamp late") and the tool proceeds —
`shield` alone protects against cancellation, NOT a wedged socket; the bounded
`wait_for` is what cannot hang the tool, and the detached inner task still lands a
best-effort late tombstone. Attributes are still captured eagerly (D4 rationale,
`bash.py:697-708`): everything needed to attribute and kill the group is captured
BEFORE any subsequent await that could be cancelled.

**Secondary: env stamp `ENSEMBLE_BASH_TRACKING_ID=<tracking_id>`** injected into the
spawn env (`bash.py:771-782` assembly, stamped just before the spawn calls
`:784-802`). Reuses the `proc_tools` Layer-3 pattern (`daemon/tools/proc_tools.py:131`,
`:504-544`) for pre-kill verification: Linux reads `/proc/<pid>/environ` with
NUL-separated exact-entry match (`proc_tools.py:288-365`); macOS uses `ps eww -p <pid>`
with the documented fail-open (`proc_tools.py:368-503`).

**Restart lane structurally excluded (both discriminators fail there, by construction):**
- No registry row: registration happens ONLY in the bash tool; `spawn_executor`
  deliberately skips every registry (`daemon/tools/upgrade_journal.py:2913-2915`).
- No env stamp: the executor env is rebuilt from `EXECUTOR_ENV_ALLOWLIST`
  (`upgrade_journal.py:2157-2188`) — any `ENSEMBLE_BASH_TRACKING_ID` in the daemon's
  environ is stripped before `Popen`/`--setenv`; the nohup launcher fallback
  (`lib.sh:5179`) inherits only that scrubbed env; the next daemon gets its env from
  the launcher's `.env` loader (`launcher.sh:1645-1649`, ADR-014). No code path
  re-injects unknown `ENSEMBLE_*` vars.
  This is pinned by extending the existing allowlist-purity oracle
  (`tests/unit/tools/test_agent_restart.py:188-193`).
- Signature/cmdline matching (`"python -c while True"`-style scans) is explicitly
  REJECTED as fragile — nothing in this design selects kill targets by command text.

### Substrate decision: NEW dedicated table (NOT reuse of `resource_pid_map`)

`resource_pid_map` is REJECTED for reuse, despite carrying the right column shape and
dormant `action/action_by/acted_at` columns (`daemon/models/resource_monitor.py:251-253`):

1. It is NOT a durable live registry — the live map is RAM (`daemon/services/resource_monitor/pid_registry.py:1-54`); the table receives rows only from the asynchronous
   flusher at exit time (write site `flusher.py:811`). The reaper needs a write at SPAWN
   time, synchronous with the bash seam, which the flusher's drain-on-exit design cannot
   provide (`:462` is the 7-day sweep CALL, not a write — anchor corrected MINOR-2).
2. Its partial UNIQUE `(pid) WHERE exited_at IS NULL` (`resource_monitor.py:258-276`)
   is keyed to the exit-history lifecycle; Path A requires rows to OUTLIVE the direct
   child's exit — the row IS the tombstone after the shell is gone.
3. Its retention lane (7-day sweep of exited rows, `flusher.py:171-177`) and its
   enablement are owned by resource_monitor; the reaper's bookkeeping must not depend on
   the monitor being enabled.

The dormant control-seam columns stay reserved for their original P6 owner; co-opting
them would silently change an observability lane's contract. New table follows the
recent brand-new-table conventions: model registered BEFORE manager `create_all`
(`daemon/manager.py:885-907` pattern), SQLite handled by a `.sql` migration, PG by
`create_all` alone — **no `_ensure_postgres_columns` mirror** (`daemon/api.py:975-976`).
Live-row uniqueness is not enforced (a pid may legitimately have rows from multiple
boots); indexes are PARTIAL over the open subset — `(instance_id) WHERE reaped_at IS
NULL` and `(boot_id) WHERE reaped_at IS NULL` (dual-dialect
`sqlite_where`/`postgresql_where`, precedent `resource_monitor.py:258-276`) plus a
plain `(pgid)` for kill-site dedup. Justification (one sentence, m6): open rows are
the small hot subset and post-F2 EVERY lane scans open rows, so the index is shaped
to the open subset rather than to the full table — a `(boot_id, reaped_at)` composite
could not serve the `reaped_at IS NULL AND boot_id != X` reconcile predicate anyway.
Timestamps are TEXT aware-UTC ISO (`+00:00` suffix) via `now_utc_iso()`
(`daemon/services/timestamps.py:63-72`) — the sanctioned TEXT-timestamp producer —
per the `spawned_at` TEXT precedent (`resource_monitor.py:298-302`).

### Core semantic change (Path A) — tombstone, not kill-on-return

Normal completion **keeps its current no-kill behavior** (`bash.py:926-957` success arm
still unregisters the RAM entry — the N-1 fix stays). What changes: the durable row
is NOT closed on completion — it is the tombstone that keeps `(pid, pgid, tracking_id)`
reclaimable after the RAM entry is gone. The next cleanup point with reach
(instance terminal Tier-1/Tier-2 `daemon/services/job_feedback_observer.py:3286-3379`,
terminate step 2.56 `daemon/services/instance_lifecycle.py:2770-2794`, daemon shutdown
`daemon/manager.py:15229-15244` + new Phase-3 block, one-shot boot reconcile) verifies
and killpgs the recorded group, then marks the row `reaped`.

Rationale:
- Kill-on-return would break deliberate intra-session backgrounding (`nohup server &`
  then later calls poke it) — a workflow the current design supports within instance
  lifetime — and would touch the anti-hang contract.
- The no-hang return pins (`tests/test_tools.py:122-133`, `:135-173`) and the temp-file
  capture design (`bash.py:719-727`) stay byte-for-byte untouched (constraint 6).
- The RAM zero-monotonic-growth pin (`tests/test_resource_pid_registry.py:275-312`)
  stays green WITHOUT re-baselining — the RAM lifecycle is unchanged; only the durable
  lifecycle is new (and gets its own pins).

Leak-class result: stragglers are bounded by the next cleanup point instead of
infinite. Path A groups become killpg-reachable again at terminal/terminate/shutdown;
Path F groups via the boot reconcile. **Worst-case reap bound (m6, acknowledged):**
the instance's terminal transition — a long-lived instance that backgrounds a burner
and never terminates can run DAYS between cleanup points; the daemon-shutdown sweep
and the boot reconcile bound the cross-boot tail, and within-boot the bound is
explicitly instance-lifetime (same scoping the current cleanup model already uses).

### setsid-escapee reach (Paths B/C/D-survivors) — phase-gated

Early phases reach ONLY registered pgids (killpg on `row.pgid`). Setsid/job-control
escapees sit outside that group (`bash.py:503-514`, `:611-625` cannot reach them —
documented limitation `bash.py:480-486`). Phase 4 adds an **enumerated-pid env-stamp
sweep** at the cleanup points: a fresh `ps -axo pid,ppid,pgid,etime,command` scan (new
code — no repo precedent exists for full-process-table enumeration; flagged as such)
selects pids whose env carries one of OUR open rows' tracking ids (Linux `/proc` exact;
macOS `ps eww` — kill ONLY on positive match, skip+WARNING on unreadable, never
kill-unverified), then TERM→grace→KILL per pid. Path-by-path closure map:

| Escape path | Closed in | Mechanism |
|---|---|---|
| A normal-completion straggler (same-group) | Phase 2 | durable tombstone + cleanup-point killpg of `row.pgid` |
| F restart-stranded (same-group) | Phase 3 | one-shot boot reconcile of previous-boot rows |
| D timeout-race setsid escapee | Phase 4 | stamp enumeration at the NEXT cleanup point (explicitly NOT inline in the timeout arm — no new kill site inside the tool call; the next terminal/shutdown/boot sweep reaches it, event-driven) |
| B setsid / C `set -m` job-control | Phase 4 | stamp enumeration at cleanup points |
| E `instance_id=None` untracked | explicit residual | registration is gated `instance_id is not None` (`bash.py:819`, `:876-877`); production wiring always injects (`daemon/tools/instance.py:2454-2458`) — documented residual, out of scope |
| G cancel-in-spawn-window | partial (pre-existing window) | cancel arm already killpgs (`bash.py:990-1030`); a row written before cancellation is reaped like any other; a cancel landing BEFORE the register entry leaves no row (same un-narrowed window as today) — documented residual |

### Verification predicate (kill-safety core)

A candidate is killable only when ALL hold (mirrors the two-signal PID-recycle doctrine
of `daemon/services/upgrade_journal_sweep.py:27-32` — pid-existence alone is not
load-bearing):
1. Row is open (`reaped_at IS NULL`) and `row.daemon_pid` fails the live-sibling guard
   in EVERY lane (terminal, terminate, shutdown, Phase-4 selection, boot reconcile —
   F2): skip when a live process OTHER THAN this daemon holds `daemon_pid` AND (F3
   hard gate) its process-start-time ≤ `row.created_at` (i.e. it IS a concurrent
   writing daemon — protects concurrent-daemon dev setups); a holder that started
   after the row is a recycled pid → writer presumed dead → proceed. **Self rows
   always proceed** (`daemon_pid == os.getpid()` — every current-boot row — is exempt
   from the guard in every lane; MAJOR-1). Boot reconcile additionally scopes to
   `row.boot_id != CURRENT_BOOT_ID` (uuid4 minted per daemon process).
2. Group reachability: `os.kill(row.pid, 0)` (direct child — on Linux additionally
   require its `/proc` starttime jiffies to equal `row.start_time`, the captured
   `bash.py:830` value: PID-recycle defense) OR — for Path A leaderless groups — a
   member scan confirms ≥1 live process with `pgid == row.pgid` (fresh `ps` scan or
   `/proc/*/stat` field 5 on Linux) AND (F3 HARD START-TIME GATE) ≥1 such member's
   process-start-time ≤ `row.created_at + tolerance` (default 10 s,
   `ENSEMBLE_BASH_REAPER_START_TOLERANCE_SECONDS`, covers shell fork/exec latency —
   the direct child and immediate grandchildren predate the row write; a recycled
   group's members CANNOT). If every member is younger than the row: refuse (safe
   direction — a too-young group is either recycled or ambiguous; note the
   direct-child-alive case auto-satisfies the gate since spawn precedes the row
   write, so killpg then also reaches late-forked group members via the group
   signal).
3. Attribution: env-stamp exact match on ≥1 member (Linux reliable; macOS
   positive-only) — when env is READABLE and no member matches, the kill is REFUSED
   (fail-closed on readable mismatch); when env is unreadable (macOS ordinary CLI
   children), pgid-only attribution IS permitted — SAME-BOOT OR PREVIOUS-BOOT (F1
   unified rule: the spawn-synchronous row IS the attribution) — but ONLY in
   combination with the F3 hard start-time gate of rule 2, which makes it two-signal
   and defeats pgid recycling. Logged `attribution=pgid-only` (documented fail-open;
   accepted residual risk, bounded by rules 2 and 4).
4. Hard guards: `row.pgid > 1`; `row.pgid != os.getpgid(os.getpid())` (daemon's own
   group); member pids exclude self/daemon; never a kernel thread or zombie.
5. Kill ladder: SIGTERM group → bounded grace → SIGKILL group (reuses the
   `_kill_process` ladder shape, `bash.py:582-631`); ESRCH swallowed (kill-site
   exemption precedent, `tests/integration/test_service_tool_kill_site_exemption.py`);
   row marked `reaped` with `reaped_by` recording the cleanup point.

## Scope

### In Scope
- New durable spawn registry (model + repository + spawn-seam write + env stamp).
- New reaper service (verification predicate, guard rails, kill ladder, boot reconcile).
- Wiring at the four cleanup points (terminal tiers, terminate, shutdown, boot).
- Kill-switch env knobs: master `ENSEMBLE_BASH_ORPHAN_REAPER` (default **ON in ALL
  lanes** — F6 leader ruling, see D7; explicit `0` is the operator off-ramp) +
  boot-leg knob `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP`; the explicit-override
  parsing follows the `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` shape
  (`daemon/services/boot_subprocess_sweep.py:63-98`) with the lane default FLIPPED.
- Test supersession (m8) and new pins per the required matrix; known-limitation
  comment updates (`bash.py:480-486`, `job_feedback_observer.py:3301-3309`,
  `instance_lifecycle.py:2784-2786`, `manager.py:15207-15213/:15229-15231`).

### Out of Scope (non-goals)
- Restart lane, `spawn_executor`, `restart.sh`, `launcher.sh`, `lib.sh` — UNTOUCHED.
- Bash tool redesign (args, env-arg semantics, temp-file capture, timeout clamp).
- Any polling/interval sweep service (constraint 4); no turn-end hook (none exists —
  research §5).
- Path E (`instance_id=None` spawns) and pre-register cancellation window (Path G
  tail) — documented residuals.
- Windows: today's taskkill fallback remains; the reaper's new legs are POSIX-gated
  (`sys.platform != "win32"` guard consistency, `bash.py:729`/`:506` family).
- Generic process management / psutil adoption (zero new deps).

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Durable substrate + write-side discriminators | Rows + stamps exist and are verifiable; zero kill behavior | 6 | independent (touches only bash spawn seam + new modules) | pending |
| 2 | Cleanup-point reach, same-boot (Path A) | Terminal tiers, terminate, shutdown reap open rows via killpg; guard rails live | 6 | tight with Phase 1 (row lifecycle/state machine) | pending |
| 3 | One-shot boot reconcile (Path F) + kill-switch defaults | Previous-boot rows reaped at boot; switches wired default-ON-all-lanes with explicit-off ramp (F6) | 5 | tight with 1-2 (predicate reuse); independent of 4 | pending |
| 4 | Enumerated stamp sweep — setsid/job-control reach (Paths B/C/D) | Escapees outside the registered group reaped at cleanup points; m8 superseded deliberately | 5 | tight with 1-3 (predicate + rows); independent of 3 ordering-wise | pending |
| 5 | Matrix completion, platform legs, docs, activation | Full required matrix green; runbook + rollback story final | 4 | loose with all | pending |

Full task tables: `plan.md` §Phases.

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| Phase 1 | — | tight (row shape + state machine are the contract) | tight (boot reconcile reads rows) | tight (enumeration matches on tracking_id from rows) | loose (tests/docs only) |
| Phase 2 | tight | — | loose (shared predicate module) | tight (same kill sites extended) | loose |
| Phase 3 | tight | loose | — | loose (adds boot as an enumeration site) | loose |
| Phase 4 | tight | tight | loose | — | loose |
| Phase 5 | loose | loose | loose | loose | — |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Reaper kills a wrong process (pgid/pid recycled; attribution wrong) | High | Low | Positive verification chain (§Verification predicate): exact env-entry match where readable (fail-closed on readable mismatch), member-scan pgid confirmation + F3 HARD start-time gate (members must predate the row — a recycled group's members cannot), every-lane daemon_pid live-sibling guard, hard guards (`pgid>1`, not daemon group, not self), TERM-before-KILL, kill-site ESRCH exemption; kill-switch is an instant full off-ramp |
| 2 | Concurrent daemons sharing one PG (dev :8094 + main on `ensemble_dev`) cross-kill each other's bash children | High | Low | Rows carry `daemon_pid`; the live-sibling guard (F3 start-time gated) applies in EVERY lane — terminal, terminate, shutdown, Phase-4 selection, boot reconcile (F2) — not just boot; single-daemon-per-DB remains the deployment contract (documented) |
| 3 | macOS env readback unreliable → escapees survive on macOS (fail-open) | Medium | High (by design) | Accepted + documented residual; positive-only kill on macOS env; the F1-unified pgid-only path (same- or previous-boot, two-signal via the F3 start-time gate) still closes Paths A/F on macOS; residual limited to env-scrubbed/unreadable setsid escapees (B/C/D); explicit leg in the matrix |
| 4 | DB write at spawn seam adds latency / hangs the bash tool on a wedged engine | Medium | Low | F4 single call-site OUTSIDE the registry lock + `asyncio.wait_for(asyncio.shield(to_thread(...)), ≈1.5s)` — bounded against BOTH cancellation and a wedged socket (shield alone is not enough); timeout ⇒ WARNING + best-effort late tombstone; the bash tool can never hang or fail because bookkeeping did; stalled-engine leg S6 pins it; perf criterion in Success Criteria |
| 5 | Row growth unbounded (long-lived instances never hit a cleanup point) | Low | Medium | Lazy GC: every sweep marks verified-dead rows `reaped`; boot reconcile closes ALL previous-boot rows; rows are tiny; per-instance index supports pruning |
| 6 | Regression to existing killpg/timeout/cancel paths (constraint 3) | High | Low | RAM registry lifecycle untouched (success arm still unregisters; zero-growth pin stays green without re-baseline); timeout/cancel arms untouched in Phases 1-3; full no-regression matrix leg per phase gate |
| 7 | Restart lane contacted by accident | Critical | Very Low | Structural exclusion (no row writer outside bash seam; allowlist strips stamp) + pins: allowlist purity (extends `test_agent_restart.py:188-193`), spawn_executor-shaped-child-survives-all-sweeps, static no-spawn discipline (`upgrade_journal.py:2202-2204`) untouched |
| 8 | `ps -axo` enumeration cost / parsing drift at cleanup points; macOS per-pid `ps eww` env fan-out | Low | Medium | Runs only at event-driven cleanup points (never polling); the F3 start-time gate PRE-FILTERS candidates before any env read (m6 — shrinks the `ps eww` fan-out to the few pgid+time-surviving candidates; batch reads where a backend allows); parse failures fail-open-to-skip with WARNING |
| 9 | Boot reconcile delays or crashes startup | High | Very Low | `recover_stale_job_locks` shape: awaited, fully wrapped non-fatal try/except (`api.py:924-944`); OFF = complete no-op — no kills AND zero DB queries at every cleanup point (m3: the sweeps never even consult the repository when disabled), one INFO line at boot |
| 10 | m8 supersession silently weakens the setsid pin | Medium | Low | Supersession is an explicit Phase-4 task: old test replaced by a stronger one (escapee REAPED where verifiable; darwin-unverifiable leg asserts skip+WARNING), docstring records the lineage |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Path A burner reaped at next cleanup point | Real-process test: bash spawns `python3 -c` fork burner in same group; tool returns; trigger terminal sweep; burner pid dead | burner dead ≤5s after sweep; row marked `reaped` |
| 2 | Path F burner reaped at boot reconcile | Real-process test with simulated previous `boot_id`; run one-shot reconcile | killed; current-boot rows untouched |
| 3 | Restart-lane child never reaped | spawn_executor-harness child (own session, unstamped, unregistered) survives cleanup_instance + shutdown Phase-3 + boot reconcile | 3/3 sweeps leave it alive; purity pins green |
| 4 | Daemon never targeted | Guard-rail unit tests + poisoned-row integration (row.pgid = daemon group, pgid=1, self) | zero signals delivered; refusal logged |
| 5 | No regression (constraint 3) | Existing packs: `test_tools.py` timeout/cancel/no-hang, `test_auto_kill_integration.py` (pre-Phase-4 subset), `test_manager_shutdown.py`, `test_resource_pid_registry.py:275-312`, `test_job_feedback_observer.py` mirror tiers | all green, zero re-baselines except the explicitly planned m8 supersession |
| 6 | macOS fail-open honored | darwin-gated / monkeypatched-platform leg: unreadable env → skip + WARNING, never kill-unverified | documented branch asserted both platforms |
| 7 | Kill-switch semantics | Matrix: explicit `0` off everywhere; explicit truthy on everywhere; absent → **ON in ALL lanes** (F6 leader ruling — the 2026-10-31 incident ran in the dev lane; the row-scoped positive discriminator does not inherit the boot sweep's dev-OFF over-broad-candidates rationale); OFF = complete no-op (no kills, no writes, no READS) | 3×3 cases pinned (pattern: `tests/test_opt_o8_stale_subprocess_sweep.py`) |
| 8 | Spawn-seam latency | New bash-call timing assertion in write-side tests | durable write adds <10ms p50 and is BOUNDED (≈1.5 s `wait_for` — a stalled engine cannot hang the tool; slow/late write degrades to WARNING + best-effort late tombstone, never to tool failure) |
| 9 | Event-driven only (constraint 4) | Code inspection + test census: no interval service introduced; boot reconcile called exactly once per lifespan | one call site, awaited, non-fatal |

## Research Insights (shaped this plan)

- Path A is the incident path: success arm `bash.py:926-957` unregisters WITHOUT
  killing; backgrounded same-group grandchildren become unreachable exactly at that
  moment; incident fingerprint (unlinked `bash-stdout-*.tmp` fds) matches
  `bash.py:740-741` + `:1050-1056` (research-bash-mechanics §2, incident note).
- Registry is RAM-only, self-documented (`bash.py:484-486`); restart loss is Path F.
- Restart lane can never carry the discriminators: env allowlist rebuild
  (`upgrade_journal.py:2157-2188`), deliberate non-registration (`:2913-2915`),
  nohup fallback inherits scrubbed env (`lib.sh:5179`), next daemon env from
  the launcher `.env` loader (`launcher.sh:1645-1649`)
  (research-restart-lane §1.3/§1.4/§2; launcher anchor corrected in revision pass).
- `proc_tools` already ships the stamp + verify-before-kill pattern with the exact
  fail-open/fail-closed contract to copy (`proc_tools.py:131/:241-285/:504-544`).
- Boot reconcile precedent: `recover_stale_job_locks` awaited non-fatal one-shot
  (`api.py:924-944`); kill-switch lane-conditional shape
  (`boot_subprocess_sweep.py:63-98`); two-signal liveness doctrine
  (`upgrade_journal_sweep.py:27-32`).
- Brand-new tables need NO `_ensure_postgres_columns` mirror
  (`api.py:975-976`); PG = `create_all` (manager.py:885-907 / `api.py:952` precedents).
- Cleanup hook map (all four sites verified): timeout `bash.py:883-901`; cancel
  `:990-1030`; success `:926-957`; terminal tiers `job_feedback_observer.py:3286-3379`;
  terminate `instance_lifecycle.py:2770-2794`; shutdown `manager.py:15229-15244`;
  boot `__main__.py:389-399` (adjacent prior art, bash-registry-independent).
- m8 characterization (`tests/tools/test_auto_kill_integration.py:1675-1749`) exists to
  force exactly this re-evaluation; zero-growth pin (`tests/test_resource_pid_registry.py:275-312`) stays green because the RAM lifecycle is unchanged.

## Open Questions

1. **PG table vs data-dir file** — resolved: PG table (this document). The data-dir
   JSON option (`lib.sh:13-14` temp+mv precedent) was rejected because the reaper's
   cleanup points (observer tiers, terminate) already run with engine access, PG gives
   cross-boot reads without file-lifecycle code, and the repo's recent new-table
   conventions are fresh. If PG unavailability at bash-spawn time proves noisy in dev
   (risk 4), the fail-open WARNING path already degrades gracefully — revisit only if
   criterion 8 fails.
2. **Chart lane** (`daemon/tools/chart_tools.py:663-679` calls `cleanup_instance` after
   chart-render kill): inherits the extended `cleanup_instance` automatically once the
   durable consult lands inside `BashProcessRegistry`. No separate wiring task; flagged
   so reviewers know the reach extends there implicitly.
3. **Windows**: all new kill legs are `sys.platform != "win32"`-gated; Windows retains
   today's taskkill fallback only. No Windows reaper work in this plan.
