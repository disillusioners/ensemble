"""Sync repository for the custom-agent config store.

Agent-prompt-immutability Phase C — User Directive #5A (2026-10-14,
overnight, R-A1/R-A2/B1-B4 lifecycle ruling). The repo wraps ONE
SQLModel table — :class:`CustomAgentConfig` — and exposes the
narrow surface that the rest of the daemon needs:

* :meth:`get_by_agent_id` — registry resolution lookups (the
  hybrid registry calls this on every discovery sweep);
* :meth:`list_active` — the post-filter listing for the
  operator-facing API;
* :meth:`list_all` — raw listing (incl. soft-deleted rows; the
  registry uses this for sweeping soft-deleted entries off the
  discoverable set);
* :meth:`insert_config` — the creation-side primitive (the router
  composes this into a wider atomic transaction that also writes
  the v1 prompt_versions rows — see ``routers/agents.py``);
* :meth:`soft_delete` — the R-A2 lifecycle trash path;
* :meth:`restore` — the morning-fork future restore primitive
  (conservative: keep rows, mark config deleted — the restore is
  a one-column UPDATE).

Engine-only constructor (per the corpus convention for
SQLModel-backed repos). Every mutator takes an ``actor``
(:class:`ActorContext` — shared with the prompt-version repo so
HTTP-lane identity is identical) and stamps an audit row inside
the same ``engine.begin()`` block. The prompt_versions audit
events land inside the wider transaction the router opens.

The custom-agent creation flow uses BOTH repos in one
transaction:

    with engine.begin() as conn:
        repo.insert_config(conn, ...)       # this module
        pv_repo.create_draft(conn, ...)    # prompt_versions
        conn.execute(audit_events)         # both tables

That single-txn write is the R-A1 invariant: the config row + v1
versions + audit rows all commit atomically or roll back
together. A half-written creation is impossible.

**Identity** — see :class:`CustomAgentConfig` and
:data:`CUSTOM_AGENT_ID_PREFIX`. Customs carry the wire form
``"custom:<user-chosen-name>"`` in BOTH this table and the
``prompt_versions.agent_id`` column (same string, no
double-wrapping). The wire form is provably disjoint from
file-defined ids because ``_parse_agent_dir_name`` would have
rejected any dir containing ``:``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, select

from .models import (
    CUSTOM_AGENT_ID_PREFIX,
    CustomAgentConfig,
    is_custom_agent_id,
    make_custom_agent_id,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Local mirror of the model helper (kept as a module function so
    the repo can stamp timestamps without circular imports).
    """
    return datetime.now(timezone.utc).isoformat()


# ActorContext re-export — the repository's mutating methods take
# the same :class:`ActorContext` shape as the prompt-version repo.
# Re-exported here so callers need only one import line.
try:  # pragma: no cover — defensive re-export
    from daemon.repositories.prompt_version.repository import (
        ActorContext,
        actor_from_request,
    )
    _actor_import_ok = True
except Exception:  # noqa: BLE001
    ActorContext = None  # type: ignore[assignment]
    actor_from_request = None  # type: ignore[assignment]
    _actor_import_ok = False


# ============================================================
# Custom errors
# ============================================================


class CustomAgentIdError(ValueError):
    """Raised when a custom-agent id fails validation.

    Used by the router's guard rails to map to a 400 INVALID_REQUEST
    with the actionable message. The class never escapes the
    internal surface — the HTTP boundary maps to a typed
    ErrorResponse.
    """


class CustomAgentConfigNotFound(LookupError):
    """Raised when ``get_by_agent_id`` finds no config row.

    Distinct from a connection error (which the engine raises); used
    by the registry to signal "DB has no such agent" so the registry
    can fall through to its file-scan path.
    """


class CustomAgentCollisionError(ValueError):
    """Raised when an insert would clash with a predefined agent.

    The R-A2 lifecycle rule: a custom agent's wire identity
    (``"custom:foo"``) is provably disjoint from any file-defined
    id, so the wire form itself never collides — but a USER
    requesting ``name="custom:other"`` (with the prefix already
    baked in) WOULD collide semantically (same wire form
    ``"custom:custom:other"``). This class catches the
    caller-side misuse case (a router that fails to strip a
    submitted prefix before validation) and surfaces the
    actionable message.
    """


# ============================================================
# Repository
# ============================================================


@dataclass
class CustomAgentConfigRepository:
    """Sync repository for :class:`CustomAgentConfig`.

    Engine-only constructor; per-method short-lived Sessions. The
    mutator methods accept either a ``Connection`` (preferred,
    lets the router compose into a wider transaction) or fall back
    to ``engine.begin()``.

    Attributes:
        _engine: SQLAlchemy engine bound to the project's
            database (SQLite or PostgreSQL). The repo holds NO
            engine-creating logic — engine-only by corpus
            convention (research-findings-db-api.md §1).
    """

    _engine: Engine

    # ----------------------------------------------------------------
    # Mappers — model ↔ AgentMetadata-shaped view
    # ----------------------------------------------------------------

    @staticmethod
    def to_metadata_shape(row: CustomAgentConfig) -> dict[str, Any]:
        """Convert a SQLModel row to the dict shape the registry expects.

        The :class:`AgentRegistry` consumes ``AgentMetadata``
        pydantic models; for the custom-agent path the simplest
        contract is a dict that the registry unpacks into its
        ``AgentMetadata(**kwargs)`` constructor.

        The ``path`` field is left unset — the registry's
        ``discover_custom_agents`` stamps the
        ``<custom-agent>/<id>`` sentinel directly so callers
        have a single source of truth for the sentinel form.
        The ``system`` field is FORCED 0 — customs are non-system.
        The ``tools`` field is set to a real :class:`ToolFilter`
        so the registry's ``validate_tool_configs`` + the
        runtime tool-resolution code can consume the same
        shape used for ``meta.json``-derived agents (single
        code path, different source).

        Args:
            row: A :class:`CustomAgentConfig` row.

        Returns:
            A kwargs dict ready for ``AgentMetadata(**kwargs)``
            (with the registry's ``path`` substitution).
        """
        from daemon.registry import ToolFilter

        tools = ToolFilter(
            allow=list(row.tools_allow or []),
            deny=list(row.tools_deny or []),
            deny_spawn=list(row.tools_deny_spawn or []),
        ) if (
            row.tools_allow or row.tools_deny or row.tools_deny_spawn
        ) else None

        return {
            "id": row.agent_id,
            "name": row.name or row.agent_id,
            "description": row.description,
            "icon": row.icon,
            "color": row.color,
            "path": None,  # sentinel set by discover_custom_agents
            "system": 0,  # FORCED non-system on this path
            "capabilities": list(row.capabilities or []),
            "tags": [],
            "tools": tools,
            "innate_skills": list(row.innate_skills or []),
            "llm_model": row.llm_model,
            "llm_models": list(row.llm_models or []),
            "team_members": list(row.team_members or []),
            "skill_injection": int(row.skill_injection or 0),
            "snapshot_enabled": int(row.snapshot_enabled or 0),
            "skill_search_interval": int(row.skill_search_interval or 1),
            "blueprint_inactive": int(row.blueprint_inactive or 0),
            "inject_allowed_models": int(
                row.inject_allowed_models or 0
            ),
            "recursion_limit_multiplier": float(
                row.recursion_limit_multiplier or 1.0
            ),
            "recursion_limit": (
                int(row.recursion_limit)
                if row.recursion_limit is not None
                else None
            ),
            "context_injection_heuristic_match_shared_md_files": int(
                row.context_injection_heuristic_match_shared_md_files
                or 0
            ),
            "version_tag": "",
            "is_custom": True,
            "created_by": row.created_by,
        }

    # ----------------------------------------------------------------
    # Read paths
    # ----------------------------------------------------------------

    def get_by_agent_id(
        self,
        agent_id: str,
        *,
        include_deleted: bool = False,
    ) -> Optional[CustomAgentConfig]:
        """Look up a single config row by its wire identity.

        Returns ``None`` when no row matches. ``include_deleted``
        defaults to ``False`` so the registry's resolution path
        naturally hides soft-deleted rows; lifecycle trash can
        use ``include_deleted=True`` to surface the tombstone.

        Args:
            agent_id: Wire identity (e.g. ``"custom:myhelper"``).
            include_deleted: When ``True``, also return rows whose
                ``deleted_at`` is non-null.

        Returns:
            The matching :class:`CustomAgentConfig` row, or
            ``None`` when no row matches.
        """
        stmt = select(CustomAgentConfig).where(
            col(CustomAgentConfig.agent_id) == agent_id
        )
        if not include_deleted:
            stmt = stmt.where(
                col(CustomAgentConfig.deleted_at).is_(None)
            )
        with Session(self._engine) as session:
            return session.exec(stmt).first()

    def list_active(self) -> Sequence[CustomAgentConfig]:
        """Return all LIVE (non-deleted) configs in creation order.

        Used by the registry's hybrid discovery sweep.

        Returns:
            Sequence of live :class:`CustomAgentConfig` rows
            (ordered by ``created_at`` ascending so the discovery
            list is stable; alphabetical sort belongs on the
            caller side).
        """
        with Session(self._engine) as session:
            stmt = (
                select(CustomAgentConfig)
                .where(col(CustomAgentConfig.deleted_at).is_(None))
                .order_by(col(CustomAgentConfig.created_at).asc())
            )
            return list(session.exec(stmt).all())

    def list_all(self) -> Sequence[CustomAgentConfig]:
        """Return ALL configs incl. soft-deleted.

        Used by the audit / graveyard surface; the registry only
        surfaces active rows to the operator-facing API.

        Returns:
            Sequence of all :class:`CustomAgentConfig` rows
            (incl. soft-deleted) ordered by ``created_at``
            ascending.
        """
        with Session(self._engine) as session:
            stmt = select(CustomAgentConfig).order_by(
                col(CustomAgentConfig.created_at).asc()
            )
            return list(session.exec(stmt).all())

    # ----------------------------------------------------------------
    # Mutator (insert)
    # ----------------------------------------------------------------

    def insert_config(
        self,
        *,
        agent_id: str,
        name: str,
        description: str = "",
        icon: str = "🤖",
        color: str = "accent-blue",
        tools_allow: Optional[list[str]] = None,
        tools_deny: Optional[list[str]] = None,
        tools_deny_spawn: Optional[list[str]] = None,
        llm_model: Optional[str] = None,
        llm_models: Optional[list[dict]] = None,
        team_members: Optional[list[str]] = None,
        innate_skills: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
        skill_injection: int = 0,
        snapshot_enabled: int = 0,
        blueprint_inactive: int = 0,
        inject_allowed_models: int = 0,
        recursion_limit_multiplier: float = 1.0,
        recursion_limit: Optional[int] = None,
        context_injection_heuristic_match_shared_md_files: int = 0,
        created_by: str = "",
        created_auth_method: str = "session",
        conn: Optional[Connection] = None,
    ) -> CustomAgentConfig:
        """Insert a new :class:`CustomAgentConfig` row.

        Used by the router's creation endpoint. The method takes
        an OPTIONAL ``conn`` argument — when supplied, the insert
        runs inside the caller's transaction (the router composes
        this with the ``prompt_versions`` v1 inserts into ONE
        atomic block). When ``None``, the method opens its own
        ``engine.begin()`` block (used by tests + ad-hoc admin).

        Args:
            agent_id: Wire identity (MUST be
                ``CUSTOM_AGENT_ID_PREFIX``-prefixed; the router
                builds it via :func:`make_custom_agent_id`).
            name: Display name (required; non-empty).
            description: Free-form description.
            icon: UI icon (emoji or token).
            color: UI color tag.
            tools_allow: Allow-list for tools / categories.
            tools_deny: Deny-list for tools / categories.
            tools_deny_spawn: Deny-spawn list (per
                :class:`ToolFilter`).
            llm_model: Optional default model override.
            llm_models: List of weighted model entries.
            team_members: Spawn-eligible agent ids.
            innate_skills: Innate skill names.
            capabilities: Capability tags.
            skill_injection: 0/1 — opt-in for skill auto-search.
            snapshot_enabled: 0/1 — Agent Snapshot warm-start.
            blueprint_inactive: 0/1 — hide from blueprints.
            inject_allowed_models: 0/1 — model-list injection.
            recursion_limit_multiplier: Default 1.0.
            recursion_limit: Optional explicit recursion limit.
            context_injection_heuristic_match_shared_md_files:
                0/1 — shared-context heuristic opt-in.
            created_by: HTTP AuthContext.subject of the creator.
                Empty string is allowed (testing fallback) but
                the router enforces non-empty in production.
            created_auth_method: 'session' (default) / 'bearer'.
                'internal' is REJECTED here — same S16 anti-forgery
                as the prompt-version repo's ``actor_from_request``.
            conn: Optional ``Connection`` (live transaction). When
                ``None``, the method opens ``engine.begin()``.

        Returns:
            The freshly inserted :class:`CustomAgentConfig` row.

        Raises:
            CustomAgentIdError: On invalid wire identity.
            CustomAgentCollisionError: On a caller-side prefix
                misuse (``user_name`` already carrying the prefix).
            IntegrityError: When a row with the same ``agent_id``
                already exists (the router catches this for the
                409 INVALID_REQUEST shape).
            ValueError: On 'internal' auth-method (defensive
                parity with ``actor_from_request``).
        """
        if not is_custom_agent_id(agent_id):
            raise CustomAgentIdError(
                f"agent_id must carry the {CUSTOM_AGENT_ID_PREFIX!r} "
                f"prefix (R-A2 wire form); got {agent_id!r}"
            )
        if created_auth_method == "internal":
            raise ValueError(
                "created_auth_method='internal' is reserved for "
                "actor_from_instance; refuse to mint custom-agent "
                "rows from internal lane (S16 anti-forgery)."
            )
        if not name:
            raise ValueError(
                f"name must be a non-empty string; got {name!r}"
            )

        now = _now_iso()
        row = CustomAgentConfig(
            config_id=str(uuid.uuid4()),
            agent_id=agent_id,
            name=name,
            description=description,
            icon=icon,
            color=color,
            system=0,  # FORCED non-system on this path
            tools_allow=list(tools_allow or []),
            tools_deny=list(tools_deny or []),
            tools_deny_spawn=list(tools_deny_spawn or []),
            llm_model=llm_model,
            llm_models=list(llm_models or []),
            team_members=list(team_members or []),
            innate_skills=list(innate_skills or []),
            capabilities=list(capabilities or []),
            skill_injection=int(skill_injection or 0),
            snapshot_enabled=int(snapshot_enabled or 0),
            skill_search_interval=1,
            blueprint_inactive=int(blueprint_inactive or 0),
            inject_allowed_models=int(inject_allowed_models or 0),
            recursion_limit_multiplier=float(
                recursion_limit_multiplier or 1.0
            ),
            recursion_limit=(
                int(recursion_limit)
                if recursion_limit is not None
                else None
            ),
            context_injection_heuristic_match_shared_md_files=int(
                context_injection_heuristic_match_shared_md_files
                or 0
            ),
            version_tag="",
            created_by=created_by or "",
            created_auth_method=created_auth_method or "session",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )

        def _do_insert(c: Connection) -> None:
            c.execute(
                CustomAgentConfig.__table__.insert().values(
                    config_id=row.config_id,
                    agent_id=row.agent_id,
                    name=row.name,
                    description=row.description,
                    icon=row.icon,
                    color=row.color,
                    system=row.system,
                    tools_allow=row.tools_allow,
                    tools_deny=row.tools_deny,
                    tools_deny_spawn=row.tools_deny_spawn,
                    llm_model=row.llm_model,
                    llm_models=row.llm_models,
                    team_members=row.team_members,
                    innate_skills=row.innate_skills,
                    capabilities=row.capabilities,
                    skill_injection=row.skill_injection,
                    snapshot_enabled=row.snapshot_enabled,
                    skill_search_interval=row.skill_search_interval,
                    blueprint_inactive=row.blueprint_inactive,
                    inject_allowed_models=row.inject_allowed_models,
                    recursion_limit_multiplier=(
                        row.recursion_limit_multiplier
                    ),
                    recursion_limit=row.recursion_limit,
                    context_injection_heuristic_match_shared_md_files=(
                        row.context_injection_heuristic_match_shared_md_files
                    ),
                    version_tag=row.version_tag,
                    created_by=row.created_by,
                    created_auth_method=row.created_auth_method,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    deleted_at=row.deleted_at,
                )
            )

        if conn is not None:
            _do_insert(conn)
        else:
            with self._engine.begin() as c:
                _do_insert(c)

        logger.info(
            f"custom_agent_configs.insert: agent_id={agent_id} "
            f"name={name!r} created_by={created_by!r} "
            f"created_auth_method={created_auth_method}"
        )
        return row

    # ----------------------------------------------------------------
    # Mutator (soft-delete)
    # ----------------------------------------------------------------

    def soft_delete(
        self,
        agent_id: str,
        *,
        conn: Optional[Connection] = None,
    ) -> bool:
        """Soft-delete a custom agent by setting ``deleted_at``.

        The R-A2 lifecycle ruling: custom-delete is conservative —
        keep rows, mark config deleted (history is preserved; a
        future restore is a one-column UPDATE on this row +
        the existing ``prompt_versions`` rows are already
        untouched and resolve correctly).

        Returns ``False`` when no live row matches (the row is
        already deleted OR was never live). The router uses this
        to surface a clean 404.

        Args:
            agent_id: Wire identity of the custom agent to
                soft-delete.
            conn: Optional ``Connection``. When ``None``, the
                method opens ``engine.begin()``.

        Returns:
            ``True`` when a live row was marked deleted;
            ``False`` when no live row matched.
        """
        now = _now_iso()

        def _do_update(c: Connection) -> bool:
            result = c.execute(
                CustomAgentConfig.__table__.update()
                .where(col(CustomAgentConfig.agent_id) == agent_id)
                .where(col(CustomAgentConfig.deleted_at).is_(None))
                .values(deleted_at=now, updated_at=now)
            )
            return bool(result.rowcount)

        if conn is not None:
            deleted = _do_update(conn)
        else:
            with self._engine.begin() as c:
                deleted = _do_update(c)

        if deleted:
            logger.info(
                f"custom_agent_configs.soft_delete: agent_id={agent_id} "
                f"deleted_at={now}"
            )
        return deleted

    def restore(
        self,
        agent_id: str,
        *,
        conn: Optional[Connection] = None,
    ) -> bool:
        """Restore a soft-deleted custom agent by clearing ``deleted_at``.

        Morning-fork surface (Phase C conservative-default — restore
        is intentionally simple; a wider "restore history + audit
        replay" is left for a future commission). Returns ``False``
        when no soft-deleted row matches.

        Args:
            agent_id: Wire identity of the custom agent to
                restore.
            conn: Optional ``Connection``. When ``None``, the
                method opens ``engine.begin()``.

        Returns:
            ``True`` when a previously-deleted row was restored;
            ``False`` when no soft-deleted row matched.
        """
        now = _now_iso()

        def _do_update(c: Connection) -> bool:
            result = c.execute(
                CustomAgentConfig.__table__.update()
                .where(col(CustomAgentConfig.agent_id) == agent_id)
                .where(col(CustomAgentConfig.deleted_at).is_not(None))
                .values(deleted_at=None, updated_at=now)
            )
            return bool(result.rowcount)

        if conn is not None:
            restored = _do_update(conn)
        else:
            with self._engine.begin() as c:
                restored = _do_update(c)

        if restored:
            logger.info(
                f"custom_agent_configs.restore: agent_id={agent_id} "
                f"updated_at={now}"
            )
        return restored


__all__ = [
    "CustomAgentConfigRepository",
    "CustomAgentIdError",
    "CustomAgentConfigNotFound",
    "CustomAgentCollisionError",
]
