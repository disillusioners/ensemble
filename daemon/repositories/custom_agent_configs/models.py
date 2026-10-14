"""SQLModel table definitions for the custom-agent config store.

Agent-prompt-immutability Phase C — User Directive #5A (2026-10-14,
overnight, the DB-born custom agents ruling). One backing table:

* :class:`CustomAgentConfig` — one row per user-defined custom
  agent. The custom agent has NO on-disk ``meta.json``; this row
  IS the config. The agent's prompt content lives in the existing
  ``prompt_versions`` table per Phase A; resolution happens through
  the existing Phase A hook (``resolve_prompts_from_db``) with
  ``is_custom_agent=True`` flipping the empty-state posture to
  raise :class:`NoPromptContentError` (User Directive #5A
  resolution-side hook — landed).

Design notes
------------

* **R-A2 (decisions.md §8.4 Block #5)** — customs are DB-only;
  no ``[v2]``-shaped dir tags; the ``prompt_versions`` /
  ``custom_agent_configs`` keys share the agent id (no
  ``custom:`` prefix; this module's design call is documented in
  ``custom_agent_configs/__init__.py`` and re-summarized below).

* **R-A1** — the creating user IS the human approver of v1
  versions. The ``created_by`` column carries the HTTP
  AuthContext.subject; ``created_auth_method`` carries the lane
  ('session' / 'bearer' — refused on 'internal' per the same
  S16 anti-forgery convention as ``actor_from_request``).

* **Soft-delete fence** — ``deleted_at`` is the tombstone. The
  config row stays (history preservation; future restore is a
  one-column UPDATE); ``AgentRegistry.list_active_customs()``
  filters ``WHERE deleted_at IS NULL`` and ``list_all()``
  surfaces soft-deleted rows with ``is_custom_deleted=True``
  on the AgentMetadata so the operator-facing API can choose
  whether to hide them.

* **JSON columns** (``tools_*`` / ``team_members`` /
  ``innate_skills`` / ``capabilities`` / ``llm_models``) use
  :class:`JSONBType` so the same schema works on both SQLite
  and PostgreSQL without per-dialect forks (research-
  findings-db-api.md §1 D2 dual-driver contract).

* **Booleans** as INTEGER 0/1 (skill/models.py precedent).

* **Timestamps** as ISO TEXT (existing project convention;
  ``_now_iso()`` helper).

Index / unique-constraint names stay byte-identical to the
companion migration ``daemon/migrations/versions/
20261014_040000_create_custom_agent_configs.sql`` (canonical
SQL source). The
``tests/migration/test_prompt_versions_index_parity.py``
test pattern extends to this table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary constants
# ─────────────────────────────────────────────────────────────────────────────


# Namespace guard constant — R-A2 implementation detail.
#
# User Directive #5A called for either a ``""`` (base) version_tag
# with ``is_custom`` carried on the registry row OR a ``custom:``
# prefix-namespaced identity. We chose the latter:
#
#   * Wire identity is ``"custom:<user-chosen-name>"`` (e.g.
#     ``"custom:myhelper"``).
#   * Custom agent's id NEVER collides with a file-defined
#     ``agents/<dir>/`` agent because real dir names cannot
#     contain ``:`` (``_parse_agent_dir_name`` would have flagged
#     one via its tag regex); the wire form is provably
#     disjoint.
#   * ``prompt_versions`` rows for customs carry the SAME
#     ``agent_id = "custom:myhelper"`` + ``version_tag = ""``
#     (F9 normalization — base tag).
#   * The registry's collision rule (file-defined wins) + the
#     ``custom:`` prefix on the wire is the no-leak property
#     tested in ``tests/test_custom_agent_lifecycle.py``.
#
# This is a runtime constant — the value is a literal in
# ``prompt_versions`` rows and matches the wire identity that
# hits the loader + spawn path. Touch with care.
CUSTOM_AGENT_ID_PREFIX: str = "custom:"


def make_custom_agent_id(user_name: str) -> str:
    """Build the wire identity for a custom agent.

    Strips whitespace, validates the ``user_name`` form, and
    prefixes it with ``CUSTOM_AGENT_ID_PREFIX``. The caller has
    already upper-bound the ID alphabet (alphanumeric + hyphen +
    underscore) at the API/router layer; this function
    re-validates as defense-in-depth (a misuse here would leak
    a malformed id into ``prompt_versions`` keys and corrupt
    the cache fingerprint per agent).

    Args:
        user_name: User-provided agent id (e.g. ``"myhelper"``).

    Returns:
        The wire identity (e.g. ``"custom:myhelper"``).

    Raises:
        ValueError: On empty / invalid ``user_name``.
    """
    if not user_name or not isinstance(user_name, str):
        raise ValueError(f"user_name must be a non-empty string; got {user_name!r}")
    cleaned = user_name.strip()
    if not cleaned:
        raise ValueError(
            f"user_name must be a non-empty string (got whitespace-only: "
            f"{user_name!r})"
        )
    if not cleaned.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            f"user_name must contain only alphanumeric characters, hyphens, "
            f"and underscores (User Directive #5A id alphabet); "
            f"got {user_name!r}"
        )
    if cleaned.startswith(CUSTOM_AGENT_ID_PREFIX):
        # Already-prefixed — refuse; the caller is double-wrapping.
        raise ValueError(
            f"user_name must NOT carry the {CUSTOM_AGENT_ID_PREFIX!r} prefix "
            f"caller-side; got {user_name!r}"
        )
    return f"{CUSTOM_AGENT_ID_PREFIX}{cleaned}"


def is_custom_agent_id(agent_id: str) -> bool:
    """Return ``True`` when the wire id belongs to the custom namespace.

    Pure prefix check; no DB access. Used by the lifecycle coder
    surface (router, registry, spawn path) to discriminate the
    custom namespace from the predefined ``agents/<dir>/``
    namespace.

    Args:
        agent_id: The wire identity string to test.

    Returns:
        ``True`` when ``agent_id`` is prefixed with
        ``CUSTOM_AGENT_ID_PREFIX``; ``False`` otherwise.
    """
    return isinstance(agent_id, str) and agent_id.startswith(
        CUSTOM_AGENT_ID_PREFIX,
    )


# ============================================================
# Models
# ============================================================


class CustomAgentConfig(SQLModel, table=True):
    """A single user-defined custom agent's config row.

    The R-A2 lifecycle backing store. ONE row per custom agent;
    the agent's prompt content lives separately in
    ``prompt_versions`` (the existing Phase A store) keyed by the
    SAME ``agent_id`` + ``version_tag=''``.

    Lifecycle
    ---------

    * **Creation** — the router inserts the row + v1 prompt
      rows in ONE ``engine.begin()`` transaction (the v1 rows
      are stamped ``state='approved'`` + ``is_selected=1`` +
      ``approver_subject`` = the HTTP actor subject — R-A1: the
      creating user IS the human approver; same-txn audit events
      land alongside).
    * **Soft-delete** — the row's ``deleted_at`` column is set
      via an UPDATE; ``prompt_versions`` rows are NOT touched
      (history preservation; the row remains a valid resolution
      target for the existing versions — a future restore is a
      ``deleted_at = NULL`` UPDATE).
    * **Hard-delete** — OUT OF SCOPE for Phase C (R-A2 ruling:
      "conservative: keep rows, mark config deleted"). A
      morning-fork future work item — the SQL DOWN section in
      the migration is for staging-only PG teardown, not a
      caller surface.

    Identity
    --------

    * ``agent_id`` is the PRIMARY KEY (the wire identity,
      e.g. ``"custom:myhelper"``).
    * ``created_by`` + ``created_auth_method`` carry the
      creator identity verbatim (``AuthContext.subject`` +
      ``AuthContext.auth_method``). The ``created_auth_method``
      is ``'session'`` (the human-lane creation path) or
      ``'bearer'`` (machine lane — for any future
      automation / scripts).

    Attributes:
        config_id: UUID4 primary key (TEXT — dual-driver
            portability; precedent: ``StoredWatch``,
            ``PromptVersion``).
        agent_id: Wire identity of the custom agent.
        name: Display name shown in UI lists.
        description: Free-form description.
        icon: UI icon (emoji or token).
        color: UI color tag.
        system: FORCED 0 — customs are non-system by
            construction. The router stamps 0 on insert.
        tools_allow: List of allowed tools / categories (JSON).
        tools_deny: List of denied tools / categories (JSON).
        tools_deny_spawn: List of category spawn-blocks (JSON).
        llm_model: Optional default model override.
        llm_models: List of weighted model entries (JSON).
        team_members: List of spawn-eligible agent ids (JSON).
        innate_skills: List of innate skill names (JSON).
        capabilities: List of capability tags (JSON).
        skill_injection: 0/1 — opt-in for skill auto-search.
        snapshot_enabled: 0/1 — Agent Snapshot warm-start
            opt-in.
        skill_search_interval: 1 (default; honored when
            ``skill_injection=1``).
        blueprint_inactive: 0/1 — hide from active blueprints.
        inject_allowed_models: 0/1 — model-list injection.
        recursion_limit_multiplier: Default 1.0.
        recursion_limit: Optional explicit recursion limit.
        context_injection_heuristic_match_shared_md_files:
            0/1 — shared-context heuristic opt-in.
        version_tag: F9 version tag (``""`` for base; customs
            carry NO dir-derived tag).
        created_by: HTTP AuthContext.subject of the creator.
        created_auth_method: 'session' / 'bearer' (never
            'internal' per S16).
        created_at: ISO-8601 timestamp (immutable).
        updated_at: ISO-8601 timestamp (bumped on every
            mutation).
        deleted_at: ISO-8601 timestamp of soft-delete
            (NULL = live).
    """

    __tablename__ = "custom_agent_configs"
    __table_args__ = (
        # PRIMARY KEY is on agent_id itself (TEXT); the
        # UUID4 ``config_id`` is a defense-in-depth tie-
        # breaker if a future migration needs it (and is
        # what every other dual-driver table carries). No
        # additional unique constraint — agent_id IS the
        # one row-key.

        # Soft-delete sweep (e.g. "list live customs" +
        # "purge deletes older than N days").
        Index(
            "idx_custom_agent_configs_deleted_at",
            "deleted_at",
        ),
        # Audit-by-creator (e.g. "which customs did user X
        # create"). No FK — AuthContext.subject is opaque to
        # this module.
        Index(
            "idx_custom_agent_configs_created_by",
            "created_by",
        ),
        # Recency queries (e.g. "recently created customs"
        # for the operator's UI).
        Index(
            "idx_custom_agent_configs_created_at",
            "created_at",
        ),
    )

    config_id: str = Field(
        default_factory=lambda: __import__("uuid").uuid4().__str__(),
        primary_key=True,
        max_length=64,
    )
    agent_id: str = Field(
        max_length=128,
        unique=True,
        description=(
            "Wire identity of the custom agent (e.g. "
            "'custom:myhelper'). PRIMARY KEY."
        ),
    )
    name: str = Field(
        default="",
        max_length=128,
        description="Display name shown in UI lists.",
    )
    description: str = Field(
        default="",
        sa_column=Column(Text),
        description="Free-form description.",
    )
    icon: str = Field(
        default="🤖",
        max_length=32,
        description="UI icon (emoji or token).",
    )
    color: str = Field(
        default="accent-blue",
        max_length=64,
        description="UI color tag.",
    )
    system: int = Field(
        default=0,
        description=(
            "FORCED 0 — customs are non-system by construction; "
            "the router enforces this at insert."
        ),
    )
    tools_allow: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="List of allowed tools / categories (JSON).",
    )
    tools_deny: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="List of denied tools / categories (JSON).",
    )
    tools_deny_spawn: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="List of category spawn-blocks (JSON).",
    )
    llm_model: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional default model override.",
    )
    llm_models: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="Weighted model entries (JSON list).",
    )
    team_members: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="Spawn-eligible agent ids (JSON list).",
    )
    innate_skills: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="Innate skill names (JSON list).",
    )
    capabilities: list = Field(
        default_factory=list,
        sa_column=Column(JSONBType),
        description="Capability tags (JSON list).",
    )
    skill_injection: int = Field(
        default=0,
        description="0/1 — opt-in for skill auto-search.",
    )
    snapshot_enabled: int = Field(
        default=0,
        description="0/1 — Agent Snapshot warm-start opt-in.",
    )
    skill_search_interval: int = Field(
        default=1,
        description="Skill search interval (default 1).",
    )
    blueprint_inactive: int = Field(
        default=0,
        description="0/1 — hide from active blueprints.",
    )
    inject_allowed_models: int = Field(
        default=0,
        description="0/1 — model-list injection.",
    )
    recursion_limit_multiplier: float = Field(
        default=1.0,
        description="Default 1.0; per-agent recursion knob.",
    )
    recursion_limit: Optional[int] = Field(
        default=None,
        description="Optional explicit recursion limit.",
    )
    context_injection_heuristic_match_shared_md_files: int = Field(
        default=0,
        description="0/1 — shared-context heuristic opt-in.",
    )
    version_tag: str = Field(
        default="",
        max_length=64,
        description=(
            "F9 version tag ('' for base; customs carry NO "
            "dir-derived tag)."
        ),
    )
    created_by: str = Field(
        default="",
        max_length=256,
        description=(
            "HTTP AuthContext.subject of the creator."
        ),
    )
    created_auth_method: str = Field(
        default="session",
        max_length=32,
        description=(
            "'session' / 'bearer' (never 'internal' per S16)."
        ),
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    deleted_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of soft-delete (NULL = live)."
        ),
    )
