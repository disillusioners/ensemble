"""Trigger embedding repository for project blueprints.

Stores (blueprint_id, query_text, embedding) rows in the existing
``project_blueprint_triggers`` table. Schema-compatible with
``SkillEmbeddingRepository`` so we do not need a new migration, but
logically independent: this repo is created whenever the blueprint
embedding model is configured, regardless of whether skill_evolution
is enabled.

See ``evolution-phase1-fixes.md`` §G4 for the design rationale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .models import BlueprintTrigger

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BlueprintEmbeddingRepository:
    """CRUD for ``project_blueprint_triggers`` rows.

    A thin, well-typed handle over the trigger table used by the
    blueprint embedding service (independent of skill_evolution). The
    canonical write boundary (:class:`~daemon.services.blueprint_write_service.BlueprintWriteService`)
    calls :meth:`replace_triggers` to atomically swap a blueprint's
    trigger vectors during the publish unit.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def replace_triggers(
        self,
        blueprint_id: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        """Atomically delete and replace all trigger rows for a blueprint.

        DELETE + INSERT runs in a single ``Session`` (transaction).
        Empty ``items`` → no INSERTs, just the DELETE (i.e. clear all
        triggers for the blueprint). This is the C4 fix for the
        ``trigger_queries=[]`` clear-all signal.
        """
        with Session(self.engine) as session:
            session.execute(
                text(
                    "DELETE FROM project_blueprint_triggers "
                    "WHERE blueprint_id = :bid"
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

    def get_triggers(self, blueprint_id: str) -> list[BlueprintTrigger]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintTrigger).where(
                        BlueprintTrigger.blueprint_id == blueprint_id
                    )
                )
            )


def create_blueprint_embedding_repository(engine: Engine) -> BlueprintEmbeddingRepository:
    """Factory matching the ``create_blueprint_repository`` convention."""
    return BlueprintEmbeddingRepository(engine=engine)
