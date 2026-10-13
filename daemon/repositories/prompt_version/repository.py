"""Prompt-version repository — agent-prompt-immutability Phase A.

Phase A ships the DB-backed prompt-version layer so a runtime prompt
for an agent is resolved by
``effective = selected_version > latest_approved > template_file``.

This module implements :class:`PromptVersionRepository`, the sync
repository that backs the async service facade at
:mod:`daemon.services.prompt_version_service`. Per the project's
standard pattern (research-findings-db-api.md §2 + decisions.md D14):

* **Engine-only constructor** — ``__init__(self, engine)``; the
  repo holds no state besides the engine.
* **All methods are synchronous** — callers bridge via
  ``asyncio.to_thread`` (the Phase A facade's job, not this file's).
* **Per-method short-lived ``Session``** — every public method opens
  its own session and commits; no instance state.
* **Mutating methods use ``engine.begin()``** — wraps the SQL
  writes + the audit row in a single transaction per phase-contract.md
  A3.

Phase B consumes the public surface verbatim:

* ``create_draft``, ``submit_for_approval``, ``approve``,
  ``reject``, ``atomic_transition``, ``set_selection``,
  ``clear_selection``, ``get_selection``, ``list_versions``,
  ``get_version``, ``get_latest_approved``, ``get_effective``,
  ``append_audit_event`` — the 13 A2 methods.
* ``get_effective_rows`` — the A8 single-SELECT fingerprint +
  content read (Phase B uses it for the cache fingerprint bust;
  Phase A's loader hook will read it on every ``load_and_cache_prompt``
  call).
* ``create_and_submit`` — the A5/C4 composite used by ``inner_soul``
  and ``agent_mother.agent_modify`` rewrites (Phase B).

Design choices
--------------

* **Per-key writer serialization (R3, PG-concurrency fix 2026-10-13)** —
  every read-modify-write writer that can race on a key
  (``create_draft``, ``create_and_submit``, ``set_selection``,
  ``clear_selection``) takes a dialect-gated lock at the START of
  its transaction. On PostgreSQL that is ``pg_advisory_xact_lock``
  over a stable 63-bit hash of ``(agent_id, version_tag,
  artifact)`` (see :func:`_lock_key` + :meth:`PromptVersionRepository._acquire_key_lock`);
  on SQLite it is a no-op (the database-level single-writer
  serialization already orders these writers). Two SC8/SC9
  PG-parity defects this closes:

  - **F-1** — concurrent ``set_selection`` on PG READ COMMITTED
    left multiple ``is_selected`` pointers (plain SELECT+UPDATE
    pairs serialize on SQLite but not on PG); the advisory lock
    also covers the ABSENT-ROW gap that ``SELECT … FOR UPDATE``
    cannot lock (the first-selection race on a key with no rows
    yet).
  - **F-2** — the A7 bounded INSERT-retry (``_MAX_INSERT_RETRIES
    = 8``) was exhaustible under 10-way PG contention; with
    per-key serialization the ``SELECT MAX(version)`` + INSERT
    race window is closed, and the bounded retry remains as the
    backstop (A7 amendment db9f9e371 — budget NOT raised).
* **One-selection-pointer invariant** (F5, R3) — enforced
  transactionally via ``set_selection``/``clear_selection`` wrapped
  in one ``engine.begin()`` AND serialized per-key via the advisory
  lock above (the transaction wrapper alone is necessary but not
  sufficient on PG READ COMMITTED). NULL-distinct in UNIQUE on both
  drivers forbids a NULL-aware partial unique index; the repo
  holds the only enforcement path.
* **A7/C5 new-row INSERT-retry** — ``create_draft`` and
  ``create_and_submit`` INSERT under the
  ``UNIQUE(agent_id, version_tag, artifact, version)`` constraint
  and retry on ``IntegrityError`` (recompute ``max(version)``,
  retry ≤ 8, bounded — NOT raised by the concurrency fix; the
  per-key advisory lock removes the steady-state race, the budget
  is the backstop). ``SELECT MAX(version)+1`` was REJECTED as the
  sole mechanism for the same reason the one-selection-pointer
  invariant is transactional: the read-modify-write race under
  concurrent submitters.
* **Tag normalization** (A7/S12) — ``NULL``/``""`` unify to ``""``
  at every write site (``version_tag`` column is
  ``NOT NULL DEFAULT ''``; the repo coerces ``None`` to ``""``
  before insert).
* **``InvalidTransitionError(ValueError)``** — per A1, mirroring
  ``daemon/services/job_state_machine.py:65-81``. The ``VALID_TRANSITIONS``
  set-of-tuples is the single source of truth for state-machine
  guards.
* **Audit-event stamping** — every mutating method writes one (or
  more) :class:`PromptAuditEvent` rows INSIDE the same
  ``engine.begin()`` as the mutation. Never outside the
  transaction boundary.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .models import (
    PROMPT_AUDIT_EVENT_TYPES,
    PROMPT_STATES,
    PromptAuditEvent,
    PromptVersion,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Mirrors the project-wide pattern used by :class:`SkillBankItem`,
    :class:`StoredWatch`, etc. The model's ``default_factory`` uses
    its own module-local helper; this repository-local copy stamps
    update paths where the model's ``default_factory`` would not
    fire (e.g. UPDATE statements that mutate ``updated_at``).
    """
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(content: str) -> str:
    """Return lowercase hex sha256 of ``content`` (utf-8).

    Mirrors the convention used by the loader's fingerprint check
    (``loader.py:915``) so a ``content_hash`` comparison against
    the synthetic ``db_versions_fingerprint`` is one-line cheap.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_version_tag(version_tag: Optional[str]) -> str:
    """Coerce ``None`` / ``""`` to ``""`` (A7/S12 contract).

    The DB column is ``NOT NULL DEFAULT ''``; the repo never inserts
    NULL so the ``UNIQUE(agent_id, version_tag, artifact, version)``
    constraint behaves the same on SQLite and PostgreSQL (NULLs
    distinct in UNIQUE — research-findings-db-api.md §1 PITFALLS).
    """
    if version_tag is None or version_tag == "":
        return ""
    return version_tag


def _lock_key(agent_id: str, version_tag: str, artifact: str) -> int:
    """Deterministic advisory-lock key for one ``(agent_id, version_tag,
    artifact)`` prompt-version key (R3).

    Returns a **63-bit non-negative int** — always a valid positive
    signed bigint for ``pg_advisory_xact_lock(bigint)`` (the
    two-int ``(int4, int4)`` variant was rejected: one truncation
    instead of two, and a single-int call site reads cleaner in the
    SQL).

    Derivation: ``sha256`` over a canonical, **length-prefixed**
    serialization of the three components (length prefixes make
    delimiter collisions inside any component impossible —
    ``("a:bc", "d")`` and ``("a", "bc:d")`` derive different
    keys), truncated to the first 8 bytes and masked to 63 bits.
    The ``"prompt-version\\x1f"`` prefix namespaces the key-space so
    a future non-prompt advisory-lock user can never collide with
    these keys by accident (2026-10-13 audit: no other
    ``pg_advisory`` users exist in the codebase).

    Collision posture: with realistic key counts (≤ 10⁴), the
    birthday bound against the 2⁶³ space is ≈ (10⁴)² / 2⁶⁴ ≈
    5×10⁻¹¹ — negligible. A collision would only mean two DIFFERENT
    keys serialize against the same lock (a performance
    interaction, never a correctness one): the invariants
    (one-pointer, UNIQUE version) are enforced by the transaction
    bodies + constraints; the lock is a serializer, not the
    enforcement.

    Args:
        agent_id: Resolved agent id.
        version_tag: Version tag — pass the NORMALIZED value
            (``""`` for base); every lock call site normalizes
            before locking.
        artifact: Artifact key.
    """
    canonical = (
        f"prompt-version\x1f"
        f"{len(agent_id)}:{agent_id}\x1f"
        f"{len(version_tag)}:{version_tag}\x1f"
        f"{len(artifact)}:{artifact}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


# ─────────────────────────────────────────────────────────────────────────────
# State machine
# ─────────────────────────────────────────────────────────────────────────────


# Phase-contract.md A1 — frozen for Phase B. ``approved`` and
# ``rejected`` are TERMINAL; re-submission after reject = a NEW row at
# max(version)+1 for the key (the rejected row stays in lineage; same-row
# re-open would break forensic identity). ``(rejected, draft)`` is
# DROPPED.
VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("draft", "pending_approval"),
    ("pending_approval", "approved"),
    ("pending_approval", "rejected"),
    ("pending_approval", "draft"),
    ("draft", "archived"),
})


class InvalidTransitionError(ValueError):
    """Raised when an invalid prompt-version state transition is attempted.

    Mirrors ``daemon/services/job_state_machine.py:65-81``: inherits
    from ``ValueError`` so callers can catch both with a single
    ``except ValueError`` clause; ``except InvalidTransitionError``
    and ``isinstance`` checks keep working via the subclass
    relationship.
    """

    def __init__(
        self,
        version_id: str,
        from_state: Optional[str],
        to_state: str,
    ) -> None:
        self.version_id = version_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition for prompt_version {version_id}: "
            f"{from_state} \u2192 {to_state}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Actor context (A3)
# ─────────────────────────────────────────────────────────────────────────────


# Actor auth-method vocabulary (A3). HTTP lanes are ``session`` /
# ``bearer``; in-process submitters mint ``internal``. The HTTP
# constructor REFUSES ``internal`` (anti-forgery per S16 — only
# ``actor_from_instance()`` may mint internal).
AUTH_METHOD_SESSION = "session"
AUTH_METHOD_BEARER = "bearer"
AUTH_METHOD_INTERNAL = "internal"

_AUTH_METHODS: frozenset[str] = frozenset({
    AUTH_METHOD_SESSION,
    AUTH_METHOD_BEARER,
    AUTH_METHOD_INTERNAL,
})


@dataclass(frozen=True)
class ActorContext:
    """Caller identity for a repository mutation.

    ``AuthContext.subject`` + ``AuthContext.auth_method`` for HTTP
    lanes; ``(instance_id, agent_id, version_tag)`` for in-process
    submitters (``auth_method == "internal"``). Mutating methods
    stamp this onto the row's split identity columns
    (``submitter_*`` / ``approver_*``) and into the audit row's
    ``actor_subject`` / ``actor_auth_method``.

    The dataclass is ``frozen=True`` so accidental mid-call mutation
    can't corrupt the audit trail.

    Attributes:
        subject: Identity string. HTTP: ``AuthContext.subject``.
            Internal: ``instance_id`` (the agent instance that
            submitted).
        auth_method: One of ``AUTH_METHOD_SESSION`` /
            ``AUTH_METHOD_BEARER`` / ``AUTH_METHOD_INTERNAL``.
        instance_id: For ``auth_method == "internal"``: the
            submitter instance id. ``None`` for HTTP lanes (the
            approver side stamps ``AuthContext.subject``; the
            instance id does not survive the HTTP boundary).
        agent_id: For ``auth_method == "internal"``: the submitter
            instance's resolved agent_id. ``None`` for HTTP lanes.
        version_tag: For ``auth_method == "internal"``: the
            submitter instance's version tag (``""`` for base).
            ``None`` for HTTP lanes.
    """

    subject: str
    auth_method: str
    instance_id: Optional[str] = None
    agent_id: Optional[str] = None
    version_tag: Optional[str] = None

    def __post_init__(self) -> None:
        if self.auth_method not in _AUTH_METHODS:
            raise ValueError(
                f"auth_method must be one of {sorted(_AUTH_METHODS)}, "
                f"got {self.auth_method!r}"
            )
        if self.auth_method == AUTH_METHOD_INTERNAL:
            # In-process submitters MUST carry the instance identity
            # triple so the audit row can trace back to the
            # submitter instance. Without it, audit reconstruction
            # would have to walk the lineage by subject string
            # alone — fragile.
            if not (self.instance_id and self.agent_id is not None):
                raise ValueError(
                    "internal auth_method requires instance_id AND agent_id"
                )

    def is_internal(self) -> bool:
        return self.auth_method == AUTH_METHOD_INTERNAL


def actor_from_instance(
    instance_id: str,
    agent_id: str,
    version_tag: Optional[str] = None,
) -> ActorContext:
    """Build an ``ActorContext`` for an in-process submitter (A3).

    Use for ``inner_soul`` / ``agent_mother.agent_modify`` callers
    that submit DB drafts without an HTTP request boundary. The
    only constructor that mints ``auth_method == "internal"`` (per
    S16 anti-forgery; the HTTP constructor refuses internal).
    """
    return ActorContext(
        subject=instance_id,
        auth_method=AUTH_METHOD_INTERNAL,
        instance_id=instance_id,
        agent_id=agent_id,
        version_tag=_normalize_version_tag(version_tag),
    )


def actor_from_request(request: Any) -> ActorContext:
    """Build an ``ActorContext`` from an HTTP request (A3).

    Reads ``request.state.auth_context`` (the AuthContext the
    middleware attaches after a successful auth resolution).
    Raises if ``auth_method == "internal"`` — that lane is reserved
    for in-process submitters only; an HTTP request presenting an
    ``internal`` AuthContext is a forgery attempt (S16).

    Body-asserted actor fields are IGNORED (decisions.md R3 source-
    forgery ticket, critical note 257488dc). The approver side
    stamps ``AuthContext.subject`` verbatim, never the request body.

    Args:
        request: A FastAPI / Starlette request object. The function
            only reads ``request.state.auth_context``.

    Returns:
        ActorContext for HTTP submitter / approver lanes.

    Raises:
        ValueError: If no AuthContext is attached or the auth_method
            is the reserved ``internal`` value (anti-forgery).
    """
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        raise ValueError(
            "actor_from_request: no auth_context on request.state "
            "(auth is OFF or auth resolution failed)"
        )
    auth_method = getattr(auth_ctx, "auth_method", "")
    if auth_method == AUTH_METHOD_INTERNAL:
        raise ValueError(
            "actor_from_request: auth_method='internal' is reserved for "
            "in-process submitters; an HTTP request presenting internal "
            "auth is a forgery attempt (S16)"
        )
    if auth_method not in {AUTH_METHOD_SESSION, AUTH_METHOD_BEARER}:
        raise ValueError(
            f"actor_from_request: unknown auth_method {auth_method!r}"
        )
    subject = getattr(auth_ctx, "subject", "")
    if not subject:
        raise ValueError(
            "actor_from_request: AuthContext.subject is empty"
        )
    return ActorContext(
        subject=subject,
        auth_method=auth_method,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Effective-row dataclass (A8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffectiveRow:
    """One row of ``get_effective_rows`` output (A8).

    Captures the artifact name plus the selected + latest-approved
    version ids + content (any of which may be ``None`` — the key
    has no selected / no approved row yet). The fingerprint tuple
    is computed by the caller from the sorted
    ``(artifact, selected_version_id, latest_approved_version_id)``
    triples; the ``content`` fields carry the F6-resolved body
    (``selected.content`` > ``latest_approved.content`` > ``None``)
    so the loader hook can replace the file content without a
    second read.

    Attributes:
        artifact: Artifact key (one of ``PROMPT_ARTIFACTS``).
        selected_version_id: The currently selected row's PK
            (``is_selected=1`` for the key), or ``None``.
        selected_content: The selected row's body, or ``None``.
        latest_approved_version_id: The latest-approved row's PK
            (``MAX(version) WHERE state='approved'``), or ``None``.
        latest_approved_content: The latest-approved row's body,
            or ``None``.
    """

    artifact: str
    selected_version_id: Optional[str] = None
    selected_content: Optional[str] = None
    latest_approved_version_id: Optional[str] = None
    latest_approved_content: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Repository
# ─────────────────────────────────────────────────────────────────────────────


# Max retry attempts for INSERT-under-UNIQUE contention (A7/C5/G-4e).
# Bumped to 8 — under heavy concurrency (10+ threads racing on the
# same key) the first read of MAX(version) can lose multiple races
# before a write succeeds. 8 absorbs the realistic contention
# window; any deeper stall is a sign of contention worth surfacing.
# 2026-10-13 PG-concurrency fix: the per-key advisory lock
# (``_acquire_key_lock``) closes the steady-state race on PG, so
# this budget is now the BACKSTOP (A7 amendment db9f9e371 —
# bounded at 8, NOT raised; exhaustion still re-raises).
_MAX_INSERT_RETRIES = 8


class PromptVersionRepository:
    """Sync repository for the prompt-version + audit-event tables.

    Constructor takes ONLY the shared engine. Every public method
    opens its own short-lived ``Session`` and commits; mutating
    methods wrap writes in ``engine.begin()`` so the audit row
    commits atomically with the mutation (A3).

    All methods are synchronous; the service facade at
    :mod:`daemon.services.prompt_version_service` bridges to async
    via ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine) -> None:
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database. The same engine should be
                shared across all repositories to avoid SQLite lock
                contention.
        """
        self.engine = engine

    # ─────────────────────────────────────────────────────────────────
    # Validation + small helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_state(state: str) -> None:
        if state not in PROMPT_STATES:
            raise ValueError(
                f"state must be one of {sorted(PROMPT_STATES)}, "
                f"got {state!r}"
            )

    @staticmethod
    def _validate_event_type(event_type: str) -> None:
        if event_type not in PROMPT_AUDIT_EVENT_TYPES:
            raise ValueError(
                f"event_type must be one of {sorted(PROMPT_AUDIT_EVENT_TYPES)}, "
                f"got {event_type!r}"
            )

    @staticmethod
    def _validate_transition(from_state: str, to_state: str, version_id: str) -> None:
        if (from_state, to_state) not in VALID_TRANSITIONS:
            raise InvalidTransitionError(
                version_id=version_id,
                from_state=from_state,
                to_state=to_state,
            )

    def _acquire_key_lock(
        self,
        conn: Any,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> None:
        """Serialize per-key read-modify-write writers (R3, 2026-10-13).

        Dialect-gated — MUST be the FIRST statement inside the
        caller's ``engine.begin()`` transaction, before any read of
        the key's rows:

        * **PostgreSQL** — ``pg_advisory_xact_lock`` over
          :func:`_lock_key(agent_id, version_tag, artifact)`. The
          transaction-scoped advisory lock BLOCKS until acquired,
          auto-releases at commit/rollback (no cleanup path, no
          session-state leak on exceptions), and — unlike
          ``SELECT … FOR UPDATE`` — locks the ABSENT-ROW case (a
          key with no rows yet, e.g. the first-ever ``set_selection``
          or first ``create_draft`` racing another writer on the
          same key). Single lock per transaction ⇒ no lock-ordering
          cycles ⇒ no deadlock posture. Writers on DIFFERENT keys
          never contend (per-key granularity).
        * **SQLite** — no-op. The database-level single-writer
          serialization already orders these writers; the SC8
          SQLite legs have never reproduced either defect (verified,
          not assumed — both parity legs of the concurrency tests
          run against real file-SQLite WAL engines).

        Callers: every read-modify-write writer that can race on a
        key — ``create_draft``, ``create_and_submit``,
        ``set_selection``, ``clear_selection``. NOT taken by:
        ``atomic_transition`` (pure single-statement CAS — the
        ``UPDATE … WHERE state = from_state`` rowcount check is the
        serialization), ``append_audit_event`` (insert-only), and
        the readers (no RMW).
        """
        if self.engine.dialect.name != "postgresql":
            return
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _lock_key(agent_id, version_tag, artifact)},
        )

    # ─────────────────────────────────────────────────────────────────
    # A2 surface — Phase B's compile target
    # ─────────────────────────────────────────────────────────────────

    # 1. create_draft ---------------------------------------------------------

    def create_draft(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: ActorContext,
        *,
        state: str = "draft",
    ) -> PromptVersion:
        """Insert a new prompt-version row (A2 / A7).

        New-row version assignment = INSERT under the
        ``UNIQUE(agent_id, version_tag, artifact, version)`` constraint
        + retry-on-IntegrityError (recompute ``max(version)``, retry
        ≤ 3) — kills the ``SELECT MAX(version)+1`` race (A7/C5/G-4e).

        Args:
            agent_id: Resolved agent id.
            version_tag: Version tag (``""`` for base; F9 — ``NULL``/``""``
                normalize to ``""`` at write time).
            artifact: Artifact key (one of ``PROMPT_ARTIFACTS``).
            content: Override body (markdown).
            actor: Submitter identity (use ``actor_from_instance`` for
                in-process callers; ``actor_from_request`` for HTTP).
            state: Initial state — defaults to ``"draft"``;
                Phase B uses the same primitive to seed
                ``"pending_approval"`` rows if it chooses (the A1
                state machine allows the "submit for approval"
                shortcut as a single composite).

        Returns:
            The newly inserted :class:`PromptVersion` row.

        Raises:
            ValueError: On bad ``state`` or after the retry budget
                is exhausted (caller surfaces the contention).
        """
        self._validate_state(state)
        version_tag = _normalize_version_tag(version_tag)
        content_hash = _sha256_hex(content)
        now = _now_iso()

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_INSERT_RETRIES):
            try:
                with self.engine.begin() as conn:
                    # Per-key serialization first (R3) — the
                    # MAX(version) read below must not overlap a
                    # concurrent writer on the same key (F-2).
                    self._acquire_key_lock(conn, agent_id, version_tag, artifact)
                    # Recompute max(version) under the txn; safe
                    # because the UNIQUE constraint enforces the
                    # final answer.
                    row = conn.execute(
                        select(func.max(col(PromptVersion.version)))
                        .select_from(PromptVersion)
                        .where(
                            col(PromptVersion.agent_id) == agent_id,
                            col(PromptVersion.version_tag) == version_tag,
                            col(PromptVersion.artifact) == artifact,
                        )
                    ).one()
                    next_version = (row[0] or 0) + 1

                    version_id = str(uuid.uuid4())
                    prompt_version = PromptVersion(
                        version_id=version_id,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version=next_version,
                        state=state,
                        content=content,
                        content_hash=content_hash,
                        is_selected=0,
                        submitter_instance_id=actor.instance_id,
                        submitter_agent_id=actor.agent_id if actor.is_internal() else None,
                        submitter_version_tag=_normalize_version_tag(actor.version_tag) if actor.is_internal() else "",
                        approver_subject=None,
                        approver_auth_method=None,
                        created_at=now,
                        updated_at=now,
                    )
                    conn.execute(
                        PromptVersion.__table__.insert().values(
                            version_id=prompt_version.version_id,
                            agent_id=prompt_version.agent_id,
                            version_tag=prompt_version.version_tag,
                            artifact=prompt_version.artifact,
                            version=prompt_version.version,
                            state=prompt_version.state,
                            content=prompt_version.content,
                            content_hash=prompt_version.content_hash,
                            is_selected=prompt_version.is_selected,
                            submitter_instance_id=prompt_version.submitter_instance_id,
                            submitter_agent_id=prompt_version.submitter_agent_id,
                            submitter_version_tag=prompt_version.submitter_version_tag,
                            approver_subject=prompt_version.approver_subject,
                            approver_auth_method=prompt_version.approver_auth_method,
                            created_at=prompt_version.created_at,
                            updated_at=prompt_version.updated_at,
                        )
                    )
                    # Audit row stamped INSIDE the same txn (A3).
                    self._append_audit_in_conn(
                        conn,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="CREATED",
                        actor=actor,
                        payload={
                            "state": state,
                            "version": next_version,
                        },
                    )
                logger.info(
                    f"prompt_version.create_draft ok: "
                    f"agent_id={agent_id} version_tag={version_tag!r} "
                    f"artifact={artifact} version={next_version} "
                    f"state={state} version_id={version_id} "
                    f"attempt={attempt + 1}"
                )
                # Return the freshly-inserted ORM instance constructed
                # in-memory — avoids the engine.begin()→Session.get
                # cross-connection round-trip that, under heavy
                # contention on a single shared engine connection
                # (SQLite StaticPool + threading), can return None
                # for rows that DID commit. The DB truth is the
                # ``engine.begin()`` block exit; the in-memory
                # model carries the same values we just wrote.
                return prompt_version
            except IntegrityError as e:
                last_error = e
                logger.warning(
                    f"prompt_version.create_draft retry: "
                    f"agent_id={agent_id} artifact={artifact} "
                    f"attempt={attempt + 1}/{_MAX_INSERT_RETRIES} "
                    f"err={type(e).__name__}"
                )
                continue
        # Retry budget exhausted — re-raise the last IntegrityError.
        assert last_error is not None
        raise last_error

    # 3. atomic_transition ----------------------------------------------------

    def atomic_transition(
        self,
        version_id: str,
        from_state: str,
        to_state: str,
        actor: ActorContext,
        *,
        approver_subject: Optional[str] = None,
        approver_auth_method: Optional[str] = None,
    ) -> PromptVersion:
        """Generic guarded state transition (A2 / A1).

        Validates ``(from_state, to_state)`` is in
        :data:`VALID_TRANSITIONS`, then UPDATEd under a row-level
        CAS (only the row whose state matches ``from_state`` gets
        updated — concurrent transitions on the same version_id
        cannot both succeed). The audit row carries the transition
        payload + the approver identity when transitioning into
        ``approved`` / ``rejected``.

        Args:
            version_id: The target :class:`PromptVersion` PK.
            from_state: Required current state — acts as the CAS
                predicate.
            to_state: Target state (must be in
                :data:`VALID_TRANSITIONS` paired with ``from_state``).
            actor: Caller identity (``actor_from_request`` for
                approval / rejection HTTP lanes;
                ``actor_from_instance`` for in-process recall).
            approver_subject: Required for ``to_state in
                {'approved', 'rejected'}`` — the HTTP approver
                ``AuthContext.subject`` (Phase A writes the column
                shape; Phase B stamps it from the request handler).
            approver_auth_method: Required for ``to_state in
                {'approved', 'rejected'}`` — the HTTP approver
                ``AuthContext.auth_method`` (``'session'`` /
                ``'bearer'``).

        Returns:
            The updated :class:`PromptVersion` row.

        Raises:
            InvalidTransitionError: If ``(from_state, to_state)``
                is not in :data:`VALID_TRANSITIONS`.
            LookupError: If no row matches ``(version_id, from_state)``
                (row missing OR state already moved by a concurrent
                transition).
        """
        self._validate_state(from_state)
        self._validate_state(to_state)
        self._validate_transition(from_state, to_state, version_id)

        if to_state in {"approved", "rejected"}:
            if not approver_subject or not approver_auth_method:
                raise ValueError(
                    f"transition to {to_state!r} requires approver_subject "
                    f"AND approver_auth_method (HTTP AuthContext stamps; "
                    f"the repo refuses to invent approver identity)"
                )
            if approver_auth_method not in {AUTH_METHOD_SESSION, AUTH_METHOD_BEARER}:
                raise ValueError(
                    f"approver_auth_method must be session/bearer; got "
                    f"{approver_auth_method!r}"
                )

        event_type_map = {
            ("draft", "pending_approval"): "SUBMITTED",
            ("pending_approval", "approved"): "APPROVED",
            ("pending_approval", "rejected"): "REJECTED",
            ("pending_approval", "draft"): "RETRACTED",
            ("draft", "archived"): "ARCHIVED",
        }
        event_type = event_type_map.get((from_state, to_state), "CREATED")
        self._validate_event_type(event_type)

        now = _now_iso()
        with self.engine.begin() as conn:
            # CAS: only update if state still matches.
            upd = (
                PromptVersion.__table__.update()
                .where(
                    col(PromptVersion.version_id) == version_id,
                    col(PromptVersion.state) == from_state,
                )
                .values(
                    state=to_state,
                    updated_at=now,
                    approver_subject=approver_subject if to_state in {"approved", "rejected"} else None,
                    approver_auth_method=approver_auth_method if to_state in {"approved", "rejected"} else None,
                )
            )
            result = conn.execute(upd)
            if result.rowcount == 0:
                # Either row missing OR state already moved — surface
                # the same error shape for both so callers don't have
                # to distinguish (the message differs; the lookup /
                # CAS race is the same diagnostic).
                existing = conn.execute(
                    select(col(PromptVersion.state))
                    .select_from(PromptVersion)
                    .where(col(PromptVersion.version_id) == version_id)
                ).one_or_none()
                if existing is None:
                    raise LookupError(
                        f"prompt_version {version_id} not found"
                    )
                raise InvalidTransitionError(
                    version_id=version_id,
                    from_state=existing[0],
                    to_state=to_state,
                )

            # Audit row stamped INSIDE the same txn (A3).
            payload = {
                "from_state": from_state,
                "to_state": to_state,
            }
            if approver_subject is not None:
                payload["approver_subject"] = approver_subject
            if approver_auth_method is not None:
                payload["approver_auth_method"] = approver_auth_method
            self._append_audit_in_conn(
                conn,
                agent_id=self._get_field_in_conn(conn, version_id, "agent_id"),
                version_tag=self._get_field_in_conn(conn, version_id, "version_tag"),
                artifact=self._get_field_in_conn(conn, version_id, "artifact"),
                version_id=version_id,
                event_type=event_type,
                actor=actor,
                payload=payload,
            )

        with Session(self.engine) as session:
            row = session.get(PromptVersion, version_id)
            assert row is not None  # we just UPDATEd it
            return row

    # 2. submit_for_approval --------------------------------------------------

    def submit_for_approval(
        self,
        version_id: str,
        actor: ActorContext,
    ) -> PromptVersion:
        """Transition ``draft → pending_approval`` (A2)."""
        return self.atomic_transition(
            version_id=version_id,
            from_state="draft",
            to_state="pending_approval",
            actor=actor,
        )

    # 4. approve --------------------------------------------------------------

    def approve(
        self,
        version_id: str,
        actor: ActorContext,
    ) -> PromptVersion:
        """Transition ``pending_approval → approved`` (A2).

        ``actor`` MUST be an HTTP actor (``actor_from_request``) —
        only ``session`` / ``bearer`` approvers may approve (F4:
        agents cannot self-approve). The repo refuses to invent
        approver identity from an internal actor.
        """
        if actor.is_internal():
            raise ValueError(
                "approve(): only HTTP actors (session/bearer) may approve; "
                "internal actors cannot satisfy the human-only approval "
                "rule (decisions.md F4)"
            )
        return self.atomic_transition(
            version_id=version_id,
            from_state="pending_approval",
            to_state="approved",
            actor=actor,
            approver_subject=actor.subject,
            approver_auth_method=actor.auth_method,
        )

    # 5. reject ---------------------------------------------------------------

    def reject(
        self,
        version_id: str,
        actor: ActorContext,
    ) -> PromptVersion:
        """Transition ``pending_approval → rejected`` (A2).

        ``actor`` MUST be an HTTP actor (``actor_from_request``) —
        mirror of :meth:`approve`.
        """
        if actor.is_internal():
            raise ValueError(
                "reject(): only HTTP actors (session/bearer) may reject; "
                "internal actors cannot satisfy the human-only approval "
                "rule (decisions.md F4)"
            )
        return self.atomic_transition(
            version_id=version_id,
            from_state="pending_approval",
            to_state="rejected",
            actor=actor,
            approver_subject=actor.subject,
            approver_auth_method=actor.auth_method,
        )

    # 6. set_selection -------------------------------------------------------

    def set_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        actor: ActorContext,
    ) -> None:
        """Set the per-key one-selection-pointer (A2 / F5 / R3).

        Wrapped in a single ``engine.begin()`` so the
        clear-all-then-set-one sequence is atomic, AND serialized
        per-key via ``_acquire_key_lock`` (the atomic wrapper alone
        does not order two concurrent selections on PG READ
        COMMITTED — F-1). NULL-distinct in UNIQUE on both
        drivers forbids a NULL-aware partial unique
        index (research-findings-db-api.md §1 PITFALLS) — the repo
        holds the only enforcement path.

        The target row's ``version_id`` MUST exist and be in
        state ``approved`` (F6 — only approved content can be
        selected for the effective prompt).

        Audit rows: ``CLEARED`` (one row per previously-selected
        row this clears) + ``SELECTED`` (one row for the new
        pointer), all inside the same txn.

        Args:
            agent_id: Resolved agent id.
            version_tag: Version tag (``""`` for base).
            artifact: Artifact key.
            version_id: The :class:`PromptVersion` PK to select.
                MUST be in state ``approved``.
            actor: Caller identity.

        Raises:
            LookupError: If the target row is missing or not in
                state ``approved``.
        """
        version_tag = _normalize_version_tag(version_tag)
        now = _now_iso()
        with self.engine.begin() as conn:
            # Per-key serialization FIRST (R3 / F-1 fix) — the
            # read-clear-set sequence below must not overlap a
            # concurrent set_selection/clear_selection on the same
            # key. pg_advisory_xact_lock also covers the absent-row
            # gap (first selection on a key with no pointer yet)
            # that SELECT … FOR UPDATE cannot lock.
            self._acquire_key_lock(conn, agent_id, version_tag, artifact)
            # Verify target row exists + is approved.
            target = conn.execute(
                select(
                    col(PromptVersion.version_id),
                    col(PromptVersion.state),
                )
                .select_from(PromptVersion)
                .where(
                    col(PromptVersion.version_id) == version_id,
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                )
            ).one_or_none()
            if target is None:
                raise LookupError(
                    f"prompt_version {version_id} not found for key "
                    f"({agent_id}, {version_tag!r}, {artifact})"
                )
            if target[1] != "approved":
                raise ValueError(
                    f"cannot select prompt_version {version_id}: state "
                    f"is {target[1]!r}, only approved versions may be "
                    f"selected (F6)"
                )

            # Find currently-selected rows for this key (audit
            # provenance for the CLEARED event).
            previously_selected = list(conn.execute(
                select(col(PromptVersion.version_id))
                .select_from(PromptVersion)
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.is_selected) == 1,
                    col(PromptVersion.version_id) != version_id,
                )
            ))
            previously_selected_ids = [r[0] for r in previously_selected]

            # Clear all is_selected for the key, then set the new one.
            conn.execute(
                PromptVersion.__table__.update()
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.is_selected) == 1,
                )
                .values(is_selected=0, updated_at=now)
            )
            conn.execute(
                PromptVersion.__table__.update()
                .where(col(PromptVersion.version_id) == version_id)
                .values(is_selected=1, updated_at=now)
            )

            # Audit rows (A3): CLEARED for the prior pointers,
            # SELECTED for the new one — all in this txn.
            for prior_id in previously_selected_ids:
                self._append_audit_in_conn(
                    conn,
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    version_id=prior_id,
                    event_type="CLEARED",
                    actor=actor,
                    payload={"replaced_by": version_id},
                )
            self._append_audit_in_conn(
                conn,
                agent_id=agent_id,
                version_tag=version_tag,
                artifact=artifact,
                version_id=version_id,
                event_type="SELECTED",
                actor=actor,
                payload={},
            )

    # 7. clear_selection -----------------------------------------------------

    def clear_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        actor: ActorContext,
    ) -> None:
        """Clear the per-key one-selection-pointer (A2 / F5 / R3).

        Wrapped in a single ``engine.begin()`` so the clear-all
        sequence is atomic. Emits one ``CLEARED`` audit row per
        previously-selected row (inside the same txn).

        Args:
            agent_id: Resolved agent id.
            version_tag: Version tag (``""`` for base).
            artifact: Artifact key.
            actor: Caller identity.
        """
        version_tag = _normalize_version_tag(version_tag)
        now = _now_iso()
        with self.engine.begin() as conn:
            # Per-key serialization FIRST (R3) — a concurrent
            # set_selection must not interleave between the read of
            # previously-selected rows and the clear UPDATE (it
            # would clear a pointer set after the read).
            self._acquire_key_lock(conn, agent_id, version_tag, artifact)
            previously_selected = list(conn.execute(
                select(col(PromptVersion.version_id))
                .select_from(PromptVersion)
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.is_selected) == 1,
                )
            ))
            previously_selected_ids = [r[0] for r in previously_selected]

            if not previously_selected_ids:
                # No-op — but log + still no audit row (the invariant
                # is preserved).
                return

            conn.execute(
                PromptVersion.__table__.update()
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.is_selected) == 1,
                )
                .values(is_selected=0, updated_at=now)
            )

            for prior_id in previously_selected_ids:
                self._append_audit_in_conn(
                    conn,
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    version_id=prior_id,
                    event_type="CLEARED",
                    actor=actor,
                    payload={"cleared_via": "clear_selection"},
                )

    # 8. get_selection -------------------------------------------------------

    def get_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[PromptVersion]:
        """Fetch the currently-selected row for the key (A2).

        Returns ``None`` if no selection pointer is set.
        """
        version_tag = _normalize_version_tag(version_tag)
        with Session(self.engine) as session:
            return session.exec(
                select(PromptVersion)
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.is_selected) == 1,
                )
            ).one_or_none()

    # 9. list_versions -------------------------------------------------------

    def list_versions(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PromptVersion]:
        """List versions for a key, newest first (A2)."""
        version_tag = _normalize_version_tag(version_tag)
        with Session(self.engine) as session:
            stmt = (
                select(PromptVersion)
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                )
                .order_by(col(PromptVersion.version).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    # 10. get_version ---------------------------------------------------------

    def get_version(self, version_id: str) -> Optional[PromptVersion]:
        """Fetch a single row by PK (A2)."""
        with Session(self.engine) as session:
            return session.get(PromptVersion, version_id)

    # 11. get_latest_approved -------------------------------------------------

    def get_latest_approved(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[PromptVersion]:
        """Fetch the latest-approved row for the key (A2 / F6)."""
        version_tag = _normalize_version_tag(version_tag)
        with Session(self.engine) as session:
            return session.exec(
                select(PromptVersion)
                .where(
                    col(PromptVersion.agent_id) == agent_id,
                    col(PromptVersion.version_tag) == version_tag,
                    col(PromptVersion.artifact) == artifact,
                    col(PromptVersion.state) == "approved",
                )
                .order_by(col(PromptVersion.version).desc())
                .limit(1)
            ).one_or_none()

    # 12. get_effective -------------------------------------------------------

    def get_effective(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[str]:
        """Resolve the effective prompt content (A2 / F6 / A8).

        Precedence: ``selected_version.content`` >
        ``latest_approved.content`` > ``None``.

        Implementation note: this method reads
        :meth:`get_effective_rows` for the artifact and applies the
        F6 precedence in Python (no second SELECT needed — A8's
        one-SELECT guarantee is preserved because the rows for ALL
        artifacts for the (agent_id, version_tag) pair are read in
        a single query).
        """
        rows = self.get_effective_rows(agent_id, version_tag)
        for row in rows:
            if row.artifact == artifact:
                if row.selected_content is not None:
                    return row.selected_content
                if row.latest_approved_content is not None:
                    return row.latest_approved_content
                return None
        return None

    # A8. get_effective_rows --------------------------------------------------

    def get_effective_rows(
        self,
        agent_id: str,
        version_tag: str,
    ) -> list[EffectiveRow]:
        """ONE-SELECT effective read for the (agent_id, version_tag) pair (A8).

        Returns the selected + latest-approved row ids + content for
        every artifact that has DB rows, sorted by artifact name.
        The caller (loader hook) computes the synthetic
        ``db_versions_fingerprint`` from the sorted
        ``(artifact, version_id)`` pairs and the cache-hit equality
        check busts the cache whenever the fingerprint changes.

        All data comes from ONE SELECT — a CTE-driven LEFT JOIN that
        fetches the selected row per artifact and the latest-approved
        row per artifact in a single query. Closes the
        fingerprint×content TOCTOU window (A8 / C2 — the loader
        cache fingerprint and the effective content are guaranteed
        to be from the same DB snapshot).
        """
        version_tag = _normalize_version_tag(version_tag)
        # Single SELECT: a UNION of selected + latest_approved
        # subqueries, joined back to artifact name. SQLModel/SQLAlchemy
        # composes this into ONE database round-trip.
        # We use a direct text() expression for clarity — the
        # alternative (Python aggregation over per-artifact queries)
        # would break the A8 single-SELECT guarantee.
        sql = text(
            """
            SELECT
              pv.artifact AS artifact,
              sel.version_id AS sel_version_id,
              sel.content AS sel_content,
              appr.version_id AS appr_version_id,
              appr.content AS appr_content
            FROM (
              SELECT DISTINCT artifact
              FROM prompt_versions
              WHERE agent_id = :agent_id AND version_tag = :version_tag
            ) pv
            LEFT JOIN prompt_versions sel
              ON sel.agent_id = :agent_id
              AND sel.version_tag = :version_tag
              AND sel.artifact = pv.artifact
              AND sel.is_selected = 1
            LEFT JOIN prompt_versions appr
              ON appr.agent_id = :agent_id
              AND appr.version_tag = :version_tag
              AND appr.artifact = pv.artifact
              AND appr.state = 'approved'
              AND appr.version = (
                SELECT MAX(appr2.version) FROM prompt_versions appr2
                WHERE appr2.agent_id = :agent_id
                  AND appr2.version_tag = :version_tag
                  AND appr2.artifact = pv.artifact
                  AND appr2.state = 'approved'
              )
            ORDER BY pv.artifact
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(
                sql,
                {"agent_id": agent_id, "version_tag": version_tag},
            )
            rows = result.fetchall()

        out: list[EffectiveRow] = []
        for r in rows:
            artifact = r[0]
            sel_id = r[1]
            sel_content = r[2]
            appr_id = r[3]
            appr_content = r[4]
            out.append(
                EffectiveRow(
                    artifact=artifact,
                    selected_version_id=sel_id,
                    selected_content=sel_content,
                    latest_approved_version_id=appr_id,
                    latest_approved_content=appr_content,
                )
            )
        return out

    # 13. append_audit_event --------------------------------------------------

    def append_audit_event(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        event_type: str,
        actor: ActorContext,
        payload: Optional[dict] = None,
    ) -> PromptAuditEvent:
        """Append a single audit row (A2).

        Standalone variant for callers that need to record an event
        outside a repo-level mutation (rare; mostly for the
        ``AGENT_CREATE_TEMPLATE_COPIED`` event when an agent is
        created). Mutating repo methods stamp their audit rows
        INSIDE the same ``engine.begin()`` as the mutation; do NOT
        use this method to append audit rows for those — call the
        mutating method instead.
        """
        self._validate_event_type(event_type)
        version_tag = _normalize_version_tag(version_tag)
        event_id = str(uuid.uuid4())
        now = _now_iso()
        with self.engine.begin() as conn:
            self._append_audit_in_conn(
                conn,
                agent_id=agent_id,
                version_tag=version_tag,
                artifact=artifact,
                version_id=version_id,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
                event_id=event_id,
                created_at=now,
            )
        with Session(self.engine) as session:
            row = session.get(PromptAuditEvent, event_id)
            assert row is not None
            return row

    # 14. list_audit_events --------------------------------------------------

    def list_audit_events(
        self,
        agent_id: Optional[str] = None,
        version_tag: Optional[str] = None,
        artifact: Optional[str] = None,
        version_id: Optional[str] = None,
        event_type: Optional[str] = None,
        actor_subject: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromptAuditEvent], int]:
        """List audit events, newest first, with optional filters (Phase B T5 — endpoint 12).

        Mirrors the ``list_versions`` / ``skill_bank.py:72-108``
        pagination template: returns ``(items, total)`` where
        ``items`` is the page of rows (DESC by ``created_at``) and
        ``total`` is the unfiltered-by-pagination count for the same
        filter set.

        All filters are optional and combine with AND. The
        ``version_tag`` filter passes through :func:`_normalize_version_tag`
        so ``None`` and ``""`` resolve to ``""`` (the F9 / A7
        normalization) and the query stays consistent with the
        mutating methods.

        Uses the Phase A indexes (query-friendly by design —
        ``idx_prompt_audit_events_key_created`` covers
        ``(agent_id, version_tag, artifact, created_at)``; the
        ``version_id`` lookup rides ``idx_prompt_audit_events_version``;
        the ``event_type`` lookup rides
        ``idx_prompt_audit_events_type``). EXPLAIN QUERY PLAN on
        SQLite for the filtered shape is part of T5 acceptance
        (cheap sanity; reported alongside the implementation).

        Args:
            agent_id: Optional — match the row's ``agent_id``.
            version_tag: Optional — match the row's ``version_tag``
                (``""`` normalizes to ``""``).
            artifact: Optional — match the row's ``artifact``.
            version_id: Optional — match the row's ``version_id``
                (the per-version timeline lookup).
            event_type: Optional — match the row's ``event_type``
                (one of ``PROMPT_AUDIT_EVENT_TYPES``; not validated
                here so a bad value returns ``[]`` like a no-match
                query — the repo treats ``event_type`` as data, not
                as a privileged column).
            actor_subject: Optional — match the row's
                ``actor_subject`` (HTTP lane subject or in-process
                instance id).
            limit: Page size (default 50, matching the
                ``audit_endpoint`` contract).
            offset: Page offset (default 0).

        Returns:
            ``(items, total)`` — ``items`` is a list of
            :class:`PromptAuditEvent` rows ordered by ``created_at
            DESC`` (newest first); ``total`` is the unfiltered-by-
            pagination count for the same WHERE clause.
        """
        if event_type is not None:
            # Match the mutating-method shape — a known event type
            # is the only valid filter value; the endpoint layer
            # also has the path-param allowlist (S4) so a malformed
            # value never reaches here.
            self._validate_event_type(event_type)
        norm_version_tag = (
            _normalize_version_tag(version_tag) if version_tag is not None else None
        )

        with Session(self.engine) as session:
            where_clauses = []
            if agent_id is not None:
                where_clauses.append(col(PromptAuditEvent.agent_id) == agent_id)
            if norm_version_tag is not None:
                where_clauses.append(
                    col(PromptAuditEvent.version_tag) == norm_version_tag
                )
            if artifact is not None:
                where_clauses.append(col(PromptAuditEvent.artifact) == artifact)
            if version_id is not None:
                where_clauses.append(
                    col(PromptAuditEvent.version_id) == version_id
                )
            if event_type is not None:
                where_clauses.append(
                    col(PromptAuditEvent.event_type) == event_type
                )
            if actor_subject is not None:
                where_clauses.append(
                    col(PromptAuditEvent.actor_subject) == actor_subject
                )

            # Total count for the same WHERE clause (no LIMIT/OFFSET).
            # ``select(func.count(col))`` returns a single-column
            # scalar SELECT — ``session.exec(...).one()`` returns
            # the int directly (sqlmodel ``Session.exec`` unwraps
            # single-column selects into ``ScalarResult``; the
            # existing repo pattern at :1457 doesn't apply to
            # sqlmodel ``Session``).
            count_stmt = select(func.count(col(PromptAuditEvent.event_id)))
            if where_clauses:
                count_stmt = count_stmt.where(*where_clauses)
            total = int(session.exec(count_stmt).one() or 0)

            # Page of rows (newest first).
            items_stmt = select(PromptAuditEvent)
            if where_clauses:
                items_stmt = items_stmt.where(*where_clauses)
            items_stmt = (
                items_stmt
                .order_by(col(PromptAuditEvent.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
            items = list(session.exec(items_stmt))

        return items, total

    # A5/C4. create_and_submit ------------------------------------------------

    def create_and_submit(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content_or_mutator: Union[str, Callable[[Optional[str]], str]],
        actor: ActorContext,
        *,
        file_fallback: Optional[str] = None,
    ) -> PromptVersion:
        """Composite ``create_draft`` + ``submit_for_approval`` (A5 / C4).

        Used by ``inner_soul`` and ``agent_mother.agent_modify``
        rewrites (Phase B) — the only submission path that goes
        through this repo's atomic transition machinery. Serialized
        per-key via ``_acquire_key_lock`` inside the composite
        transaction (R3).

        Composition rule: ``new_content = f(effective_now, ...)``
        where ``effective_now = selected.content > latest_approved.
        content > file_fallback``. ``content_or_mutator`` is either
        a full content string (used as-is) or a callable applied to
        ``effective_now`` (the agent mutates the effective chain
        content — e.g. ``soul.append_learned(...)``).

        Consecutive un-approved calls chain on
        ``max(version) WHERE state IN ('draft', 'pending_approval')``
        — the prior pending row gets archived (≤ 1 active pending
        per key).

        All operations happen in one ``engine.begin()`` so the
        archive + insert + submit transition + audit rows commit
        atomically.

        Args:
            agent_id: Resolved agent id.
            version_tag: Version tag (``""`` for base).
            artifact: Artifact key.
            content_or_mutator: Full content (str) OR a callable
                ``(effective_content: Optional[str]) -> new_content: str``
                applied to the resolved effective chain content.
            actor: Submitter identity.
            file_fallback: The on-disk template content for the
                artifact — the bottom of the F6 precedence chain.
                Pass ``None`` if the file does not exist (rare; the
                loader has no template to fall back to either in
                that case).

        Returns:
            The newly inserted :class:`PromptVersion` row in state
            ``pending_approval``.

        Raises:
            ValueError: On bad inputs or after the retry budget is
                exhausted (caller surfaces the contention).
        """
        # Resolve effective content for the mutator path.
        if callable(content_or_mutator):
            effective_now = self.get_effective(agent_id, version_tag, artifact)
            if effective_now is None:
                effective_now = file_fallback
            new_content = content_or_mutator(effective_now)
        else:
            new_content = content_or_mutator

        version_tag = _normalize_version_tag(version_tag)
        content_hash = _sha256_hex(new_content)
        now = _now_iso()

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_INSERT_RETRIES):
            try:
                with self.engine.begin() as conn:
                    # Per-key serialization first (R3) — the
                    # prior-active read + MAX(version) + INSERT below
                    # must not overlap a concurrent writer on the
                    # same key (keeps "≤ 1 active pending per key"
                    # and closes the F-2 race window; the retry
                    # budget remains the backstop).
                    self._acquire_key_lock(conn, agent_id, version_tag, artifact)
                    # Find the prior active (draft OR pending) row
                    # for archival (A5/C4 "≤ 1 active pending per key").
                    prior_active = conn.execute(
                        select(
                            col(PromptVersion.version_id),
                            col(PromptVersion.version),
                            col(PromptVersion.state),
                        )
                        .select_from(PromptVersion)
                        .where(
                            col(PromptVersion.agent_id) == agent_id,
                            col(PromptVersion.version_tag) == version_tag,
                            col(PromptVersion.artifact) == artifact,
                            col(PromptVersion.state).in_(["draft", "pending_approval"]),
                        )
                        .order_by(col(PromptVersion.version).desc())
                        .limit(1)
                    ).one_or_none()

                    # Recompute max(version) — UNIQUE constraint is
                    # the final fence; the retry absorbs the race.
                    row = conn.execute(
                        select(func.max(col(PromptVersion.version)))
                        .select_from(PromptVersion)
                        .where(
                            col(PromptVersion.agent_id) == agent_id,
                            col(PromptVersion.version_tag) == version_tag,
                            col(PromptVersion.artifact) == artifact,
                        )
                    ).one()
                    next_version = (row[0] or 0) + 1

                    version_id = str(uuid.uuid4())
                    # Insert directly in 'pending_approval' state — the
                    # A5 composite skips the intermediate 'draft' row
                    # (the user-facing audit trail still has CREATED +
                    # SUBMITTED events).
                    conn.execute(
                        PromptVersion.__table__.insert().values(
                            version_id=version_id,
                            agent_id=agent_id,
                            version_tag=version_tag,
                            artifact=artifact,
                            version=next_version,
                            state="pending_approval",
                            content=new_content,
                            content_hash=content_hash,
                            is_selected=0,
                            submitter_instance_id=actor.instance_id,
                            submitter_agent_id=actor.agent_id if actor.is_internal() else None,
                            submitter_version_tag=_normalize_version_tag(actor.version_tag) if actor.is_internal() else "",
                            approver_subject=None,
                            approver_auth_method=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )

                    # Audit: CREATED (the new row), SUBMITTED (the
                    # transition), ARCHIVED (the prior pending row if
                    # any — A5/C4 ≤ 1 active pending per key).
                    self._append_audit_in_conn(
                        conn,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="CREATED",
                        actor=actor,
                        payload={
                            "state": "pending_approval",
                            "version": next_version,
                            "via": "create_and_submit",
                        },
                    )
                    self._append_audit_in_conn(
                        conn,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="SUBMITTED",
                        actor=actor,
                        payload={"from_state": "draft", "to_state": "pending_approval"},
                    )
                    if prior_active is not None:
                        prior_id = prior_active[0]
                        prior_version = prior_active[1]
                        prior_state = prior_active[2]
                        # Archive the prior pending row (CAS so a
                        # concurrent transition on that row isn't
                        # silently overwritten).
                        upd = (
                            PromptVersion.__table__.update()
                            .where(
                                col(PromptVersion.version_id) == prior_id,
                                col(PromptVersion.state).in_(["draft", "pending_approval"]),
                            )
                            .values(state="archived", updated_at=now)
                        )
                        result = conn.execute(upd)
                        if result.rowcount == 1:
                            self._append_audit_in_conn(
                                conn,
                                agent_id=agent_id,
                                version_tag=version_tag,
                                artifact=artifact,
                                version_id=prior_id,
                                event_type="ARCHIVED",
                                actor=actor,
                                payload={
                                    "from_state": prior_state,
                                    "to_state": "archived",
                                    "superseded_by": version_id,
                                    "superseded_version": next_version,
                                    "superseded_prior_version": prior_version,
                                },
                            )

                logger.info(
                    f"prompt_version.create_and_submit ok: "
                    f"agent_id={agent_id} version_tag={version_tag!r} "
                    f"artifact={artifact} version={next_version} "
                    f"version_id={version_id} attempt={attempt + 1}"
                )
                # Return the freshly-inserted ORM instance (see
                # ``create_draft`` for the rationale on avoiding a
                # cross-connection Session.get under contention).
                return PromptVersion(
                    version_id=version_id,
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    version=next_version,
                    state="pending_approval",
                    content=new_content,
                    content_hash=content_hash,
                    is_selected=0,
                    submitter_instance_id=actor.instance_id,
                    submitter_agent_id=actor.agent_id if actor.is_internal() else None,
                    submitter_version_tag=_normalize_version_tag(actor.version_tag) if actor.is_internal() else "",
                    approver_subject=None,
                    approver_auth_method=None,
                    created_at=now,
                    updated_at=now,
                )
            except IntegrityError as e:
                last_error = e
                logger.warning(
                    f"prompt_version.create_and_submit retry: "
                    f"agent_id={agent_id} artifact={artifact} "
                    f"attempt={attempt + 1}/{_MAX_INSERT_RETRIES} "
                    f"err={type(e).__name__}"
                )
                continue
        assert last_error is not None
        raise last_error

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers — execute against a live ``Connection`` so the
    # audit row commits inside the caller's ``engine.begin()`` block.
    # ─────────────────────────────────────────────────────────────────

    def _append_audit_in_conn(
        self,
        conn: Any,
        *,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        event_type: str,
        actor: ActorContext,
        payload: dict,
        event_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Insert one :class:`PromptAuditEvent` row inside the caller's txn."""
        self._validate_event_type(event_type)
        conn.execute(
            PromptAuditEvent.__table__.insert().values(
                event_id=event_id or str(uuid.uuid4()),
                agent_id=agent_id,
                version_tag=_normalize_version_tag(version_tag),
                artifact=artifact,
                version_id=version_id,
                event_type=event_type,
                actor_subject=actor.subject,
                actor_auth_method=actor.auth_method,
                payload=payload,
                created_at=created_at or _now_iso(),
            )
        )

    def _get_field_in_conn(
        self,
        conn: Any,
        version_id: str,
        field_name: str,
    ) -> Any:
        """Read a single column from a prompt_versions row inside the caller's txn."""
        colref = getattr(PromptVersion, field_name)
        result = conn.execute(
            select(colref)
            .select_from(PromptVersion)
            .where(col(PromptVersion.version_id) == version_id)
        ).one_or_none()
        if result is None:
            raise LookupError(
                f"prompt_version {version_id} not found while reading "
                f"{field_name}"
            )
        return result[0]


__all__ = [
    "PromptVersionRepository",
    "VALID_TRANSITIONS",
    "InvalidTransitionError",
    "ActorContext",
    "actor_from_instance",
    "actor_from_request",
    "AUTH_METHOD_SESSION",
    "AUTH_METHOD_BEARER",
    "AUTH_METHOD_INTERNAL",
    "EffectiveRow",
]