# D-1 Prod `channel_versions` JSONB Shape Verification — DEFER-WITH-SIGNOFF

> Date: 2026-09-04 (UTC) | v2 HEAD: `41347ee4`
> Branch: `feature/langgraph-checkpoint-perf-v2`
> DSN discipline: no writes attempted. No write to `ensemble_prod` was attempted — the deferred-with-signoff disposition applies.

## Status

**DEFERRED WITH SIGN-OFF REQUEST.** Operator action required before destructive-enable (`CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`) flip.

Per FR-11 AC-11.2, this disposition is an ACCEPTED outcome per the brief:
"if ensemble_prod is unreachable or operator sign-off is required and unavailable, write the explicit defer-with-signoff entry in that file instead".

## Reason for deferral

The Phase 5 Stage-2 implementation environment does NOT have access to the `ensemble_prod` PostgreSQL cluster:
- `psql -h ensemble_prod_pg_host` returns "could not translate host name ... to address"
- No operator-provided read-only credentials available
- The Phase 5 brief is explicit: this is the ONLY sanctioned prod touch. Without operator-provided credentials, attempting a production query is forbidden.

## What this defers

Per `docs/runbooks/checkpoint-blob-prune-restore.md` section 2, the query that would be run:

```sql
SELECT thread_id, count(*) AS checkpoint_count FROM checkpoints
GROUP BY thread_id ORDER BY checkpoint_count DESC LIMIT 1;

SELECT jsonb_pretty(checkpoint->'channel_versions') FROM checkpoints
WHERE thread_id = 'representative' ORDER BY checkpoint_id DESC LIMIT 5;

SELECT count(*) FROM checkpoints ck
JOIN LATERAL jsonb_each_text(ck.checkpoint->'channel_versions') je ON true
JOIN checkpoint_blobs bl ON bl.thread_id = ck.thread_id AND bl.checkpoint_ns = ck.checkpoint_ns
  AND bl.channel = je.key AND bl.version = je.value
WHERE ck.thread_id = 'representative';
```

## What does NOT depend on this deferral

- The destructive-enable FLAG (`CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`) is NOT flipped in v2.
- The v2 merge to `latest` does NOT require this deferral to be resolved.
- The flag stays at its current value (default OFF per the runbook section 1).

## Sign-off line (to be filled by operator)

```
[ ] Operator confirms structural-unreachability claim via either:
    (a) production channel_versions query result pasted below, or
    (b) review of the binding-gate ZERO_REFS_FAIL_SAFE test as a sufficient proxy.

Operator name:
Operator timestamp:
Operator signature:
```

## Test artifacts already on v2 (binding-gate)

For reference, the binding-gate test `tests/integration/checkpoint_prune_real_saver.py::TestRealSaverFailSafe::test_real_saver_zero_refs_skip_logs_error_and_deletes_nothing` exercises the ZERO_REFS_FAIL_SAFE on real PG 14.22 (disposable). This test passed GREEN during the Phase 5 Stage-1 binding-gate run (9/9 GREEN at HEAD `41347ee4`). The test verifies that when `channel_versions` is empty / contains 0 refs, the destructive path is bypassed and a WARNING is logged. This is the SAME failure mode that the prod verification would catch.

## Disposition

- Status: DEFERRED WITH SIGN-OFF REQUEST.
- Trigger for revisit: operator has access to ensemble_prod.
- Reviewed-acceptable outcome: operator accepts the binding-gate proxy.
- No code change: this file is documentation-only.
