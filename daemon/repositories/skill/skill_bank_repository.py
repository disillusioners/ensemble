"""SQLModel-based repository for the ``skill_bank`` table.

The Skill Bank is an isolated, user-facing CRUD store — NOT part
of the Skill Evolution System. There is no FK to the ``skills``
table, no lineage, no embeddings, no triggers, no usage records.
The repository is a thin synchronous wrapper that the API router
(Phase 2) invokes via ``asyncio.to_thread``.

Methods:
    create: Insert a new SkillBankItem row.
    get: Fetch a single row by primary key.
    list_items: List rows, optionally filtered by project_id and/or
        category, ordered by created_at DESC.
    update: Apply field updates and bump updated_at.
    delete: Hard-delete the row.
    count: Return the row count for the given filters.

All methods are synchronous; callers bridge to async via
``asyncio.to_thread`` (the project's standard pattern).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import SkillBankItem

logger = logging.getLogger(__name__)


# Module-level ISO-timestamp helper. Mirrors the
# :func:`daemon.repositories.skill.repository._now_iso` pattern —
# repository update paths need an explicit stamp; model
# ``default_factory`` lambdas use the in-models module's helper.
def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class SkillBankRepository:
    """SQLModel-based repository for the ``skill_bank`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
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

    def create(
        self,
        name: str,
        content: str,
        project_id: Optional[str] = None,
        description: str = "",
        category: str = "workflow",
        template_version: str = "1.0.0",
        agent_id: Optional[str] = None,
        auto_load: bool = False,
    ) -> SkillBankItem:
        """Insert a new SkillBankItem row.

        Args:
            name: Human-readable skill name.
            content: The skill body (markdown / instructions).
            project_id: Owning project ID, or ``None`` for a
                global skill.
            description: One-line summary (default empty).
            category: Free-form category string (default
                ``'workflow'``).
            template_version: Semver version of this template
                (default ``'1.0.0'``). Bumped by the seeding
                pipeline when the source skill-set.md file
                changes so stale bank copies can be refreshed.
            agent_id: Agent this template belongs to (e.g.
                ``'tester'``). ``None`` means a generic/shared
                template (default ``None``).
            auto_load: Whether skills cloned from this template
                should be loaded into the system prompt before
                every task. ``False`` = on-demand only (default).

        Returns:
            The newly created :class:`SkillBankItem` instance.
        """
        now = _now_iso()
        item = SkillBankItem(
            name=name,
            content=content,
            project_id=project_id,
            description=description,
            category=category,
            template_version=template_version,
            agent_id=agent_id,
            auto_load=auto_load,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session:
            session.add(item)
            session.commit()
            session.refresh(item)
            logger.info(
                f"Created skill bank item: id={item.id}, name={name}, "
                f"project_id={project_id}, agent_id={agent_id}, "
                f"auto_load={auto_load}"
            )
            return item

    def get(self, item_id: str) -> SkillBankItem | None:
        """Fetch a single item by its primary key.

        Args:
            item_id: The item's UUID4 ID.

        Returns:
            The :class:`SkillBankItem` instance, or ``None`` if no
            row matches.
        """
        with Session(self.engine) as session:
            return session.get(SkillBankItem, item_id)

    def list_items(
        self,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillBankItem]:
        """List items, optionally filtered by project and/or category.

        Args:
            project_id: Owning project ID to filter by. ``None``
                means no project filter — rows are returned across
                all projects, including global rows with
                ``project_id IS NULL``.
            category: Filter by category string. ``None`` means
                no category filter.
            limit: Maximum number of rows to return (default ``100``).
            offset: Number of rows to skip.

        Returns:
            List of :class:`SkillBankItem` instances ordered by
            ``created_at`` descending. Empty list when no rows match.
        """
        with Session(self.engine) as session:
            stmt = select(SkillBankItem)
            if project_id is not None:
                stmt = stmt.where(SkillBankItem.project_id == project_id)
            if category is not None:
                stmt = stmt.where(SkillBankItem.category == category)
            stmt = (
                stmt.order_by(col(SkillBankItem.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def update(self, item_id: str, **fields: Any) -> SkillBankItem | None:
        """Update fields on an existing item.

        Protected keys (``id``, ``created_at``) are silently
        dropped. ``updated_at`` is owned by the repository and
        bumped to the current time before commit.

        Args:
            item_id: The item to update.
            **fields: Column values to overwrite. Any unknown key
                that isn't a column on :class:`SkillBankItem`
                raises ``AttributeError``.

        Returns:
            The updated :class:`SkillBankItem` instance, or
            ``None`` if no row with that ID exists.
        """
        protected = {"id", "created_at"}
        with Session(self.engine) as session:
            item = session.get(SkillBankItem, item_id)
            if item is None:
                logger.warning(
                    f"Skill bank item not found for update: id={item_id}"
                )
                return None
            for key, value in fields.items():
                if key in protected:
                    logger.warning(
                        f"Ignoring protected field in skill bank item "
                        f"update: id={item_id}, field={key}"
                    )
                    continue
                if not hasattr(item, key):
                    raise AttributeError(
                        f"SkillBankItem has no field {key!r}"
                    )
                setattr(item, key, value)
            item.updated_at = _now_iso()
            session.commit()
            session.refresh(item)
            logger.info(f"Updated skill bank item: id={item_id}")
            return item

    def delete(self, item_id: str) -> bool:
        """Hard-delete an item row.

        Args:
            item_id: The item to delete.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row
            with that ID existed.
        """
        with Session(self.engine) as session:
            item = session.get(SkillBankItem, item_id)
            if item is None:
                logger.warning(
                    f"Skill bank item not found for delete: id={item_id}"
                )
                return False
            session.delete(item)
            session.commit()
            logger.info(f"Deleted skill bank item: id={item_id}")
            return True

    def get_by_name_and_agent(
        self,
        name: str,
        agent_id: str,
    ) -> SkillBankItem | None:
        """Fetch a skill bank template by ``(name, agent_id)``.

        Used by the seeding pipeline to look up the existing copy of
        a template before deciding whether to refresh it (Phase 3 of
        tester-skill-evolution).

        Args:
            name: Human-readable skill name.
            agent_id: Owning agent ID (e.g. ``'tester'``).

        Returns:
            The matching :class:`SkillBankItem`, or ``None`` when
            no row exists for the pair. When multiple rows match
            the same ``(name, agent_id)`` (the bank intentionally
            permits duplicates across and within agents), the
            specific row returned is implementation-defined —
            callers needing a deterministic choice should add an
            explicit ``order_by`` to a custom query.
        """
        with Session(self.engine) as session:
            stmt = (
                select(SkillBankItem)
                .where(SkillBankItem.name == name)
                .where(SkillBankItem.agent_id == agent_id)
            )
            return session.exec(stmt).first()

    def get_auto_load_by_agent(
        self,
        agent_id: str,
    ) -> list[SkillBankItem]:
        """Fetch all ``auto_load=True`` templates for an agent.

        Used by the clone-on-miss path (Phase 4 of
        tester-skill-evolution) to clone foundational skills into
        project scope before the first spawn. The set is filtered
        to ``auto_load=True`` so on-demand templates are excluded
        from the auto-clone batch — they only land in a project
        when explicitly requested.

        Args:
            agent_id: Owning agent ID (e.g. ``'tester'``).

        Returns:
            List of :class:`SkillBankItem` rows for the agent with
            ``auto_load=True``. Empty list when no such templates
            exist (or no templates at all).
        """
        with Session(self.engine) as session:
            stmt = (
                select(SkillBankItem)
                .where(SkillBankItem.agent_id == agent_id)
                .where(SkillBankItem.auto_load == True)  # noqa: E712
            )
            return list(session.exec(stmt))

    def list_by_agent(
        self,
        agent_id: str,
    ) -> list[SkillBankItem]:
        """Fetch all templates for an agent (every ``auto_load`` value).

        Used by inspection / debugging endpoints where the caller
        wants the full per-agent template set, not just the
        auto-clone subset returned by
        :meth:`get_auto_load_by_agent`.

        Args:
            agent_id: Owning agent ID (e.g. ``'tester'``).

        Returns:
            List of :class:`SkillBankItem` rows for the agent.
            Empty list when no templates exist for the agent.
        """
        with Session(self.engine) as session:
            stmt = select(SkillBankItem).where(
                SkillBankItem.agent_id == agent_id
            )
            return list(session.exec(stmt))

    def count(
        self,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> int:
        """Count items matching the given filters.

        Args:
            project_id: Owning project ID to filter by. ``None``
                means no project filter — rows are counted across
                all projects, including global rows with
                ``project_id IS NULL``.
            category: Filter by category string. ``None`` means
                no category filter.

        Returns:
            Integer row count matching the filters.
        """
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(SkillBankItem)
            if project_id is not None:
                stmt = stmt.where(SkillBankItem.project_id == project_id)
            if category is not None:
                stmt = stmt.where(SkillBankItem.category == category)
            return int(session.scalar(stmt) or 0)
