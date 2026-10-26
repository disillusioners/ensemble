"""Unit tests — the COMMITTING backfill (Proposal B PR3, §5.2 + PR1
notes §(b)).

PR1/PR2 ran the boot backfill as a DRY-RUN preview ALWAYS; the
committing run was an operator action. PR3 makes it MODE-GATED at boot:

* ``off``     — skipped entirely;
* ``dry``     — the ship default: the dry-run preview (the PR1/PR2
                posture, byte-identical);
* ``enforce`` — the COMMITTING run (``dry_run=False``), IDEMPOTENT
                (deterministic UUID5 commission ids + epoch-0
                resolve-if-none: every re-boot is a no-op scan).

RETENTION STORY (documented on the manager boot block): advisory rows
are DURABLE by design — the historical ledger ("if dropped, the ledger
is worse than no ledger"); no pruning/TTL. Their persistence is
audit-only because they are excluded from EVERY read that consults
commissions by the ONE consistent ``advisory_backfill = False``
predicate — pinned here across all four surfaces end-to-end (routing,
census record-leg, quiet/L3 hold) from a COMMITTED backfill run.

The boot block itself is pinned by SOURCE inspection (the house
``inspect.getsource`` substring style — the manager __init__ is far too
heavy to construct for a knob check; the facade-forwarding guard
establishes this seam as legitimate).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.manager as manager_module
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ops.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import MissionCommission
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.services.mission_record_reads import (
    count_outstanding_commissions,
    list_outstanding_commissions,
    read_mission_census,
)
from daemon.services.mission_record_service import (
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)
from daemon.services.report_recipient import (
    resolve_report_recipient_with_commission,
)

ACTOR = "actor-0001"
TARGET = "target-0001"
STRUCTURAL_PARENT = "parent-1"


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr3-backfill.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_nudge_audit(engine, *, actor=ACTOR, target=TARGET) -> None:
    from daemon.repositories.instance_ops.models import InstanceOpsAudit

    with Session(engine) as session:
        session.add(
            InstanceOpsAudit(
                id="audit-pr3-1",
                created_by_instance_id=actor,
                action="instance_nudge",
                target=target,
                reason="test",
                dry_run=False,
                outcome="committed",
            )
        )
        session.commit()


def _seed_instance(engine, instance_id, **kwargs):
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent",
                agent_dir=f"/tmp/{instance_id[:8]}",
                status="running",
                **kwargs,
            )
        )
        session.commit()


class TestBootModeGating:
    def test_enforce_boot_runs_the_committing_backfill(self):
        """SOURCE PIN: the ``enforce`` arm passes ``dry_run=False`` (the
        committing run) and logs the COMMITTED shape."""
        src = inspect.getsource(manager_module.InstanceManager.__init__)
        assert "MissionRecordMode.ENFORCE" in src
        enforce_arm = src.split("MissionRecordMode.ENFORCE", 1)[1].split(
            "elif", 1
        )[0]
        assert "dry_run=False" in enforce_arm
        assert "dry_run=True" not in enforce_arm
        assert "COMMITTED run" in enforce_arm

    def test_dry_boot_keeps_the_preview(self):
        """SOURCE PIN: the ``dry`` arm keeps the PR1/PR2 preview
        (``dry_run=True``) — the ship default writes NOTHING."""
        src = inspect.getsource(manager_module.InstanceManager.__init__)
        dry_arm = src.split("MissionRecordMode.DRY", 1)[1].split(
            "# ``off``", 1
        )[0]
        assert "dry_run=True" in dry_arm
        assert "dry_run=False" not in dry_arm

    def test_off_boot_skips_entirely(self):
        """SOURCE PIN: the ``off`` arm is a no-op — no backfill call at
        all after the mode checks."""
        src = inspect.getsource(manager_module.InstanceManager.__init__)
        tail = src.split("# ``off`` — skipped entirely.", 1)[1]
        assert "backfill_advisory_nudge_commissions" not in tail


class TestTheBootModeLine:
    """F5: the operator's boot observable — ONE INFO line naming the mode.

    The enforce-promotion runbook §1.1 tells the operator to read the
    resolved mode out of the boot log. Before this line existed there
    was NO such observable: under ``off`` the boot was completely
    silent and ``dry`` vs ``off`` were INDISTINGUISHABLE from the log.

    The line is emitted ONLY when the resolved mode is NOT ``off`` —
    under ``off`` the ABSENCE of the line is the signal (the lane is
    inert, so the backfill line and the sweep's ``(mode=…)`` field are
    silent too). A bad ``ENSEMBLE_MISSION_RECORDS`` token is a THIRD,
    distinct case: the resolver raises a loud ``ValueError`` OUTSIDE
    any swallow envelope, so the daemon never starts (see the runbook
    §1.2).

    The pins are SOURCE-level (the house ``inspect.getsource``
    substring style — the manager ``__init__`` is far too heavy to
    construct for a knob check; the same seam the boot-mode-gating
    pins above already use).
    """

    _LOG_STRING = "mission_records: mode resolved (mode=%s)"

    def _init_src(self) -> str:
        return inspect.getsource(manager_module.InstanceManager.__init__)

    def test_the_boot_line_exists_with_the_exact_log_shape(self):
        """The line's text is FIXED — the runbook quotes it verbatim, so
        a reword would silently break the operator's grep."""
        src = self._init_src()
        assert self._LOG_STRING in src, (
            "the boot mode line MUST log the exact shape the "
            f"enforce-promotion runbook §1.1 quotes: {self._LOG_STRING!r}"
        )

    def test_the_boot_line_is_emitted_at_the_INFO_level(self):
        """The line MUST be emitted at ``INFO`` — the runbook §1.1's
        central inference ("daemon up + NO mission-records boot line ⇒
        ``off``") is only sound while the line is VISIBLE at the default
        INFO log level. A silent demotion to ``DEBUG`` leaves every
        sibling pin GREEN (the text, the ``off`` guard, the position and
        the runbook agreement all still hold) while quietly breaking the
        operator's ONLY mode observable: at INFO, ``dry`` and ``enforce``
        would become INDISTINGUISHABLE from ``off``."""
        src = self._init_src()
        log_at = src.index(self._LOG_STRING)
        # The logger call that carries the string is the LAST ``logger.``
        # before it.
        call = src[:log_at].rsplit("logger.", 1)[-1]
        assert call.startswith("info("), (
            "the boot mode line MUST be emitted via logger.info(...) — the "
            "runbook §1.1 reads the resolved mode off THIS line at the "
            "default INFO log level, and under 'off' the line's ABSENCE is "
            "the operator's signal (a DEBUG-level line would make 'dry' and "
            "'enforce' indistinguishable from 'off' with no other signal)"
        )

    def test_the_boot_line_is_guarded_by_the_off_check(self):
        """The line fires ONLY when the mode is NOT ``off`` — under
        ``off`` the ABSENCE of the line is the signal."""
        src = self._init_src()
        guard = src.split(self._LOG_STRING, 1)[0]
        assert "_mission_records_mode != MissionRecordMode.OFF" in guard, (
            "the boot mode line MUST be guarded by the OFF check — under "
            "'off' the line must NOT be emitted (its absence is the "
            "operator's signal)"
        )

    def test_the_boot_line_sits_at_the_resolution_site_outside_any_envelope(
        self,
    ):
        """The line is emitted at the RESOLUTION site and OUTSIDE every
        swallow envelope — so it is logged even when the backfill block
        below it fails, and a bad env token still fails the boot
        loudly."""
        src = self._init_src()
        resolve_at = src.index("resolve_mission_records_mode()")
        log_at = src.index(self._LOG_STRING)
        assert resolve_at < log_at, (
            "the boot mode line MUST come AFTER the mode resolution"
        )
        # The first ``try:`` of the boot block is the backfill envelope —
        # the line must be ABOVE it (outside it).
        try_at = src.index("try:", log_at)
        assert try_at > log_at, (
            "the boot mode line MUST sit OUTSIDE the backfill's swallow "
            "envelope — otherwise a failing backfill would swallow the "
            "operator's only mode observable"
        )

    def test_the_runbook_quotes_the_real_boot_line(self):
        """The operator-facing doc and the code MUST agree — the runbook
        §1.1 is the ONLY place the operator learns what to look for."""
        runbook = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "mission-records-enforce-promotion-runbook.md"
        )
        text = runbook.read_text(encoding="utf-8")
        assert "mission_records: mode resolved (mode=dry)" in text, (
            "the runbook §1.1 MUST quote the exact boot line the code "
            "emits (the 'dry' shape)"
        )
        assert "mission_records: mode resolved (mode=enforce)" in text, (
            "the runbook §1.1 MUST quote the exact boot line the code "
            "emits (the 'enforce' shape)"
        )
        assert "ABSENCE of the boot mode line is the signal" in text, (
            "the runbook §1.1 MUST state that under 'off' the ABSENCE of "
            "the boot line is the signal"
        )


class TestAdvisoryExclusionEverywhere:
    def test_committed_backfill_rows_are_excluded_from_every_read(
        self, engine
    ):
        """THE one-consistent-predicate pin, end-to-end from a COMMITTED
        backfill run: the minted advisory row is invisible to (a) the
        routing lookup, (b) the census record-leg, (c) the quiet/L3
        anchor-scoped count, (d) the SOURCE C LIST read — while the
        underlying row EXISTS and is durable (the retention story)."""
        _seed_instance(engine, ACTOR)
        _seed_instance(engine, TARGET)
        _seed_nudge_audit(engine)

        service = MissionRecordService(engine)
        counters = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert counters["minted_commissions"] == 1

        # The row EXISTS and is durable (the audit ledger).
        rows = list(
            Session(engine).exec(select(MissionCommission)).all()
        )
        assert len(rows) == 1
        assert rows[0].advisory_backfill is True

        # (a) ROUTING: the advisory row never routes. The audit leg is
        # NEUTRALIZED (the actor row is TERMINATED — leg 2 refuses a
        # dead nudger), so without the exclusion a naive commission
        # lookup would route the report at the DEAD actor; with it the
        # resolution falls through to the structural parent.
        with Session(engine) as session:
            actor_row = session.get(Instance, ACTOR)
            actor_row.status = "terminated"
            session.add(actor_row)
            session.commit()
        _seed_instance(engine, STRUCTURAL_PARENT)
        destination = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=TARGET,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert destination.commission is None
        assert destination.recipient_id == STRUCTURAL_PARENT

        # (b) CENSUS record-leg: the advisory row does not arm the
        # census for the actor's mission.
        assert read_mission_census(engine, ACTOR) is False

        # (c) QUIET / L3 HOLD: the anchor-scoped count reads 0 — the
        # advisory row can never hold the mission.
        assert count_outstanding_commissions(engine, ACTOR) == 0

        # (d) SOURCE C LIST READ: the judge's SOURCE C union (the
        # commission half) LISTS the mission's commission rows — the
        # advisory row must be invisible there too. The exclusion is
        # ONE consistent predicate across EVERY read that consults
        # commissions, and the LIST read is a read: a LIST read that
        # kept the advisory rows would leak the historical nudge ledger
        # straight into the judge's C membership (the count reads are
        # pinned above — the list read is the hole they leave open).
        #
        # The production seam is
        # ``MissionRecordRepository.list_commissions_for_mission``
        # (daemon/repositories/mission_record/repository.py:431) —
        # consumed by ``mission_record_reads.list_outstanding_commissions``
        # (the SOURCE C union read, daemon/services/mission_record_reads.py:307)
        # — which ``daemon/manager.py:12101`` consumes in production.
        assert list_outstanding_commissions(engine, ACTOR) == []
        # The same read at the repository seam, so the pin dies on the
        # repository's own predicate and not only on the service's use
        # of it.
        epoch = MissionRecordRepository(engine).find_open_epoch_by_anchor(
            ACTOR
        )
        assert epoch is not None, (
            "the COMMITTED backfill MUST have minted the actor's epoch-0 "
            "mission row — the SOURCE C list read is scoped to it"
        )
        assert (
            MissionRecordRepository(engine).list_commissions_for_mission(
                epoch.mission_id
            )
            == []
        )

    def test_a_live_commission_is_still_visible_next_to_advisory_rows(
        self, engine
    ):
        """The exclusion predicate is the ADVISORY FLAG, not the
        backfill provenance: a LIVE mint on the same anchor is fully
        visible to every read."""
        _seed_instance(engine, ACTOR)
        _seed_instance(engine, TARGET)
        _seed_nudge_audit(engine)
        service = MissionRecordService(engine)
        service.backfill_advisory_nudge_commissions(dry_run=False)

        # A LIVE commission (send_message lane) on the actor's epoch:
        epoch = MissionRecordRepository(engine).find_open_epoch_by_anchor(
            ACTOR
        )
        service.mint_commission(
            epoch.mission_id,
            commissioner_instance_id=ACTOR,
            worker_instance_id=TARGET,
            lane="send_message",
        )
        assert count_outstanding_commissions(engine, ACTOR) == 1
        assert read_mission_census(engine, ACTOR) is True
