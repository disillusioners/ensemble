"""Unit tests — MissionRecordService (Proposal B PR1).

File-backed SQLite per the repo recipe (``test_phase1_ledger_pins.py``:
file-backed engine, NullPool, WAL/busy_timeout/foreign_keys PRAGMAs,
``create_all``).

Covers:
* the 3-rung resolution ladder (open-epoch → outstanding-commission →
  mint) and the exactly-one-mission invariant;
* mint idempotency keyed (anchor, founding_message_id) — including the
  partial-unique race backstop semantics;
* guarded transitions (epoch status; commission state ONLY from
  ``outstanding``);
* the D4(ii) mint-path contract: a commission whose worker row is
  missing RAISES (and the fail-open wrapper degrades loudly);
* the delivery/resume mint-if-no-open-epoch shape (boot / cascade /
  revive) — rung 2 deliberately not consulted;
* the continuation mint (job_continue): force-NEW epoch, prior open
  epoch → superseded RECORD-SIDE ONLY, parent chain;
* the mode ladder (off/dry/enforce, unknown → LOUD ValueError,
  resolve-once + cached — the house switch pattern);
* adoption delta (a): second commission of the same worker = SECOND
  outstanding row, rung 2 resolves oldest-first;
* adoption delta (b): the advisory backfill rules (committed non-dry
  audits only; terminal targets skipped; idempotent re-run;
  advisory_backfill=True; epoch-0 honest-NULL coverage).

ZERO-BEHAVIOR-CHANGE pins: nothing here touches instance status
columns; the mission tables are the ONLY tables written.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ops.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.mission_record.models import (
    CommissionState,
    MissionCommission,
    MissionRecord,
    MissionStatus,
)
from daemon.repositories.mission_record.repository import MissionRecordError
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordMode,
    MissionRecordService,
    MissionResolution,
    _reset_mission_records_mode_for_tests,
    attach_founding_message_fail_open,
    mint_commission_fail_open,
    mint_continuation_epoch_fail_open,
    resolve_mission_records_mode,
    resolve_or_mint_fail_open,
)


# ---------------------------------------------------------------------------
# Fixtures (the repo SQLite recipe)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path):
    """File-backed SQLite engine (NullPool + PRAGMAs, create_all)."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mission-records.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _make_instance(
    engine, instance_id: str, status: str = "running"
) -> None:
    with Session(engine) as session:
        existing = session.get(Instance, instance_id)
        if existing is not None:
            if existing.status != status:
                existing.status = status  # get-or-create-UPDATE
                session.commit()
            return
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir=f"agents/worker/{instance_id[:8]}",
                status=status,
                project_id=None,
            )
        )
        session.commit()


@pytest.fixture
def service(engine):
    return MissionRecordService(engine)


@pytest.fixture
def two_instances(engine):
    _make_instance(engine, "11111111-1111-4111-8111-111111111111")
    _make_instance(engine, "22222222-2222-4222-8222-222222222222")
    return (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )


# ---------------------------------------------------------------------------
# The resolution ladder
# ---------------------------------------------------------------------------


class TestResolutionLadder:
    async def test_rung3_mints_new_epoch(self, service, engine, two_instances):
        actor, _ = two_instances
        res = service.resolve_or_mint(actor, "user_api", founding_source="user_api")
        assert res.rung == 3 and res.created is True
        row = service._repo.get_mission(res.mission_id)
        assert row.anchor_instance_id == actor
        assert row.founding_source == "user_api"
        assert row.status == MissionStatus.OPEN.value
        assert row.epoch_seq == 1
        assert row.provenance == "live"

    async def test_rung1_reuses_open_epoch(self, service, engine, two_instances):
        actor, _ = two_instances
        first = service.resolve_or_mint(actor, "user_api")
        second = service.resolve_or_mint(actor, "user_api")
        assert second.rung == 1 and second.created is False
        assert second.mission_id == first.mission_id
        # exactly one row
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(MissionRecord).where(
                        MissionRecord.anchor_instance_id == actor
                    )
                )
            )
        assert len(rows) == 1

    async def test_rung2_commissioned_worker_resolves_commissioners_mission(
        self, service, engine, two_instances
    ):
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        # The worker has NO open epoch of its own → rung 2 fires.
        res = service.resolve_or_mint(worker, "user_api")
        assert res.rung == 2 and res.created is False
        assert res.mission_id == mission.mission_id

    async def test_rung1_beats_rung2(self, service, engine, two_instances):
        """A worker with BOTH its own OPEN epoch and an outstanding
        commission resolves to ITS OWN epoch (rung 1 wins)."""
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        # Commissioned, no open epoch → rung 2 fires (the ladder
        # correctly attributes the act to the commissioner's mission).
        own = service.resolve_or_mint(worker, "chat")
        assert own.rung == 2
        assert own.mission_id == mission.mission_id
        # Give the worker its OWN open epoch (the delivery/resume mint
        # skips rung 2 by design) → rung 1 now beats rung 2.
        service.mint_boot_epoch_if_none(worker)
        again = service.resolve_or_mint(worker, "chat")
        assert again.rung == 1
        assert again.mission_id != mission.mission_id

    async def test_mint_idempotent_on_founding_message_key(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        a = service.resolve_or_mint(
            actor, "user_api", founding_message_id="msg-1"
        )
        b = service.resolve_or_mint(
            actor, "user_api", founding_message_id="msg-1"
        )
        assert a.mission_id == b.mission_id
        with Session(engine) as session:
            rows = list(session.exec(select(MissionRecord)))
        assert len(rows) == 1

    async def test_different_founding_messages_mint_distinct_epochs_when_open_gone(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        a = service.resolve_or_mint(actor, "user_api", founding_message_id="m1")
        # close the open epoch (settle) → the ladder's rung 1 misses
        assert service.settle(a.mission_id) is True
        b = service.resolve_or_mint(actor, "user_api", founding_message_id="m2")
        assert b.mission_id != a.mission_id
        row = service._repo.get_mission(b.mission_id)
        assert row.epoch_seq == 2  # monotonic per anchor


# ---------------------------------------------------------------------------
# Guarded transitions
# ---------------------------------------------------------------------------


class TestGuardedTransitions:
    async def test_settle_only_from_open(self, service, engine, two_instances):
        actor, _ = two_instances
        res = service.resolve_or_mint(actor, "user_api")
        assert service.settle(res.mission_id) is True
        # second settle is a no-op (rowcount 0)
        assert service.settle(res.mission_id) is False

    async def test_commission_satisfied_only_from_outstanding(
        self, service, engine, two_instances
    ):
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        commission = service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        assert service.satisfy_commission(commission.commission_id) is True
        # a satisfied commission can never be abandoned (the
        # dependency_watchers exactly-once contract)
        assert service.abandon_commission(
            commission.commission_id, reason="late sweep"
        ) is False

    async def test_abandon_records_reason(self, service, engine, two_instances):
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        commission = service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="instance_nudge",
        )
        assert (
            service.abandon_commission(
                commission.commission_id, reason="worker terminal, no report"
            )
            is True
        )
        row = service._repo.get_commission(commission.commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "worker terminal, no report"

    async def test_attestation_record_only_and_guarded_on_open(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        res = service.resolve_or_mint(actor, "user_api")
        assert (
            service.record_attestation(
                res.mission_id, attest_method="leader_attest"
            )
            is True
        )
        row = service._repo.get_mission(res.mission_id)
        assert row.attested_at is not None
        assert row.attest_method == "leader_attest"
        assert row.attestation_bypassed is False
        # closed epoch refuses
        service.settle(res.mission_id)
        assert (
            service.record_attestation(
                res.mission_id, attest_method="leader_attest"
            )
            is False
        )


# ---------------------------------------------------------------------------
# D4(ii) — the mint-path validation contract
# ---------------------------------------------------------------------------


class TestMintPathValidation:
    async def test_commission_against_missing_worker_row_raises(
        self, service, engine, two_instances
    ):
        commissioner, _ = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        with pytest.raises(MissionRecordError, match="no instance row"):
            service.mint_commission(
                mission.mission_id,
                commissioner_instance_id=commissioner,
                worker_instance_id="deadbeef-does-not-exist",
                lane="instance_nudge",
            )
        # nothing was recorded
        with Session(engine) as session:
            rows = list(session.exec(select(MissionCommission)))
        assert rows == []

    async def test_fail_open_wrapper_degrades_loudly_not_silently(
        self, service, engine, two_instances, caplog
    ):
        commissioner, _ = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        out = mint_commission_fail_open(
            service,
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id="deadbeef-does-not-exist",
            lane="instance_nudge",
        )
        assert out is None
        assert any("mint_commission failed" in r.message for r in caplog.records)

    async def test_epoch_mint_against_missing_anchor_raises(
        self, service, engine
    ):
        with pytest.raises(MissionRecordError, match="anchor"):
            service.resolve_or_mint("no-such-instance", "user_api")


# ---------------------------------------------------------------------------
# Delivery / resume lanes — mint-if-no-open-epoch
# ---------------------------------------------------------------------------


class TestDeliveryResumeLanes:
    async def test_boot_mint_if_none_honest_null(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        res = service.mint_boot_epoch_if_none(actor)
        assert res.created is True
        row = service._repo.get_mission(res.mission_id)
        assert row.founding_source == "boot"
        assert row.founding_message_id is None  # the honest null

    async def test_boot_no_mint_when_open_epoch_exists(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        live = service.resolve_or_mint(actor, "user_api")
        res = service.mint_boot_epoch_if_none(actor)
        assert res.created is False
        assert res.mission_id == live.mission_id

    async def test_cascade_resume_stranded_mints_revive(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        res = service.mint_cascade_resume_epoch_if_stranded(actor)
        assert res.created is True
        row = service._repo.get_mission(res.mission_id)
        assert row.founding_source == "revive"

    async def test_revive_rung2_not_consulted(
        self, service, engine, two_instances
    ):
        """The delivery/resume shape deliberately skips rung 2: a resumed
        worker's epoch boundary is its OWN, not its commissioner's."""
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        res = service.mint_revive_epoch(worker)
        assert res.created is True
        row = service._repo.get_mission(res.mission_id)
        assert row.anchor_instance_id == worker
        assert row.founding_source == "revive"

    async def test_revive_parent_chain(self, service, engine, two_instances):
        actor, _ = two_instances
        first = service.resolve_or_mint(actor, "user_api")
        service.settle(first.mission_id)
        second = service.mint_revive_epoch(actor)
        row = service._repo.get_mission(second.mission_id)
        assert row.parent_mission_id == first.mission_id

    async def test_revive_with_commissioner_and_founding_message(
        self, service, engine, two_instances
    ):
        nudger, target = two_instances
        res = service.mint_revive_epoch(
            target,
            commissioner_instance_id=nudger,
            founding_message_id="msg-revive-1",
        )
        row = service._repo.get_mission(res.mission_id)
        assert row.commissioner_instance_id == nudger
        assert row.founding_message_id == "msg-revive-1"


# ---------------------------------------------------------------------------
# Continuation mint (job_continue)
# ---------------------------------------------------------------------------


class TestContinuationEpoch:
    async def test_force_new_epoch_supersedes_prior_record_side(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        first = service.resolve_or_mint(actor, "user_api")
        res = service.mint_continuation_epoch(actor, work_id="job-new-1")
        assert res.created is True
        assert res.mission_id != first.mission_id

        new_row = service._repo.get_mission(res.mission_id)
        assert new_row.founding_source == "job_continue"
        assert new_row.work_id == "job-new-1"
        assert new_row.parent_mission_id == first.mission_id

        prior = service._repo.get_mission(first.mission_id)
        assert prior.status == MissionStatus.SUPERSEDED.value

    async def test_supersede_is_record_side_only(
        self, service, engine, two_instances
    ):
        """ZERO-BEHAVIOR-CHANGE pin: the supersede writes ONLY
        mission_records.status — the instance row is untouched."""
        actor, _ = two_instances
        service.resolve_or_mint(actor, "user_api")
        service.mint_continuation_epoch(actor, work_id="job-2")
        with Session(engine) as session:
            inst = session.get(Instance, actor)
            assert inst.status == "running"  # exactly as the fixture made it

    async def test_fail_open_wrapper(self, service, engine, two_instances, caplog):
        actor, _ = two_instances
        mid = mint_continuation_epoch_fail_open(service, actor)
        assert mid
        # missing anchor → wrapper degrades
        assert mint_continuation_epoch_fail_open(service, "nope-nope") is None
        assert any(
            "mint_continuation_epoch failed" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Mode ladder
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    """The mode ladder is CACHED (F5 — the house switch pattern).

    Every test starts from a clean cache so the env it sets is the env
    that actually gets resolved, and leaves the cache clean for the
    next test.
    """
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


class TestModeLadder:
    def test_default_is_dry(self, monkeypatch):
        monkeypatch.delenv(MISSION_RECORDS_MODE_ENV, raising=False)
        assert resolve_mission_records_mode() == MissionRecordMode.DRY

    def test_unknown_value_raises_loud_value_error(self, monkeypatch):
        """F5 — an unrecognized token fails LOUD (house switch pattern).

        The daemon's boot resolution site turns this into a failed
        start; the lane wrappers never propagate it (see the
        fail-open test below).
        """
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "shipped-yesterday")
        with pytest.raises(ValueError) as excinfo:
            resolve_mission_records_mode()
        assert MISSION_RECORDS_MODE_ENV in str(excinfo.value)

    def test_explicit_off_and_enforce(self, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        assert resolve_mission_records_mode() == "off"
        _reset_mission_records_mode_for_tests()
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        assert resolve_mission_records_mode() == "enforce"

    def test_mode_is_resolved_once_and_cached(self, monkeypatch):
        """F5 — restart-read: a mid-run env mutation cannot flip a
        resolved knob (the resolver caches the value)."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        assert resolve_mission_records_mode() == MissionRecordMode.DRY
        # mid-run mutation — IGNORED (the knob is restart-read)
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        assert resolve_mission_records_mode() == MissionRecordMode.DRY

    def test_lane_wrapper_never_raises_on_unrecognized_token(
        self, service, engine, two_instances, monkeypatch
    ):
        """F5 — the fail-open producer posture survives the loud
        resolver: a lane never breaks on a records-side problem."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "shipped-yesterday")
        actor, _ = two_instances
        assert resolve_or_mint_fail_open(service, actor, "user_api") is None

    def test_off_means_no_mints(self, service, engine, two_instances, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        actor, _ = two_instances
        assert resolve_or_mint_fail_open(service, actor, "user_api") is None
        with Session(engine) as session:
            rows = list(session.exec(select(MissionRecord)))
        assert rows == []

    def test_dry_mints(self, service, engine, two_instances, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        actor, _ = two_instances
        assert resolve_or_mint_fail_open(service, actor, "user_api")
        with Session(engine) as session:
            rows = list(session.exec(select(MissionRecord)))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# H1 drift pin — wrapper must accept AND forward ``founding_message_id``
# ---------------------------------------------------------------------------


def _stub_service_with_seam_spy(
    mission_id: str = "m-h1-pin",
) -> tuple[MagicMock, MagicMock]:
    """A stub ``MissionRecordService`` whose ``resolve_or_mint`` seam is
    spied with a plain ``MagicMock`` returning a real ``MissionResolution``
    — the WRAPPER is invoked for real end-to-end (binding → mode gate →
    branch → forward → log → return); only the service seam is a stub.
    """
    service = MagicMock(name="MissionRecordService")
    seam = MagicMock(
        return_value=MissionResolution(
            mission_id=mission_id, rung=3, created=True
        )
    )
    service.resolve_or_mint = seam
    return service, seam


class TestResolveOrMintFailOpenSignatureDriftPin:
    """H1 (2026-10-25): the chat inject sub-lane
    (``daemon/sources/registry.py``) passes ``founding_message_id=echo_id``
    to :func:`resolve_or_mint_fail_open`; the wrapper's signature lacked
    the kwarg, so the binding TypeError fired BEFORE the wrapper's
    internal try — the fail-open catch never engaged, the sole
    ``_handle_message`` handler swallowed it, and BOTH the RAM-FIFO
    ``set_injection`` AND the durable-enqueue fallback were skipped (the
    chat user message was dropped). Fires under off/dry/enforce alike —
    no config escape.

    This pin follows the house style of
    ``tests/unit/test_manager_enqueue_message_work_id_required.py``:
    REAL calls against the wrapper with the service SEAM spied.
    Deliberately NOT a lambda/``*a, **kw`` spy wrapped around the
    wrapper — that shape binds any signature and masks exactly this
    defect class (it is how the suite stayed green through the H1
    regression).
    """

    def test_signature_has_keyword_only_founding_message_id(self):
        """The parameter exists and sits after the ``*`` marker — a
        signature drift (rename/removal) fails here without any call."""
        params = inspect.signature(resolve_or_mint_fail_open).parameters
        assert "founding_message_id" in params
        assert (
            params["founding_message_id"].kind
            is inspect.Parameter.KEYWORD_ONLY
        )
        assert params["founding_message_id"].default is None

    def test_positional_founding_message_id_raises(self):
        """``founding_message_id`` has no positional slot — passing it
        positionally must raise ``TypeError`` (the kwarg is
        keyword-only, matching the service signature it forwards to)."""
        service, seam = _stub_service_with_seam_spy()

        with pytest.raises(TypeError):
            resolve_or_mint_fail_open(
                service,
                "actor-1",
                "chat",
                "chat",  # 4th positional — no slot for it
                "echo-1",
            )

        seam.assert_not_called()

    def test_plain_branch_forwards_founding_message_id(self, monkeypatch):
        """Real invocation, no session: the chat lane's kwarg binds (no
        TypeError — the pre-fix H1 behavior) and is forwarded verbatim
        into ``service.resolve_or_mint``; the wrapper returns the
        resolution's mission id."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        service, seam = _stub_service_with_seam_spy()

        mission_id = resolve_or_mint_fail_open(
            service,
            "actor-1",
            "chat",
            founding_source="chat",
            founding_message_id="echo-1",
        )

        seam.assert_called_once()
        forwarded = seam.call_args.kwargs
        assert forwarded["founding_message_id"] == "echo-1"
        assert forwarded["founding_source"] == "chat"
        assert mission_id == "m-h1-pin"

    def test_savepoint_branch_forwards_founding_message_id(
        self, monkeypatch
    ):
        """Real invocation WITH a session: the ``begin_nested`` branch
        forwards the kwarg too — both internal branches must carry it
        (the session branch is the send_message delegation lane's
        same-transaction contract; a one-branch fix would re-open H1
        through that lane the moment a caller there knows the id)."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        service, seam = _stub_service_with_seam_spy()
        savepoint = MagicMock(name="savepoint")
        savepoint.__enter__ = MagicMock(return_value=savepoint)
        savepoint.__exit__ = MagicMock(return_value=False)
        session = MagicMock(name="Session")
        session.begin_nested = MagicMock(return_value=savepoint)

        mission_id = resolve_or_mint_fail_open(
            service,
            "actor-1",
            "send_message",
            founding_message_id="echo-2",
            session=session,
        )

        session.begin_nested.assert_called_once()
        seam.assert_called_once()
        forwarded = seam.call_args.kwargs
        assert forwarded["founding_message_id"] == "echo-2"
        assert forwarded["session"] is session
        assert mission_id == "m-h1-pin"

    def test_omitted_kwarg_forwards_none_default(self, monkeypatch):
        """Omitting the kwarg forwards ``founding_message_id=None`` —
        the established lanes (user_api router, chat durable sub-lane,
        instance_nudge, scheduler) mint without a founding id (the
        mint-then-attach order) and must be byte-identical."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        service, seam = _stub_service_with_seam_spy()

        resolve_or_mint_fail_open(
            service, "actor-1", "user_api", founding_source="user_api"
        )

        seam.assert_called_once()
        assert seam.call_args.kwargs["founding_message_id"] is None


# ---------------------------------------------------------------------------
# Adoption delta (a) — multiple outstanding commissions
# ---------------------------------------------------------------------------


class TestAdoptionDeltaA:
    async def test_second_commission_mints_second_outstanding_row(
        self, service, engine, two_instances
    ):
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        c1 = service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="instance_nudge",
        )
        c2 = service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        assert c1.commission_id != c2.commission_id
        assert c1.state == CommissionState.OUTSTANDING.value
        assert c2.state == CommissionState.OUTSTANDING.value
        # no unique constraint blocks the pair — the lifecycle is the
        # discriminator
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(MissionCommission).where(
                        MissionCommission.worker_instance_id == worker
                    )
                )
            )
        assert len(rows) == 2

    async def test_rung2_resolves_oldest_outstanding(
        self, service, engine, two_instances
    ):
        commissioner, worker = two_instances
        mission = service.resolve_or_mint(commissioner, "user_api")
        c1 = service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="instance_nudge",
        )
        service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=commissioner,
            worker_instance_id=worker,
            lane="send_message",
        )
        res = service.resolve_or_mint(worker, "user_api")
        assert res.mission_id == mission.mission_id  # both rows share it
        # the OLDEST row is the rung-2 hit — satisfy it, the next resolve
        # still resolves (second row) to the SAME mission here; the
        # oldest-first ordering is pinned by satisfy-then-resolve with a
        # SECOND mission from a different commissioner
        service.satisfy_commission(c1.commission_id)
        other = "33333333-3333-4333-8333-333333333333"
        _make_instance(engine, other)
        mission2 = service.resolve_or_mint(other, "chat")
        service.mint_commission(
            mission2.mission_id,
            commissioner_instance_id=other,
            worker_instance_id=worker,
            lane="send_message",
        )
        res2 = service.resolve_or_mint(worker, "chat")
        assert res2.mission_id == mission.mission_id  # remaining older row


# ---------------------------------------------------------------------------
# Adoption delta (b) — the advisory backfill
# ---------------------------------------------------------------------------


def _seed_nudge_audit(
    engine, audit_id: str, actor: str, target: str, *, dry_run: bool = False
) -> None:
    with Session(engine) as session:
        session.add(
            InstanceOpsAudit(
                id=audit_id,
                created_by_instance_id=actor,
                action="instance_nudge",
                target=target,
                reason="test",
                dry_run=dry_run,
                outcome="committed" if not dry_run else "previewed",
            )
        )
        session.commit()


class TestAdoptionDeltaBBackfill:
    async def test_committed_nudge_to_non_terminal_target_backfills(
        self, service, engine, two_instances
    ):
        actor, target = two_instances  # both rows exist (the fixture)
        _make_instance(engine, target, status="running")  # still alive
        _seed_nudge_audit(engine, "audit-1", actor, target)

        plan = service.backfill_advisory_nudge_commissions(dry_run=True)
        assert plan["scanned"] == 1
        assert plan["minted_epochs"] == 1  # projected, NOT written
        assert plan["minted_commissions"] == 1
        # dry-run wrote NOTHING
        with Session(engine) as session:
            assert list(session.exec(select(MissionRecord))) == []
            assert list(session.exec(select(MissionCommission))) == []

        commit = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert commit["minted_epochs"] == 1
        assert commit["minted_commissions"] == 1

        with Session(engine) as session:
            epochs = list(session.exec(select(MissionRecord)))
            comms = list(session.exec(select(MissionCommission)))
        assert len(epochs) == 1
        epoch = epochs[0]
        assert epoch.founding_source == "backfill"
        assert epoch.provenance == "backfill"
        assert epoch.founding_message_id is None  # the honest null
        assert epoch.anchor_instance_id == actor
        assert len(comms) == 1
        comm = comms[0]
        assert comm.lane == "instance_nudge"
        assert comm.advisory_backfill is True
        assert comm.commissioner_instance_id == actor
        assert comm.worker_instance_id == target
        # deterministic id → idempotent re-run
        again = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert again["minted_epochs"] == 0
        assert again["minted_commissions"] == 0  # conflict = no-op (idempotent)
        with Session(engine) as session:
            assert len(list(session.exec(select(MissionCommission)))) == 1

    async def test_terminal_target_is_the_accepted_gap(
        self, service, engine, two_instances
    ):
        actor, target = two_instances  # both rows exist (the fixture)
        _make_instance(engine, target, status="completed")
        _seed_nudge_audit(engine, "audit-2", actor, target)
        result = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert result["skipped_terminal"] == 1
        assert result["minted_commissions"] == 0
        with Session(engine) as session:
            assert list(session.exec(select(MissionCommission))) == []

    async def test_dry_run_preview_rows_never_backfill(
        self, service, engine, two_instances
    ):
        actor, target = two_instances
        _make_instance(engine, target, status="running")
        _seed_nudge_audit(engine, "audit-3", actor, target, dry_run=True)
        result = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert result["scanned"] == 0
        assert result["minted_commissions"] == 0

    async def test_no_actor_is_skipped(self, service, engine, two_instances):
        _, target = two_instances
        _make_instance(engine, target, status="running")
        _seed_nudge_audit(engine, "audit-4", None, target)
        result = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert result["skipped_no_actor"] == 1

    async def test_missing_actor_row_is_never_attributed(
        self, service, engine, two_instances
    ):
        """A committed nudge whose ACTOR row is gone mints nothing —
        an unattributable commission is never written (the audit row
        carries the id, but the instance is unrecoverable)."""
        _, target = two_instances
        _make_instance(engine, target, status="running")
        _seed_nudge_audit(
            engine,
            "audit-5",
            "deadbeef-0000-4000-8000-00000000dead",
            target,
        )
        result = service.backfill_advisory_nudge_commissions(dry_run=False)
        assert result["skipped_missing_instance_row"] == 1
        assert result["minted_commissions"] == 0


# ---------------------------------------------------------------------------
# Record-side-only zero-behavior-change pins
# ---------------------------------------------------------------------------


class TestRecordSideOnly:
    async def test_mints_touch_only_the_mission_tables(
        self, service, engine, two_instances
    ):
        """The PR1 hard gate: minting writes mission_records /
        mission_commissions and NOTHING else — the instance row's
        status/updated columns are byte-identical before/after."""
        actor, worker = two_instances
        with Session(engine) as session:
            before = session.get(Instance, actor)
            before_status = before.status
            before_version = before.version

        mission = service.resolve_or_mint(actor, "user_api")
        service.mint_commission(
            mission.mission_id,
            commissioner_instance_id=actor,
            worker_instance_id=worker,
            lane="send_message",
        )
        service.mint_continuation_epoch(actor, work_id="w1")
        service.record_attestation(mission.mission_id)

        with Session(engine) as session:
            after = session.get(Instance, actor)
            assert after.status == before_status
            assert after.version == before_version

    async def test_attach_founding_message_is_guarded_first_win(
        self, service, engine, two_instances
    ):
        actor, _ = two_instances
        res = service.resolve_or_mint(actor, "user_api")
        assert attach_founding_message_fail_open(service, res.mission_id, "m-1")
        # second attach is a no-op (guarded on NULL)
        assert not attach_founding_message_fail_open(
            service, res.mission_id, "m-2"
        )
        row = service._repo.get_mission(res.mission_id)
        assert row.founding_message_id == "m-1"
