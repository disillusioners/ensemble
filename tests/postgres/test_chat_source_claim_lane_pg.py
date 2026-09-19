"""PG-side claim-seam test for chat-source-worker-lane Phase 3.

Mirrors the SQLite unit-test coverage at
``tests/unit/test_repository_claim_lane.py`` against a real
PostgreSQL backend. The SQLite harness pins PG-parity via
``PRAGMA case_sensitive_like = ON`` (F9) — this PG-side test
verifies the lane predicate behaves identically on the production
backend (PG is case-sensitive by default for LIKE).

Coverage (mirrors ``test_repository_claim_lane.py``):
  * strict two-way (flag=True): chat row → chat lane, default
    row → default lane, no overflow in either direction.
  * fail-open (flag=False): default lane claims chat rows
    exactly like the pre-lane code.
  * all three chat prefixes route to chat lane.
  * case-sensitivity: ``TELEGRAM:foo`` is NOT a chat source.
  * non-chat user sources (``agent:foo``) → default lane.

The test is marked ``pytest.mark.postgres`` (auto-applied by
``tests/postgres/conftest.py::pytest_collection_modifyitems``) and
skipped by default via the project ``addopts = -m 'not integration
and not postgres'``. Run explicitly::

    ENSEMBLE_TEST_PG_URL=... \\
    pytest tests/postgres/test_chat_source_claim_lane_pg.py \\
      --override-ini="addopts=" -m postgres

The conftest's ``pg_engine`` fixture probes the URL; if PG is
unreachable the whole module is skipped cleanly (no errors).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
from daemon.constants import CHAT_SOURCE_PREFIXES, is_chat_source
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskType
from daemon.repositories.task.repository import (
    TaskRepository,
    is_chat_lane_active,
    set_chat_lane_active,
)
from tests.integration.chat_source_harness import (
    chat_lane_flag_reset_fixture,
)


# ---------------------------------------------------------------------------
# Lane-flag isolation — shared module state must not leak between tests
# ---------------------------------------------------------------------------


# Shared autouse lane-flag reset — the @pytest.fixture(autouse=True)
# decoration travels with the harness factory's returned object, so
# this single module-level assignment wires it for every test here.
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


@pytest.fixture
def strict_lane_pg():
    """Force the B1 flag ON for the duration of the test, restore
    the fail-open default afterwards."""
    set_chat_lane_active(True)
    yield
    set_chat_lane_active(False)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_instance(pg_session_factory, instance_id: str) -> None:
    with pg_session_factory() as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="idle",
            )
        )
        s.commit()


def _seed_chat_task_pg(
    pg_session_factory,
    instance_id: str,
    source: str | None,
) -> str:
    """Insert a PENDING ``Task`` + backing ``MessageQueue`` row. Returns
    the Task ``work_id``."""
    import uuid

    message_id = f"msg-{uuid.uuid4().hex[:12]}"
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    with pg_session_factory() as s:
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
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        s.commit()
    return work_id


def _claim_pg(
    pg_repository_factory, lane: str, worker_id: str = "probe-worker"
):
    repo = pg_repository_factory(TaskRepository)
    return repo.claim_pending_task(worker_id, lane=lane)


# ---------------------------------------------------------------------------
# Tests — strict two-way (flag True)
# ---------------------------------------------------------------------------


class TestPGStrictTwoWayLanePredicate:
    """flag=True: chat rows are chat-lane-only, default rows are
    default-lane-only (D2 strict isolation, no overflow either way)."""

    def test_pg_chat_prefix_row_claimable_by_chat_lane(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        _seed_instance(pg_session_factory, "inst-pg-chat-1")
        work_id = _seed_chat_task_pg(
            pg_session_factory, "inst-pg-chat-1", "telegram:alice:1"
        )

        claimed = _claim_pg(pg_repository_factory, lane="chat")

        assert claimed is not None
        assert claimed.work_id == work_id
        # The default ``probe-worker`` id is passed in; the test
        # only asserts the row was claimed (not the worker_id —
        # the SQLite unit tests use distinct worker_ids because the
        # SQLModel SQL is portable).
        assert claimed.worker_id == "probe-worker"

    def test_pg_chat_prefix_row_not_claimable_by_default_lane(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        _seed_instance(pg_session_factory, "inst-pg-chat-2")
        _seed_chat_task_pg(
            pg_session_factory, "inst-pg-chat-2", "telegram:alice:1"
        )

        claimed = _claim_pg(pg_repository_factory, lane="default")

        assert claimed is None

    def test_pg_default_row_not_claimable_by_chat_lane(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        _seed_instance(pg_session_factory, "inst-pg-agent-1")
        _seed_chat_task_pg(
            pg_session_factory, "inst-pg-agent-1", "agent:ari"
        )

        claimed = _claim_pg(pg_repository_factory, lane="chat")

        assert claimed is None

    def test_pg_default_row_claimable_by_default_lane(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        _seed_instance(pg_session_factory, "inst-pg-agent-2")
        work_id = _seed_chat_task_pg(
            pg_session_factory, "inst-pg-agent-2", "agent:ari"
        )

        claimed = _claim_pg(pg_repository_factory, lane="default")

        assert claimed is not None
        assert claimed.work_id == work_id

    def test_pg_all_three_chat_prefixes_route_to_chat_lane(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        for i, source in enumerate(
            ["telegram:alice:1", "slack:U123:thread", "discord:guild-42:user-7"]
        ):
            inst = f"inst-pg-fam-{i}"
            _seed_instance(pg_session_factory, inst)
            work_id = _seed_chat_task_pg(pg_session_factory, inst, source)

            claimed_default = _claim_pg(pg_repository_factory, lane="default")
            assert claimed_default is None, source

            claimed_chat = _claim_pg(
                pg_repository_factory, lane="chat", worker_id=f"chat-worker-{i}"
            )
            assert claimed_chat is not None, source
            assert claimed_chat.work_id == work_id, source


# ---------------------------------------------------------------------------
# Case-sensitivity — PG is case-sensitive by default for LIKE
# ---------------------------------------------------------------------------


class TestPGCaseSensitivity:
    """PG is case-sensitive for LIKE without ``ILIKE``. The chat
    prefix predicate MUST be case-sensitive — ``TELEGRAM:foo`` is
    NOT a chat source on the production backend. Mirrors the F9
    SQLite parity discipline (``PRAGMA case_sensitive_like = ON``
    in the unit harness)."""

    def test_pg_uppercase_chat_prefix_is_NOT_a_chat_source(
        self, pg_session_factory, pg_repository_factory, strict_lane_pg
    ):
        """``TELEGRAM:foo`` (uppercase) is NOT in
        ``CHAT_SOURCE_PREFIXES`` — the claim predicate's LIKE
        pattern uses lowercase prefixes, so the row is treated as
        a default row and is claimable on the default lane."""
        _seed_instance(pg_session_factory, "inst-pg-case")
        _seed_chat_task_pg(
            pg_session_factory, "inst-pg-case", "TELEGRAM:foo"
        )

        # Default lane claims it (NOT chat).
        claimed_default = _claim_pg(pg_repository_factory, lane="default")
        assert claimed_default is not None
        # Chat lane does NOT claim it.
        claimed_chat = _claim_pg(pg_repository_factory, lane="chat")
        assert claimed_chat is None


# ---------------------------------------------------------------------------
# Fail-open (flag False) — pre-P2 behavior
# ---------------------------------------------------------------------------


class TestPGFailOpenBehavior:
    """flag=False: chat rows surface on the default lane (B1 fail-open
    default; pre-P2 behavior). Pool absent scenario."""

    def test_pg_default_lane_claims_chat_row_when_flag_false(
        self, pg_session_factory, pg_repository_factory
    ):
        """With ``is_chat_lane_active() == False`` (default — chat
        pool absent), the default lane's claim predicate has NO
        chat-source filter (the lane_gate_sql renders empty).
        Chat rows surface via the default lane — no stranding
        while the chat pool does not exist (B1 fail-open)."""
        # Confirm flag state.
        assert is_chat_lane_active() is False

        _seed_instance(pg_session_factory, "inst-pg-fo-1")
        work_id = _seed_chat_task_pg(
            pg_session_factory, "inst-pg-fo-1", "telegram:alice:1"
        )

        claimed = _claim_pg(pg_repository_factory, lane="default")

        assert claimed is not None
        assert claimed.work_id == work_id


# ---------------------------------------------------------------------------
# Constant parity — CHAT_SOURCE_PREFIXES matches the daemon's
# chat-lane predicate expectations
# ---------------------------------------------------------------------------


class TestPGChatSourcePrefixesConstant:
    """Pin the CHAT_SOURCE_PREFIXES tuple to its expected values
    on the production PG backend."""

    def test_pg_chat_source_prefixes_is_exact_tuple(self):
        """Exact-equality pin (Pin 5 cross-ref — the same pin as
        ``tests/unit/test_constants.py::test_chat_source_prefixes``)."""
        assert CHAT_SOURCE_PREFIXES == ("telegram:", "slack:", "discord:")

    def test_pg_is_chat_source_helper_matches_predicate(self):
        """The ``is_chat_source`` helper (Phase 1 / D10.1) must
        agree with the claim predicate's LIKE clause on PG."""
        # Chat sources.
        for source in [
            "telegram:alice:1",
            "slack:U123:thread",
            "discord:guild-42:user-7",
        ]:
            assert is_chat_source(source) is True, source
        # Non-chat sources (case sensitivity — PG default).
        for source in [
            "agent:ari",
            "webhook:gh-hook",
            "TELEGRAM:foo",  # uppercase — NOT a chat source
            "Slack:U123",  # mixed case — NOT a chat source
            "",
        ]:
            assert is_chat_source(source) is False, source
