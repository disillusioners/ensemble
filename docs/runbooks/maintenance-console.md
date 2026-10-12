# Maintenance Console — Operator Runbook (Section 1: Checkpoint Cleanup)

Date: 2026-09-27 · Scope: Phase-1 backend (`/api/maintenance/checkpoint-cleanup/*`) ·
Plan: `.agents/shared/planning/maintenance-console/plan-overview.md` (Contract v3).

## Two-pass reality on a never-pruned database (v3.2)

First manual cleanup on a fresh/never-pruned DB is a two-run journey — the
console's post-run banner will prompt the second run. Manual composition
is **E→D** (Op E reference-aware blob prune runs BEFORE Op D row prune) by
design — pass 1 deletes excess checkpoint rows and frees `now == 0` blob
bytes when every blob is referenced; the rows D deletes orphan the
excess-only-referenced blobs, which a follow-up run then frees. The
`bytes_reclaimable_after_row_prune` projection labels this honestly; the
post-run banner surfaces the recovery CTA.

## Kill-switch (AM-13)

`MAINTENANCE_ENDPOINTS_ENABLED=0` (boot-read — restart the daemon after
editing) → endpoints 2–5 return `503 maintenance_disabled`; `/availability`
returns `200 {state: "kill_switched"}`. The auto cycle's blob prune is
**not** affected by this switch — destructive is the unconditional
default since feature/blob-prune-default-on (the env-flag ladder
``CHECKPOINT_BLOB_PRUNE_DRY_RUN`` / ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE``
is gone). To stop the auto cycle entirely: **PRIMARY** — stop the
daemon (`sudo systemctl stop <unit>`; unit naming per
`docs/runbooks/systemd-adoption.md` — `~/agents-ensemble` →
`ensemble-main.service`, `~/agents-ensemble-demo` →
`ensemble-demo.service`; discover with
`systemctl list-units --type=service 'ensemble-*'`). **SECONDARY**
(config stop; requires the fix-cycle-1 never-due guard on
`feature/blob-prune-default-on` forward) — set
`PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` in the install's `.env`
and restart: `_is_due` treats an interval `<= 0` as disabled, so the
job never fires. (A previous revision of this runbook suggested an
UNPREFIXED interval env name here — nothing reads such a name:
`PersistenceConfig` uses the `PERSISTENCE_` env prefix, so the env
var MUST carry it. If the stale unprefixed var was set on an install,
remove it.) The manual API surface stays
kill-switched independently.

## Origin guard (AM-1 + C1 hardening)

- No `Origin` header (curl, systemd) → allowed.
- Same-origin → allowed **only when the request `Host` passes the C1
  allowlist**: loopback family (`localhost`, any `127.0.0.0/8` address,
  `::1`) + hosts parsed from `MAINTENANCE_TRUSTED_ORIGINS` + the explicit
  `MAINTENANCE_ALLOWED_HOSTS` CSV (bare hostnames; default empty).
  Both env vars are read lazily at first use — **restart the daemon to
  apply edits**.
- `Origin` host in the localhost family (any port) → allowed.
- `Origin ∈ MAINTENANCE_TRUSTED_ORIGINS` (full origin strings) → allowed.
- Anything else → `403 origin_not_trusted`.

To expose the console on a LAN hostname, set e.g.
`MAINTENANCE_ALLOWED_HOSTS=ops-box.lan` (and, for a browser on another
machine, `MAINTENANCE_TRUSTED_ORIGINS=http://ops-box.lan:8079`) and restart.

## Boot sweep (AM-7) — and the W1 heal line

Every boot runs an unconditional CAS over `maintenance_runs`:

```sql
UPDATE maintenance_runs SET status='interrupted' WHERE status='running';
```

(flipping every orphaned `running` row to `interrupted` with
`error_json.code='run_interrupted'`). The sweep is retried up to 3× with
backoff at boot (W1); the ordering sweep-before-auto-cycle-start is
structural (W2).

**Manual heal (W1)** — if the boot sweep fails on EVERY attempt (ERROR log
line `maintenance boot sweep FAILED after 3 attempts`), a stale `running`
row 409-wedges all cleanup (`run_in_flight` on every dry-run/execute,
409 body carries `details.heal_hint`). Heal against the daemon's database:

```sql
UPDATE maintenance_runs SET status='interrupted' WHERE status='running';
```

…then restart the daemon (or simply retry the operation — the row is now
terminal, the gate is free). This is the same statement the sweep runs;
running it by hand is safe at any time the daemon is NOT mid-run (check
`GET /api/maintenance/checkpoint-cleanup/status` → `in_flight` is `null`
first).

## Wedged in-flight run (no cancel endpoint in v1)

v1 has no cancel endpoint (leader ruling 3). Recourse for a wedged run:
daemon restart → the boot sweep marks the orphaned row `interrupted` →
re-execute. Prune is retention-idempotent; a partial pass finishes on the
next run.

## VACUUM FULL headroom check — co-location assumption (R6, 2026-10-12)

When an operator ticks the **VACUUM FULL** checkbox on the destructive
prune and the daemon-side headroom pre-flight runs (Phase-2 manual
escalation; `daemon/services/maintenance.py:_check_vacuum_full_headroom`),
the daemon `os.statvfs`-stats the tablespace path it resolved from PG
(`pg_tablespace_location` → fallback `SHOW data_directory`). The check
**assumes daemon and PG are co-located** (same host, same filesystem).
The decision-to-refuse-or-skip still stands; the residual ENOSPC catch
(task 2.11 / `error_json.code == 'vacuum_full_enospc'`, retriable after
space is freed) is the **safety net** for any case where the headroom
check cannot see what it needs to see (see "Tunnel topologies" below).

**Tunnel topologies (HEURISTIC WARNING).** A loopback listener (PG URL
host ∈ `localhost` / `127.0.0.1` / `::1` / `0.0.0.0`) AND a
locally-resolvable data_directory is **NOT proof of daemon/PG
co-location**. SSH-tunnels and similar port-forward shims can
false-positive the check — the daemon's `statvfs` measures the **daemon
host's disk**, not the PG host's. When this case is detected, the
daemon emits ONE `WARNING` line at the headroom-check entry (`"VACUUM
FULL headroom heuristic: loopback listener ... NOT proof of ... /
SSH-tunnel topologies can false-positive ..."`); the check still runs,
the FULL still proceeds unless the headroom is clearly insufficient,
and the residual ENOSPC path is the safety net if a tunnel falsifies
a pass. Operators seeing the WARNING on every cycle should verify the
PG host is reachable by the headroom check (or skip the FULL arm —
the destructive prune alone is the primary reclaim path; VACUUM FULL is
an operator convenience).

**The residual ENOSPC path is the net.** Even with daemon/PG
co-location, the headroom check narrows but cannot eliminate the
check-race window (concurrent writes, WAL/CLOG churn during the
rewrite). When the FULL still hits disk-full, the audit row is stamped
`error_json = {'code': 'vacuum_full_enospc', 'message': …, 'retriable':
True}`. NO auto-retry — the operator frees space and re-runs the
dry-run → execute cycle. The failure is never swallowed: it lands on
the audit row, the gate is released, the operator gets a clear
"VACUUM FULL failed: disk full (PG SQLSTATE 53100)" message.
