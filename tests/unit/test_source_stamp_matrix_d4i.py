"""D4(i) — the internal stamp-set widening (completion-subsystem Phase 1).

Defect D4(i): only FOUR source namespaces were stamped internal
(``internal_report:`` / ``internal_error_report:`` / ``internal_agent:``
/ ``system:``). Every other daemon-minted lane stayed BARE, and a BARE
message reads as a REAL USER message to the attestation scanner's
exclusion ladder. On the LEADER'S OWN stream that MOVED the SOURCE U
anchor and ERASED the delegation window — the leader's own
``send_message`` delegation fell out of the window and
``attestation_required`` collapsed to False.

The highest-traffic bare re-anchorer was ``agent:{caller}`` (the
job_continue self-continuation lane — x111 in the all-time job-source
census). The nudge lane (``instance_ops:``) was in NEITHER the stamp
set NOR the reserved set.

D4(i) widens the set to 16 members and adds the missing ``msg_type``
arms (``instance_ops:`` / ``agent:`` → AGENT, ``system:`` → SYSTEM, one
arm for the ten remaining daemon lanes → AGENT). ``scheduler`` stays
BARE by product ruling; ``api`` / the chat sources /
``api_resume_fallback`` stay BARE by contract (they ARE the user
lanes).

WHAT THIS FILE PINS
-------------------
1. The canonical set has ONE home (``daemon.constants``) and the
   writer (instance_messaging) + the reader (attestation_scanner)
   resolve the SAME tuple — no hand-maintained forks.
2. The msg_type groups' union is EXACTLY the stamp set (no lane can be
   stamped-but-unclassified or classified-but-unstamped).
3. Per-lane matrix: the stamp shape + the derived msg_type.
4. The NEVER-stamped lanes stay bare and HUMAN.
5. THE BEHAVIOURAL POINT: a stamped lane no longer moves the SOURCE U
   anchor, so the delegation window survives it. This is the pin that
   goes RED when the widening is reverted.
6. The scanner ladder recognizes the widened stamped shape (branch 3
   + branch 4b).
7. The compaction hoist treats the widened lanes exactly like the
   pre-existing ones (same joined class) — the design's "targeted
   confirmation test" for the compaction surface.
8. The persisted ``MessageQueue.type`` is AGENT / SYSTEM for the
   widened lanes (the real enqueue path, file-backed SQLite).

MUTATION NOTE: every behavioural pin below was proven to go RED when
the corresponding production change is reverted (see the commit
message for the exact revert commands).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.constants import (
    INTERNAL_AGENT_TYPED_EXACT_SOURCES,
    INTERNAL_AGENT_TYPED_SOURCE_PREFIXES,
    INTERNAL_ERROR_REPORT_SOURCE_PREFIX,
    INTERNAL_REPORT_SOURCE_PREFIX,
    INTERNAL_REPORT_TYPED_SOURCE_PREFIXES,
    INTERNAL_STAMPED_SOURCE_PREFIXES,
    INTERNAL_SYSTEM_TYPED_SOURCE_PREFIXES,
    is_internal_stamped_source,
    is_reserved_source,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageType
from daemon.services.attestation_scanner import (
    _INTERNAL_STAMPED_SOURCE_PREFIXES as SCANNER_STAMP_SET,
)
from daemon.services.attestation_scanner import (
    find_last_real_user_index,
    is_real_user_message,
    scan_delegation_after_last_user,
)
from daemon.services.instance_messaging import (
    _INTERNAL_STAMPED_SOURCE_PREFIXES as IMS_STAMP_SET,
    _derive_internal_message_type,
    _stamped_additional_kwargs,
    InstanceMessagingService,
)
from langchain_core.messages import AIMessage, HumanMessage


# ── The D4(i) matrix ────────────────────────────────────────────────────────
#
# (source, expected msg_type). The four pre-D4(i) members keep their
# pre-existing classification; the twelve new members carry the
# D4(i) arms.

_D4I_MATRIX: tuple[tuple[str, str], ...] = (
    # ── pre-existing members (unchanged) ──────────────────────────
    ("internal_report:child-1", MessageType.COMPLETION_REPORT.value),
    ("internal_error_report:child-1", MessageType.ERROR_REPORT.value),
    ("internal_agent:caller-1", MessageType.AGENT.value),
    ("system:watchdog", MessageType.SYSTEM.value),
    # ── D4(i) additions ──────────────────────────────────────────
    ("instance_ops:caller-1", MessageType.AGENT.value),
    ("agent:developer", MessageType.AGENT.value),
    ("explore:inst-1", MessageType.AGENT.value),
    ("experience:inst-1", MessageType.AGENT.value),
    ("blueprint-sidecar:inst-1", MessageType.AGENT.value),
    ("cascade_resume", MessageType.AGENT.value),
    ("auto_dispatch_fallback", MessageType.AGENT.value),
    ("watchover_next_command", MessageType.AGENT.value),
    ("skill_metric_scan", MessageType.AGENT.value),
    ("skill_evolution", MessageType.AGENT.value),
    ("admin-endpoint", MessageType.AGENT.value),
    ("auto-scan", MessageType.AGENT.value),
)

#: Never-stamped lanes — genuine user origins (plus ``scheduler``,
#: which is a product ruling: the scheduler is the de-facto requestor
#: of a scheduled mission and anchors by design).
_NEVER_STAMPED: tuple[str, ...] = (
    "api",
    "api_resume_fallback",
    "telegram:alice:1",
    "slack:team-1",
    "discord:chan-1",
    "whatsapp:123",
    "scheduler",
)


#: The canonical stamp set — the PREFIXES (not the concrete sources
#: used by the matrix above). Exact-membership pin.
_EXPECTED_STAMP_SET: frozenset[str] = frozenset({
    "internal_report:",
    "internal_error_report:",
    "internal_agent:",
    "system:",
    "instance_ops:",
    "agent:",
    "explore:",
    "experience:",
    "blueprint-sidecar:",
    "cascade_resume",
    "auto_dispatch_fallback",
    "watchover_next_command",
    "skill_metric_scan",
    "skill_evolution",
    "admin-endpoint",
    "auto-scan",
})


class TestCanonicalStampSet:
    def test_the_writer_and_the_reader_share_one_tuple(self):
        """ONE canonical home — the writer (instance_messaging) and the
        reader (attestation_scanner) must not fork."""
        assert IMS_STAMP_SET is INTERNAL_STAMPED_SOURCE_PREFIXES
        assert SCANNER_STAMP_SET is INTERNAL_STAMPED_SOURCE_PREFIXES

    def test_the_set_has_the_d4i_membership(self):
        """EXACT membership pin (fork-prevention)."""
        assert set(INTERNAL_STAMPED_SOURCE_PREFIXES) == set(_EXPECTED_STAMP_SET)

    def test_the_matrix_covers_every_stamp_set_member(self):
        """Every stamped lane appears in the behavioural matrix — a new
        member cannot be added without a behavioural pin."""
        covered = {
            source
            for source, _ in _D4I_MATRIX
            if source.endswith(":") is False
        }
        # The matrix uses CONCRETE sources for the colon families; match
        # them by prefix.
        for prefix in _EXPECTED_STAMP_SET:
            assert any(
                s == prefix or s.startswith(prefix)
                for s, _ in _D4I_MATRIX
            ), prefix
        assert covered >= {
            s for s in _EXPECTED_STAMP_SET if not s.endswith(":")
        }

    def test_the_msg_type_groups_partition_the_stamp_set(self):
        """Every stamped lane is classified, and every classified lane
        is stamped — the two sets are the same set."""
        classified = (
            set(INTERNAL_REPORT_TYPED_SOURCE_PREFIXES)
            | set(INTERNAL_SYSTEM_TYPED_SOURCE_PREFIXES)
            | set(INTERNAL_AGENT_TYPED_SOURCE_PREFIXES)
            | set(INTERNAL_AGENT_TYPED_EXACT_SOURCES)
        )
        assert classified == set(INTERNAL_STAMPED_SOURCE_PREFIXES)

    def test_the_stamp_set_is_a_subset_of_the_reserved_set(self):
        """Every stamped lane is ALSO reserved — the HTTP boundary can
        never forge an internal origin."""
        for source in INTERNAL_STAMPED_SOURCE_PREFIXES:
            assert is_reserved_source(source), source


class TestReportFamilyTuple:
    """F2 — the report-family prefixes live in ``daemon.constants`` (the
    canonical home). The enqueue lane must not hardcode them, and the
    canonical tuple must stay in lock-step with the runtime
    classification."""

    #: The documented per-family msg type — the canonical mapping the
    #: runtime must reproduce. A tuple member without an arm here goes
    #: RED (never a silently mis-derived msg type).
    _REPORT_TYPE_BY_PREFIX: dict[str, str] = {
        INTERNAL_REPORT_SOURCE_PREFIX: MessageType.COMPLETION_REPORT.value,
        INTERNAL_ERROR_REPORT_SOURCE_PREFIX: MessageType.ERROR_REPORT.value,
    }

    def test_the_tuple_is_built_from_the_canonical_literals(self):
        """ONE canonical home — the tuple and its two members cannot
        drift apart."""
        assert INTERNAL_REPORT_TYPED_SOURCE_PREFIXES == (
            INTERNAL_REPORT_SOURCE_PREFIX,
            INTERNAL_ERROR_REPORT_SOURCE_PREFIX,
        )

    def test_the_tuple_and_the_documented_mapping_agree(self):
        """A NEW tuple member REQUIRES a documented msg type (and a
        runtime arm) — the set comparison is the fail-loud gate."""
        assert set(INTERNAL_REPORT_TYPED_SOURCE_PREFIXES) == set(
            self._REPORT_TYPE_BY_PREFIX
        )

    @pytest.mark.parametrize(
        ("prefix", "expected_type"),
        [
            (INTERNAL_REPORT_SOURCE_PREFIX, MessageType.COMPLETION_REPORT.value),
            (
                INTERNAL_ERROR_REPORT_SOURCE_PREFIX,
                MessageType.ERROR_REPORT.value,
            ),
        ],
    )
    def test_each_family_keeps_its_documented_msg_type(
        self, prefix, expected_type
    ):
        """The runtime honours the canonical tuple."""
        assert (
            _derive_internal_message_type(f"{prefix}child-1")
            == expected_type
        )


class TestPerLaneMatrix:
    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_the_lane_is_stamped(self, source, expected_type):
        """The stamp shape — ``injected_message=True`` + the source."""
        assert _stamped_additional_kwargs(source) == {
            "injected_message": True,
            "source": source,
        }

    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_the_lane_derives_its_msg_type(self, source, expected_type):
        assert _derive_internal_message_type(source) == expected_type

    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_the_lane_is_recognized_as_internal(self, source, expected_type):
        assert is_internal_stamped_source(source) is True

    @pytest.mark.parametrize("source", _NEVER_STAMPED)
    def test_the_never_stamped_lane_stays_bare(self, source):
        assert _stamped_additional_kwargs(source) is None
        assert is_internal_stamped_source(source) is False
        assert _derive_internal_message_type(source) is None

    def test_exact_members_do_not_match_by_prefix(self):
        """Non-colon members match by EXACT equality — ``auto-scan``
        must not swallow a hypothetical ``auto-scan-2``."""
        assert is_internal_stamped_source("auto-scan") is True
        assert is_internal_stamped_source("auto-scan-2") is False
        assert is_internal_stamped_source("cascade_resume-2") is False


class TestScannerLadder:
    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_a_stamped_message_is_not_a_real_user_message(
        self, source, expected_type
    ):
        """Ladder step 3 (the ``injected_message`` flag) excludes it."""
        msg = HumanMessage(
            content="internal traffic",
            id="m-1",
            additional_kwargs={"injected_message": True, "source": source},
        )
        assert is_real_user_message(msg) is False

    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_the_4b_conjunction_excludes_it(self, source, expected_type):
        """Ladder step 4b — the fail-closed defense in depth. The
        conjunction (``injected_message`` AND an internal source)
        must exclude the widened lanes too."""
        from daemon.services.attestation_scanner import (
            is_real_user_message as _is_real_user_message,
        )

        msg = HumanMessage(
            content="internal traffic",
            id="m-2",
            additional_kwargs={"injected_message": True, "source": source},
        )
        assert _is_real_user_message(msg) is False

    @pytest.mark.parametrize("source", _NEVER_STAMPED)
    def test_a_bare_user_message_stays_a_real_user_message(self, source):
        msg = HumanMessage(content="hello", id="m-3", additional_kwargs={})
        assert is_real_user_message(msg) is True


class TestSourceUAnchor:
    """THE BEHAVIOURAL POINT of D4(i).

    A leader transcript: [real user message, AIMessage(send_message),
    <internal lane>]. Before D4(i) the internal lane was a REAL USER
    message, so the SOURCE U anchor moved onto it and the delegation
    window was erased → ``attestation_required`` collapsed to False.
    """

    @staticmethod
    def _transcript(tail_source: str, *, stamped: bool = True) -> list:
        """Build the leader transcript.

        The tail message's ``additional_kwargs`` come from the REAL
        WRITER (:func:`_stamped_additional_kwargs`) — not from a
        hand-built dict. That is what makes this pin mutation-sensitive:
        revert the D4(i) widening and the writer stops stamping
        ``agent:developer``, the message stays BARE, the SOURCE U
        anchor moves onto it and the delegation window is erased.
        """
        tail_kwargs = (
            _stamped_additional_kwargs(tail_source)
            if stamped
            else {"source": tail_source}
        )
        return [
            HumanMessage(content="do the thing", id="u-1", additional_kwargs={}),
            AIMessage(
                content="",
                id="a-1",
                additional_kwargs={},
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"instance_id": "child-1", "message": "go"},
                        "id": "call-1",
                    }
                ],
            ),
            HumanMessage(
                content="internal traffic",
                id="h-1",
                additional_kwargs=tail_kwargs or {},
            ),
        ]

    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    def test_the_delegation_window_survives_the_internal_lane(
        self, source, expected_type
    ):
        messages = self._transcript(source)
        assert find_last_real_user_index(messages) == 0
        result = scan_delegation_after_last_user(messages)
        assert result.delegation_since_last_user is True, (
            f"the {source!r} lane moved the SOURCE U anchor and erased "
            "the delegation window"
        )

    def test_a_genuine_user_lane_still_moves_the_anchor(self):
        """The NEGATIVE half — a real user message DOES re-anchor
        (that is the contract; only INTERNAL lanes are excluded)."""
        messages = self._transcript("api", stamped=False)
        # The bare "api" message is a real user message → the anchor
        # moves onto it → the delegation BEFORE it is out of window.
        assert find_last_real_user_index(messages) == 2
        result = scan_delegation_after_last_user(messages)
        assert result.delegation_since_last_user is False


class TestCompactionHoistConfirmation:
    """The design's "targeted confirmation test" for the compaction
    hoist surface.

    A widened lane lands in the SAME joined class as the pre-existing
    internal prefixes: a bare-flag injected note (no ``context_kind``).
    Hoisted when UNANSWERED; absorbed into the compacted span when
    ANSWERED — exactly like ``internal_report:`` today.
    """

    @staticmethod
    def _stamped_msg(source: str, msg_id: str):
        """The tail message built by the REAL WRITER — the compaction
        surface only ever sees what the writer stamped."""
        return HumanMessage(
            content="internal traffic",
            id=msg_id,
            additional_kwargs=_stamped_additional_kwargs(source) or {},
        )

    def test_an_unanswered_widened_lane_is_hoisted(self):
        from daemon._content_hardening import is_hoisted_injected

        msg = self._stamped_msg("agent:developer", "m-1")
        assert is_hoisted_injected(msg, frozenset()) is True

    def test_an_answered_widened_lane_is_absorbed_like_the_pre_existing_ones(
        self,
    ):
        """Same joined class as ``internal_report:`` — the widened lane
        is NOT a permanent preserve."""
        from daemon._content_hardening import is_hoisted_injected

        widened = self._stamped_msg("agent:developer", "m-1")
        preexisting = self._stamped_msg("internal_report:child-1", "m-2")
        absorbed = frozenset({"m-1", "m-2"})
        assert is_hoisted_injected(widened, absorbed) is False
        assert is_hoisted_injected(preexisting, absorbed) is False

    def test_a_context_kind_message_is_still_permanently_preserved(self):
        """The ``context_kind`` exemption — the fallback the design
        names — is unaffected by the widening."""
        from daemon._content_hardening import has_context_kind, is_hoisted_injected

        msg = HumanMessage(
            content="[SYSTEM CONTEXT: ...]",
            id="m-3",
            additional_kwargs={
                "injected_message": True,
                "source": "agent:developer",
                "context_kind": "project_scope_guide",
            },
        )
        assert has_context_kind(msg) is True
        assert is_hoisted_injected(msg, frozenset({"m-3"})) is True


# ── The real enqueue path (file-backed SQLite, BLUEPRINT §3 recipe) ────────


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'd4i.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_fk_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def messaging_service(engine):
    from unittest.mock import AsyncMock, MagicMock

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


def _seed_instance(engine, instance_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent-worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=None,
                status=InstanceStatus.IDLE.value,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()


class TestPersistedRowType:
    @pytest.mark.parametrize(("source", "expected_type"), _D4I_MATRIX)
    async def test_the_persisted_row_carries_the_derived_type(
        self, engine, messaging_service, source, expected_type
    ):
        """The REAL enqueue path — the persisted ``MessageQueue.type``
        is the derived one, not the old HUMAN default."""
        instance_id = "inst-" + source.replace(":", "-")
        _seed_instance(engine, instance_id)

        await messaging_service.enqueue_message(
            instance_id=instance_id,
            message="hello",
            source=source,
        )

        with Session(engine) as session:
            rows = list(session.exec(select(MessageQueue)).all())
        assert len(rows) == 1
        assert rows[0].type == expected_type
        assert rows[0].source == source

    @pytest.mark.parametrize("source", _NEVER_STAMPED)
    async def test_the_never_stamped_lane_persists_a_human_row(
        self, engine, messaging_service, source
    ):
        instance_id = "inst-human-" + source.replace(":", "-")
        _seed_instance(engine, instance_id)

        await messaging_service.enqueue_message(
            instance_id=instance_id,
            message="hello",
            source=source,
        )

        with Session(engine) as session:
            rows = list(session.exec(select(MessageQueue)).all())
        assert len(rows) == 1
        assert rows[0].type == MessageType.HUMAN.value
