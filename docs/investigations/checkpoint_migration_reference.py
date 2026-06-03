"""Test 4: Full database migration at scale.

Migrates ALL 2061 checkpoints and 2971 writes from SQLite to PG.
Validates counts match exactly.
Times the operation.
"""
import asyncio
import json
import sqlite3
import time

import asyncpg
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


async def migrate_all(sqlite_path: str, pg_conn: asyncpg.Connection, batch_size: int = 100) -> dict:
    """Migrate entire SQLite database to PG with batching.
    
    Returns: {checkpoints, blobs, writes, durations}
    """
    serde = JsonPlusSerializer()
    counts = {"checkpoints": 0, "blobs": 0, "writes": 0, "skipped_blobs": 0}
    
    sconn = sqlite3.connect(sqlite_path)
    
    # Process all threads
    thread_rows = sconn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
    print(f"Total threads to migrate: {len(thread_rows)}")
    
    t_start = time.time()
    
    for (thread_id,) in thread_rows:
        # Get all checkpoints for this thread
        scur = sconn.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        rows = scur.fetchall()
        
        # Prepare batch insert
        ckpt_records = []
        blob_records = []
        for row in rows:
            (t_thread_id, t_ns, t_cid, t_parent_cid, t_type, t_blob, t_meta) = row
            decoded = serde.loads_typed((t_type, t_blob))
            meta_dict = json.loads(t_meta) if t_meta else {}
            
            # Build JSONB-safe checkpoint (omit non-primitive channel values, not set True)
            checkpoint_for_jsonb = {}
            for k, v in decoded.items():
                if k == "channel_values":
                    cv_for_jsonb = {}
                    for ch_name, ch_value in v.items():
                        if ch_value is None or isinstance(ch_value, (str, int, float, bool)):
                            cv_for_jsonb[ch_name] = ch_value
                        # else: omitted (will be in checkpoint_blobs)
                    checkpoint_for_jsonb[k] = cv_for_jsonb
                else:
                    checkpoint_for_jsonb[k] = v
            
            ckpt_records.append((
                t_thread_id, t_ns, t_cid, t_parent_cid,
                json.dumps(checkpoint_for_jsonb, default=str),
                json.dumps(meta_dict, default=str),
            ))
            
            # Prepare blob records for non-primitive channel values
            channel_values = decoded.get("channel_values", {})
            channel_versions = decoded.get("channel_versions", {})
            for ch_name, ch_value in channel_values.items():
                if ch_value is None or isinstance(ch_value, (str, int, float, bool)):
                    continue
                version = channel_versions.get(ch_name, "")
                try:
                    blob_type, blob_bytes = serde.dumps_typed(ch_value)
                    blob_records.append((
                        t_thread_id, t_ns, ch_name, version, blob_type, blob_bytes,
                    ))
                except Exception as e:
                    counts["skipped_blobs"] += 1
        
        # Batch insert checkpoints
        if ckpt_records:
            await pg_conn.executemany(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb) "
                "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET "
                "  checkpoint = EXCLUDED.checkpoint, metadata = EXCLUDED.metadata",
                ckpt_records,
            )
            counts["checkpoints"] += len(ckpt_records)
        
        # Batch insert blobs
        if blob_records:
            await pg_conn.executemany(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO NOTHING",
                blob_records,
            )
            counts["blobs"] += len(blob_records)
    
    # Migrate writes
    wcur = sconn.execute("SELECT thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value FROM writes")
    write_rows = wcur.fetchall()
    
    # Batch in groups
    for i in range(0, len(write_rows), batch_size):
        batch = write_rows[i:i+batch_size]
        records = []
        for r in batch:
            (t_thread_id, t_ns, t_cid, t_task_id, t_idx, t_channel, t_type, t_value) = r
            records.append((t_thread_id, t_ns, t_cid, t_task_id, "", t_idx, t_channel, t_type, t_value))
        await pg_conn.executemany(
            "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE SET "
            "  channel = EXCLUDED.channel, type = EXCLUDED.type, blob = EXCLUDED.blob",
            records,
        )
        counts["writes"] += len(records)
    
    sconn.close()
    counts["duration_s"] = round(time.time() - t_start, 2)
    return counts


async def main():
    PG_DSN = "postgresql://ensemble:ensemble_dev@localhost:5432/ensemble_test"
    
    # First, clean PG tables
    pg = await asyncpg.connect(PG_DSN)
    await pg.execute("TRUNCATE TABLE checkpoint_writes, checkpoint_blobs, checkpoints CASCADE")
    
    # Get SQLite counts
    sconn = sqlite3.connect("data_dev/checkpoints.db")
    s_ckpt = sconn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    s_writes = sconn.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    s_threads = sconn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
    sconn.close()
    print(f"SQLite: {s_ckpt} checkpoints, {s_writes} writes, {s_threads} threads")
    
    # Migrate
    print("\n=== Migrating entire DB ===")
    counts = await migrate_all("data_dev/checkpoints.db", pg)
    print(f"Migration result: {counts}")
    
    # Verify counts in PG
    pg_ckpt = await pg.fetchval("SELECT COUNT(*) FROM checkpoints")
    pg_writes = await pg.fetchval("SELECT COUNT(*) FROM checkpoint_writes")
    pg_blobs = await pg.fetchval("SELECT COUNT(*) FROM checkpoint_blobs")
    pg_threads = await pg.fetchval("SELECT COUNT(DISTINCT thread_id) FROM checkpoints")
    print(f"\nPG: {pg_ckpt} checkpoints, {pg_writes} writes, {pg_blobs} blobs, {pg_threads} threads")
    
    # Check parity
    assert pg_ckpt == s_ckpt, f"Checkpoint count mismatch: PG={pg_ckpt}, SQLite={s_ckpt}"
    assert pg_writes == s_writes, f"Writes count mismatch: PG={pg_writes}, SQLite={s_writes}"
    assert pg_threads == s_threads, f"Thread count mismatch"
    print("\n✓ Counts match exactly")
    
    # Test reading back the largest thread
    sconn = sqlite3.connect("data_dev/checkpoints.db")
    big_thread = sconn.execute("""
        SELECT thread_id, COUNT(*) as c FROM checkpoints GROUP BY thread_id ORDER BY c DESC LIMIT 1
    """).fetchone()
    sconn.close()
    big_thread_id = big_thread[0]
    print(f"\n=== Reading back largest thread: {big_thread_id} ({big_thread[1]} checkpoints) ===")
    
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async with AsyncPostgresSaver.from_conn_string(PG_DSN) as saver:
        config = {"configurable": {"thread_id": big_thread_id, "checkpoint_ns": ""}}
        latest = await saver.aget_tuple(config)
        if latest:
            print(f"  Latest checkpoint: {latest.config['configurable']['checkpoint_id'][:12]}...")
            print(f"  Channel values: {list((latest.checkpoint.get('channel_values') or {}).keys())}")
            print(f"  Has {len(latest.pending_writes or [])} pending writes")
            # Check the messages
            cv = latest.checkpoint.get("channel_values", {})
            if "messages" in cv:
                msgs = cv["messages"]
                print(f"  Message count: {len(msgs) if isinstance(msgs, list) else 'N/A'}")
                if isinstance(msgs, list) and msgs:
                    print(f"  First msg type: {type(msgs[0]).__name__}")
                    print(f"  First msg preview: {repr(msgs[0])[:200]}")
        else:
            print("✗ No checkpoint found")
    
    await pg.close()


asyncio.run(main())
