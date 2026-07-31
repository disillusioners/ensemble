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

                 When the resolved injection mode is ``"human_messages"``
                 (Phase 4 add), the function additionally calls
                 :func:`daemon.services.context_messages.assemble_context_messages`
                 to rebuild the per-turn context messages on-demand and
                 inserts them between the synthetic system message and the
                 most recent user message. Each rebuilt context message is
                 stamped with ``is_synthetic=True`` and a ``context_kind``
                 field so the frontend can identify them.

    Returns:
        List of message dictionaries with role, content, thinking, tool_calls.
        When ``manager`` is provided and the system prompt can be
        reconstructed, a synthetic ``role="system"`` message is prepended.
        When the agent opts into ``human_messages`` mode, zero or more
        synthetic context messages are inserted immediately before the
        most recent user message. The response shape is additive —
        existing API consumers continue to see the same messages they
        saw before; only ``is_synthetic`` + ``context_kind`` fields are
        added on the new entries.
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

    # ── Phase 4: resolve instance/agent metadata + injection mode once ─────────
    # ``get_instance_messages`` may need to do TWO things with this
    # metadata:
    #
    #   1. Reconstruct the full system prompt for the synthetic system
    #      message (legacy behavior).
    #   2. Rebuild per-turn context messages when the agent opts into
    #      ``human_messages`` mode (Phase 4 add).
    #
    # Both lookups touch the same repos + the agent registry, so we
    # resolve them once here and thread the result into both helpers
    # rather than re-doing the work in each. When ``manager`` is None
    # or the instance meta is missing, ``ctx`` is ``None`` and both
    # downstream paths skip (backward-compatible — the legacy code
    # returned the persisted list unchanged).
    ctx = None
    if manager is not None:
        try:
            ctx = _resolve_instance_message_context(instance_id, manager)
        except Exception as exc:
            logger.debug(
                f"get_instance_messages: context metadata resolution "
                f"failed for {instance_id[:8] if instance_id else '?'}: {exc}"
            )
            ctx = None

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
                ctx,
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

    # ── Phase 4: per-turn context messages for ``human_messages`` mode ──────
    # Two paths emit context messages in ``human_messages`` mode:
    #
    # * Path A (write — ``_build_graph_input`` in
    #   ``daemon/services/instance_messaging.py:203-211``) INTENTIONALLY
    #   pre-pends ``persistent_context_msgs`` (project + shared context
    #   + skills) to ``graph_input`` so LangGraph's ``add_messages``
    #   reducer checkpoints them as REAL messages alongside the user
    #   turn. This is the desired design — preserves the LLM prefix-
    #   cache and makes the skill block visible in message history for
    #   debugging.
    #
    # * Path B (read — this function) previously assumed the
    #   checkpoint had NO context messages and rebuilt them on every
    #   GET /messages call as ``is_synthetic=True`` entries. With Path A
    #   active, that assumption is wrong: rebuilding here produces
    #   DUPLICATE ``[SYSTEM CONTEXT]`` entries in the API response (one
    #   real checkpointed msg + one synthetic rebuild).
    #
    # The guard below skips the synthetic rebuild whenever the
    # checkpoint already contains context messages. The real entries
    # are serialized into ``result`` by the normal loop above and the
    # frontend sees exactly one block per turn — matching what the LLM
    # actually received. The synthetic rebuild path is preserved as
    # the fallback for any instance that predates the Path A
    # checkpointing behavior.
    #
    # Strictly read-only: no DB writes, no skill tracking. Errors are
    # swallowed so a flaky context build never breaks the GET endpoint.
    if ctx is not None and ctx["mode"] == "human_messages":
        if _messages_have_context_block(messages):
            # Two-path conflict: checkpoint already carries context
            # messages from Path A. The normal serialization loop above
            # has already emitted them — skip the synthetic rebuild so
            # the API response does not duplicate every [SYSTEM CONTEXT]
            # block. Logged at DEBUG so the dedup path is observable in
            # the daemon log without surfacing to the API caller.
            logger.debug(
                f"get_instance_messages: checkpoint already contains "
                f"context messages for {instance_id[:8] if instance_id else '?'}; "
                f"skipping synthetic rebuild to avoid duplicates"
            )
        else:
            # Legacy / fallback path: rebuild context messages for the
            # *current* turn using the same helper ``agent_node`` uses
            # every turn. The rebuilt entries are stamped with
            # ``is_synthetic=True`` and a stable ``synthetic-context-<kind>-…``
            # ``message_id`` so the frontend can identify them.
            try:
                context_dicts = await _build_context_dicts_for_response(
                    instance_id=instance_id,
                    ctx=ctx,
                    manager=manager,
                    messages=messages,
                )
                if context_dicts:
                    # Insert AFTER the synthetic system message (if any) but
                    # BEFORE the most recent user message. ``human_messages``
                    # mode rebuilds context for the *current* turn, so only
                    # the last user turn is preceded by context — historical
                    # turns remain bare in the API response, matching what
                    # the LLM actually received on those earlier turns.
                    insert_at = _locate_context_insertion_index(result)
                    result[insert_at:insert_at] = context_dicts
            except Exception as exc:
                # Best-effort — never fail GET /messages because context
                # rebuild broke.
                logger.debug(
                    f"get_instance_messages: skipping context rebuild for "
                    f"{instance_id[:8] if instance_id else '?'}: {exc}"
                )

    return result


def _resolve_instance_message_context(
    instance_id: str,
    manager: Any,
) -> dict[str, Any] | None:
    """Resolve the metadata ``get_instance_messages`` needs to inject.

    Returns a dict with three keys:

    * ``instance_meta`` — the ``Instance`` row from the manager's
      ``_instance_repository``. ``None`` if the instance is gone or
      the repo is missing.
    * ``agent_meta`` — best-effort resolved ``AgentMetadata`` for
      the agent (versioned first, then base). ``None`` when the
      registry is unreachable.
    * ``mode`` — the result of ``_resolve_injection_mode(agent_meta)``,
      i.e. ``"human_messages"`` (default) or ``"legacy"``. Defaults to
      ``"human_messages"`` when ``agent_meta`` is ``None``, matching
      the new default contract.

    ``None`` is returned when no instance repository is attached or
    the instance row cannot be loaded — callers treat that as
    "skip both synthetic system message and context rebuild".

    The function is sync because all the lookups it performs are
    sync (SQLAlchemy ``Repository.get`` and the in-process agent
    registry). Callers that need async-safety wrap the call in
    :func:`asyncio.to_thread` (see ``get_instance_messages``).
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        return None

    # Repository.get() is sync SQLAlchemy.
    instance_meta = instance_repo.get(instance_id)
    if instance_meta is None:
        return None

    agent_id = getattr(instance_meta, "agent_id", None)
    version_tag = getattr(instance_meta, "agent_tag", None)

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

    # Lazy import here — same pattern used by the spawn/restore call sites
    # in instance_lifecycle.py so test patches at ``daemon.manager.*``
    # take effect.
    from daemon.services.instance_lifecycle import _resolve_injection_mode
    mode = _resolve_injection_mode(agent_meta)

    return {
        "instance_meta": instance_meta,
        "agent_meta": agent_meta,
        "mode": mode,
    }


def _messages_have_context_block(messages: list[Any]) -> bool:
    """Return True when any checkpoint message is a context HumanMessage.

    Detects context messages by matching the ``additional_kwargs``
    metadata that
    :func:`daemon.services.context_messages._make_context_message`
    sets on every emitted message: ``{"injected_message": True,
    "context_kind": <kind>}`` where ``kind`` is one of
    ``"project"``, ``"shared_context"``, or ``"skills"``. The metadata
    is the canonical marker for ADR-5 — matching on it (rather than
    the ``[SYSTEM CONTEXT: ...]`` content prefix) keeps the guard
    robust against content rewrites by compaction, mangling, or any
    other serialise/deserialise round-trip.

    Used by ``get_instance_messages`` to skip the duplicate-context
    synthetic rebuild when the LangGraph checkpoint already contains
    context messages — the desired behavior when Path A
    (``_build_graph_input`` in ``instance_messaging.py``) checks
    those messages through LangGraph's ``add_messages`` reducer.

    The canonical ``context_kind`` set is duplicated here as a
    module-local constant so the early-skip guard stays a cheap,
    import-free dict lookup. The values are kept in sync with
    ``daemon.services.context_messages.CONTEXT_KIND_*`` constants
    by test coverage (``tests/integration/test_context_injection_integration.py``).

    Args:
        messages: The persisted checkpoint messages list (LangChain
            ``BaseMessage`` subclasses read from
            ``saver.aget(...)['channel_values']['messages']``).

    Returns:
        True as soon as one context message is found; False otherwise.
        Defensive: any malformed message (missing attribute, wrong
        type, etc.) is silently treated as "not a context message"
        so the synthetic rebuild path stays load-bearing for any
        unexpected branch.
    """
    # Mirrors ``context_messages.CONTEXT_KIND_*`` — keep in sync.
    # ``auto_load_skills`` is included so the read path serves the
    # checkpointed auto-load block directly instead of rebuilding +
    # re-running clone-on-miss on every ``GET /messages`` poll once the
    # block has been checkpointed on turn 1.
    _CONTEXT_KINDS = frozenset({
        "project", "shared_context", "auto_load_skills", "skills",
    })

    for msg in messages:
        try:
            kwargs = getattr(msg, "additional_kwargs", None) or {}
        except Exception:
            continue
        if not kwargs:
            continue
        if not kwargs.get("injected_message"):
            continue
        if kwargs.get("context_kind") in _CONTEXT_KINDS:
            return True
    return False


async def _build_context_dicts_for_response(
    *,
    instance_id: str,
    ctx: dict[str, Any],
    manager: Any,
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Build the synthetic context-message dicts for the API response.

    Calls :func:`daemon.services.context_messages.assemble_context_messages`
    using the pre-resolved metadata from :func:`_resolve_instance_message_context`
    and the *latest user message* from the persisted checkpoint as the
    ``user_query``. The resulting ``HumanMessage`` instances are then
    run through :func:`daemon.utils.serialize_message` (which emits
    ``context_kind`` per Phase 4 CHANGE 1) and stamped with
    ``is_synthetic=True`` and a stable ``synthetic-context-<kind>-<idx>``
    ``message_id`` so the frontend can identify them.

    Args:
        instance_id: The instance whose context to rebuild.
        ctx: Pre-resolved metadata from ``_resolve_instance_message_context``.
        manager: The :class:`InstanceManager` facade (passed through to
            ``assemble_context_messages`` for repo / skill-search access).
        messages: The persisted checkpoint messages list. Used to find
            the latest user message text.

    Returns:
        Serialized context-message dicts in the same canonical order as
        ``assemble_context_messages`` emits them. Empty list when there
        is no user message yet (nothing to query against) or when the
        helper returned no messages (no context to show).
    """
    # Need a user query to drive the RAG + skill-search pipelines.
    # ``assemble_context_messages`` requires non-empty input; we mirror
    # the agent_node contract by using the latest persisted user message.
    user_query = ""
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "human":
            content = getattr(msg, "content", "") or ""
            user_query = content if isinstance(content, str) else str(content)
            break
    if not user_query:
        # No user turn yet — nothing to contextualize against.
        return []

    instance_meta = ctx["instance_meta"]
    project_id = getattr(instance_meta, "project_id", None)
    parent_id = getattr(instance_meta, "parent_id", None)
    agent_meta = ctx["agent_meta"]
    instance_repo = getattr(manager, "_instance_repository", None)

    # Lazy import — keeps the import-time graph ↔ services cycle from
    # biting during ``daemon.persistence`` import in test collection.
    from daemon.services.context_messages import assemble_context_messages

    # Hybrid Context Injection (2026-07-29): the orchestrator now
    # returns ``(persistent_msgs, ephemeral_msgs)``. The read path
    # (``GET /messages``) needs ALL context kinds surfaced as
    # synthetic messages so the frontend can render the full context
    # block — flatten the tuple and emit in canonical order
    # (project → shared_context → skills) regardless of which half
    # the runtime agent_node would consume.
    persistent_msgs, ephemeral_msgs = await assemble_context_messages(
        instance_id=instance_id,
        user_query=user_query,
        project_id=project_id,
        agent_meta=agent_meta,
        manager=manager,
        instance_repository=instance_repo,
        parent_id=parent_id,
        # Pre-computed skill result is unavailable on the read path;
        # ``assemble_context_messages`` will run the search itself.
        skill_injection_result=None,
    )
    context_msgs = list(persistent_msgs) + list(ephemeral_msgs)
    if not context_msgs:
        return []

    # Lazy import — matches the existing helper above.
    from daemon.utils import serialize_message

    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(context_msgs):
        d = serialize_message(msg)
        # Phase 4: mark every context message synthetic so
        # ``child_reports.py`` (lines 523, 1007) and any other
        # ``is_synthetic`` filters continue to exclude them, and the
        # frontend can apply the synthetic styling. Stable ID encodes
        # the kind so the same kind re-rebuilt on the next request
        # keeps its identity if the frontend wants to key on it.
        context_kind = d.get("context_kind") or "context"
        d["message_id"] = f"synthetic-context-{context_kind}-{instance_id}-{idx}"
        d["instance_id"] = instance_id
        d["is_synthetic"] = True
        out.append(d)
    return out


def _locate_context_insertion_index(result: list[dict[str, Any]]) -> int:
    """Find the index to insert synthetic context messages at.

    The contract: insert AFTER the synthetic system message (if any) but
    BEFORE the most recent user message. When there is no user message
    yet, append at the end of the list (after the synthetic system
    message). The synthetic system message is at index 0 only when it
    was injected — we detect it by the ``message_id`` convention.
    """
    has_synthetic_system = bool(result) and result[0].get("message_id", "").startswith(
        "synthetic-system-"
    )
    scan_from = 1 if has_synthetic_system else 0
    for i in range(len(result) - 1, scan_from - 1, -1):
        if result[i].get("role") == "user":
            return i
    # No user message found after the synthetic system message → append.
    return len(result)


def _reconstruct_full_system_prompt(
    instance_id: str,
    manager: Any,
    ctx: dict[str, Any] | None = None,
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
        ctx: Optional pre-resolved metadata from
            :func:`_resolve_instance_message_context` so we don't
            re-do the instance_repo lookup, the agent registry lookup,
            and the injection-mode resolution when ``get_instance_messages``
            already did them. ``None`` → resolve them locally
            (preserves the pre-Phase-4 call signature used by callers
            outside ``get_instance_messages``).

    Returns:
        The fully composed system prompt (base + post-cache appends), or
        ``None`` if any required dependency is missing. Caller treats
        ``None`` as "skip injection" — never raises.
    """
    # Phase 4: when ``ctx`` is provided by ``get_instance_messages``, reuse
    # the already-resolved metadata. Otherwise fall back to the legacy
    # local lookups so existing direct callers keep working.
    if ctx is not None:
        instance_meta = ctx["instance_meta"]
        agent_meta = ctx["agent_meta"]
        resolved_mode = ctx["mode"]
        if instance_meta is None:
            return None
    else:
        instance_repo = getattr(manager, "_instance_repository", None)
        prompt_cache = getattr(manager, "prompt_cache", None)
        if instance_repo is None or prompt_cache is None:
            return None

        # Repository.get() is sync SQLAlchemy.
        instance_meta = instance_repo.get(instance_id)
        if instance_meta is None:
            return None

        agent_meta: Any = None
        try:
            from daemon.registry import get_registry
            registry = get_registry()
            version_tag = getattr(instance_meta, "agent_tag", None)
            if version_tag is not None:
                agent_meta = registry.get_version(
                    getattr(instance_meta, "agent_id", None), version_tag
                )
            if agent_meta is None:
                agent_meta = registry.get_resolved(
                    getattr(instance_meta, "agent_id", None)
                )
        except Exception:
            agent_meta = None

        from daemon.services.instance_lifecycle import _resolve_injection_mode
        resolved_mode = _resolve_injection_mode(agent_meta)

    agent_id = getattr(instance_meta, "agent_id", None)
    agent_dir_raw = getattr(instance_meta, "agent_dir", None)
    if not agent_id or not agent_dir_raw:
        return None

    prompt_cache = getattr(manager, "prompt_cache", None)
    if prompt_cache is None:
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

    # Apply the post-cache append chain — same call sites as the spawn
    # path at daemon/services/instance_lifecycle.py:1250-1261 and the
    # restore path at lines 2623-2634. We mirror those signature exactly.
    from daemon.services.instance_lifecycle import _apply_post_cache_appends

    instance_repo = getattr(manager, "_instance_repository", None)

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
