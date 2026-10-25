"""SYNC repository for the mission-record tables (Proposal B PR1).

The single-writer data plane behind :mod:`daemon.services.mission_record_service`.
Mirrors the ``dependency_bus`` repository conventions: SYNC methods on a
shared ``sqlalchemy.Engine``, guarded ``UPDATE ... WHERE <state>``
transitions (the ``dependency_watchers`` PENDING→FIRED exactly-once
shape), and no enforced FKs (application code is the only writer).

PR1 contract: **mints write, NOTHING reads for decisions.** The only
readers in PR1 are the service's own resolution ladder (rung lookups
for minting) and the tests. Every gate / census / anchor / routing read
stays legacy until the PR2/PR3 flips.

Timestamps: TEXT columns carry UTC ISO digits (``now_utc_iso`` family);
there are no naive-datetime columns on these tables, so the
``now_utc_naive()`` bind discipline does not apply here.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlmodel import Session, select

from .models import (
    CommissionState,
    MissionCommission,
    MissionProvenance,
    MissionRecord,
    MissionStatus,
    _utc_now_iso,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)


class MissionRecordError(RuntimeError):
    """Raised on mission-record CONTRACT violations (mint-path validation).

    D4(ii) scope: a commission mint against a MISSING worker row raises
    here — "a commission that can never be satisfied must not be
    recorded as outstanding" (proposal-b §3). Lane-side fail-open
    wrappers catch this and log; the RAISE is the semantic contract.
    """


class MissionRecordRepository:
    """SYNC data access for ``mission_records`` + ``mission_commissions``.

    Constructed against the shared engine (the factory convention).
    Every mutating method takes an optional ``session``: when provided,
    the caller's transaction is used and NOT committed (the caller
    owns the atomicity — e.g. the revive mint inside
    ``_prepare_enqueued_message``'s ``WriteGuardSession``); when
    ``None``, a fresh session is opened and committed.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── Reads (the service's own ladder lookups; NOT decision inputs in PR1) ──

    def find_open_epoch_by_anchor(
        self,
        anchor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionRecord | None:
        """Rung 1: the anchor's OPEN epoch, if any (newest first)."""
        if session is not None:
            stmt = (
                select(MissionRecord)
                .where(MissionRecord.anchor_instance_id == anchor_instance_id)
                .where(MissionRecord.status == MissionStatus.OPEN.value)
                .order_by(MissionRecord.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            )
            return session.exec(stmt).first()
        with Session(self._engine) as own:
            stmt = (
                select(MissionRecord)
                .where(MissionRecord.anchor_instance_id == anchor_instance_id)
                .where(MissionRecord.status == MissionStatus.OPEN.value)
                .order_by(MissionRecord.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            )
            return own.exec(stmt).first()

    def find_latest_epoch_by_anchor(
        self,
        anchor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionRecord | None:
        """The anchor's most recent epoch of ANY status (parent-chain
        resolution for continuation / revive mints)."""
        stmt = (
            select(MissionRecord)
            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .order_by(MissionRecord.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        if session is not None:
            return session.exec(stmt).first()
        with Session(self._engine) as own:
            return own.exec(stmt).first()

    def find_outstanding_commission_for_worker(
        self,
        worker_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionCommission | None:
        """Rung 2: the worker's OLDEST outstanding commission row.

        Oldest-first (deterministic under adoption-delta (a)'s
        multiple-outstanding-rows rule): the commission that has been
        waiting longest is the one the work most plausibly belongs to.
        """
        stmt = (
            select(MissionCommission)
            .where(MissionCommission.worker_instance_id == worker_instance_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
            .order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]
            .limit(1)
        )
        if session is not None:
            return session.exec(stmt).first()
        with Session(self._engine) as own:
            return own.exec(stmt).first()

    def find_epoch_by_anchor_and_founding_message(
        self,
        anchor_instance_id: str,
        founding_message_id: str,
        *,
        session: Session | None = None,
    ) -> MissionRecord | None:
        """Mint idempotency probe: the ``(anchor, founding_message_id)`` key."""
        stmt = (
            select(MissionRecord)
            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionRecord.founding_message_id == founding_message_id)
        )
        if session is not None:
            return session.exec(stmt).first()
        with Session(self._engine) as own:
            return own.exec(stmt).first()

    def find_backfill_row_for_anchor(
        self,
        anchor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionRecord | None:
        """Backfill idempotency probe: the anchor's synthetic epoch-0 row."""
        stmt = (
            select(MissionRecord)
            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionRecord.provenance == MissionProvenance.BACKFILL.value)
            .order_by(MissionRecord.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        if session is not None:
            return session.exec(stmt).first()
        with Session(self._engine) as own:
            return own.exec(stmt).first()

    def get_mission(
        self,
        mission_id: str,
        *,
        session: Session | None = None,
    ) -> MissionRecord | None:
        if session is not None:
            return session.get(MissionRecord, mission_id)
        with Session(self._engine) as own:
            return own.get(MissionRecord, mission_id)

    def get_commission(
        self,
        commission_id: str,
        *,
        session: Session | None = None,
    ) -> MissionCommission | None:
        if session is not None:
            return session.get(MissionCommission, commission_id)
        with Session(self._engine) as own:
            return own.get(MissionCommission, commission_id)

    def count_epochs_for_anchor(
        self,
        anchor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> int:
        """Epoch counter for ``epoch_seq`` (``1 + count``)."""
        stmt = (
            select(MissionRecord)
            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
        )
        if session is not None:
            return len(list(session.exec(stmt)))
        with Session(self._engine) as own:
            return len(list(own.exec(stmt)))

    # ── PR2 read-side: the mission-scoped commission reads ──
    #
    # Proposal B PR2 flips the READ sites onto the record (the census,
    # the SOURCE U anchor, the 5th R2 input, the L3 mission leg, the
    # judge's SOURCE C membership). These are the three primitives those
    # sites need — a census count, an outstanding count, and a row list.
    #
    # ADVISORY ROWS ARE EXCLUDED BY DEFAULT: adoption-delta (b) backfill
    # rows are historical noise (a nudge whose target was still
    # non-terminal at boot). Counting them would (a) arm the census for
    # missions that never delegated in this epoch and (b) hold missions
    # open forever on rows nothing will ever satisfy. The exclusion
    # mirrors the routing-authority rule ("advisory rows are EXCLUDED
    # from routing authority FOREVER" — the "if dropped, the ledger is
    # worse than no ledger" rule).

    def count_commissions_for_mission(
        self,
        mission_id: str,
        *,
        states: tuple[str, ...] | None = None,
        include_advisory: bool = False,
        session: Session | None = None,
    ) -> int:
        """Count the commission rows on one mission (the census read).

        Args:
            mission_id: The mission whose rows are counted.
            states: Optional state filter (e.g. ``("outstanding",)``).
                ``None`` = every state (the census's "has commission
                rows" shape — a mission that delegated stays delegated
                for the whole epoch).
            include_advisory: Include adoption-delta (b) backfill rows.
                Default ``False`` (see the class-level note).

        Returns:
            The row count (0 when the mission has no commission rows).
        """
        stmt = select(MissionCommission).where(
            MissionCommission.mission_id == mission_id
        )
        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        if session is not None:
            return len(list(session.exec(stmt)))
        with Session(self._engine) as own:
            return len(list(own.exec(stmt)))

    def count_outstanding_commissions_for_mission(
        self,
        mission_id: str,
        *,
        include_advisory: bool = False,
        session: Session | None = None,
    ) -> int:
        """Count the OUTSTANDING commission rows on one mission.

        The 5th R2 input's mission-scoped read (``outstanding_commissions``)
        and the L3 root gate's mission leg both consume this.
        """
        return self.count_commissions_for_mission(
            mission_id,
            states=(CommissionState.OUTSTANDING.value,),
            include_advisory=include_advisory,
            session=session,
        )

    def list_commissions_for_mission(
        self,
        mission_id: str,
        *,
        states: tuple[str, ...] | None = None,
        include_advisory: bool = False,
        session: Session | None = None,
    ) -> list[MissionCommission]:
        """List the commission rows on one mission (the SOURCE C union).

        The judge's SOURCE C membership is ``structural tree ∪ commission
        membership`` (proposal-b §3 NEW(v)) — this is the commission half
        of that union: the OUT-OF-TREE commissioned workers the tree walk
        can never see.
        """
        stmt = select(MissionCommission).where(
            MissionCommission.mission_id == mission_id
        )
        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]
        if session is not None:
            return list(session.exec(stmt))
        with Session(self._engine) as own:
            return list(own.exec(stmt))

    def list_committed_nudge_audits(
        self,
        action: str,
        outcome: str,
        dry_run: bool,
        *,
        session: Session | None = None,
    ) -> list[tuple[str, str, str, str]]:
        """Adoption-delta (b) source scan: committed non-dry audit rows.

        Returns raw ``(id, created_by_instance_id, target, created_at)``
        tuples from ``instance_ops_audit``. Lives here (not in the
        instance_ops repository) because the backfill is a mission-record
        concern; the audit table is read-ONLY from this lane (the
        4-reason verdict against writing commission state into the audit
        table — comparison.md §3-C — is respected).
        """
        from ..instance_ops.models import InstanceOpsAudit

        stmt = (
            select(
                InstanceOpsAudit.id,
                InstanceOpsAudit.created_by_instance_id,
                InstanceOpsAudit.target,
                InstanceOpsAudit.created_at,
            )
            .where(InstanceOpsAudit.action == action)
            .where(InstanceOpsAudit.outcome == outcome)
            .where(InstanceOpsAudit.dry_run == dry_run)
        )
        if session is not None:
            return list(session.exec(stmt))
        with Session(self._engine) as own:
            return list(own.exec(stmt))

    # ── Writes ──

    def insert_mission(
        self,
        record: MissionRecord,
        *,
        session: Session | None = None,
    ) -> MissionRecord:
        """INSERT one epoch row. The partial unique index is the
        concurrent-mint race backstop; the service re-resolves on
        ``IntegrityError``."""
        if session is not None:
            session.add(record)
            return record
        with Session(self._engine) as own:
            own.add(record)
            own.commit()
            own.refresh(record)
            return record

    def insert_commission(
        self,
        commission: MissionCommission,
        *,
        session: Session | None = None,
    ) -> MissionCommission:
        if session is not None:
            session.add(commission)
            return commission
        with Session(self._engine) as own:
            own.add(commission)
            own.commit()
            own.refresh(commission)
            return commission

    def transition_epoch_status(
        self,
        mission_id: str,
        to_status: str,
        *,
        from_statuses: tuple[str, ...] = (MissionStatus.OPEN.value,),
        settled_at: str | None = None,
        session: Session | None = None,
    ) -> bool:
        """Guarded epoch transition (the ``dependency_watchers`` shape).

        ``UPDATE mission_records SET status=:to WHERE mission_id=:id AND
        status IN :from`` — returns True iff exactly one row moved.
        Exactly-once: a second concurrent transition of the same kind is
        a no-op (rowcount 0).
        """
        values: dict[str, object] = {"status": to_status}
        if settled_at is not None:
            values["settled_at"] = settled_at
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.status.in_(from_statuses))
            .values(**values)
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def transition_commission_state(
        self,
        commission_id: str,
        to_state: str,
        *,
        abandon_reason: str | None = None,
        session: Session | None = None,
    ) -> bool:
        """Guarded commission transition — ONLY from ``outstanding``.

        ``UPDATE mission_commissions SET state=:to WHERE
        commission_id=:id AND state='outstanding'`` — the
        ``dependency_watchers`` PENDING→FIRED exactly-once contract: a
        satisfied commission can never be abandoned (and vice versa).
        """
        values: dict[str, object] = {"state": to_state}
        if to_state == CommissionState.SATISFIED.value:
            values["satisfied_at"] = _utc_now_iso()
        if abandon_reason is not None:
            values["abandon_reason"] = abandon_reason
        stmt = (
            update(MissionCommission)
            .where(MissionCommission.commission_id == commission_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
            .values(**values)
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def attach_founding_message(
        self,
        mission_id: str,
        founding_message_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Guarded founding-message attach / amendment (records only).

        ``UPDATE ... WHERE mission_id=:id AND founding_message_id IS
        NULL AND status='open'`` — the mint-then-attach order (the
        enqueue's message id is only known after the enqueue returns) and
        the re-founding amendment seam share this guarded write: the
        FIRST attach wins; a later amendment REPLACES it only via
        :meth:`amend_founding_message` (an explicit, auditable act).

        F2 (Proposal B PR2 carry) — the ``status='open'`` GUARD: only an
        OPEN epoch's anchor may be attached. A closed epoch (settled /
        superseded) is immutable — attaching to it would rewrite history
        on a row the settle machinery already closed. Mirrors the
        ``dependency_watchers`` PENDING→FIRED guarded-transition shape.
        """
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.founding_message_id.is_(None))  # type: ignore[union-attr]
            .where(MissionRecord.status == MissionStatus.OPEN.value)
            .values(founding_message_id=founding_message_id)
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def amend_founding_message(
        self,
        mission_id: str,
        founding_message_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Re-founding amendment (mid-mission user correction).

        Proposal B PR2: the amendment fires AT DELIVERY (the message tap
        site) — the delivered correction becomes the mission's anchor,
        so the delegation window re-bounds on the correction rather than
        on the stale founding message. The amendment row is the durable
        fact the gate reads (SOURCE U anchor lookup).

        F2 (Proposal B PR2 carry) — the ``status='open'`` GUARD: the
        amendment touches ONLY an OPEN epoch. A closed epoch's anchor is
        immutable (the settle machinery closed it). Same guarded-UPDATE
        shape as the ``dependency_watchers`` PENDING→FIRED transition.
        """
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.status == MissionStatus.OPEN.value)
            .values(founding_message_id=founding_message_id)
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def attach_work_id(
        self,
        mission_id: str,
        work_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Guarded job-linkage attach (mint-then-attach order for
        ``job_continue`` — the new ``work_id`` is only known after the
        enqueue returns)."""
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.work_id.is_(None))  # type: ignore[union-attr]
            .values(work_id=work_id)
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def record_attestation(
        self,
        mission_id: str,
        *,
        attest_method: str,
        attestation_bypassed: bool = False,
        session: Session | None = None,
    ) -> bool:
        """Write the attestation block on the OPEN epoch (record-only).

        PR1 lands the FIELDS + this seam; the Term-1 blocking POLICY
        stays default-OFF (the ruling). The write is guarded on the
        epoch being OPEN — attestation events on closed epochs are
        contract violations (return False, logged by the caller).
        """
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.status == MissionStatus.OPEN.value)
            .values(
                attested_at=_utc_now_iso(),
                attest_method=attest_method,
                attestation_bypassed=attestation_bypassed,
            )
        )
        if session is not None:
            result = session.exec(stmt)
            return (result.rowcount or 0) == 1
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return (result.rowcount or 0) == 1

    def supersede_open_epochs_for_anchor(
        self,
        anchor_instance_id: str,
        *,
        except_mission_id: str | None = None,
        session: Session | None = None,
    ) -> int:
        """Record-side ``open → superseded`` sweep (job_continue lane).

        "Prior open epoch → superseded record-side only" — a continuation
        epoch supersedes the anchor's prior OPEN epoch IN THE RECORDS;
        no instance status, no gate channel, no ledger is touched (the
        §2.4 ledger-clear POLICY is PR2+; PR1 is record-side only).

        NOTE (honest scope): in PR1 the job_continue lane supersedes the
        prior open epoch only when the record read sees one; the
        attestation gate still reads its legacy inputs and keeps
        counting the same way it does today.
        """
        stmt = (
            update(MissionRecord)
            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionRecord.status == MissionStatus.OPEN.value)
        )
        if except_mission_id:
            stmt = stmt.where(MissionRecord.mission_id != except_mission_id)
        stmt = stmt.values(status=MissionStatus.SUPERSEDED.value)
        if session is not None:
            result = session.exec(stmt)
            return result.rowcount or 0
        with Session(self._engine) as own:
            result = own.exec(stmt)
            own.commit()
            return result.rowcount or 0

    @staticmethod
    def deterministic_commission_id(
        mission_id: str,
        worker_instance_id: str,
        lane: str,
        audit_row_id: str,
    ) -> str:
        """UUID5 identity for BACKFILL commission rows.

        The backfill's idempotency key: re-running the backfill
        re-derives the SAME id for the same (mission, worker, lane,
        audit-row) quadruple, and the backfill PRE-CHECKS that id
        (``get_commission``) before inserting. There is NO
        ``ON CONFLICT`` clause anywhere in this layer — the pre-check
        is what makes a re-run a no-op (a PK conflict inside the narrow
        pre-check → insert window would RAISE, not no-op). Live mints
        use random UUID4s and are unaffected.
        """
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"mission-commission-backfill:{mission_id}:{worker_instance_id}:{lane}:{audit_row_id}",
            )
        )
