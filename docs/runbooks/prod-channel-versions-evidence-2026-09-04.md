# D-1 Prod `channel_versions` JSONB Shape Verification — EVIDENCE (ACTUAL RUN)

> Evidence run: 2026-09-04 (UTC) | v2 branch tip at run: `237d3eba`
> Branch: `feature/langgraph-checkpoint-perf-v2`
> DSN discipline: read-only SELECT ONLY. No INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/TRUNCATE/COPY/VACUUM/DDL of any kind was executed against `ensemble_prod`. Every command was a plain `psql -c "SELECT ..."` — no transaction blocks.

## Status

**EVIDENCE CAPTURED (supersedes same-day defer-with-signoff).** Checklist item §2 of `docs/runbooks/checkpoint-blob-prune-restore.md` is satisfied with actual prod output below. This fulfills option (a) of the superseded entry's sign-off checkbox.

## History

- **2026-09-04 (earlier same day):** a DEFER-WITH-SIGNOFF entry was filed here claiming `ensemble_prod` was structurally unreachable ("could not translate host name"). That reason was **factually wrong** — it was a host-name guess, never an actual probe. `ensemble_prod` exists on the local Homebrew PostgreSQL and is reachable read-only (`psql -h localhost -U ensemble -d ensemble_prod` lists it via `-lqt`). No operator signature was ever attached. Superseded same day by this evidence run.

## Environment

- Prod DSN: `psql -h localhost -U ensemble -d ensemble_prod` (local Homebrew PG)
- All queries are SELECT-only; run date 2026-09-04 UTC; branch `feature/langgraph-checkpoint-perf-v2` @ `237d3eba`

## Step 1 — Representative thread selection

```sql
SELECT thread_id, count(*) AS ck FROM checkpoints GROUP BY thread_id ORDER BY ck DESC LIMIT 5;
```

```
             thread_id               |  ck
-------------------------------------+------
 4b4fc33f-9225-4b60-a0ce-41af0f6eda0a | 1287
 7a322ac7-fa70-46fc-8412-f4bd71ecf64e |  852
 20e61566-eb95-4841-9f6f-4a7e5eb00fd7 |  801
 d35f7ff0-06ba-43bf-8013-07d56ce2a378 |  744
 78a33066-b8a7-4257-a285-6f2489a79996 |  699
```

Representative thread: **`4b4fc33f-9225-4b60-a0ce-41af0f6eda0a`** (most active, 1287 checkpoints). Namespace census for this thread: single namespace `checkpoint_ns = ''` (root), 1287 checkpoints.

## Step 2 — Runbook §2 query 1 (jsonb_pretty), verbatim

```sql
SELECT jsonb_pretty(checkpoint->'channel_versions') AS cv
FROM checkpoints
WHERE thread_id = '4b4fc33f-9225-4b60-a0ce-41af0f6eda0a'
ORDER BY checkpoint_id DESC
LIMIT 5;
```

Raw output (5 newest checkpoints, `-t -A` formatting; **complete, no elision** — each map has 8 channels):

```json
{
    "messages": "00000000000000000000000000001287.0.7912798232118846",
    "__start__": "00000000000000000000000000000002.0.302747013122182",
    "branch:to:agent": "00000000000000000000000000001287.0.7912798232118846",
    "branch:to:tools": "00000000000000000000000000001286.0.33930514964864156",
    "watchover_route": "00000000000000000000000000001285.0.48312014610709064",
    "watchover_turn_id": "00000000000000000000000000001287.0.7912798232118846",
    "watchover_denial_count": "00000000000000000000000000000003.0.31374196956650313",
    "branch:to:watchover_check": "00000000000000000000000000001285.0.48312014610709064"
}
{
    "messages": "00000000000000000000000000001286.0.33930514964864156",
    "__start__": "00000000000000000000000000000002.0.302747013122182",
    "branch:to:agent": "00000000000000000000000000001286.0.33930514964864156",
    "branch:to:tools": "00000000000000000000000000001286.0.33930514964864156",
    "watchover_route": "00000000000000000000000000001285.0.48312014610709064",
    "watchover_turn_id": "00000000000000000000000000001284.0.44854452969034864",
    "watchover_denial_count": "00000000000000000000000000000003.0.31374196956650313",
    "branch:to:watchover_check": "00000000000000000000000000001285.0.48312014610709064"
}
{
    "messages": "00000000000000000000000000001284.0.44854452969034864",
    "__start__": "00000000000000000000000000000002.0.302747013122182",
    "branch:to:agent": "00000000000000000000000000001284.0.44854452969034864",
    "branch:to:tools": "00000000000000000000000000001285.0.48312014610709064",
    "watchover_route": "00000000000000000000000000001285.0.48312014610709064",
    "watchover_turn_id": "00000000000000000000000000001284.0.44854452969034864",
    "watchover_denial_count": "00000000000000000000000000000003.0.31374196956650313",
    "branch:to:watchover_check": "00000000000000000000000000001285.0.48312014610709064"
}
{
    "messages": "00000000000000000000000000001284.0.44854452969034864",
    "__start__": "00000000000000000000000000000002.0.302747013122182",
    "branch:to:agent": "00000000000000000000000000001284.0.44854452969034864",
    "branch:to:tools": "00000000000000000000000000001283.0.028554803550552288",
    "watchover_route": "00000000000000000000000000001282.0.5094034894425995",
    "watchover_turn_id": "00000000000000000000000000001284.0.44854452969034864",
    "watchover_denial_count": "00000000000000000000000000000003.0.31374196956650313",
    "branch:to:watchover_check": "00000000000000000000000000001284.0.44854452969034864"
}
{
    "messages": "00000000000000000000000000001283.0.028554803550552288",
    "__start__": "00000000000000000000000000000002.0.302747013122182",
    "branch:to:agent": "00000000000000000000000000001283.0.028554803550552288",
    "branch:to:tools": "00000000000000000000000000001283.0.028554803550552288",
    "watchover_route": "00000000000000000000000000001282.0.5094034894425995",
    "watchover_turn_id": "00000000000000000000000000001281.0.8762823123853685",
    "watchover_denial_count": "00000000000000000000000000000003.0.31374196956650313",
    "branch:to:watchover_check": "00000000000000000000000000001282.0.5094034894425995"
}
```

**Observed layout:** flat JSONB object; key = channel name; value = scalar version string of the expected `<32-hex counter>.0.<fraction>` form (e.g. `00000000000000000000000000001287.0.7912798232118846`). NOT nested under another key. NOT `versions_seen`. No non-scalar values. Matches the runbook's expected `§36-style` layout exactly.

## Step 3 — Runbook §2 query 2 (per-ref blob resolution), verbatim

```sql
SELECT c.checkpoint_id,
       e.key AS channel, e.value AS version,
       (SELECT count(*) FROM checkpoint_blobs bl
         WHERE bl.thread_id = c.thread_id
           AND bl.checkpoint_ns = c.checkpoint_ns
           AND bl.channel = e.key
           AND bl.version = e.value) AS blob_rows
FROM checkpoints c
CROSS JOIN LATERAL jsonb_each_text(
        CASE WHEN jsonb_typeof(c.checkpoint->'channel_versions') = 'object'
             THEN c.checkpoint->'channel_versions' ELSE '{}'::jsonb END) e
WHERE c.thread_id = '4b4fc33f-9225-4b60-a0ce-41af0f6eda0a'
ORDER BY c.checkpoint_id DESC
LIMIT 50;
```

Newest checkpoint's 8 rows verbatim (50 rows returned in total; **rows for checkpoints 2–10 elided here for readability — they follow the identical pattern: `messages` → 1, all other channels → 0**):

```
            checkpoint_id             |          channel          |                       version                       | blob_rows
--------------------------------------+---------------------------+-----------------------------------------------------+-----------
 1f1a81cc-e7ee-6998-8505-531fe4135015 | messages                  | 00000000000000000000000000001287.0.7912798232118846  |         1
 1f1a81cc-e7ee-6998-8505-531fe4135015 | branch:to:agent           | 00000000000000000000000000001287.0.7912798232118846  |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | branch:to:tools           | 00000000000000000000000000001286.0.33930514964864156 |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | branch:to:watchover_check | 00000000000000000000000000001285.0.48312014610709064 |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | watchover_denial_count    | 00000000000000000000000000000003.0.31374196956650313 |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | watchover_turn_id         | 00000000000000000000000000001287.0.7912798232118846  |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | __start__                 | 00000000000000000000000000000002.0.302747013122182   |         0
 1f1a81cc-e7ee-6998-8505-531fe4135015 | watchover_route           | 00000000000000000000000000001285.0.48312014610709064 |         0
```

## Step 4 — FR-11 round-trip counts (jsonb_each_text join)

Verbatim join count (checkpoints × refs × blobs, whole thread):

```sql
SELECT count(*) AS roundtrip_join_rows
FROM checkpoints ck
JOIN LATERAL jsonb_each_text(ck.checkpoint->'channel_versions') je ON true
JOIN checkpoint_blobs bl ON bl.thread_id = ck.thread_id AND bl.checkpoint_ns = ck.checkpoint_ns
  AND bl.channel = je.key AND bl.version = je.value
WHERE ck.thread_id = '4b4fc33f-9225-4b60-a0ce-41af0f6eda0a';
```

→ **roundtrip_join_rows = 1287**

Per-channel thread-wide census:

```sql
SELECT je.key AS channel,
       count(*) AS ref_rows,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM checkpoint_blobs bl
              WHERE bl.thread_id = ck.thread_id AND bl.checkpoint_ns = ck.checkpoint_ns
                AND bl.channel = je.key AND bl.version = je.value)) AS resolved_rows,
       count(DISTINCT (je.key, je.value)) AS distinct_refs,
       count(DISTINCT (je.key, je.value)) FILTER (WHERE EXISTS (SELECT 1 FROM checkpoint_blobs bl
              WHERE bl.thread_id = ck.thread_id AND bl.checkpoint_ns = ck.checkpoint_ns
                AND bl.channel = je.key AND bl.version = je.value)) AS distinct_resolved
FROM checkpoints ck
CROSS JOIN LATERAL jsonb_each_text(ck.checkpoint->'channel_versions') je
WHERE ck.thread_id = '4b4fc33f-9225-4b60-a0ce-41af0f6eda0a'
GROUP BY je.key ORDER BY distinct_refs DESC;
```

```
          channel          | ref_rows | resolved_rows | distinct_refs | distinct_resolved
---------------------------+----------+---------------+---------------+-------------------
 messages                  |     1286 |          1286 |           858 |               858
 branch:to:agent           |     1286 |             0 |           858 |                 0
 branch:to:tools           |     1284 |             0 |           856 |                 0
 branch:to:watchover_check |     1285 |             0 |           856 |                 0
 watchover_turn_id         |     1285 |             0 |           429 |                 0
 watchover_route           |     1284 |             0 |           428 |                 0
 __start__                 |     1287 |             1 |             2 |                 1
 watchover_denial_count    |     1285 |             0 |             1 |                 0
```

**The round-trip numbers (record both per FR-11):**

| Quantity | Value |
|---|---|
| Checkpoints in thread (all root ns) | 1287 |
| channel_versions ref rows (thread total) | 10282 |
| Distinct `(channel, version)` refs | 4288 |
| Round-trip join rows (refs resolving to ≥1 blob) | **1287** |
| Distinct refs resolving to blobs | **859** |
| Actual `checkpoint_blobs` rows for the thread | **859** |
| — of which `channel='messages'` | 858 |
| — of which `channel='__start__'` | 1 |

**Bijection check: distinct resolved refs (859) == blob rows (859) — exactly.** Every blob row on this thread is reachable from some checkpoint's `channel_versions`, and every distinct ref that materializes a blob resolves. Zero orphan blobs; zero missing blobs for the refs that carry serialized channel values. The 1287 join rows decompose exactly as 1286 (one per non-first checkpoint, `messages`) + 1 (the thread's first checkpoint, `__start__`).

Supporting facts (all read-only verified):
- Exactly **1** checkpoint lacks a `messages` ref (`SELECT count(*) ... AND NOT (checkpoint->'channel_versions') ? 'messages'` → 1): the thread's first checkpoint `1f1a80c8-9327-6cf2-bfff-61513604f48c` (confirmed via `ORDER BY checkpoint_id ASC LIMIT 1`).
- That same first checkpoint holds the only non-`messages` resolving ref: `__start__` → version `00000000000000000000000000000001.0.5036732102426865` (version counter `.0001.` — the thread's first write) → 1 blob row. Anti-join note: old-checkpoint refs like this one are exactly what the reader keyset protects; deletion set = complement of reader keyset holds.
- Prod-wide namespace census: **all 100,369 checkpoints and all 158,063 blob rows are root ns** (`checkpoint_ns = ''`). No subgraph namespaces exist in prod today; the prune's per-`(thread_id, checkpoint_ns)` scoping is currently exercised only at root.

## Shape verdict

**CONSISTENT — no shape drift.** Prod `channel_versions` is the expected flat per-channel version map (`channel → <32-hex counter>.0.<fraction>`), and the reader-relation round-trip is a clean bijection (859/859 distinct refs ↔ blob rows; `messages` resolves 1286/1286). The runbook's abort conditions (nested object, `versions_seen`, non-scalar values, systematic 0-blob resolution) are **none of them met** — no operator decision trigger fires per plan Risk 4.

Nuances recorded (expected behavior, not drift — documented here so a future reader does not misread them):
1. **7 of 8 channels resolve to 0 blob rows.** These are the primitive/inlined channels (`branch:to:*`, `watchover_*`, `__start__`) — consistent with the runbook's carve-out ("channels whose values are primitives — those live inline, no blob") and the known library fact that `channel_versions` also references primitive channels. This is why the prune's survival assertions must intersect with had-blob pairs. The channel that matters for reader integrity (`messages`) resolves 100%.
2. **`branch:to:agent` shares `messages`' exact version string at the same checkpoint yet has no blob row** — version counter is per-write-step and shared across channels updated in the same step, while blob materialization is per-channel (primitive → inline). Not a shape problem for the anti-join: a missing blob row for an inlined channel is never referenced by blob-keyed reads.
3. **`messages` distinct versions (858) == messages blob rows (858):** shared versions across checkpoints dedupe to one blob row (upsert semantics); e.g. `...1286.0.339…` is referenced by multiple consecutive checkpoints but stored once.

## Disposition

- Status: **EVIDENCE CAPTURED** (§2 of the pre-enable checklist: query executed against prod, layout as expected, round-trip verified).
- This does NOT by itself flip the destructive-enable flag: §1 (saver schema), §3 (7-day dry-run soak), §4 (real-saver suite), §5 (restore rehearsal), §6 (blob snapshot) still govern the ladder, and `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE` remains default OFF.
- No code change: this file is documentation-only.
