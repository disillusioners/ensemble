"""T2 / T1 / T4 / W1 — the Phase-1 test pins (completion-subsystem Phase 1).

Three small pins from the Phase-0 gate ledger:

* **T2** — the repo-level LIKE escape hardening in
  ``SQLModelInstanceRepository.find_by_instance_id_prefix``. The LIKE
  pattern is built from a caller-supplied string; ``%`` and ``_`` are
  LIKE WILDCARDS. The caller pre-validates the shape (hex digits +
  dashes), but the repository no longer relies on that single gate: it
  escapes the wildcards itself, so a wildcard-bearing prefix matches
  LITERALLY. The shape gate itself is pinned here too (the tool-layer
  contract) — the two layers are independent defenses.
* **T1** — the queued-enabler pin: reverting
  ``queued=ctx.task_id is not None`` must make the test go RED. The
  D4(iv) flag MIRRORS the Task creation; if it fell back to the
  dataclass default (``False``) every plain enqueue would report
  "queued=False" and the marker-guard prose would fire on every
  successful send.
* **T4** — the 35-char boundary label. The uuid-prefix shape gate
  accepts 8..35 chars (a full uuid is 36 — the EXACT path owns that).
  The boundary is pinned at both ends so a widening/narrowing is
  caught.

Plus the **W1 job-lane** pin — the PRODUCER half of the not-queued-reason
split (see ``TestW1JobLaneNotQueuedReason`` below).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
    _escape_like_pattern,
)
from daemon.services.instance_messaging import (
    AsyncMessageResult,
    _stamped_additional_kwargs,
)
from daemon.tools.instance import (
    _UUID_PREFIX_MAX_LEN,
    _UUID_PREFIX_MIN_LEN,
    _is_uuid_prefix_shape,
)


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite engine (the repo conventions recipe)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.message_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    eng = create_engine(
        f"sqlite:///{tmp_path / 'phase1-ledger-pins.sqlite'}",
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
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


def _seed(engine, instance_id: str, *, status: str = InstanceStatus.RUNNING.value) -> str:
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
    return instance_id


class TestT2LikeWildcardHardening:
    def test_a_percent_prefix_matches_literally(self, engine, repo):
        """``%`` must NOT behave as "match anything"."""
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        _seed(engine, "0badf00d-1111-2222-3333-444455556666")
        assert repo.find_by_instance_id_prefix("%", limit=10) == []

    def test_an_underscore_prefix_matches_literally(self, engine, repo):
        """``_`` must NOT behave as "any single character"."""
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        # An underscore-bearing id that a wildcard ``_`` would match.
        _seed(engine, "8ade5d22_1111-2222-3333-444455556666")
        hits = repo.find_by_instance_id_prefix("8ade5d22_", limit=10)
        assert hits == ["8ade5d22_1111-2222-3333-444455556666"]

    def test_a_wildcard_carrying_prefix_does_not_match_other_rows(
        self, engine, repo
    ):
        """``%ade5d22`` must not match the ``8ade5d22...`` row."""
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        assert repo.find_by_instance_id_prefix("%ade5d22", limit=10) == []

    def test_the_escape_helper_escapes_both_wildcards(self):
        assert _escape_like_pattern("%") == "\\%"
        assert _escape_like_pattern("_") == "\\_"
        # The escape character itself is escaped FIRST.
        assert _escape_like_pattern("\\") == "\\\\"
        # A plain uuid prefix is untouched.
        assert _escape_like_pattern("8ade5d22") == "8ade5d22"

    def test_the_shape_gate_rejects_wildcards(self):
        """THE SHAPE ASSERT — the tool-layer gate independently rejects
        a wildcard-bearing prefix (the second, independent defense)."""
        assert _is_uuid_prefix_shape("8ade5d22") is True
        assert _is_uuid_prefix_shape("%ade5d22") is False
        assert _is_uuid_prefix_shape("8ade5d2_") is False
        assert _is_uuid_prefix_shape("8ade5d22-1111-2222-3333-4444555") is True


class TestT4BoundaryLabels:
    def test_the_minimum_length_boundary(self):
        """8 chars is the shortest accepted prefix."""
        assert _UUID_PREFIX_MIN_LEN == 8
        assert _is_uuid_prefix_shape("8ade5d22") is True
        assert _is_uuid_prefix_shape("8ade5d2") is False

    def test_the_maximum_length_boundary(self):
        """35 chars is the longest accepted prefix — a full uuid is 36
        chars and the EXACT path owns that."""
        assert _UUID_PREFIX_MAX_LEN == 35
        at_max = ("8ade5d22" + "1" * 27)[:35]
        assert len(at_max) == 35
        assert _is_uuid_prefix_shape(at_max) is True
        over = (at_max + "1")[:36]
        assert len(over) == 36
        assert _is_uuid_prefix_shape(over) is False

    def test_a_dash_only_run_is_not_a_prefix(self):
        """At least the first uuid group's worth of hex digits."""
        assert _is_uuid_prefix_shape("-" * 10) is False


class TestT1QueuedEnabler:
    """The queued-enabler pin.

    The D4(iv) ``queued`` flag MIRRORS whether the PROCESS_MESSAGE Task
    row was created. Reverting it to the dataclass default (``False``)
    would make EVERY plain enqueue report ``queued=False`` — the
    marker-guard prose would fire on every successful send.
    """

    async def test_the_flag_mirrors_the_task_creation(self, engine):
        """A REAL enqueue on a live instance → the Task row exists →
        ``queued`` is True."""
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
        service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(is_shutting_down=False),
        )

        iid = "inst-queued-enabler"
        _seed(engine, iid)

        result = await service.enqueue_message(
            instance_id=iid,
            message="hello",
            source="api",
        )

        assert isinstance(result, AsyncMessageResult)
        assert result.queued is True
        assert result.not_queued_reason is None

    def test_the_dataclass_default_is_still_false(self):
        """The dataclass default is unchanged (``False``) — the
        enqueue lane always sets it explicitly."""
        bare = AsyncMessageResult(message_id="m", instance_id="i")
        assert bare.queued is False
        assert bare.not_queued_reason is None

    def test_a_stamped_source_is_unaffected_by_the_flag(self):
        """The D4(i) stamp and the queued flag are INDEPENDENT — a
        stamped (internal) source still reports ``queued=True`` when
        the Task row was created."""
        assert _stamped_additional_kwargs("internal_agent:x") == {
            "injected_message": True,
            "source": "internal_agent:x",
        }


class TestW1JobLaneNotQueuedReason:
    """W1 — the JOB-LANE half of the not-queued-reason split.

    The renderer pins (``tests/unit/tools/test_send_message_status_prose.py``)
    construct an ``AsyncMessageResult`` BY HAND and therefore prove only
    that the FORMATTER honours a reason — they prove NOTHING about the
    PRODUCER. A producer that never attached a reason would leave every
    renderer pin GREEN. That was the reviewer-council's W1 finding: the
    job lane returned ``queued=False`` with ``not_queued_reason=None``,
    so the public HTTP lane (``daemon/routers/messages.py`` calls
    ``manager.enqueue_message_job``) surfaced a not-queued result with no
    way to tell the marker-skip case (``deferred_pause_marker`` — the
    Task was SKIPPED, the target was NOT woken) from the refresh-fail
    case (``task_id_refresh_failed`` — the Task row EXISTS, the target
    WILL be woken).

    This pin drives the REAL job lane through the REAL deferred-pause
    marker branch and asserts the reason REACHED the result OBJECT.
    """

    async def test_the_job_lane_attaches_the_reason_to_the_result_object(
        self, engine, repo
    ):
        """ONE pin, two legs.

        1. **PRODUCER (behavioural)** — the marker branch produces
           ``deferred_pause_marker`` and it must land on
           ``AsyncMessageResult.not_queued_reason``. Dropping the
           threading at the marker exit turns this RED.
        2. **COVERAGE (structural)** — EVERY ``AsyncMessageResult`` the
           job lane builds must thread the reason. The job lane has TWO
           exits and only the marker exit can currently carry a non-None
           reason (both producers of a reason leave ``ctx.task_id`` None,
           so the second exit is defence-in-depth), which is why the
           "threaded at BOTH exits" invariant cannot be observed
           behaviourally and is pinned structurally.
        """
        import inspect
        from unittest.mock import AsyncMock, MagicMock

        from daemon.services.instance_messaging import InstanceMessagingService

        iid = "inst-w1-job-lane"
        _seed(engine, iid)

        def _service(*, marker: bool) -> InstanceMessagingService:
            manager = MagicMock()
            manager.engine = engine
            manager.write_guard = MagicMock()
            manager._live_hub = MagicMock()
            manager._live_hub.stream_status_change = AsyncMock()
            manager._worker_pool = MagicMock()
            # A REAL set — the marker guard does
            # ``instance_id in self._manager._deferred_question_pause``.
            manager._deferred_question_pause = {iid} if marker else set()
            # The REAL repository — the instance row is READ from the
            # live SQLite engine, not fabricated.
            manager._instance_repository = repo
            manager._job_queue_service = MagicMock()
            # No queue repository → the job lane takes its documented
            # "defaulting queued=False" capacity branch. That is the
            # QUEUE-CAPACITY axis, NOT the not-queued REASON axis.
            manager._job_queue_service._queue_repo = None
            manager._job_queue_service.enqueue = AsyncMock(return_value=None)
            manager._job_queue_service._repository = MagicMock()
            manager._job_queue_service._repository.stamp_message_id = MagicMock()
            return InstanceMessagingService(
                manager=manager,
                cancellation_service=MagicMock(is_shutting_down=False),
            )

        # ── Leg 1: PRODUCER — the marker branch ───────────────────────
        marked = _service(marker=True)
        result = await marked.enqueue_message_job(
            instance_id=iid,
            message="hello",
            source="api",
        )

        assert isinstance(result, AsyncMessageResult)
        # The marker guard SKIPPED the Task row → the target was NOT woken.
        assert result.queued is False
        # THE PRODUCER CONTRACT — the reason must be ON THE OBJECT.
        assert result.not_queued_reason == "deferred_pause_marker"

        # ── Leg 2: the normal exit still returns a well-formed result ─
        plain = _service(marker=False)
        ok = await plain.enqueue_message_job(
            instance_id=iid,
            message="hello",
            source="api",
        )

        assert isinstance(ok, AsyncMessageResult)
        assert ok.message_id
        # No marker → the Task row WAS created → there is NO reason.
        assert ok.not_queued_reason is None

        # ── Leg 3: COVERAGE — every job-lane exit threads the reason ──
        source = inspect.getsource(InstanceMessagingService.enqueue_message_job)
        result_builds = source.count("return AsyncMessageResult(")
        threadings = source.count("not_queued_reason=ctx.not_queued_reason")
        assert result_builds == 2, (
            "the job lane is expected to build the result object at two "
            f"exits, found {result_builds}"
        )
        assert threadings == result_builds, (
            "every job-lane result build must thread the not-queued reason "
            f"(builds={result_builds}, threadings={threadings})"
        )
