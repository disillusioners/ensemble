"""D5(i) — the deliberate deny-epoch clears (completion-subsystem Phase 1).

Defect D5-latent(i): the O4 deny-epoch ledger
(``instance_metadata["attestation:denial_epochs"]``) was appended to on
every deny and NEVER cleared. Two consequences:

* KG-3 — the array grows without bound across episodes (its lifetime
  was the instance's whole life, not one episode's).
* A deny REPLAYED under a PRE-reset epoch key is silently swallowed
  (the O4 dedup sees the epoch as "already seen") instead of
  re-counting toward the bound.

D5(i) makes the resets DELIBERATE: all three existing reset sites clear
the epoch ledger in the SAME transaction as the counter reset.

  1. ``reset_attestation_denied_count`` (the attested-allow +
     fresh-episode reset op),
  2. ``reset_attestation_ledger_with_escalation`` (the
     terminal_after_bound finalization),
  3. the fresh-episode INLINE clear in the enqueue lane
     (``instance_messaging._prepare_enqueued_message`` — the same
     transaction as the status=RUNNING write).

``attestation:l6_anchor`` does NOT join: it is reader-only at HEAD
(``waiting_children_watchdog.py``) with ZERO writers — clearing an
unwritten key is dead code.

REPLAY HAZARD (accepted by design): a shrink-replayed pre-reset deny
re-counts ONCE toward bound 3. The failure direction is LOUD
escalation, never a silent allow.

Test fixtures: file-backed SQLite (tmp_path + NullPool + WAL +
busy_timeout) with the REAL repository — no mock on the write path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository


@pytest.fixture
def engine(tmp_path: Path):
    db_path = tmp_path / "d5i-epochs.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _configure(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelInstanceRepository(engine=engine)


def _seed(engine, *, metadata=None):
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status="idle",
                version=1,
                instance_metadata=dict(metadata or {}),
            )
        )
        session.commit()
    return iid


def _epochs(engine, iid) -> list:
    with Session(engine) as session:
        row = session.exec(
            select(Instance).where(Instance.instance_id == iid)
        ).first()
        meta = row.instance_metadata or {}
        value = meta.get("attestation:denial_epochs")
        return list(value) if isinstance(value, list) else []


def _meta(engine, iid) -> dict:
    with Session(engine) as session:
        row = session.exec(
            select(Instance).where(Instance.instance_id == iid)
        ).first()
        return dict(row.instance_metadata or {})


class TestResetSitesClearTheEpochs:
    def test_reset_attestation_denied_count_clears_the_epochs(
        self, engine, repo
    ):
        iid = _seed(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")
        repo.increment_attestation_denied_count(iid, "epoch-2")
        assert len(_epochs(engine, iid)) == 2

        assert repo.reset_attestation_denied_count(iid) is True
        assert _epochs(engine, iid) == []
        assert repo.get_attestation_denied_count(iid) == 0

    def test_reset_attestation_ledger_with_escalation_clears_the_epochs(
        self, engine, repo
    ):
        iid = _seed(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")
        assert len(_epochs(engine, iid)) == 1

        assert repo.reset_attestation_ledger_with_escalation(iid) is True
        assert _epochs(engine, iid) == []
        assert repo.get_attestation_denied_count(iid) == 0

    def test_set_completion_gate_escalated_does_not_clear_the_epochs(
        self, engine, repo
    ):
        """The flag setter is NOT a reset site — it must not clear the
        epoch ledger (the epochs describe the deny history, not the
        escalation marker)."""
        iid = _seed(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")
        repo.set_completion_gate_escalated(iid)
        assert len(_epochs(engine, iid)) == 1

    def test_the_l6_anchor_key_is_untouched(self, engine, repo):
        """``attestation:l6_anchor`` is reader-only at HEAD (zero
        writers) — clearing an unwritten key is dead code, so it stays
        exactly as it was."""
        iid = _seed(
            engine,
            metadata={
                "attestation:l6_anchor": "anchor-value",
                "attestation:denial_epochs": ["epoch-1"],
            },
        )
        assert repo.reset_attestation_denied_count(iid) is True
        meta = _meta(engine, iid)
        assert meta.get("attestation:l6_anchor") == "anchor-value"
        assert meta.get("attestation:denial_epochs") == []

    def test_other_metadata_keys_survive_the_clear(self, engine, repo):
        """The epoch clear touches ONE key — the rest of the metadata
        dict is untouched."""
        iid = _seed(
            engine,
            metadata={
                "watchover_enabled": True,
                "attestation:denial_epochs": ["epoch-1"],
            },
        )
        repo.reset_attestation_denied_count(iid)
        meta = _meta(engine, iid)
        assert meta.get("watchover_enabled") is True
        assert meta.get("attestation:denial_epochs") == []


class TestBoundedReplay:
    def test_a_pre_reset_deny_replay_re_counts_once(self, engine, repo):
        """THE ACCEPTED REPLAY HAZARD — a shrink-replayed pre-reset
        deny re-counts ONCE toward bound 3. The failure direction is
        LOUD escalation, never a silent allow."""
        iid = _seed(engine)
        assert repo.increment_attestation_denied_count(iid, "epoch-A") == 1
        # The reset clears the epoch ledger.
        assert repo.reset_attestation_denied_count(iid) is True
        # The SAME epoch replays — it is NOT swallowed by the O4 dedup
        # (the ledger was cleared), so it re-counts.
        assert repo.increment_attestation_denied_count(iid, "epoch-A") == 1
        # And a SECOND replay of the same epoch IS swallowed (O4 still
        # holds within the episode).
        assert repo.increment_attestation_denied_count(iid, "epoch-A") == 1

    def test_the_epoch_ledger_is_bounded_across_episodes(self, engine, repo):
        """KG-3 — the array's lifetime is ONE episode, not the
        instance's whole life."""
        iid = _seed(engine)
        for episode in range(5):
            repo.increment_attestation_denied_count(iid, f"epoch-{episode}")
            assert len(_epochs(engine, iid)) == 1
            repo.reset_attestation_denied_count(iid)
            assert _epochs(engine, iid) == []


class TestMissingRowSentinel:
    def test_a_missing_instance_still_returns_false(self, engine, repo):
        assert repo.reset_attestation_denied_count("no-such-instance") is False
        assert (
            repo.reset_attestation_ledger_with_escalation("no-such-instance")
            is False
        )


class TestFreshEpisodeInlineClear:
    """The THIRD reset site — the fresh-episode inline clear in the
    enqueue lane (``instance_messaging._prepare_enqueued_message``).

    It rides the SAME transaction as the status=RUNNING write, so the
    epoch clear commits together with the revive.
    """

    @pytest.fixture
    def messaging_service(self, engine):
        from unittest.mock import AsyncMock, MagicMock

        from daemon.services.instance_messaging import InstanceMessagingService

        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._worker_pool = MagicMock()
        manager._worker_pool.notify_work = MagicMock()
        manager._job_queue_service = MagicMock()
        manager._job_queue_service._repository = MagicMock()
        manager._job_queue_service._repository.stamp_message_id = MagicMock()
        return InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(is_shutting_down=False),
        )

    async def test_the_fresh_episode_clear_clears_the_epochs(
        self, engine, messaging_service
    ):
        iid = _seed(engine)
        # The instance is TERMINAL — the fresh-episode clear only fires
        # on a revive-from-terminal.
        with Session(engine) as session:
            row = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            row.status = "completed"
            row.attestation_denied_count = 3
            row.instance_metadata = {
                "attestation:denial_epochs": ["epoch-1", "epoch-2"]
            }
            session.commit()

        await messaging_service.enqueue_message(
            instance_id=iid,
            message="a fresh user message",
            source="api",
        )

        with Session(engine) as session:
            row = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            assert row.attestation_denied_count == 0
            assert row.completion_gate_escalated is False
            assert (row.instance_metadata or {}).get(
                "attestation:denial_epochs"
            ) == []

    async def test_an_internal_episode_does_not_clear_the_epochs(
        self, engine, messaging_service
    ):
        """The NEGATIVE half — an internal revive (an AGENT-typed
        source) is NOT a fresh episode, so the ledger must survive."""
        iid = _seed(engine)
        with Session(engine) as session:
            row = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            row.status = "completed"
            row.attestation_denied_count = 3
            row.instance_metadata = {
                "attestation:denial_epochs": ["epoch-1"]
            }
            session.commit()

        await messaging_service.enqueue_message(
            instance_id=iid,
            message="an internal nudge",
            source="instance_ops:caller-1",
        )

        with Session(engine) as session:
            row = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            assert row.attestation_denied_count == 3
            assert (row.instance_metadata or {}).get(
                "attestation:denial_epochs"
            ) == ["epoch-1"]
