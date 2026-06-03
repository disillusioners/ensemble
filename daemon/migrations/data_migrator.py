"""Data migrator: copies table contents from SQLite to PostgreSQL.

Phase 3 of the SQLite -> PostgreSQL migration plan. This module is responsible
for moving *data* (rows) once the target schema has been created on the
PostgreSQL side via ``SQLModel.metadata.create_all()``.

Why an ORM-layer migrator instead of raw SQL?

1. **Type coercion** - SQLite stores ``BOOLEAN`` as 0/1 integers and ``JSON``
   columns as ``TEXT``. Running raw ``INSERT INTO ... SELECT *`` from
   ``sqlite_master`` would push raw text into Postgres' ``JSONB`` columns
   and fail type validation. Letting SQLModel serialize each row via
   ``model_dump()`` gives us automatic Python-to-Postgres coercion.
2. **Column-name remapping** - Several models use ``sa_column=Column("name", ...)``
   to alias a Python attribute (e.g. ``instance_metadata``) to a database
   column (e.g. ``metadata``). The ORM knows about this mapping; raw
   ``INSERT ... SELECT *`` does not.
3. **Conflict-aware upsert** - Re-running the migrator should be safe.
   ``INSERT ... ON CONFLICT (pk_columns) DO NOTHING`` is the simplest way to
   achieve idempotency once FK ordering is correct.

Why topological ordering matters
--------------------------------

PostgreSQL validates foreign-key constraints *before* its
``ON CONFLICT`` resolution runs. If a child row references a parent that
hasn't been inserted yet, the insert fails with a ``foreign_key_violation``
that ``DO NOTHING`` cannot suppress. ``SQLModel.metadata.sorted_tables``
returns tables in dependency order (parents first), which is exactly what
we need.

Tables that intentionally opt out
---------------------------------

* ``schema_migrations`` - Phase 3 backfills it with all version rows marked
  as applied at once. The data migrator skips it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Iterator

from sqlalchemy import Engine, func, select
from sqlmodel import Session, SQLModel

from daemon.migrations import MigrationCancelledError

# ---------------------------------------------------------------------------
# Import every model module so its tables register with SQLModel.metadata.
#
# This is the canonical list of model modules per the Phase 3 plan. Each
# import has the side-effect of attaching a ``Table`` to
# ``SQLModel.metadata``, which is what ``sorted_tables`` and our
# table-name -> class lookup walk.
#
# ``watcher_models`` is a submodule of ``job_queue`` that is *not* re-exported
# by ``daemon.repositories.__init__``; we must import it explicitly so the
# ``job_watchers`` table is registered.
# ---------------------------------------------------------------------------
from daemon.repositories.instance import models as _instance_models  # noqa: F401
from daemon.repositories.project import models as _project_models  # noqa: F401
from daemon.repositories.source import models as _source_models  # noqa: F401
from daemon.repositories.job_queue import models as _job_queue_models  # noqa: F401
from daemon.repositories.job_queue import watcher_models as _watcher_models  # noqa: F401
from daemon.repositories.message_queue import models as _message_queue_models  # noqa: F401
from daemon.repositories.mcp_server import models as _mcp_server_models  # noqa: F401
from daemon.repositories.task import models as _task_models  # noqa: F401
from daemon.repositories.event import models as _event_models  # noqa: F401
from daemon.migrations.models import SchemaMigration as _SchemaMigration  # noqa: F401

logger = logging.getLogger(__name__)


# Tables that the data migrator never touches. ``schema_migrations`` is
# backfilled separately by the Phase 3 orchestration layer with every
# version row pre-marked as applied (PostgreSQL starts with the latest
# schema, so we don't replay individual migrations).
TABLES_TO_SKIP: frozenset[str] = frozenset({"schema_migrations"})

# Default batch size for streaming rows from SQLite to PostgreSQL.
DEFAULT_BATCH_SIZE: int = 500

# Callback signature for progress reporting. Implementations may attach
# this to an SSE event stream, a structured logger, or just ``print``.
LogCallback = Callable[..., None]


class TableMigrator:
    """Migrates table data from SQLite to PostgreSQL using ORM-layer batch inserts.

    The migrator is a *single-shot* orchestrator: instantiate it with the
    source (SQLite) and destination (PostgreSQL) engines, then call
    :meth:`migrate_all_tables`. It is safe to call multiple times - the
    per-row ``ON CONFLICT DO NOTHING`` makes re-runs idempotent.

    Args:
        sqlite_engine: Synchronous SQLAlchemy engine for the source database.
        pg_engine: Synchronous SQLAlchemy engine for the destination
            PostgreSQL database. The schema must already exist (call
            ``SQLModel.metadata.create_all(pg_engine)`` first).
        cancel_event: Threading event used to request cancellation. The
            migrator checks ``is_set()`` between batches; if it fires we
            raise :class:`MigrationCancelledError`.
        log_callback: Optional callable invoked with ``(level=..., message=..., **kwargs)``
            to report progress. If ``None`` we fall back to the module logger.

    Example:
        >>> import threading
        >>> from daemon.migrations.data_migrator import TableMigrator
        >>> cancel = threading.Event()
        >>> migrator = TableMigrator(sqlite_engine, pg_engine, cancel)
        >>> counts = migrator.migrate_all_tables()
        >>> print(counts)
    """

    def __init__(
        self,
        sqlite_engine: Engine,
        pg_engine: Engine,
        cancel_event: threading.Event,
        log_callback: LogCallback | None = None,
    ) -> None:
        self._sqlite_engine = sqlite_engine
        self._pg_engine = pg_engine
        self._cancel_event = cancel_event
        self._log_callback = log_callback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def migrate_all_tables(self) -> dict[str, int]:
        """Migrate all tables in FK-safe order.

        Uses :attr:`sqlalchemy.sql.MetaData.sorted_tables` to resolve the
        topological ordering: every parent table is migrated before any
        table that references it. This is the property that makes
        ``ON CONFLICT DO NOTHING`` safe even with PostgreSQL's
        eager FK validation.

        Tables in :data:`TABLES_TO_SKIP` are excluded (the only one today
        is ``schema_migrations``).

        Returns:
            Mapping of ``table_name -> rows_migrated``. A table that
            existed in the schema but was empty in the source database
            is included with ``0`` rows. Tables that don't exist in
            either engine are silently skipped (some optional models
            are conditional).

        Raises:
            MigrationCancelledError: If ``cancel_event`` fires mid-run.
        """
        # Introspect once, then iterate. The registry is mutable in
        # principle, but in practice it's frozen after application
        # startup so this is safe.
        sorted_tables = list(SQLModel.metadata.sorted_tables)
        model_map = self._build_model_class_map()
        results: dict[str, int] = {}

        for table in sorted_tables:
            table_name = table.name
            if table_name in TABLES_TO_SKIP:
                self._log(
                    "info",
                    f"Skipping table {table_name} (handled separately)",
                    table_name=table_name,
                )
                continue

            # Skip tables that don't have a model class registered.
            # ``sorted_tables`` walks every metadata entry; some are
            # referenced by FK from other models but we don't own them.
            model_cls = model_map.get(table_name)
            if model_cls is None:
                self._log(
                    "debug",
                    f"No model class for {table_name}; skipping",
                    table_name=table_name,
                )
                continue

            # Only migrate tables that actually exist in the source DB.
            # Fresh databases won't have every table populated.
            if not self._table_exists(self._sqlite_engine, table_name):
                self._log(
                    "debug",
                    f"Table {table_name} not present in source DB; skipping",
                    table_name=table_name,
                )
                continue

            # Cancellation check between tables. The inner batch loop
            # also checks; this gives the caller a clean exit point
            # even for small tables (< 1 batch).
            if self._cancel_event.is_set():
                raise MigrationCancelledError(
                    f"Migration cancelled before table {table_name}"
                )

            self._log(
                "info",
                f"Migrating table {table_name}",
                table_name=table_name,
            )

            rows = self.migrate_table(table_name, [model_cls])
            results[table_name] = rows

            self._log(
                "info",
                f"Migrated {rows} rows from {table_name}",
                table_name=table_name,
                rows_processed=rows,
            )

        return results

    def migrate_table(
        self,
        table_name: str,
        model_classes: list[type],
    ) -> int:
        """Migrate a single table.

        Reads rows from the SQLite engine as SQLModel instances, then
        writes them to PostgreSQL in batches of :data:`DEFAULT_BATCH_SIZE`.
        Each batch is committed as a single transaction.

        Args:
            table_name: Target table name. Used for logging only.
            model_classes: SQLModel classes to read from the source. The
                first entry is used as the source-of-truth model for both
                the SELECT and the INSERT shape (column order, defaults,
                column-name aliases). Multiple classes are accepted for
                API symmetry but in practice each table has exactly one
                owning model.

        Returns:
            Number of rows successfully written to PostgreSQL.

        Raises:
            MigrationCancelledError: If the cancel event fires between
                batches. Any rows already written in earlier batches are
                *not* rolled back; the caller is responsible for that.
            ValueError: If ``model_classes`` is empty.
        """
        if not model_classes:
            raise ValueError(
                f"migrate_table({table_name!r}) requires at least one model class"
            )

        # PostgreSQL-specific ``Insert`` construct — lazy import so the
        # module loads on SQLite-only installs that don't have psycopg
        # / asyncpg installed. The ``ON CONFLICT`` clause only exists on
        # the PG dialect; the generic ``sqlalchemy.insert`` factory does
        # not expose ``on_conflict_do_nothing``.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        model_cls = model_classes[0]
        pk_columns = [col.name for col in model_cls.__table__.primary_key.columns]

        rows_migrated = 0
        with Session(self._sqlite_engine) as src_session, \
                Session(self._pg_engine) as dst_session:
            # Stream the source rows in batches. We use a simple offset/limit
            # pagination because ``yield_per`` requires server-side cursors
            # which sqlite + aiosqlite don't support uniformly. The pagination
            # is good enough for our table sizes (millions of rows max).
            offset = 0
            batch_size = DEFAULT_BATCH_SIZE
            while True:
                if self._cancel_event.is_set():
                    raise MigrationCancelledError(
                        f"Migration cancelled during {table_name} "
                        f"after {rows_migrated} rows"
                    )

                stmt = select(model_cls).offset(offset).limit(batch_size)
                batch = src_session.exec(stmt).all()
                if not batch:
                    break

                for row in batch:
                    if self._cancel_event.is_set():
                        raise MigrationCancelledError(
                            f"Migration cancelled during {table_name} "
                            f"after {rows_migrated} rows"
                        )

                    data = self._row_to_dict(row, model_cls)
                    # ``on_conflict_do_nothing`` requires explicit conflict
                    # targets - PostgreSQL does not infer them from the
                    # primary key automatically. We pass the PK column
                    # names extracted via metadata introspection.
                    insert_stmt = pg_insert(model_cls).values(**data)
                    if pk_columns:
                        insert_stmt = insert_stmt.on_conflict_do_nothing(
                            index_elements=pk_columns,
                        )
                    dst_session.execute(insert_stmt)
                    rows_migrated += 1

                # Commit once per batch. Smaller commits (per row) would
                # be O(n) round-trips; larger commits risk holding a big
                # transaction open. 500 is the sweet spot the plan calls
                # out and matches existing batch boundaries elsewhere.
                dst_session.commit()
                offset += batch_size

                self._log(
                    "info",
                    f"Batch of {len(batch)} rows committed to {table_name} "
                    f"(total {rows_migrated})",
                    table_name=table_name,
                    rows_processed=rows_migrated,
                    batch_size=len(batch),
                )

        return rows_migrated

    def validate_migration(self) -> list[dict[str, Any]]:
        """Compare row counts between SQLite and PostgreSQL for all tables.

        Runs ``SELECT COUNT(*)`` against every non-skipped table on both
        engines and reports any mismatches. This is a *coarse* check -
        it's fast and catches gross errors (e.g. a table that wasn't
        migrated at all) but does not verify that individual rows are
        byte-identical. Use it as a smoke test, not a deep diff.

        Returns:
            A list of dicts, one per mismatch. Each dict has the shape::

                {
                    "table": "<name>",
                    "sqlite_count": <int>,
                    "pg_count": <int>,
                    "diff": <int>,        # sqlite_count - pg_count
                }

            An empty list means all tables have matching counts.
        """
        model_map = self._build_model_class_map()
        mismatches: list[dict[str, Any]] = []

        for table in SQLModel.metadata.sorted_tables:
            table_name = table.name
            if table_name in TABLES_TO_SKIP:
                continue

            # Only validate tables that exist in the source - if the
            # source never had it, the destination won't either.
            if not self._table_exists(self._sqlite_engine, table_name):
                continue

            # Use the model class when available; fall back to a raw
            # ``text()`` count for tables we don't own directly.
            sqlite_count = self._count_rows(self._sqlite_engine, table_name, model_map)
            pg_count = self._count_rows(self._pg_engine, table_name, model_map)

            if sqlite_count != pg_count:
                mismatches.append({
                    "table": table_name,
                    "sqlite_count": sqlite_count,
                    "pg_count": pg_count,
                    "diff": sqlite_count - pg_count,
                })
                self._log(
                    "warning",
                    f"Row count mismatch for {table_name}: "
                    f"sqlite={sqlite_count}, pg={pg_count}",
                    table_name=table_name,
                    sqlite_count=sqlite_count,
                    pg_count=pg_count,
                )

        if not mismatches:
            self._log("info", "Migration validation passed: all tables match")
        else:
            self._log(
                "warning",
                f"Migration validation found {len(mismatches)} mismatches",
                mismatch_count=len(mismatches),
            )

        return mismatches

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_model_class_map() -> dict[str, type]:
        """Build a ``table_name -> model_class`` map from registered models.

        Walks the SQLModel class registry, picks out subclasses that are
        concrete tables (``table=True``), and keys them by their declared
        ``__tablename__``. This is the inverse of
        :data:`SQLModel.metadata.tables` and is needed to look up the
        Python class for a given database table (e.g. to drive a
        ``select(Model)``).
        """
        result: dict[str, type] = {}
        for cls in SQLModel.__subclasses__():
            # Recurse into subclasses (model inheritance).
            for sub in _walk_subclasses(cls):
                tablename = getattr(sub, "__tablename__", None)
                if not tablename:
                    continue
                # ``table=True`` models are the only ones that get a
                # SQLAlchemy Table attached; skip pure Pydantic helpers.
                if getattr(sub, "__table__", None) is None:
                    continue
                # First registration wins. In practice every table has
                # exactly one model class, but if there were aliases we
                # keep the canonical one.
                result.setdefault(tablename, sub)
        return result

    def _row_to_dict(self, row: Any, model_cls: type) -> dict[str, Any]:
        """Convert a SQLModel instance into a dict suitable for INSERT.

        Uses Pydantic's ``model_dump`` to coerce values into the column
        types the destination expects. For ``JSON`` columns the ORM will
        re-serialize Python dicts/lists; for ``datetime`` fields the
        default JSON encoder produces ISO 8601 strings, which Postgres
        accepts for ``TIMESTAMP`` columns.

        A subtle point: some models store a Python attribute under a
        different name than the database column (e.g.
        ``Instance.instance_metadata`` -> ``metadata`` column). We
        intentionally use ``model_dump()`` (which returns attribute
        names) and pass the dict to ``insert(Model).values(**data)`` -
        the ORM maps the attribute name to the column name, so the
        alias is handled transparently.

        Args:
            row: A SQLModel instance read from the source database, or
                a SQLAlchemy ``Row`` wrapper around one. ``session.exec``
                on SQLAlchemy 2.0 returns ``Row`` objects; we unwrap
                them transparently so the rest of the migrator can
                treat rows uniformly as model instances.
            model_cls: The class of ``row``. Currently unused but kept
                in the signature for future type-aware serialization
                (e.g. enum value coercion).
        """
        # ``session.exec(select(Model)).all()`` may return Row objects
        # (each row is a 1-tuple wrapping the model). Unwrap them.
        # The runner in ``daemon.migrations.runner`` does the same.
        if hasattr(row, "__getitem__") and not hasattr(row, "model_dump"):
            row = row[0]

        # ``exclude_unset=False`` (the default) means we serialize every
        # field, including those with default values. That's what we
        # want - the destination's column has a default too, but we
        # need to make the insert self-contained.
        return row.model_dump()

    def _count_rows(
        self,
        engine: Engine,
        table_name: str,
        model_map: dict[str, type],
    ) -> int:
        """Count rows in a table on the given engine.

        Prefers ``select(func.count()).select_from(Model)`` because the
        ORM compiles that to the most portable SQL. Falls back to a raw
        text query for tables that don't have a model class registered.
        """
        model_cls = model_map.get(table_name)
        if model_cls is not None:
            with Session(engine) as session:
                stmt = select(func.count()).select_from(model_cls)
                # ``.one()`` returns a SQLAlchemy ``Row`` (1-tuple wrapper).
                # ``Row`` defines ``__getitem__`` but is *not* a ``tuple``
                # subclass, so we have to index it explicitly. Defensive
                # code for the single-scalar case where the driver returns
                # a bare int.
                result = session.exec(stmt).one()
                if hasattr(result, "__getitem__") and not isinstance(result, (int, float)):
                    return int(result[0])
                return int(result)

        # Fallback: raw count. Should be rare - every migrated table
        # has a model class. Defensive code in case someone adds a
        # view or auxiliary table to metadata.
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            row = result.fetchone()
            return int(row[0]) if row else 0

    @staticmethod
    def _table_exists(engine: Engine, table_name: str) -> bool:
        """Check whether ``table_name`` is present in the given engine.

        Uses dialect-appropriate introspection: ``sqlite_master`` for
        SQLite and ``information_schema.tables`` for everything else
        (PostgreSQL in practice). This is a small but important check
        - some optional tables (``job_watchers``, ``mcp_servers``, etc.)
        may not exist on a fresh database, and we don't want to fail
        the whole migration because of one missing table.
        """
        from sqlalchemy import text
        url = str(engine.url)
        with engine.connect() as conn:
            if "sqlite" in url:
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name=:name"
                    ),
                    {"name": table_name},
                )
            else:
                result = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name = :name"
                    ),
                    {"name": table_name},
                )
            return result.fetchone() is not None

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """Forward a log message to the configured callback or stdlib logger.

        The callback protocol is intentionally loose: a positional
        ``level`` and ``message`` plus arbitrary keyword arguments. This
        is enough for the SSE event stream to render progress bars and
        for the CLI to print structured JSON. When no callback is
        registered we degrade gracefully to the module-level ``logger``.
        """
        if self._log_callback is not None:
            try:
                self._log_callback(level=level, message=message, **kwargs)
                return
            except Exception:  # pragma: no cover - callback must not break migration
                logger.exception("log_callback raised; falling back to logger")

        getattr(logger, level, logger.info)(message, extra=kwargs)


def _walk_subclasses(cls: type) -> Iterator[type]:
    """Yield ``cls`` and all transitive subclasses.

    Used by :meth:`TableMigrator._build_model_class_map` to handle
    models that inherit from other SQLModel classes. We yield the root
    first so the ``isinstance`` order in the caller matches declaration
    order, but order doesn't actually matter for this code path.
    """
    yield cls
    for sub in cls.__subclasses__():
        yield from _walk_subclasses(sub)


# ---------------------------------------------------------------------------
# Convenience: a re-exportable batch-chunking helper. Kept here (rather than
# ``utils.py``) because the data-migration plan is the only place that needs
# it and we'd rather not pollute the global utils namespace.
# ---------------------------------------------------------------------------
def chunked(iterable: Iterable, size: int) -> Iterator[list]:
    """Yield successive ``size``-element chunks from ``iterable``.

    A drop-in replacement for ``more_itertools.chunked`` (which isn't
    a project dependency). Used in test fixtures and any future
    streaming paths; the main migrator already iterates with explicit
    ``offset/limit`` pagination, but this helper is exposed for
    callers that want to chunk an in-memory list.
    """
    chunk: list = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


__all__ = [
    "TableMigrator",
    "TABLES_TO_SKIP",
    "DEFAULT_BATCH_SIZE",
    "chunked",
]
