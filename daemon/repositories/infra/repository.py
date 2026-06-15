"""SQLModel-based Infra Asset Repository.

This module is the persistence layer for the infrastructure
asset storage (Phase 1 of the infra info storage design). It
exposes a dialect-aware CRUD + search + history API on top of
three SQLModel tables defined in :mod:`.models`.

Design highlights:

* **Audit tracking.** Every ``create_asset`` / ``update_asset``
  / ``delete_asset`` automatically writes a row to
  ``infra_asset_history`` with a full snapshot and (for
  updates) the ``changed_fields`` / ``old_values`` /
  ``new_values`` diff. Callers never have to remember to
  record history — the repository does it for them.
* **Dialect-aware JSON queries.** :meth:`search_assets`
  branches on the bound engine's dialect. PostgreSQL uses
  ``attributes->>'key'`` for path extraction and ``@>`` for
  containment (which the GIN indexes can serve); SQLite uses
  ``json_extract()`` and ``LIKE`` (the best SQLite can do
  without GIN).
* **Project isolation.** All asset queries filter on
  ``project_id`` — there is no escape hatch for cross-project
  reads at the repository level. Type-registry reads are
  global on purpose (one schema set across all projects).
* **Engine sharing.** The constructor takes a SQLAlchemy
  ``Engine`` only. The shared engine is created once at the
  ``InstanceManager`` level — see
  :func:`daemon.repositories.factory.create_engine_from_config`
  — and is passed to every repository to avoid DB lock
  contention.

The repository is intentionally sync. Sync calls are bridged
to async at the call sites (``asyncio.to_thread``) consistent
with the rest of the project (see the execution-lease
repository for the same pattern).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, cast as sa_cast
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import Float, String
from sqlmodel import Session, col, select

from .models import InfraAsset, InfraAssetHistory, InfraAssetType, InfraChangeType
from .types import INFRA_TYPE_DEFINITIONS

logger = logging.getLogger(__name__)


# ============================================================
# Result types
# ============================================================


@dataclass
class BootstrapResult:
    """Outcome of :meth:`SQLModelInfraRepository.bootstrap_default_types`.

    Attributes:
        registered: Every type row that was touched during the
            bootstrap (whether newly inserted or updated in place),
            in the order declared in
            :data:`~daemon.repositories.infra.types.INFRA_TYPE_DEFINITIONS`.
            Useful for callers that need to surface the resulting
            schemas (e.g. a startup log) without re-querying.
        new_count: Number of types that were inserted for the
            first time during this call. ``len(INFRA_TYPE_DEFINITIONS)``
            on a fresh DB, ``0`` on subsequent calls (the bootstrap
            is idempotent and upserts via :meth:`register_type`).
        updated_count: Number of types that already existed and
            were updated in place. Always equal to
            ``len(registered) - new_count``.
    """

    registered: list[InfraAssetType]
    new_count: int
    updated_count: int


# ============================================================
# Repository
# ============================================================


class SQLModelInfraRepository:
    """SQLModel-based repository for infra assets, types, and history.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` (the project's standard pattern) or
    invoke from inside the worker thread pool.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database. The same engine should be
                shared across all repositories to avoid lock
                contention — see
                :func:`daemon.repositories.factory.create_engine_from_config`.
        """
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _is_postgres(self) -> bool:
        """True iff the bound engine is PostgreSQL.

        Used to branch JSON-query construction between
        ``attributes->>'key'`` / ``@>`` (PostgreSQL JSONB) and
        ``json_extract(...)`` (SQLite).
        """
        return self.engine.dialect.name == "postgresql"

    def _json_path_text(self, column: Any, key: str) -> Any:
        """Build a dialect-aware expression that returns a JSON
        value cast to TEXT for ``key`` in JSON ``column``.

        * PostgreSQL → ``column->>'key'`` (returns ``text``).
        * SQLite → ``json_extract(column, '$.key')`` cast to TEXT.

        The cast is done with ``String`` (the SQLAlchemy
        canonical TEXT type) so the comparison behaves like a
        string compare, which is what we want for ``=`` and
        ``LIKE`` operations on attribute values.
        """
        if self._is_postgres():
            return column[key].astext
        return sa_cast(func.json_extract(column, f"$.{key}"), String)

    def _json_path_numeric(self, column: Any, key: str) -> Any:
        """Build a dialect-aware expression for a JSON value
        cast to a SQL numeric type.

        * PostgreSQL → ``column->>'key'`` cast to ``FLOAT`` /
          ``DOUBLE PRECISION``. (PostgreSQL's ``->>`` already
          returns TEXT, so we have to cast back.)
        * SQLite → ``CAST(json_extract(column, '$.key') AS REAL)``.

        Used by comparison operators (``$gt``, ``$gte``, ``$lt``,
        ``$lte``) when the caller-supplied value is numeric —
        comparing a JSON number against an integer via TEXT
        cast breaks lexicographically (``"16" > "8"`` is
        ``False``).
        """
        if self._is_postgres():
            return sa_cast(column[key].astext, Float)
        return sa_cast(func.json_extract(column, f"$.{key}"), Float)

    def _json_contains_predicate(self, column: Any, key: str, value: Any) -> Any:
        """Build a dialect-aware predicate for "the value at
        ``key`` in JSON ``column`` substring-contains ``value``".

        * PostgreSQL → ``column->>'key' LIKE '%value%'`` (case
          sensitive). The GIN index on ``column`` is not used
          for this predicate (LIKE on JSONB-extracted text is a
          sequential scan in PG too) — it's a fallback for
          tag-style substring matching.
        * SQLite → ``json_extract(column, '$.key') LIKE '%value%'``.
        """
        path = self._json_path_text(column, key)
        like_pattern = f"%{value}%"
        return path.like(like_pattern)

    def _json_eq_predicate(self, column: Any, key: str, value: Any) -> Any:
        """Equality predicate on a JSON value.

        For string values this is straightforward. For numeric
        values, both backends cast to TEXT (PostgreSQL's
        ``->>`` already returns text; SQLite's json_extract
        returns a number for numeric JSON). Equality still
        works correctly because the caller is expected to
        supply the same type the JSON stored.

        Booleans need a dialect branch because the two backends
        serialize them differently when extracted to text:

        * PostgreSQL ``->>`` returns the literal text
          ``"true"`` / ``"false"``.
        * SQLite ``json_extract`` returns ``1`` / ``0``.

        Comparing against ``str(True)`` (``"True"``) or
        ``str(False)`` (``"False"``) would never match on
        either backend — hence the explicit handling here.
        """
        if isinstance(value, bool):
            if self._is_postgres():
                # PostgreSQL ->> returns the literal text "true" / "false".
                return self._json_path_text(column, key) == ("true" if value else "false")
            # SQLite json_extract returns 1/0 for JSON booleans.
            return self._json_path_text(column, key) == ("1" if value else "0")
        return self._json_path_text(column, key) == str(value)

    def _json_ineq_predicate(
        self, column: Any, key: str, op: str, value: Any
    ) -> Any:
        """Build a comparison predicate on a JSON-extracted
        value. Supports ``>``, ``>=``, ``<``, ``<=`` and
        their negated form ``!=``.

        Numeric values (int / float, but not bool) are
        compared via the numeric path so JSON numbers sort
        correctly. Other values fall back to the TEXT path.

        Booleans get the same dialect branch as
        :meth:`_json_eq_predicate` because the two backends
        encode JSON booleans differently when extracted to
        text (``"true"``/``"false"`` on PostgreSQL,
        ``"1"``/``"0"`` on SQLite). Without this, ``$ne`` on
        a boolean would compare the SQLite ``"1"`` /
        ``"0"`` extraction against Python's ``str(True)`` =
        ``"True"`` and never match — every row passes the
        filter.
        """
        if isinstance(value, bool):
            if self._is_postgres():
                text_value = "true" if value else "false"
            else:
                text_value = "1" if value else "0"
            path = self._json_path_text(column, key)
            if op == "!=":
                return path != text_value
            # ``>`` / ``>=`` / ``<`` / ``<=`` on booleans are
            # semantically odd but supported for symmetry with
            # ``$eq``: True sorts after False on both backends
            # in this encoding.
            cmp = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}.get(
                op, op
            )
            return path.op(cmp)(text_value)
        is_numeric = isinstance(value, (int, float))
        path = (
            self._json_path_numeric(column, key)
            if is_numeric
            else self._json_path_text(column, key)
        )
        cmp_value: Any = value if is_numeric else str(value)
        if op == "!=":
            return path != cmp_value
        if op == ">":
            return path > cmp_value
        if op == ">=":
            return path >= cmp_value
        if op == "<":
            return path < cmp_value
        if op == "<=":
            return path <= cmp_value
        raise ValueError(f"Unsupported operator: {op!r}")

    def _json_in_predicate(self, column: Any, key: str, values: list[Any]) -> Any:
        """Build an IN-list predicate on a JSON-extracted TEXT value."""
        path = self._json_path_text(column, key)
        return path.in_([str(v) for v in values])

    def _build_attribute_predicates(
        self, column: Any, attributes: dict[str, Any]
    ) -> list[Any]:
        """Translate the caller-supplied ``attributes`` filter
        dict into a list of dialect-aware SQL predicates.

        Operator syntax (mongo-style, all values JSON-serializable):

        * Plain value: ``{"env": "production"}`` → equality.
        * Operator dict: ``{"$gt": 8}`` / ``{"$gte": ...}`` /
          ``{"$lt": ...}`` / ``{"$lte": ...}`` / ``{"$ne": ...}``
          → corresponding comparison.
        * ``{"$in": [...]}`` → IN list.
        * ``{"$contains": "..."}`` → substring match.
        * ``{"$exists": True}`` → key exists. PostgreSQL
          ``attributes ? 'key'``; SQLite uses ``json_type`` /
          ``json_extract`` to check non-NULL.
        """
        predicates: list[Any] = []
        for key, spec in attributes.items():
            if isinstance(spec, dict):
                # Operator dict.
                for op, value in spec.items():
                    if op == "$eq":
                        predicates.append(self._json_eq_predicate(column, key, value))
                    elif op == "$ne":
                        predicates.append(self._json_ineq_predicate(column, key, "!=", value))
                    elif op in ("$gt", "$gte", "$lt", "$lte"):
                        op_map = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}
                        predicates.append(
                            self._json_ineq_predicate(column, key, op_map[op], value)
                        )
                    elif op == "$in":
                        if not isinstance(value, (list, tuple)):
                            raise ValueError(
                                f"$in operator requires a list, got {type(value).__name__}"
                            )
                        predicates.append(self._json_in_predicate(column, key, list(value)))
                    elif op == "$contains":
                        predicates.append(self._json_contains_predicate(column, key, value))
                    elif op == "$exists":
                        # "Key exists". The two backends have
                        # different strengths here:
                        #
                        # * PostgreSQL JSONB has a real
                        #   "key exists" operator (``?``) that
                        #   distinguishes "key missing" from
                        #   "key present with null value".
                        # * SQLite's ``json_extract`` collapses
                        #   both into ``NULL`` — so the SQLite
                        #   branch is necessarily the
                        #   "non-NULL" check, which conflates
                        #   the two cases. This is an accepted
                        #   limitation documented in the method
                        #   docstring.
                        if self._is_postgres():
                            # The column is already cast to
                            # JSONB upstream in
                            # :meth:`search_assets` so the ``?``
                            # operator is available.
                            if value:
                                predicates.append(column.op("?")(key))
                            else:
                                predicates.append(~column.op("?")(key))
                        else:
                            path = self._json_path_text(column, key)
                            if value:
                                predicates.append(path.isnot(None))
                            else:
                                predicates.append(path.is_(None))
                    else:
                        raise ValueError(f"Unsupported operator: {op!r}")
            else:
                # Plain value = equality.
                predicates.append(self._json_eq_predicate(column, key, spec))
        return predicates

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _classify_integrity_error(e: IntegrityError) -> str:
        """Classify an :class:`IntegrityError` as ``"unique"``,
        ``"foreign_key"``, or ``"unknown"``.

        ``IntegrityError`` is raised by SQLAlchemy for *any* constraint
        violation — UNIQUE, FOREIGN KEY, CHECK, NOT NULL — not just
        UNIQUE. The previous behavior of ``create_asset`` was to
        assume UNIQUE and surface an ``"already exists"`` message,
        which was misleading for FK violations (e.g. a non-existent
        ``project_id`` or ``parent_asset_id``).

        The classification is dialect-aware:

        * PostgreSQL — uses the ``pgcode`` attribute on
          :class:`sqlalchemy.exc.DBAPIError`. ``"23505"`` is
          ``unique_violation``, ``"23503"`` is
          ``foreign_key_violation``.
        * SQLite — parses the error message. SQLite embeds the
          constraint kind as ``"UNIQUE constraint failed: ..."`` or
          ``"FOREIGN KEY constraint failed"`` in ``str(orig)``.
        * Fallback — ``"unknown"`` for dialects that expose neither
          a SQLSTATE code nor a parseable message.

        Args:
            e: The :class:`IntegrityError` to classify.

        Returns:
            One of ``"unique"``, ``"foreign_key"``, or ``"unknown"``.
        """
        orig = getattr(e, "orig", None)
        if orig is None:
            return "unknown"

        # PostgreSQL: pgcode is the SQLSTATE.
        pgcode = getattr(orig, "pgcode", None)
        if pgcode == "23505":
            return "unique"
        if pgcode == "23503":
            return "foreign_key"

        # SQLite / generic: inspect the error message. SQLite uses
        # "UNIQUE constraint failed" and "FOREIGN KEY constraint failed".
        msg = str(orig).upper()
        if "UNIQUE CONSTRAINT" in msg:
            return "unique"
        if "FOREIGN KEY" in msg:
            return "foreign_key"

        return "unknown"

    # --------------------------------------------------------
    # ASSET CRUD (with auto-history)
    # --------------------------------------------------------

    def create_asset(
        self,
        project_id: str,
        type: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        parent_asset_id: str | None = None,
        relationships: dict[str, list[str]] | None = None,
        created_by: str | None = None,
        asset_id: str | None = None,
    ) -> InfraAsset:
        """Create a new infrastructure asset.

        Args:
            project_id: Owning project ID. Must already exist
                (enforced by the FK to ``projects.project_id``).
            type: Asset type — e.g. ``"server"``,
                ``"k8s_cluster"``, ``"datacenter"``. Not
                validated against :class:`InfraAssetType`
                here; the DevOps tool layer is responsible for
                that contract.
            name: Human-readable name, unique within
                ``(project_id, type)``.
            attributes: Type-specific structured data. Defaults
                to an empty dict.
            parent_asset_id: Optional parent asset ID for
                parent/child hierarchies.
            relationships: Optional dict of
                ``{entity_type: [id, ...]}`` for cross-entity
                links. Defaults to an empty dict.
            created_by: Optional ``instance_id`` of the agent
                creating the asset, recorded on the row and on
                the ``created`` history entry.
            asset_id: Optional deterministic ID. Defaults to a
                new UUID4.

        Returns:
            The newly created :class:`InfraAsset` instance.

        Raises:
            ValueError: If the row violates a database constraint.
                The message is differentiated by constraint kind:

                * UNIQUE (project_id, type, name) duplicate →
                  ``"An asset with type=... and name=... already
                  exists in project ..."``
                * FOREIGN KEY violation (invalid ``project_id`` or
                  ``parent_asset_id``) →
                  ``"Invalid reference (project_id=... or
                  parent_asset_id=... does not exist)"``
                * Other / unknown → ``"Failed to create asset
                  (constraint violation: ...)"`` so the original
                  cause is still visible to operators.
        """
        attributes = dict(attributes) if attributes else {}
        relationships = dict(relationships) if relationships else {}
        now = self._now_iso()
        asset_id = asset_id or str(uuid.uuid4())

        with Session(self.engine) as session:
            asset = InfraAsset(
                id=asset_id,
                project_id=project_id,
                type=type,
                name=name,
                parent_asset_id=parent_asset_id,
                attributes=attributes,
                relationships=relationships,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(asset)
            try:
                session.flush()
            except IntegrityError as e:
                session.rollback()
                # Differentiate UNIQUE vs FK vs unknown — see
                # :meth:`_classify_integrity_error`. A non-existent
                # ``project_id`` or ``parent_asset_id`` is the most
                # common FK violation here; both deserve a clearer
                # message than the previous catch-all "already exists".
                kind = self._classify_integrity_error(e)
                if kind == "unique":
                    raise ValueError(
                        f"An asset with type={type!r} and "
                        f"name={name!r} already exists "
                        f"in project {project_id!r}"
                    ) from e
                if kind == "foreign_key":
                    raise ValueError(
                        f"Invalid reference (project_id={project_id!r} "
                        f"or parent_asset_id={parent_asset_id!r} "
                        f"does not exist)"
                    ) from e
                # Unknown / other constraint (CHECK, NOT NULL, ...).
                # Surface the original error so operators can debug,
                # but use a generic prefix so the tool layer can
                # safely echo this to the agent.
                raise ValueError(
                    f"Failed to create asset (constraint violation: {e})"
                ) from e

            # History row for the create.
            history = InfraAssetHistory(
                asset_id=asset.id,
                project_id=project_id,
                change_type=InfraChangeType.CREATED.value,
                snapshot=asset.to_dict(),
                changed_fields=None,
                old_values=None,
                new_values=None,
                changed_by=created_by,
                timestamp=now,
            )
            session.add(history)
            session.commit()
            session.refresh(asset)
            logger.info(
                f"Created infra asset: id={asset.id}, "
                f"project_id={project_id}, type={type}, name={name}"
            )
            return asset

    def get_asset(
        self, asset_id: str, project_id: str | None = None
    ) -> InfraAsset | None:
        """Fetch a single asset by its primary key.

        Args:
            asset_id: The asset's UUID4 ID.
            project_id: Optional project ID. When provided, the
                returned asset is verified to belong to that
                project — mismatches yield ``None`` instead of
                the asset. This lets call-sites enforce
                project isolation when they have a project
                context but should never see assets belonging
                to other projects.

        Returns:
            The :class:`InfraAsset` instance, or ``None`` if no
            row matches, or if ``project_id`` was supplied and
            the asset belongs to a different project.
        """
        with Session(self.engine) as session:
            asset = session.get(InfraAsset, asset_id)
            if asset is None:
                return None
            if project_id is not None and asset.project_id != project_id:
                return None
            return asset

    def list_assets(
        self,
        project_id: str,
        type: str | None = None,
        parent_asset_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InfraAsset]:
        """List assets for a project, with optional filters.

        Always filters on ``project_id`` — there is no
        cross-project list at the repository layer.

        Args:
            project_id: The project to list assets for.
            type: Optional type filter (exact match).
            parent_asset_id: Optional parent filter. Behavior:

                * ``None`` (default) — return ONLY unparented
                  assets (``parent_asset_id IS NULL``). This
                  is the "top level" / "roots" view.
                * A string ID — return only children whose
                  ``parent_asset_id`` matches the supplied ID.

                To list every asset regardless of parent,
                callers should use :meth:`search_assets` with
                no ``parent_asset_id`` filter (which is a no-op
                on ``search_assets`` and returns the full set),
                or supply the parent's ID to get just its
                descendants.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            List of :class:`InfraAsset` instances ordered by
            ``updated_at`` descending. Empty list if none match.
        """
        with Session(self.engine) as session:
            stmt = select(InfraAsset).where(InfraAsset.project_id == project_id)
            if type is not None:
                stmt = stmt.where(InfraAsset.type == type)
            if parent_asset_id is None:
                # None means "unparented only" — the roots of
                # the parent/child hierarchy. Callers wanting
                # the full set should use ``search_assets``
                # without a ``parent_asset_id`` filter.
                stmt = stmt.where(InfraAsset.parent_asset_id.is_(None))
            else:
                stmt = stmt.where(InfraAsset.parent_asset_id == parent_asset_id)
            stmt = (
                stmt.order_by(col(InfraAsset.updated_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def update_asset(
        self,
        asset_id: str,
        project_id: str | None = None,
        updated_by: str | None = None,
        **updates: Any,
    ) -> InfraAsset | None:
        """Update fields on an existing asset.

        Auto-records an ``updated`` history row capturing the
        full pre-update snapshot (``old_values`` for the
        changed keys only, plus ``changed_fields``).

        Args:
            asset_id: The asset to update.
            project_id: Optional project ID for project-isolation.
                When supplied, the asset is verified to belong to
                this project before the update is applied — a
                mismatch yields ``None`` (the same return as
                "asset not found") rather than mutating a
                cross-project row. C2 fix: the previous
                implementation silently operated on any asset
                regardless of project_id, which is a security
                hole.
            updated_by: Optional ``instance_id`` of the agent
                making the change; recorded on the row and on
                the history entry.
            **updates: Column values to overwrite. Allowed
                keys: ``name``, ``type``, ``parent_asset_id``,
                ``attributes``, ``relationships``. Protected
                keys (``id``, ``project_id``, ``created_at``,
                ``created_by``, ``updated_at``, ``updated_by``)
                are silently dropped with a warning. W1 fix:
                ``updated_at`` / ``updated_by`` are owned by
                the repository — ``updated_by`` is passed as
                a named argument and the timestamp is set
                internally to ``self._now_iso()``.

        Returns:
            The updated :class:`InfraAsset` instance, or
            ``None`` if no asset with that ID exists, **or** if
            ``project_id`` was supplied and the asset belongs
            to a different project.

        Raises:
            AttributeError: If any ``updates`` key is neither a
                column on :class:`InfraAsset` nor a protected
                key.
            ValueError: If the update would violate the
                ``UNIQUE(project_id, type, name)`` constraint
                (i.e. caller is renaming / retyping into a
                name that's already taken).
        """
        protected = {
            "id",
            "project_id",
            "created_at",
            "created_by",
            # W1 fix: ``updated_at`` / ``updated_by`` are owned
            # by the repository — the caller passes the
            # ``updated_by`` audit value as a NAMED arg (not via
            # ``**updates``) and the timestamp is set internally
            # to ``self._now_iso()`` after the mutation loop.
            # Silently dropping these here prevents a caller
            # from injecting a stale timestamp or spoofing the
            # audit identity on the row.
            "updated_at",
            "updated_by",
        }
        # JSON column names that need ``flag_modified`` after
        # in-place mutation (no-op when the caller replaces the
        # whole dict).
        json_columns = {"attributes", "relationships"}

        with Session(self.engine) as session:
            asset = session.get(InfraAsset, asset_id)
            if asset is None:
                logger.warning(
                    f"Infra asset not found for update: id={asset_id}"
                )
                return None
            # C2 fix: enforce project isolation. A mismatched
            # project_id must behave identically to "asset not
            # found" so callers cannot probe for asset IDs
            # belonging to other projects.
            if project_id is not None and asset.project_id != project_id:
                logger.warning(
                    f"Infra asset {asset_id} belongs to project "
                    f"{asset.project_id!r}, not {project_id!r} — "
                    f"refusing update"
                )
                return None

            # Capture the PRE-update snapshot BEFORE the
            # mutation loop runs. The history row's
            # ``snapshot`` column must reflect the asset's
            # state at the time of the change — i.e. the
            # state *before* the update applied. Reading
            # ``to_dict()`` after ``setattr`` would yield the
            # post-update state, making the snapshot
            # redundant with ``new_values`` and losing the
            # audit trail of the prior state.
            pre_update_snapshot = asset.to_dict()

            old_values: dict[str, Any] = {}
            new_values: dict[str, Any] = {}
            changed_fields: list[str] = []

            for key, value in updates.items():
                if key in protected:
                    logger.warning(
                        f"Ignoring protected field in update_asset: "
                        f"id={asset_id}, field={key}"
                    )
                    continue
                if not hasattr(asset, key):
                    raise AttributeError(
                        f"InfraAsset has no field {key!r}"
                    )
                old = getattr(asset, key)
                # Compare safely — dict / list equality.
                if old != value:
                    old_values[key] = old
                    new_values[key] = value
                    changed_fields.append(key)
                setattr(asset, key, value)
                if key in json_columns:
                    # Defensive: flag_modified is needed for in-place JSON
                    # mutation; when replacing the whole dict SQLAlchemy
                    # already detects the change via the attribute set, but
                    # we flag anyway to guard against future in-place edits
                    # (e.g. ``asset.attributes["k"] = v``) silently
                    # bypassing the change tracker.
                    flag_modified(asset, key)

            asset.updated_at = self._now_iso()
            asset.updated_by = updated_by

            if changed_fields:
                history = InfraAssetHistory(
                    asset_id=asset.id,
                    project_id=asset.project_id,
                    change_type=InfraChangeType.UPDATED.value,
                    snapshot=pre_update_snapshot,
                    changed_fields=sorted(changed_fields),
                    old_values=old_values,
                    new_values=new_values,
                    changed_by=updated_by,
                    timestamp=asset.updated_at,
                )
                session.add(history)

            try:
                session.commit()
            except IntegrityError as e:
                session.rollback()
                raise ValueError(
                    f"Update violates UNIQUE(project_id, type, name) "
                    f"constraint for asset id={asset_id}"
                ) from e
            session.refresh(asset)
            logger.info(
                f"Updated infra asset: id={asset_id}, "
                f"changed_fields={changed_fields}"
            )
            return asset

    def delete_asset(
        self,
        asset_id: str,
        project_id: str | None = None,
        deleted_by: str | None = None,
    ) -> bool:
        """Delete an asset and record a ``deleted`` history row.

        The history row is written *before* the delete so the
        ``ON DELETE SET NULL`` on the history FK does not wipe
        the audit trail. (The FK is SET NULL, not CASCADE — see
        :class:`InfraAssetHistory` — so the history row would
        survive the asset's removal regardless, but writing
        the row explicitly *before* the delete also keeps the
        ``asset_id`` column populated on the new row, which
        is what :meth:`get_history` matches against as its
        primary lookup.)

        Args:
            asset_id: The asset to delete.
            project_id: Optional project ID for project-isolation.
                When supplied, the asset is verified to belong to
                this project before the delete is applied — a
                mismatch yields ``False`` (the same return as
                "asset not found") rather than mutating a
                cross-project row. C2 fix: the previous
                implementation silently deleted any asset
                regardless of project_id, which is a security
                hole.
            deleted_by: Optional ``instance_id`` of the agent
                performing the delete.

        Returns:
            ``True`` if a row was deleted, ``False`` if no asset
            with that ID existed, **or** if ``project_id`` was
            supplied and the asset belongs to a different
            project.
        """
        with Session(self.engine) as session:
            asset = session.get(InfraAsset, asset_id)
            if asset is None:
                logger.warning(
                    f"Infra asset not found for delete: id={asset_id}"
                )
                return False
            # C2 fix: enforce project isolation on delete. A
            # mismatched project_id must behave identically to
            # "asset not found" so callers cannot delete assets
            # belonging to other projects.
            if project_id is not None and asset.project_id != project_id:
                logger.warning(
                    f"Infra asset {asset_id} belongs to project "
                    f"{asset.project_id!r}, not {project_id!r} — "
                    f"refusing delete"
                )
                return False

            snapshot = asset.to_dict()
            now = self._now_iso()

            history = InfraAssetHistory(
                asset_id=asset.id,
                project_id=asset.project_id,
                change_type=InfraChangeType.DELETED.value,
                snapshot=snapshot,
                changed_fields=None,
                old_values=None,
                new_values=None,
                changed_by=deleted_by,
                timestamp=now,
            )
            session.add(history)
            session.flush()  # Ensure history row is persisted before delete cascades.

            session.delete(asset)
            session.commit()
            logger.info(
                f"Deleted infra asset: id={asset_id}, "
                f"project_id={asset.project_id}"
            )
            return True

    # --------------------------------------------------------
    # SEARCH (dialect-aware)
    # --------------------------------------------------------

    def search_assets(
        self,
        project_id: str,
        query: dict[str, Any],
        limit: int = 50,
        offset: int = 0,
    ) -> list[InfraAsset]:
        """Search assets within a project using a structured query.

        Query keys (all optional except ``project_id`` is
        implicit in this signature):

        * ``type`` (str): exact match on the ``type`` column.
        * ``name`` (str): substring match (``LIKE '%value%'``
          on both backends).
        * ``parent_asset_id`` (str): exact match.
        * ``attributes`` (dict): JSON path filters. Values are
          either a plain value (``=`` semantics) or an
          operator dict — see
          :meth:`_build_attribute_predicates` for the full
          operator vocabulary.

        The method is project-scoped by signature; the
        ``project_id`` keyword is required.

        Args:
            project_id: The project to search within.
            query: Structured query dict.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            List of matching :class:`InfraAsset` instances
            ordered by ``updated_at`` descending. Empty list if
            none match.
        """
        with Session(self.engine) as session:
            stmt = select(InfraAsset).where(InfraAsset.project_id == project_id)

            # --- Top-level column filters ---
            if "type" in query and query["type"] is not None:
                stmt = stmt.where(InfraAsset.type == query["type"])

            if "name" in query and query["name"] is not None:
                # Substring match on the ``name`` column.
                escaped = (
                    str(query["name"])
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                stmt = stmt.where(
                    col(InfraAsset.name).ilike(f"%{escaped}%", escape="\\")
                )

            if "parent_asset_id" in query and query["parent_asset_id"] is not None:
                stmt = stmt.where(
                    InfraAsset.parent_asset_id == query["parent_asset_id"]
                )

            # --- JSONB / JSON attribute filters ---
            attributes = query.get("attributes")
            if attributes:
                if self._is_postgres():
                    # Cast the column to JSONB so the operators
                    # (``->>``, ``@>``) emit valid JSONB SQL
                    # rather than generic JSON SQL.
                    json_col = sa_cast(InfraAsset.attributes, JSONB)
                else:
                    json_col = InfraAsset.attributes
                for predicate in self._build_attribute_predicates(
                    json_col, attributes
                ):
                    stmt = stmt.where(predicate)

            stmt = (
                stmt.order_by(col(InfraAsset.updated_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    # --------------------------------------------------------
    # TYPE REGISTRY (GLOBAL, no project_id)
    # --------------------------------------------------------

    def register_type(
        self,
        name: str,
        schema_json: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> InfraAssetType:
        """Insert or update a type definition in the global registry.

        Atomic upsert: if a row with the same ``name`` already
        exists, ``description`` and ``schema_json`` are
        overwritten and ``updated_at`` is bumped; otherwise a
        new row is created.

        Args:
            name: Type identifier — also the value used in
                :attr:`InfraAsset.type`.
            schema_json: Optional JSON-Schema-shaped document.
                Stored verbatim. Defaults to an empty dict.
            description: Optional human-readable description.
                Defaults to empty string.

        Returns:
            The :class:`InfraAssetType` instance reflecting the
            post-upsert state.
        """
        now = self._now_iso()
        schema_json = dict(schema_json) if schema_json else {}

        with Session(self.engine) as session:
            existing = session.get(InfraAssetType, name)
            if existing is None:
                row = InfraAssetType(
                    name=name,
                    description=description or "",
                    schema_doc=schema_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                logger.info(f"Registered infra asset type: name={name}")
                return row

            # Update-in-place.
            if description is not None:
                existing.description = description
            existing.schema_doc = schema_json
            existing.updated_at = now
            # Defensive: flag_modified is needed for in-place JSON
            # mutation; when replacing the whole dict SQLAlchemy already
            # detects the change via the attribute set, but we flag
            # anyway to guard against future in-place edits to
            # ``existing.schema_doc`` silently bypassing the change
            # tracker.
            flag_modified(existing, "schema_doc")
            session.commit()
            session.refresh(existing)
            logger.info(f"Updated infra asset type: name={name}")
            return existing

    def get_type(self, name: str) -> InfraAssetType | None:
        """Fetch a type definition by name.

        Args:
            name: The type's primary key.

        Returns:
            The :class:`InfraAssetType` instance, or ``None``
            if no such type is registered.
        """
        with Session(self.engine) as session:
            return session.get(InfraAssetType, name)

    def list_types(self) -> list[InfraAssetType]:
        """List every registered asset type.

        Unlike asset queries, this is intentionally
        project-less — types are a global registry.

        Returns:
            List of :class:`InfraAssetType` instances ordered
            by name ascending. Empty list if none registered.
        """
        with Session(self.engine) as session:
            stmt = select(InfraAssetType).order_by(col(InfraAssetType.name).asc())
            return list(session.exec(stmt))

    def bootstrap_default_types(self) -> BootstrapResult:
        """Upsert the seed type definitions from
        :data:`INFRA_TYPE_DEFINITIONS`.

        Idempotent. Safe to call on every daemon startup. Each
        built-in type (``datacenter``, ``server``, ``rack``,
        ``k8s_cluster``, ``k8s_node``, ``network``,
        ``load_balancer``, ``database``, ``storage``) is upserted
        with its declared ``schema_doc`` and description via
        :meth:`register_type`; existing rows are updated in place
        (and ``updated_at`` is bumped) so schema changes between
        daemon versions propagate on the next startup.

        ``new_count`` and ``updated_count`` are derived by
        snapshotting the registry with :meth:`list_types` *before*
        the upsert loop runs and comparing the result row-by-row.
        The extra round-trip is acceptable here because the
        registry is tiny (9 rows) and bootstrap is a startup-only
        path.

        Returns:
            A :class:`BootstrapResult` with the touched rows and
            the new / updated counts. On a fresh database
            ``new_count`` equals ``len(INFRA_TYPE_DEFINITIONS)``;
            on subsequent startups ``new_count`` is ``0`` and
            ``updated_count`` reflects the number of schemas that
            drifted from the seed.
        """
        existing_names = {t.name for t in self.list_types()}

        registered: list[InfraAssetType] = []
        for defn in INFRA_TYPE_DEFINITIONS:
            row = self.register_type(
                name=defn.type_name,
                schema_json=defn.schema_doc,
                description=defn.description,
            )
            registered.append(row)

        new_count = sum(1 for row in registered if row.name not in existing_names)
        updated_count = len(registered) - new_count
        logger.info(
            f"Bootstrapped infra asset types: "
            f"{len(registered)} total, {new_count} new, {updated_count} updated"
        )
        return BootstrapResult(
            registered=registered,
            new_count=new_count,
            updated_count=updated_count,
        )

    # --------------------------------------------------------
    # HISTORY / VERSIONING
    # --------------------------------------------------------

    def get_history(
        self,
        asset_id: str,
        project_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[InfraAssetHistory]:
        """Return history rows for an asset, newest first.

        After an asset is deleted the ``asset_id`` column on
        its history rows is set to NULL (``ON DELETE SET NULL``
        on the FK — see :class:`InfraAssetHistory`). To make
        this method work for both live and deleted assets, it
        matches either ``asset_id = X`` or
        ``snapshot->>'id' = X`` (the snapshot preserves the
        original asset ID).

        Args:
            asset_id: The asset whose history to list. Can be
                a live asset ID or the ID of a previously
                deleted asset (the snapshot is used as the
                fallback lookup).
            project_id: Optional project ID for project-isolation.
                When supplied, only history rows whose
                ``project_id`` matches are returned. The
                ``project_id`` is denormalized on history rows,
                so this filter does not need a join. C2 fix:
                the previous implementation returned history
                for any project matching the asset_id, which
                is a security hole.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            List of :class:`InfraAssetHistory` instances
            ordered by ``timestamp`` descending. Empty list if
            no history exists, or if ``project_id`` was
            supplied and no history row matches it.
        """
        with Session(self.engine) as session:
            # Match by ``asset_id`` OR by the snapshot's
            # ``id`` field. The second branch is what makes
            # this query work for the ``deleted`` history row
            # of a now-removed asset (its ``asset_id`` is NULL
            # but the snapshot still has the original ID).
            #
            # C2 fix: the match expression is parenthesized
            # before being ANDed with the optional
            # ``project_id`` filter. Without the explicit
            # parenthesization, SQL ``AND`` has higher
            # precedence than ``OR`` and the filter would be
            # silently dropped (it would attach to the
            # snapshot branch only).
            snapshot_id_predicate = self._json_eq_predicate(
                InfraAssetHistory.snapshot, "id", asset_id
            )
            asset_match = or_(
                InfraAssetHistory.asset_id == asset_id,
                snapshot_id_predicate,
            )
            if project_id is not None:
                # C2 fix: filter history rows by project_id
                # when supplied. The ``project_id`` column on
                # ``infra_asset_history`` is denormalized from
                # the asset at write time (see
                # InfraAssetHistory model), so no join is
                # required.
                stmt = (
                    select(InfraAssetHistory)
                    .where(
                        and_(
                            asset_match,
                            InfraAssetHistory.project_id == project_id,
                        )
                    )
                    .order_by(col(InfraAssetHistory.timestamp).desc())
                    .offset(offset)
                    .limit(limit)
                )
            else:
                stmt = (
                    select(InfraAssetHistory)
                    .where(asset_match)
                    .order_by(col(InfraAssetHistory.timestamp).desc())
                    .offset(offset)
                    .limit(limit)
                )
            return list(session.exec(stmt))

    def record_change(
        self,
        asset_id: str,
        change_type: str,
        changed_fields: list[str] | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        changed_by: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> InfraAssetHistory:
        """Append an out-of-band history row.

        Most callers never invoke this — ``create_asset``,
        ``update_asset`` and ``delete_asset`` already write
        history automatically. This method is the escape hatch
        for tool-layer code that needs to record a change
        (e.g. a ``paused`` transition) without going through
        one of the asset-mutating methods.

        Args:
            asset_id: The asset this history row is about.
            change_type: One of :class:`InfraChangeType`
                values (``"created"``, ``"updated"``,
                ``"deleted"``).
            changed_fields: Optional list of field names that
                changed.
            old_values: Optional dict of pre-change values.
            new_values: Optional dict of post-change values.
            changed_by: Optional ``instance_id`` of the agent
                recording the change.
            snapshot: Optional full snapshot of the asset at
                the time of the change.

        Returns:
            The newly created :class:`InfraAssetHistory`
            instance.

        Raises:
            ValueError: If ``change_type`` is not a known value
                or if the asset does not exist.
        """
        if not InfraChangeType.is_valid(change_type):
            raise ValueError(
                f"Invalid change_type: {change_type!r}. "
                f"Must be one of {[c.value for c in InfraChangeType]}"
            )

        with Session(self.engine) as session:
            asset = session.get(InfraAsset, asset_id)
            if asset is None:
                raise ValueError(f"Infra asset not found: id={asset_id}")

            now = self._now_iso()
            history = InfraAssetHistory(
                asset_id=asset_id,
                project_id=asset.project_id,
                change_type=change_type,
                snapshot=snapshot,
                changed_fields=list(changed_fields) if changed_fields else None,
                old_values=old_values,
                new_values=new_values,
                changed_by=changed_by,
                timestamp=now,
            )
            session.add(history)
            session.commit()
            session.refresh(history)
            logger.info(
                f"Recorded infra asset change: asset_id={asset_id}, "
                f"change_type={change_type}"
            )
            return history
