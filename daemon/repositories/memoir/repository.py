"""The persona lane — the versioned durable self and its human gate.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` T1.5, D1.11,
D1.12; amendment AM-17).

Layer 3 of the memoir store (design §4.3) is ONE small evolving markdown
document — "who we are". This module is its state machine:

* the transition vocabulary is MIRRORED from the prompt-version lane
  (:data:`daemon.repositories.memoir.models.PERSONA_VALID_TRANSITIONS`),
  so DD-9's gate is enforced by the DATA and not by a convention:
  ``draft -> pending_approval -> approved | rejected | draft`` and
  ``draft -> archived``. ``(draft, approved)`` is NOT a member — the
  human lane is not optional, and a v2 submitter cannot reach
  ``approved`` without passing through ``pending_approval``.
* the approve/reject seam ships the HUMAN-LANE GUARD (D1.12 / AM-17):
  it raises the house ``HumanLaneRequiredError`` unless
  ``actor.auth_method == "session"``. The house discriminator is
  ``daemon/services/prompt_version_service.py`` ``_require_human_lane``
  — the lane, not the scope claim, is the source of authority: even a
  bearer token holding the right scope is refused, because a machine
  cannot satisfy the human-only approval rule.
* ``submit()`` has NO CALLER in v1 (the v1 persona is read-only; the
  five-tool surface of design §5.4 has no persona-edit tool). The seam
  exists so that the v2 lane inherits the guard explicitly instead of
  re-deriving it.

The EPISODE surface (the insert / recall / pin / unpin writers and the
mutability partition) arrives in phase 2 — this module carries the
PERSONA lane only.

A NOTE ON THE ERROR CLASSES: the transition error is a LOCAL class
(:class:`MemoirTransitionError`) because the house
``InvalidTransitionError`` lives in the prompt-version repository and a
cross-lane import would couple the two artifact families. The
human-lane error IS the house class — it is imported lazily inside the
guard so the repository layer keeps no module-load dependency on the
service layer, and so the HTTP layer's existing ``403 HUMAN_LANE_REQUIRED``
mapping keeps working unchanged.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from daemon.repositories.memoir.models import (
    PERSONA_SOURCES,
    PERSONA_STATUSES,
    PERSONA_VALID_TRANSITIONS,
    MemoirPersona,
    MemoirPersonaVersion,
)
from daemon.services.timestamps import now_utc, now_utc_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class MemoirTransitionError(ValueError):
    """Raised when a persona version transition is not permitted.

    The mirror of the prompt-version lane's ``InvalidTransitionError``:
    a ``ValueError`` subclass, so a caller can catch ``ValueError`` and
    still get the specific type.
    """

    def __init__(self, version_id: str, from_status: str, to_status: str) -> None:
        self.version_id = version_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"persona version {version_id}: transition {from_status!r} -> "
            f"{to_status!r} is not permitted"
        )


class MemoirPersonaRepository:
    """Sync repository over the persona layer.

    Sync class operating through ``Session`` blocks (the
    ``daemon/repositories/bash_spawn/repository.py`` precedent).
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── reads ─────────────────────────────────────────────────────────────

    def list_versions(self, agent_id: str) -> list[MemoirPersonaVersion]:
        """Every persona version for the agent, oldest version first."""
        with Session(self.engine) as session:
            stmt = (
                select(MemoirPersonaVersion)
                .where(MemoirPersonaVersion.agent_id == agent_id)
                .order_by(MemoirPersonaVersion.version)
            )
            return list(session.exec(stmt).all())

    def get_version(self, version_id: str) -> MemoirPersonaVersion | None:
        """One persona version by id, or ``None``."""
        with Session(self.engine) as session:
            return session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == version_id
                )
            ).first()

    def get_current(self, agent_id: str) -> MemoirPersonaVersion | None:
        """The version currently in force for the agent, or ``None``.

        The pointer row (``memoir_persona.current_version_id``) decides;
        a pointer that names a missing row resolves to ``None`` (the
        briefing then renders its "persona unavailable" prefix — AM-39).
        """
        with Session(self.engine) as session:
            pointer = session.exec(
                select(MemoirPersona).where(MemoirPersona.agent_id == agent_id)
            ).first()
            if pointer is None or not pointer.current_version_id:
                return None
            return session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == pointer.current_version_id
                )
            ).first()

    # ── writes ────────────────────────────────────────────────────────────

    def create_draft(
        self, agent_id: str, body: str, source: str = "ari"
    ) -> MemoirPersonaVersion:
        """Create the NEXT draft version for the agent.

        The version number is ``max(version) + 1`` — the rejected row
        stays in the lineage and a re-submission is a NEW row (the house
        prompt-version rule), so the forensic identity of every version
        is preserved.
        """
        if source not in PERSONA_SOURCES:
            raise ValueError(
                f"persona source must be one of {sorted(PERSONA_SOURCES)}; "
                f"got {source!r}"
            )
        with Session(self.engine) as session:
            versions = session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.agent_id == agent_id
                )
            ).all()
            next_version = max((v.version for v in versions), default=0) + 1
            draft = MemoirPersonaVersion(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                version=next_version,
                body=body,
                status="draft",
                source=source,
            )
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft

    def submit(self, version_id: str, actor: Any = None) -> MemoirPersonaVersion:
        """Move a draft to ``pending_approval``.

        NO CALLER IN v1 (the v1 persona is read-only). The seam exists so
        the v2 lane inherits the guard and the transition vocabulary
        explicitly. ``actor`` is accepted for signature parity with
        :meth:`approve`; submitting is NOT a human-lane action (a
        machine may propose) — only the APPROVAL is.
        """
        return self._transition(version_id, "pending_approval", actor=actor)

    def approve(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
        """Approve a pending persona version — HUMAN LANE ONLY.

        Raises:
            HumanLaneRequiredError: when ``actor.auth_method`` is not
                ``"session"``. The house discriminator is reused
                verbatim (AM-17), so the seam is safe by construction
                rather than safe by a grep pin.
            MemoirTransitionError: when the current status does not
                permit ``pending_approval -> approved``.
        """
        self._require_human_lane(version_id, actor)
        return self._transition(version_id, "approved", actor=actor)

    def reject(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
        """Reject a pending persona version — HUMAN LANE ONLY."""
        self._require_human_lane(version_id, actor)
        return self._transition(version_id, "rejected", actor=actor)

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _require_human_lane(version_id: str, actor: Any) -> None:
        """Raise the house ``HumanLaneRequiredError`` unless human.

        The check is a single equality test on the lane discriminator
        (``actor.auth_method``) — NOT on the scope claim: a bearer
        token holding the right scope is still refused, because a
        machine cannot satisfy the human-only approval rule.
        """
        # Imported lazily: the repository layer keeps no module-load
        # dependency on the service layer, and the exception object is
        # the SAME house class the HTTP layer maps to
        # ``403 HUMAN_LANE_REQUIRED``.
        from daemon.services.prompt_version_service import HumanLaneRequiredError

        auth_method = getattr(actor, "auth_method", None)
        if auth_method != "session":
            raise HumanLaneRequiredError(
                version_id=version_id,
                actor_auth_method=auth_method or "<missing>",
            )

    def _transition(
        self, version_id: str, to_status: str, actor: Any = None
    ) -> MemoirPersonaVersion:
        """Apply one validated transition, updating the pointer in the
        SAME transaction.

        The transition is validated against the MIRRORED vocabulary
        (:data:`PERSONA_VALID_TRANSITIONS`) — the read and the write
        share one session, so a concurrent transition cannot slip
        between them.
        """
        if to_status not in PERSONA_STATUSES:
            raise ValueError(
                f"unknown persona status {to_status!r}; expected one of "
                f"{sorted(PERSONA_STATUSES)}"
            )
        with Session(self.engine) as session:
            version = session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == version_id
                )
            ).first()
            if version is None:
                raise ValueError(f"persona version {version_id} not found")
            from_status = version.status
            if (from_status, to_status) not in PERSONA_VALID_TRANSITIONS:
                raise MemoirTransitionError(version_id, from_status, to_status)

            version.status = to_status
            if to_status == "pending_approval":
                version.submitted_at = now_utc_iso()
            elif to_status in ("approved", "rejected"):
                version.approved_at = now_utc_iso()

            if to_status == "approved":
                pointer = session.exec(
                    select(MemoirPersona).where(
                        MemoirPersona.agent_id == version.agent_id
                    )
                ).first()
                now = now_utc()
                if pointer is None:
                    session.add(
                        MemoirPersona(
                            agent_id=version.agent_id,
                            current_version_id=version.id,
                            updated_at=now,
                        )
                    )
                else:
                    pointer.current_version_id = version.id
                    pointer.updated_at = now

            session.commit()
            session.refresh(version)
            return version


__all__ = [
    "MemoirPersonaRepository",
    "MemoirTransitionError",
]
