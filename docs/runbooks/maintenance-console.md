# Maintenance Console — Operator Runbook (Section 1: Checkpoint Cleanup)

Date: 2026-09-27 · Scope: Phase-1 backend (`/api/maintenance/checkpoint-cleanup/*`) ·
Plan: `.agents/shared/planning/maintenance-console/plan-overview.md` (Contract v3).

## Kill-switch (AM-13)

`MAINTENANCE_ENDPOINTS_ENABLED=0` (boot-read — restart the daemon after
editing) → endpoints 2–5 return `503 maintenance_disabled`; `/availability`
returns `200 {state: "kill_switched"}`. The auto cycle's env dual-arm
(`CHECKPOINT_BLOB_PRUNE_DRY_RUN` / `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE`) is
UNAFFECTED by this switch (INV-1): auto stays destructive-if-armed while the
manual API refuses. That split is by design.

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
