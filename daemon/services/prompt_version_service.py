"""Prompt-version service — thin async facade (agent-prompt-immutability Phase A; Phase B extensions).

Phase A's service layer for the prompt-version DB foundation. Mirrors
the :class:`SkillStoreService` facade at
:mod:`daemon.services.skill_store_service` (the project's canonical
thin-async-facade docstring, lines 1-60): holds no engine, every
public method = one ``await asyncio.to_thread(...)`` wrapping a
single repo call. Multiple repo calls in one method share the
same thread to keep the session boundary clean.

Phase B additions (T1+T2+T3+T10, see
``.agents/shared/planning/agent-prompt-immutability/phaseB-plan.md``):

* The module-level docstring block at the bottom of this string
  documents the **F1 OVERRIDES-ONLY seeding strategy** (T10) — no
  boot seeding, first ``create_draft`` per key = version 1; the
  on-disk template file stays the live fallback. The loader-side
  docstring note is owned by a sibling cycle; this one stays the
  service-side authoritative reference.
* ``approve`` / ``reject`` now refuse every caller whose actor is
  not ``auth_method == "session"`` (T3, D2, decisions.md F4) — even
  with ``prompts:approve`` scope, a bearer token is the machine
  lane and cannot approve. The repo's defense-in-depth check
  (``only HTTP actors may approve``) stays; the service is the
  surface that names the typed error.
* ``retract`` is a new method (T2, S16/O-13 fold-in) that gates
  retraction by **own-row identity** — internal submitters may
  retract only the rows they themselves submitted, and only humans
  (``auth_method == "session"``) may retract any row. The check
  enforces the same anti-forgery rule as ``actor_from_request``
  (an HTTP presenter cannot mint ``internal`` — the retract
  authority check reads ``actor.instance_id`` against the row's
  ``submitter_instance_id`` for the internal path, and reuses
  the project's "session == trusted human" convention for HTTP).
* USER DIRECTIVE #2 (2026-10-14, frontend specs) — the CRUD + badge
  deltas: ``update_version_content`` / ``delete_version`` (thin
  wrappers; the editable-state guard draft/pending-ONLY lives in the
  repo and raises the typed ``VersionNotEditableError`` /
  ``VersionNotDeletableError`` the router maps to ``409
  VERSION_IMMUTABLE``) and ``count_pending`` (the Persona UI badge
  feed). Approved rows stay append-only immutable history; rejected
  is terminal; hard-delete of approved history is a MORNING FORK and
  is deliberately NOT built.

Why a service layer
-------------------

The repository (:mod:`daemon.repositories.prompt_version.repository`)
stays the source of truth for SQL behavior — every CRUD primitive is
a synchronous method that opens its own ``Session`` and commits.
The service layer adds:

* **Async facade.** Repo methods are sync; callers in the daemon's
  async event loop hop through ``asyncio.to_thread`` so the DB
  never blocks the loop. The service exposes ``async def`` methods
  that callers can ``await`` directly.

* **Routers reach via the manager** (manager.py:1171-1173 next to
  ``_skill_bank_repo``) — :attr:`InstanceManager._prompt_version_service`
  holds the singleton.

* **No LLM / no embedding / no async I/O.** Pure daemon-DB layer;
  Phase A does NOT touch the loader hook or inner_soul — those
  live in the Phase B / Phase C scope.

* **Loader hook takes the SYNC repo directly** (A4/C6) — the
  loader's 4 call sites are sync; routing them through the
  async facade would deadlock the event loop or force a mid-build
  refactor. This service is the consumer-facing facade for routers
  + inner_soul/agent_mother callers only.

* **State machine + lane guards.** Phase B widens the service's
  responsibility from "thin facade" to "the only consumer-facing
  surface for mutating approval rows." Repository methods take
  an ``actor`` for stamping, but the human-only approve/reject rule
  (D2) lives at THIS layer so the typed error surfaces here
  (testable independently of the HTTP layer) AND the repo's
  defense-in-depth gate stays the last line of defense (R3
  source-forgery risk: a developer calling ``repo.approve``
  directly bypasses the service).

Composition
-----------

* All 13 A2 methods pass through (one-to-one with the repo).
* The A5/C4 composite ``create_and_submit`` passes through.
* The A8 ``get_effective_rows`` passes through (loader's
  fingerprint-bust-on-get hook reads this on every
  ``load_and_cache_prompt`` call — but the loader uses the SYNC
  repo, not this facade, per A4).

Design notes
------------

* **No engine held** — service constructor takes the repo only.
* **Defensive repo call wrapping.** Each ``async`` method awaits
  a single ``asyncio.to_thread`` invocation that calls into the
  repo. Multiple repo calls (e.g. the SELECT-MAX + INSERT in
  :meth:`PromptVersionRepository.create_draft`) live inside the
  same thread to keep the DB session boundary clean — the repo
  handles those for us.
* **No global state.** The service is trivially testable with
  mock repos.

F1 OVERRIDES-ONLY seeding strategy (T10)
========================================

The :class:`PromptVersionRepository` resolves a prompt via the chain
``selected_version.content`` → ``latest_approved.content`` →
``None`` (F6; per-phase-contract.md A8). The ``None`` case means
"the resolver falls back to the on-disk template file at
``agents/<name>/<artifact>.md``" — the SAME fallback the loader
has used since before Phase A; no behavior change for agents that
never submit an override.

* **No boot seeding.** There is no code anywhere that, at daemon
  boot, walks ``agents/<name>/<artifact>.md`` files and inserts
  ``(version=1, state=approved)`` rows for them. Such a code
  path does not exist in this repository.
* **No lazy-seed, hashed or otherwise.** The first
  ``create_draft`` / ``create_and_submit`` call for a key — i.e.
  the moment a runtime prompt needs to diverge from the template
  — inserts ``version = 1`` and the row carries the requested
  override content (NOT a copy of the template file).
* **Template file stays live.** Until a DB row exists for an
  artifact, the loader reads the template file directly. A new
  template release therefore upgrades every agent that has no
  override rows for that artifact, automatically.
* **Resolution reads DB before file.** The loader hook runs
  ``repo.get_effective_rows(agent_id, version_tag)`` BEFORE the
  file read; if the DB returns any row for the artifact, the
  file content is discarded entirely. The on-disk file is only
  read when the DB lookup yields no rows for the artifact.

Rejected alternatives (documented per T10, morning-review hook):

* **Full-seed-at-boot.** Walk every ``agents/<name>/<artifact>.md``
  on daemon start, insert each as ``(version=1, state=approved)``.
  *Why rejected.* Every template file release becomes a re-seed
  event; the boot blast radius grows unbounded over the project's
  lifetime; agent-v2 sibling dirs (F9) get phantom v1 rows that
  mask future template changes for the v2 lineage. The
  template-is-the-baseline model has no re-seed event.
* **Hash-guarded lazy-seed.** On first DB read for a key, fingerprint
  the on-disk template; if the fingerprint differs from the
  fingerprint stored on the last-seen row, auto-supersede.
  *Why rejected.* Correct, but more state (a per-artifact fingerprint
  table), and the "first override" identity becomes confusing
  (``approved`` with no submitter — a fingerprint-only identity).
  Switch to this only if morning review finds OVERRIDES-ONLY
  insufficient (F1 is the conservative default; the agent-prompt-
  immutability commission explicitly deferred this choice).

The contract Phase B's consumers rely on: ``create_and_submit``
succeeds on a fresh key and returns ``(version=1, state='pending_approval')``;
``get_effective`` returns ``None`` for keys with no rows (file fallback
contract); the on-disk template files are NEVER modified by this
service layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional, Union

# Re-export of the repo's typed not-editable / not-deletable errors —
# part of this facade's contract surface (the routers / callers that
# hold only the service can catch them without a second import path).
# noqa: F401 — intentional re-export.
from daemon.repositories.prompt_version.repository import (  # noqa: F401
    VersionNotDeletableError,
    VersionNotEditableError,
)

logger = logging.getLogger(__name__)


# ============================================================
# Typed errors (Phase B, T2 / T3 + Ud#3)
# ============================================================


class HumanLaneRequiredError(ValueError):
    """Raised when approve / reject is called by a non-human actor.

    The ``AuthContext.auth_method`` of the caller is the lane
    discriminator, NOT the scope claim. Per phaseB-plan.md D2 /
    decisions.md F4 / phase-contract.md A3, only
    ``auth_method == "session"`` may approve or reject — even a
    bearer token holding ``prompts:approve`` is refused because the
    bearer lane is the machine lane and a machine cannot satisfy
    the human-only approval rule.

    Inherits from ``ValueError`` so callers can catch both
    :class:`HumanLaneRequiredError` and the more common
    :class:`ValueError` from the repo layer with a single
    ``except ValueError`` clause; the subclass relationship keeps
    :class:`HumanLaneRequiredError`-only catchers working.

    Attributes:
        version_id: The PromptVersion PK the caller tried to act on.
        actor_auth_method: The actor's auth_method (the offending
            lane), useful for the HTTP 403 body.

    The HTTP layer maps this to ``403 HUMAN_LANE_REQUIRED`` (D2);
    the router builds the response body shape
    ``{"error": "HUMAN_LANE_REQUIRED", "message": ...}``.
    """

    def __init__(
        self,
        version_id: str,
        actor_auth_method: str,
    ) -> None:
        self.version_id = version_id
        self.actor_auth_method = actor_auth_method
        super().__init__(
            f"approve/reject on prompt_version {version_id} requires "
            f"auth_method='session' (human lane); got {actor_auth_method!r}. "
            f"Per phaseB-plan.md D2 / F4, only authenticated human "
            f"operators may approve or reject (decisions.md F4)."
        )


class AuthContextMissingError(ValueError):
    """Raised when an HTTP-lane actor helper sees no AuthContext.

    Defensive against the auth-OFF posture (D3 / ``daemon/routers/
    auth.py:96`` ``503 LOGIN_NOT_CONFIGURED`` precedent): the HTTP
    bridge constructs an actor only after a successful auth
    resolution, so a missing AuthContext is a programming error,
    not a runtime auth state. The router-side equivalent raises
    ``503 AUTH_NOT_CONFIGURED`` (consume-side); THIS error is the
    service-side equivalent for callers (test tooling, internal
    helpers) that get a request without a resolved AuthContext.

    Inherits from :class:`ValueError` (uniform error-shape with the
    repo's validation errors).
    """

    def __init__(self, message: str = "no AuthContext on request") -> None:
        super().__init__(
            f"AuthContextMissingError: {message} (per phaseB-plan.md "
            f"D3 / A3 anti-forgery, auth-context-shaped inputs are "
            f"the HTTP lane's only constructor)"
        )


class RetractAuthorityError(PermissionError):
    """Raised when a retract caller fails the own-row authority check.

    Per phaseB-plan.md T2 / decisions.md S16 (anti-forgery), an
    ``auth_method == "internal"`` actor may retract ONLY the row
    they themselves submitted (``actor.instance_id`` ==
    ``row.submitter_instance_id``); any other-instance internal
    caller is refused. ``auth_method == "session"`` may retract any
    row; ``auth_method == "bearer"`` is refused outright (machine
    lane cannot retract — submitter is an instance, not a bearer
    credential).

    Inherits from :class:`PermissionError` (Python stdlib's "this
    caller does not have permission" type) — distinct from
    :class:`HumanLaneRequiredError` so routers / callers can
    discriminate approval-lane refusals from retract-lane refusals
    without parsing error strings. Catchers that prefer a single
    ``except ValueError`` still work via :class:`PermissionError`'s
    own (None) base — but :class:`PermissionError` inherits from
    :class:`OSError` and not from :class:`ValueError`, so callers
    MUST catch this error type specifically or use ``except Exception``.

    Attributes:
        version_id: The PromptVersion PK the caller tried to retract.
        actor_auth_method: The actor's auth_method (the offending
            lane).
        actor_instance_id: For ``internal``-lane callers, the
            actor's instance id (``None`` for HTTP lanes).
        row_submitter_instance_id: The row's recorded submitter
            instance id (the field the authority check reads).
        reason: A short stable string suitable for the HTTP 403 body
            (``{"error": "RETRACT_AUTHORITY", "message": ...}``).
            Values: ``"wrong_instance"`` (other-instance internal
            caller), ``"machine_lane_refused"`` (bearer),
            ``"row_not_found"`` (defensive — repo raises
            ``LookupError``, but T2 normalizes it here).
    """

    def __init__(
        self,
        version_id: str,
        actor_auth_method: str,
        actor_instance_id: Optional[str],
        row_submitter_instance_id: Optional[str],
        reason: str,
    ) -> None:
        self.version_id = version_id
        self.actor_auth_method = actor_auth_method
        self.actor_instance_id = actor_instance_id
        self.row_submitter_instance_id = row_submitter_instance_id
        self.reason = reason
        super().__init__(
            f"retract on prompt_version {version_id} refused: "
            f"reason={reason} actor_auth_method={actor_auth_method!r} "
            f"actor_instance_id={actor_instance_id!r} "
            f"row_submitter_instance_id={row_submitter_instance_id!r}. "
            f"Per phaseB-plan.md T2 / S16, retract authority = own "
            f"submitter instance OR a session-lane (human) actor."
        )


# ============================================================
# PromptVersionService
# ============================================================


class PromptVersionService:
    """Async facade over :class:`PromptVersionRepository`.

    Every public method is a thin ``await asyncio.to_thread(...)``
    wrapper around the corresponding sync repo method. Holds no
    engine; the repo reference is the sole dependency.

    Attributes:
        _repo: :class:`PromptVersionRepository` bound to the
            project's SQLAlchemy engine.
    """

    def __init__(self, repo: Any) -> None:
        """Store the repo reference.

        Args:
            repo: :class:`PromptVersionRepository` instance bound to
                the project's SQLAlchemy engine. The service holds
                NO engine — all DB work goes through the repo.
        """
        self._repo = repo

    # --------------------------------------------------------
    # Create
    # --------------------------------------------------------

    async def create_draft(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: Any,
        *,
        state: str = "draft",
    ) -> Any:
        """Create a new draft (A2). Async wrapper."""
        return await asyncio.to_thread(
            self._repo.create_draft,
            agent_id,
            version_tag,
            artifact,
            content,
            actor,
            state=state,
        )

    async def create_and_submit(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content_or_mutator: Union[str, Callable[[Optional[str]], str]],
        actor: Any,
        *,
        file_fallback: Optional[str] = None,
    ) -> Any:
        """Create draft + submit for approval atomically (A5/C4)."""
        return await asyncio.to_thread(
            self._repo.create_and_submit,
            agent_id,
            version_tag,
            artifact,
            content_or_mutator,
            actor,
            file_fallback=file_fallback,
        )

    # --------------------------------------------------------
    # Phase C — User Directive #5A (R-A1 helper wrapper)
    # --------------------------------------------------------

    async def create_initial_approved_v1(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: Any,
        *,
        artifact_class: Optional[str] = None,
    ) -> Any:
        """Async wrapper over ``repo.create_initial_approved_v1``.

        Used by the POST ``/api/custom-agents`` endpoint — R-A1
        helper that bypasses the state machine to write v1 rows
        directly as ``approved`` + ``is_selected=1``. The
        ``conn=`` parameter is intentionally NOT exposed at the
        service layer (the router handles the wide transaction
        itself via ``manager._engine.begin()`` so the config row
        + v1 versions + audit rows all commit atomically — the
        R-A1 atomicity invariant).
        """
        return await asyncio.to_thread(
            self._repo.create_initial_approved_v1,
            agent_id,
            version_tag,
            artifact,
            content,
            actor,
            artifact_class=artifact_class,
        )

    # --------------------------------------------------------
    # State transitions
    # --------------------------------------------------------

    async def submit_for_approval(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``draft → pending_approval`` (A2)."""
        return await asyncio.to_thread(
            self._repo.submit_for_approval,
            version_id,
            actor,
        )

    async def approve(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``pending_approval → approved`` (A2 / T3).

        **Human-only lane guard (D2 / F4 / A7).** ``actor.auth_method``
        MUST be ``"session"``; bearers, internal callers, and
        anything else raise :class:`HumanLaneRequiredError`. Lane
        discriminator beats scope: a bearer with ``prompts:approve``
        is still refused at this layer (the repo's defense-in-depth
        check stays as the second line — both layers raise on the
        same conditions; this one raises the typed error the router
        maps to ``403 HUMAN_LANE_REQUIRED``).

        Args:
            version_id: The PromptVersion PK.
            actor: Caller identity (``actor_from_request`` for the
                HTTP lane). Internal ``actor_from_instance`` is
                refused (see ``self._require_human_lane``).

        Returns:
            The updated :class:`PromptVersion` row.

        Raises:
            HumanLaneRequiredError: If ``actor.auth_method != "session"``.
            InvalidTransitionError: If the row is not in
                ``pending_approval`` (e.g. already approved,
                rejected, or archived).
            LookupError: If the version_id does not exist.
        """
        self._require_human_lane(version_id, actor)
        return await asyncio.to_thread(
            self._repo.approve,
            version_id,
            actor,
        )

    async def reject(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``pending_approval → rejected`` (A2 / T3).

        **Human-only lane guard (D2 / F4 / A7).** Mirror of
        :meth:`approve` — only ``auth_method == "session"`` may
        reject; every other lane raises :class:`HumanLaneRequiredError`.

        Args:
            version_id: The PromptVersion PK.
            actor: Caller identity (``actor_from_request`` for the
                HTTP lane). Internal and bearer lanes are refused.

        Returns:
            The updated :class:`PromptVersion` row.

        Raises:
            HumanLaneRequiredError: If ``actor.auth_method != "session"``.
            InvalidTransitionError: If the row is not in
                ``pending_approval``.
            LookupError: If the version_id does not exist.
        """
        self._require_human_lane(version_id, actor)
        return await asyncio.to_thread(
            self._repo.reject,
            version_id,
            actor,
        )

    async def retract(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Recall a submitted draft (T2 / S16).

        The state-machine retract transition is (the ONLY one this
        method performs):

        * ``pending_approval → draft`` — the user / agent wants to
          take the row back and edit. The :class:`PromptVersion`
          row's ``version`` is preserved (lineage stays intact);
          the user re-submits via a new call to
          ``create_and_submit`` which inserts ``version + 1`` and
          archives this row (per A5/C4).

        A row in ``draft`` state is NOT retractable via this
        method — the repo CAS below always targets the
        ``pending_approval → draft`` pair, so a draft row
        (never submitted) surfaces as :class:`InvalidTransitionError`.
        An abandoned DRAFT is removed via ``delete_version``
        (UD#2 endpoint 14; the editable-state rule there covers
        draft/pending rows).

        Per phaseB-plan.md T2 / decisions.md S16 (anti-forgery),
        retract authority is:

        * ``auth_method == "session"`` (any human operator) — may
          retract ANY row.
        * ``auth_method == "internal"`` (the row's submitter
          instance) — may retract ONLY the row whose
          ``submitter_instance_id`` matches ``actor.instance_id``.
          Anti-forgery holds because ``actor_from_request``
          REFUSES to mint ``internal`` for an HTTP request — the
          only path to ``internal`` is ``actor_from_instance``,
          which IS in-process and trustworthy.
        * ``auth_method == "bearer"`` — REFUSED outright. The
          bearer lane is machine-only; no machine identity can
          retract a row that an instance submitted.

        **HTTP wiring (F2 gate finding, 2026-10-14).** The HTTP
        router ``daemon/routers/prompts.py:765`` MUST route through
        this method. A prior bypass via ``svc.atomic_transition(
        version_id, "pending_approval", "draft", actor)`` reached
        the repo WITHOUT this authority gate — any caller with
        ``prompts:submit`` could retract ANY actor's pending row.
        Callers that need the underlying CAS without authority
        must come through this method, not through
        ``svc.atomic_transition``.

        All other :class:`ActorContext` fields are read for the
        authority check but never asserted from a request body —
        :meth:`actor_from_request` ignores body-asserted actor
        fields (R3 source-forgery ticket, critical note 257488dc).

        Args:
            version_id: The PromptVersion PK.
            actor: Caller identity. ``actor_from_request`` for
                human sessions; ``actor_from_instance`` for the
                submitting agent's own retract; bearer is
                refused.

        Returns:
            The updated :class:`PromptVersion` row.

        Raises:
            RetractAuthorityError: If the caller fails the
                own-row authority check (other-instance internal,
                bearer, or any condition the matrix below marks
                as REFUSED).
            ValueError: If ``actor.auth_method`` is not a known
                value (the service never invents identities).
            InvalidTransitionError: If the row is not in a
                retract-able state (already approved / rejected /
                archived — those are terminal).
            LookupError: If the version_id does not exist.

        Authority matrix (per T2 fold-in):

        ==================  ====================================
        actor.auth_method   result
        ==================  ====================================
        ``session``         ALLOWED for any row
        ``internal``        ALLOWED iff ``actor.instance_id ==
                            row.submitter_instance_id``;
                            otherwise ``RETRACT_AUTHORITY`` with
                            ``reason="wrong_instance"``
        ``bearer``          REFUSED with
                            ``reason="machine_lane_refused"``
        anything else       REFUSED via ``ValueError``
        ==================  ====================================
        """
        auth_method = getattr(actor, "auth_method", None)
        if auth_method not in ("session", "internal", "bearer"):
            raise ValueError(
                f"retract on {version_id}: actor.auth_method "
                f"{auth_method!r} is not a known value "
                f"(session/internal/bearer)"
            )

        if auth_method == "bearer":
            # Machine lane cannot retract a row submitted by an
            # instance identity (the row's submitter_subject is an
            # instance id, not a bearer-credential subject).
            raise RetractAuthorityError(
                version_id=version_id,
                actor_auth_method=auth_method,
                actor_instance_id=None,
                row_submitter_instance_id=None,
                reason="machine_lane_refused",
            )

        if auth_method == "internal":
            # Own-row authority check: the actor's instance_id must
            # match the row's submitter_instance_id. We read the
            # row first (the repo's get_version is a no-lock
            # single-row read — safe to run before the
            # transition).
            row = await asyncio.to_thread(
                self._repo.get_version, version_id,
            )
            if row is None:
                raise LookupError(
                    f"prompt_version {version_id} not found "
                    f"(retract authority check)"
                )
            actor_instance_id = getattr(actor, "instance_id", None)
            if actor_instance_id != row.submitter_instance_id:
                raise RetractAuthorityError(
                    version_id=version_id,
                    actor_auth_method=auth_method,
                    actor_instance_id=actor_instance_id,
                    row_submitter_instance_id=row.submitter_instance_id,
                    reason="wrong_instance",
                )

        # auth_method == "session" OR internal+own-row: pass the
        # gate. The repo's atomic_transition is the next gate (it
        # validates A1 transitions and stamps the audit row inside
        # the same engine.begin() txn as the UPDATE).
        if auth_method == "session":
            # Per A1: pending_approval → draft (recall the
            # pending row) is the only session-friendly retract.
            return await asyncio.to_thread(
                self._repo.atomic_transition,
                version_id,
                "pending_approval",
                "draft",
                actor,
            )
        # internal+own-row: the SAME pending_approval → draft pair
        # (a never-submitted draft row is NOT retractable here —
        # use delete_version for abandoned drafts; see the
        # docstring). The row was read once above for the
        # authority check; the CAS below refuses atomically if the
        # row's state moved between that read and this transition
        # (the from_state guard IS the serialization).
        return await asyncio.to_thread(
            self._repo.atomic_transition,
            version_id,
            "pending_approval",
            "draft",
            actor,
        )

    # --------------------------------------------------------
    # Internal helpers — lane guards (T3)
    # --------------------------------------------------------

    @staticmethod
    def _require_human_lane(version_id: str, actor: Any) -> None:
        """Raise :class:`HumanLaneRequiredError` unless the actor is human.

        Helper for :meth:`approve` and :meth:`reject`. The check
        is a single equality test:

        * ``actor.auth_method == "session"`` — the human operator
          lane; passes.
        * ``actor.auth_method == "bearer"`` / ``"internal"`` /
          anything else — REFUSED.

        Per phaseB-plan.md D2, the lane discriminator (not the
        scope claim) is the source of authority. This is the
        service-side gate; the repo's atomic_transition then
        validates the A1 state pair (defense-in-depth, R3).

        **Ride-along (F2 gate, 2026-10-14).** A bearer carrying
        ``prompts:approve`` scope is still refused here — lane
        discriminator beats scope (D2). The repo's defense-in-depth
        check (``only HTTP actors may approve``) is the second line;
        if a future router bug ever lets a bearer reach ``approve``
        or ``reject``, this gate refuses with the typed
        :class:`HumanLaneRequiredError` carrying:

        * ``version_id`` — the PromptVersion PK the caller tried
          to act on.
        * ``actor_auth_method`` — the offending lane string
          (``"bearer"`` / ``"internal"`` / ``"<missing>"`` when the
          actor has no ``auth_method`` attribute); surfaces in the
          HTTP 403 body for log forensics.

        The HTTP router maps this to ``403 HUMAN_LANE_REQUIRED``
        (D2 / phase-contract.md A3); the typed error is the
        testable refusal the F2 ride-along requires.
        """
        auth_method = getattr(actor, "auth_method", None)
        if auth_method != "session":
            raise HumanLaneRequiredError(
                version_id=version_id,
                actor_auth_method=auth_method or "<missing>",
            )

    async def atomic_transition(
        self,
        version_id: str,
        from_state: str,
        to_state: str,
        actor: Any,
        *,
        approver_subject: Optional[str] = None,
        approver_auth_method: Optional[str] = None,
    ) -> Any:
        """Generic guarded state transition (A2 / A1)."""
        return await asyncio.to_thread(
            self._repo.atomic_transition,
            version_id,
            from_state,
            to_state,
            actor,
            approver_subject=approver_subject,
            approver_auth_method=approver_auth_method,
        )

    # --------------------------------------------------------
    # Selection pointer
    # --------------------------------------------------------

    async def set_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        actor: Any,
    ) -> None:
        """Set the per-key one-selection-pointer (A2 / F5 / R3)."""
        return await asyncio.to_thread(
            self._repo.set_selection,
            agent_id,
            version_tag,
            artifact,
            version_id,
            actor,
        )

    async def clear_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        actor: Any,
    ) -> None:
        """Clear the per-key one-selection-pointer (A2 / F5 / R3)."""
        return await asyncio.to_thread(
            self._repo.clear_selection,
            agent_id,
            version_tag,
            artifact,
            actor,
        )

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    async def get_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Any:
        """Fetch the currently-selected row for the key (A2)."""
        return await asyncio.to_thread(
            self._repo.get_selection,
            agent_id,
            version_tag,
            artifact,
        )

    async def list_versions(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """List versions for a key, newest first (A2)."""
        return await asyncio.to_thread(
            self._repo.list_versions,
            agent_id,
            version_tag,
            artifact,
            limit=limit,
            offset=offset,
        )

    async def get_version(self, version_id: str) -> Any:
        """Fetch a single row by PK (A2)."""
        return await asyncio.to_thread(self._repo.get_version, version_id)

    async def get_latest_approved(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Any:
        """Fetch the latest-approved row for the key (A2 / F6)."""
        return await asyncio.to_thread(
            self._repo.get_latest_approved,
            agent_id,
            version_tag,
            artifact,
        )

    async def get_effective(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[str]:
        """Resolve the effective prompt content (A2 / F6 / A8).

        Precedence: ``selected_version.content`` >
        ``latest_approved.content`` > ``None``.
        """
        return await asyncio.to_thread(
            self._repo.get_effective,
            agent_id,
            version_tag,
            artifact,
        )

    async def get_effective_rows(
        self,
        agent_id: str,
        version_tag: str,
    ) -> list:
        """ONE-SELECT effective read for the (agent_id, version_tag) pair (A8).

        Returns the selected + latest-approved row ids + content for
        every artifact that has DB rows, sorted by artifact name.
        The loader hook's fingerprint-bust-on-get check consumes
        this (BUT via the SYNC repo, A4 — this facade is for
        routers + inner_soul/agent_mother callers).
        """
        return await asyncio.to_thread(
            self._repo.get_effective_rows,
            agent_id,
            version_tag,
        )

    # --------------------------------------------------------
    # Audit
    # --------------------------------------------------------

    async def append_audit_event(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        event_type: str,
        actor: Any,
        payload: Optional[dict] = None,
    ) -> Any:
        """Append a single audit row (A2).

        Standalone variant — most audit rows are stamped by the
        mutating repo methods inside their ``engine.begin()``
        transaction (A3). Use this only when an audit event must
        be recorded outside a repo-level mutation (rare).
        """
        return await asyncio.to_thread(
            self._repo.append_audit_event,
            agent_id,
            version_tag,
            artifact,
            version_id,
            event_type,
            actor,
            payload,
        )

    async def list_audit_events(
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
    ) -> tuple[list, int]:
        """List audit events, newest first, with optional filters (Phase B T5).

        Thin ``await asyncio.to_thread(...)`` wrapper over
        :meth:`PromptVersionRepository.list_audit_events`. Returns
        ``(items, total)`` where ``items`` is the page of
        :class:`PromptAuditEvent` rows ordered by ``created_at DESC``
        and ``total`` is the unfiltered-by-pagination count for the
        same WHERE clause (the ``{items, total}`` envelope — see
        ``daemon/routers/skill_bank.py:72-108``).

        All filters are optional and combine with AND. ``None``
        means "no filter on this column." The router is the
        authoritative validator of the filter values
        (path-param allowlist ``^[A-Za-z0-9_-]+$`` from S4); the
        service is a passthrough for them. The repo normalizes
        ``version_tag=None`` / ``""`` to ``""`` so the query
        semantics are stable across drivers.
        """
        return await asyncio.to_thread(
            self._repo.list_audit_events,
            agent_id,
            version_tag,
            artifact,
            version_id,
            event_type,
            actor_subject,
            limit=limit,
            offset=offset,
        )

    # --------------------------------------------------------
    # USER DIRECTIVE #2 (2026-10-14) — content edit / delete /
    # pending-count (the CRUD + badge API deltas, deliverable 2).
    # --------------------------------------------------------

    async def update_version_content(
        self,
        version_id: str,
        content: str,
        actor: Any,
    ) -> Any:
        """Replace the content of a draft/pending row in place (UD#2).

        Thin wrapper — the editable-state guard (draft/pending ONLY;
        approved = append-only immutable history, rejected = terminal,
        archived = superseded) lives in the repo
        (:meth:`PromptVersionRepository.update_version_content`) and
        raises the typed
        :class:`daemon.repositories.prompt_version.repository.VersionNotEditableError`
        carrying the row's current state; the router maps it to
        ``409 VERSION_IMMUTABLE``. The UPDATED audit row (prior/new
        content_hash + byte sizes) is stamped inside the same
        ``engine.begin()`` as the content UPDATE.

        Lane note: no human-lane guard here (unlike approve/reject) —
        editing an UNAPPROVED draft/pending row is a ``prompts:write``
        operation available to both HTTP lanes per the endpoint table
        (USER DIRECTIVE #2); the immutable-history protection is the
        state rule itself, not the lane.

        Raises:
            VersionNotEditableError: Non-editable current state.
            LookupError: Unknown version_id.
        """
        return await asyncio.to_thread(
            self._repo.update_version_content,
            version_id,
            content,
            actor,
        )

    async def delete_version(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Hard-delete a draft/pending row (UD#2).

        Thin wrapper — same editable-state rule as
        :meth:`update_version_content`; the repo raises the typed
        :class:`daemon.repositories.prompt_version.repository.VersionNotDeletableError`
        carrying the row's current state (mapped to
        ``409 VERSION_IMMUTABLE``). The DELETED audit row (identity +
        content_hash + content byte length in the payload — forensics
        survive the deleted row) is stamped inside the same
        ``engine.begin()`` as the DELETE. Hard-delete of APPROVED
        history is a morning fork and is deliberately NOT built.

        Raises:
            VersionNotDeletableError: Non-deletable current state.
            LookupError: Unknown version_id.
        """
        return await asyncio.to_thread(
            self._repo.delete_version,
            version_id,
            actor,
        )

    async def count_pending(self) -> int:
        """Count rows in state ``pending_approval`` (UD#2 badge feed).

        Plain count — the A8 effective fingerprint does not apply
        (a badge answers "how many rows await approval"); drains as
        approve/reject land.

        User Directive #3 (2026-10-14): CORE-only — MEMORY-class
        rows have no pending state; see repo's ``count_pending``
        for the predicate.
        """
        return await asyncio.to_thread(self._repo.count_pending)

    # --------------------------------------------------------
    # User Directive #3 (2026-10-14, RESOLUTION-CORE) — MEMORY-class
    # creator + class-aware create_draft forwarding
    # --------------------------------------------------------

    async def create_memory_version(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: Any,
    ) -> Any:
        """Persist a MEMORY-class row directly (Ud#3 binding blocks 1+2).

        MEMORY rows are TRANSITION-FREE and born-effective: this
        method writes a row in ``state='approved'`` and stamps
        one ``CREATED`` audit event with
        ``payload.artifact_class='MEMORY'``. The row appears in
        the loader resolution chain the same instant it commits —
        no submit/approve cycle needed.

        The :class:`daemon.repositories.prompt_version.repository.MemoryClassError`
        surfaces only when the :class:`PromptVersionRepository`
        refuses (a non-MEMORY-class artifact was supplied) — the
        router maps that to ``409 MEMORY_NOT_GOVERNED``.

        Args:
            agent_id: Resolved agent identity.
            version_tag: Version tag (``""`` for base; F9).
            artifact: Artifact key (``memory`` / ``user`` /
                ``growth`` / ``memories/<slug>``).
            content: Override body.
            actor: Submitter identity.

        Returns:
            The freshly inserted :class:`PromptVersion` row.

        Raises:
            ValueError: On bad artifact class (the repo's
                :class:`PromptClassMismatchError` covers the
                mismatch path).
        """
        return await asyncio.to_thread(
            self._repo.create_memory_version,
            agent_id,
            version_tag,
            artifact,
            content,
            actor,
        )


__all__ = [
    "PromptVersionService",
    "HumanLaneRequiredError",
    "AuthContextMissingError",
    "RetractAuthorityError",
    "VersionNotEditableError",
    "VersionNotDeletableError",
    "MemoryClassError",
]


# User Directive #3 (2026-10-14) — surface the typed MEMORY-class
# error through this facade. Routers / callers that hold only the
# service can catch it without a second import path.
# noqa: F401 — intentional re-export.
from daemon.repositories.prompt_version.repository import (  # noqa: E402,F401
    ARTIFACT_CLASS_CORE,
    ARTIFACT_CLASS_MEMORY,
    MemoryClassError,
)