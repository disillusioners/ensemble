"""Unit tests for the chat-source lane predicate at the claim seam.

``TaskRepository.claim_pending_task(worker_id, lane)`` (chat-source-
worker-lane, D1/D2/B1 — Phase 1 Tasks #6-#7) routes claims by the
candidate row's ``message_queue.source`` prefix:

* ``lane="chat"`` — the row MUST carry a chat prefix
  (``CHAT_SOURCE_PREFIXES``; EXISTS required — strict, unconditional).
* ``lane="default"`` — the row must NOT carry a chat prefix, but ONLY
  when the B1 chat-lane-active flag is True (strict two-way). With the
  flag False (fail-open default) the NOT EXISTS clause is omitted and
  default-lane claims pick chat rows up exactly like the pre-lane
  code — nothing strands while the chat pool does not exist.

Harness (F7/F9 — project test discipline): REAL file-backed SQLite
(``tmp_path`` + ``NullPool`` — deliberately NOT StaticPool/:memory:;
the per-checkout-connection + WAL shape mirrors production
concurrency), with ``PRAGMA case_sensitive_like = ON`` issued on EVERY
connection via the engine-connect listener (with NullPool a one-shot
post-creation PRAGMA would not reach later connections — the listener
is the mechanism that makes the F9 parity guarantee real). This keeps
``source LIKE 'telegram:%'`` case-SENSITIVE here exactly as PostgreSQL
behaves in production.

Fixtures (F1 review pin): realistic production-shaped mint values —
``telegram:alice:1``, ``slack:U123:thread``, ``discord:guild-42:user-7``
(never bare ``"telegram:"`` — the registry mint site always appends
``:<external_user_id>``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.task.models  # noqa: F401 — register task tables
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
from daemon.constants import is_chat_source
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskType
from daemon.repositories.task.repository import (
    TaskRepository,
    is_chat_lane_active,
    set_chat_lane_active,
)
from daemon.services.timestamps import now_utc_naive
from tests.integration.chat_source_harness import (
    chat_lane_flag_reset_fixture,
)


# ---------------------------------------------------------------------------
# Fixtures — file-backed SQLite, NullPool, WAL, case_sensitive_like (F9)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database with per-connection PRAGMAs.

    ``case_sensitive_like = ON`` (F9) keeps the ``source LIKE
    'telegram:%'`` predicate case-SENSITIVE — matching production PG —
    so the case-variant pins (``TELEGRAM:foo`` is NOT chat) are honest.
    Issued in the connect listener because NullPool opens a fresh
    connection per checkout.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/claim_lane.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        # F9 — PG-parity for the lane predicate's LIKE semantics.
        cursor.execute("PRAGMA case_sensitive_like = ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def strict_lane():
    """Force the B1 flag ON for the duration of the test, restore the
    fail-open default afterwards (module-global state — never leak)."""
    set_chat_lane_active(True)
    yield
    set_chat_lane_active(False)


# Shared autouse lane-flag reset — the @pytest.fixture(autouse=True)
# decoration travels with the harness factory's returned object, so
# this single module-level assignment wires it for every test here.
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


# ---------------------------------------------------------------------------
# Seeding helpers — realistic production-shaped rows
# ---------------------------------------------------------------------------


_INSTANCE_SEQ = 0


def _seed_instance(eng: Engine, instance_id: str) -> None:
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="idle",
            )
        )
        s.commit()


def _seed_chat_task(
    eng: Engine,
    instance_id: str,
    source: str | None,
    created_at: datetime | None = None,
) -> str:
    """Insert a PENDING task + its backing message_queue row.

    Returns the Task ``work_id``. ``created_at`` is the claim FIFO key —
    pass distinct values when seeding multiple candidates.
    """
    message_id = f"msg-{uuid4().hex[:12]}"
    work_id = f"work-{uuid4().hex[:12]}"
    with Session(eng) as s:
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="hello from chat",
                source=source,
            )
        )
        s.add(
            Task(
                work_id=work_id,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status="pending",
                created_at=created_at or now_utc_naive(),
            )
        )
        s.commit()
    return work_id


def _claim(repo: TaskRepository, lane: str, worker_id: str = "probe-worker"):
    return repo.claim_pending_task(worker_id, lane=lane)


# ---------------------------------------------------------------------------
# Strict two-way isolation (flag True — chat pool live, D2)
# ---------------------------------------------------------------------------


class TestStrictTwoWay:
    """flag=True: chat rows are chat-lane-only, default rows are
    default-lane-only (D2 strict isolation, no overflow either way)."""

    def test_chat_prefix_row_claimable_by_chat_lane(self, engine, strict_lane):
        _seed_instance(engine, "inst-tg-1")
        work_id = _seed_chat_task(engine, "inst-tg-1", "telegram:alice:1")
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="chat", worker_id="chat-worker-0")

        assert claimed is not None
        assert claimed.work_id == work_id
        assert claimed.worker_id == "chat-worker-0"

    def test_chat_prefix_row_not_claimable_by_default_lane(
        self, engine, strict_lane
    ):
        _seed_instance(engine, "inst-tg-2")
        _seed_chat_task(engine, "inst-tg-2", "telegram:alice:1")
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="default", worker_id="worker-0")

        assert claimed is None

    def test_default_row_not_claimable_by_chat_lane(
        self, engine, strict_lane
    ):
        """Unconditional (flag-independent): the chat lane NEVER claims
        default-provenance rows — no chat-lane → default overflow."""
        _seed_instance(engine, "inst-agent-1")
        _seed_chat_task(engine, "inst-agent-1", "agent:ari")
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="chat", worker_id="chat-worker-0")

        assert claimed is None

    def test_default_row_claimable_by_default_lane(self, engine, strict_lane):
        _seed_instance(engine, "inst-agent-2")
        work_id = _seed_chat_task(engine, "inst-agent-2", "agent:ari")
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="default", worker_id="worker-0")

        assert claimed is not None
        assert claimed.work_id == work_id

    def test_default_lane_skips_chat_row_and_claims_next_default(
        self, engine, strict_lane
    ):
        """Two-way filter, not claim-poisoning: with a chat row OLDER
        than a default row, the default lane skips the chat row (never
        claims it) and claims the default row; the chat lane then still
        claims the chat row. (Distinct instances — the PRE-EXISTING
        one-Running-task-per-instance guard serializes same-instance
        claims (D4) and must not confound the lane assertion.)"""
        _seed_instance(engine, "inst-mix-1")
        _seed_instance(engine, "inst-mix-2")
        _seed_chat_task(
            engine,
            "inst-mix-1",
            "slack:U123:thread",
            created_at=datetime(2026, 9, 19, 10, 0, 0),
        )
        default_work = _seed_chat_task(
            engine,
            "inst-mix-2",
            "agent:ari",
            created_at=datetime(2026, 9, 19, 10, 1, 0),
        )
        repo = TaskRepository(engine)

        first = _claim(repo, lane="default", worker_id="worker-0")
        assert first is not None
        assert first.work_id == default_work  # chat row skipped, not blocked

        second = _claim(repo, lane="chat", worker_id="chat-worker-0")
        assert second is not None
        assert is_chat_source(second_message_source(engine, second.message_id))

    def test_all_three_chat_prefixes_route_to_chat_lane(
        self, engine, strict_lane
    ):
        """Each member of CHAT_SOURCE_PREFIXES (realistic fixtures, F1)
        is chat-lane claimable and default-lane invisible."""
        for i, source in enumerate(
            ["telegram:alice:1", "slack:U123:thread", "discord:guild-42:user-7"]
        ):
            _seed_instance(engine, f"inst-fam-{i}")
            work_id = _seed_chat_task(engine, f"inst-fam-{i}", source)
            repo = TaskRepository(engine)

            by_default = _claim(repo, lane="default", worker_id="worker-0")
            assert by_default is None, source

            by_chat = _claim(repo, lane="chat", worker_id="chat-worker-0")
            assert by_chat is not None, source
            assert by_chat.work_id == work_id, source


def second_message_source(engine: Engine, message_id: str | None) -> str | None:
    """Read a message_queue.source back (helper for lineage asserts)."""
    if message_id is None:
        return None
    with Session(engine) as s:
        row = s.get(MessageQueue, message_id)
        return row.source if row is not None else None


# ---------------------------------------------------------------------------
# B1 conditional fail-open (flag False — Phase-1-alone / post-teardown)
# ---------------------------------------------------------------------------


class TestB1ConditionalFailOpen:
    """flag=False: default lane claims chat rows (fail-open = today's
    behavior); strictness returns when the flag is set back True."""

    def test_default_lane_claims_chat_rows_when_flag_false(self, engine):
        assert is_chat_lane_active() is False  # module default
        _seed_instance(engine, "inst-fo-1")
        _seed_chat_task(engine, "inst-fo-1", "telegram:alice:1")
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="default", worker_id="worker-0")

        # Fail-open: the pre-lane behavior — nothing strands while the
        # chat pool does not exist (B1 three-state table, state 1).
        assert claimed is not None

    def test_chat_lane_stays_strict_when_flag_false(self, engine):
        """The EXISTS arm is UNCONDITIONAL — fail-open only widens the
        default lane, never the chat lane (chat lane still cannot take
        default rows)."""
        _seed_instance(engine, "inst-fo-2")
        _seed_chat_task(engine, "inst-fo-2", "agent:ari")
        repo = TaskRepository(engine)

        assert is_chat_lane_active() is False
        claimed = _claim(repo, lane="chat", worker_id="chat-worker-0")

        assert claimed is None

    def test_flag_true_restored_default_lane_excludes_again(
        self, engine
    ):
        """Set True → strict; set False → fail-open; set True → strict
        again. The flag is NOT a one-way boot latch (B1) — every flip
        is honored at the NEXT claim (per-claim read, E2)."""
        _seed_instance(engine, "inst-fo-3")
        work_id = _seed_chat_task(engine, "inst-fo-3", "telegram:alice:1")
        repo = TaskRepository(engine)

        set_chat_lane_active(True)
        assert _claim(repo, lane="default", worker_id="worker-0") is None

        set_chat_lane_active(False)
        reopened = _claim(repo, lane="default", worker_id="worker-0")
        assert reopened is not None
        assert reopened.work_id == work_id

        set_chat_lane_active(True)
        _seed_instance(engine, "inst-fo-4")
        _seed_chat_task(engine, "inst-fo-4", "slack:U123:thread")
        assert _claim(repo, lane="default", worker_id="worker-0") is None


# ---------------------------------------------------------------------------
# Classification edges — NULL / non-chat prefixes / case variants
# ---------------------------------------------------------------------------


class TestLaneClassificationEdges:
    """Rows that are NOT chat-provenanced belong to the default lane
    (with the flag active, they are default-only; the chat lane never
    sees them)."""

    @pytest.mark.parametrize(
        "source",
        [
            None,  # NULL source → default lane (mint never NULLs, but the column allows it)
            "agent:ari",  # internal fan-out (D3 non-inheritance)
            "internal_agent:ari",
            "api",
            "scheduler",
            "webhook:gh-hook",  # 3-vs-5 asymmetry — excluded from the chat lane (A7.3)
            "whatsapp:1234",  # asymmetry pin
            "TELEGRAM:foo",  # case-variant — case-SENSITIVE predicate (F9)
            "Slack:U123",
            "telegramX:not-a-prefix",  # colon-boundary near-miss
        ],
    )
    def test_non_chat_sources_stay_on_default_lane(
        self, engine, strict_lane, source
    ):
        _seed_instance(engine, "inst-edge")
        work_id = _seed_chat_task(engine, "inst-edge", source)
        repo = TaskRepository(engine)

        by_chat = _claim(repo, lane="chat", worker_id="chat-worker-0")
        assert by_chat is None, source

        by_default = _claim(repo, lane="default", worker_id="worker-0")
        assert by_default is not None, source
        assert by_default.work_id == work_id, source

    def test_case_variant_not_claimed_by_chat_lane_case_pragma_honest(
        self, engine, strict_lane
    ):
        """Explicit case-sensitivity proof: ``TELEGRAM:foo`` is NOT a
        chat source under the harness PRAGMA (case_sensitive_like=ON)
        — if the PRAGMA were missing this test would fail (SQLite
        default is case-INSENSITIVE), keeping the F9 parity honest."""
        _seed_instance(engine, "inst-case-1")
        _seed_chat_task(engine, "inst-case-1", "TELEGRAM:foo")
        repo = TaskRepository(engine)

        assert _claim(repo, lane="chat", worker_id="chat-worker-0") is None

    def test_null_source_routes_to_default(self, engine, strict_lane):
        _seed_instance(engine, "inst-null-1")
        work_id = _seed_chat_task(engine, "inst-null-1", None)
        repo = TaskRepository(engine)

        claimed = _claim(repo, lane="default", worker_id="worker-0")

        assert claimed is not None
        assert claimed.work_id == work_id


# ---------------------------------------------------------------------------
# Signature contract
# ---------------------------------------------------------------------------


class TestLaneSignatureContract:
    """Shared contract (phase1 §Coupling): the lane defaults to
    "default" and unknown lanes fail LOUD."""

    def test_default_lane_kwarg_preserves_caller_compatibility(self, engine):
        """Calling WITHOUT ``lane`` (every pre-lane caller, e.g.
        ``TaskProcessor.claim_task(worker_id)``) behaves identically to
        today — no lane filtering with the flag at its fail-open
        default."""
        _seed_instance(engine, "inst-sig-1")
        work_id = _seed_chat_task(engine, "inst-sig-1", "telegram:alice:1")
        repo = TaskRepository(engine)

        claimed = repo.claim_pending_task("worker-0")

        assert claimed is not None
        assert claimed.work_id == work_id

    def test_unknown_lane_raises_value_error(self, engine):
        """A typo'd lane must fail loud, not silently claim like
        "default" (misroute-with-zero-signal is the failure class this
        seam exists to close)."""
        repo = TaskRepository(engine)
        with pytest.raises(ValueError, match="lane"):
            repo.claim_pending_task("worker-0", lane="chatt")

    def test_flag_transport_is_shared_module_state_not_per_instance(
        self, engine
    ):
        """E2: TWO separately-constructed TaskRepository instances (the
        manager constructs separate ones for discard_on_startup and
        on_pending_task) read the SAME flag value — per-claim, never a
        construction-time snapshot."""
        _seed_instance(engine, "inst-shared-1")
        _seed_chat_task(engine, "inst-shared-1", "telegram:alice:1")
        repo_a = TaskRepository(engine)
        repo_b = TaskRepository(engine)

        set_chat_lane_active(True)
        # repo_a was constructed BEFORE the flip, repo_b AFTER — both
        # must see the strict semantics (no construction-time copy).
        assert repo_a.claim_pending_task("worker-0", lane="default") is None
        assert repo_b.claim_pending_task("worker-1", lane="default") is None
        assert is_chat_lane_active() is True
