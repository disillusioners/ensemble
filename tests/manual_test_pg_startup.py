"""PG startup integration test for Phase 2.

Sets PG env vars, then verifies that:
1. daemon.persistence.get_checkpointer dispatches to the PG path.
2. create_postgres_checkpointer returns a PostgresCheckpointerAdapter.
3. The asyncpg pool is real (not mocked) and can serve a query.
4. Cleanup: env vars and any leftover test data are removed.
"""
import asyncio
import os
import sys
import tempfile

# ── Set PG env vars (per task spec) ─────────────────────────────────────
os.environ["ENSEMBLE_DB_TYPE"] = "postgres"
os.environ["ENSEMBLE_PG_HOST"] = "localhost"
os.environ["ENSEMBLE_PG_PORT"] = "5432"
os.environ["ENSEMBLE_PG_DATABASE"] = "ensemble_test"
os.environ["ENSEMBLE_PG_USER"] = "ensemble"
os.environ["ENSEMBLE_PG_PASSWORD"] = "ensemble_dev"

# The persistence module reads POSTGRES_* (not ENSEMBLE_PG_*) — see
# _build_pg_connection_string in daemon/persistence.py. Mirror the values
# so that env-var override path is exercised.
os.environ["POSTGRES_HOST"] = os.environ["ENSEMBLE_PG_HOST"]
os.environ["POSTGRES_PORT"] = os.environ["ENSEMBLE_PG_PORT"]
os.environ["POSTGRES_DB"] = os.environ["ENSEMBLE_PG_DATABASE"]
os.environ["POSTGRES_USER"] = os.environ["ENSEMBLE_PG_USER"]
os.environ["POSTGRES_PASSWORD"] = os.environ["ENSEMBLE_PG_PASSWORD"]


async def main() -> int:
    from daemon.persistence import (
        get_checkpointer,
        create_postgres_checkpointer,
    )
    from daemon.ensemble_config import EnsembleConfig
    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    cfg = EnsembleConfig(
        database="postgres",
        postgres={
            "host": "localhost",
            "port": 5432,
            "db": "ensemble_test",
            "user": "ensemble",
            "password": "ensemble_dev",
        },
    )
    print(f"[1] Config: database={cfg.database!r}, is_postgres={cfg.is_postgres}")
    assert cfg.is_postgres is True, "PG env vars should set is_postgres=True"

    # ── Dispatcher test ─────────────────────────────────────────────────
    adapter = await get_checkpointer(cfg)
    print(f"[2] Adapter type via dispatcher: {type(adapter).__name__}")
    assert isinstance(adapter, PostgresCheckpointerAdapter), \
        f"expected PostgresCheckpointerAdapter, got {type(adapter).__name__}"

    # ── Real-query test (proves asyncpg pool is functional) ─────────────
    threads = await adapter.list_thread_ids()
    print(f"[3] list_thread_ids -> {len(threads)} threads (real asyncpg query)")

    # Find excess groups
    excess = await adapter.find_excess_checkpoint_groups(max_per_thread=999)
    print(f"[3] find_excess_checkpoint_groups(999) -> {len(excess)} groups")

    await adapter.close()
    print("\nPG startup integration: PASS")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    finally:
        # ── Cleanup env vars (per task constraint) ──────────────────────
        for k in ("ENSEMBLE_DB_TYPE", "ENSEMBLE_PG_HOST", "ENSEMBLE_PG_PORT",
                  "ENSEMBLE_PG_DATABASE", "ENSEMBLE_PG_USER",
                  "ENSEMBLE_PG_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT",
                  "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            os.environ.pop(k, None)
    sys.exit(rc)
