# SYNTHETIC FIXTURE — NOT A LIVE REQUEST

> This receipt was generated from a **synthetic fixture**, not from `ensemble_prod`. It exists so a reviewer can verify the emitter's guard / fingerprint / receipt mechanics. **Do not execute anything in this file.**

**Generated:** 2026-11-17T00:00:00+00:00  
**Database observed:** `SYNTHETIC-FIXTURE-NOT-A-DATABASE`  
**Parent instance:** `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`)  
**Classification at emit time:** `not_yet`  
**Gate flag presented:** `wake-path-carrier-fix/live-wedge/17efd2ef`  
**Pre-check JSON sha256:** `163e43bea11843b133af9d831ced89a2205a540c7fb88295da86d426931f6f06`  
**Statement file:** `live_wedge_repair_request__SELFTEST__20261117T235441Z.sql`  
**Inventory fingerprint (parent-scoped, the --verify anchor):** `1111111111111111111111111111111111111111111111111111111111111111` (1 other parent watchers, 0 other parent commissions)  
**Inventory fingerprint (global scan, informational on a live fleet):** `2222222222222222222222222222222222222222222222222222222222222222` (0 watcher rows, 0 commission rows)

## 1. Pre-check assertions (A1-A9)

```
+----+------------------------------------------------------------------------+-------------------------------------------+-------------------------------------------+---------+
| id | assertion                                                              | observed                                  | expected                                  | verdict |
+----+------------------------------------------------------------------------+-------------------------------------------+-------------------------------------------+---------+
| A1 | parent exists AND status == waiting_children                           | exists=True, status=waiting_children      | exists=True, status=waiting_children      | PASS    |
| A2 | watcher 8a1f60d7 resolves uniquely; PENDING; target==parent; source_ta | matches=1, state=PENDING                  | matches=1, state=PENDING                  | PASS    |
| A3 | task 39320 exists and is in a terminal status                          | exists=True, status=completed             | exists=True, status terminal              | PASS    |
| A4 | watched worker 542b49a6 exists and is terminal                         | exists=True, status=completed             | exists=True, status terminal              | PASS    |
| A5 | watcher 999efa71 unique; PENDING; target==parent; source_task==39424;  | matches=1, state=PENDING, child_id=parent | matches=1, state=PENDING, child_id=parent | PASS    |
| A6 | task 39424 exists and is terminal                                      | exists=True, status=completed             | exists=True, status terminal              | PASS    |
| A7 | both commissions exist; state==outstanding; commissioner==parent       | matches=1+1, state=outstanding            | matches=1+1, state=outstanding            | PASS    |
| A8 | PENDING-watcher count for the parent == exactly 2                      | count=2                                   | count=2                                   | PASS    |
| A9 | parent status IS waiting_children                                      | status=waiting_children                   | status=waiting_children                   | PASS    |
+----+------------------------------------------------------------------------+-------------------------------------------+-------------------------------------------+---------+
```

## 2. Extended census (reported, NEVER gated)

Every PENDING watcher targeting the parent, and every anchor-scoped outstanding commission. Rows marked `same_class_extra` are **not** in this repair's target set.

```
+------------+----------+-------------+----------------+-------------+---------------------+
| kind       | id       | task/worker | child/lane     | state       | scope               |
+------------+----------+-------------+----------------+-------------+---------------------+
| watcher    | deadbeef | 39320       | 542b49a6       | PENDING     | in_commission_scope |
| watcher    | deadbeef | 39424       | 17efd2ef       | PENDING     | in_commission_scope |
| commission | feedface | 542b49a6    | instance_nudge | outstanding | in_commission_scope |
| commission | cafebabe | 17efd2ef    | instance_nudge | outstanding | in_commission_scope |
+------------+----------+-------------+----------------+-------------+---------------------+
```

Park predicate value at emit time: `count_pending_for_target(parent)` = **2**; `count_outstanding_commissions(parent)` = **2**.

## 3. Statements (EMITTED ONLY — never executed)

### Block 1 — dependency_watchers `deadbeef-0000-4000-8000-000000000001`

- **Current state:** `PENDING` → **target state:** `CANCELLED`
- **Guard:** full PK + `state = 'PENDING'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** release the nudge-armed watcher from the park count. Base end state mirrors the bus's own cancel transition (CANCELLED + fired_at — repository.py:717-762; boot sweep dependency_bus.py:2653-2666). DELIBERATE ADDITION: enqueued_at is stamped as well, which excludes the row from the ONLY resurrection path in the codebase (find_cancelled_unenqueued_for_target requires enqueued_at IS NULL — repository.py:797-843; re-armed by instance_lifecycle.py:4125-4260 on a later pause+resume) so an Emperor-authorised retirement cannot be undone by a pause.
- **Why this state (source):** watched child 542b49a6 completed 19:04:27 but the COMPLETED lane has no target-agnostic child-keyed fire, so this watcher can never fire; CANCELLED is inert for every fire primitive (all three match state='PENDING' only).

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: W1 cancel 8a1f60d7
    UPDATE dependency_watchers
    SET state = 'CANCELLED',
        fired_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'deadbeef-0000-4000-8000-000000000001'
      AND state = 'PENDING'
      AND target_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W1 cancel 8a1f60d7: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W1 cancel 8a1f60d7: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W1 cancel 8a1f60d7: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 2 — dependency_watchers `deadbeef-0000-4000-8000-000000000002`

- **Current state:** `PENDING` → **target state:** `CANCELLED`
- **Guard:** full PK + `state = 'PENDING'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** release the self-nudge watcher from the park count. Same end state and same inertness argument as W1. The enqueued_at stamp is LOAD-BEARING on THIS row specifically: its child_id is the parent itself, which is NON-terminal, so without the stamp a later pause+resume of the parent would pass the re-arm guard at instance_lifecycle.py:4230-4239 and rebuild this exact self-watcher.
- **Why this state (source):** structurally unfulfillable: child_id == target_instance_id == commissioner, so no terminal event can ever fire it. It also holds RDRS lane 7's bus-watcher probe for commission 90493206 (report_delivery_recovery.py:2841-2859) — cancelling it unblocks that commission's own reconciliation.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: W2 cancel 999efa71
    UPDATE dependency_watchers
    SET state = 'CANCELLED',
        fired_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'deadbeef-0000-4000-8000-000000000002'
      AND state = 'PENDING'
      AND target_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W2 cancel 999efa71: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W2 cancel 999efa71: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W2 cancel 999efa71: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 3 — mission_commissions `feedface-0000-4000-8000-000000000003`

- **Current state:** `outstanding` → **target state:** `abandoned`
- **Guard:** full PK + `state = 'outstanding'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** leave `outstanding` so the mission leg of _root_completion_gate (child_reports.py:2691-2695) stops counting it. Mirrors MissionRecordRepository.transition_commission_state (repository.py:726-766): satisfied_at stays NULL, resolved_at is stamped, reason from the frozen vocabulary.
- **Why this state (source):** the commissioned worker (sonic-leader) is terminal and no report was ever delivered — this is EXACTLY RDRS lane 7's worker_terminal_no_report abandonment (report_delivery_recovery.py:2929-2937), which currently cannot run only because the PENDING watcher holds its probe at :2841-2859.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: M1 abandon 0cdc0b31
    UPDATE mission_commissions
    SET state = 'abandoned',
        resolved_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        abandon_reason = 'worker_terminal_no_report'
    WHERE commission_id = 'feedface-0000-4000-8000-000000000003'
      AND state = 'outstanding'
      AND commissioner_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'M1 abandon 0cdc0b31: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'M1 abandon 0cdc0b31: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'M1 abandon 0cdc0b31: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 4 — mission_commissions `cafebabe-0000-4000-8000-000000000004`

- **Current state:** `outstanding` → **target state:** `abandoned`
- **Guard:** full PK + `state = 'outstanding'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** leave `outstanding`. Same transition mechanics as M1.
- **Why this state (source):** self-nudge: commissioner == worker == target. The worker is the parent itself (not terminal), so the sweep's R1 leg does not apply; its R2b TTL backstop is the only source-sanctioned abandon, and it lands at created_at + 21600s (report_delivery_recovery.py:2941-2947, config.py:2400-2421 TTL default 6h). DECLARED DIVERGENCE: this statement acts BEFORE the TTL elapses. The END STATE is identical to what lane 7 will write at TTL; only the timing differs. If the Emperor prefers zero guesswork, DROP this block and let lane 7 abandon 90493206 at its TTL (~02:30Z).

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: M2 abandon 90493206
    UPDATE mission_commissions
    SET state = 'abandoned',
        resolved_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        abandon_reason = 'ttl_expired_not_live'
    WHERE commission_id = 'cafebabe-0000-4000-8000-000000000004'
      AND state = 'outstanding'
      AND commissioner_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'M2 abandon 90493206: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'M2 abandon 90493206: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'M2 abandon 90493206: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

## 4. Inventory snapshot (for --verify)

Two snapshots, both `sha256` over ORDERED PK-keyed dumps, both EXCLUDING the four target rows, both including the parent row's key fields (`instance_id`, `agent_id`, `status`, `parent_id`, `created_at`, `updated_at`, `version`):

* **`parent_scoped_sha256`** — every `dependency_watchers` row whose `target_instance_id` is the parent, plus every `mission_commissions` row of the parent's mission epochs. **This is the load-bearing before/after anchor** for `--verify`, because it is exactly the population this repair can plausibly move.
* **`global_sha256`** — the literal "everything else in both tables" scan, recorded for audit. On a live fleet this population churns constantly, so equality across time is only achievable on a QUIESCED system; `--verify` reports it as informational and lists the row-level delta instead of failing on unrelated churn.

```json
{
  "global_counts": {
    "commissions": 0,
    "watchers": 0
  },
  "global_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "parent_keys": {
    "agent_id": "ari",
    "created_at": "2026-11-14T21:54:59.848480+00:00",
    "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13",
    "parent_id": null,
    "status": "waiting_children",
    "updated_at": "2026-11-17T23:16:17.336402+00:00",
    "version": 97
  },
  "parent_scoped_counts": {
    "commissions": 0,
    "watchers": 1
  },
  "parent_scoped_sha256": "1111111111111111111111111111111111111111111111111111111111111111"
}
```

**Parent-scoped rows at emit time** (the exact set `--verify` re-diffs):

```

00000000-0000-4000-8000-0000000000aa	watch_id=fixture-unrelated-row state=FIRED
```

**Parent row keys at emit time:** `{"agent_id": "ari", "created_at": "2026-11-14T21:54:59.848480+00:00", "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13", "parent_id": null, "status": "waiting_children", "updated_at": "2026-11-17T23:16:17.336402+00:00", "version": 97}`

## 5. Authority statement

**Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.** The executor is `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py:939`), reachable only from an agent holding the privileged `ens-db` category (today: `maintenancer`). Its three gates are: kill-switch `ENSEMBLE_REPAIR_ENABLED`, input validation (idempotency + migration-runner trap + self-surgery refusal), and a single-use confirm nonce that only a DRY RUN mints.

**Rollback is NOT free.** See `RUNBOOK.md` §9: reverting these rows means re-inserting a PENDING watcher, which RE-WEDGES the parent.
