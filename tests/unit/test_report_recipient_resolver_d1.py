"""D1 — the report-recipient resolver (completion-subsystem Phase 1).

Defect D1: a child's completion report was routed to the child's
STRUCTURAL PARENT. When the work was commissioned by somebody ELSE (an
``instance_nudge`` from an out-of-tree agent), the report went to the
wrong instance: the commissioner never learned the work finished, and
the structural parent consumed a report that was never its own (the
demonstrated fd302262 shape).

THE RULE (proposal-a §2.1)
  1. ``n`` = the latest ``instance_ops_audit`` row with
     ``action='instance_nudge'`` AND ``outcome='committed'`` AND
     ``dry_run=false`` AND ``target=<child>`` AND
     ``created_at <= T_completion``.
  2. ``T_prev`` = ``max(report_injections.created_at)`` for the child
     (``-inf`` when the child never reported).
  3. The recipient is the nudger iff ``n`` exists AND
     ``n.created_at > T_prev`` AND the nudger's row exists AND its
     status is not TERMINATED; else the STRUCTURAL PARENT.
  4. ``created_at`` is TEXT ISO — parsed in PYTHON, never
     string-compared in SQL.

This file is the unit table for the resolver (the A §7 "unit-test trio"
matrix): no-nudge / committed / preview-only / pending / error / stale /
revive / terminated-nudger.

The table is built on a REAL database (file-backed SQLite) with REAL
rows — no mocks on the read path. The "error" leg is driven by a REAL
DB failure (the table is dropped), not by a patched resolver.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ops.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.report_injection.models import ReportInjection
from daemon.services.report_recipient import (
    parse_iso_utc,
    resolve_report_recipient,
)

CHILD = "child-0001"
STRUCTURAL_PARENT = "parent-structural"
COMMISSIONER = "commissioner-0001"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'd1.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(engine, instance_id, status=InstanceStatus.RUNNING.value):
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent-worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()


def _seed_nudge(
    engine,
    *,
    target=CHILD,
    actor=COMMISSIONER,
    outcome="committed",
    dry_run=False,
    created_at=None,
):
    with Session(engine) as session:
        session.add(
            InstanceOpsAudit(
                action="instance_nudge",
                target=target,
                reason="r",
                dry_run=dry_run,
                outcome=outcome,
                created_by_instance_id=actor,
                created_by_agent_id="maintenancer",
                created_at=created_at
                or _iso(datetime.now(timezone.utc)),
            )
        )
        session.commit()


def _seed_report(engine, *, child=CHILD, parent=STRUCTURAL_PARENT, created_at=None):
    with Session(engine) as session:
        session.add(
            ReportInjection(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id="msg-1",
                report_message_id="rmsg-1",
                content="done",
                created_at=created_at
                or _iso(datetime.now(timezone.utc)),
            )
        )
        session.commit()


@pytest.fixture
def seeded(engine):
    _seed_instance(engine, CHILD)
    _seed_instance(engine, STRUCTURAL_PARENT)
    _seed_instance(engine, COMMISSIONER)
    return engine


def _resolve(engine, **kwargs):
    return resolve_report_recipient(
        engine=engine,
        child_instance_id=CHILD,
        structural_parent_id=STRUCTURAL_PARENT,
        **kwargs,
    )


class TestResolverTable:
    def test_no_nudge_falls_back_to_the_structural_parent(self, seeded):
        """THE NORMAL CASE — 99%+ of children were never nudged. The
        resolver is a byte-level no-op here."""
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_committed_nudge_routes_to_the_commissioner(self, seeded):
        _seed_nudge(seeded)
        assert _resolve(seeded) == COMMISSIONER

    def test_a_preview_only_nudge_is_ignored(self, seeded):
        """``instance_nudge`` writes a PREVIEW row by default
        (``dry_run=True``). A preview is not a commission."""
        _seed_nudge(seeded, outcome="previewed", dry_run=True)
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_pending_write_ahead_nudge_is_ignored(self, seeded):
        """The Tier-2 write-ahead row (``outcome='pending'``) is not a
        committed act."""
        _seed_nudge(seeded, outcome="pending", dry_run=False)
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_committed_but_dry_run_nudge_is_ignored(self, seeded):
        """``outcome='committed'`` AND ``dry_run=true`` is contradictory
        but must not commission anything."""
        _seed_nudge(seeded, outcome="committed", dry_run=True)
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_nudge_for_another_child_is_ignored(self, seeded):
        _seed_nudge(seeded, target="some-other-child")
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_stale_nudge_older_than_the_previous_report_is_ignored(self, seeded):
        """Rule 3 — the nudge must be NEWER than the child's previous
        report. A nudge older than the previous report describes work
        that has already been reported."""
        now = datetime.now(timezone.utc)
        _seed_report(seeded, created_at=_iso(now))
        _seed_nudge(seeded, created_at=_iso(now - timedelta(minutes=5)))
        assert _resolve(seeded) == STRUCTURAL_PARENT

    def test_a_fresh_nudge_after_the_previous_report_routes_to_the_commissioner(
        self, seeded
    ):
        now = datetime.now(timezone.utc)
        _seed_report(seeded, created_at=_iso(now - timedelta(minutes=5)))
        _seed_nudge(seeded, created_at=_iso(now))
        assert _resolve(seeded) == COMMISSIONER

    def test_the_first_report_after_an_old_nudge_routes_to_the_commissioner(
        self, seeded
    ):
        """``T_prev = -inf`` (the child never reported) — the design
        accepts that an old committed nudge routes the FIRST report to
        the nudger (a nudge is the plausible cause of a first
        completion)."""
        _seed_nudge(
            seeded,
            created_at=_iso(
                datetime.now(timezone.utc) - timedelta(days=3)
            ),
        )
        assert _resolve(seeded) == COMMISSIONER

    def test_a_terminated_nudger_falls_back_to_the_structural_parent(
        self, engine
    ):
        """The recipient must be LIVE."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_instance(
            engine, COMMISSIONER, status=InstanceStatus.TERMINATED.value
        )
        _seed_nudge(engine)
        assert _resolve(engine) == STRUCTURAL_PARENT

    def test_a_missing_nudger_row_falls_back_to_the_structural_parent(
        self, engine
    ):
        """The nudger's instance row must EXIST."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_nudge(engine)
        assert _resolve(engine) == STRUCTURAL_PARENT

    def test_a_revived_nudger_still_receives_the_report(self, engine):
        """A nudger that was TERMINATED and then REVIVED (status back
        to RUNNING) is live again — it receives the report."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_instance(
            engine, COMMISSIONER, status=InstanceStatus.TERMINATED.value
        )
        _seed_nudge(engine)
        # Revive: the row's status flips back to RUNNING.
        with Session(engine) as session:
            row = session.get(Instance, COMMISSIONER)
            row.status = InstanceStatus.RUNNING.value
            session.commit()
        assert _resolve(engine) == COMMISSIONER

    def test_the_latest_committed_nudge_wins(self, engine):
        """Two committed nudges — the LATER one is the commission."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_instance(engine, COMMISSIONER)
        other = "commissioner-0002"
        _seed_instance(engine, other)
        now = datetime.now(timezone.utc)
        _seed_nudge(engine, actor=COMMISSIONER, created_at=_iso(now - timedelta(minutes=5)))
        _seed_nudge(engine, actor=other, created_at=_iso(now))
        assert _resolve(engine) == other

    def test_a_nudge_after_the_completion_instant_is_ignored(self, engine):
        """Rule 1 — ``created_at <= T_completion``."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_instance(engine, COMMISSIONER)
        now = datetime.now(timezone.utc)
        _seed_nudge(engine, created_at=_iso(now + timedelta(minutes=5)))
        assert (
            _resolve(engine, completion_time=now) == STRUCTURAL_PARENT
        )


class TestResolverFailOpen:
    def test_a_db_failure_falls_back_to_the_structural_parent(self, engine):
        """THE FAIL-OPEN ARM (an UN-MOCKED construction): the audit
        table is DROPPED, so the resolver's read raises for real."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE instance_ops_audit"))
        assert _resolve(engine) == STRUCTURAL_PARENT

    def test_a_corrupt_timestamp_is_skipped(self, engine):
        """A row whose ``created_at`` cannot be parsed is skipped, not
        fatal — the resolver falls back to the structural parent."""
        _seed_instance(engine, CHILD)
        _seed_instance(engine, STRUCTURAL_PARENT)
        _seed_instance(engine, COMMISSIONER)
        with Session(engine) as session:
            session.add(
                InstanceOpsAudit(
                    action="instance_nudge",
                    target=CHILD,
                    reason="r",
                    dry_run=False,
                    outcome="committed",
                    created_by_instance_id=COMMISSIONER,
                    created_at="not-a-timestamp",
                )
            )
            session.commit()
        assert _resolve(engine) == STRUCTURAL_PARENT

    def test_no_structural_parent_returns_none(self, engine):
        """A root child has no parent — the resolver returns ``None``
        (no recipient)."""
        assert (
            resolve_report_recipient(
                engine=engine,
                child_instance_id=CHILD,
                structural_parent_id=None,
            )
            is None
        )


class TestTimestampParsing:
    def test_a_naive_timestamp_is_read_as_utc(self):
        parsed = parse_iso_utc("2026-10-24T12:00:00")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_an_aware_timestamp_is_normalised_to_utc(self):
        tz = timezone(timedelta(hours=7))
        parsed = parse_iso_utc(
            datetime(2026, 10, 24, 19, 0, 0, tzinfo=tz).isoformat()
        )
        assert parsed == datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

    def test_a_garbage_value_is_none(self):
        assert parse_iso_utc("nope") is None
        assert parse_iso_utc(None) is None
        assert parse_iso_utc("") is None
