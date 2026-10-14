"""Service facade for custom-agent lifecycle (Phase C).

User Directive #5A (2026-10-14, overnight, R-A1/A2/B1-B4 ruling).
The thin async facade over
:class:`CustomAgentConfigRepository` + the
:class:`PromptVersionService.create_initial_approved_v1` helper
on the prompt-version repo. The lifecycle owns the
**wide-transaction coordination** — the R-A1 atomicity invariant:

    with engine.begin() as conn:
        config_repo.insert_config(conn=conn, ...)
        for artifact, content in version_specs:
            pv_repo.create_initial_approved_v1(
                conn=conn,
                agent_id=agent_id,
                version_tag="",
                artifact=artifact,
                content=content,
                actor=actor,
            )

The service wraps that coordination in :meth:`create_custom_agent`
(async, runs the sync transaction inside ``asyncio.to_thread``).
:meth:`soft_delete_custom_agent` / :meth:`list_active` are simple
delegates to the repo.

A no-engine invariant: the service holds NO engine — engine work
goes through the repo's engine binding (corpus convention per
``research-findings-db-api.md`` §1).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from ..repositories.custom_agent_configs import (
    CUSTOM_AGENT_ID_PREFIX,
    CustomAgentConfig,
    CustomAgentConfigRepository,
    make_custom_agent_id,
)
from ..repositories.custom_agent_configs.repository import (
    CustomAgentCollisionError,
    CustomAgentIdError,
)

logger = logging.getLogger(__name__)


class CustomAgentNotFoundError(LookupError):
    """Raised when the targeted custom agent has no live config row.

    Distinct from a connection error (engine raises); used by the
    router to surface a clean 404.
    """


class CustomAgentCreationError(ValueError):
    """Raised when the creation transaction cannot complete.

    Maps to a 400 INVALID_REQUEST shape at the router boundary.
    The class is narrowly scoped to the creation flow — soft-
    delete / restore have their own typed errors.
    """


class CustomAgentLifecycleService:
    """Async facade for the custom-agent lifecycle (Phase C).

    Holds ONE repo reference (``CustomAgentConfigRepository``)
    and borrows the manager's ``_prompt_version_repo`` for the
    R-A1 wide transaction (the prompt-version repo is NOT
    constructed here — the service is OWNED by the manager and
    the manager passes the two repos in). See
    ``daemon/manager.py`` for the construction point.

    Attributes:
        _config_repo: Custom-agent config repo (singleton on
            ``manager._custom_agent_config_repo``).
        _prompt_version_repo: Prompt-version repo (singleton on
            ``manager._prompt_version_repo``). The Phase A repo
            owns the R-A1 ``create_initial_approved_v1``
            helper.
        _actor: Default actor for HTTP-lane creation. Not used
            right now — actors are thread / request-scoped and
            callers pass them explicitly.
    """

    def __init__(
        self,
        config_repo: CustomAgentConfigRepository,
        prompt_version_repo: Any,
    ) -> None:
        """Store the two repos.

        Args:
            config_repo: Custom-agent config repo.
            prompt_version_repo: Prompt-version repo (the one
                with ``create_initial_approved_v1``; type is
                ``Any`` to avoid a circular import — the
                manager owns both and passes them here).
        """
        self._config_repo = config_repo
        self._prompt_version_repo = prompt_version_repo

    # ----------------------------------------------------------------
    # Build helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _wire_id_for(user_name: str) -> str:
        """Build the wire identity from a user-supplied name.

        Thin wrapper over :func:`make_custom_agent_id` that
        re-raises the helper's ValueError as the typed
        :class:`CustomAgentIdError` for the router boundary to
        map cleanly.

        Args:
            user_name: User-supplied agent id (alphanumeric +
                hyphen + underscore).

        Returns:
            Wire identity (e.g. ``"custom:myhelper"``).

        Raises:
            CustomAgentIdError: On invalid input.
        """
        try:
            return make_custom_agent_id(user_name)
        except ValueError as e:
            raise CustomAgentIdError(str(e)) from e

    # ----------------------------------------------------------------
    # CREATE — R-A1 wide transaction
    # ----------------------------------------------------------------

    async def create_custom_agent(
        self,
        *,
        user_name: str,
        name: str,
        actor: Any,
        soul_md: str,
        rule_md: Optional[str] = None,
        workflow_md: Optional[str] = None,
        tools_note_md: Optional[str] = None,
        tools_md: Optional[str] = None,
        description: str = "",
        icon: str = "🤖",
        color: str = "accent-blue",
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
        tools_allow: Optional[list[str]] = None,
        tools_deny: Optional[list[str]] = None,
        tools_deny_spawn: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Create a custom agent + its v1 prompt rows in ONE transaction.

        R-A1 wide-transaction invariant: the config row, every
        v1 prompt_versions row, and every audit row ALL commit
        in ONE ``engine.begin()`` block. A half-written creation
        is impossible (the rollback is ``engine.begin()``'s
        exit-on-exception).

        The required ``soul_md`` content is the canonical
        ``soul.md`` body for the agent — the loader's empty-
        state posture (User Directive #5A, R-A2 hook) raises
        :class:`NoPromptContentError` when a custom agent has
        zero versions for the ``soul`` artifact, so we REFUSE
        creation without a non-empty soul at the boundary.

        Optional artifacts — ``rule_md`` / ``workflow_md`` /
        ``tools_note_md`` / ``tools_md`` — are also written as
        v1 rows when non-empty. Other prompt artifacts (skill,
        memory, user, growth) are NOT seeded by this method
        (the conservative-default creation surface — expanding
        to other artifacts is a follow-up).

        Args:
            user_name: User-supplied agent id (alphanumeric +
                hyphen + underscore; NOT prefix-prefixed).
            name: Display name (non-empty).
            actor: HTTP actor (``actor_from_request`` or an
                equivalent test value). MUST satisfy the
                R-A1 stamp requirements (non-empty
                ``subject``; ``auth_method`` in
                'session'/'bearer').
            soul_md: Required soul.md body (non-empty).
            rule_md / workflow_md / tools_note_md / tools_md:
                Optional v1 contents for the other CORE
                artifacts.
            description / icon / color: UI fields.
            llm_model / llm_models / team_members /
                innate_skills / capabilities: Spawn-time
                config.
            skill_injection / snapshot_enabled /
                blueprint_inactive / inject_allowed_models /
                recursion_limit_multiplier / recursion_limit /
                context_injection_heuristic_match_shared_md_files:
                Tooling / config knobs.
            tools_allow / tools_deny / tools_deny_spawn:
                Per-agent :class:`ToolFilter` shapes.

        Returns:
            A dict summary of the created custom agent (config
            + v1 versions for the operator's 201 response):

                {
                    "agent_id": "custom:myhelper",
                    "name": "...",
                    "config_id": "uuid",
                    "v1_versions": [
                        {"artifact": "soul", "version_id": "..."},
                        {"artifact": "rule", "version_id": "..."},
                    ],
                    "created_at": "...",
                }

        Raises:
            CustomAgentIdError: On invalid ``user_name``.
            CustomAgentCreationError: On ``soul_md`` empty, on
                identity collision (a row already exists for
                the wire id — IntegrityError), or on any DB
                write error.
        """
        if not soul_md or not soul_md.strip():
            raise CustomAgentCreationError(
                "soul_md is required (User Directive #5A — customs "
                "have NO template fallback; an empty soul would "
                "immediately trip NoPromptContentError on every "
                "spawn)"
            )

        agent_id = self._wire_id_for(user_name)

        # R-A1 version_specs — built BEFORE the transaction so a
        # malformed optional artifact does not lock the DB.
        version_specs: list[tuple[str, str]] = [
            ("soul", soul_md),
        ]
        if rule_md:
            version_specs.append(("rule", rule_md))
        if workflow_md:
            version_specs.append(("workflow", workflow_md))
        if tools_note_md:
            version_specs.append(("tools_note", tools_note_md))
        if tools_md:
            version_specs.append(("tools", tools_md))

        # Wide transaction — config row + v1 versions + audit
        # rows all commit together. Run the sync transaction
        # inside asyncio.to_thread so the async lane is preserved.
        def _do_create() -> dict[str, Any]:
            engine = self._config_repo.engine
            v1_versions: list[dict[str, Any]] = []
            config_dict: Optional[dict[str, Any]] = None

            try:
                with engine.begin() as conn:
                    # H5 — race-free typed discrimination. Pre-SELECT
                    # the wire id BEFORE the INSERT so a duplicate
                    # surfaces as CustomAgentCollisionError at this
                    # site (rolled back via the surrounding
                    # ``engine.begin()`` exit-on-exception), not as
                    # a generic IntegrityError. The router maps the
                    # two typed exceptions to 409 and 400 cleanly
                    # — substring sniffing the message is gone.
                    #
                    # The SELECT runs on the same ``conn`` so the
                    # read sees uncommitted INSERTs from a sibling
                    # txn that has not yet finished — closing the
                    # race window that the bare IntegrityError path
                    # could not.
                    existing = conn.execute(
                        select(
                            col(CustomAgentConfig.config_id),
                        ).where(
                            col(CustomAgentConfig.agent_id) == agent_id,
                            col(CustomAgentConfig.deleted_at).is_(None),
                        )
                    ).first()
                    if existing is not None:
                        raise CustomAgentCollisionError(
                            f"custom agent {agent_id!r} already exists"
                        )

                    config_row = self._config_repo.insert_config(
                        agent_id=agent_id,
                        name=name,
                        description=description,
                        icon=icon,
                        color=color,
                        tools_allow=tools_allow,
                        tools_deny=tools_deny,
                        tools_deny_spawn=tools_deny_spawn,
                        llm_model=llm_model,
                        llm_models=llm_models,
                        team_members=team_members,
                        innate_skills=innate_skills,
                        capabilities=capabilities,
                        skill_injection=skill_injection,
                        snapshot_enabled=snapshot_enabled,
                        blueprint_inactive=blueprint_inactive,
                        inject_allowed_models=inject_allowed_models,
                        recursion_limit_multiplier=recursion_limit_multiplier,
                        recursion_limit=recursion_limit,
                        context_injection_heuristic_match_shared_md_files=(
                            context_injection_heuristic_match_shared_md_files
                        ),
                        created_by=(
                            getattr(actor, "subject", "")
                            if actor is not None
                            else ""
                        ),
                        created_auth_method=(
                            getattr(actor, "auth_method", "session")
                            if actor is not None
                            else "session"
                        ),
                        conn=conn,
                    )
                    config_dict = {
                        "config_id": config_row.config_id,
                        "agent_id": config_row.agent_id,
                        "created_at": config_row.created_at,
                    }

                    for artifact, content in version_specs:
                        pv = self._prompt_version_repo.create_initial_approved_v1(
                            agent_id=agent_id,
                            version_tag="",
                            artifact=artifact,
                            content=content,
                            actor=actor,
                            conn=conn,
                        )
                        v1_versions.append(
                            {
                                "artifact": artifact,
                                "version_id": pv.version_id,
                                "version": pv.version,
                            }
                        )
            except IntegrityError as e:
                # H5 — any remaining IntegrityError after the
                # pre-SELECT is genuinely a creation error (FK
                # violation, NOT-NULL, CHECK constraint, race
                # window where another writer slipped between the
                # pre-SELECT and the INSERT). The router maps the
                # 400 path; the ``from e`` chain is preserved for
                # forensics.
                logger.warning(
                    f"custom_agent.create integrity error: "
                    f"agent_id={agent_id} err={type(e).__name__}: {e}"
                )
                raise CustomAgentCreationError(
                    f"custom agent {agent_id!r} creation failed: {e}"
                ) from e

            assert config_dict is not None
            return {
                **config_dict,
                "v1_versions": v1_versions,
            }

        result = await asyncio.to_thread(_do_create)
        logger.info(
            f"custom_agent.create: agent_id={agent_id} "
            f"name={name!r} v1_count={len(result['v1_versions'])}"
        )
        # Augment with display fields the router likes to surface
        # back as a 201.
        result["name"] = name
        return result

    # ----------------------------------------------------------------
    # SOFT-DELETE — R-A2 lifecycle trash
    # ----------------------------------------------------------------

    async def soft_delete_custom_agent(
        self,
        *,
        user_name: str,
    ) -> bool:
        """Soft-delete a custom agent by setting ``deleted_at``.

        Args:
            user_name: User-supplied agent id (NOT prefix-
                prefixed).

        Returns:
            ``True`` when a live row was marked deleted;
            ``False`` when no live row matched.

        Raises:
            CustomAgentIdError: On invalid ``user_name``.
        """
        agent_id = self._wire_id_for(user_name)
        return await asyncio.to_thread(
            self._config_repo.soft_delete,
            agent_id,
        )

    # ----------------------------------------------------------------
    # READS
    # ----------------------------------------------------------------

    async def list_active(self) -> list[dict[str, Any]]:
        """Return all LIVE custom-agent configs as dicts.

        The dict shape is the same as the registry's
        ``to_metadata_shape`` output so the router can pass the
        list through unchanged.

        Returns:
            List of dicts ordered by ``created_at`` ascending.
        """
        configs = await asyncio.to_thread(self._config_repo.list_active)
        return [
            {
                "agent_id": c.agent_id,
                "config_id": c.config_id,
                "name": c.name,
                "description": c.description,
                "icon": c.icon,
                "color": c.color,
                "is_custom": True,
                "created_by": c.created_by,
                "created_at": c.created_at,
                "deleted_at": c.deleted_at,
                "tools_allow": list(c.tools_allow or []),
                "innate_skills": list(c.innate_skills or []),
                "llm_model": c.llm_model,
            }
            for c in configs
        ]

    async def get_by_agent_id(
        self,
        agent_id: str,
    ) -> Optional[dict[str, Any]]:
        """Look up a custom-agent config by wire identity.

        Args:
            agent_id: The wire identity (``"custom:myhelper"``
                form OR the bare user name ``"myhelper"`` —
                the helper auto-prefixes).

        Returns:
            A dict summary (same shape as :meth:`list_active`),
            or ``None`` when no live row matches.

        Raises:
            CustomAgentIdError: On invalid ``agent_id``.
        """
        if not agent_id.startswith(CUSTOM_AGENT_ID_PREFIX):
            agent_id = self._wire_id_for(
                agent_id.split(":", 1)[-1]
                if ":" in agent_id
                else agent_id
            )
        row = await asyncio.to_thread(
            self._config_repo.get_by_agent_id,
            agent_id,
        )
        if row is None:
            return None
        return {
            "agent_id": row.agent_id,
            "config_id": row.config_id,
            "name": row.name,
            "description": row.description,
            "icon": row.icon,
            "color": row.color,
            "is_custom": True,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "deleted_at": row.deleted_at,
            "tools_allow": list(row.tools_allow or []),
            "innate_skills": list(row.innate_skills or []),
            "llm_model": row.llm_model,
        }


__all__ = [
    "CustomAgentLifecycleService",
    "CustomAgentNotFoundError",
    "CustomAgentCreationError",
]
