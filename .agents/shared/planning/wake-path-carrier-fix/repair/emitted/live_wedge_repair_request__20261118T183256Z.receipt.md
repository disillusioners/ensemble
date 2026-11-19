# LIVE WEDGE REPAIR REQUEST — NOT EXECUTED

> **NOT-EXECUTED BANNER.** No statement in this request has been run, by any process, at any time. This artifact is a PROPOSAL for the daemon's gated repair path. The generating script has no `--apply` and opens every connection with `default_transaction_read_only=on`.

> **Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.**

> **RECEIPT NOTE (verbatim, amendment 2026-11-18):** the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot sweep) before this repair; the remaining hold was the mission leg's two commissions. The Emperor's authorization is recorded on board item wi-8f60e6f1.

**Generated:** 2026-11-18T18:32:56.277416+00:00  
**Database observed:** `ensemble_prod`  
**Parent instance:** `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`)  
**Classification at emit time:** `partial`  
**Gate flag presented:** `wake-path-carrier-fix/live-wedge/17efd2ef/w8`  
**Pre-check JSON sha256:** `709c5417769328b8be58c41e5596a672349487b9ac594bb4d25c23fdbec0b4e6`  
**Statement file:** `live_wedge_repair_request__20261118T183256Z.sql`  
**Inventory fingerprint (parent-scoped, the --verify anchor):** `8f773cf687fbce38be96bb7e1cdc92e6040667aabf6f4477d583896f39efb85b` (0 other parent watchers, 3 other parent commissions)  
**Inventory fingerprint (global scan, informational on a live fleet):** `d4d4dd75a79ea8ca622671c3519c3c6dfac39a46d4419b032bd0ec5d2378302e` (12373 watcher rows, 4979 commission rows)

## 1. Pre-check assertions (A1-A14)

```
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
| id  | assertion                                                              | observed                                                                                   | expected                                           | verdict |
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
| A1  | parent exists AND status in {waiting_children, running} (amendment: tr | exists=True, first=waiting_children, re_read=None, final=waiting_children, mid_turn=False  | exists=True, status in {waiting_children, running} | PASS    |
| A2  | watcher 8a1f60d7 resolves uniquely; target==parent; source_task==39320 | matches=1, state=CANCELLED, disposition=naturally_resolved, target=17efd2ef-9f98-46d4-b41b | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A3  | task 39320 exists and is terminal (or GC'd after resolution for a reso | watcher resolved; source_task=MISSING (GCed, expected)                                     | exists=True & terminal, OR resolved watcher with G | PASS    |
| A4  | watched worker 542b49a6 exists and is terminal                         | exists=True, status=completed, updated_at=2026-11-17T19:04:27.795218+00:00                 | exists=True, status in {completed,terminated,error | PASS    |
| A5  | watcher 999efa71 unique; target==parent; source_task==39424; child_id= | matches=1, state=CANCELLED, disposition=naturally_resolved, target=17efd2ef-9f98-46d4-b41b | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A6  | task 39424 exists and is terminal (or GC'd after resolution for a reso | watcher resolved; source_task=MISSING (GC'd, expected)                                     | exists=True & terminal, OR resolved watcher with G | PASS    |
| A7  | all 4 commissions resolve uniquely; commissioner==parent; worker bindi | M1=abandoned/naturally_resolved; M2=abandoned/naturally_resolved; M3=outstanding/in_scope_ | 4/4 identity matches, state outstanding|resolved   | PASS    |
| A8  | DRIFT GUARD: every PENDING watcher / outstanding commission of the par | pending_for_parent=0 (unknown=0), outstanding_for_parent=2 (unknown=0)                     | unknown=0 and unknown=0                            | PASS    |
| A9  | parent status in {waiting_children, running} (transient self-resolving | status=waiting_children, mid_turn=False, first=waiting_children, re_read=None              | status in {waiting_children, running}              | PASS    |
| A10 | watcher 5602e91d resolves uniquely; target==parent; source_task==39564 | matches=1, state=CANCELLED, disposition=naturally_resolved, target=17efd2ef-9f98-46d4-b41b | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A11 | watcher c83e12a2 resolves uniquely; target==parent; source_task==39564 | matches=1, state=CANCELLED, disposition=naturally_resolved, target=17efd2ef-9f98-46d4-b41b | matches=1, identity matches, state=PENDING|resolve | PASS    |
| A12 | commission 06001f88 resolves uniquely; commissioner==parent; worker==8 | matches=1, state=outstanding, disposition=in_scope_stale, commissioner=17efd2ef-9f98-46d4- | matches=1, identity matches, state=outstanding|res | PASS    |
| A13 | commission a1561f19 resolves uniquely; commissioner==parent; worker==8 | matches=1, state=outstanding, disposition=in_scope_stale, commissioner=17efd2ef-9f98-46d4- | matches=1, identity matches, state=outstanding|res | PASS    |
| A14 | task 39564 (source of W3/W4) exists and is terminal (or GC'd after res | watchers resolved; source_task=MISSING (GC'd, expected)                                    | exists=True & terminal, OR resolved watchers with  | PASS    |
+-----+------------------------------------------------------------------------+--------------------------------------------------------------------------------------------+----------------------------------------------------+---------+
```

## 2. Per-row disposition (evaluated BEFORE any statement)

The leader's partial-resolution boundary: a row the system itself already resolved (`naturally_resolved`) is EXCLUDED from the statements and disclosed here, so the gate is not brittle against lane 7's TTL abandons or a pre-ceremony boot sweep. `in_scope_stale` rows get a block. A CANCELLED row whose `enqueued_at` is still NULL gets one guarded backfill block.

```
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
| label | kind       | id       | state       | disposition        | reason/note                                    |
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
| W1    | watcher    | 8a1f60d7 | CANCELLED   | naturally_resolved |                                                |
| W2    | watcher    | 999efa71 | CANCELLED   | naturally_resolved | needs enqueued_at backfill                     |
| W3    | watcher    | 5602e91d | CANCELLED   | naturally_resolved | needs enqueued_at backfill                     |
| W4    | watcher    | c83e12a2 | CANCELLED   | naturally_resolved | needs enqueued_at backfill                     |
| M1    | commission | 0cdc0b31 | abandoned   | naturally_resolved | worker_terminal_no_report / worker completed   |
| M2    | commission | 90493206 | abandoned   | naturally_resolved | ttl_expired_not_live / worker waiting_children |
| M3    | commission | 06001f88 | outstanding | in_scope_stale     | ttl_expired_not_live / worker waiting_children |
| M4    | commission | a1561f19 | outstanding | in_scope_stale     | ttl_expired_not_live / worker waiting_children |
+-------+------------+----------+-------------+--------------------+------------------------------------------------+
```

## 3. Extended census (reported, NEVER gated)

Every PENDING watcher targeting the parent, and every anchor-scoped outstanding commission. Rows marked `in_target_set` are inside the 8-row scope; any row marked `UNKNOWN` would have refused the emit (drift guard).

```
+------------+----------+-------------+----------------+-------------+---------------+
| kind       | id       | task/worker | child/lane     | state       | scope         |
+------------+----------+-------------+----------------+-------------+---------------+
| commission | 06001f88 | 82551f59    | instance_nudge | outstanding | in_target_set |
| commission | a1561f19 | 82551f59    | instance_nudge | outstanding | in_target_set |
+------------+----------+-------------+----------------+-------------+---------------+
```

Park predicate value at emit time: `count_pending_for_target(parent)` = **0**; `count_outstanding_commissions(parent)` = **2**.

## 4. Statements (EMITTED ONLY — never executed)

### Block 1 (W2-backfill, backfill) — dependency_watchers `999efa71-8ce3-4fd5-b19e-1627e0018205`

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
    WHERE watch_id = '999efa71-8ce3-4fd5-b19e-1627e0018205'
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

### Block 2 (W3-backfill, backfill) — dependency_watchers `5602e91d-5d4c-4fb6-9e2b-8f351d203f52`

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
    -- repair block: W3 backfill enqueued_at 5602e91d
    UPDATE dependency_watchers
    SET enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = '5602e91d-5d4c-4fb6-9e2b-8f351d203f52'
      AND state = 'CANCELLED'
      AND enqueued_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W3 backfill enqueued_at 5602e91d: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W3 backfill enqueued_at 5602e91d: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W3 backfill enqueued_at 5602e91d: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 3 (W4-backfill, backfill) — dependency_watchers `c83e12a2-83b8-46ce-9213-f5a24a5060f6`

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
    -- repair block: W4 backfill enqueued_at c83e12a2
    UPDATE dependency_watchers
    SET enqueued_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    WHERE watch_id = 'c83e12a2-83b8-46ce-9213-f5a24a5060f6'
      AND state = 'CANCELLED'
      AND enqueued_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 1 THEN
        RAISE NOTICE 'W4 backfill enqueued_at c83e12a2: repaired 1 row';
    ELSIF n = 0 THEN
        RAISE NOTICE 'W4 backfill enqueued_at c83e12a2: no-op (already repaired or state moved)';
    ELSE
        RAISE EXCEPTION 'W4 backfill enqueued_at c83e12a2: EXPECTED 1 ROW, GOT % — aborting', n;
    END IF;
END $$;
```

### Block 4 (M3, repair) — mission_commissions `06001f88-9e00-4720-8e59-88959d8faa75`

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

### Block 5 (M4, repair) — mission_commissions `a1561f19-2338-4053-9b7d-ea85c9d92613`

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
    "commissions": 4979,
    "watchers": 12373
  },
  "global_sha256": "d4d4dd75a79ea8ca622671c3519c3c6dfac39a46d4419b032bd0ec5d2378302e",
  "parent_keys": {
    "agent_id": "ari",
    "created_at": "2026-11-14T21:54:59.848480+00:00",
    "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13",
    "parent_id": null,
    "status": "waiting_children",
    "updated_at": "2026-11-18T18:32:06.391418+00:00",
    "version": 511
  },
  "parent_scoped_counts": {
    "commissions": 3,
    "watchers": 0
  },
  "parent_scoped_sha256": "8f773cf687fbce38be96bb7e1cdc92e6040667aabf6f4477d583896f39efb85b"
}
```

**Parent-scoped rows at emit time** (the exact set `--verify` re-diffs):

```

c:03ff6858-b67b-52e8-a51c-97db43a098e5	commission_id='03ff6858-b67b-52e8-a51c-97db43a098e5'	mission_id='cb904c16-a982-41d4-9d33-2a04c7c2d6c0'	commissioner_instance_id='17efd2ef-9f98-46d4-b41b-60e3e32b2d13'	worker_instance_id='17efd2ef-9f98-46d4-b41b-60e3e32b2d13'	lane='instance_nudge'	state='outstanding'	created_at='2026-11-18T04:11:04.854913+00:00'	satisfied_at=None	abandon_reason=None	resolved_at=None	advisory='true'
c:487f36d0-d846-5e45-bec5-2131187dbd71	commission_id='487f36d0-d846-5e45-bec5-2131187dbd71'	mission_id='cb904c16-a982-41d4-9d33-2a04c7c2d6c0'	commissioner_instance_id='17efd2ef-9f98-46d4-b41b-60e3e32b2d13'	worker_instance_id='82551f59-4603-4b88-bbce-c9bccfa6fcd5'	lane='instance_nudge'	state='outstanding'	created_at='2026-11-18T04:11:04.947950+00:00'	satisfied_at=None	abandon_reason=None	resolved_at=None	advisory='true'
c:eaceb2d3-928d-5b85-8bde-ae358d5ad352	commission_id='eaceb2d3-928d-5b85-8bde-ae358d5ad352'	mission_id='cb904c16-a982-41d4-9d33-2a04c7c2d6c0'	commissioner_instance_id='17efd2ef-9f98-46d4-b41b-60e3e32b2d13'	worker_instance_id='82551f59-4603-4b88-bbce-c9bccfa6fcd5'	lane='instance_nudge'	state='outstanding'	created_at='2026-11-18T04:11:04.904968+00:00'	satisfied_at=None	abandon_reason=None	resolved_at=None	advisory='true'
```

**Parent row keys at emit time:** `{"agent_id": "ari", "created_at": "2026-11-14T21:54:59.848480+00:00", "instance_id": "17efd2ef-9f98-46d4-b41b-60e3e32b2d13", "parent_id": null, "status": "waiting_children", "updated_at": "2026-11-18T18:32:06.391418+00:00", "version": 511}`

## 6. Amendment 2026-11-18 (post-natural-resolution)

**Parent posture at emit:** `first=`waiting_children`, re_read=`None`, final=`waiting_children`, mid_turn=False.

**Drift-guard expected population (derived from the target set's dispositions):** PENDING watchers = 0, outstanding commissions = 2.

**Backfill eligibility** mirrors the re-arm path's terminal-child skip (instance_lifecycle.py:4229-4239; TERMINAL_STATUSES from job_queue_service.py:126-131 = {terminated, completed, error, failed}): a CANCELLED watcher whose child is TERMINAL is skip-protected (no backfill); one whose child is NON-TERMINAL is re-arm eligible and is stamped. Missing child row -> skipped.

**Receipt note (verbatim):** the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot sweep) before this repair; the remaining hold was the mission leg's two commissions. The Emperor's authorization is recorded on board item wi-8f60e6f1.

## 7. Authority statement

**Execution is the daemon's gated repair path (3-factor: user attestation + audit stamp + dry-run), not this script.** The executor is `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py:939`), reachable only from an agent holding the privileged `ens-db` category (today: `maintenancer`). Its three gates are: kill-switch `ENSEMBLE_REPAIR_ENABLED`, input validation (idempotency + migration-runner trap + self-surgery refusal), and a single-use confirm nonce that only a DRY RUN mints.

**Rollback is NOT free.** See `RUNBOOK.md` §9: reverting these rows means re-inserting a PENDING watcher, which RE-WEDGES the parent.
