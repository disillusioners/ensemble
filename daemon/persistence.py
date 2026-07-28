"""
Persistence layer for LangGraph checkpointing — supports SQLite and PostgreSQL.

Threading Notes (SQLite):
- AsyncSqliteSaver uses aiosqlite which runs SQLite operations in a background thread pool.
- This is safe because aiosqlite manages thread isolation internally.
- The checkpointer operates independently from the main SQLAlchemy session used by repositories.
- No additional synchronization is needed between checkpointing and repository operations since they
  use separate connections.

PostgreSQL Notes:
- For PostgreSQL, the saver uses a long-lived psycopg.AsyncConnection (driven by
  ``langgraph-checkpoint-postgres``). The adapter additionally maintains an asyncpg.Pool
  for the raw SQL operations used by maintenance.py (Phase 2 migration).
- Both imports (psycopg/asyncpg + langgraph.checkpoint.postgres.aio) are LAZY so the
  SQLite path is unaffected when PostgreSQL extras are not installed.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from daemon.checkpoint_adapter import CheckpointerAdapter, SqliteCheckpointerAdapter
from daemon.ensemble_config import EnsembleConfig

logger = logging.getLogger(__name__)


# ── SQLite Path ───────────────────────────────────────────────────────────────


async def _open_sqlite_adapter(db_path: Path) -> CheckpointerAdapter:
    """Async factory for the SQLite-backed CheckpointerAdapter.

    Opens an aiosqlite connection with the same PRAGMAs as the previous
    implementation, wraps it in an ``AsyncSqliteSaver``, then returns a
    ``SqliteCheckpointerAdapter``.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A ``CheckpointerAdapter`` instance backed by SQLite.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Create connection directly - don't use async context manager.
    # This is intentional: the connection must outlive this function for the
    # lifetime of the application, matching the historical pattern.
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA synchronous=NORMAL")
    saver = AsyncSqliteSaver(conn)
    return SqliteCheckpointerAdapter(saver)


# ── PostgreSQL Path ───────────────────────────────────────────────────────────


def _build_pg_connection_string(config: EnsembleConfig) -> str:
    """Build the PostgreSQL connection string used by psycopg/asyncpg.

    Honors, in order of precedence:
    1. ``POSTGRES_URL`` — full DSN, returned as-is
    2. ``POSTGRES_HOST`` / ``POSTGRES_PORT`` / ``POSTGRES_DB`` / ``POSTGRES_USER`` /
       ``POSTGRES_PASSWORD`` — env vars (override file values for credential rotation)
    3. ``config.postgres`` — values loaded from ``ensemble.json``

    Returns a DSN of the form ``postgresql://user:password@host:port/db`` which is
    accepted by both ``psycopg.AsyncConnection.connect`` and ``asyncpg.create_pool``.
    """
    # 1. Full DSN shortcut
    direct = os.environ.get("POSTGRES_URL")
    if direct:
        return direct

    # 2/3. Compose from env vars (overriding file values)
    pg = config.postgres
    host = os.environ.get("POSTGRES_HOST", pg.host)
    port = int(os.environ.get("POSTGRES_PORT", str(pg.port)))
    db = os.environ.get("POSTGRES_DB", pg.db)
    user = os.environ.get("POSTGRES_USER", pg.user)
    password = os.environ.get("POSTGRES_PASSWORD", pg.password)

    # URL-encode credentials so that special characters in user/password
    # (e.g. ``@``, ``:``, ``/``, ``?``, ``#``) cannot break the DSN.
    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"


async def create_postgres_checkpointer(config: EnsembleConfig) -> CheckpointerAdapter:
    """Create a PostgreSQL-backed CheckpointerAdapter.

    This function performs LAZY imports of the optional PostgreSQL dependencies
    (psycopg, asyncpg, and ``langgraph.checkpoint.postgres.aio``). If they are
    not installed, a clear ``ImportError`` is raised instructing the user to
    install the ``postgres`` extras.

    The returned adapter wraps:
    - A long-lived ``psycopg.AsyncConnection`` feeding an ``AsyncPostgresSaver``
      (the LangGraph checkpointer). ``setup()`` is called on the saver to
      create the required tables.
    - An ``asyncpg.Pool`` used by ``PostgresCheckpointerAdapter`` for the
      raw SQL operations required by maintenance.py (operation D, etc.).

    The connection string is built from environment variables (with
    ``POSTGRES_URL`` as a shortcut) falling back to ``config.postgres`` from
    ``ensemble.json``.

    Args:
        config: Ensemble configuration containing ``postgres`` connection
                details. Env vars override file values for credential rotation.

    Returns:
        A ``PostgresCheckpointerAdapter`` ready to be used by LangGraph and
        the maintenance service.

    Raises:
        ImportError: If psycopg/asyncpg/langgraph-checkpoint-postgres are
                     not installed. The error message tells the user to run
                     ``pip install ensemble[postgres]``.
    """
    # ── Lazy imports ───────────────────────────────────────────────────────
    try:
        import asyncpg
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        import psycopg
    except ImportError as e:
        raise ImportError(
            "PostgreSQL checkpoint support requires optional dependencies. "
            "Install postgres support: pip install ensemble[postgres]"
        ) from e

    conn_string = _build_pg_connection_string(config)
    logger.info(
        f"Creating PostgreSQL checkpointer for {config.postgres.host}:"
        f"{config.postgres.port}/{config.postgres.db}"
    )

    # Both the saver (psycopg) and the adapter's asyncpg pool are long-lived
    # resources tied to the application's lifetime. We open them inside a
    # single try/except so that a failure during pool creation also closes
    # the saver connection (otherwise it would leak on startup errors).
    saver_conn = None
    try:
        # ``autocommit=True``: AsyncPostgresSaver manages its own
        # transactions internally (it explicitly begins/commits per
        # operation). Leaving autocommit off would cause psycopg to
        # implicitly start a transaction on the first statement and
        # starve the saver of an open transaction for its writes.
        # ``prepare_threshold=0``: disables psycopg's server-side
        # prepared-statement cache. This is the recommended setting
        # when running behind connection poolers such as PgBouncer
        # in "transaction" mode, where prepared statements cannot be
        # reused across pooled connections. It also matches the
        # upstream ``AsyncPostgresSaver`` recommendation.
        saver_conn = await psycopg.AsyncConnection.connect(
            conn_string,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = AsyncPostgresSaver(conn=saver_conn)
        # ``setup`` is idempotent and must be called before the saver is used.
        # It creates the checkpoint tables and runs any pending migrations.
        await saver.setup()

        # ── Open an asyncpg pool for the adapter's direct SQL operations ───
        # The adapter uses asyncpg for the GROUP BY / DELETE / COUNT operations
        # that maintenance.py needs. asyncpg's connection pool is the most
        # ergonomic way to share connections across the maintenance service.
        pool = await asyncpg.create_pool(
            conn_string,
            min_size=1,
            max_size=5,
        )
    except Exception:
        # If any step in resource setup fails, close the saver connection
        # (if it was opened) and re-raise. The pool, if it was the failing
        # step, cleans itself up automatically on creation failure.
        if saver_conn is not None:
            try:
                await saver_conn.close()
            except Exception:
                pass
        raise

    # Lazy import to avoid pulling PostgresCheckpointerAdapter at module import
    # time on SQLite-only installs (it has no postgres-specific code, but the
    # pattern keeps the SQLite path's import surface minimal).
    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    adapter = PostgresCheckpointerAdapter(saver, pool)
    logger.info(
        f"PostgreSQL checkpointer adapter ready "
        f"(saver=AsyncPostgresSaver, pool=asyncpg.Pool)"
    )
    return adapter


# ── Public Factory ───────────────────────────────────────────────────────────


async def get_checkpointer(config: EnsembleConfig) -> CheckpointerAdapter:
    """Create a checkpointer adapter for the configured database backend.

    Dispatches to the appropriate backend based on ``config.database``:

    - ``"postgres"`` → ``create_postgres_checkpointer`` → returns a
      ``PostgresCheckpointerAdapter`` (lazy-imports psycopg/asyncpg).
    - ``"sqlite"`` (default) → opens an ``AsyncSqliteSaver`` and wraps it
      in a ``SqliteCheckpointerAdapter`` (preserves the historical
      ``PRAGMA busy_timeout=5000`` / ``PRAGMA synchronous=NORMAL`` setup).

    The returned ``CheckpointerAdapter`` exposes the raw saver via the
    ``raw_saver`` property for LangGraph operations (``aget``, ``aput``,
    ``alist``, …) that the adapter does not cover.

    Lifecycle:
    - This is intentionally NOT an async context manager: the returned
      adapter (and its underlying connections) lives for the entire
      application lifetime, just like the pre-migration
      ``AsyncSqliteSaver`` did. Cleanup happens at process shutdown.

    Args:
        config: Loaded ``EnsembleConfig`` selecting the database backend.
                When ``config.is_postgres`` is True the PostgreSQL path is
                used; otherwise the SQLite path is used.

    Returns:
        A ``CheckpointerAdapter`` (concrete type depends on the backend).

    Raises:
        ImportError: If the database is ``"postgres"`` but the optional
                     PostgreSQL dependencies are not installed.
    """
    if config.is_postgres:
        return await create_postgres_checkpointer(config)

    # SQLite path — config.sqlite.checkpoints_db is the file path.
    db_path = Path(config.sqlite.checkpoints_db)
    return await _open_sqlite_adapter(db_path)


# ── Message History ──────────────────────────────────────────────────────────


async def get_instance_messages(
    checkpointer: Any,
    instance_id: str,
    manager: Any | None = None,
) -> list[dict[str, Any]]:
    """Get message history from LangGraph checkpoints.

    Accepts either the raw saver (``AsyncSqliteSaver`` / ``AsyncPostgresSaver``)
    or a ``CheckpointerAdapter``; the function uses ``.raw_saver`` automatically
    when given an adapter, so callers can pass ``self._checkpointer`` regardless
    of which backend is active.

    Args:
        checkpointer: Raw LangGraph checkpointer, or a ``CheckpointerAdapter``
                      wrapping one. Must support ``aget(config)`` and
                      ``alist(config, limit=...)`` (both ``AsyncSqliteSaver``
                      and ``AsyncPostgresSaver`` do).
        instance_id: Instance identifier to retrieve messages for.
        manager: Optional ``InstanceManager`` reference. When provided, the
                 function attempts to reconstruct the agent's system prompt
                 (which is **not** persisted to the LangGraph checkpoint) and
                 inject it as a synthetic ``role="system"`` message at the
                 start of the returned list. This is needed by the frontend
                 "View system message" toggle. Any failure is swallowed and
                 the function returns the original list — the synthetic
                 message is best-effort, never load-bearing.

    Returns:
        List of message dictionaries with role, content, thinking, tool_calls.
        When ``manager`` is provided and the system prompt can be
        reconstructed, a synthetic ``role="system"`` message is prepended.
    """
    from typing import cast
    from langgraph.checkpoint.memory import CheckpointTuple

    from daemon.utils import serialize_message

    # Accept either a raw saver or a CheckpointerAdapter.
    saver = checkpointer.raw_saver if isinstance(checkpointer, CheckpointerAdapter) else checkpointer

    config = {"configurable": {"thread_id": instance_id}}

    # Get the current state from async checkpointer
    state = await saver.aget(config)
    if state is None:
        return []

    # LangGraph stores messages in channel_values
    channel_values = state.get("channel_values", {})
    messages = channel_values.get("messages", [])
    if not messages:
        return []

    # Collect all checkpoints with timestamps
    # We need to iterate oldest-to-newest to track when messages first appeared
    checkpoints_data: list[tuple[str | None, list[Any]]] = []

    async for checkpoint_tuple in saver.alist(config, limit=1000):
        ct = cast(CheckpointTuple, checkpoint_tuple)
        checkpoint = ct.checkpoint
        if not isinstance(checkpoint, dict):
            continue
        ts = checkpoint.get("ts")
        checkpoint_messages = checkpoint.get("channel_values", {}).get("messages", [])
        checkpoints_data.append((ts, checkpoint_messages))

    # Reverse to get oldest-to-newest order
    checkpoints_data.reverse()

    # Track when each message first appeared
    msg_timestamps: dict[str, str] = {}
    for ts, checkpoint_messages in checkpoints_data:
        if not ts:
            continue
        for msg in checkpoint_messages:
            msg_id = getattr(msg, 'id', None)
            if msg_id and msg_id not in msg_timestamps:
                msg_timestamps[msg_id] = ts

    # Build a map of tool_call_id -> output from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):  # It's a ToolMessage
            tool_outputs[msg.tool_call_id] = msg.content

    result = []

    for msg in messages:
        msg_type = getattr(msg, 'type', 'unknown')

        # Skip tool messages (they're included in tool_calls of AIMessages)
        if msg_type == 'tool':
            continue

        # Serialize the message using shared utility
        serialized = serialize_message(msg, tool_outputs)

        # Get message ID and use it to look up timestamp
        msg_id = serialized["message_id"]
        created_at = msg_timestamps.get(msg_id)
        if not created_at:
            created_at = state.get("ts")

        # Add instance_id and created_at
        serialized["instance_id"] = instance_id
        serialized["created_at"] = created_at

        result.append(serialized)

    # ── Synthetic system message injection ──────────────────────────────────
    # The agent's system prompt is NOT persisted to the LangGraph checkpoint
    # (it is constructed locally in the agent_node for each LLM call, then
    # discarded). This means the frontend "View system message" toggle sees
    # no system message. Reconstruct it from the manager + prompt cache and
    # prepend it as a synthetic role="system" entry. Best-effort: any
    # failure (missing instance meta, no manager, no cache, etc.) leaves the
    # original list unchanged.
    if manager is not None:
        try:
            # The full prompt path (load_and_cache_prompt + DB read +
            # _apply_post_cache_appends) does sync filesystem + DB I/O and
            # may walk the registry. Offload the whole reconstruction to a
            # worker thread so we don't block the event loop, matching the
            # pattern already used inside the helper for the SQLAlchemy
            # ``Repository.get`` call.
            reconstruction = await asyncio.to_thread(
                _reconstruct_full_system_prompt,
                instance_id,
                manager,
            )
            if reconstruction is not None:
                system_prompt, instance_created_at = reconstruction
            else:
                system_prompt, instance_created_at = None, None
            if system_prompt and system_prompt.strip():
                created_at = (
                    instance_created_at.isoformat()
                    if hasattr(instance_created_at, "isoformat")
                    else str(instance_created_at or instance_id)
                )
                system_msg = {
                    "message_id": f"synthetic-system-{instance_id}",
                    "type": "system",
                    "role": "system",
                    "content": system_prompt,
                    "thinking": None,
                    "thinking_extracted": None,
                    "tool_calls": None,
                    "images": None,
                    "created_at": created_at,
                    "instance_id": instance_id,
                    "is_synthetic": True,
                }
                result.insert(0, system_msg)
        except Exception as exc:
            # Best-effort injection — never fail the call because of a
            # missing system prompt.
            logger.debug(
                f"get_instance_messages: skipping synthetic system prompt "
                f"for {instance_id[:8] if instance_id else '?'}: {exc}"
            )

    return result


def _reconstruct_full_system_prompt(
    instance_id: str,
    manager: Any,
) -> tuple[str, Any] | None:
    """Reconstruct the FULL system prompt the LLM actually saw.

    Mirrors the spawn/restore path in
    :func:`daemon.services.instance_lifecycle.spawn_instance` /
    :func:`_restore_instance`:

        base_prompt = load_and_cache_prompt(...)
        full_prompt = _apply_post_cache_appends(base_prompt, ...)

    Args:
        instance_id: The instance whose checkpoint we are reading.
        manager: The ``InstanceManager`` facade. Used to access the
            instance/project/shared-context repositories, the prompt cache,
            and (best-effort) the agent registry.

    Returns:
        The fully composed system prompt (base + post-cache appends), or
        ``None`` if any required dependency is missing. Caller treats
        ``None`` as "skip injection" — never raises.
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    prompt_cache = getattr(manager, "prompt_cache", None)
    if instance_repo is None or prompt_cache is None:
        return None

    # Repository.get() is sync SQLAlchemy.
    instance_meta = instance_repo.get(instance_id)
    if instance_meta is None:
        return None

    agent_id = getattr(instance_meta, "agent_id", None)
    agent_dir_raw = getattr(instance_meta, "agent_dir", None)
    if not agent_id or not agent_dir_raw:
        return None

    agent_dir = Path(agent_dir_raw)
    mcp_tool_names = None
    inst_meta = getattr(instance_meta, "instance_metadata", None)
    if isinstance(inst_meta, dict):
        mcp_tool_names = inst_meta.get("mcp_tool_names")
    version_tag = getattr(instance_meta, "agent_tag", None)

    # Lazy import here — same pattern used by the spawn/restore call sites
    # in instance_lifecycle.py so test patches at ``daemon.manager.*``
    # take effect.
    from daemon.manager import load_and_cache_prompt

    system_prompt, _ = load_and_cache_prompt(
        agent_id=agent_id,
        agent_dir=agent_dir,
        cache=prompt_cache,
        mcp_tool_names=mcp_tool_names,
        version_tag=version_tag,
    )
    if not system_prompt or not system_prompt.strip():
        return None

    # Best-effort agent_meta lookup so context_injection and allowed_models
    # appenders (which are gated on agent_meta flags) can run too. The
    # resolve mirrors the restore path: try the versioned agent first, then
    # fall back to the base, then to None. Any registry failure is
    # swallowed — the prompt will still get the other appends.
    agent_meta: Any = None
    try:
        from daemon.registry import get_registry
        registry = get_registry()
        if version_tag is not None:
            agent_meta = registry.get_version(agent_id, version_tag)
        if agent_meta is None:
            agent_meta = registry.get_resolved(agent_id)
    except Exception:
        agent_meta = None

    # Apply the post-cache append chain — same call sites as the spawn
    # path at daemon/services/instance_lifecycle.py:1250-1261 and the
    # restore path at lines 2623-2634. We mirror those signature exactly.
    from daemon.services.instance_lifecycle import (
        _apply_post_cache_appends,
        _resolve_injection_mode,
    )

    # Phase 2 (ADR-8): pass the resolved mode from agent_meta so
    # human_messages agents see the system prompt without the 3
    # CONTEXT knots but WITH the prompt-injection defense instruction.
    # ``agent_meta`` is best-effort above and may be ``None`` here;
    # ``_resolve_injection_mode`` defaults to ``"system_prompt"`` in
    # that case so legacy call behavior is preserved byte-identical.
    resolved_mode = _resolve_injection_mode(agent_meta)

    system_prompt, _user_language = _apply_post_cache_appends(
        system_prompt=system_prompt,
        instance_id=instance_id,
        instance_repository=instance_repo,
        shared_context_metadata_repo=getattr(
            manager, "shared_context_metadata_repo", None
        ),
        parent_id=getattr(instance_meta, "parent_id", None),
        agent_id=agent_id,
        project_id=getattr(instance_meta, "project_id", None),
        project_repository=getattr(manager, "_project_repository", None),
        manager=manager,
        agent_meta=agent_meta,
        disable_auto_load_tracking=True,
        mode=resolved_mode,
    )
    return system_prompt, getattr(instance_meta, "created_at", None)
