# Runbook: Hard-Reboot Recovery

Operator entry point for the hard-reboot recovery feature: after a hard machine/VM
power-loss reboot (the kernel SIGKILLs every process), the daemon comes back and
transparently continues the work that was in flight. Binding design contract:
`.agents/shared/planning/hard-reboot-recovery/` — `plan-overview.md`, `decisions.md`
(D1-D18), `phase1-plan.md`, `technical-analysis.md`, `architecture-recommendation.md` (A1-A11).

## 1. What happens automatically

| Layer | Mechanism | Where |
|---|---|---|
| PG startup | `After=postgresql.service` (ordering ONLY) + boot preflight `SELECT 1` (exit 75 → budget-exempt backoff) + `connect_timeout=BOOT_DB_TIMEOUT_S` (10s) | `scripts/systemd/ensemble-daemon.service`, `daemon/__main__.py::_boot_db_preflight`, `daemon/repositories/factory.py::create_postgres_engine` |
| Continue RUNNING work | auto-continue boot pass (existing) | `daemon/services/auto_continue_boot_pass.py` |
| Stranded queue rows | message-queue boot recovery (new) | `daemon/services/message_queue_boot_recovery.py` |
| Stale-task backstop | StaleTaskRecovery, `boot+10min` clamp (existing) | `daemon/services/stale_task_recovery.py` |

The MQ boot pass resets message-queue rows stranded in `'processing'` back to `'ready'`. It is
**reset-only** — it never re-dispatches a message. Two skip-guards protect the reset: a row
whose instance is TERMINAL is left alone (revive hazard — a later revive would deliver a
stale row), and a row backed by a RUNNING/PAUSED task is left alone (AutoContinue owns
those). Kill-switch `ENSEMBLE_HARD_REBOOT_BOOT_PASS=0` disables the pass (default ON — the
deliverable is zero-user-action).

## 2. Per-state RTO after the reboot

| State before reboot | Recovery | RTO |
|---|---|---|
| RUNNING (interrupted turn) | auto-continue resumes from the last LangGraph checkpoint | ~14s (`RestartSec=10` + boot) |
| IDLE | next worker claim | next claim cycle |
| WAITING_CHILDREN | DependencyBus (durable, re-armed at boot) | bus-driven |
| PAUSED | stays PAUSED — never auto-resumed | never (by design) |

The StaleTaskRecovery `boot+10min` clamp is an **upper-bound backstop**, NOT the target RTO — recovery landing at ~10min means the auto-continue path did not fire.

**The `~14s` figures cover the DAEMON's recovery path only** — systemd's `RestartSec=10` plus the boot pass. They do **NOT** include host-boot time: a FULL HOST REBOOT adds the machine's own power-on / firmware / bootloader time on top, so the end-to-end wall clock from power loss to a resumed turn is `host boot time + ~14s` (plus, on a remote-PG host, the PG start-to-accept window). The `kill -9` drill in §6 measures the daemon path only.

## 3. PG ordering for an EXISTING install (D15)

`scripts/upgrade/adopt-unit.sh` refuses to clobber an existing unit file, so editing the
repo template does NOT reach an installed host. The template ships the
`After=postgresql.service` line for NEW installs; existing installs need the drop-in — the
ONLY delivery mechanism that reaches them. One stanza, ordering only (no `Wants=` /
`Requires=`):

```bash
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-pg-order.conf >/dev/null <<'EOF'
[Unit]
After=postgresql.service
EOF
sudo systemctl daemon-reload && sudo systemctl restart ensemble-main
```

## 4. Triage queries

**Task-less RUNNING instances (D13 — tracked follow-up, not fixed here).** Invisible to
every boot sweep (auto-continue roots on `task`; STR, the RDRS lanes, the bus sweeps and
JobRecovery all root elsewhere), so they survive a reboot as silent ghosts. MANDATORY
tracked follow-up commission (both 2026-10-19 RCAs were instances of it); the boot pass does
not touch them.

```sql
SELECT instance_id, status, agent_id, created_at FROM instances i WHERE i.status = 'running'
  AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id = i.instance_id
                  AND t.status IN ('pending','running','paused'));
```

**No-checkpoint orphans (G4).** No checkpoint thread ⇒ the instance cannot be resumed;
StaleTaskRecovery's `boot+10min` backstop handles the task layer —
`SELECT instance_id, status, agent_id FROM instances WHERE status IN
('running','waiting_children') AND instance_id NOT IN (SELECT thread_id FROM checkpoints);`

**PAUSED stays PAUSED (G5).** Verify a paused instance did not auto-resume —
`SELECT instance_id, status, paused_at FROM instances WHERE instance_id = '<iid>' AND status
= 'paused';`

**DependencyBus started.** A hard reboot is the most likely bus-start failure moment (PG
WAL replay); a bus that failed to start is a hard error with no fallback —
`journalctl -u ensemble-main | grep "DependencyBus started"`. No line ⇒ the bus did not
start; check PG reachability first.

## 5. Operator disclosures

- **In-flight tool calls are at-least-once (NG-4).** An interrupted tool call is NOT
  re-executed by the daemon; the model is told the result is unavailable and may re-issue
  it, so a NON-IDEMPOTENT tool (an external send, a write) can run twice. The runtime
  does not replay the tool.
- **Pause durability window.** A pause torn by the power loss (graph task cancelled, DB
  write uncommitted) leaves the instance RUNNING, so it resumes — the pause did not land.
- **Scheduler one-shot duplicate-fire window (G3.B).** A scheduled one-shot whose fire was
  lost re-emits ONCE on the next boot (the lost in-RAM "already executed" marker). Single
  re-emit, bounded and operator-observable; recover by re-issuing the task manually.
- **KMS_LANE-1 handles (G7.C).** If you used KMS handles, re-attach them after a hard
  reboot, or migrate to LANE-2 / Secrets. LANE-1 deprecation is a post-promote follow-up.
- **QUEUE_DISCARD_ON_STARTUP interplay.** Shipped default is `false` (`config.yaml` →
  `queue.discard_on_startup`): the MQ boot pass clears stranded rows and the wedge becomes
  a visible `ready` row — accounting is honest, but the row is still queued. On an install
  overriding it to `true`, the deferred discard wipe removes the doomed rows instead and
  `processing_count` reaches 0 post-boot.
- **Known gaps (tracked, not fixed here).** D13 task-less RUNNING instances (MANDATORY
  follow-up, §4) · G1 JobRecovery alive-row (deferred) · G3 scheduler duplicate fire
  (parked, §5) · G7 KMS_LANE-1 deprecation (post-promote, §5) · G5 Task↔JobItem PAUSED
  reconciliation (runbook-only, §4) · G4 no-checkpoint orphans (runbook-only, §4) · NG-4
  (LangGraph invariant, §5).
- **Manual MQ-row edits carry a terminal-revive hazard (R-A7).** `send_message` to a
  TERMINAL instance REVIVES it — the messaging path auto-transitions COMPLETED /
  TERMINATED / ERROR / FAILED back to RUNNING and reuses the existing checkpoint. So
  hand-editing a `message_queue` row into a deliverable state for a terminal
  instance's row can revive that instance and deliver a stale row into it.
- **Do NOT manually edit `message_queue` rows during a reboot (R-03).** The boot pass
  owns stranded rows — it resets them under a guarded, idempotent per-row UPDATE whose
  skip-guards (terminal instance, RUNNING/PAUSED task) are evaluated inside the SAME
  statement. A hand-edited row can defeat those guards, so a manual reset can deliver
  a row whose owning task is still running, or whose instance is terminal. If a row
  must be re-driven, do it AFTER the boot pass has completed, and let the normal
  task-driven claim path deliver it.
- **The reset does NOT clear the stale error text.** The guarded UPDATE sets
  `status='ready'` and `processing_started_at=NULL`; it does NOT touch
  `error_message`, `last_error` or `retry_count`. A row that failed before the power
  loss therefore rests at `ready` still carrying its old error text and its old retry
  count. There is **no retry loop** behind that: the claim path (`dequeue`) gates on
  `status='ready'` plus the `next_retry_at` due-time, NOT on `retry_count` — so the
  stale values are cosmetic-but-alarming in the queue views, not a retry driver.

## 6. Hard-reboot drill (Q-5)

Run on a NON-live host.

1. Preconditions: `systemctl status ensemble-main` active; the instance you will interrupt
   has a live RUNNING turn. Baseline: `journalctl -u ensemble-main -n 50` + the instance's
   `id` / `status`.
2. **Simulate the hard kill** — `kill -9 <daemon-pid>`. The daemon gets NO chance to clean
   up; this is the state a power loss leaves (no SIGTERM handlers, no graceful drain). A
   full host reboot is a stronger variant; the `kill -9` is the cheap first pass.
3. Confirm the daemon came back: `systemctl status ensemble-main` active again (~14s), and
   the boot log shows `Boot preflight: PostgreSQL reachable`, `DependencyBus started`,
   `AutoContinue boot pass:`, `[HardReboot] MQ boot pass: reset=<n>`.
4. Confirm the interrupted turn RESUMED (not restarted from scratch): the checkpoint
   advanced and the message row is no longer stranded in `processing` —
   `SELECT status, last_activity_at FROM message_queue WHERE instance_id = '<iid>' ORDER BY
   enqueued_at DESC LIMIT 5;`
5. Queue accounting sane — `SELECT status, count(*) FROM message_queue GROUP BY status;`.
   No row stuck in `processing` with a `last_activity_at` older than the boot.
6. Negative checks: a PAUSED instance must still be `paused` (§4); no message may have been
   re-delivered twice (check the instance's turn count / the ledger).
