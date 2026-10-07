# Investigation: v0.18.0 Self-Upgrade Failure — Daemon Kills Its Own Executor

**Date:** 2026-10-07
**Author:** DevOps (external operator agent)
**Status:** Root cause identified, fix needed (dev team), daemon recovered on v0.17.2
**Downtime:** ~30 minutes (09:53 → 10:20 UTC)

---

## 1. What Happened

The agent-gated self-upgrade to v0.18.0 was armed and the executor spawned. The promote ran, stopped the daemon via systemctl (polkit worked, unit-managed path worked), and then **the daemon's own shutdown cleanup killed the executor** — leaving the daemon stopped, the promote dead mid-ceremony, and no flip.

The daemon stayed down for ~30 minutes because systemd saw a clean stop (inactive, not failed) and did not restart.

## 2. Root Cause — Self-Referential Kill

The promote executor is spawned **by the daemon** (the agent-gated upgrade path runs it as a tracked child process). When the promote tells the daemon to stop:

```
1. Executor (promote.sh) calls: systemctl stop ensemble-main
2. Daemon receives SIGTERM → begins graceful shutdown
3. Daemon's cleanup logic runs:
   - daemon.manager: "shutdown: killed background processes in 1 instance(s)"
   - daemon.tools.bash: "bash cleanup_all: killed 1 process(es)"
   ← The "1 process" IS the upgrade executor
4. Executor dies → promote stops (daemon stopped, no flip, no rollback)
5. systemd sees clean stop → no restart → daemon stays down
```

### Evidence (from journalctl)

```
Oct 07 09:53:50 launcher.sh[1292283]: daemon.manager - INFO - shutdown: killed background processes in 1 instance(s)
Oct 07 09:53:51 systemd[3288474]: ensemble-upgrade-r-20261007-092750-a6a0.scope: Consumed 26min 3.210s CPU time.
Oct 07 09:53:51 launcher.sh[1292283]: daemon.tools.bash - INFO - bash cleanup_all: killed 1 process(es)
Oct 07 09:53:51 launcher.sh[1292283]: daemon.manager - INFO - shutdown: killed bash processes: 1
Oct 07 09:53:58 launcher.sh[1292153]: shutdown complete — exiting 143 (child's exit code)
Oct 07 09:53:58 systemd[1]: Stopped ensemble-main.service
```

The scope `ensemble-upgrade-r-20260907-092750-a6a0.scope` was the executor's systemd-run scope. It was consumed at 09:53:51 — exactly when the daemon's cleanup ran.

### Prior Occurrence (same bug class)

The journal history records this on v0.16.1 (2026-09-28):
> "executor killed by ensemble-live cgroup teardown 2026-09-28T00:56:53Z after phase 2; phases 3-8 executed 2026-09-28T01:31:26Z from operator SSH session outside the service cgroup"

The v0.16.1 incident used a MANUAL recovery (operator re-ran phases 3-8 from SSH). This time the daemon stayed down until DevOps intervened.

## 3. Why the Daemon Didn't Come Back

- The promote's stop was **clean** (SIGTERM, exit 143, graceful shutdown)
- systemd's `Restart=on-failure` only fires on **failure** exits
- `SuccessExitStatus=143 SIGTERM` (added 2026-09-24) explicitly marks 143 as success
- Therefore: systemd saw a successful stop → inactive → no restart

This is correct behavior for intentional stops — the problem is that the promote died before it could restart the daemon.

## 4. Recovery Procedure Used

```bash
# 1. Verify the txn is pre-flip (safe to clear)
python3 -c "
import json
s = json.load(open('/home/nea/agents-ensemble/releases/state.json'))
# Check: in_flight.flipped must be False
assert s['in_flight']['flipped'] == False
print('safe to clear')
"

# 2. Clear stale state
python3 -c "
import json
p = '/home/nea/agents-ensemble/releases/state.json'
s = json.load(open(p))
s['in_flight'] = None
s['pending_op'] = None
s['pending_actions'] = {}
json.dump(s, open(p, 'w'), indent=2)
"

# 3. Remove stale rollback lock
rm -rf ~/agents-ensemble/releases/rollback.lock.d

# 4. Start the daemon (from current symlink = last-known-good)
sudo systemctl start ensemble-main

# 5. Verify (~60s for boot)
curl -s http://127.0.0.1:9797/livez   # should be 200, last-known-good version
```

## 5. Proposed Fixes (for dev team)

### Fix A — Exempt the executor from shutdown cleanup (minimal change)

The daemon's shutdown cleanup (`daemon.manager` background-process kill + `daemon.tools.bash` cleanup_all) should skip processes that belong to an active upgrade executor:

- Match by process group/session name: `ensemble-upgrade-r-*`
- Or match by the `run_id` from the journal's `pending_op`
- The executor is doing critical infrastructure work — killing it during shutdown defeats the upgrade

```python
# In the shutdown cleanup logic:
UPGRADE_EXECUTOR_PATTERN = "ensemble-upgrade-r-"  # or check pgid against active upgrade scopes
if any(UPGRADE_EXECUTOR_PATTERN in str(cmdline) for cmdline in process_cmdline):
    logger.info(f"skipping upgrade executor pid={pid} (active promote)")
    continue
```

### Fix B — Fully detach the executor (architectural)

Spawn the executor in a way that the daemon CANNOT kill it:
- `systemd-run --scope` already isolates the cgroup, but the daemon still tracks it as a child
- Double-fork + `setsid` + close all fds — removes it from the daemon's process table entirely
- The daemon should fire-and-forget the executor (no PID tracking, no cleanup ownership)

### Fix C — Watchdog for stopped-not-failed (operational safety net)

Even with Fix A/B, a crashed promote could leave the daemon stopped. Add a systemd timer or watchdog:

```ini
# /etc/systemd/system/ensemble-main-watchdog.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

# /etc/systemd/system/ensemble-main-watchdog.service
[Service]
Type=oneshot
ExecCondition=/bin/sh -c '! systemctl is-active --quiet ensemble-main'
ExecStart=/bin/systemctl start ensemble-main
```

This restarts the daemon if it's been down for more than one timer cycle — a safety net for any future "stopped and forgotten" scenario.

## 6. Timeline

| Time (UTC) | Event |
|---|---|
| 09:27:50 | Executor scope started (`ensemble-upgrade-r-20260907-092750-a6a0.scope`) |
| 09:41:16 | in_flight txn opened, target v0.18.0, f2_verified |
| 09:53:50 | Executor calls systemctl stop → daemon begins graceful shutdown |
| 09:53:51 | **Daemon's cleanup kills the executor** ("bash cleanup_all: killed 1 process(es)") |
| 09:53:51 | Executor scope consumed (26min CPU — it had been running since 09:27) |
| 09:53:58 | Daemon shutdown complete, systemd unit → inactive (clean stop) |
| 09:53–10:20 | Daemon DOWN (~30 min) — no restart (clean stop = no Restart= trigger) |
| ~10:15 | DevOps investigates (user report: "daemon died") |
| ~10:18 | Stale in_flight cleared (flipped: false — safe), lock removed |
| ~10:20 | systemctl start → daemon boots on v0.17.2 (last-known-good) |
| ~10:22 | All green: livez v0.17.2, readyz healthy, NRestarts=0 |

## 7. Current State

- Daemon: **v0.17.2**, healthy, systemd-owned, NRestarts=0
- Journal: clean (in_flight cleared, current=v0.17.2)
- v0.18.0 tag: exists upstream, not deployed
- The self-upgrade path is **blocked** until Fix A or B lands — every agent-gated upgrade will hit this kill
- Manual (SSH-executed) promotes are NOT affected — the executor runs from the operator's SSH session, not as a daemon child

---

## 8. RECURRENCE — Same Bug, Second Kill (2026-10-07 ~10:42 UTC)

The agent retried the v0.18.0 upgrade ~20 minutes after the first recovery. **Same failure, deterministic**:

| Check | State |
|---|---|
| Daemon | UNREACHABLE, systemd inactive (clean stop, no restart) |
| in_flight | promote v0.18.0, flipped: false, owner_pid 1574846 (dead) |
| rollback lock | stale (from the dead executor) |
| Recovery | Same procedure: clear state + remove lock + systemctl start → v0.17.2 all-green at ~12:35 UTC |

**Downtime**: ~2 hours (10:42 → 12:35 UTC) — longer than the first because the retry happened while the operator was away.

**This confirms the bug is deterministic, not intermittent** — every agent-gated self-upgrade that reaches the stop step WILL kill the daemon via the executor cleanup. The self-upgrade path is effectively broken until Fix A or B (§5) lands.

**Immediate operational guidance**: the ensemble agents should NOT retry the self-upgrade until the fix ships. Each retry = daemon outage. If v0.18.0 needs to go out before the fix, use the manual (SSH-executed) promote path — it is unaffected because the executor runs from the operator's session, outside the daemon's process tree.

**Timeline addendum**:
| Time (UTC) | Event |
|---|---|
| 10:20 | First recovery complete (daemon on v0.17.2, all green) |
| 10:42:15 | Agent retries v0.18.0 — executor spawned |
| 10:4x | Executor stops daemon → daemon's cleanup kills executor → daemon stays down |
| 10:42–12:33 | Daemon DOWN (~2 hours) |
| 12:33 | DevOps recovers (same procedure) |
| 12:35 | Daemon all-green on v0.17.2 |

---

## 9. Manual v0.18.0 Push (2026-10-07 ~13:24 UTC) — Success with Post-Flip Hang

The manual SSH promote to v0.18.0 completed, but hit a NEW hang variant:

**What worked**: preflight (plugin fresh, integrity PASS both releases), stop via systemctl, launcher swap, atomic flip — all clean.

**What hung**: the promote STUCK between flip and hand-back (parent in pipe_read 10+ min, child subshell spinning, log frozen, no systemctl start issued). Daemon stayed stopped.

**Recovery**: manual systemctl start → daemon boot sweep halted on the stale flipped txn (sweep-halt AGAIN — §4 pattern) → manual journal commit (correct: flip was intentional, release integrity-verified pre-flip) → clean restart → v0.18.0 all-green.

**Root-cause hypothesis for the post-flip hang**: same pipe_read class as the journal-sweep hang (§4 of runbook) — a subshell in the hand-back path blocks indefinitely. The promote code needs a TIMEOUT on all post-flip subprocess waits; without one, any slow/hung child stalls the ceremony with the daemon down.

**Status**: v0.18.0 committed, all-green, NRestarts=0. Awaiting v0.18.1 with the executor-kill fix (manual push #2, then self-upgrade healed permanently per the team's plan).
