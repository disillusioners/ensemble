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
from typing import Any, Callable, NamedTuple, Optional, Union

from sqlalchemy import func, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .models import (
    PROMPT_AUDIT_EVENT_TYPES,
    PROMPT_ARTIFACT_CLASSES,
    PROMPT_ARTIFACTS,
    PROMPT_MEMORY_ARTIFACTS,
    PROMPT_STATES,
    PromptAuditEvent,
    PromptTemplateBaseline,
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


# USER DIRECTIVE #2 (2026-10-14) — the editable-state rule. EDIT and
# DELETE apply to ``draft`` / ``pending_approval`` rows ONLY. Leader
# ruling (binding composition with frozen A1): approved rows are
# append-only immutable history; rejected is terminal (resubmit = a
# new row); archived is a superseded side state. Hard-delete of
# approved history is deliberately NOT built (morning fork).
_EDITABLE_STATES: frozenset[str] = frozenset({"draft", "pending_approval"})


# User Directive #3 (2026-10-14, RESOLUTION-CORE) — the canonical
# class-resolution table and the typed guards the state-machine methods
# raise against MEMORY-class rows.
#
# Resolution precedence (loader.py:resolve_prompts_from_db + Ud#3
# binding block 3):
#
#   * CORE  (soul / rule / workflow / tools_note / tools / skill):
#       selected > latest_approved > template-file fallback
#
#   * MEMORY  (memory / user / growth / memories/<slug>):
#       selected > LATEST row (any state value: the row is
#       born-effective at creation) > template-file fallback
#       (empty post-sweep; empty-file boot-tolerance proven).
#
# The state machine governs CORE only. MEMORY rows are
# transition-free: created via a single ``create_memory_version``
# INSERT (no VALID_TRANSITIONS check, no ``pending_approval`` row,
# no audit row stamped under the state-machine event types; the
# audit row uses ``CREATED`` once, with class=MEMORY stamped on
# the payload). Approve / reject / submit_for_approval /
# atomic_transition raise :class:`MemoryClassError` on a
# MEMORY-class row — the typed error the loader / routers map to
# ``409 MEMORY_NOT_GOVERNED``.
#
# count_pending (the Persona UI badge feed, USER DIRECTIVE #2
# deliverable 1) counts CORE pending rows only; the ``MEMORY``
# class has no pending state by definition.
#
# set_selection / clear_selection work for BOTH classes — the
# selection pointer is the MEMORY rollback mechanism (a user can
# pin an older MEMORY row to "undo" an edit). R-B3 interplay: an
# EXPLICIT MEMORY selection SURVIVES the auto-reset path; only the
# un-selected default path resets.

ARTIFACT_CLASS_CORE: str = "CORE"
ARTIFACT_CLASS_MEMORY: str = "MEMORY"

ARTIFACT_CLASS_TYPES: frozenset[str] = PROMPT_ARTIFACT_CLASSES


def validate_artifact_class(artifact_class: str) -> None:
    """Raise :class:`ValueError` unless ``artifact_class`` is a known class.

    Single-source-of-truth validation for every point where
    an artifact_class string flows into a column or branch
    (:meth:`PromptVersionRepository.create_draft`,
    ``create_memory_version``, ``record_initial_baselines``,
    ``detect_template_drift``). Validation here means downstream
    code never re-rejects the same string — keeps the
    ``PROMPT_ARTIFACT_CLASSES`` contract stable.
    """
    if artifact_class not in ARTIFACT_CLASS_TYPES:
        raise ValueError(
            f"artifact_class must be one of {sorted(ARTIFACT_CLASS_TYPES)}, "
            f"got {artifact_class!r}"
        )


def class_for_artifact(artifact: str) -> str:
    """Return the canonical :class:`PROMPT_ARTIFACT_CLASSES` value for an artifact key.

    Single-source-of-truth mapping used by:

    * :meth:`PromptVersionRepository.create_draft` to stamp
      ``artifact_class`` at INSERT time (so the column carries the
      right default without caller bookkeeping).
    * ``BaselineService.record_initial_baselines`` /
      ``detect_template_drift`` to skip MEMORY-class artifacts
      (R-B2 CORE-only).
    * The MEMORY-creator path to construct an explicit class
      stamp.

    Mapping:

    * ``artifact in PROMPT_ARTIFACTS``           → ``'CORE'``
    * ``artifact in PROMPT_MEMORY_ARTIFACTS``    → ``'MEMORY'``
    * ``artifact.startswith("memories/")``       → ``'MEMORY'`` (per-file
      keying — each memory file is its own row class; selection = per-file
      pin)
    * anything else                              → ``'CORE'`` (default:
      the conservative-default bucket is the safe default).

    The fallback to ``'CORE'`` is INTENTIONAL for the
    ``create_and_submit`` path — a future artifact string that
    has not yet been classified lands in the governed set so the
    user explicitly reviews and approves, never silently bypasses
    approval. A future expansion would add the artifact to
    ``PROMPT_MEMORY_ARTIFACTS`` explicitly.
    """
    if artifact in PROMPT_ARTIFACTS:
        return ARTIFACT_CLASS_CORE
    if artifact in PROMPT_MEMORY_ARTIFACTS:
        return ARTIFACT_CLASS_MEMORY
    if artifact.startswith("memories/"):
        return ARTIFACT_CLASS_MEMORY
    return ARTIFACT_CLASS_CORE


class MemoryClassError(ValueError):
    """Raised when a state-machine operation targets a MEMORY-class row.

    User Directive #3, binding block 4: MEMORY rows are
    TRANSITION-FREE and born-effective at creation; the state
    machine (:data:`VALID_TRANSITIONS`) governs CORE rows only.

    The typed error surfaces at the service layer
    (``approve`` / ``reject`` / ``submit_for_approval`` /
    ``atomic_transition`` / ``update_version_content`` /
    ``delete_version`` — see
    :mod:`daemon.services.prompt_version_service`) and the HTTP
    routers map it to ``409 MEMORY_NOT_GOVERNED``. Inherits from
    :class:`ValueError` so callers can keep a single
    ``except ValueError`` umbrella while still discriminating
    via :class:`MemoryClassError` specifically.

    Attributes:
        version_id: The :class:`PromptVersion` PK the caller
            tried to operate on.
        artifact_class: The row's class stamp (always
            ``'MEMORY'`` when this error fires — the error
            cannot raise on a CORE-class row).
        operation: The operation the caller attempted (e.g.
            ``'approve'``, ``'reject'``, ``'submit_for_approval'``,
            ``'atomic_transition'``).
    """

    def __init__(
        self,
        version_id: str,
        artifact_class: str,
        operation: str,
    ) -> None:
        self.version_id = version_id
        self.artifact_class = artifact_class
        self.operation = operation
        super().__init__(
            f"prompt_version {version_id} is artifact_class="
            f"{artifact_class!r}; operation {operation!r} is "
            f"not governed for MEMORY-class rows (User Directive #3, "
            f"2026-10-14: MEMORY rows are transition-free; born-effective; "
            f"selection (set_selection / clear_selection) is the only "
            f"mutator — use the create_memory_version creator instead)"
        )


class PromptClassMismatchError(ValueError):
    """Raised when the supplied ``artifact_class`` does not match the
    canonical class for the artifact string (defensive consistency
    check).

    Two paths exist for filling ``artifact_class``:

    1. Compute from artifact via :func:`class_for_artifact`
       (canonical — used by ``create_draft`` / ``create_and_submit``
       / ``create_memory_version``).
    2. Caller-supplied explicit class (rare; reserved for the
       "I know the artifact is MEMORY despite the default
       classification" case — the MEMORY-creator path uses this).

    When caller-supplied, the function refuses to stamp an
    inconsistent class (e.g. artifact=``'soul'`` with
    class=``'MEMORY'``). The error carries both values so
    test-failure output is self-explanatory.
    """

    def __init__(
        self,
        artifact: str,
        artifact_class: str,
        canonical_class: str,
    ) -> None:
        self.artifact = artifact
        self.artifact_class = artifact_class
        self.canonical_class = canonical_class
        super().__init__(
            f"artifact={artifact!r} canonical class is "
            f"{canonical_class!r}; caller supplied class="
            f"{artifact_class!r} (mismatch — refusing to stamp a "
            f"row under a class that contradicts the artifact's role; "
            f"User Directive #3, 2026-10-14)"
        )


class VersionNotEditableError(ValueError):
    """Raised when content-edit is attempted on a non-editable row.

    The editable-state rule (USER DIRECTIVE #2, 2026-10-14): only
    ``draft`` / ``pending_approval`` rows may have their content
    replaced in place. ``approved`` rows are the append-only
    immutable history; ``rejected`` is terminal (resubmit = new row);
    ``archived`` is superseded. Carries the row's current state so
    the HTTP layer can map to ``409 VERSION_IMMUTABLE`` with a
    targeted message without a second read.

    Inherits from ``ValueError`` (uniform error shape with
    :class:`InvalidTransitionError`).
    """

    def __init__(self, version_id: str, current_state: str) -> None:
        self.version_id = version_id
        self.current_state = current_state
        super().__init__(
            f"prompt_version {version_id} is not editable: state is "
            f"{current_state!r} (only draft/pending_approval rows may "
            f"be edited; approved history is append-only immutable, "
            f"rejected is terminal, archived is superseded)"
        )


class VersionNotDeletableError(ValueError):
    """Raised when hard-delete is attempted on a non-deletable row.

    Mirror of :class:`VersionNotEditableError` for the DELETE path
    (USER DIRECTIVE #2, 2026-10-14): only ``draft`` /
    ``pending_approval`` rows may be hard-deleted. Approved rows are
    append-only immutable history — hard-delete of approved history
    is a morning fork and is deliberately NOT built. Carries the
    row's current state for the ``409 VERSION_IMMUTABLE`` mapping.

    Inherits from ``ValueError`` (uniform error shape).
    """

    def __init__(self, version_id: str, current_state: str) -> None:
        self.version_id = version_id
        self.current_state = current_state
        super().__init__(
            f"prompt_version {version_id} is not deletable: state is "
            f"{current_state!r} (only draft/pending_approval rows may "
            f"be deleted; approved history is append-only immutable, "
            f"rejected is terminal, archived is superseded)"
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

    User Directive #3 (2026-10-14): each effective row carries the
    :attr:`artifact_class` dimension so the loader's
    ``db_versions_fingerprint`` can fold per-class — class-distinct
    identities matter for cache bust correctness (a MEMORY write
    busts ONLY the MEMORY slot; a CORE write busts ONLY the CORE
    slot). The single-SELECT fold preserves A8: one query covers
    BOTH classes for the (agent_id, version_tag) pair.

    Attributes:
        artifact: Artifact key (CORE: one of ``PROMPT_ARTIFACTS``;
            MEMORY: ``memory`` / ``user`` / ``growth`` /
            ``memories/<slug>``).
        artifact_class: Resolution class (``'CORE'`` / ``'MEMORY'``,
            from ``PROMPT_ARTIFACT_CLASSES``). Same value for every
            row carrying the same artifact string.
        selected_version_id: The currently selected row's PK
            (``is_selected=1`` for the key), or ``None``.
        selected_content: The selected row's body, or ``None``.
        latest_approved_version_id: The latest-approved row's PK
            (``MAX(version) WHERE state='approved'``), or ``None``
            (always ``None`` for MEMORY-class rows — see
            :meth:`PromptVersionRepository.get_effective_rows`).
        latest_approved_content: The latest-approved row's body,
            or ``None`` (MEMORY: ``None``; CORE precedence uses
            this when no selection is set).
    """

    artifact: str
    artifact_class: str = ARTIFACT_CLASS_CORE
    selected_version_id: Optional[str] = None
    selected_content: Optional[str] = None
    latest_approved_version_id: Optional[str] = None
    latest_approved_content: Optional[str] = None
    # User Directive #3 (2026-10-14): MEMORY-class latest-row
    # materialization. For CORE rows this is always ``None``
    # (latest-approved carries the resolution work). For MEMORY rows
    # this carries the latest row's version_id + content (any state
    # value — MEMORY rows are state-agnostic). The single-SELECT
    # LEFT JOIN in :meth:`get_effective_rows` materializes both
    # the CORE and MEMORY readings in one round-trip; class-folding
    # happens in the SQL.
    latest_version_id: Optional[str] = None
    latest_content: Optional[str] = None


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

    @staticmethod
    def _validate_artifact_class(artifact_class: str) -> None:
        """Validate ``artifact_class`` against the canonical vocabulary.

        User Directive #3 (2026-10-14) — single-source-of-truth
        validation: every stamp / filter / branch consults the
        same ``PROMPT_ARTIFACT_CLASSES`` set so a future class
        addition lands in one place.
        """
        if artifact_class not in PROMPT_ARTIFACT_CLASSES:
            raise ValueError(
                f"artifact_class must be one of "
                f"{sorted(PROMPT_ARTIFACT_CLASSES)}, got "
                f"{artifact_class!r}"
            )

    def _refuse_if_memory(
        self,
        version_id: str,
        operation: str,
    ) -> None:
        """Pre-flight probe: refuse the operation if the row is MEMORY-class.

        User Directive #3 (2026-10-14) — state-machine methods
        (``submit_for_approval`` / ``approve`` / ``reject`` /
        ``atomic_transition`` / ``update_version_content`` /
        ``delete_version``) govern CORE-class rows only. MEMORY-class
        rows are transition-free (born-effective; selection is the
        only mutator; creator = ``create_memory_version``). The probe
        runs as a single-row SELECT pre-flight so the typed error
        (:class:`MemoryClassError`) is raised BEFORE the UPDATE
        side-effects — saves a wasted CAS round-trip on the hot path.

        The ``LookupError`` for a missing row surfaces from the
        probe as-is — the repository's own ``LookupError`` semantics
        apply (the upsert caller is expected to surface this). CORE
        rows are NEVER refused here (the check is purely
        class-dimension).
        """
        with Session(self.engine) as session:
            row_class = session.exec(
                select(col(PromptVersion.artifact_class))
                .select_from(PromptVersion)
                .where(col(PromptVersion.version_id) == version_id)
            ).one_or_none()
        if row_class is None:
            # Row missing — let the repo's own LookupError
            # surface when the UPDATE matches zero rows. The probe
            # path is intentionally silent here so the typed error
            # shape stays uniform (state-machine lookups always
            # surface LookupError at the same site).
            return
        if row_class == ARTIFACT_CLASS_MEMORY:
            raise MemoryClassError(
                version_id=version_id,
                artifact_class=row_class,
                operation=operation,
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
        artifact_class: Optional[str] = None,
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
            artifact: Artifact key (one of ``PROMPT_ARTIFACTS`` +
                per-file ``memories/<slug>`` keys + ``memory`` /
                ``user`` / ``growth``).
            content: Override body (markdown).
            actor: Submitter identity (use ``actor_from_instance`` for
                in-process callers; ``actor_from_request`` for HTTP).
            state: Initial state — defaults to ``"draft"``;
                Phase B uses the same primitive to seed
                ``"pending_approval"`` rows if it chooses (the A1
                state machine allows the "submit for approval"
                shortcut as a single composite).
            artifact_class: User Directive #3 (2026-10-14) extension —
                when ``None`` (the default for backward-compatibility
                with Phase A callers), the class is computed from
                ``artifact`` via :func:`class_for_artifact`. When
                supplied, MUST match the canonical class for the
                artifact string (the function refuses a mismatch via
                :class:`PromptClassMismatchError`).

                Why this exists: the canonical class wins by default,
                but a test harness / migration tool may want to stamp
                a row under an explicit class for forensic purposes
                — the explicit-class path refuses to override the
                canonical mapping (an artifact=``'soul'`` row with
                class=``'MEMORY'`` is rejected at INSERT time).

        Returns:
            The newly inserted :class:`PromptVersion` row.

        Raises:
            ValueError: On bad ``state``, bad ``artifact_class``, an
                ``artifact_class`` mismatch (rejected via
                :class:`PromptClassMismatchError`), or after the retry
                budget is exhausted (caller surfaces the contention).
        """
        self._validate_state(state)
        version_tag = _normalize_version_tag(version_tag)
        canonical_class = class_for_artifact(artifact)
        if artifact_class is None:
            artifact_class = canonical_class
        else:
            self._validate_artifact_class(artifact_class)
            if artifact_class != canonical_class:
                raise PromptClassMismatchError(
                    artifact=artifact,
                    artifact_class=artifact_class,
                    canonical_class=canonical_class,
                )
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
                        artifact_class=artifact_class,
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
                            artifact_class=prompt_version.artifact_class,
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
                            "artifact_class": artifact_class,
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

    # 2b. create_initial_approved_v1 ----------------------------------------
    # User Directive #5A (Phase C, R-A1) — the custom-agent
    # creation path writes the v1 prompt_versions rows DIRECTLY
    # with state='approved' + is_selected=1 (no draft /
    # pending-approval / approval flow). The creating user IS the
    # human approver, stamped from the HTTP lane. This helper is
    # the R-A1 commit-everything-in-one-txn atom alongside
    # :meth:`insert_config` on the custom_agent_configs repo.

    def create_initial_approved_v1(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: ActorContext,
        *,
        artifact_class: Optional[str] = None,
        conn: Optional[Any] = None,
    ) -> PromptVersion:
        """Insert a v1 prompt-version row directly as ``approved`` (R-A1).

        User Directive #5A (2026-10-14, Phase C) — the
        custom-agent creation ruling. R-A1: "user-authored
        creation writes v1 CORE rows as APPROVED (the creating
        user IS the human approver; audit-stamped via the
        ``actor_from_request`` session lane). Badge does NOT
        move (v1 lands approved, not pending)."

        Bypasses the ``draft → pending_approval → approved``
        state machine (the only caller surface that uses this
        method is the custom-agent POST endpoint, which is a
        one-shot atomic transaction). The created row carries:

        * ``state = 'approved'``;
        * ``is_selected = 1`` (the only row for the key — it is
          both latest_approved AND selected, so the loader
          serves it via F6 immediately);
        * ``approver_subject = actor.subject`` (verified
          non-empty HTTP lane);
        * ``approver_auth_method = actor.auth_method`` (verified
          in 'session'/'bearer').

        Lifecycle rows:

        * One :class:`PromptVersion` row (the version itself);
        * One :class:`PromptAuditEvent` ``CREATED`` row
          (audit provenance per A3 + the D-IDENTITY-STAMPING
          ruling — submitter/approver identity carried on
          both columns);
        * One :class:`PromptAuditEvent` ``APPROVED`` row
          (the auto-approved event so the timeline reflects
          the R-A1 "v1 lands approved, not pending" rule).

        All three rows commit in ONE ``engine.begin()`` (or in
        the caller's transaction when ``conn=`` is provided).
        Caller-side integration with
        :meth:`CustomAgentConfigRepository.insert_config` uses
        the same ``conn`` argument so config + versions +
        audit all share one transaction — R-A1's atomicity
        invariant.

        Args:
            agent_id: Resolved agent id (MUST be a custom agent
                wire form when called from the custom-agent POST
                endpoint; the loader's R-A2 empty-state posture
                is gated by ``is_custom_agent=True`` upstream of
                this method — the repo itself does not enforce
                the prefix because it is also reachable from
                baseline-seeding callers that may use non-custom
                ids in test paths).
            version_tag: Version tag (``""`` for base; F9).
            artifact: Artifact key (one of PROMPT_ARTIFACTS +
                memory family + memories/<slug>).
            content: Override body (markdown).
            actor: Caller identity — MUST be an HTTP actor
                (``actor_from_request``); the method refuses
                ``internal`` per S16 anti-forgery.
            artifact_class: Optional explicit class
                (default: canonical via :func:`class_for_artifact`).
            conn: Optional ``Connection``. When supplied, the
                insert runs inside the caller's transaction
                (the router opens a wide ``engine.begin()`` and
                passes ``conn`` here AND to
                :meth:`CustomAgentConfigRepository.insert_config`).
                When ``None``, the method opens its own
                ``engine.begin()`` block.

        Returns:
            The freshly inserted :class:`PromptVersion` row.

        Raises:
            ValueError: On 'internal' actor, on missing
                ``actor.subject`` / ``actor.auth_method``, or on
                bad ``state`` (defensive — 'approved' is the
                only legal value here; ``draft`` /
                ``pending_approval`` etc. are REFUSED so the
                direct-from-R-A1 surface can never be misused
                to mint unreviewed rows in other states).
            PromptClassMismatchError: On a caller-supplied
                ``artifact_class`` that doesn't match the
                canonical class for ``artifact``.
            IntegrityError: After the retry budget is
                exhausted (concurrent writer on the same key).
        """
        # S16: refuse internal lane — only HTTP actors (session /
        # bearer) may satisfy the R-A1 "creating user IS the
        # human approver" rule.
        if actor.is_internal():
            raise ValueError(
                "create_initial_approved_v1() requires an HTTP "
                "actor (session/bearer); internal actors cannot "
                "satisfy the R-A1 'creating user IS the human "
                "approver' rule"
            )
        if not actor.subject:
            raise ValueError(
                "create_initial_approved_v1() requires a non-empty "
                "actor.subject (R-A1: the creating user IS the "
                "human approver; an empty subject refuses the "
                "stamp)"
            )
        if actor.auth_method not in {AUTH_METHOD_SESSION, AUTH_METHOD_BEARER}:
            raise ValueError(
                f"create_initial_approved_v1() requires "
                f"auth_method in session/bearer; got "
                f"{actor.auth_method!r}"
            )

        # The state column is hard-coded to 'approved' here; the
        # helper allows NO override — it is the R-A1 direct
        # approval surface, not a generic create-with-state path.
        state = "approved"
        self._validate_state(state)
        version_tag = _normalize_version_tag(version_tag)
        canonical_class = class_for_artifact(artifact)
        if artifact_class is None:
            artifact_class = canonical_class
        else:
            self._validate_artifact_class(artifact_class)
            if artifact_class != canonical_class:
                raise PromptClassMismatchError(
                    artifact=artifact,
                    artifact_class=artifact_class,
                    canonical_class=canonical_class,
                )
        content_hash = _sha256_hex(content)
        now = _now_iso()

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_INSERT_RETRIES):
            try:
                def _do_insert(c: Connection) -> PromptVersion:
                    self._acquire_key_lock(
                        c, agent_id, version_tag, artifact
                    )
                    row = c.execute(
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
                    pv = PromptVersion(
                        version_id=version_id,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version=next_version,
                        state=state,
                        content=content,
                        content_hash=content_hash,
                        is_selected=1,  # R-A1: v1 lands selected
                        submitter_instance_id=actor.instance_id,
                        submitter_agent_id=(
                            actor.agent_id if actor.is_internal() else None
                        ),
                        submitter_version_tag=(
                            _normalize_version_tag(actor.version_tag)
                            if actor.is_internal()
                            else ""
                        ),
                        # The R-A1 stamp: the creating user IS the
                        # human approver — stamps are filled from
                        # the actor directly, NOT via the
                        # ``draft → pending_approval → approved``
                        # path.
                        approver_subject=actor.subject,
                        approver_auth_method=actor.auth_method,
                        artifact_class=artifact_class,
                        created_at=now,
                        updated_at=now,
                    )
                    c.execute(
                        PromptVersion.__table__.insert().values(
                            version_id=pv.version_id,
                            agent_id=pv.agent_id,
                            version_tag=pv.version_tag,
                            artifact=pv.artifact,
                            version=pv.version,
                            state=pv.state,
                            content=pv.content,
                            content_hash=pv.content_hash,
                            is_selected=pv.is_selected,
                            submitter_instance_id=pv.submitter_instance_id,
                            submitter_agent_id=pv.submitter_agent_id,
                            submitter_version_tag=pv.submitter_version_tag,
                            approver_subject=pv.approver_subject,
                            approver_auth_method=pv.approver_auth_method,
                            artifact_class=pv.artifact_class,
                            created_at=pv.created_at,
                            updated_at=pv.updated_at,
                        )
                    )
                    # Two audit rows in ONE txn (A3): CREATED + the
                    # auto-APPROVED so the timeline reflects the
                    # R-A1 "v1 lands approved, not pending" rule.
                    self._append_audit_in_conn(
                        c,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="CREATED",
                        actor=actor,
                        payload={
                            "state": state,
                            "version": next_version,
                            "artifact_class": artifact_class,
                            "via": "create_initial_approved_v1",
                            "ruling": "R-A1",
                        },
                    )
                    self._append_audit_in_conn(
                        c,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="APPROVED",
                        actor=actor,
                        payload={
                            "from_state": "draft",
                            "to_state": "approved",
                            "version": next_version,
                            "via": "create_initial_approved_v1",
                            "ruling": "R-A1",
                            "auto_approved": True,
                        },
                    )
                    return pv

                if conn is not None:
                    pv_result = _do_insert(conn)
                else:
                    with self.engine.begin() as c:
                        pv_result = _do_insert(c)

                logger.info(
                    f"prompt_version.create_initial_approved_v1 ok: "
                    f"agent_id={agent_id} version_tag={version_tag!r} "
                    f"artifact={artifact} version={pv_result.version} "
                    f"version_id={pv_result.version_id} "
                    f"approver={actor.subject!r}/"
                    f"{actor.auth_method} attempt={attempt + 1}"
                )
                return pv_result
            except IntegrityError as e:
                last_error = e
                logger.warning(
                    f"prompt_version.create_initial_approved_v1 retry: "
                    f"agent_id={agent_id} artifact={artifact} "
                    f"attempt={attempt + 1}/{_MAX_INSERT_RETRIES} "
                    f"err={type(e).__name__}"
                )
                continue
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

        User Directive #3 (2026-10-14) — refuses to mutate a
        MEMORY-class row. The probe runs on the same read path the
        CAS uses to surface ``state != from_state`` errors, so the
        typed error rides the same single-statement UPDATE; the
        cost is one tiny single-row SELECT pre-flight.

        Order of guards:

        1. **Class probe first** — MEMORY rows reject with the
           typed :class:`MemoryClassError` BEFORE the
           transition-shape validation.
        2. Transition validation against
           :data:`VALID_TRANSITIONS`.
        3. CAS UPDATE — rowcount check.

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
            MemoryClassError: If the row's ``artifact_class`` is
                ``'MEMORY'`` (User Directive #3 binding block 4).
            InvalidTransitionError: If ``(from_state, to_state)``
                is not in :data:`VALID_TRANSITIONS`.
            LookupError: If no row matches ``(version_id, from_state)``
                (row missing OR state already moved by a concurrent
                transition).
        """
        # User Directive #3 — pre-flight probe: refuse if MEMORY.
        # This runs BEFORE the transition validation so a MEMORY
        # row + any transition pair yields the class-dimension
        # error (the more informative diagnostic for the
        # caller) rather than the ``InvalidTransitionError``
        # shape.
        self._refuse_if_memory(version_id, "atomic_transition")

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
        """Transition ``draft → pending_approval`` (A2).

        User Directive #3 (2026-10-14): refuses MEMORY-class
        rows — the typed ``MemoryClassError`` rides
        ``atomic_transition``'s pre-flight probe.
        """
        self._refuse_if_memory(version_id, "submit_for_approval")
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

        User Directive #3 (2026-10-14): also refuses if the row
        is MEMORY-class (carried by ``atomic_transition``'s
        pre-flight probe — the typed ``MemoryClassError`` rides
        one consistent error shape across approve / reject /
        submit / atomic-transition callers).
        """
        self._refuse_if_memory(version_id, "approve")
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

        User Directive #3 (2026-10-14): also refuses if the row
        is MEMORY-class (carried by ``atomic_transition``'s
        pre-flight probe — the typed ``MemoryClassError`` rides
        one consistent error shape across approve / reject /
        submit / atomic-transition callers).
        """
        self._refuse_if_memory(version_id, "reject")
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

        User Directive #3 (2026-10-14) — class-aware precedence:

        * **CORE** (the conservative-default set: soul / rule /
          workflow / tools_note / tools / skill):
          ``selected_version.content`` > ``latest_approved.content``
          > ``None`` (file fallback fires).
        * **MEMORY** (memory / user / growth / ``memories/<slug>``):
          ``selected_version.content`` > **LATEST row content**
          (any state value — MEMORY rows are born-effective; the
          state machine does not gate them) > ``None`` (template
          file fallback; empty post-sweep).

        Implementation note: this method reads
        :meth:`get_effective_rows` for the artifact and applies the
        class-aware precedence in Python (no second SELECT needed —
        A8's one-SELECT guarantee is preserved because the rows
        for ALL artifacts for the (agent_id, version_tag) pair are
        read in a single query, including the ``latest_content``
        for MEMORY rows).
        """
        rows = self.get_effective_rows(agent_id, version_tag)
        for row in rows:
            if row.artifact == artifact:
                if row.selected_content is not None:
                    return row.selected_content
                if row.artifact_class == ARTIFACT_CLASS_MEMORY:
                    # MEMORY: latest-row content (any state value)
                    # beats template-file fallback. The folder
                    # below carries ``memories_latest_content`` in
                    # the same single-SELECT — see get_effective_rows.
                    if row.latest_content is not None:
                        return row.latest_content
                    return None
                # CORE: standard F6 precedence.
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

        User Directive #3 (2026-10-14) — single-SELECT still holds
        under the class dimension: the query fans out across both
        classes in one round-trip. The single-SELECT fold works
        because:

        * CORE artifacts ride the existing ``approved`` LEFT JOIN
          (latest-approved row + selected row).
        * MEMORY artifacts ride an additional ``latest``
          LEFT JOIN — ``MAX(version) WHERE state IN (
          'effective', 'approved', 'pending_approval', 'draft',
          'rejected', 'archived')`` — i.e. ALL state values, since
          MEMORY rows are state-agnostic. The folder materializes
          the latest row's content into ``EffectiveRow.
          latest_content``; CORE rows leave this field ``None``
          (latest-approved covers it).

        All data comes from ONE SELECT — a CTE-driven LEFT JOIN
        that fetches the selected row per artifact, the
        latest-approved row per artifact (CORE only), and the
        latest row per artifact (MEMORY only) in a single query.
        Closes the fingerprint×content TOCTOU window (A8 / C2 —
        the loader cache fingerprint and the effective content are
        guaranteed to be from the same DB snapshot).
        """
        version_tag = _normalize_version_tag(version_tag)
        # Single SELECT: a UNION of selected + latest_approved +
        # latest (any-state) subqueries, joined back to artifact
        # name. SQLModel/SQLAlchemy composes this into ONE
        # database round-trip. We use a direct text() expression
        # for clarity — the alternative (Python aggregation over
        # per-artifact queries) would break the A8 single-SELECT
        # guarantee. The class is folded into per-artifact logic
        # (CORE uses approved-only; MEMORY uses any-state), so the
        # SAME query serves both — class-folding happens in the
        # LEFT JOIN predicates below.
        sql = text(
            """
            SELECT
              pv.artifact AS artifact,
              pv.artifact_class AS artifact_class,
              sel.version_id AS sel_version_id,
              sel.content AS sel_content,
              appr.version_id AS appr_version_id,
              appr.content AS appr_content,
              mem.version_id AS mem_version_id,
              mem.content AS mem_content
            FROM (
              SELECT DISTINCT artifact, artifact_class
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
              AND appr.artifact_class = 'CORE'
              AND appr.state = 'approved'
              AND appr.version = (
                SELECT MAX(appr2.version) FROM prompt_versions appr2
                WHERE appr2.agent_id = :agent_id
                  AND appr2.version_tag = :version_tag
                  AND appr2.artifact = pv.artifact
                  AND appr2.artifact_class = 'CORE'
                  AND appr2.state = 'approved'
              )
            LEFT JOIN prompt_versions mem
              ON mem.agent_id = :agent_id
              AND mem.version_tag = :version_tag
              AND mem.artifact = pv.artifact
              AND mem.artifact_class = 'MEMORY'
              AND mem.version = (
                SELECT MAX(mem2.version) FROM prompt_versions mem2
                WHERE mem2.agent_id = :agent_id
                  AND mem2.version_tag = :version_tag
                  AND mem2.artifact = pv.artifact
                  AND mem2.artifact_class = 'MEMORY'
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
            artifact_class = r[1] or ARTIFACT_CLASS_CORE
            sel_id = r[2]
            sel_content = r[3]
            appr_id = r[4]
            appr_content = r[5]
            mem_id = r[6]
            mem_content = r[7]
            out.append(
                EffectiveRow(
                    artifact=artifact,
                    artifact_class=artifact_class,
                    selected_version_id=sel_id,
                    selected_content=sel_content,
                    latest_approved_version_id=appr_id,
                    latest_approved_content=appr_content,
                    # For MEMORY rows this carries the latest row's
                    # content; for CORE rows it stays ``None`` (the
                    # loader resolves CORE via ``latest_approved``
                    # instead, per F6).
                    latest_version_id=mem_id,
                    latest_content=mem_content,
                )
            )
        return out

    # 12a. create_memory_version (User Directive #3) -----------------------------

    def create_memory_version(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: ActorContext,
        *,
        artifact_class: str = ARTIFACT_CLASS_MEMORY,
    ) -> PromptVersion:
        """Create a MEMORY-class row directly, bypassing the state machine.

        User Directive #3, binding blocks 1 + 2 — MEMORY rows are
        TRANSITION-FREE and born-effective: this method persists a
        row in ``state='approved'`` (the stable value MEMORY rows
        land at; the audit row records the ``CREATED`` event with
        ``artifact_class='MEMORY'`` stamped on the payload). The
        row's ``is_selected=0`` by default; the selection is opt-in
        via :meth:`set_selection` (the per-key one-pointer holds
        the same F5 invariant for both classes).

        Why a separate creator vs :meth:`create_draft`:

        * :meth:`create_draft` always stamps class via the canonical
          mapping (a ``memory`` artifact stamp = ``'MEMORY'``
          automatically), but it relies on the A1 state machine and
          lands the row in ``state='draft'`` then requires
          :meth:`submit_for_approval`. MEMORY rows don't need that
          path — they're effective immediately.
        * This creator lands the row in ``state='approved'``
          directly (the stable non-transitional value) and stamps
          the audit row with ``CREATED`` + ``artifact_class``. The
          row appears in the loader resolution chain the same
          instant it commits.

        Resolution precedence for MEMORY rows (loader hook, Ud#3
        binding block 3): ``selected_version.content`` > LATEST row
        > template-file fallback (empty post-sweep; proven
        boot-tolerant per decisions.md §7).

        Args:
            agent_id: Resolved agent id.
            version_tag: Version tag (``""`` for base; F9).
            artifact: The artifact key — must classify as MEMORY
                per :func:`class_for_artifact` (otherwise the
                function refuses via
                :class:`PromptClassMismatchError`). The ``memory``
                / ``user`` / ``growth`` triple is the canonical set;
                ``memories/<slug>`` is supported per-file.
            content: Override body.
            actor: Submitter identity (the only mutator for the
                MEMORY path is a creator — no state-machine
                transitions apply).
            artifact_class: Mostly internal; default 'MEMORY'.
                Override is allowed for the artifact-class
                consistency check (caller MUST supply a class that
                matches the canonical class for the artifact).

        Returns:
            The newly inserted :class:`PromptVersion` row in
            ``state='approved'``.

        Raises:
            ValueError: On bad ``artifact_class`` or
                ``artifact_class`` mismatch.
            IntegrityError: After exhausting the INSERT-retry
                budget (per the A7/C5/G-4e fence — same
                :data:`_MAX_INSERT_RETRIES` as :meth:`create_draft`).
        """
        # Resolve the canonical class for the artifact; refuse a
        # class-supplied mismatch (the same path create_draft
        # enforces — see ``_validate_artifact_class`` /
        # ``PromptClassMismatchError``).
        canonical_class = class_for_artifact(artifact)
        if artifact_class is None:
            artifact_class = canonical_class
        else:
            self._validate_artifact_class(artifact_class)
            if artifact_class != canonical_class:
                raise PromptClassMismatchError(
                    artifact=artifact,
                    artifact_class=artifact_class,
                    canonical_class=canonical_class,
                )
        if artifact_class != ARTIFACT_CLASS_MEMORY:
            # MEMORY creator is ONLY for MEMORY-class artifacts.
            # CORE artifacts must use ``create_draft`` (state
            # machine) or ``create_and_submit`` (A5 composite).
            raise ValueError(
                f"create_memory_version() refuses artifact_class="
                f"{artifact_class!r}: this creator is the MEMORY-class "
                f"path only; use create_draft() for class='CORE' "
                f"artifacts (User Directive #3, 2026-10-14)"
            )
        version_tag = _normalize_version_tag(version_tag)
        content_hash = _sha256_hex(content)
        now = _now_iso()

        # MEMORY rows are born-effective (state='approved' is the
        # stable non-transitional value the loader resolves against
        # — the state column on a MEMORY row is just a "this row is
        # landed / recallable by selection" indicator, NOT a state
        # machine state. The single-row update path is sufficient
        # for the creator.
        MEMORY_EFFECTIVE_STATE = "approved"

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_INSERT_RETRIES):
            try:
                with self.engine.begin() as conn:
                    # Per-key serialization first (R3) — the
                    # MAX(version) + INSERT below must not overlap
                    # a concurrent MEMORY writer on the same key.
                    self._acquire_key_lock(
                        conn, agent_id, version_tag, artifact,
                    )
                    # Recompute max(version) — UNIQUE constraint is
                    # the final fence; retry absorbs the race.
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
                        state=MEMORY_EFFECTIVE_STATE,
                        content=content,
                        content_hash=content_hash,
                        is_selected=0,
                        submitter_instance_id=actor.instance_id,
                        submitter_agent_id=actor.agent_id if actor.is_internal() else None,
                        submitter_version_tag=_normalize_version_tag(actor.version_tag) if actor.is_internal() else "",
                        approver_subject=None,
                        approver_auth_method=None,
                        artifact_class=ARTIFACT_CLASS_MEMORY,
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
                            artifact_class=prompt_version.artifact_class,
                            created_at=prompt_version.created_at,
                            updated_at=prompt_version.updated_at,
                        )
                    )
                    # Audit row stamped INSIDE the same txn (A3).
                    # The event_type stays 'CREATED' (the only
                    # transition a MEMORY row participates in is
                    # the initial persist); the payload carries
                    # the class stamp and a memo that the row is
                    # born-effective.
                    self._append_audit_in_conn(
                        conn,
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        version_id=version_id,
                        event_type="CREATED",
                        actor=actor,
                        payload={
                            "state": MEMORY_EFFECTIVE_STATE,
                            "version": next_version,
                            "artifact_class": ARTIFACT_CLASS_MEMORY,
                            "via": "create_memory_version",
                        },
                    )

                logger.info(
                    f"prompt_version.create_memory_version ok: "
                    f"agent_id={agent_id} version_tag={version_tag!r} "
                    f"artifact={artifact} version={next_version} "
                    f"version_id={version_id} attempt={attempt + 1}"
                )
                return prompt_version
            except IntegrityError as e:
                last_error = e
                logger.warning(
                    f"prompt_version.create_memory_version retry: "
                    f"agent_id={agent_id} artifact={artifact} "
                    f"attempt={attempt + 1}/{_MAX_INSERT_RETRIES} "
                    f"err={type(e).__name__}"
                )
                continue
        assert last_error is not None
        raise last_error

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
    # USER DIRECTIVE #2 (2026-10-14, frontend specs) — content edit /
    # delete / pending-count. The CRUD + badge API deltas the mandated
    # Persona UI consumes.
    #
    # Leader ruling (binding composition with frozen A1): EDIT and
    # DELETE apply to ``draft`` / ``pending_approval`` rows ONLY
    # (:data:`_EDITABLE_STATES`). Approved rows stay append-only
    # immutable history; rejected is terminal (resubmit = new row);
    # pending-supersession (create_and_submit archive) still governs.
    # Hard-delete of approved history is a MORNING FORK — deliberately
    # NOT built here.
    #
    # Serialization posture (mirrors ``atomic_transition``, the
    # documented no-advisory-lock case): these are CAS-guarded
    # single-statement mutations — the
    # ``… WHERE state IN (draft, pending_approval)`` rowcount check IS
    # the serialization. A concurrent approve/reject/archive between
    # the read and the write flips the row out of the editable set and
    # the CAS matches zero rows → the typed not-editable /
    # not-deletable error carries the row's CURRENT state. No
    # per-key advisory lock is taken (the lock's documented callers
    # are the key-level read-modify-write writers; these methods
    # target one row by PK).
    # ─────────────────────────────────────────────────────────────────

    def update_version_content(
        self,
        version_id: str,
        content: str,
        actor: ActorContext,
    ) -> PromptVersion:
        """Replace the content of a ``draft`` / ``pending_approval`` row
        in place (USER DIRECTIVE #2, deliverable 1).

        Editable-state rule: only draft/pending rows may be edited.
        Any other state raises :class:`VersionNotEditableError`
        carrying the row's current state (approved = append-only
        immutable history; rejected = terminal; archived =
        superseded). The recompute of ``content_hash`` and the
        ``UPDATED`` audit row commit INSIDE the same
        ``engine.begin()`` as the content UPDATE (A3) — the audit
        payload carries the prior + new content_hash and both byte
        sizes.

        User Directive #3 (2026-10-14): also refuses MEMORY-class
        rows via the pre-flight probe — the typed
        ``MemoryClassError`` rides a consistent error shape across
        the state-machine methods. MEMORY edits go through
        ``create_memory_version`` instead (each revision = its own
        row; selection = per-file pin; the row is born-effective).

        Returns the updated row snapshot (rebuilt in-memory from the
        pre-update read — same rationale as ``create_draft``'s
        return: no cross-connection Session.get under contention).

        Raises:
            MemoryClassError: If the row is MEMORY-class (use
                ``create_memory_version`` instead).
            LookupError: If no row carries ``version_id``.
            VersionNotEditableError: If the row's state is outside
                ``draft`` / ``pending_approval`` (or raced out of the
                editable set between the read and the CAS UPDATE —
                the error carries the current state either way).

        Forensic note: under two concurrent edits of the SAME row,
        both CAS UPDATEs succeed (state is unchanged by edits) and
        each stamps its own UPDATED row; the second committer's
        ``prior_content_hash`` is read at its own transaction start,
        so an intermediate edit that committed in between may be
        skipped in the prior-hash provenance. Content + new hash are
        always exact; only the prior-side provenance can skip an
        intermediate under that race. Single-editor UI flows (the
        Persona UI) never hit this.
        """
        # User Directive #3 (2026-10-14) — MEMORY pre-flight probe.
        self._refuse_if_memory(version_id, "update_version_content")
        new_hash = _sha256_hex(content)
        now = _now_iso()
        with self.engine.begin() as conn:
            prior = self._read_full_row_in_conn(conn, version_id)
            if prior is None:
                raise LookupError(f"prompt_version {version_id} not found")
            prior_hash = prior[5]
            prior_content: str = prior[6]

            upd = (
                PromptVersion.__table__.update()
                .where(
                    col(PromptVersion.version_id) == version_id,
                    col(PromptVersion.state).in_(sorted(_EDITABLE_STATES)),
                )
                .values(content=content, content_hash=new_hash, updated_at=now)
            )
            result = conn.execute(upd)
            if result.rowcount == 0:
                # The row exists (read above) but left the editable
                # set — carry the CURRENT state (the read-to-write
                # race case resolves here too).
                cur = conn.execute(
                    select(col(PromptVersion.state))
                    .select_from(PromptVersion)
                    .where(col(PromptVersion.version_id) == version_id)
                ).one_or_none()
                raise VersionNotEditableError(
                    version_id, cur[0] if cur is not None else prior[4]
                )

            # Audit row stamped INSIDE the same txn (A3).
            self._append_audit_in_conn(
                conn,
                agent_id=prior[0],
                version_tag=prior[1],
                artifact=prior[2],
                version_id=version_id,
                event_type="UPDATED",
                actor=actor,
                payload={
                    "prior_content_hash": prior_hash,
                    "new_content_hash": new_hash,
                    "prior_content_bytes": len(prior_content.encode("utf-8")),
                    "new_content_bytes": len(content.encode("utf-8")),
                    "state": prior[4],
                    "version": prior[3],
                },
            )

        logger.info(
            f"prompt_version.update_version_content ok: "
            f"version_id={version_id} agent_id={prior[0]} "
            f"version_tag={prior[1]!r} artifact={prior[2]} "
            f"state={prior[4]} prior_bytes="
            f"{len(prior_content.encode('utf-8'))} "
            f"new_bytes={len(content.encode('utf-8'))}"
        )
        return self._row_snapshot(
            prior, version_id, content=content, content_hash=new_hash, updated_at=now
        )

    def delete_version(
        self,
        version_id: str,
        actor: ActorContext,
    ) -> PromptVersion:
        """Hard-delete a ``draft`` / ``pending_approval`` row (USER
        DIRECTIVE #2, deliverable 1).

        Editable-state rule mirrors :meth:`update_version_content`:
        only draft/pending rows may be deleted; any other state
        raises :class:`VersionNotDeletableError` carrying the row's
        current state. Hard-delete of APPROVED history is a morning
        fork and is deliberately NOT implemented.

        The ``DELETED`` audit row commits INSIDE the same
        ``engine.begin()`` as the row DELETE (A3) and carries the
        full identity + ``content_hash`` + ``content_bytes`` in its
        payload — the forensics survive the deleted row (the audit
        row's own identity columns also carry agent_id / version_tag
        / artifact / version_id per the drift_events template).

        Returns the deleted row's snapshot (post-delete forensic
        echo for the HTTP response body).

        Raises:
            LookupError: If no row carries ``version_id``.
            VersionNotDeletableError: If the row's state is outside
                ``draft`` / ``pending_approval`` (or raced out of the
                editable set between the read and the CAS DELETE).
        """
        # User Directive #3 (2026-10-14) — MEMORY pre-flight probe.
        # MEMORY rows hard-delete via a separate decision (TBD
        # morning review); for now, refuse to delete them through
        # the CRUD endpoint — the creator path (create_memory_version)
        # is the forward path.
        self._refuse_if_memory(version_id, "delete_version")
        now = _now_iso()
        with self.engine.begin() as conn:
            prior = self._read_full_row_in_conn(conn, version_id)
            if prior is None:
                raise LookupError(f"prompt_version {version_id} not found")

            result = conn.execute(
                PromptVersion.__table__.delete()
                .where(
                    col(PromptVersion.version_id) == version_id,
                    col(PromptVersion.state).in_(sorted(_EDITABLE_STATES)),
                )
            )
            if result.rowcount == 0:
                cur = conn.execute(
                    select(col(PromptVersion.state))
                    .select_from(PromptVersion)
                    .where(col(PromptVersion.version_id) == version_id)
                ).one_or_none()
                raise VersionNotDeletableError(
                    version_id, cur[0] if cur is not None else prior[4]
                )

            # Audit row stamped INSIDE the same txn (A3). Identity +
            # content forensics ride the payload so the trail survives
            # the deleted row.
            self._append_audit_in_conn(
                conn,
                agent_id=prior[0],
                version_tag=prior[1],
                artifact=prior[2],
                version_id=version_id,
                event_type="DELETED",
                actor=actor,
                payload={
                    "agent_id": prior[0],
                    "version_tag": prior[1],
                    "artifact": prior[2],
                    "version": prior[3],
                    "state": prior[4],
                    "content_hash": prior[5],
                    "content_bytes": len(prior[6].encode("utf-8")),
                },
            )

        logger.info(
            f"prompt_version.delete_version ok: "
            f"version_id={version_id} agent_id={prior[0]} "
            f"version_tag={prior[1]!r} artifact={prior[2]} "
            f"state={prior[4]}"
        )
        return self._row_snapshot(
            prior, version_id, content=prior[6], content_hash=prior[5],
            updated_at=now,
        )

    def count_pending(self) -> int:
        """Count rows in state ``pending_approval`` (USER DIRECTIVE #2,
        deliverable 1 — the Persona UI badge feed).

        User Directive #3 (2026-10-14) — CORE-only: MEMORY-class rows
        have no pending state (they are born-effective at creation);
        the badge feed answers "how many rows await approval" and
        only CORE artifacts ride the human-approval state machine.
        The class filter is a cheap index-eligible predicate on
        ``state == 'pending_approval' AND artifact_class = 'CORE'``
        — the existing ``idx_prompt_versions_state_updated`` index
        is state-leading; with a low row count this is a single
        sequential scan; for production scale, a future
        ``(state, artifact_class)`` covering index is a 1-line
        follow-up (out of scope here).

        Cheap single-scalar SELECT; drains as approve/reject land.

        The count is deliberately a PLAIN count — the A8 effective
        fingerprint does not apply (a badge answers "how many rows
        await approval", not "what is in effect").
        """
        with Session(self.engine) as session:
            stmt = (
                select(func.count(col(PromptVersion.version_id)))
                .where(
                    col(PromptVersion.state) == "pending_approval",
                    col(PromptVersion.artifact_class) == ARTIFACT_CLASS_CORE,
                )
            )
            return int(session.exec(stmt).one() or 0)

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

    # USER DIRECTIVE #2 helpers — full-row read + snapshot rebuild for
    # ``update_version_content`` / ``delete_version``.

    _FULL_ROW_COLUMNS = (
        col(PromptVersion.agent_id),
        col(PromptVersion.version_tag),
        col(PromptVersion.artifact),
        col(PromptVersion.version),
        col(PromptVersion.state),
        col(PromptVersion.content_hash),
        col(PromptVersion.content),
        col(PromptVersion.is_selected),
        col(PromptVersion.submitter_instance_id),
        col(PromptVersion.submitter_agent_id),
        col(PromptVersion.submitter_version_tag),
        col(PromptVersion.approver_subject),
        col(PromptVersion.approver_auth_method),
        col(PromptVersion.created_at),
    )

    def _read_full_row_in_conn(self, conn: Any, version_id: str) -> Optional[tuple]:
        """Read one row's full column tuple inside the caller's txn.

        Returns ``None`` when the row does not exist. Used by
        :meth:`update_version_content` / :meth:`delete_version` so the
        audit payload + the returned snapshot are built from a single
        in-transaction read (no cross-connection round-trip).
        """
        return conn.execute(
            select(*self._FULL_ROW_COLUMNS)
            .select_from(PromptVersion)
            .where(col(PromptVersion.version_id) == version_id)
        ).one_or_none()

    @staticmethod
    def _row_snapshot(
        row: tuple,
        version_id: str,
        *,
        content: str,
        content_hash: str,
        updated_at: str,
    ) -> PromptVersion:
        """Rebuild a :class:`PromptVersion` from a full-row tuple.

        ``content`` / ``content_hash`` / ``updated_at`` are passed
        explicitly because they are the fields a mutation changes;
        everything else comes from the pre-mutation read.
        """
        (
            agent_id, version_tag, artifact, version, state, _hash,
            _content, is_selected, sub_inst, sub_agent, sub_tag,
            appr_subj, appr_meth, created_at,
        ) = row
        return PromptVersion(
            version_id=version_id,
            agent_id=agent_id,
            version_tag=version_tag,
            artifact=artifact,
            version=version,
            state=state,
            content=content,
            content_hash=content_hash,
            is_selected=is_selected,
            submitter_instance_id=sub_inst,
            submitter_agent_id=sub_agent,
            submitter_version_tag=sub_tag,
            approver_subject=appr_subj,
            approver_auth_method=appr_meth,
            created_at=created_at,
            updated_at=updated_at,
        )


# ============================================================
# User Directive #5B (2026-10-14) — Template-baseline repository
# (R-B1/B2/B3 enforcement; record / detect / reset semantics).
# ============================================================


class TemplateDriftResult(NamedTuple):
    """The outcome of a single template-drift detection pass per artifact.

    User Directive #5B — result type for the R-B2 auto-reset path.
    Distinct result shapes for "no drift" (a stable baseline that
    matched the shipped file), "drift detected" (a hash mismatch
    needing R-B2 auto-reset + baseline re-point), and "skipped"
    (a MEMORY-class artifact or a custom-agent subtree, both of
    which carry no baseline — the auto-reset path is CORE-only).

    Fields:
        agent_id: Agent identity.
        version_tag: Resolved tag (``''`` for base).
        artifact: Artifact key.
        drift_detected: True iff the live file's sha256 differs from
            the recorded baseline's template_sha256. False if
            ``drift_skipped`` is True (the per-artifact skip case).
        old_sha256: The baseline's recorded hash (when
            ``drift_detected=True``; ``None`` when skipped).
        new_sha256: The live file's hash (when ``drift_detected=True``;
            ``None`` when skipped).
        drift_skipped: True iff the artifact was skipped (MEMORY-class
            or no baseline to compare against).
        skip_reason: Stable token describing WHY the artifact was
            skipped (``'memory_class'`` / ``'no_baseline_recorded'`` /
            ``'custom_agent'`` / ``'no_file_to_hash'``).
        selection_preserved: True iff an explicit
            ``is_selected=1`` row was already pinned for the key
            BEFORE the auto-reset; the user's intent wins per R-B3.
    """

    agent_id: str
    version_tag: str
    artifact: str
    drift_detected: bool
    old_sha256: Optional[str]
    new_sha256: Optional[str]
    drift_skipped: bool
    skip_reason: Optional[str]
    selection_preserved: bool


class BaselineRepository:
    """Sync repository for :class:`PromptTemplateBaseline` + the
    R-B2 auto-reset audit hooks.

    User Directive #5B (2026-10-14) — the core methods
    ``record_initial_baselines`` and ``detect_template_drift`` plus
    the audit-event stamping helpers ``stamp_template_reset_audit``
    (called when an auto-reset lands; the row carries the
    ``TEMPLATE_RESET`` event). The repository owns the per-baseline
    UPSERT idiom (SQLite ``INSERT OR REPLACE``; PG ``INSERT …
    ON CONFLICT``) because the schema declares a single row per
    ``(agent_id, version_tag, artifact)`` key (UNIQUE constraint,
    per :data:`UNIQUE(agent_id, version_tag, artifact)`).

    Constructor takes ONLY the shared engine. Reads use a short
    Session; writes wrap the per-key UPSERT in
    ``engine.begin()``. The audit row stamps live in the SAME
    ``engine.begin()`` block as the auto-reset's DB state change
    (the audit-event creation always pairs with the reset
    transition — the event_type maps 1:1 to the resolution chain).

    Auto-reset semantics — R-B1 / R-B2 / R-B3:

    * **R-B1** — the baseline service seeds baselines from the
      currently-shipped file hashes. The initial-record path is
      idempotent (re-running on unchanged files = no events).
    * **R-B2** — CORE-only auto-reset: when a shipped file's
      sha256 changes from its recorded baseline, the
      resolution path resets. EXPLICIT selections SURVIVE
      per R-B3. Approved history rows REMAIN (the audit trail
      survives the reset — they're append-only immutable per
      phase-contract.md A1).
    * **R-B3** — user intent wins: an EXPLICIT
      ``is_selected=1`` row for the key SURVIVES the auto-reset.
      Only the un-selected default path (``latest_approved → file``)
      resets to the new file base.

    MEMORY-class artifacts (``memory`` / ``user`` / ``growth`` /
    ``memories/<slug>``) are NEVER recorded in the baselines
    table — the auto-reset is CORE-only per the directive. The
    baseline record path rejects MEMORY-class artifact strings
    with :class:`ValueError` (``MEMORY-class artifacts do not
    participate in the baseline table``).

    Custom-agent ground (R-A2 resolution-side hook — the
    lifecycle coder builds the full A2/R-A2 ruling; this
    module threads the ``is_custom_agent`` flag through
    ``detect_template_drift`` so a custom agent never
    participates in the auto-reset protocol — custom agents
    have no template fallback, so resetting the resolution
    chain has no effect on rendering; the artifact IS the
    DB content).
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ─────────────────────────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_artifact_class_core_only(artifact: str) -> None:
        """Refuse artifact strings that classify as MEMORY.

        The baselines table records CORE artifacts only (R-B2);
        MEMORY-class artifacts are a transition-free carve-out
        that owns the resolution (no template-fallback
        re-pointing applies).
        """
        cls = class_for_artifact(artifact)
        if cls != ARTIFACT_CLASS_CORE:
            raise ValueError(
                f"baseline path refuses artifact={artifact!r} "
                f"(class={cls!r}); only CORE-class artifacts "
                f"participate in the R-B2 auto-reset (User Directive "
                f"#5B, 2026-10-14). MEMORY-class artifacts own the "
                f"resolution directly; no template fallback applies."
            )

    @staticmethod
    def _normalize_template_sha256(value: str) -> str:
        """Lowercase the hash + verify the 64-hex shape.

        Cheap shape check; a non-hex value raises :class:`ValueError`
        so callers fail fast on garbage input rather than writing
        a malformed row.
        """
        normalized = value.lower()
        if len(normalized) != 64:
            raise ValueError(
                f"template_sha256 must be 64-hex chars (got len={len(normalized)})"
            )
        try:
            int(normalized, 16)
        except ValueError as exc:
            raise ValueError(
                f"template_sha256 must be a hex string: {exc}"
            ) from exc
        return normalized

    # ─────────────────────────────────────────────────────────────────
    # Reads
    # ─────────────────────────────────────────────────────────────────

    def get_baseline(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[PromptTemplateBaseline]:
        """Fetch the single baseline for the key, or ``None``.

        Returns ``None`` when no baseline has been recorded yet
        (the lazy-seed flow at first ``detect_template_drift``
        run fires :meth:`record_initial_baselines` first if this
        returns ``None``).
        """
        version_tag = _normalize_version_tag(version_tag)
        with Session(self.engine) as session:
            return session.exec(
                select(PromptTemplateBaseline)
                .where(
                    col(PromptTemplateBaseline.agent_id) == agent_id,
                    col(PromptTemplateBaseline.version_tag) == version_tag,
                    col(PromptTemplateBaseline.artifact) == artifact,
                )
            ).one_or_none()

    def list_baselines_for_agent(
        self,
        agent_id: str,
        version_tag: str,
    ) -> list[PromptTemplateBaseline]:
        """Fetch every baseline for the ``(agent_id, version_tag)`` pair.

        Sort order: by artifact name (ascending). Used by the
        ``detect_template_drift`` walk.
        """
        version_tag = _normalize_version_tag(version_tag)
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(PromptTemplateBaseline)
                    .where(
                        col(PromptTemplateBaseline.agent_id) == agent_id,
                        col(PromptTemplateBaseline.version_tag) == version_tag,
                    )
                    .order_by(col(PromptTemplateBaseline.artifact).asc())
                )
            )

    # ─────────────────────────────────────────────────────────────────
    # Writes — R-B1 initial seeding
    # ─────────────────────────────────────────────────────────────────

    def record_initial_baselines(
        self,
        agent_id: str,
        version_tag: str,
        agents_root: Any,
        *,
        actor: ActorContext,
        artifact_filter: Optional[frozenset[str]] = None,
        is_custom_agent: bool = False,
    ) -> list[PromptTemplateBaseline]:
        """Seed baselines for a predefined agent (R-B1).

        Walks ``agents_root/<agent>/<artifact>.md`` files and
        inserts a baseline row for every CORE-class artifact whose
        versioned set is present. Idempotent — the row's
        ``template_sha256`` is the LIVE file's current hash, so
        re-running on unchanged files produces identical rows
        (the UPSERT swallows the no-op).

        Custom agents (``is_custom_agent=True``) return ``[]``
        with NO rows inserted — the A2/R-A2 ruling says a custom
        agent has no template fallback, so the baseline table
        carries no rows for it (the lifecycle coder owns the
        custom-agent identity; this module extends the hook).

        MEMORY-class artifacts (``memory`` / ``user`` / ``growth``
        / ``memories/<slug>``) are NEVER recorded — the
        ``_validate_artifact_class_core_only`` path rejects them
        explicitly. ``memories/`` files are walked as their
        per-file keys but only the metadata-side check applies
        (MEMORY per-file baselines are out of scope per R-B2;
        a future morning fork could extend the table to track
        per-file MEMORY baselines if drift detection ever needs
        it).

        Args:
            agent_id: Resolved agent identity (``developer`` not
                ``developer[v2]`` per F9).
            version_tag: Version tag (``""`` for base; F9).
            agents_root: Path to the ``agents`` root directory.
                Carried as a positional argument so test code can
                pass ``tmp_path/agents`` without touching the
                canonical ``agents/`` tree.
            actor: Audit actor (the boot actor for the lifecycle;
                carries ``auth_method='internal'``).
            artifact_filter: Optional explicit set of CORE artifact
                strings to seed; default = :data:`PROMPT_ARTIFACTS`
                (= the conservative-default CORE set per F2).
            is_custom_agent: When True, return ``[]`` without
                touching the DB (the A2/R-A2 resolution-side hook).

        Returns:
            The freshly-recorded :class:`PromptTemplateBaseline`
            rows (the loaded-after-insert snapshots, in artifact-
            ascending order).
        """
        from pathlib import Path  # local import — module path

        if is_custom_agent:
            # R-A2 — custom agents have no template fallback; the
            # baseline table carries no rows for them. Returning
            # ``[]`` keeps the boot-side walk zero-touch for the
            # custom-agent subtree (the lifecycle coder owns the
            # full A2/R-A2 ruling).
            return []
        version_tag = _normalize_version_tag(version_tag)
        artifacts = artifact_filter or frozenset(PROMPT_ARTIFACTS)
        # Snapshot Path resolution: the agents_root must be a
        # Path-like. Type-annotated as Any in the signature to
        # keep the path import local.
        agents_root_path: Path = Path(agents_root)
        agent_dir = agents_root_path / agent_id

        out: list[PromptTemplateBaseline] = []
        for artifact in sorted(artifacts):
            self._validate_artifact_class_core_only(artifact)
            # Phase A T13/S10 file-content convention: artifact
            # names ship without ``.md`` (e.g. ``soul``) and the
            # file path is ``<artifact>.md`` under
            # ``<agents_root>/<agent_id>/``. The ``tools`` /
            # ``tools_note`` pair rides the tools_note.md primary
            # path; tools.md is the legacy fallback (read by the
            # loader) but only ONE row per artifact — the
            # :data:`PROMPT_ARTIFACTS` set names the canonical
            # artifact keys (``soul`` / ``rule`` / ``workflow`` /
            # ``tools_note`` / ``tools`` / ``skill``).
            artifact_filename = (
                _artifact_to_filename(artifact)
            )
            artifact_path = agent_dir / artifact_filename
            if not artifact_path.exists():
                # Custom agent absence: skip silently. The
                # baseline service's lazy-seed flow at
                # ``detect_template_drift`` does NOT error on
                # missing artifacts — Phase A T9 S10 has the
                # Versioned_PROMPT_ARTIFACTS as the canonical set,
                # but a particular agent may lack one (e.g. no
                # ``rule.md``).
                continue
            content = artifact_path.read_text(encoding="utf-8")
            sha = self._sha256_hex_str(content)
            recorded = self._upsert_baseline(
                agent_id=agent_id,
                version_tag=version_tag,
                artifact=artifact,
                template_sha256=sha,
            )
            out.append(recorded)
        return out

    @staticmethod
    def _sha256_hex_str(text: str) -> str:
        """Lowercase hex sha256 of ``text`` (utf-8) — same shape as
        ``_sha256_hex`` above but local to this module so the
        baseline service can be tested in isolation.
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _upsert_baseline(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        template_sha256: str,
    ) -> PromptTemplateBaseline:
        """Insert-or-replace the baseline row in one ``engine.begin()``.

        Dialect-gated:

        * SQLite — ``INSERT OR REPLACE`` (the runner-managed
          SQLite driver — see ``daemon/migrations/runner.py``).
        * PostgreSQL — ``INSERT … ON CONFLICT (agent_id,
          version_tag, artifact) DO UPDATE SET
          template_sha256=…, recorded_at=…``.

        The dialect detection keeps the surrounding code identical
        to the A1 row-insert pattern in
        :meth:`PromptVersionRepository.create_draft`. Both
        drivers commit inside ``engine.begin()`` so the row
        commits atomically with its surrounding audit event
        (the audit-event stamp helper below rides the same
        ``engine.begin()`` block when called from
        ``record_initial_baselines``).
        """
        version_tag = _normalize_version_tag(version_tag)
        template_sha256 = self._normalize_template_sha256(template_sha256)
        # Belt-and-braces: refuse MEMORY-class artifacts here too.
        # record_initial_baselines validates up-front (a clean
        # gate for callers); detect_template_drift's lazy-seed
        # path bypasses that gate (a defense-in-depth layer).
        self._validate_artifact_class_core_only(artifact)
        baseline_id = str(uuid.uuid4())
        now = _now_iso()
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "INSERT INTO prompt_template_baselines ("
                        "baseline_id, agent_id, version_tag, artifact, "
                        "template_sha256, recorded_at"
                        ") VALUES ("
                        ":baseline_id, :agent_id, :version_tag, "
                        ":artifact, :template_sha256, :recorded_at"
                        ") ON CONFLICT (agent_id, version_tag, artifact) "
                        "DO UPDATE SET "
                        "template_sha256 = EXCLUDED.template_sha256, "
                        "recorded_at = EXCLUDED.recorded_at"
                    ),
                    {
                        "baseline_id": baseline_id,
                        "agent_id": agent_id,
                        "version_tag": version_tag,
                        "artifact": artifact,
                        "template_sha256": template_sha256,
                        "recorded_at": now,
                    },
                )
            else:
                # SQLite (and any future dialect with no UPSERT
                # surface — fall through to the canonical
                # INSERT-OR-REPLACE).
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO prompt_template_baselines "
                        "(baseline_id, agent_id, version_tag, artifact, "
                        "template_sha256, recorded_at) VALUES ("
                        ":baseline_id, :agent_id, :version_tag, "
                        ":artifact, :template_sha256, :recorded_at"
                        ")"
                    ),
                    {
                        "baseline_id": baseline_id,
                        "agent_id": agent_id,
                        "version_tag": version_tag,
                        "artifact": artifact,
                        "template_sha256": template_sha256,
                        "recorded_at": now,
                    },
                )
        with Session(self.engine) as session:
            row = session.exec(
                select(PromptTemplateBaseline)
                .where(
                    col(PromptTemplateBaseline.agent_id) == agent_id,
                    col(PromptTemplateBaseline.version_tag) == version_tag,
                    col(PromptTemplateBaseline.artifact) == artifact,
                )
            ).one_or_none()
            assert row is not None, (
                f"baseline upsert for ({agent_id}, {version_tag!r}, "
                f"{artifact}) committed but Session.get returned None"
            )
            return row

    # ─────────────────────────────────────────────────────────────────
    # Writes — R-B2 auto-reset (drift detection + run)
    # ─────────────────────────────────────────────────────────────────

    def detect_template_drift(
        self,
        agent_id: str,
        version_tag: str,
        agents_root: Any,
        *,
        actor: ActorContext,
        prompt_version_repo: PromptVersionRepository,
        artifact_filter: Optional[frozenset[str]] = None,
        run_reset: bool = True,
        is_custom_agent: bool = False,
    ) -> list[TemplateDriftResult]:
        """Walk the agent's CORE artifacts vs the recorded baselines.

        For each ``(agent_id, version_tag, artifact)``:

        1. Skip when MEMORY-class (carve-out — R-B2 CORE-only).
        2. Skip when no baseline was ever recorded (no
           ``get_baseline`` row) — the ``record_initial_baselines``
           path seeds the row first; the lazy-seed flow at
           detection time avoids a circular dependency.
        3. Compute the live file's sha256.
        4. If the live hash differs from the recorded baseline,
           ``drift_detected=True`` carries
           ``(old_sha256, new_sha256)``; if
           ``run_reset=True`` (the default) the R-B2 auto-reset
           fires (the audit event is stamped via
           :meth:`stamp_template_reset_audit`).
        5. If the live hash matches, ``drift_detected=False`` and
           no audit row is stamped (idempotency — re-running
           against unchanged files = zero events).
        6. Custom agents (``is_custom_agent=True``) return ``[]``
           with no rows inspected (the A2/R-A2 hook).

        The R-B2 reset semantics:

        * The ``un-selected`` default resolution path resets
          (``latest_approved → template-file`` falls through to
          the new file base).
        * An EXPLICIT ``is_selected=1`` pointer SURVIVES the
          reset (R-B3 — user intent wins). The
          :attr:`TemplateDriftResult.selection_preserved` flag
          captures the prior state.
        * Approved history rows REMAIN (the audit trail is
          append-only immutable per A1).

        Args:
            agent_id: Resolved agent identity.
            version_tag: Version tag (``""`` for base).
            agents_root: Path to the ``agents`` root directory.
            actor: Audit actor (boot-side internal actor for the
                lifecycle coder — ``auth_method='internal'``).
            prompt_version_repo: Reference to the
                :class:`PromptVersionRepository` for the
                selection-preservation check (R-B3) + the
                audit-event stamp.
            artifact_filter: Optional explicit set of CORE
                artifacts to inspect; default = all of
                :data:`PROMPT_ARTIFACTS`.
            run_reset: When ``True`` (default), execute the R-B2
                auto-reset + stamp the audit event. When
                ``False``, do the drift-detection walk WITHOUT
                acting — useful for dry-run / preview surfaces.
            is_custom_agent: When True, return ``[]`` without
                touching the DB (the A2/R-A2 hook).

        Returns:
            A list of :class:`TemplateDriftResult` — one per
            artifact inspected (in artifact-ascending order). The
            caller can compute the journal of drift events from
            the list (``sum(1 for r in results if r.drift_detected)``
            + ``sum(1 for r in results if r.drift_skipped)``).
        """
        from pathlib import Path

        if is_custom_agent:
            return []
        version_tag = _normalize_version_tag(version_tag)
        artifacts = artifact_filter or frozenset(PROMPT_ARTIFACTS)
        agents_root_path: Path = Path(agents_root)
        agent_dir = agents_root_path / agent_id

        out: list[TemplateDriftResult] = []
        for artifact in sorted(artifacts):
            try:
                self._validate_artifact_class_core_only(artifact)
            except ValueError:
                # MEMORY-class artifact appears in PROMPT_ARTIFACTS
                # by accident — skip silently (defensive: the
                # validation rejects up-front; the message would
                # otherwise log spam during the walk).
                out.append(
                    TemplateDriftResult(
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        drift_detected=False,
                        old_sha256=None,
                        new_sha256=None,
                        drift_skipped=True,
                        skip_reason="memory_class",
                        selection_preserved=False,
                    )
                )
                continue

            baseline_row = self.get_baseline(agent_id, version_tag, artifact)
            if baseline_row is None:
                # No baseline recorded — the R-B1 path seeds
                # baselines at boot; if it didn't run (e.g. a
                # late-arriving artifact), the lazy-seed flow
                # records one now and reports ``no_baseline_recorded``
                # (the caller can opt to seed for next time). For
                # THIS run, treat as "no drift to detect".
                if run_reset:
                    artifact_filename = _artifact_to_filename(artifact)
                    artifact_path = agent_dir / artifact_filename
                    if artifact_path.exists():
                        content = artifact_path.read_text(encoding="utf-8")
                        sha = self._sha256_hex_str(content)
                        self._upsert_baseline(
                            agent_id=agent_id,
                            version_tag=version_tag,
                            artifact=artifact,
                            template_sha256=sha,
                        )
                out.append(
                    TemplateDriftResult(
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        drift_detected=False,
                        old_sha256=None,
                        new_sha256=None,
                        drift_skipped=True,
                        skip_reason="no_baseline_recorded",
                        selection_preserved=False,
                    )
                )
                continue

            artifact_filename = _artifact_to_filename(artifact)
            artifact_path = agent_dir / artifact_filename
            if not artifact_path.exists():
                # The shipped file is gone — U4 / R-A2 boundary
                # case; record the skip and continue.
                out.append(
                    TemplateDriftResult(
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        drift_detected=False,
                        old_sha256=baseline_row.template_sha256,
                        new_sha256=None,
                        drift_skipped=True,
                        skip_reason="no_file_to_hash",
                        selection_preserved=False,
                    )
                )
                continue

            content = artifact_path.read_text(encoding="utf-8")
            new_sha = self._sha256_hex_str(content)
            old_sha = baseline_row.template_sha256
            if new_sha == old_sha:
                out.append(
                    TemplateDriftResult(
                        agent_id=agent_id,
                        version_tag=version_tag,
                        artifact=artifact,
                        drift_detected=False,
                        old_sha256=old_sha,
                        new_sha256=new_sha,
                        drift_skipped=False,
                        skip_reason=None,
                        selection_preserved=False,
                    )
                )
                continue

            # Drift detected — record the prior pointer state
            # BEFORE running the reset (the reset re-points the
            # baseline, but the user-pointer state on
            # prompt_versions stays as it was).
            prior_selection = prompt_version_repo.get_selection(
                agent_id, version_tag, artifact,
            )
            selection_preserved = prior_selection is not None

            # Stamp the TEMPLATE_RESET audit event BEFORE the
            # baseline re-point, so the audit row reflects the
            # pre-re-point baseline hash. The same ``engine.begin()``
            # block carries both the audit INSERT and the
            # baseline re-point UPDATE — atomic with the version
            # row's ``is_selected`` state.
            if run_reset:
                self.stamp_template_reset_audit_and_repoint_baseline(
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    old_sha256=old_sha,
                    new_sha256=new_sha,
                    selection_preserved=selection_preserved,
                    actor=actor,
                )

            out.append(
                TemplateDriftResult(
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    drift_detected=True,
                    old_sha256=old_sha,
                    new_sha256=new_sha,
                    drift_skipped=False,
                    skip_reason=None,
                    selection_preserved=selection_preserved,
                )
            )
        return out

    def stamp_template_reset_audit_and_repoint_baseline(
        self,
        *,
        agent_id: str,
        version_tag: str,
        artifact: str,
        old_sha256: str,
        new_sha256: str,
        selection_preserved: bool,
        actor: ActorContext,
    ) -> None:
        """Stamp a ``TEMPLATE_RESET`` audit row + re-point the
        baseline (atomic in one ``engine.begin()``).

        Direct-write helper — the audit-event stamp paired with
        the baseline re-point is atomic. The selection state on
        :class:`PromptVersion` is NOT mutated here (R-B3
        preserves explicit pointers; the auto-reset only affects
        the un-selected default resolution path).

        Args:
            agent_id: Resolved agent identity.
            version_tag: Version tag (``""`` for base).
            artifact: Artifact key (CORE-only).
            old_sha256: The recorded baseline hash before the reset.
            new_sha256: The new live-file hash (the post-re-point
                baseline).
            selection_preserved: Whether an explicit
                ``is_selected=1`` row survived (R-B3). Recorded
                on the audit payload.
            actor: Audit actor.
        """
        self._validate_artifact_class_core_only(artifact)
        version_tag = _normalize_version_tag(version_tag)
        old_sha256 = self._normalize_template_sha256(old_sha256)
        new_sha256 = self._normalize_template_sha256(new_sha256)
        now = _now_iso()
        # The audit row carries the version_id-shaped identity — we
        # synthesize a placeholder version_id for the audit event
        # because the TEMPLATE_RESET event isn't tied to a single
        # PromptVersion row (the reset is a class-wide action on
        # the baseline, not a row mutation). The convention: use
        # a stable token ``__baseline__`` so the audit-list filter
        # by ``version_id='__baseline__'`` returns the
        # class-wide reset history.
        TEMPLATE_RESET_VERSION_ID = "__baseline__"
        with self.engine.begin() as conn:
            # 1) Audit event — stamped INSIDE the same txn (A3).
            conn.execute(
                PromptAuditEvent.__table__.insert().values(
                    event_id=str(uuid.uuid4()),
                    agent_id=agent_id,
                    version_tag=version_tag,
                    artifact=artifact,
                    version_id=TEMPLATE_RESET_VERSION_ID,
                    event_type="TEMPLATE_RESET",
                    actor_subject=actor.subject,
                    actor_auth_method=actor.auth_method,
                    payload={
                        "old_sha256": old_sha256,
                        "new_sha256": new_sha256,
                        "selection_preserved": selection_preserved,
                    },
                    created_at=now,
                )
            )
            # 2) Baseline re-point — UPDATE template_sha256 +
            # recorded_at on the matching key. The UNIQUE
            # constraint lets us UPDATE in place (no second row).
            conn.execute(
                PromptTemplateBaseline.__table__.update()
                .where(
                    col(PromptTemplateBaseline.agent_id) == agent_id,
                    col(PromptTemplateBaseline.version_tag) == version_tag,
                    col(PromptTemplateBaseline.artifact) == artifact,
                )
                .values(template_sha256=new_sha256, recorded_at=now)
            )


def _artifact_to_filename(artifact: str) -> str:
    """Map a :data:`PROMPT_ARTIFACTS` key to the on-disk filename.

    Phase A T9 / S10 file-content convention: artifact names ship
    without ``.md`` (e.g. ``soul``) and the on-disk file is
    ``<artifact>.md`` under ``<agents_root>/<agent_id>/``.

    Special pairs:

    * ``tools_note`` → ``tools_note.md`` (the primary
      fallback). The legacy ``tools.md`` rides the same key
      (handled by the loader — the baseline table tracks the
      canonical artifact key ``tools_note``, not the
      file-shape ``tools``). The legacy ``tools.md`` is OUT of
      the baseline's R-B2 walk — only the canonical
      ``tools_note.md`` is hashed; if absent, no baseline is
      recorded.
    * ``tools`` → ``tools_note.md`` (same primary path; the
      legacy ``tools.md`` file is a duplicate that the loader
      carries as a fallback — Phase A R8/S10 carves it in).

    The map is intentionally small and additive — a future
    artifact lands here when the artifact joins
    :data:`PROMPT_ARTIFACTS`.
    """
    if artifact == "tools_note":
        return "tools_note.md"
    if artifact == "tools":
        return "tools_note.md"
    return f"{artifact}.md"


__all__ = [
    "PromptVersionRepository",
    "VALID_TRANSITIONS",
    "InvalidTransitionError",
    "VersionNotEditableError",
    "VersionNotDeletableError",
    "MemoryClassError",
    "PromptClassMismatchError",
    "ActorContext",
    "actor_from_instance",
    "actor_from_request",
    "AUTH_METHOD_SESSION",
    "AUTH_METHOD_BEARER",
    "AUTH_METHOD_INTERNAL",
    "ARTIFACT_CLASS_CORE",
    "ARTIFACT_CLASS_MEMORY",
    "ARTIFACT_CLASS_TYPES",
    "class_for_artifact",
    "validate_artifact_class",
    "EffectiveRow",
    "BaselineRepository",
    "TemplateDriftResult",
]