"""Synchronous repository for the Project Blueprint subsystem."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import Blueprint, BlueprintRevision, BlueprintTrigger

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BlueprintRepository:
    """SQLModel-based repository for project blueprints and related rows."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def get_by_id(self, blueprint_id: str) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            return session.get(Blueprint, blueprint_id)

    def get_by_slug(self, project_id: str, slug: str) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            return session.exec(
                select(Blueprint).where(
                    Blueprint.project_id == project_id,
                    Blueprint.slug == slug,
                )
            ).first()

    def get_core(self, project_id: str) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            return session.exec(
                select(Blueprint).where(
                    Blueprint.project_id == project_id,
                    Blueprint.kind == "core",
                    Blueprint.is_active == True,  # noqa: E712
                )
            ).first()

    def auto_dedup_cores(self, project_id: str) -> int:
        """G7 pre-flight (C6): if multiple active cores exist for a project,
        keep the most recent (highest version, latest updated_at) and
        soft-disable the rest (``is_active = False``). Returns the count
        of cores soft-disabled.

        Run BEFORE creating the one-core-per-project partial unique
        index. The DB-level index is the PRIMARY enforcement
        mechanism; this app-level dedup is a one-time cleanup so the
        index can be created against existing duplicates.
        """
        with Session(self.engine) as session:
            cores = list(
                session.exec(
                    select(Blueprint)
                    .where(Blueprint.project_id == project_id)
                    .where(Blueprint.kind == "core")
                    .where(Blueprint.is_active == True)  # noqa: E712
                    .order_by(
                        col(Blueprint.version).desc(),
                        col(Blueprint.updated_at).desc(),
                    )
                )
            )
            if len(cores) <= 1:
                return 0

            kept = cores[0]
            disabled = 0
            for core in cores[1:]:
                core.is_active = False
                logger.info(
                    "G7 auto-dedup: soft-disabling duplicate core %s for "
                    "project %s (kept %s)",
                    core.id, project_id, kept.id,
                )
                session.add(core)
                disabled += 1
            session.commit()
        return disabled

    def list_by_project(
        self,
        project_id: str,
        kind: Optional[str] = None,
        active_only: bool = True,
    ) -> list[Blueprint]:
        with Session(self.engine) as session:
            statement = select(Blueprint).where(Blueprint.project_id == project_id)
            if kind is not None:
                statement = statement.where(Blueprint.kind == kind)
            if active_only:
                statement = statement.where(Blueprint.is_active == True)  # noqa: E712
            return list(session.exec(statement))

    def create(self, **fields: Any) -> Blueprint:
        # G7 UX guard (C6): if a caller is creating a ``core``
        # blueprint for a project that already has an active core,
        # raise a friendly ValueError BEFORE the DB-level partial
        # unique index fires. The DB constraint is the primary
        # enforcement mechanism; this is convenience only.
        if fields.get("kind") == "core":
            existing = self.get_core(fields["project_id"])
            if existing is not None:
                raise ValueError(
                    f"Project {fields['project_id']} already has a core "
                    f"blueprint (id={existing.id}). "
                    "Only one core per project is allowed."
                )
        blueprint = Blueprint(**fields)
        with Session(self.engine) as session:
            session.add(blueprint)
            session.commit()
            session.refresh(blueprint)
        return blueprint

    def update(
        self,
        blueprint_id: str,
        reason: str | None = None,
        **fields: Any,
    ) -> Optional[Blueprint]:
        """Update a blueprint; capture a revision snapshot post-commit.

        C4 fix 3 (G2): ``reason`` is a revision metadata field, NOT a
        ``Blueprint`` field. It is extracted from kwargs BEFORE the
        setattr loop so the setattr validation does not raise
        ``ValueError: Unknown Blueprint field: reason``.

        C4 fix 2: ``trigger_queries=[]`` is a valid clear-all signal and
        is passed through to the setattr loop. The caller
        (:class:`~daemon.services.blueprint_write_service.BlueprintWriteService`)
        has already distinguished ``[]`` (clear) from ``None`` (no-op).

        G2: after the update session commits, an append-only revision
        snapshot is captured in its OWN session (post-commit).

        W1: Revision capture runs POST-COMMIT in a SEPARATE session.
        A crash between the content commit and the revision INSERT
        leaves a version bump without a corresponding audit row. This
        is an accepted trade-off (C8: revision failure must never roll
        back the update). Same-transaction capture would require a
        schema migration to share the session and is deferred to a
        future phase. Only capture when the version actually incremented
        (content/file_refs/tags/trigger_queries changed) so metadata-only
        updates don't grow the audit table.
        """
        # Pop reason FIRST so it is not in ``fields`` when we setattr.
        reason = fields.pop("reason", None) if reason is None else reason

        with Session(self.engine) as session:
            blueprint = session.get(Blueprint, blueprint_id)
            if blueprint is None:
                return None

            version_incremented = False
            if {"content", "file_refs", "tags", "trigger_queries"}.intersection(fields):
                blueprint.version += 1
                version_incremented = True
            for name, value in fields.items():
                if not hasattr(blueprint, name):
                    raise ValueError(f"Unknown Blueprint field: {name}")
                setattr(blueprint, name, value)
            blueprint.updated_at = _now_iso()
            session.add(blueprint)
            session.commit()
            session.refresh(blueprint)

        # Capture revision snapshot OUTSIDE the update session (C8: never
        # roll back the update on revision failure). Only capture when the
        # version actually incremented (content/file_refs/tags/trigger_queries
        # changed) so metadata-only updates don't grow the audit table.
        if version_incremented:
            try:
                self.add_revision(
                    blueprint_id=blueprint.id,
                    version=blueprint.version,
                    content_snapshot=blueprint.content,
                    source=blueprint.source,
                    file_refs=list(blueprint.file_refs or []),
                    tags=list(blueprint.tags or []),
                    trigger_queries=list(blueprint.trigger_queries or []),
                    reason=reason,
                )
            except Exception as e:
                logger.error(
                    "add_revision failed for blueprint %s v%d: %s",
                    blueprint_id, blueprint.version, e, exc_info=True,
                )

        return blueprint

    def soft_delete(self, blueprint_id: str) -> bool:
        with Session(self.engine) as session:
            blueprint = session.get(Blueprint, blueprint_id)
            if blueprint is None:
                return False
            blueprint.is_active = False
            blueprint.updated_at = _now_iso()
            session.add(blueprint)
            session.commit()
        return True

    def get_triggers_by_blueprint(
        self,
        blueprint_id: str,
    ) -> list[BlueprintTrigger]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintTrigger).where(
                        BlueprintTrigger.blueprint_id == blueprint_id
                    )
                )
            )

    def replace_triggers(
        self,
        blueprint_id: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        with Session(self.engine) as session:
            session.execute(
                text(
                    "DELETE FROM project_blueprint_triggers WHERE blueprint_id = :bid"
                ),
                {"bid": blueprint_id},
            )
            for query_text, embedding in items:
                session.add(
                    BlueprintTrigger(
                        blueprint_id=blueprint_id,
                        query_text=query_text,
                        embedding=list(embedding),
                    )
                )
            session.commit()
        return len(items)

    def add_revision(self, **fields: Any) -> BlueprintRevision:
        revision = BlueprintRevision(**fields)
        with Session(self.engine) as session:
            session.add(revision)
            session.commit()
            session.refresh(revision)
        return revision

    def list_revisions(
        self,
        blueprint_id: str,
        limit: int = 50,
    ) -> list[BlueprintRevision]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintRevision)
                    .where(BlueprintRevision.blueprint_id == blueprint_id)
                    .order_by(col(BlueprintRevision.version).desc())
                    .limit(limit)
                )
            )

    def search_candidates(
        self,
        project_id: str,
    ) -> list[tuple[Blueprint, list[BlueprintTrigger]]]:
        """Load active area blueprints for a project with grouped triggers.

        G8: only ``status == "published"`` blueprints are matchable.
        Drafts (``status == "draft"``) are excluded from the matcher
        load so a half-written blueprint never reaches the agent's
        context. The hardcoded ``"published"`` filter matches the
        :attr:`BlueprintConfig.matchable_statuses` default; the config
        option is reserved for future flexibility (e.g. ``"review"``
        for phased rollouts).
        """
        with Session(self.engine) as session:
            blueprints = list(
                session.exec(
                    select(Blueprint).where(
                        Blueprint.project_id == project_id,
                        Blueprint.kind == "area",
                        Blueprint.is_active == True,  # noqa: E712
                        Blueprint.status == "published",  # G8: drafts NOT matchable
                    )
                )
            )
            if not blueprints:
                return []

            blueprint_ids = [blueprint.id for blueprint in blueprints]
            triggers = list(
                session.exec(
                    select(BlueprintTrigger).where(
                        col(BlueprintTrigger.blueprint_id).in_(blueprint_ids)
                    )
                )
            )

        by_blueprint: dict[str, list[BlueprintTrigger]] = {
            blueprint.id: [] for blueprint in blueprints
        }
        for trigger in triggers:
            if trigger.blueprint_id in by_blueprint:
                by_blueprint[trigger.blueprint_id].append(trigger)
        return [(blueprint, by_blueprint[blueprint.id]) for blueprint in blueprints]
