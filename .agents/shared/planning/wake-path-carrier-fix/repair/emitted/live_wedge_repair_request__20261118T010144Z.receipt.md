# LIVE WEDGE REPAIR REQUEST — NOT EXECUTED

> **NOT-EXECUTED BANNER.** No statement in this request has been run, by any process, at any time. This artifact is a PROPOSAL for the daemon's gated repair path. The generating script has no `--apply` and opens every connection with `default_transaction_read_only=on`.

> **Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.**

**Generated:** 2026-11-18T01:01:44.238050+00:00  
**Database observed:** `ensemble_prod`  
**Parent instance:** `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`)  
**Classification at emit time:** `not_yet`  
**Gate flag presented:** `wake-path-carrier-fix/live-wedge/17efd2ef/w8`  
**Pre-check JSON sha256:** `d93649ec1bf2a00e7ae4cb15306a514d0c572653aaac1a2afac75372a6658444`  
**Statement file:** `live_wedge_repair_request__20261118T010144Z.sql`  
**Inventory fingerprint (parent-scoped, the --verify anchor):** `f457bcb339f7f860bbd700f2acb54f35e307a07273afc9c9c9a90cba1bd8cfcd` (0 other parent watchers, 0 other parent commissions)  
**Inventory fingerprint (global scan, informational on a live fleet):** `964bb3f368abdacf3e4076660da82e5b7234b20fb54965de9a216289620e57bd` (12167 watcher rows, 4770 commission rows)

## 1. Pre-check assertions (A1-A14)

```
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
| id  | assertion                                                              | observed                                                                                   | expected                                           | verdict |
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
| A1  | parent exists AND status == waiting_children                           | exists=True, status=waiting_children                                                       | exists=True, status=waiting_children               | PASS    |
| A2  | watcher 8a1f60d7 resolves uniquely; target==parent; source_task==39320 | matches=1, state=PENDING, disposition=in_scope_stale, target=17efd2ef-9f98-46d4-b41b-60e3e | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A3  | task 39320 exists and is in a terminal status                          | exists=True, status=completed, completed_at=2026-11-17 18:08:26.230850                     | exists=True, status in {completed,failed,cancelled | PASS    |
| A4  | watched worker 542b49a6 exists and is terminal                         | exists=True, status=completed, updated_at=2026-11-17T19:04:27.795218+00:00                 | exists=True, status in {completed,terminated,error | PASS    |
| A5  | watcher 999efa71 unique; target==parent; source_task==39424; child_id= | matches=1, state=PENDING, disposition=in_scope_stale, target=17efd2ef-9f98-46d4-b41b-60e3e | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A6  | task 39424 exists and is terminal                                      | exists=True, status=completed                                                              | exists=True, status in {completed,failed,cancelled | PASS    |
| A7  | all 4 commissions resolve uniquely; commissioner==parent; worker bindi | M1=outstanding/in_scope_stale; M2=outstanding/in_scope_stale; M3=outstanding/in_scope_stal | 4/4 identity matches, state outstanding|resolved   | PASS    |
| A8  | DRIFT GUARD: every PENDING watcher / outstanding commission of the par | pending_for_parent=4 (unknown=0), outstanding_for_parent=4 (unknown=0)                     | unknown=0 and unknown=0                            | PASS    |
| A9  | parent status IS waiting_children (a re-run never clobbers an unknown  | status=waiting_children                                                                    | status=waiting_children                            | PASS    |
| A10 | watcher 5602e91d resolves uniquely; target==parent; source_task==39564 | matches=1, state=PENDING, disposition=in_scope_stale, target=17efd2ef-9f98-46d4-b41b-60e3e | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A11 | watcher c83e12a2 resolves uniquely; target==parent; source_task==39564 | matches=1, state=PENDING, disposition=in_scope_stale, target=17efd2ef-9f98-46d4-b41b-60e3e | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A12 | commission 06001f88 resolves uniquely; commissioner==parent; worker==8 | matches=1, state=outstanding, disposition=in_scope_stale, commissioner=17efd2ef-9f98-46d4- | matches=1, identity matches, state=outstanding|res | PASS    |
| A13 | commission a1561f19 resolves uniquely; commissioner==parent; worker==8 | matches=1, state=outstanding, disposition=in_scope_stale, commissioner=17efd2ef-9f98-46d4- | matches=1, identity matches, state=outstanding|res | PASS    |
| A14 | task 39564 (source of W3/W4) exists and is terminal                    | exists=True, status=completed                                                              | exists=True, status in {completed,failed,cancelled | PASS    |
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
```

## 2. Per-row disposition (evaluated BEFORE any statement)

The leader's partial-resolution boundary: a row the system itself already resolved (`naturally_resolved`) is EXCLUDED from the statements and disclosed here, so the gate is not brittle against lane 7's TTL abandons or a pre-ceremony boot sweep. `in_scope_stale` rows get a block. A CANCELLED row whose `enqueued_at` is still NULL gets one guarded backfill block.

```
+-------+------------+----------+-------------+----------------+------------------------------------------------+
| label | kind       | id       | state       | disposition    | reason/note                                    |
+-------+------------+----------+-------------+----------------+------------------------------------------------+
| W1    | watcher    | 8a1f60d7 | PENDING     | in_scope_stale |                                                |
| W2    | watcher    | 999efa71 | PENDING     | in_scope_stale |                                                |
| W3    | watcher    | 5602e91d | PENDING     | in_scope_stale |                                                |
| W4    | watcher    | c83e12a2 | PENDING     | in_scope_stale |                                                |
| M1    | commission | 0cdc0b31 | outstanding | in_scope_stale | worker_terminal_no_report / worker completed   |
| M2    | commission | 90493206 | outstanding | in_scope_stale | ttl_expired_not_live / worker waiting_children |
| M3    | commission | 06001f88 | outstanding | in_scope_stale | ttl_expired_not_live / worker waiting_children |
| M4    | commission | a1561f19 | outstanding | in_scope_stale | ttl_expired_not_live / worker waiting_children |
+-------+------------+----------+-------------+----------------+------------------------------------------------+
```

## 3. Extended census (reported, NEVER gated)

Every PENDING watcher targeting the parent, and every anchor-scoped outstanding commission. Rows marked `in_target_set` are inside the 8-row scope; any row marked `UNKNOWN` would have refused the emit (drift guard).

```
+------------+----------+-------------+----------------+-------------+---------------+
| kind       | id       | task/worker | child/lane     | state       | scope         |
+------------+----------+-------------+----------------+-------------+---------------+
| watcher    | 8a1f60d7 | 39320       | 542b49a6       | PENDING     | in_target_set |
| watcher    | 999efa71 | 39424       | 17efd2ef       | PENDING     | in_target_set |
| watcher    | 5602e91d | 39564       | 82551f59       | PENDING     | in_target_set |
| watcher    | c83e12a2 | 39564       | 82551f59       | PENDING     | in_target_set |
| commission | 0cdc0b31 | 542b49a6    | instance_nudge | outstanding | in_target_set |
| commission | 90493206 | 17efd2ef    | instance_nudge | outstanding | in_target_set |
| commission | 06001f88 | 82551f59    | instance_nudge | outstanding | in_target_set |
| commission | a1561f19 | 82551f59    | instance_nudge | outstanding | in_target_set |
+------------+----------+-------------+----------------+-------------+---------------+
```

Park predicate value at emit time: `count_pending_for_target(parent)` = **4**; `count_outstanding_commissions(parent)` = **4**.

## 4. Statements (EMITTED ONLY — never executed)

### Block 1 (W1, repair) — dependency_watchers `8a1f60d7-c164-4b6a-8901-271152d4001b`

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
    WHERE watch_id = '8a1f60d7-c164-4b6a-8901-271152d4001b'
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

### Block 2 (W2, repair) — dependency_watchers `999efa71-8ce3-4fd5-b19e-1627e0018205`

- **Current state:** `PENDING` → **target state:** `CANCELLED`
- **Guard:** full PK + `state = 'PENDING'` + `commissioner/target_instance_id = parent`
- **Expected rowcount:** 1
- **Intended effect:** release the nudge-armed watcher from the park count. Base end state mirrors the bus's own cancel transition (CANCELLED + fired_at — repository.py:717-762; boot sweep dependency_bus.py:2653-2666). DELIBERATE ADDITION: enqueued_at is stamped as well, which excludes the row from the ONLY resurrection path in the codebase (find_cancelled_unenqueued_for_target requires enqueued_at IS NULL — repository.py:797-843; re-armed by instance_lifecycle.py:4125-4260 on a later pause+resume) so an Emperor-authorised retirement cannot be undone by a pause. The enqueued_at stamp is LOAD-BEARING on THIS row: its child_id is the parent itself, which is NON-terminal, so without the stamp a later pause+resume of the parent would pass the re-arm guard at instance_lifecycle.py:4230-4239 and rebuild this exact self-watcher.
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
    WHERE watch_id = '999efa71-8ce3-4fd5-b19e-1627e0018205'
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

### Block 3 (W3, repair) — dependency_watchers `5602e91d-5d4c-4fb6-9e2b-8f351d203f52`

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
    WHERE watch_id = '5602e91d-5d4c-4fb6-9e2b-8f351d203f52'
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

### Block 4 (W4, repair) — dependency_watchers `c83e12a2-83b8-46ce-9213-f5a24a5060f6`

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
    WHERE watch_id = 'c83e12a2-83b8-46ce-9213-f5a24a5060f6'
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

### Block 5 (M1, repair) — mission_commissions `0cdc0b31-8c80-4cf7-8c39-40c1ff508956`

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
    WHERE commission_id = '0cdc0b31-8c80-4cf7-8c39-40c1ff508956'
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

### Block 6 (M2, repair) — mission_commissions `90493206-7e82-4602-aa4c-70964e50d51c`

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
    WHERE commission_id = '90493206-7e82-4602-aa4c-70964e50d51c'
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

### Block 7 (M3, repair) — mission_commissions `06001f88-9e00-4720-8e59-88959d8faa75`

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
    WHERE commission_id = '06001f88-9e00-4720-8e59-88959d8faa75'
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

### Block 8 (M4, repair) — mission_commissions `a1561f19-2338-4053-9b7d-ea85c9d92613`

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
    WHERE commission_id = 'a1561f19-2338-4053-9b7d-ea85c9d92613'
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
    "commissions": 4770,
    "watchers": 12167
  },
  "global_sha256": "964bb3f368abdacf3e4076660da82e5b7234b20fb54965de9a216289620e57bd",
  "parent_keys": {
    "agent_id": "ari",
    "created_at": "2026-11-14T21:54:59.848480+00:00",
    "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13",
    "parent_id": null,
    "status": "waiting_children",
    "updated_at": "2026-11-18T00:37:24.304543+00:00",
    "version": 99
  },
  "parent_scoped_counts": {
    "commissions": 0,
    "watchers": 0
  },
  "parent_scoped_sha256": "f457bcb339f7f860bbd700f2acb54f35e307a07273afc9c9c9a90cba1bd8cfcd"
}
```

**Parent-scoped rows at emit time** (the exact set `--verify` re-diffs):

```

```

**Parent row keys at emit time:** `{"agent_id": "ari", "created_at": "2026-11-14T21:54:59.848480+00:00", "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13", "parent_id": null, "status": "waiting_children", "updated_at": "2026-11-18T00:37:24.304543+00:00", "version": 99}`

## 6. Authority statement

**Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.** The executor is `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py:939`), reachable only from an agent holding the privileged `ens-db` category (today: `maintenancer`). Its three gates are: kill-switch `ENSEMBLE_REPAIR_ENABLED`, input validation (idempotency + migration-runner trap + self-surgery refusal), and a single-use confirm nonce that only a DRY RUN mints.

**Rollback is NOT free.** See `RUNBOOK.md` §9: reverting these rows means re-inserting a PENDING watcher, which RE-WEDGES the parent.
