"""Unit tests — the PR2 READ FLIP at the gate (Proposal B PR2).

The read sites PR2 flips:

* the DELEGATION CENSUS (``attestation_required``) — the record's
  commission rows + the legacy scan (the declared fallback leg);
* the SOURCE U ANCHOR — the mission record's ``founding_message_id``
  (the six-step exclusion ladder survives as the legacy fallback);
* the FIFTH R2 INPUT (``outstanding_commissions``) — the
  mission-scoped count, with its ``-1`` "unknown" fail-open sentinel;
* the QUIET PREDICATE (``c_quiet``) — it now requires
  ``outstanding_commissions == 0``.

The manager fake wires the REAL read helpers (a file-backed SQLite
``MissionRecordService`` behind them) so the tests exercise the
production read path end-to-end — not a replica of it.

FAIL-OPEN PINS (convention #3): the ``-1`` sentinel must fail the
evaluation OPEN (a plain allow), never read as "0 outstanding"
(a quiet mission) and never deny.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.services import attestation_gate as gate_module
from daemon.services.attestation_gate import (
    DEFAULT_GATE_SETTINGS,
    Decision,
    evaluate,
)
from daemon.services.attestation_resolver_activation import (
    SourceCSignals,
    activation_predicate,
)
from daemon.services.mission_record_reads import (
    MISSION_READ_UNKNOWN,
    count_outstanding_commissions,
    read_founding_message_id,
    read_mission_census,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)

ANCHOR = "11111111-1111-4111-8111-111111111111"
WORKER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr2-gate.sqlite'}",
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


@pytest.fixture(autouse=True)
def _reset_mode():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def enforce_mode(monkeypatch):
    monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


class _Manager:
    """A manager double wired to the REAL mission-record read helpers."""

    def __init__(self, engine, *, pending=0, wakeups=0, live=0, busy=0):
        self._engine = engine
        self.count_pending_children = lambda _iid: pending
        self.get_queued_or_expected_wakeups = lambda _iid: wakeups
        self.count_live_descendants = lambda _iid: live
        self.count_busy_descendants = lambda _iid: busy
        self.has_open_user_answer = lambda _iid: False
        # The PR2 facades — the REAL read path.
        self.mission_has_commissions = lambda iid: read_mission_census(
            self._engine, iid
        )
        self.get_mission_founding_message_id = (
            lambda iid: read_founding_message_id(self._engine, iid)
        )
        self.count_outstanding_commissions = (
            lambda iid: count_outstanding_commissions(self._engine, iid)
        )
        self.get_mission_id = lambda iid: None
        self.list_mission_outstanding_commissions = lambda iid: []


def _ai(text: str, tool_calls=None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])


def _messages_with_delegation_then_user() -> list:
    """A delegation, then a NEW user message (the re-anchoring shape).

    The delegation sits BEFORE the second user message, so the LEGACY
    window (anchored on the LAST real user message) does not see it —
    while the RECORD window (anchored on the epoch's founding message)
    does.
    """
    return [
        HumanMessage(content="do the thing", id="m-founding"),
        _ai(
            "",
            tool_calls=[
                {
                    "name": "send_message",
                    "args": {"target": "child"},
                    "id": "d1",
                }
            ],
        ),
        HumanMessage(content="actually, do this instead", id="m-user-2"),
        _ai("Everything is complete."),
    ]


def _messages_legacy_anchor() -> list:
    """A delegation AFTER the last user message (the legacy window sees
    it — the bundle is assembled and the U section is the last real
    user message)."""
    return [
        HumanMessage(content="actually, do this instead", id="m-user-2"),
        _ai(
            "",
            tool_calls=[
                {
                    "name": "send_message",
                    "args": {"target": "child"},
                    "id": "d1",
                }
            ],
        ),
        _ai("Everything is complete."),
    ]


@pytest.fixture
def seeded(engine):
    for iid in (ANCHOR, WORKER):
        with Session(engine) as session:
            if session.get(Instance, iid) is None:
                session.add(
                    Instance(
                        instance_id=iid,
                        agent_id="leader",
                        agent_dir=f"agents/leader/{iid[:8]}",
                        status="running",
                        project_id=None,
                    )
                )
                session.commit()
    return MissionRecordService(engine)


class TestCensusFlip:
    def test_the_record_arms_the_census_after_a_re_anchoring_user_message(
        self, seeded, engine, enforce_mode
    ):
        """The D2 shape: a delegation happened, then a NEW user message
        moved the legacy window. The legacy scan alone would say "no
        delegation since the last user message" (the gate OFF); the
        record remembers the epoch's commission row."""
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-founding"
        )
        seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="send_message",
        )
        manager = _Manager(engine)
        messages = _messages_with_delegation_then_user()

        # The legacy scan alone: the delegation is BEFORE the new user
        # message ⇒ the legacy window says "no delegation".
        legacy_only = _Manager(engine)
        legacy_only.mission_has_commissions = lambda _iid: None
        legacy_only.get_mission_founding_message_id = lambda _iid: None
        legacy_result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, legacy_only
        )
        assert legacy_result.attestation_required is False

        # With the record consulted: the epoch still has its commission
        # row ⇒ the census stays ON.
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.attestation_required is True

    def test_the_legacy_scan_is_the_fallback_leg(self, seeded, engine, enforce_mode):
        """No record ⇒ the legacy scan's answer decides."""
        manager = _Manager(engine)
        messages = [
            HumanMessage(content="go", id="m-1"),
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child"},
                        "id": "d1",
                    }
                ],
            ),
            _ai("done"),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.attestation_required is True

    def test_the_census_never_un_arms_a_real_delegation(
        self, seeded, engine, enforce_mode
    ):
        """The UNION half: a record that says "no commissions" must not
        UN-arm the gate for a delegation the legacy scan can see (the
        non-child send_message mints no commission row — the watcher
        tree-guard)."""
        seeded.resolve_or_mint(ANCHOR, "user_api")
        manager = _Manager(engine)
        messages = [
            HumanMessage(content="go", id="m-1"),
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "not-a-child"},
                        "id": "d1",
                    }
                ],
            ),
            _ai("done"),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.attestation_required is True


class TestAnchorFlip:
    def test_source_u_is_the_record_anchor(self, seeded, engine, enforce_mode):
        """The SOURCE U message is the epoch's founding message — NOT
        the last real user message."""
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-founding"
        )
        seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="send_message",
        )
        manager = _Manager(engine)
        messages = _messages_with_delegation_then_user()
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        snapshot = result.resolver
        assert snapshot is not None
        bundle = snapshot.result.bundle
        assert bundle is not None
        # The bundle's U section carries the FOUNDING message's text.
        assert "do the thing" in bundle.text
        assert "actually, do this instead" not in bundle.text

    def test_the_legacy_ladder_is_the_fallback(self, seeded, engine, enforce_mode):
        """No record ⇒ the legacy six-step ladder picks the anchor."""
        manager = _Manager(engine)
        messages = _messages_legacy_anchor()
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        snapshot = result.resolver
        assert snapshot is not None
        bundle = snapshot.result.bundle
        assert bundle is not None
        assert "actually, do this instead" in bundle.text


class TestFifthR2Input:
    def test_an_outstanding_commission_holds_the_mission(
        self, seeded, engine, enforce_mode
    ):
        """The D2 shape: an OUT-OF-TREE commissioned worker is running.
        The tree counters read zero (it is not a descendant) — the
        mission-scoped input sees the commission and the gate allows
        the turn as a legitimate pending wakeup instead of denying."""
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-founding"
        )
        seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="instance_nudge",
        )
        manager = _Manager(engine)
        messages = [
            HumanMessage(content="do the thing", id="m-founding"),
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "instance_nudge",
                        "args": {"target": "out-of-tree"},
                        "id": "d1",
                    }
                ],
            ),
            _ai("Everything is complete."),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.outstanding_commissions == 1
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        # The deny counter is UNCHANGED on the legit-pending path.
        assert result.next_denied_count == 0

    def test_the_unknown_sentinel_fails_the_evaluation_open(
        self, seeded, engine, enforce_mode
    ):
        """The ``-1`` sentinel ⇒ the whole evaluation fails OPEN (a plain
        allow) — NEVER a deny, NEVER read as "0 outstanding"."""
        manager = _Manager(engine)
        manager.count_outstanding_commissions = (
            lambda _iid: MISSION_READ_UNKNOWN
        )
        messages = [
            HumanMessage(content="do the thing", id="m-founding"),
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child"},
                        "id": "d1",
                    }
                ],
            ),
            _ai("Everything is complete."),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.decision is Decision.ALLOWED
        assert result.outstanding_commissions == MISSION_READ_UNKNOWN
        assert result.gate_exception_seen is True

    def test_a_missing_facade_leaves_the_legacy_predicate_intact(
        self, seeded, engine, enforce_mode
    ):
        """A facade that is absent entirely reads as INACTIVE (0) — the
        pre-PR2 three-counter predicate is preserved exactly."""
        manager = _Manager(engine)
        del manager.count_outstanding_commissions
        messages = [
            HumanMessage(content="do the thing", id="m-founding"),
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child"},
                        "id": "d1",
                    }
                ],
            ),
            _ai("Everything is complete."),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.outstanding_commissions == 0
        assert result.decision is Decision.DENIED


class TestQuietPredicate:
    def test_quiet_requires_zero_outstanding_commissions(self):
        """The predicate's ``c_quiet`` term gains the 5th input."""
        base = dict(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: _no_suspicion(),
            b_source=lambda: _no_suspicion_b(),
        )
        quiet = activation_predicate(
            c_source=lambda: SourceCSignals(0, 0, 0, 0),
            **base,
        )
        assert quiet.fired is True
        assert quiet.band == "deny"

        not_quiet = activation_predicate(
            c_source=lambda: SourceCSignals(
                0, 0, 0, 0, outstanding_commissions=1
            ),
            **base,
        )
        # An outstanding commission means the mission is NOT quiet: the
        # deny band does not fire (the judge lane takes it instead).
        assert not_quiet.fired is False
        assert not_quiet.band == ""

    def test_the_legacy_quiet_predicate_is_unchanged_by_default(self):
        """The dataclass default keeps the pre-PR2 predicate EXACTLY
        (the input is inactive when the caller does not consult the
        records)."""
        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: _no_suspicion(),
            b_source=lambda: _no_suspicion_b(),
            c_source=lambda: SourceCSignals(0, 0, 0, 0),
        )
        assert "c_quiet" in result.terms_fired


def _no_suspicion():
    from daemon.services.attestation_resolver_activation import SourceASignals

    return SourceASignals(
        advisory_present=False,
        contradiction_flag=False,
        phrase_match=False,
        word_count_below_threshold=False,
        evidence=(),
    )


def _no_suspicion_b():
    from daemon.services.attestation_resolver_activation import SourceBSignals

    return SourceBSignals(
        marker_hit=False,
        marker_terms=(),
        length_trigger=False,
        final_word_count=100,
        attested=False,
    )
