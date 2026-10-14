"""SQLModel table definitions for the prompt-version layer.

Phase A of the agent-prompt-immutability commission (see
``.agents/shared/planning/agent-prompt-immutability/`` for the
full plan + decisions). Two tables back the versioned prompt
override store + audit/event row:

* :class:`PromptVersion` — one row per (agent_id, version_tag,
  artifact, version). The ``(agent_id, version_tag)`` key composes
  with the directory-suffix version tag from the agent-versioning
  prior art (decisions.md F9; D15/D16 from
  ``.agents/shared/planning/agent-versioning/``) so a tagged
  sibling copy's overrides never leak into the base agent and
  vice versa.

  * **state** — formal approval state machine per phase-contract.md
    A1: ``draft → pending_approval → approved | rejected``; recall
    edge ``pending_approval → draft``; archival edge ``draft →
    archived``. ``approved`` and ``rejected`` are TERMINAL —
    re-submission after reject = a NEW row at ``max(version)+1``
    for the key (the rejected row stays in lineage; same-row
    re-open would break forensic identity). The repository owns
    the ``VALID_TRANSITIONS`` set-of-tuples; this model just
    stores the column.

  * **is_selected** — the per-key one-selection-pointer flag (F5 —
    Skill ``is_active`` precedent, decisions.md F5). Repo enforces
    ``≤ 1 selected per key`` transactionally (R3 mitigation;
    NULL-distinct in UNIQUE on both drivers forbids a NULL-aware
    partial unique index — research-findings-db-api.md §1
    PITFALLS).

  * **split identity columns** (decisions.md D-IDENTITY-STAMPING) —
    submitter side (``submitter_instance_id`` /
    ``submitter_agent_id`` / ``submitter_version_tag``) is the
    in-process submitter provenance; approver side
    (``approver_subject`` / ``approver_auth_method``) is the
    HTTP-side stamp from ``AuthContext.subject`` +
    ``AuthContext.auth_method``. NEVER a single ``submitted_by``
    blob — both classes need separate provenance columns for
    audit reconstruction in either direction.

  * **content / content_hash** — the override body. ``content_hash``
    is the sha256 hex of ``content`` (lowercase hex) so the cache
    fingerprint check (``loader.py:915``) can cheaply detect
    ``same DB content vs new DB content`` without re-hashing the
    body.

* :class:`PromptAuditEvent` — one row per state transition /
  selection change / template-copy event (drift_events precedent,
  ``daemon/migrations/versions/20261007_000001``). The audit row
  is stamped INSIDE the same ``engine.begin()`` transaction as
  the mutation per phase-contract.md A3 — never outside the
  repo call boundary.

Notes
-----
* ``UNIQUE(agent_id, version_tag, artifact, version)`` per A7/C5/G-4e
  lets multiple ``''`` ``version_tag`` rows share a name (matches
  ``Skill.generation`` precedent at ``skill/models.py:44-49``); the
  ``version_tag`` column is normalized to ``''`` (never NULL) at
  write time so the uniqueness check is unambiguous on drivers that
  treat NULLs as distinct in UNIQUE.
* All timestamps are ISO-8601 strings (``_now_iso()`` style) for
  cross-driver consistency.
* JSON columns (``payload``) use :class:`JSONBType` so the same
  schema works on both SQLite and PostgreSQL without per-dialect
  forks (decisions.md D2 dual-driver contract).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Module-level helper so model ``default_factory`` lambdas stay
    short. Mirrors the project-wide pattern used by
    :class:`SkillBankItem` and friends.
    """
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary constants (single source of truth — repository + service
# import these instead of inlining string literals).
# ─────────────────────────────────────────────────────────────────────────────

# Approval state vocabulary (phase-contract.md A1). Order is the
# typical lifecycle progression; ``approved`` and ``rejected`` are
# terminal; ``archived`` is a draft-only side state (F1 overrides-
# only: prior pending rows get archived when a new draft supersedes
# them via ``create_and_submit``).
PROMPT_STATES: frozenset[str] = frozenset({
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "archived",
})

# Audit event-type vocabulary (phase-contract.md T2 + decisions.md
# D-IDENTITY-STAMPING). UPPERCASE per the addendum S3/O-12
# prescription — never ``created`` / ``submitted`` etc.
#
# USER DIRECTIVE #2 (2026-10-14, frontend specs) — ADDITIVE extension
# of the S3/O-12 nine-type set with ``UPDATED`` + ``DELETED`` (the
# CRUD + badge API deltas, deliverable 1). Code-level frozenset only:
# the DB column is ``event_type TEXT NOT NULL`` (migration
# ``20261013_214000_create_prompt_versions.sql:119``) and validation
# happens here at every stamp site (:meth:`PromptVersionRepository.
# _validate_event_type`), so the extension is additive code with NO
# schema change. Morning-review surface: the two new types cover the
# draft/pending content-edit and draft/pending hard-delete paths —
# ``UPDATED`` = content replaced in place on an editable row;
# ``DELETED`` = row removed, forensics carried by the audit payload.
#
# USER DIRECTIVE #5B (2026-10-14, RESOLUTION-CORE) — ADDITIVE
# extension with ``TEMPLATE_RESET`` (the R-B2 CORE-only auto-reset
# audit event when the baseline service detects a shipped-template
# drift). Code-level frozenset only: no DB schema change (same
# ``event_type TEXT NOT NULL`` column); validation reuses
# :meth:`PromptVersionRepository._validate_event_type`. The payload
# carries ``(old_hash, new_hash, selection_preserved: bool)``.
PROMPT_AUDIT_EVENT_TYPES: frozenset[str] = frozenset({
    "CREATED",
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "RETRACTED",
    "ARCHIVED",
    "SELECTED",
    "CLEARED",
    "AGENT_CREATE_TEMPLATE_COPIED",
    "UPDATED",
    "DELETED",
    "TEMPLATE_RESET",
})

# User Directive #3 (2026-10-14, RESOLUTION-CORE) — class vocabulary. Each
# ``prompt_versions`` row carries a single ``artifact_class`` value that
# drives both the resolution chain and the audit/CREATE-time guard rails:
#
#   * 'CORE' — soul / rule / workflow / tools_note / tools / skill
#     (the conservative Phase A set, decisions.md F2). State-machine
#     governed (draft → pending_approval → approved | rejected); the
#     R-B2 CORE-only template auto-reset applies on shipped-file
#     drift.
#   * 'MEMORY' — memory / user / growth / memories/<slug> family.
#     TRANSITION-FREE: no state machine, no pending / approval rows;
#     rows are born-effective at creation time. The loader's
#     resolution precedence for MEMORY rows is
#     ``selected_version.content > LATEST row > template-file fallback``
#     (the file fallback is empty post-sweep — the empty-file boot
#     tolerance proven by ``decisions.md §7 boot-tolerance proofs``).
#     The R-B2 auto-reset does NOT touch MEMORY rows (MEMORY owns
#     the artifact; user updates are stored row-by-row).
#
# The class DISTINCT identity is critical: a MEMORY write must bust
# ONLY the MEMORY fingerprint slot (a CORE write busts ONLY the CORE
# slot). The loader's synthetic ``db_versions_fingerprint``
# (loader.py ``_compute_db_versions_fingerprint``) splits the
# ``EffectiveRow`` list by class and folds per-class — see Ud#3
# binding block 3.
PROMPT_ARTIFACT_CLASSES: frozenset[str] = frozenset({
    "CORE",
    "MEMORY",
})

# Conservative-default versioned-immutable artifact set (decisions.md
# F2). The hook (T6 in phaseA-plan.md) iterates this set when
# replacing file content with DB overrides; ``memory.md`` / ``user.md``
# / ``growth.md`` are CARVED OUT (D-ARTIFACT-DISPOSITION) and the
# loader does NOT consult the DB for them in Phase A.
#
# User Directive #3 (2026-10-14, RESOLUTION-CORE) extends the loader's
# resolution set to the memory family for MEMORY-class rows (the
# carve-out covered FILE writes by inner_soul; it now also covers DB
# rows by inner_soul's MEMORY-class create path). The set below is
# unchanged for the loader hook — ``PROMPT_ARTIFACTS`` keeps the
# CORE-CLASS scope (decision-stable); the loader's MEMORY resolution
# is implemented via the artifact string (``memory`` / ``user`` /
# ``growth`` / ``memories/<slug>``) carrying class=MEMORY. See
# ``loader.py:_HOOK_ARTIFACT_KEYS`` for the resolution extension.
PROMPT_ARTIFACTS: frozenset[str] = frozenset({
    "soul",
    "rule",
    "workflow",
    "tools_note",
    "tools",
    "skill",
})

# Memory-class artifact keys (Ud#3 binding block 1). The loader
# resolves these with MEMORY precedence when DB rows are present;
# file fallback fires (now empty post-sweep) when they are not.
# ``memories/<slug>`` is per-file (each memory file is its own key,
# with versions = its revisions; selection = per-file pin).
PROMPT_MEMORY_ARTIFACTS: frozenset[str] = frozenset({
    "memory",
    "user",
    "growth",
})


# ============================================================
# Models
# ============================================================


class PromptVersion(SQLModel, table=True):
    """A single versioned override row for an agent prompt artifact.

    Versioning model:

    * Per-key monotonic integer ``version`` (``(agent_id,
      version_tag, artifact)`` → monotonically increasing on each
      new draft submission).
    * State machine ``draft → pending_approval → approved |
      rejected`` with the A1 recall / archival edges.
    * Append-only: ``approved`` rows are never mutated; rollback =
      ``set_selection`` to an older approved row.

    Selection:

    * ``is_selected=1`` is the per-key one-selection-pointer
      (F5). Repo enforces ``≤ 1 per key`` via transactional
      clear-and-set inside ``engine.begin()`` (R3).

    Identity:

    * ``submitter_*`` columns track the in-process submitter (an
      agent instance that called ``inner_soul`` or
      ``agent_mother.agent_modify``).
    * ``approver_*`` columns track the HTTP approver
      (``AuthContext.subject`` + ``AuthContext.auth_method``);
      Phase A writes the column shape; Phase B stamps them from
      request handlers.

    Attributes:
        version_id: UUID4 primary key (TEXT — dual-driver
            portability; precedent: ``StoredWatch``).
        agent_id: Agent this version is for (the resolved base
            agent id; ``developer`` not ``developer[v2]``).
        version_tag: Version tag from the directory-suffix
            (F9 — ``''`` for base, ``'v2'`` for
            ``agents/developer[v2]/``). ``NULL`` is NOT used;
            the repo normalizes NULL/``''`` to ``''`` before
            insert (NULL-distinct in UNIQUE pitfall).
        artifact: Artifact key (one of ``PROMPT_ARTIFACTS`` —
            ``'soul'``, ``'rule'``, ``'workflow'``,
            ``'tools_note'``, ``'tools'``, ``'skill'``).
        version: Monotonic integer per key. New row = retry-on-
            IntegrityError in the repo; the UNIQUE constraint is
            the F9 / A7 / C5 / G-4e fence.
        state: Approval state (one of ``PROMPT_STATES``).
        content: Override body (markdown).
        content_hash: sha256 hex of ``content`` (lowercase
            hex; repo stamps before insert).
        is_selected: 0/1 boolean flag — at most one row per
            key has ``is_selected=1`` (transactional).
        submitter_instance_id: Instance id of the submitter
            (``instances.instance_id`` shape; nullable for
            HTTP-originated drafts).
        submitter_agent_id: Agent id of the submitter instance
            (``coder``, ``inner_soul`` rewrite caller, etc.).
        submitter_version_tag: Version tag of the submitter
            instance — ``coder[v2]`` → ``'v2'``. ``''`` for
            base.
        approver_subject: HTTP approver subject (OIDC ``sub``
            or bearer owner label) stamped from
            ``AuthContext.subject``. ``''`` until approved /
            rejected.
        approver_auth_method: HTTP approver auth method
            (``'session'``, ``'bearer'``). ``''`` until
            approved / rejected.
        created_at: ISO-8601 timestamp (immutable).
        updated_at: ISO-8601 timestamp (bumped on every
            mutation).
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        # A7 / C5 / G-4e: the FENCE that blocks the
        # ``SELECT MAX(version)+1`` race for new-row creation.
        # The repo INSERTs and retries on IntegrityError (recompute
        # max, retry ≤ 3) — see ``atomic_transition`` and
        # ``create_and_submit``.
        UniqueConstraint(
            "agent_id",
            "version_tag",
            "artifact",
            "version",
            name="uq_prompt_versions_agent_tag_artifact_version",
        ),
        # Lookup index for ``list_versions(key)`` + the loader
        # hook's per-key queries.
        Index(
            "idx_prompt_versions_key",
            "agent_id",
            "version_tag",
            "artifact",
        ),
        # ``get_latest_approved(key)`` scan + the
        # ``state IN ('approved')`` filter.
        Index(
            "idx_prompt_versions_key_state",
            "agent_id",
            "version_tag",
            "artifact",
            "state",
        ),
        # Sweep queries by lifecycle state (e.g. orphan-pending
        # sweep, GC-by-state).
        Index(
            "idx_prompt_versions_state_updated",
            "state",
            "updated_at",
        ),
    )

    version_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    agent_id: str = Field(
        max_length=128,
        description="Resolved base agent id (NOT the directory name).",
    )
    version_tag: str = Field(
        default="",
        max_length=64,
        description=(
            "Version tag from directory suffix (F9). NULL/'' normalize "
            "to '' at write time so UNIQUE constraint is unambiguous "
            "across drivers (NULL-distinct in UNIQUE pitfall)."
        ),
    )
    artifact: str = Field(
        max_length=64,
        description="Artifact key (one of PROMPT_ARTIFACTS).",
    )
    version: int = Field(
        description=(
            "Monotonic integer per (agent_id, version_tag, artifact); "
            "UNIQUE constraint is the F9/A7/C5 fence for new-row "
            "creation."
        ),
    )
    state: str = Field(
        default="draft",
        max_length=32,
        description="Approval state (one of PROMPT_STATES).",
    )
    content: str = Field(
        sa_column=Column(Text),
        description="Override body (markdown).",
    )
    content_hash: str = Field(
        max_length=64,
        description="sha256 hex of content (lowercase).",
    )
    is_selected: int = Field(
        default=0,
        description=(
            "0/1 boolean — at most one row per key has is_selected=1. "
            "Repo enforces via transactional clear-and-set."
        ),
    )
    submitter_instance_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "In-process submitter instance_id (NULL for HTTP-originated drafts)."
        ),
    )
    submitter_agent_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description=(
            "Submitter instance's resolved agent_id (e.g. 'coder')."
        ),
    )
    submitter_version_tag: str = Field(
        default="",
        max_length=64,
        description=(
            "Submitter instance's version tag ('' for base; 'v2' etc.)."
        ),
    )
    approver_subject: Optional[str] = Field(
        default=None,
        max_length=256,
        description=(
            "HTTP approver subject (AuthContext.subject). '' until "
            "approved / rejected; Phase B stamps from request handlers."
        ),
    )
    approver_auth_method: Optional[str] = Field(
        default=None,
        max_length=32,
        description=(
            "HTTP approver auth_method ('session' / 'bearer'). '' until "
            "approved / rejected."
        ),
    )
    artifact_class: str = Field(
        default="CORE",
        max_length=16,
        description=(
            "Resolution class (User Directive #3, 2026-10-14). One of "
            "PROMPT_ARTIFACT_CLASSES (CORE | MEMORY). CORE = the "
            "state-machine-governed conservative-default set (soul/rule/"
            "workflow/tools_note/tools/skill); MEMORY = the carve-out "
            "memory family (memory/user/growth/memories/<slug>) which "
            "is TRANSITION-FREE and born-effective. Class-distinct "
            "identity is critical for the loader fingerprint (a MEMORY "
            "write busts ONLY the MEMORY slot; a CORE write busts "
            "ONLY the CORE slot). Migration adds this column with "
            "DEFAULT 'CORE'; backfill UPDATE classifies existing rows "
            "into MEMORY when artifact IN (memory, user, growth) or "
            "artifact LIKE 'memories/%'."
        ),
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class PromptAuditEvent(SQLModel, table=True):
    """Audit/event-row table for prompt-version state transitions.

    One row per state transition / selection change / template-copy
    event (drift_events precedent). The row is stamped INSIDE the
    same ``engine.begin()`` transaction as the mutation it logs
    (phase-contract.md A3) — never outside the repo call boundary.

    Identity model:

    * ``actor_subject`` + ``actor_auth_method`` carry the submitter
      or approver identity verbatim (request body fields are
      IGNORED; actor always comes from ``AuthContext`` for HTTP
      or from the submitter instance for in-process lanes; see
      decisions.md D-IDENTITY-STAMPING).
    * ``payload`` is a free-form JSON blob carrying the
      transition-specific context (e.g.
      ``{"from_state": "pending_approval", "to_state":
      "approved", "selected_version_id": "..."}``).

    Attributes:
        event_id: UUID4 primary key (TEXT — dual-driver).
        agent_id: Agent identity for the event.
        version_tag: Version tag for the event (``''`` for base).
        artifact: Artifact key for the event.
        version_id: The :class:`PromptVersion` row this event
            describes (no FK — audit must survive even when the
            version row is archived / cleaned up).
        event_type: UPPERCASE event type (one of
            ``PROMPT_AUDIT_EVENT_TYPES``).
        actor_subject: Identity of the actor (``AuthContext.subject``
            for HTTP, instance_id for in-process).
        actor_auth_method: Lane of the actor (``'session'``,
            ``'bearer'``, ``'internal'``).
        payload: JSONBType blob — transition context.
        created_at: ISO-8601 timestamp.
    """

    __tablename__ = "prompt_audit_events"
    __table_args__ = (
        # Per-key timeline query (e.g. "show me the last N events
        # for the soul.md of developer[v2]").
        Index(
            "idx_prompt_audit_events_key_created",
            "agent_id",
            "version_tag",
            "artifact",
            "created_at",
        ),
        # Reverse lookup — "show me all events touching this
        # specific version_id".
        Index(
            "idx_prompt_audit_events_version",
            "version_id",
        ),
        # Event-type sweep (e.g. "all APPROVED events in window").
        Index(
            "idx_prompt_audit_events_type",
            "event_type",
            "created_at",
        ),
    )

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    agent_id: str = Field(max_length=128)
    version_tag: str = Field(default="", max_length=64)
    artifact: str = Field(max_length=64)
    version_id: str = Field(max_length=64)
    event_type: str = Field(
        max_length=64,
        description="UPPERCASE event type (one of PROMPT_AUDIT_EVENT_TYPES).",
    )
    actor_subject: str = Field(
        default="",
        max_length=256,
        description=(
            "Actor identity (AuthContext.subject for HTTP, instance_id "
            "for in-process). Empty string only when the actor is "
            "unknown — explicitly recorded as such."
        ),
    )
    actor_auth_method: str = Field(
        default="",
        max_length=32,
        description=(
            "Actor auth lane ('session', 'bearer', 'internal'). "
            "Empty string only when unknown."
        ),
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONBType),
        description="JSONBType blob — transition-specific context.",
    )
    created_at: str = Field(default_factory=_now_iso)


# ============================================================
# User Directive #5B (2026-10-14, RESOLUTION-CORE) — Template
# baselines for the predefined-agent R-B2 CORE-only auto-reset.
# ============================================================


class PromptTemplateBaseline(SQLModel, table=True):
    """The shipped-template sha256 baseline per ``(agent_id, version_tag,
    artifact)``.

    Records the file-content identity at ``record_initial_baselines``
    time so the R-B2 CORE-only auto-reset can detect shipped template
    drift and clear the un-selected resolution path
    (``latest_approved → template-file``) back to the new file base
    (an EXPLICIT ``is_selected=1`` pointer SURVIVES the auto-reset per
    R-B3 — user intent wins; the approved-history rows remain).

    Index / unique-constraint names stay byte-identical to the
    companion migration ``daemon/migrations/versions/
    20261014_013815_create_prompt_template_baselines.sql`` (the
    canonical source for the SQL DDL; the model is the canonical
    source for the PG ``create_all`` path). The
    ``tests/migration/test_prompt_versions_index_parity.py`` test
    pattern extends to this table (parity checked by T_DR-B2).

    This table does NOT carry a state column — baselines are
    monotonically-replaced (the auto-reset re-points in place:
    UPDATE on the matching key, never INSERT a second row). No
    selection pointer (baselines are inherently one-slot-per-key).

    MEMORY-class artifacts (``memory`` / ``user`` / ``growth`` /
    ``memories/<slug>``) are NEVER inserted here — the auto-reset
    is CORE-only (R-B2). The service guards against MEMORY-class
    artifact strings before the insert.

    Attributes:
        baseline_id: UUID4 primary key (TEXT — dual-driver
            portability; precedent: ``StoredWatch``).
        agent_id: Resolved base agent id (``"developer"`` for
            ``developer[v2]/`` per the F9 / D15/D16 identity model).
        version_tag: Version tag from directory-suffix (``''`` for
            base, ``'v2'`` for tagged copies). NULL-normalized to
            ``''`` at write time (NULL-distinct UNIQUE pitfall).
        artifact: Artifact key (one of ``PROMPT_ARTIFACTS`` — the
            CORE set; MEMORY-class is rejected at the service
            boundary).
        template_sha256: Lowercase hex sha256 of the shipped file
            body at record time (``None`` is NOT valid;
            ``record_initial_baselines`` always hashes a real file).
        recorded_at: ISO-8601 stamp of the seed / re-point event.
    """

    __tablename__ = "prompt_template_baselines"
    __table_args__ = (
        # One baseline per (agent_id, version_tag, artifact) key.
        # The auto-reset re-points via UPDATE on this UNIQUE
        # predicate; a fresh INSERT on a duplicate key is a service-
        # side bug (the record_initial_baselines path explicitly
        # inserts-or-updates via INSERT ... ON CONFLICT (PostgreSQL)
        # or INSERT OR REPLACE (SQLite) — see :class:`BaselineService`).
        UniqueConstraint(
            "agent_id",
            "version_tag",
            "artifact",
            name="uq_prompt_template_baselines_agent_tag_artifact",
        ),
        # Sweep queries by artifact (e.g. ``all soul.md baselines
        # across agents``).
        Index(
            "idx_prompt_template_baselines_artifact",
            "artifact",
        ),
        # Recency queries (e.g. ``baselines re-pointed in the last
        # 7 days`` for the journal surface).
        Index(
            "idx_prompt_template_baselines_recorded_at",
            "recorded_at",
        ),
    )

    baseline_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    agent_id: str = Field(max_length=128)
    version_tag: str = Field(
        default="",
        max_length=64,
        description=(
            "Version tag from directory-suffix ('' for base; "
            "'v2' for tagged copies). NULL-normalized to '' at "
            "write time — the UNIQUE constraint is unambiguous "
            "across drivers (NULL-distinct pitfall)."
        ),
    )
    artifact: str = Field(max_length=64)
    template_sha256: str = Field(
        max_length=64,
        description=(
            "Lowercase hex sha256 of the shipped file at "
            "recorded_at time."
        ),
    )
    recorded_at: str = Field(default_factory=_now_iso)