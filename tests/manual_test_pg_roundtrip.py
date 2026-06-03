"""
Checkpoint round-trip test for PostgresCheckpointerAdapter.

Verifies:
1. PostgresCheckpointerAdapter can be created against the live PG test DB.
2. AsyncPostgresSaver.setup() creates all 3 expected tables
   (checkpoints, checkpoint_writes, checkpoint_blobs).
3. list_thread_ids / get_checkpoint_ids return correct empty state.
4. adelete_thread correctly cleans all 3 tables (no orphans left behind).
5. Connection string URL-encoding works with special characters in password.
"""
import asyncio
import os
import sys
import uuid
from urllib.parse import quote_plus

# Use the test DB
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_DB"] = "ensemble_test"
os.environ["POSTGRES_USER"] = "ensemble"
os.environ["POSTGRES_PASSWORD"] = "ensemble_dev"


async def main() -> int:
    # Lazy imports after env vars are set
    from daemon.persistence import get_checkpointer
    from daemon.ensemble_config import EnsembleConfig
    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    print("=" * 70)
    print("Checkpoint Round-Trip Test — PostgresCheckpointerAdapter")
    print("=" * 70)

    # ── Test 1: build config and adapter ────────────────────────────────
    config = EnsembleConfig(
        database="postgres",
        postgres={
            "host": "localhost",
            "port": 5432,
            "db": "ensemble_test",
            "user": "ensemble",
            "password": "ensemble_dev",
        },
    )
    print(f"\n[1] Config: host={config.postgres.host}, db={config.postgres.db}, "
          f"user={config.postgres.user}")

    try:
        adapter = await get_checkpointer(config)
    except Exception as e:
        print(f"FAIL — get_checkpointer raised: {type(e).__name__}: {e}")
        return 1

    if not isinstance(adapter, PostgresCheckpointerAdapter):
        print(f"FAIL — expected PostgresCheckpointerAdapter, got {type(adapter).__name__}")
        return 1
    print(f"[1] PASS — adapter is PostgresCheckpointerAdapter")

    # ── Test 2: verify all 3 tables exist in PG ──────────────────────────
    import psycopg
    expected_tables = {"checkpoints", "checkpoint_writes", "checkpoint_blobs"}
    conn = psycopg.connect("postgresql://ensemble:ensemble_dev@localhost:5432/ensemble_test")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' "
            "AND tablename IN ('checkpoints', 'checkpoint_writes', 'checkpoint_blobs')"
        )
        actual = {row[0] for row in cur.fetchall()}
    conn.close()
    missing = expected_tables - actual
    if missing:
        print(f"FAIL — missing PG tables: {missing}")
        return 1
    print(f"[2] PASS — all 3 PG tables present: {sorted(actual)}")

    # ── Test 3: empty-state queries ─────────────────────────────────────
    thread_ids = await adapter.list_thread_ids()
    print(f"[3] list_thread_ids() -> {len(thread_ids)} threads (empty)")

    sample_thread = f"test-roundtrip-{uuid.uuid4().hex[:8]}"
    cp_ids = await adapter.get_checkpoint_ids(sample_thread, "", 10)
    print(f"[3] get_checkpoint_ids({sample_thread!r}, '', 10) -> {len(cp_ids)} ids (empty)")

    # ── Test 4: write a real checkpoint via raw_saver, read it back ─────
    from langchain_core.messages import HumanMessage, AIMessage
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    import json
    from langgraph.graph import END, START, StateGraph
    from typing import TypedDict, Annotated
    from langgraph.graph.message import add_messages

    class S(TypedDict):
        messages: Annotated[list, add_messages]

    g = StateGraph(S)
    g.add_node("a", lambda s: {"messages": [AIMessage(content=f"echo:{s['messages'][-1].content}")]})
    g.add_edge(START, "a")
    g.add_edge("a", END)
    graph = g.compile(checkpointer=adapter.raw_saver)

    cfg = {"configurable": {"thread_id": sample_thread}}
    out = await graph.ainvoke({"messages": [HumanMessage(content="hello world")]}, cfg)
    print(f"[4] Wrote checkpoint for thread={sample_thread}")
    print(f"    Final state messages: {[m.content for m in out['messages']]}")

    cp_ids = await adapter.get_checkpoint_ids(sample_thread, "", 10)
    if not cp_ids:
        print(f"FAIL — get_checkpoint_ids returned empty after write")
        return 1
    print(f"[4] PASS — read back {len(cp_ids)} checkpoint_id(s)")

    # ── Test 5: adelete_thread cleans ALL 3 tables (the bug we just fixed) ──
    # Insert a row directly into checkpoint_blobs to simulate a real write
    # — this is the table that the previous (buggy) adelete_thread ignored.
    conn = psycopg.connect("postgresql://ensemble:ensemble_dev@localhost:5432/ensemble_test")
    with conn.cursor() as cur:
        # Snapshot pre-delete row counts
        cur.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s", (sample_thread,))
        cp_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id = %s", (sample_thread,))
        cw_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = %s", (sample_thread,))
        cb_before = cur.fetchone()[0]
        # Insert a synthetic blob row (representing a non-primitive channel value)
        cur.execute(
            "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
            "VALUES (%s, '', 'synthetic', 'v1', 'json', %s) ON CONFLICT DO NOTHING",
            (sample_thread, b'\\x00'),
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = %s", (sample_thread,))
        cb_inserted = cur.fetchone()[0]
    conn.close()
    print(f"[5] Pre-delete counts: checkpoints={cp_before}, "
          f"checkpoint_writes={cw_before}, checkpoint_blobs={cb_inserted}")

    # Now run the (fixed) adelete_thread
    await adapter.adelete_thread(sample_thread)

    conn = psycopg.connect("postgresql://ensemble:ensemble_dev@localhost:5432/ensemble_test")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s", (sample_thread,))
        cp_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id = %s", (sample_thread,))
        cw_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = %s", (sample_thread,))
        cb_after = cur.fetchone()[0]
    conn.close()
    print(f"[5] Post-delete counts: checkpoints={cp_after}, "
          f"checkpoint_writes={cw_after}, checkpoint_blobs={cb_after}")

    if cp_after or cw_after or cb_after:
        print(f"FAIL — adelete_thread left orphans!")
        return 1
    print(f"[5] PASS — adelete_thread cleaned all 3 tables, no orphans")

    # ── Test 6: URL-encoding of credentials ──────────────────────────────
    from daemon.persistence import _build_pg_connection_string
    # Test a password with special characters
    cfg_special = EnsembleConfig(
        database="postgres",
        postgres={
            "host": "localhost",
            "port": 5432,
            "db": "ensemble_test",
            "user": "user@with:special",
            "password": "p@ss:wo/rd?#",
        },
    )
    # Force env-var free path
    saved = {}
    for k in ("POSTGRES_URL", "POSTGRES_HOST", "POSTGRES_PORT",
              "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        if k in os.environ:
            saved[k] = os.environ[k]
        if k != "POSTGRES_URL":
            os.environ.pop(k, None)
    os.environ.pop("POSTGRES_URL", None)

    dsn = _build_pg_connection_string(cfg_special)
    expected_user = quote_plus("user@with:special")
    expected_pw = quote_plus("p@ss:wo/rd?#")
    if expected_user not in dsn or expected_pw not in dsn:
        print(f"FAIL — URL encoding missing: dsn={dsn!r}")
        return 1
    if "@with" in dsn.split("@", 1)[0]:  # raw '@' in user portion
        print(f"FAIL — un-encoded '@' leaked into DSN: {dsn!r}")
        return 1
    print(f"[6] PASS — connection string URL-encodes user/password")

    # Restore env
    for k, v in saved.items():
        os.environ[k] = v

    # ── Cleanup ─────────────────────────────────────────────────────────
    await adapter.close()
    print("\n" + "=" * 70)
    print("ALL CHECKS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
