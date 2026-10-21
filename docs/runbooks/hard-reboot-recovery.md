# Runbook: Hard-Reboot Recovery

Operator entry point for the hard-reboot recovery feature. After a hard
machine/VM power-loss reboot (the kernel SIGKILLs every process; the
host loses power mid-flight), the ensemble daemon comes back and
transparently continues the work that was in flight.

Design contract (binding):
`.agents/shared/planning/hard-reboot-recovery/` — `plan-overview.md`,
`decisions.md` (D1-D18), `phase1-plan.md`,
`technical-analysis.md`, `architecture-recommendation.md` (A1-A11).

## 1. What happens automatically

| Layer | Mechanism | Where |
|---|---|---|
| PG ordering | `After=postgresql.service` (ordering ONLY) | `scripts/systemd/ensemble-daemon.service` |
| PG wait | boot preflight `SELECT 1`, exit 75 → budget-exempt infinite backoff | `daemon/__main__.py::_boot_db_preflight` |
| PG connect bound | `connect_timeout=BOOT_DB_TIMEOUT_S` (10s) on the shared engine | `daemon/repositories/factory.py::create_postgres_engine` |
| Continue RUNNING work | auto-continue boot pass (existing) | `daemon/services/auto_continue_boot_pass.py` |
| Stranded queue rows | message-queue boot recovery (new) | `daemon/services/message_queue_boot_recovery.py` |
| Stale-task backstop | StaleTaskRecovery, `boot+10min` clamp (existing) | `daemon/services/stale_task_recovery.py` |

The MQ boot pass resets message-queue rows stranded in `'processing'`
back to `'ready'`. It is **reset-only**: it never re-dispatches a
message. Two skip-guards protect the reset: a row whose instance is
TERMINAL is left alone (revive hazard — a later revive would deliver
a stale row), and a row backed by a RUNNING/PAUSED task is left alone
(AutoContinue owns those and completes the row itself).

Kill-switch: `ENSEMBLE_HARD_REBOOT_BOOT_PASS=0` disables the MQ boot
pass (default is ON — the deliverable is zero-user-action).

## 2. Per-state RTO after the reboot

| State before reboot | Recovery | RTO |
|---|---|---|
| RUNNING (interrupted turn) | auto-continue resumes from the last LangGraph checkpoint | ~14s (systemd `RestartSec=10` + boot) |
| IDLE | next worker claim | next claim cycle |
| WAITING_CHILDREN | DependencyBus (durable, re-armed at boot) | bus-driven |
| PAUSED | stays PAUSED — never auto-resumed | never (by design) |

`RestartSec=10` + boot is the ~14s figure. The StaleTaskRecovery
`boot+10min` clamp is an **upper-bound backstop**, NOT the target RTO —
if you are seeing recovery land at ~10min, the auto-continue path did
not fire and you are reading the backstop, not the normal recovery.

## 3. Applying the PG ordering to an EXISTING install

`scripts/upgrade/adopt-unit.sh` refuses to clobber an existing unit
file, so editing the repo template does NOT reach an installed host.
The unit template ships the `After=postgresql.service` line for NEW
installs; existing installs need the drop-in:

```bash
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-pg-order.conf >/dev/null <<'EOF'
[Unit]
After=postgresql.service
EOF
sudo systemctl daemon-reload
sudo systemctl restart ensemble-main
```

One stanza, ordering only — no `Wants=` / `Requires=`. This is the
ONLY delivery mechanism that reaches existing installs.

## 4. Triage queries

**Task-less RUNNING instances (D13 — tracked follow-up, not fixed
here).** These are invisible to every boot sweep (auto-continue roots
on `task`; STR, the RDRS lanes, the bus sweeps and JobRecovery all
root elsewhere), so they survive a reboot as silent ghosts:

```sql
SELECT instance_id, status, agent_id, created_at FROM instances i
WHERE i.status = 'running'
  AND NOT EXISTS (
    SELECT 1 FROM task t
    WHERE t.instance_id = i.instance_id
      AND t.status IN ('pending','running','paused')
  );
```

This class is a MANDATORY tracked follow-up commission (both 2026-10-19
RCAs were instances of it). The boot pass does not touch these rows.

**No-checkpoint orphans (G4).** An instance row with no checkpoint
thread cannot be resumed; StaleTaskRecovery's `boot+10min` backstop
handles the task layer:

```sql
SELECT instance_id, status, agent_id FROM instances
WHERE status IN ('running','waiting_children')
  AND instance_id NOT IN (SELECT thread_id FROM checkpoints);
```

**PAUSED stays PAUSED (G5).** Verify a paused instance did not
auto-resume:

```sql
SELECT instance_id, status, paused_at FROM instances
WHERE instance_id = '<iid>' AND status = 'paused';
```

**DependencyBus started.** A hard reboot is the most likely
bus-start failure moment (PG WAL replay), and a bus that failed to
start is a hard error with no fallback:

```bash
journalctl -u ensemble-main | grep "DependencyBus started"
```

No line ⇒ the bus did not start; check the PG reachability first.

## 5. Operator disclosures

- **In-flight tool calls are at-least-once (NG-4).** A tool call
  interrupted by the power loss is NOT re-executed by the daemon. The
  model is told the result is unavailable and may re-issue the call,
  so a NON-IDEMPOTENT tool (an external send, a write) can run twice.
  The tool itself is not replayed by the runtime.
- **Pause durability window.** A pause torn by the power loss (graph
  task cancelled, DB write uncommitted) leaves the instance RUNNING,
  so it resumes. The pause simply did not land.
- **Scheduler one-shot duplicate-fire window (G3.B).** A scheduled
  one-shot whose fire was lost re-emits ONCE on the next boot (the
  lost in-RAM "already executed" marker). Single re-emit, bounded and
  operator-observable; recover by re-issuing the task manually.
- **KMS_LANE-1 handles (G7.C).** If you used KMS handles, re-attach
  them after a hard reboot, or migrate to LANE-2 / Secrets. LANE-1
  deprecation is a post-promote follow-up, not yet shipped.
- **QUEUE_DISCARD_ON_STARTUP interplay.** The shipped default is
  `false` (`config.yaml` → `queue.discard_on_startup`), so the MQ boot
  pass is what clears stranded rows: the wedge becomes a visible
  `pending` row. On an install that overrides it to `true`, the
  deferred discard wipe removes the doomed rows instead, and
  `processing_count` reaches 0 post-boot. On `false` the pass leaves
  the row visible at `ready` — the queue accounting is honest, but the
  row is still queued.

## 6. Hard-reboot drill (Q-5)

Run on a NON-live host.

1. Confirm the preconditions: `systemctl status ensemble-main` is
   active; the instance you will interrupt has a live RUNNING turn.
2. Record the baseline: `journalctl -u ensemble-main -n 50`, and the
   instance's `id` / `status`.
3. **Simulate the hard kill** — `kill -9 <daemon-pid>`. The daemon
   does NOT get a chance to clean up; this is the same state a power
   loss leaves behind (no SIGTERM handlers run, no graceful drain).
   A full host reboot is a stronger variant; the `kill -9` is the
   cheap first pass.
4. Confirm the daemon came back: `systemctl status ensemble-main`
   shows it active again (~14s), and the boot log shows the expected
   sequence — `Boot preflight: PostgreSQL reachable`,
   `DependencyBus started`, `AutoContinue boot pass:`,
   `Message-queue boot recovery: reset=<n>`.
5. Confirm the interrupted turn RESUMED (not restarted from scratch):
   the instance's checkpoint advanced, and its message row is no
   longer stranded in `processing`:
   ```sql
   SELECT status, last_activity_at FROM message_queue
   WHERE instance_id = '<iid>' ORDER BY enqueued_at DESC LIMIT 5;
   ```
6. Confirm the queue accounting is sane:
   ```sql
   SELECT status, count(*) FROM message_queue GROUP BY status;
   ```
   No row should be stuck in `processing` with a `last_activity_at`
   older than the boot.
7. Negative checks: a PAUSED instance must still be `paused` (§4); no
   message may have been re-delivered twice (check the instance's turn
   count / the ledger).

## 7. Known gaps (tracked, not fixed here)

| Gap | Status |
|---|---|
| D13 — task-less RUNNING instances | MANDATORY tracked follow-up commission (§4) |
| G1 — JobRecovery alive-row | deferred (auto-continue covers the row) |
| G3 — scheduler catch-up duplicate fire | parked; single re-emit window only (§5) |
| G7 — KMS_LANE-1 deprecation | post-promote follow-up (§5) |
| G5 — Task↔JobItem PAUSED reconciliation | runbook-only (§4) |
| G4 — no-checkpoint orphans | runbook-only (§4) |
| NG-4 — in-flight tool-call at-least-once | LangGraph invariant (§5) |
