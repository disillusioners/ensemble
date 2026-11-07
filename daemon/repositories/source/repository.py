"""SQLModel-based Source Repository implementation."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, insert, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, col

from .models import (
    SourceConfig,
    InstanceMapping,
    ProcessedMessage,
    ScheduleExecution,
    ScheduleExecutionArchive,
    SourceConfigArchive,
    SourceStatus,
    ExecutionStatus,
)


logger = logging.getLogger(__name__)


# W3 (plan council, binding) — per-source-id first-warning latch for the
# archive-PK collision path (AM-3). Collisions are REACHABLE (the generic
# REST create path accepts ``source_type="scheduler"`` with a
# caller-supplied id) and PERMANENT (an archived terminal row is never
# re-collectable), so without the latch the sweep would re-WARN hourly
# ≈ 24 noise lines/day forever. FIRST collision per id → WARNING +
# counted; the SAME id on later ticks → counted SILENTLY. The latch
# resets on daemon restart (acceptable: bounded by collided ids only).
# Lives at the IntegrityError catch site (this module) per phase4 §2
# AM-3 / decisions.md D1 AM-3.
_ARCHIVE_COLLISION_WARNED: set[str] = set()

# Terminal statuses the GC may collect (phase4 §5 predicate leg 3 —
# 'error' is EXCLUDED per SP-5/LEADER RULING 3: an error row preserves
# misconfigured-but-wanted intent; next user touch rewrites status).
_GC_TERMINAL_STATUSES = ("stopped", "cancelled")


@dataclass
class GcCandidate:
    """One collectable scheduler row as selected by ``gc_find_candidates``.

    ``config_json`` is ``json.dumps()`` of the LIVE config dict and is
    the SOLE reconstruction input for a restored schedule (advisory C —
    no restore API exists in v1; update-of-missing raises). The
    one-shot|cron shape is derived at SERVICE layer from the config
    keys (``run_at`` / ``schedule`` — scheduler.py:511/:513), not here.
    """

    source_id: str
    name: str
    status: str
    updated_at: str
    age_days: float
    config_json: str


@dataclass
class GcRowResult:
    """Outcome of ONE ``gc_archive_and_delete`` attempt (per-source).

    ``skipped_reason`` values: ``not_found`` · ``non_scheduler`` ·
    ``enabled`` · ``non_terminal_status`` · ``unparseable_updated_at`` ·
    ``not_old_enough`` · ``raced`` (the advisory-A DELETE matched 0 rows
    — a concurrent resume re-stamped the row) · ``archive_id_collision``
    (AM-3: a stale archive row already holds this id). ``error`` carries
    the exception text for the collision case; all other skips leave it
    ``None``. ``dry_run=True`` + ``skipped_reason=None`` means the
    predicate re-assert PASSED and the row WOULD have been collected.
    """

    source_id: str
    deleted: bool = False
    dry_run: bool = False
    archived_config: bool = False
    archived_executions: int = 0
    deleted_mappings: int = 0
    deleted_processed: int = 0
    deleted_executions: int = 0
    skipped_reason: str | None = None
    error: str | None = None


class SQLModelSourceRepository:
    """SQLModel-based Source repository for source configs, instance mappings, and message deduplication."""

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    def _get_dialect_insert(self, session: Session):
        """Get dialect-appropriate insert function for upsert operations.

        Generic ``sqlalchemy.insert()`` does not support
        ``on_conflict_do_update()`` — that method is dialect-specific. This
        helper returns the dialect-specific insert callable so the caller can
        chain ``on_conflict_do_update`` for both SQLite and PostgreSQL.

        Args:
            session: SQLAlchemy Session whose bound engine determines dialect.

        Returns:
            Dialect-specific insert callable. Both ``sqlite`` and
            ``postgresql`` dialect inserts support ``on_conflict_do_update``.
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert
        return sqlite_insert

    # ==================== Source Config Operations ====================

    def create_source_config(
        self,
        source_type: str,
        name: str,
        config: dict[str, Any],
        credentials: str | None = None,
        enabled: bool = True,
        autostart: bool = True,
        source_id: str | None = None,
    ) -> SourceConfig:
        """Create a new source configuration.

        Concurrency note (M11):
            This is an INSERT of a brand-new row. The ``config`` JSON/JSONB
            column is written exactly once, with the full dict supplied by the
            caller. There is no prior row to read first, so this path is
            **full-replace by design and safe** — no read-modify-write race.
            Per-key atomic updates of an existing config (e.g. the internal
            ``_run_counter`` bookkeeping field) go through
            ``increment_scheduler_run_counter``, which uses dialect-aware
            ``jsonb_set`` / ``json_set`` with ``RETURNING``.
        """
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            source_id = source_id or str(uuid.uuid4())
            
            source_config = SourceConfig(
                source_id=source_id,
                source_type=source_type,
                name=name,
                config=config,
                credentials=credentials,
                enabled=enabled,
                autostart=autostart,
                status=SourceStatus.STOPPED.value,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
            
            session.add(source_config)
            session.commit()
            session.refresh(source_config)
            
            logger.info(f"Created source config: source_id={source_id}, name={name}")
            return source_config

    def update_source_config(
        self,
        source_id: str,
        source_type: str | None = None,
        name: str | None = None,
        config: dict[str, Any | None] = None,
        credentials: str | None = None,
        enabled: bool | None = None,
        autostart: bool | None = None,
    ) -> SourceConfig | None:
        """Update a source configuration.

        Concurrency note (M11):
            The ``config`` field is **always written as a full replace**:
            ``source_config.config = config`` assigns the supplied dict
            wholesale inside the SQLAlchemy UPDATE. Callers are expected to
            build and pass a complete config object — there is no partial
            key-level merge at this layer, so the JSON/JSONB column is never
            subject to a read-modify-write race in this repository method.
            If a future caller needs to mutate a single nested key, route
            through the ``increment_scheduler_run_counter``-style dialect-aware
            ``jsonb_set`` / ``json_set`` ``RETURNING`` pattern rather than
            reintroducing a Python read-modify-write here.
        """
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                return None
            
            if source_type is not None:
                source_config.source_type = source_type
            if name is not None:
                source_config.name = name
            if config is not None:
                source_config.config = config
            if credentials is not None:
                source_config.credentials = credentials
            if enabled is not None:
                source_config.enabled = enabled
            if autostart is not None:
                source_config.autostart = autostart
            
            source_config.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            session.refresh(source_config)
            
            logger.info(f"Updated source config: source_id={source_id}")
            return source_config

    def increment_scheduler_run_counter(self, source_id: str) -> int | None:
        """Atomically increment and return the scheduler run counter for a source.

        The counter is stored in the source's config field (_run_counter) so it persists
        even if sessions crash. Initializes to 0 if not present.

        Uses atomic SQL with a dialect-aware JSON update and RETURNING to avoid
        race conditions on both SQLite (``json_set``/``json_extract``) and
        PostgreSQL (``jsonb_set``/``->>``).

        Args:
            source_id: The source ID to increment the counter for.

        Returns:
            The new counter value, or None if the source was not found.
        """
        with Session(self.engine) as session:
            # Dialect-aware atomic update of the JSON ``_run_counter`` field.
            # SQLite uses json_set/json_extract; PostgreSQL uses jsonb_set/->>.
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                update_sql = text("""
                    UPDATE source_configs
                    SET config = jsonb_set(
                        COALESCE(config, '{}'::jsonb),
                        '{_run_counter}',
                        to_jsonb(
                            COALESCE((config->>'_run_counter')::int, 0) + 1
                        )
                    ),
                    updated_at = :updated_at
                    WHERE source_id = :source_id
                    RETURNING (config->>'_run_counter')::int AS counter
                """)
            else:
                update_sql = text("""
                    UPDATE source_configs
                    SET config = json_set(
                        COALESCE(config, '{}'),
                        '$._run_counter',
                        COALESCE(CAST(json_extract(config, '$._run_counter') AS INTEGER), 0) + 1
                    ),
                    updated_at = :updated_at
                    WHERE source_id = :source_id
                    RETURNING CAST(json_extract(config, '$._run_counter') AS INTEGER) as counter
                """)
            result = session.execute(
                update_sql,
                {"source_id": source_id, "updated_at": datetime.now(timezone.utc).isoformat()},
            ).fetchone()

            session.commit()

            if result is None:
                logger.warning(f"Source not found for run counter increment: source_id={source_id}")
                return None

            new_counter = result[0]
            logger.debug(f"Incremented run counter: source_id={source_id}, new_value={new_counter}")
            return new_counter

    def get_source_config(self, source_id: str) -> SourceConfig | None:
        """Get a source configuration by source_id."""
        with Session(self.engine) as session:
            return session.get(SourceConfig, source_id)

    # ------------------------------------------------------------------
    # Scheduler deferred-delivery episode state (commission
    # ``scheduler-deferred-alarm-delivery``; plan §(c) C0 / decisions.md
    # DEC-9, DEC-12, DEC-19, DEC-22).
    # ------------------------------------------------------------------
    #
    # The episode carrier rides the ``source_configs.config`` JSON/JSONB
    # column as ATOMIC siblings of ``_run_counter`` (the same
    # dialect-aware ``jsonb_set`` / ``json_set`` + ``RETURNING`` pattern as
    # ``increment_scheduler_run_counter`` above):
    #
    #   ``_defer_gen``        — the generation number (canonical spelling;
    #                            the architect's ``_deferred_gen`` is
    #                            SUPERSEDED)
    #   ``_defer_message_id`` — the live delivery's ``message_id`` (the
    #                            forward-carried coalescing anchor — the
    #                            predecessor is NEVER matched by
    #                            idempotency key)
    #   ``_defer_count``      — the occurrences coalesced into the current
    #                            generation
    #   ``_defer_since``      — the NON-DELIVERY episode start (set-if-
    #                            absent; the 24h age cap's clock)
    #
    # NEVER a Python read-modify-write: ``update_source_config`` replaces
    # the whole ``config`` dict (its docstring forbids per-key mutation),
    # so a per-key update MUST go through this method.

    def apply_scheduler_defer_state(
        self,
        source_id: str,
        *,
        count_delta: int = 0,
        count_cap: int = 16,
        gen_delta: int = 0,
        set_message_id: str | None = None,
        set_since: str | None = None,
        reset_count: bool = False,
        clear_since: bool = False,
        clear_message_id: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically apply the scheduler deferral-episode state mutation.

        ONE dialect-aware statement (``UPDATE ... RETURNING``) — never a
        Python read-modify-write.  The clearing parameters are IN the
        signature on purpose: set-if-absent alone can OPEN an episode but
        never CLOSE one, so a recurring schedule would re-trip the age cap
        forever (plan §(c) C0, "should-fix d").

        Args:
            source_id: The scheduler row's source id.
            count_delta: Occurrences to add to ``_defer_count`` (capped at
                ``count_cap``; ``GREATEST``/``MAX`` floor 0).
            count_cap: The saturation cap for ``_defer_count`` (the
                ``SchedulingConfig.defer_max_count`` value).
            gen_delta: Generations to add to ``_defer_gen``.
            set_message_id: Set ``_defer_message_id`` to this value.
            set_since: Set ``_defer_since`` — SET-IF-ABSENT (``COALESCE``
                inside the statement, so a plain overwrite would reset the
                age each cycle and the age cap would never trip).
            reset_count: Reset ``_defer_count`` to 0 before applying
                ``count_delta`` (the generation-rollover reset).
            clear_since: Clear ``_defer_since`` (the episode ends).
            clear_message_id: Clear ``_defer_message_id``.

            Precedence: when both are given, ``set_message_id`` wins over
            ``clear_message_id`` and ``set_since`` wins over
            ``clear_since``.

        Returns:
            The updated ``config`` dict, or ``None`` when the row was not
            found.
        """
        params: dict[str, Any] = {
            "source_id": source_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count_delta": int(count_delta),
            "count_cap": int(count_cap),
            "gen_delta": int(gen_delta),
        }

        # ---- the fragments (dialect-specific expressions) ---------------
        gen_fragment = (
            "COALESCE(CAST(json_extract(config, '$._defer_gen') AS INTEGER), 0) + :gen_delta"
        )
        count_fragment = (
            "MIN(:count_cap, MAX(0, COALESCE(CAST(json_extract(config, '$._defer_count') "
            "AS INTEGER), 0) + :count_delta))"
        )
        if reset_count:
            count_fragment = "MIN(:count_cap, MAX(0, :count_delta))"

        with Session(self.engine) as session:
            is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

            if is_pg:
                # PostgreSQL: ``jsonb_set`` on a JSONB column.  The merge
                # operator (``||``) replaces the WHOLE value of each key
                # (the keys are scalars, so a deep merge is not needed).
                #
                # Every bound parameter carries an EXPLICIT cast: PG cannot
                # resolve the type of an uncast bind parameter inside
                # ``LEAST`` / ``GREATEST`` / ``jsonb_build_object``
                # ("could not determine polymorphic type because input has
                # type unknown").
                pieces: list[str] = []
                if gen_delta:
                    pieces.append(
                        "'_defer_gen', to_jsonb(COALESCE((config->>'_defer_gen')::int, 0) "
                        "+ CAST(:gen_delta AS int))"
                    )
                if reset_count or count_delta:
                    if reset_count:
                        pg_count = (
                            "LEAST(CAST(:count_cap AS int), "
                            "GREATEST(0, CAST(:count_delta AS int)))"
                        )
                    else:
                        pg_count = (
                            "LEAST(CAST(:count_cap AS int), GREATEST(0, "
                            "COALESCE((config->>'_defer_count')::int, 0) "
                            "+ CAST(:count_delta AS int)))"
                        )
                    pieces.append(f"'_defer_count', to_jsonb({pg_count})")
                if set_message_id is not None:
                    pieces.append(
                        "'_defer_message_id', to_jsonb(CAST(:set_message_id AS text))"
                    )
                elif clear_message_id:
                    pieces.append("'_defer_message_id', NULL::jsonb")
                if set_since is not None:
                    # SET-IF-ABSENT (COALESCE) — a plain overwrite would
                    # reset the age on every cycle and the cap would never
                    # trip (decisions.md DEC-22).
                    pieces.append(
                        "'_defer_since', to_jsonb(COALESCE(config->>'_defer_since', "
                        "CAST(:set_since AS text)))"
                    )
                elif clear_since:
                    pieces.append("'_defer_since', NULL::jsonb")
                if not pieces:
                    # Nothing to mutate — read the row back unchanged.
                    row = session.get(SourceConfig, source_id)
                    return dict(row.config or {}) if row is not None else None
                update_sql = text(
                    "UPDATE source_configs "
                    "SET config = COALESCE(config, '{}'::jsonb) || jsonb_build_object("
                    + ", ".join(pieces)
                    + "), updated_at = :updated_at "
                    "WHERE source_id = :source_id "
                    "RETURNING config"
                )
            else:
                # SQLite: ``json_set`` with one path/value pair per key.
                pieces = []
                if gen_delta:
                    pieces.append(f"'$._defer_gen', {gen_fragment}")
                if reset_count or count_delta:
                    pieces.append(f"'$._defer_count', {count_fragment}")
                if set_message_id is not None:
                    pieces.append("'$._defer_message_id', :set_message_id")
                elif clear_message_id:
                    pieces.append("'$._defer_message_id', NULL")
                if set_since is not None:
                    pieces.append(
                        "'$._defer_since', COALESCE(json_extract(config, '$._defer_since'), :set_since)"
                    )
                elif clear_since:
                    pieces.append("'$._defer_since', NULL")
                if not pieces:
                    row = session.get(SourceConfig, source_id)
                    return dict(row.config or {}) if row is not None else None
                update_sql = text(
                    "UPDATE source_configs "
                    "SET config = json_set(COALESCE(config, '{}'), "
                    + ", ".join(pieces)
                    + "), updated_at = :updated_at "
                    "WHERE source_id = :source_id "
                    "RETURNING config"
                )

            if set_message_id is not None:
                params["set_message_id"] = set_message_id
            if set_since is not None:
                params["set_since"] = set_since

            result = session.execute(update_sql, params).fetchone()
            session.commit()

            if result is None:
                logger.warning(
                    "Source not found for scheduler defer-state update: source_id=%s",
                    source_id,
                )
                return None

            new_config = result[0]
            if isinstance(new_config, str):
                import json as _json

                new_config = _json.loads(new_config)
            return dict(new_config or {})

    def get_source_config_by_name(self, name: str) -> SourceConfig | None:
        """Get a source configuration by name."""
        with Session(self.engine) as session:
            stmt = select(SourceConfig).where(SourceConfig.name == name)
            return session.exec(stmt).first()

    def list_source_configs(
        self,
        enabled: bool | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceConfig]:
        """List source configurations with optional filters."""
        with Session(self.engine) as session:
            stmt = select(SourceConfig)
            
            if enabled is not None:
                stmt = stmt.where(SourceConfig.enabled == enabled)
            if status is not None:
                stmt = stmt.where(SourceConfig.status == status)
            
            stmt = stmt.order_by(col(SourceConfig.created_at).desc()).offset(offset).limit(limit)
            return list(session.exec(stmt))

    def update_source_status(
        self,
        source_id: str,
        status: str,
        error_message: str | None = None,
    ) -> SourceConfig | None:
        """Update the status of a source configuration."""
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(f"Source not found for status update: source_id={source_id}")
                return None

            if not SourceStatus.is_valid(status):
                raise ValueError(f"Invalid status: {status}")

            source_config.status = status
            source_config.error_message = error_message
            source_config.updated_at = datetime.now(timezone.utc).isoformat()

            session.commit()
            session.refresh(source_config)

            logger.info(f"Updated source status: source_id={source_id}, status={status}")
            return source_config

    def cancel_source_config(self, source_id: str) -> SourceConfig | None:
        """Atomically transition a source to terminal CANCELLED state (ADR-007).

        Writes ``enabled=False`` AND ``status='cancelled'`` in ONE session /
        commit. Replaces the previous plan's two-call sequence
        (``update_source_config`` + ``update_source_status`` were independent
        sessions, see architecture §3.2) — the atomic method honors the
        plan's own "same transaction" contract honestly.

        Cancel is terminal — the row cannot be re-armed via ``start`` (the
        boot filter at ``registry.py:292`` skips ``status in {STOPPED,
        CANCELLED}``). Cancel NEVER routes through ``delete_source_config``
        (which would purge ``schedule_executions`` history at
        ``repository.py:281-284``).

        Returns:
            The updated SourceConfig row, or ``None`` if the source_id
            does not exist.
        """
        if not SourceStatus.is_valid(SourceStatus.CANCELLED.value):
            # Defensive: invariant guard so a future rename of the enum
            # value fails loud at the cancel site rather than silently
            # writing an invalid status.
            raise RuntimeError(
                "SourceStatus.CANCELLED is not a valid enum value; "
                "the cancel path is broken (architecture §3.2 invariant)."
            )
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(
                    f"Source not found for cancel: source_id={source_id}"
                )
                return None

            source_config.enabled = False
            source_config.status = SourceStatus.CANCELLED.value
            source_config.updated_at = datetime.now(timezone.utc).isoformat()

            session.commit()
            session.refresh(source_config)

            logger.info(
                f"Atomically cancelled source: source_id={source_id}, "
                f"status={source_config.status}, enabled={source_config.enabled}"
            )
            return source_config

    def delete_source_config(self, source_id: str) -> dict[str, Any]:
        """Delete a source configuration and all associated mappings."""
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(f"Source config not found for deletion: source_id={source_id}")
                return {"deleted": False, "source_id": source_id, "error": "Not found"}
            
            # Delete all mappings for this source
            session.exec(
                sql_delete(InstanceMapping).where(InstanceMapping.source_id == source_id)
            )
            
            # Delete processed messages for this source
            session.exec(
                sql_delete(ProcessedMessage).where(ProcessedMessage.source_id == source_id)
            )
            
            # Delete schedule executions for this source
            session.exec(
                sql_delete(ScheduleExecution).where(ScheduleExecution.schedule_id == source_id)
            )
            
            # Delete source config
            session.delete(source_config)
            session.commit()
            
            logger.info(f"Deleted source config and associated data: source_id={source_id}")
            return {
                "deleted": True,
                "source_id": source_id,
                "name": source_config.name
            }

    # ==================== Source-GC Operations ====================

    @staticmethod
    def _gc_parse_age_seconds(
        updated_at: str | None, now: datetime
    ) -> float | None:
        """Parse a ``updated_at`` ISO stamp into an age in seconds.

        Returns ``None`` when the stamp is missing or unparseable — the
        caller SKIPS + counts (fail-MISS: never delete on unprovable
        age). A naive parsed stamp is attached UTC under the documented
        assume-UTC policy (``daemon/services/timestamps.py`` doctrine).
        """
        if not updated_at:
            return None
        try:
            parsed = datetime.fromisoformat(str(updated_at))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = now - parsed
        return age.total_seconds()

    def gc_find_candidates(
        self,
        *,
        retention_days: int,
        now: datetime,
        limit: int,
    ) -> tuple[list[GcCandidate], int]:
        """Select collectable scheduler rows for one GC tick.

        Predicate (phase4 §5 — single source of truth, repo layer):
        ``source_type == 'scheduler' AND enabled == False AND status IN
        ('stopped','cancelled') AND age(now - fromisoformat(updated_at))
        > retention_days``. The type/enabled/status legs prefilter in
        SQL; the AGE leg evaluates in Python against the parsed
        ``updated_at`` (103-row scale). Unparseable/missing age →
        SKIP + count (fail-MISS). ``ORDER BY updated_at ASC`` (AM-2):
        oldest-first, so the batch cap defers the NEWEST rows
        deterministically.

        Args:
            retention_days: Age threshold in days. Re-checked >= 1
                here (the config knob is fail-fast ``ge=1``; the repo
                does not trust its callers).
            now: Timezone-aware reference instant for the age math.
            limit: Batch cap for this tick (``>= 1``).

        Returns:
            ``(candidates, skipped_unparseable)`` — at most ``limit``
            candidates, oldest ``updated_at`` first, plus the number of
            prefiltered rows whose age could not be proven.
        """
        retention_days = max(1, int(retention_days))
        limit = max(1, int(limit))
        with Session(self.engine) as session:
            stmt = (
                select(SourceConfig)
                .where(SourceConfig.source_type == "scheduler")
                .where(SourceConfig.enabled == False)  # noqa: E712 — SQL prefilter leg
                .where(
                    col(SourceConfig.status).in_(_GC_TERMINAL_STATUSES)
                )
                .order_by(col(SourceConfig.updated_at).asc())
                .limit(limit)
            )
            rows = list(session.exec(stmt))

        candidates: list[GcCandidate] = []
        skipped_unparseable = 0
        for row in rows:
            age_seconds = self._gc_parse_age_seconds(row.updated_at, now)
            if age_seconds is None:
                skipped_unparseable += 1
                continue
            if age_seconds <= retention_days * 86400.0:
                # Within retention — not collectable. SQL cannot do
                # this leg soundly (ISO TEXT comparison), so rows that
                # fail it are simply dropped here (not "skips" — they
                # are not GC-eligible yet).
                continue
            candidates.append(
                GcCandidate(
                    source_id=row.source_id,
                    name=row.name,
                    status=row.status,
                    updated_at=row.updated_at,
                    age_days=age_seconds / 86400.0,
                    config_json=json.dumps(row.config or {}),
                )
            )
        return candidates, skipped_unparseable

    def gc_count_error_rows(self) -> int:
        """Count scheduler rows parked at ``status='error'`` (SP-5/AM-7).

        Cheap ``COUNT(*)`` telemetry for the sweep report — the
        phase-9.3-planner revisit trigger reads this instead of
        folklore. Error rows are NEVER collected (excluded by the
        terminal-status leg); this counter only OBSERVES them.
        """
        with Session(self.engine) as session:
            stmt = (
                select(SourceConfig)
                .where(SourceConfig.source_type == "scheduler")
                .where(SourceConfig.status == SourceStatus.ERROR.value)
            )
            return len(list(session.exec(stmt)))

    def gc_archive_and_delete(
        self,
        *,
        source_id: str,
        now: datetime,
        retention_days: int = 14,
        dry_run: bool = False,
    ) -> GcRowResult:
        """Archive + delete ONE scheduler source row (ONE transaction).

        NEVER trusts the earlier ``gc_find_candidates`` SELECT (AM-1
        critical): inside the single transaction the row is re-read
        under ``with_for_update`` (house idiom,
        ``instance/repository.py:1988-1992``; renders no lock on SQLite
        per :1949-1954) and ALL FOUR predicate legs are re-asserted
        from the locked row — source_type, ``enabled == False``,
        terminal status, and the AGE re-parsed from the FRESH
        ``updated_at``. A resume that landed between candidacy and this
        call re-stamps ``updated_at`` and/or flips ``enabled``; the
        re-assert (plus the advisory-A ``updated_at`` pin on the final
        DELETE, whose 0-rowcount ⇒ raced-skip) makes such a row
        skipped+counted instead of silently deleted.

        Archive INSERTs are PLAIN and fail-loud (AM-3 — NEVER
        ``ON CONFLICT``): a colliding id means a STALE archive row for
        a re-created source, and absorbing the fresh snapshot silently
        would be audit corruption. ``IntegrityError`` → rollback →
        FIRST collision per source_id warns loudly via the
        ``_ARCHIVE_COLLISION_WARNED`` latch, later ticks for the same
        id count silently (W3) → skipped, sweep continues.

        Deletes run child-first (``delete_source_config`` :522-534
        parity): instance_mappings → processed_messages (parity no-op
        today — scheduler sources produce zero rows; kept for parity)
        → schedule_executions → source_config. The final DELETE
        additionally pins ``enabled = false AND status IN (terminal)
        AND updated_at = :locked_value`` (advisory A) — rowcount 0 ⇒
        raced-skip ⇒ rollback, NOTHING archived.

        Not-found → skipped, never raise. ``dry_run=True`` performs the
        full locked re-assert and reports the outcome WITHOUT writing
        (``deleted=False`` + ``dry_run=True`` + ``skipped_reason=None``
        = the row WOULD be collected).

        Signature note (recorded in the lane implementation-notes): the
        plan's binding signature is ``(*, source_id, now, dry_run)``,
        but its own AM-1 four-leg re-assert includes the AGE leg
        (P16's "drop a leg → RED" mutation needs it to have teeth), and
        an age check needs a retention threshold the signature does not
        carry. ``retention_days`` is therefore an ADDITIVE keyword with
        the shipped default (14) as the fail-closed fallback — the
        service always passes the live knob; plan-signature callers
        keep working and still get the age leg at production-default
        strength.
        """
        result = GcRowResult(source_id=source_id)
        with Session(self.engine) as session:
            locked = session.get(SourceConfig, source_id, with_for_update=True)
            if locked is None:
                result.skipped_reason = "not_found"
                return result

            # --- AM-1: re-assert ALL FOUR legs from the locked row ---
            if locked.source_type != "scheduler":
                result.skipped_reason = "non_scheduler"
                return result
            if locked.enabled:
                result.skipped_reason = "enabled"
                return result
            if locked.status not in _GC_TERMINAL_STATUSES:
                result.skipped_reason = "non_terminal_status"
                return result
            retention_days = max(1, int(retention_days))
            age_seconds = self._gc_parse_age_seconds(locked.updated_at, now)
            if age_seconds is None:
                result.skipped_reason = "unparseable_updated_at"
                return result
            if age_seconds <= retention_days * 86400.0:
                result.skipped_reason = "not_old_enough"
                return result

            if dry_run:
                result.dry_run = True
                return result

            # --- Archive INSERTs: PLAIN, fail-loud (AM-3) ---
            executions = list(
                session.exec(
                    select(ScheduleExecution).where(
                        ScheduleExecution.schedule_id == source_id
                    )
                )
            )
            try:
                session.add(
                    SourceConfigArchive(
                        source_id=locked.source_id,
                        source_type=locked.source_type,
                        name=locked.name,
                        config=json.dumps(locked.config or {}),
                        enabled=bool(locked.enabled),
                        autostart=bool(locked.autostart),
                        status=locked.status,
                        error_message=locked.error_message,
                        created_at=locked.created_at,
                        updated_at=locked.updated_at,
                        archived_at=now.isoformat(),
                    )
                )
                for execution in executions:
                    session.add(
                        ScheduleExecutionArchive(
                            execution_id=execution.execution_id,
                            schedule_id=execution.schedule_id,
                            triggered_at=execution.triggered_at,
                            instance_id=execution.instance_id,
                            status=execution.status,
                            error_message=execution.error_message,
                            completed_at=execution.completed_at,
                            archived_at=now.isoformat(),
                        )
                    )
                session.flush()
            except IntegrityError as collision_err:
                session.rollback()
                result.skipped_reason = "archive_id_collision"
                result.error = str(collision_err)
                if source_id not in _ARCHIVE_COLLISION_WARNED:
                    _ARCHIVE_COLLISION_WARNED.add(source_id)
                    logger.warning(
                        f"[SourcesGC] archive id collision — stale archive "
                        f"row for a re-created source: source_id={source_id} "
                        f"(re-create with a FRESH id or purge the stale "
                        f"archive row); skipped"
                    )
                # Same id on a later tick: counted silently (no log line).
                return result

            # --- Child-first deletes (delete_source_config parity) ---
            result.deleted_mappings = session.exec(
                sql_delete(InstanceMapping).where(
                    InstanceMapping.source_id == source_id
                )
            ).rowcount
            result.deleted_processed = session.exec(
                sql_delete(ProcessedMessage).where(
                    ProcessedMessage.source_id == source_id
                )
            ).rowcount
            result.deleted_executions = session.exec(
                sql_delete(ScheduleExecution).where(
                    ScheduleExecution.schedule_id == source_id
                )
            ).rowcount

            # --- Final DELETE with the advisory-A pinned WHERE ---
            delete_stmt = sql_delete(SourceConfig).where(
                SourceConfig.source_id == source_id,
                SourceConfig.enabled == False,  # noqa: E712 — predicate pin
                col(SourceConfig.status).in_(_GC_TERMINAL_STATUSES),
                SourceConfig.updated_at == locked.updated_at,
            )
            deleted_count = session.exec(delete_stmt).rowcount
            if not deleted_count:
                # Raced-skip: a concurrent writer re-stamped the row
                # between the locked read and this DELETE (closes the
                # SQLite micro-window — with_for_update renders no lock
                # there). Roll back; archive NOTHING.
                session.rollback()
                result.skipped_reason = "raced"
                return result

            session.commit()

            result.deleted = True
            result.archived_config = True
            result.archived_executions = len(executions)
            logger.info(
                f"[SourcesGC] collected source: source_id={source_id} "
                f"executions={len(executions)} "
                f"mappings={result.deleted_mappings}"
            )
            return result

    # ==================== Instance Mapping Operations ====================

    def create_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
        agent_instance_id: str,
        agent_id: str,
        agent_dir: str,
        metadata: dict[str, Any | None] = None,
        mapping_id: str | None = None,
    ) -> InstanceMapping:
        """Create or update an instance mapping (atomic dialect-aware upsert).

        Replaces the previous SELECT-then-INSERT/UPDATE pattern that produced
        duplicate mappings under concurrent first-message access from the same
        external user. We rely on the dialect-aware
        ``INSERT ... ON CONFLICT(source_id, external_user_id) DO UPDATE``,
        which is a single atomic round trip and is enforced by the
        ``uq_instance_mappings_source_user`` unique constraint added on the
        model.
        """
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            mapping_id = mapping_id or str(uuid.uuid4())
            metadata_payload = metadata or {}

            insert_fn = self._get_dialect_insert(session)
            stmt = insert_fn(InstanceMapping).values(
                mapping_id=mapping_id,
                source_id=source_id,
                external_user_id=external_user_id,
                agent_instance_id=agent_instance_id,
                agent_id=agent_id,
                agent_dir=agent_dir,
                mapping_metadata=metadata_payload,
                last_message_at=now,
                created_at=now,
            ).on_conflict_do_update(
                index_elements=["source_id", "external_user_id"],
                set_={
                    "agent_instance_id": agent_instance_id,
                    "agent_id": agent_id,
                    "agent_dir": agent_dir,
                    "mapping_metadata": metadata_payload,
                    "last_message_at": now,
                },
            )
            session.execute(stmt)
            session.commit()

            mapping = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == source_id,
                    InstanceMapping.external_user_id == external_user_id,
                )
            ).first()
            logger.info(
                f"Upserted instance mapping: source_id={source_id}, "
                f"external_user_id={external_user_id}, mapping_id={mapping.mapping_id if mapping else mapping_id}"
            )
            return mapping

    def get_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
    ) -> InstanceMapping | None:
        """Get an instance mapping by source_id and external_user_id."""
        with Session(self.engine) as session:
            stmt = select(InstanceMapping).where(
                InstanceMapping.source_id == source_id,
                InstanceMapping.external_user_id == external_user_id
            )
            return session.exec(stmt).first()

    def get_instance_mapping_by_instance(
        self,
        agent_instance_id: str,
    ) -> InstanceMapping | None:
        """Get an instance mapping by agent_instance_id."""
        with Session(self.engine) as session:
            stmt = select(InstanceMapping).where(
                InstanceMapping.agent_instance_id == agent_instance_id
            )
            return session.exec(stmt).first()

    def update_mapping_last_message(
        self,
        source_id: str,
        external_user_id: str,
    ) -> bool:
        """Update the last_message_at timestamp for an instance mapping."""
        with Session(self.engine) as session:
            mapping = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == source_id,
                    InstanceMapping.external_user_id == external_user_id
                )
            ).first()
            if mapping is None:
                return False
            
            mapping.last_message_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            
            logger.debug(
                f"Updated last_message_at: source_id={source_id}, external_user_id={external_user_id}"
            )
            return True

    def delete_instance_mapping(self, mapping_id: str) -> dict[str, Any]:
        """Delete an instance mapping."""
        with Session(self.engine) as session:
            mapping = session.get(InstanceMapping, mapping_id)
            if mapping is None:
                logger.warning(f"Instance mapping not found for deletion: mapping_id={mapping_id}")
                return {"deleted": False, "mapping_id": mapping_id, "error": "Not found"}
            
            session.delete(mapping)
            session.commit()
            
            logger.info(f"Deleted instance mapping: mapping_id={mapping_id}")
            return {"deleted": True, "mapping_id": mapping_id}

    def list_instance_mappings(
        self,
        source_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InstanceMapping]:
        """List all instance mappings for a source."""
        with Session(self.engine) as session:
            stmt = (
                select(InstanceMapping)
                .where(InstanceMapping.source_id == source_id)
                .order_by(col(InstanceMapping.last_message_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def cleanup_inactive_mappings(
        self,
        max_age_days: int = 30,
    ) -> int:
        """Clean up inactive instance mappings older than max_age_days."""
        with Session(self.engine) as session:
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            cutoff_str = cutoff_time.isoformat()
            
            # Find inactive mappings
            stmt = select(InstanceMapping).where(
                ((InstanceMapping.last_message_at == None) & (InstanceMapping.created_at < cutoff_str))
                | ((InstanceMapping.last_message_at != None) & (InstanceMapping.last_message_at < cutoff_str))
            )
            inactive_mappings = list(session.exec(stmt))
            
            # Delete them
            for mapping in inactive_mappings:
                session.delete(mapping)
            
            session.commit()
            
            deleted_count = len(inactive_mappings)
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} inactive instance mappings older than {max_age_days}d")
            
            return deleted_count

    # ==================== Deduplication Operations ====================

    def check_and_mark_processed(
        self,
        source_id: str,
        external_message_id: str,
    ) -> bool:
        """Check if a message has already been processed and mark it as processed.
        
        Uses atomic check-and-insert with UNIQUE constraint.
        
        Note: This is an atomic operation - if INSERT succeeds, the message
        is marked as processed. If the caller fails to handle the message,
        it will be permanently dropped. Callers must handle their own errors
        before calling this if different behavior is needed.
        
        Returns:
            True if message was already processed (duplicate).
            False if this is a new message (and now marked as processed).
        """
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            
            processed = ProcessedMessage(
                source_id=source_id,
                external_message_id=external_message_id,
                processed_at=now,
            )
            
            try:
                session.add(processed)
                session.commit()
                return False  # New message, not a duplicate
            except IntegrityError:
                # Unique constraint violation - message already exists
                session.rollback()
                logger.debug(
                    f"Duplicate message detected: source_id={source_id}, "
                    f"external_message_id={external_message_id}"
                )
                return True  # Duplicate

    def cleanup_old_processed_messages(
        self,
        max_age_hours: int = 24,
    ) -> int:
        """Clean up processed messages older than max_age_hours."""
        with Session(self.engine) as session:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            cutoff_str = cutoff_time.isoformat()
            
            # Find messages to delete first
            count_stmt = select(ProcessedMessage).where(
                ProcessedMessage.processed_at < cutoff_str
            )
            messages_to_delete = list(session.exec(count_stmt))
            deleted_count = len(messages_to_delete)
            
            # Delete them
            stmt = sql_delete(ProcessedMessage).where(
                ProcessedMessage.processed_at < cutoff_str
            )
            session.exec(stmt)
            session.commit()
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} processed messages older than {max_age_hours}h")
            
            return deleted_count

    # ==================== Schedule Execution Operations ====================

    def record_execution_start(
        self,
        schedule_id: str,
        instance_id: str | None = None,
        execution_id: str | None = None,
    ) -> ScheduleExecution:
        """Record a new execution with status 'triggered'.
        
        Args:
            schedule_id: The schedule that triggered this execution
            instance_id: Optional instance ID associated with the execution
            execution_id: Optional execution ID (generated if not provided)
        """
        with Session(self.engine) as session:
            execution = ScheduleExecution(
                schedule_id=schedule_id,
                instance_id=instance_id,
                status=ExecutionStatus.TRIGGERED.value,
            )
            
            # Use provided execution_id if available
            if execution_id:
                execution.execution_id = execution_id
            
            session.add(execution)
            session.commit()
            session.refresh(execution)
            
            logger.info(f"Recorded execution start: execution_id={execution.execution_id}, schedule_id={schedule_id}")
            return execution

    def record_execution_complete(
        self,
        execution_id: str,
        status: str = ExecutionStatus.COMPLETED.value,
        error_message: str | None = None,
    ) -> ScheduleExecution | None:
        """Update execution status to completed or failed."""
        if not ExecutionStatus.is_valid(status):
            raise ValueError(f"Invalid execution status: {status}. Must be one of: {', '.join(s.value for s in ExecutionStatus)}")

        with Session(self.engine) as session:
            execution = session.get(ScheduleExecution, execution_id)
            if execution is None:
                logger.warning(f"Execution not found for update: execution_id={execution_id}")
                return None
            
            execution.status = status
            execution.error_message = error_message
            execution.completed_at = datetime.now(timezone.utc).isoformat()
            
            session.commit()
            session.refresh(execution)
            
            logger.info(f"Recorded execution completion: execution_id={execution_id}, status={status}")
            return execution

    def list_schedule_executions(
        self,
        schedule_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScheduleExecution]:
        """List executions for a schedule."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .order_by(col(ScheduleExecution.triggered_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def get_latest_execution(self, schedule_id: str) -> ScheduleExecution | None:
        """Get the most recent execution for a schedule."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .order_by(col(ScheduleExecution.triggered_at).desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def get_running_executions(self, schedule_id: str) -> list[ScheduleExecution]:
        """Get currently running executions (status='triggered' but not completed)."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(
                    ScheduleExecution.schedule_id == schedule_id,
                    ScheduleExecution.status == ExecutionStatus.TRIGGERED.value,
                )
                .order_by(col(ScheduleExecution.triggered_at).desc())
            )
            return list(session.exec(stmt))
