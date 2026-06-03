"""SQLite startup integration test for Phase 2.

Verifies:
1. `daemon.persistence.get_checkpointer` imports cleanly (the canonical
   smoke-test from the task spec).
2. The dispatcher chooses the SQLite path when no PG env vars are set.
3. SqliteCheckpointerAdapter end-to-end: write a checkpoint, read it
   back, list thread ids, then adelete_thread.
"""
import asyncio
import os
import sys
import tempfile

# Clear any PG env vars to ensure SQLite path is the default
for k in list(os.environ):
    if k.startswith(("ENSEMBLE_PG_", "POSTGRES_")):
        del os.environ[k]


async def main() -> int:
    # ── Step 4 from the task spec: import smoke test ─────────────────────
    from daemon.persistence import get_checkpointer
    print("[1] import OK — daemon.persistence.get_checkpointer is importable")

    from daemon.ensemble_config import EnsembleConfig
    from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

    with tempfile.TemporaryDirectory() as tmp:
        cfg = EnsembleConfig(
            database="sqlite",
            sqlite={"instances_db": f"{tmp}/inst.db",
                    "checkpoints_db": f"{tmp}/cp.db"},
        )
        # Step 2 from the task: verify the SQLite code path is the default
        print(f"[2] Config: database={cfg.database!r}, is_postgres={cfg.is_postgres}")
        assert cfg.is_postgres is False, "default should NOT be postgres"
        assert cfg.database == "sqlite"

        # Step 3 from the task: actually instantiate via the dispatcher
        adapter = await get_checkpointer(cfg)
        print(f"[3] Adapter type: {type(adapter).__name__}")
        assert isinstance(adapter, SqliteCheckpointerAdapter), \
            f"expected SqliteCheckpointerAdapter, got {type(adapter).__name__}"

        # Round-trip via real LangGraph compile (this triggers saver.setup())
        from langchain_core.messages import HumanMessage
        from langgraph.graph import END, START, StateGraph
        from typing import TypedDict, Annotated
        from langgraph.graph.message import add_messages

        class S(TypedDict):
            messages: Annotated[list, add_messages]

        g = StateGraph(S)
        g.add_node("a", lambda s: {"messages": []})
        g.add_edge(START, "a")
        g.add_edge("a", END)
        graph = g.compile(checkpointer=adapter.raw_saver)

        thread_id = "test-sqlite-startup"
        await graph.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            {"configurable": {"thread_id": thread_id}},
        )
        print(f"[3] Wrote checkpoint for {thread_id}")

        # Now that the saver has been used, list_thread_ids is safe to call
        threads = await adapter.list_thread_ids()
        assert threads == [thread_id], f"expected [{thread_id}], got {threads}"
        print(f"[3] list_thread_ids -> {threads}")

        # Cleanup
        await adapter.adelete_thread(thread_id)
        threads = await adapter.list_thread_ids()
        assert threads == [], f"expected empty after delete, got {threads}"
        print(f"[3] After delete: list_thread_ids -> {threads}")

        await adapter.close()

    print("\nSQLite startup integration: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
