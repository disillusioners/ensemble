"""Diagnostic tests for JobFeedbackObserver post-restart empty-queue anomaly.

Anomaly (2026-08 incident): after daemon restart, the observer processed 0
events while ``instance_lifecycle`` events continued to be persisted to the
events table. The EventBus subscriber queue (created by
``JobFeedbackObserver.start()`` via ``EventBus.subscribe_all``) does not
appear to receive the published events, so the observer's ``_event_loop``
times out and logs ``no events in 600s`` repeatedly.

These tests target the EventBus broadcast mechanism itself, not the
observer's downstream processing — i.e. the lowest-leverage spot where
the bug could live.

Each test is independent and stand-alone (per project convention).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.event.models import EventKind
from daemon.repositories.event.repository import EventRepository
from daemon.services.event_bus import EventBus
from daemon.services.job_feedback_observer import JobFeedbackObserver


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine. File-backed per project convention
    (StaticPool corrupts writes inside one open transaction)."""
    from tempfile import NamedTemporaryFile
    tmp = NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def event_repo(engine):
    return EventRepository(engine=engine)


@pytest.fixture
def event_bus(event_repo):
    """A real ``EventBus`` instance backed by a real (file-backed) SQLite
    EventRepository — no mocks. Mocks would mask the exact mechanism we
    want to investigate."""
    return EventBus(event_repo=event_repo)


@pytest.fixture
def instance_id():
    return str(uuid.uuid4())


# ──────────────────────────────────────────────────────────────────────
# Diagnostic — Hypothesis A: create_event broadcasts to subscribed queue
# ──────────────────────────────────────────────────────────────────────


class TestCreateEventBroadcastsToSubscribers:
    """Verify the full EventBus create_event → broadcast_to_global path.

    The anomaly has two observable consequences:
        1. Events are persisted in the ``events`` table (CONFIRMED by ops).
        2. Events are NOT consumed by the subscriber queue returned by
           ``subscribe_all`` — observed by the observer's ``events_processed == 0``
           counter and the repeated ``no events in 600s`` log.

    If hypothesis A holds, every ``create_event`` call after ``subscribe_all``
    places a dict on the subscriber's queue with ``event_type='instance_lifecycle'``.
    """

    @pytest.mark.asyncio
    async def test_create_event_places_event_on_subscribed_queue(
        self, event_bus, instance_id
    ):
        """After subscribe_all, create_event must enqueue an item the consumer can ``get()``."""
        queue = event_bus.subscribe_all("job_feedback_observer")

        # Pre-condition: queue empty.
        assert queue.empty(), "subscriber queue must start empty"

        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "completed"},
        )

        # Post-condition: exactly one item in the queue.
        assert not queue.empty(), (
            "BUG: subscriber queue still empty after create_event — broadcast "
            "did not reach the registered subscriber. This is the exact "
            "shape of the observed anomaly."
        )

        item = queue.get_nowait()
        assert item["event_type"] == "instance_lifecycle", (
            f"event_type mismatch: {item.get('event_type')!r}"
        )
        assert item["instance_id"] == instance_id
        assert item["data"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_create_event_broadcasts_to_each_subscriber(
        self, event_bus, instance_id
    ):
        """Each registered subscriber receives the event."""
        q1 = event_bus.subscribe_all("subscriber-A")
        q2 = event_bus.subscribe_all("subscriber-B")

        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "error", "error": "boom"},
        )

        # Both queues must receive the event.
        a_event = q1.get_nowait()
        b_event = q2.get_nowait()

        assert a_event["event_type"] == "instance_lifecycle"
        assert b_event["event_type"] == "instance_lifecycle"
        assert a_event["data"]["status"] == "error"
        assert b_event["data"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_subscribers_means_no_broadcast_target(
        self, event_bus, instance_id, event_repo
    ):
        """If no subscriber is registered, ``create_event`` must still persist —
        persistence is the canonical side-effect; broadcast is only a best-
        effort fan-out. This pins the documented invariant."""
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "completed"},
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.INSTANCE_LIFECYCLE.value

    @pytest.mark.asyncio
    async def test_create_event_with_string_kind_broadcasts(
        self, event_bus, instance_id
    ):
        """``kind`` accepts both ``EventKind`` enum and string — must broadcast either way."""
        queue = event_bus.subscribe_all("subscriber-str")

        await event_bus.create_event(
            instance_id=instance_id,
            kind="instance_lifecycle",
            data={"instance_id": instance_id, "status": "completed"},
        )

        assert not queue.empty(), (
            "string kind must also broadcast; if this fails, callers that "
            "pass kind as a string are silently dropping events."
        )
        item = queue.get_nowait()
        assert item["event_type"] == "instance_lifecycle"


# ──────────────────────────────────────────────────────────────────────
# Diagnostic — Hypothesis B: Restart semantics — re-subscribe after restart
# ──────────────────────────────────────────────────────────────────────


class TestRestartResubscribe:
    """Simulate the restart flow: the EventBus instance is recreated and
    a new subscriber registers via ``subscribe_all``. Verify broadcasts
    reach the NEW subscriber (not the orphan old queue)."""

    @pytest.mark.asyncio
    async def test_new_bus_after_restart_receives_events(self, event_repo, instance_id):
        """Restart = new EventBus. New subscriber must receive events published
        via the NEW bus. (We can't easily simulate in-process restart, so we
        use two distinct EventBus instances back-to-back.)"""
        first_bus = EventBus(event_repo=event_repo)
        first_queue = first_bus.subscribe_all("job_feedback_observer")
        await first_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "completed"},
        )
        # First bus queues the event.
        assert not first_queue.empty()

        # Simulate restart: new bus, new subscriber.
        second_bus = EventBus(event_repo=event_repo)
        second_queue = second_bus.subscribe_all("job_feedback_observer")
        await second_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "error"},
        )
        # Second bus queue must receive the event.
        item = second_queue.get_nowait()
        assert item["data"]["status"] == "error"


# ──────────────────────────────────────────────────────────────────────
# Diagnostic — Hypothesis C: Consecutive start/stop cycles re-register queue
# ──────────────────────────────────────────────────────────────────────


class TestSubscribeTwiceReplacesQueue:
    """Calling subscribe_all twice with the same id REPLACES the
    internally-held queue (line 306 of event_bus.py:
    ``self._global_subscribers[subscriber_id] = queue``) and returns the
    NEW queue.

    A caller that holds onto the OLD queue reference (the observer's
    ``self._queue``) would see an orphaned queue. This is a real risk
    during startup races (restart where start() runs twice)."""

    @pytest.mark.asyncio
    async def test_subscribe_all_twice_returns_a_new_queue(self, event_bus):
        q1 = event_bus.subscribe_all("subscriber-X")
        q2 = event_bus.subscribe_all("subscriber-X")
        # Second call replaces the entry in _global_subscribers.
        assert q1 is not q2, (
            "Two subscribe_all() calls with the same id must yield "
            "distinct queues — otherwise the internal substitution "
            "in subscribe_all line 306 silently orphans callers."
        )

    @pytest.mark.asyncio
    async def test_orphaned_queue_does_not_receive_subsequent_events(
        self, event_bus, instance_id
    ):
        """If the observer's ``self._queue`` is the OLD queue (already
        replaced inside the bus), the new publish lands on the NEW queue.
        The OLD queue never sees the event.

        This is the asymmetric failure pattern: side-effect (persistence)
        succeeds, but the original holder of the old queue gets nothing.
        """
        old_queue = event_bus.subscribe_all("subscriber-orphan")
        new_queue = event_bus.subscribe_all("subscriber-orphan")  # replaces inside

        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_LIFECYCLE,
            data={"instance_id": instance_id, "status": "completed"},
        )

        # New queue gets the event.
        assert not new_queue.empty(), (
            "Replacement subscription must still receive the broadcast — "
            "otherwise restart-time re-subscribe silently drops events."
        )
        # OLD queue is orphaned and stays empty.
        assert old_queue.empty(), (
            "BUG: orphaned queue received the event, but ``_global_subscribers`` "
            "should now hold the NEW queue reference."
        )


# ──────────────────────────────────────────────────────────────────────
# Diagnostic — Hypothesis D: The full JobFeedbackObserver.start() ↔
#                              EventBus.create_event() pairing
# ──────────────────────────────────────────────────────────────────────


class TestJobFeedbackObserverWithRealEventBus:
    """End-to-end: a real ``JobFeedbackObserver`` subscribes to a real
    EventBus (no mocks). Verifies the diagnostic question directly:
    after ``start()``, do events that traverse ``create_event(..., kind=
    INSTANCE_LIFECYCLE, ...)`` land in ``observer._queue``?

    Mocks would mask any subtle reference-graph disconnect (``MagicMock``
    returns deterministic objects that survive identically). Real objects
    surface the actual queue binding.
    """

    @pytest.mark.asyncio
    async def test_start_then_publish_event_lands_in_observer_queue(
        self, event_repo, instance_id
    ):
        """The exact symptom: observer subscribes via start(); events
        arrive via the production publish path; queue must receive them."""
        event_bus = EventBus(event_repo=event_repo)

        observer = JobFeedbackObserver(
            event_bus=event_bus,
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        await observer.start()
        try:
            assert observer._queue is not None
            assert observer._queue.empty()

            # Publish via the SAME path the production publisher uses
            # (event_publisher._publish_instance_lifecycle_event → bus.create_event).
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_LIFECYCLE,
                data={"instance_id": instance_id, "status": "completed"},
            )

            # The observer's queue MUST have received the event.
            assert not observer._queue.empty(), (
                "BUG: observer queue empty after create_event — this is "
                "the exact shape of the post-restart empty-queue anomaly."
            )
            item = observer._queue.get_nowait()
            assert item["event_type"] == "instance_lifecycle"
            assert item["data"]["status"] == "completed"
        finally:
            await observer.stop()

    @pytest.mark.asyncio
    async def test_start_stop_start_then_publish_lands_in_latest_queue(
        self, event_repo, instance_id
    ):
        """Restart pattern: start → stop → start again. The LATEST observer
        instance's queue must receive events.

        This is the most literal simulation of the production restart cycle.
        """
        event_bus = EventBus(event_repo=event_repo)

        first_observer = JobFeedbackObserver(
            event_bus=event_bus,
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        await first_observer.start()
        first_queue = first_observer._queue
        await first_observer.stop()

        # Subscribe again with a NEW observer instance.
        second_observer = JobFeedbackObserver(
            event_bus=event_bus,
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        await second_observer.start()
        try:
            second_queue = second_observer._queue
            assert second_queue is not first_queue, (
                "Sanity check: restart must yield a fresh queue reference."
            )

            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_LIFECYCLE,
                data={"instance_id": instance_id, "status": "completed"},
            )

            assert not second_queue.empty(), (
                "BUG: post-restart second observer's queue empty. "
                "Either EventBus lost its subscriber dict, or the new "
                "queue isn't in `_global_subscribers`."
            )
            assert first_queue.empty(), (
                "Sanity check: orphaned first queue must NOT receive events."
            )
        finally:
            await second_observer.stop()
