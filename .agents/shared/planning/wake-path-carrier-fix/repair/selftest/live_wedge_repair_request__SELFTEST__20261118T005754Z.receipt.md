# SYNTHETIC FIXTURE — NOT A LIVE REQUEST

> This receipt was generated from a **synthetic fixture**, not from `ensemble_prod`. It exists so a reviewer can verify the emitter's guard / fingerprint / receipt mechanics. **Do not execute anything in this file.**

**Generated:** 2026-11-18T00:00:00+00:00  
**Database observed:** `SYNTHETIC-FIXTURE-NOT-A-DATABASE`  
**Parent instance:** `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`)  
**Classification at emit time:** `partial`  
**Gate flag presented:** `wake-path-carrier-fix/live-wedge/17efd2ef/w8`  
**Pre-check JSON sha256:** `66de9d2adb7c4fcd9554e8c016e2e958f755130f8a3824062fa40733c11e1b5a`  
**Statement file:** `live_wedge_repair_request__SELFTEST__20261118T005754Z.sql`  
**Inventory fingerprint (parent-scoped, the --verify anchor):** `1111111111111111111111111111111111111111111111111111111111111111` (1 other parent watchers, 0 other parent commissions)  
**Inventory fingerprint (global scan, informational on a live fleet):** `2222222222222222222222222222222222222222222222222222222222222222` (0 watcher rows, 0 commission rows)

## 1. Pre-check assertions (A1-A14)

```
+----+-----------+----------+----------+---------+
| id | assertion | observed | expected | verdict |
+----+-----------+----------+----------+---------+
+----+-----------+----------+----------+---------+
```

## 2. Per-row disposition (evaluated BEFORE any statement)

The leader's partial-resolution boundary: a row the system itself already resolved (`naturally_resolved`) is EXCLUDED from the statements and disclosed here, so the gate is not brittle against lane 7's TTL abandons or a pre-ceremony boot sweep. `in_scope_stale` rows get a block. A CANCELLED row whose `enqueued_at` is still NULL gets one guarded backfill block.

```
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
| label | kind       | id       | state       | disposition        | reason/note                                    |
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
| W1    | watcher    | 8a1f60d7 | PENDING     | in_scope_stale     |                                                |
| W2    | watcher    | 999efa71 | CANCELLED   | naturally_resolved | needs enqueued_at backfill                     |
| W3    | watcher    | 5602e91d | PENDING     | in_scope_stale     |                                                |
| W4    | watcher    | c83e12a2 | PENDING     | in_scope_stale     |                                                |
| M1    | commission | 0cdc0b31 | outstanding | in_scope_stale     | worker_terminal_no_report / worker completed   |
| M2    | commission | 90493206 | outstanding | in_scope_stale     | ttl_expired_not_live / worker waiting_children |
| M3    | commission | 06001f88 | outstanding | in_scope_stale     | ttl_expired_not_live / worker waiting_children |
| M4    | commission | a1561f19 | outstanding | in_scope_stale     | ttl_expired_not_live / worker waiting_children |
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
```

## 3. Extended census (reported, NEVER gated)

Every PENDING watcher targeting the parent, and every anchor-scoped outstanding commission. Rows marked `in_target_set` are inside the 8-row scope; any row marked `UNKNOWN` would have refused the emit (drift guard).

```
(no rows)
```

Park predicate value at emit time: `count_pending_for_target(parent)` = **3**; `count_outstanding_commissions(parent)` = **4**.

## 4. Statements (EMITTED ONLY — never executed)

### Block 1 (W1, repair) — dependency_watchers `deadbeef-0000-4000-8000-000000000001`

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

### Block 2 (W2-backfill, backfill) — dependency_watchers `deadbeef-0000-4000-8000-000000000002`

- **Current state:** `CANCELLED` → **target state:** `CANCELLED (enqueued_at stamped)`
- **Guard:** full PK + `state = 'CANCELLED'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** stamp enqueued_at on a row this repair did NOT cancel (a pre-ceremony boot sweep got there first). Same end state the repair's own cancel writes; closes the pause+resume re-arm hole (repository.py:797-843). Does not touch state/fired_at.
- **Why this state (source):** the boot sweep cancels with state='CANCELLED', fired_at=now only — enqueued_at stays NULL, so the row is re-arm eligible. This is a DISCLOSED extension of the ruling's own safety argument, not a widening of its row scope.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: W2 backfill enqueued_at 999efa71
    UPDATE dependency_watchers
    SET enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'deadbeef-0000-4000-8000-000000000002'
      AND state = 'CANCELLED'
      AND enqueued_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W2 backfill enqueued_at 999efa71: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W2 backfill enqueued_at 999efa71: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W2 backfill enqueued_at 999efa71: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 3 (W3, repair) — dependency_watchers `deadbeef-0000-4000-8000-000000000005`

- **Current state:** `PENDING` → **target state:** `CANCELLED`
- **Guard:** full PK + `state = 'PENDING'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** release the nudge-armed watcher from the park count. Base end state mirrors the bus's own cancel transition (CANCELLED + fired_at — repository.py:717-762; boot sweep dependency_bus.py:2653-2666). DELIBERATE ADDITION: enqueued_at is stamped as well, which excludes the row from the ONLY resurrection path in the codebase (find_cancelled_unenqueued_for_target requires enqueued_at IS NULL — repository.py:797-843; re-armed by instance_lifecycle.py:4125-4260 on a later pause+resume) so an Emperor-authorised retirement cannot be undone by a pause. The enqueued_at stamp is LOAD-BEARING on THIS row too: child 82551f59 (leader) is NON-terminal, so without the stamp a later pause+resume would re-arm it (instance_lifecycle.py:4230-4241).
- **Why this state (source):** source task 39564 is completed (23:03:54) and 82551f59 is not a structural child of the parent, so none of the three fire primitives can match — the same defect class as W1/W2, past the point of no return. It is the mutual-deadlock partner of M3 (lane 7's bus-watcher probe holds the commission while the watcher is PENDING).

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: W3 cancel 5602e91d
    UPDATE dependency_watchers
    SET state = 'CANCELLED',
        fired_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'deadbeef-0000-4000-8000-000000000005'
      AND state = 'PENDING'
      AND target_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W3 cancel 5602e91d: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W3 cancel 5602e91d: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W3 cancel 5602e91d: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 4 (W4, repair) — dependency_watchers `deadbeef-0000-4000-8000-000000000006`

- **Current state:** `PENDING` → **target state:** `CANCELLED`
- **Guard:** full PK + `state = 'PENDING'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** release the nudge-armed watcher from the park count. Base end state mirrors the bus's own cancel transition (CANCELLED + fired_at — repository.py:717-762; boot sweep dependency_bus.py:2653-2666). DELIBERATE ADDITION: enqueued_at is stamped as well, which excludes the row from the ONLY resurrection path in the codebase (find_cancelled_unenqueued_for_target requires enqueued_at IS NULL — repository.py:797-843; re-armed by instance_lifecycle.py:4125-4260 on a later pause+resume) so an Emperor-authorised retirement cannot be undone by a pause. The enqueued_at stamp is LOAD-BEARING on THIS row too: child 82551f59 (leader) is NON-terminal, so without the stamp a later pause+resume would re-arm it (instance_lifecycle.py:4230-4241).
- **Why this state (source):** same shape as W3 (source task 39564 completed; child 82551f59 non-structural and non-terminal) and the mutual-deadlock partner of M4.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: W4 cancel c83e12a2
    UPDATE dependency_watchers
    SET state = 'CANCELLED',
        fired_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'deadbeef-0000-4000-8000-000000000006'
      AND state = 'PENDING'
      AND target_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W4 cancel c83e12a2: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W4 cancel c83e12a2: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W4 cancel c83e12a2: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 5 (M1, repair) — mission_commissions `feedface-0000-4000-8000-000000000003`

- **Abandon reason:** `worker_terminal_no_report` (worker `542b49a6-8334-4703-96a8-6628dfb2ab06`, status `completed`)
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

### Block 6 (M2, repair) — mission_commissions `feedface-0000-4000-8000-000000000004`

- **Abandon reason:** `ttl_expired_not_live` (worker `17efd2ef-9f98-46d4-b41b-60e3e32b2d13`, status `waiting_children`)
- **Current state:** `outstanding` → **target state:** `abandoned`
- **Guard:** full PK + `state = 'outstanding'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** leave `outstanding` so the mission leg of _root_completion_gate (child_reports.py:2691-2695) stops counting it. Mirrors MissionRecordRepository.transition_commission_state (repository.py:726-766): satisfied_at stays NULL, resolved_at is stamped, reason from the frozen vocabulary.
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
    WHERE commission_id = 'feedface-0000-4000-8000-000000000004'
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

### Block 7 (M3, repair) — mission_commissions `feedface-0000-4000-8000-000000000007`

- **Abandon reason:** `ttl_expired_not_live` (worker `82551f59-4603-4b88-bbce-c9bccfa6fcd5`, status `waiting_children`)
- **Current state:** `outstanding` → **target state:** `abandoned`
- **Guard:** full PK + `state = 'outstanding'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** leave `outstanding` so the mission leg of _root_completion_gate (child_reports.py:2691-2695) stops counting it. Mirrors MissionRecordRepository.transition_commission_state (repository.py:726-766): satisfied_at stays NULL, resolved_at is stamped, reason from the frozen vocabulary.
- **Why this state (source):** minted by the parent's recurring self-nudge; worker 82551f59 (leader) is NON-terminal, so lane 7's R1 leg does not apply and its R2b TTL backstop is the only source-sanctioned abandon (TTL ≈ 04:52Z). DECLARED DIVERGENCE: acts BEFORE the TTL; the end state is identical to lane 7's own write, only the timing differs. NOTE: this closes the ledger entry for a lane that is still working; the lane's report routes through the session lane, not the mission ledger, so it is unaffected.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: M3 abandon 06001f88
    UPDATE mission_commissions
    SET state = 'abandoned',
        resolved_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        abandon_reason = 'ttl_expired_not_live'
    WHERE commission_id = 'feedface-0000-4000-8000-000000000007'
      AND state = 'outstanding'
      AND commissioner_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'M3 abandon 06001f88: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'M3 abandon 06001f88: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'M3 abandon 06001f88: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 8 (M4, repair) — mission_commissions `feedface-0000-4000-8000-000000000008`

- **Abandon reason:** `ttl_expired_not_live` (worker `82551f59-4603-4b88-bbce-c9bccfa6fcd5`, status `waiting_children`)
- **Current state:** `outstanding` → **target state:** `abandoned`
- **Guard:** full PK + `state = 'outstanding'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** leave `outstanding` so the mission leg of _root_completion_gate (child_reports.py:2691-2695) stops counting it. Mirrors MissionRecordRepository.transition_commission_state (repository.py:726-766): satisfied_at stays NULL, resolved_at is stamped, reason from the frozen vocabulary.
- **Why this state (source):** same shape as M3 (worker 82551f59 non-terminal, TTL ≈ 04:53Z); same declared before-TTL divergence and the same disclosure that the in-flight lane's report is not blocked by this ledger close.

```sql
DO $$
DECLARE
    n integer;
BEGIN
    -- repair block: M4 abandon a1561f19
    UPDATE mission_commissions
    SET state = 'abandoned',
        resolved_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
        abandon_reason = 'ttl_expired_not_live'
    WHERE commission_id = 'feedface-0000-4000-8000-000000000008'
      AND state = 'outstanding'
      AND commissioner_instance_id = '17efd2ef-9f98-46d4-b41b-60e3e32b2d13';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'M4 abandon a1561f19: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'M4 abandon a1561f19: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'M4 abandon a1561f19: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

## 5. Inventory snapshot (for --verify)

Two snapshots, both `sha256` over ORDERED PK-keyed dumps, both EXCLUDING ALL EIGHT target rows, both including the parent row's key fields (`instance_id`, `agent_id`, `status`, `parent_id`, `created_at`, `updated_at`, `version`):

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
    "created_at": "2026-11-14T21:54:59+00:00",
    "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13",
    "parent_id": null,
    "status": "waiting_children",
    "updated_at": "2026-11-18T00:04:47+00:00",
    "version": 98
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

00000000-0000-4000-8000-0000000000aa	watch_id=fixture-unrelated state=FIRED
```

**Parent row keys at emit time:** `{"agent_id": "ari", "created_at": "2026-11-14T21:54:59+00:00", "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13", "parent_id": null, "status": "waiting_children", "updated_at": "2026-11-18T00:04:47+00:00", "version": 98}`

## 6. Authority statement

**Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.** The executor is `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py:939`), reachable only from an agent holding the privileged `ens-db` category (today: `maintenancer`). Its three gates are: kill-switch `ENSEMBLE_REPAIR_ENABLED`, input validation (idempotency + migration-runner trap + self-surgery refusal), and a single-use confirm nonce that only a DRY RUN mints.

**Rollback is NOT free.** See `RUNBOOK.md` §9: reverting these rows means re-inserting a PENDING watcher, which RE-WEDGES the parent.
