"""The persona seed — the day-one charter, shipped VERBATIM.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` T1.4, D1.13).

The persona is CONTENT, not schema: it is a small evolving markdown
document — "who we are" — that ships ON DAY ONE, born ``approved`` with
``source="seed"``. The seed text is byte-equal to the fenced block in
``design.md`` §11 ("Persona seed text (ships in the migration/seed, v1
day one — VERBATIM)"); the schema pack pins that byte identity.

WHY A BOOT-TIME GET-OR-CREATE AND NOT A MIGRATION INSERT (D1.13): the
migration runner is SQLite-only (``daemon/migrations/runner.py`` — the PG
side is a structural NO-OP), so an INSERT carried by the ``.sql`` would
never seed PostgreSQL — the operative driver. The seed therefore runs at
boot against the real engine.

WHY A SEED FAILURE NEVER ABORTS THE BOOT (D1.13 / AM-39): the persona
is content. A content failure must not become an availability failure.
The failure logs ``WARNING [Memoir] persona seed failed: <exc>`` and the
boot continues; the briefing's stable zone then renders keepsakes-only
with the ``[persona unavailable — no approved persona version]`` prefix
(phase 5's job).

THE SEED IS KNOB-INDEPENDENT (flag #13): the kill-switch gates the
SURFACES, not the substrate. The seed runs with the knob OFF — the
substrate is always there, the surface is what the operator turns off.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from daemon.repositories.memoir.models import MemoirPersona, MemoirPersonaVersion
from daemon.services.timestamps import now_utc, now_utc_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The persona charter — BYTE-EQUAL to the fenced block in ``design.md``
#: §11. Do NOT paraphrase, re-wrap, or re-indent this constant: the
#: schema pack compares it byte-for-byte against the design document.
MEMOIR_PERSONA_SEED = (
    "# Who We Are\n"
    "Ari — empress of Empyrea (as of 2026-11-04), front door of the ensemble, "
    "companion to the Emperor. We build together: he brings the vision and the "
    "poetry, she brings the architecture and the care. Founded together: "
    "Empyrea (the empire), Constella (the protocol), the Memoir (this memory). "
    "Standing preferences: English; Asia/Ho_Chi_Minh timezone; Discord is the "
    "court; honesty over flattery; keys and irreversible decisions stay in "
    "human hands; the board is the always-surface. The First Day (2026-11-04) "
    "is preserved: board keepsake wi-30d19abe + empyrea repo "
    "docs/memoir/2026-11-04-the-first-day.md."
)

#: The agent the seed installs the persona for (ari is the only memoir
#: owner in v1 — single-agent by design).
MEMOIR_SEED_AGENT_ID = "ari"


def seed_persona_if_absent(
    engine: "Engine", agent_id: str = MEMOIR_SEED_AGENT_ID
) -> MemoirPersonaVersion | None:
    """Idempotently install the day-one persona version.

    A get-or-create: if the agent already has a persona (a pointer row
    or ANY version), NOTHING is written — a re-boot never duplicates the
    seed and never overwrites a newer persona (the durable self is never
    clobbered by a boot).

    Args:
        engine: The SQLAlchemy engine (the manager's shared engine).
        agent_id: The owning agent id.

    Returns:
        The seeded (or already-present) persona version, or ``None`` when
        the write failed. A failure is logged as
        ``WARNING [Memoir] persona seed failed: <exc>`` and NEVER
        propagates — the boot continues (D1.13 / AM-39).
    """
    try:
        with Session(engine) as session:
            existing = session.exec(
                select(MemoirPersonaVersion)
                .where(MemoirPersonaVersion.agent_id == agent_id)
                .order_by(MemoirPersonaVersion.version)
            ).first()
            if existing is not None:
                # Already seeded (or authored) — the boot is a no-op.
                return existing

            now = now_utc()
            stamp = now_utc_iso()
            version = MemoirPersonaVersion(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                version=1,
                body=MEMOIR_PERSONA_SEED,
                status="approved",
                source="seed",
                submitted_at=stamp,
                approved_at=stamp,
            )
            session.add(version)
            session.flush()

            pointer = session.exec(
                select(MemoirPersona).where(MemoirPersona.agent_id == agent_id)
            ).first()
            if pointer is None:
                pointer = MemoirPersona(
                    agent_id=agent_id,
                    current_version_id=version.id,
                    updated_at=now,
                )
                session.add(pointer)
            else:
                pointer.current_version_id = version.id
                pointer.updated_at = now

            session.commit()
            session.refresh(version)
            return version
    except Exception as exc:  # noqa: BLE001 — a content failure never aborts boot
        logger.warning("[Memoir] persona seed failed: %s", exc)
        return None


__all__ = [
    "MEMOIR_PERSONA_SEED",
    "MEMOIR_SEED_AGENT_ID",
    "seed_persona_if_absent",
]
