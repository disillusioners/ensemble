"""Facade-forwarding guard — the deny-ledger EPOCH TAG (Proposal B PR2).

THE DEFECT THIS GUARD PINS
---------------------------
PR2 added the epoch tag to the deny ledger: the in-graph gate calls
``safe_increment(ledger, instance_id, denial_epoch, mission_id=…)``
when the owning mission is known, and the ledger entry is written as
``{"epoch": <fingerprint>, "mission_id": <mission>}``.

The ONLY production implementation of the ledger is the protocol alias
on ``SQLModelInstanceRepository``::

    def increment(self, instance_id, denial_epoch) -> int:      # 2-arg
        return self.increment_attestation_denied_count(instance_id, denial_epoch)

If that alias is not widened to accept and FORWARD the third argument,
the production call raises a ``TypeError`` INSIDE the C3 fail-open
wrapper → ``None`` → ``graph.py`` returns ``{"attestation_route":
None}`` → in enforce mode every ``Decision.DENIED`` silently drops its
nudge, never increments ``attestation_denied_count`` and never writes
the epoch-tagged entry. The whole PR2 epoch-tagged ledger is DEAD CODE
in production while every direct-call unit test stays green.

That is the repo's manual-forwarding seam bug class (the
``InstanceManager`` facade-forwarding discipline's sibling at the ledger
seam) — and it is invisible to a test that calls
``increment_attestation_denied_count`` directly.

So this file drives the REAL seam end-to-end:
``safe_increment`` → the REAL ``SQLModelInstanceRepository.increment``
(alias) → the REAL row. No mock on the write path.

Test fixtures: file-backed SQLite (the established unit pattern for
this repository class — ``tests/unit/test_attestation_epoch_clears_d5i.py``;
the alias-forwarding defect is dialect-INDEPENDENT — it is a Python
signature/forwarding defect, not a transaction-dialect artifact, so the
SQLite unit leg is a faithful guard. The production-dialect (PostgreSQL)
proof rides the PR2 PG scratch integration file).
"""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.attestation_ledger import safe_increment


@pytest.fixture
def engine(tmp_path: Path):
    db_path = tmp_path / "pr2-ledger-forwarding.sqlite"
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


def _seed(engine) -> str:
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
            )
        )
        session.commit()
    return iid


def _entries(engine, iid) -> list:
    with Session(engine) as session:
        row = session.get(Instance, iid)
        value = (row.instance_metadata or {}).get("attestation:denial_epochs")
        return list(value) if isinstance(value, list) else []


class TestTheAliasForwardsTheEpochTag:
    def test_safe_increment_with_a_mission_tag_writes_the_tagged_entry(
        self, engine, repo
    ):
        """THE seam: ``safe_increment`` → the REAL alias → the REAL row.

        Pre-fix (the 2-arg alias) this raises a TypeError inside the
        C3 fail-open wrapper → ``None`` → NOTHING is written: the
        counter stays 0 and the deny ledger stays empty.
        """
        iid = _seed(engine)

        result = safe_increment(
            repo,
            iid,
            "fingerprint-1",
            mission_id="mission-a",
        )

        # The write landed (not swallowed by the fail-open wrapper).
        assert result == 1
        assert repo.get_attestation_denied_count(iid) == 1
        entries = _entries(engine, iid)
        assert entries == [
            {"epoch": "fingerprint-1", "mission_id": "mission-a"}
        ]

    def test_safe_increment_without_a_tag_keeps_the_legacy_shape(
        self, engine, repo
    ):
        """The epoch-unaware call path is byte-identical to pre-PR2 (the
        legacy plain-fingerprint entry)."""
        iid = _seed(engine)

        result = safe_increment(repo, iid, "fingerprint-1")

        assert result == 1
        assert _entries(engine, iid) == ["fingerprint-1"]

    def test_the_alias_accepts_the_mission_tag_as_a_keyword(self):
        """The protocol contract: the alias's third parameter is named
        ``mission_id`` and is OPTIONAL (a two-argument call must still
        work — the pre-PR2 call shape)."""
        signature = inspect.signature(
            SQLModelInstanceRepository.increment
        )
        parameters = list(signature.parameters.values())
        assert [p.name for p in parameters] == [
            "self",
            "instance_id",
            "denial_epoch",
            "mission_id",
        ]
        assert parameters[-1].default is None


class TestTheModeGateHasOneDefinition:
    def test_the_reads_module_does_not_shadow_the_gate(self):
        """ONE definition of the mode gate (review finding).

        The mode ladder has a single owner (``mission_record_service``);
        the reads module IMPORTS ``mission_records_enforce_enabled``
        from it. A second (shadowing) definition in the reads module
        would be two sources of truth for one knob — and the manager
        would import one copy while the writer used the other, so the
        reads could be armed while the epoch-boundary clear was not.
        """
        from daemon.services import mission_record_reads, mission_record_service

        assert (
            mission_record_reads.mission_records_enforce_enabled
            is mission_record_service.mission_records_enforce_enabled
        )
        # The reads module must not DEFINE it.
        source = inspect.getsource(mission_record_reads)
        assert (
            "def mission_records_enforce_enabled(" not in source
        ), "the reads module must IMPORT the gate, never redefine it"


class TestTheEpochTagSurvivesTheSeam:
    def test_a_replay_inside_the_epoch_is_swallowed_through_the_seam(
        self, engine, repo
    ):
        """The O4 replay-proofing rides the SAME seam — a replayed
        fingerprint under the same mission does not double-count."""
        iid = _seed(engine)
        first = safe_increment(
            repo, iid, "fingerprint-1", mission_id="mission-a"
        )
        replay = safe_increment(
            repo, iid, "fingerprint-1", mission_id="mission-a"
        )
        assert first == 1
        assert replay == 1  # the UNCHANGED count (the O4 dedup)
        assert repo.get_attestation_denied_count(iid) == 1

    def test_the_same_fingerprint_under_a_new_epoch_counts(self, engine, repo):
        """The D5 carryover lane dies: the same fingerprint under a
        DIFFERENT mission counts again."""
        iid = _seed(engine)
        safe_increment(repo, iid, "fingerprint-1", mission_id="mission-a")
        second = safe_increment(
            repo, iid, "fingerprint-1", mission_id="mission-b"
        )
        assert second == 2
        assert repo.get_attestation_denied_count(iid) == 2
