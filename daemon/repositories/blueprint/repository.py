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
        blueprint = Blueprint(**fields)
        with Session(self.engine) as session:
            session.add(blueprint)
            session.commit()
            session.refresh(blueprint)
        return blueprint

    def update(self, blueprint_id: str, **fields: Any) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            blueprint = session.get(Blueprint, blueprint_id)
            if blueprint is None:
                return None

            if {"content", "file_refs", "tags", "trigger_queries"}.intersection(fields):
                blueprint.version += 1
            for name, value in fields.items():
                if not hasattr(blueprint, name):
                    raise ValueError(f"Unknown Blueprint field: {name}")
                setattr(blueprint, name, value)
            blueprint.updated_at = _now_iso()
            session.add(blueprint)
            session.commit()
            session.refresh(blueprint)
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

    def add_triggers(
        self,
        blueprint_id: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        with Session(self.engine) as session:
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

    def delete_triggers_by_blueprint(self, blueprint_id: str) -> int:
        with Session(self.engine) as session:
            result = session.execute(
                text(
                    "DELETE FROM project_blueprint_triggers WHERE blueprint_id = :bid"
                ),
                {"bid": blueprint_id},
            )
            session.commit()
            return result.rowcount

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
        """Load active area blueprints for a project with grouped triggers."""
        with Session(self.engine) as session:
            blueprints = list(
                session.exec(
                    select(Blueprint).where(
                        Blueprint.project_id == project_id,
                        Blueprint.kind == "area",
                        Blueprint.is_active == True,  # noqa: E712
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
