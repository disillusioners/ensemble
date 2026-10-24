"""NEW(iv) — the never-stamped source guard-rails.

SOURCE U (the "last real user message" anchor) is a heuristic: the
enqueue-lane stamp (``injected_message=True``) is what tells the
attestation scanner that a delivered message was NOT user-authored. The
stamp set is therefore load-bearing for the anchor.

Phase 0 adds NO stamp-set change (the widening is D4(i), Phase 1) — but
it DOES pin the negative half of the contract, so the Phase-1 widening
can never swallow a genuine user lane:

* ``api``                — the HTTP chat path.
* the chat source types — ``telegram`` / ``slack`` / ``discord`` /
  ``whatsapp`` (``USER_ORIGIN_CHAT_SOURCE_TYPES``).
* ``api_resume_fallback`` — the resume path.

These are NEVER stamped: they carry no ``additional_kwargs`` marker and
they derive ``msg_type=HUMAN`` (the Phase-1 stamp table lists them as
"NEVER / HUMAN / anchors by design").

The pin is a guard-rail, not a description of today's set: the
constants are asserted directly, so a future widening that adds one of
these prefixes FAILS this test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageType
from daemon.services.instance_messaging import (
    _INTERNAL_STAMPED_SOURCE_PREFIXES,
    _stamped_additional_kwargs,
    InstanceMessagingService,
)
from daemon.tools.upgrade_journal import USER_ORIGIN_CHAT_SOURCE_TYPES


_NEVER_STAMPED_EXACT = ("api", "api_resume_fallback")
_NEVER_STAMPED_CHAT = tuple(sorted(USER_ORIGIN_CHAT_SOURCE_TYPES))
# The chat lane's concrete source values carry the external user id
# (``telegram:alice:1`` — ``daemon/sources/registry.py:857``), so the
# colon-family form is pinned too.
_NEVER_STAMPED_CHAT_PREFIXED = ("telegram:", "slack:", "discord:")


def _all_never_stamped() -> tuple[str, ...]:
    return (
        _NEVER_STAMPED_EXACT
        + _NEVER_STAMPED_CHAT
        + _NEVER_STAMPED_CHAT_PREFIXED
    )


@pytest.fixture
def engine():
    """In-memory SQLite engine with the tables the enqueue lane writes.

    Mirrors the engine pattern of ``tests/services/conftest.py``
    (StaticPool + FK enforcement + ``SQLModel.metadata.create_all``).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_fk_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    import daemon.repositories.event.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


class TestNeverStampedSources:
    @pytest.mark.parametrize("source", _all_never_stamped())
    def test_the_source_is_never_stamped(self, source):
        """No ``injected_message`` marker — the message stays BARE, so
        the anchor treats it as a real user message."""
        assert _stamped_additional_kwargs(source) is None

    @pytest.mark.parametrize("source", _all_never_stamped())
    def test_the_source_is_not_in_the_stamp_set(self, source):
        """Structural pin — the stamp constant must not grow to cover a
        genuine user lane."""
        assert source not in _INTERNAL_STAMPED_SOURCE_PREFIXES
        for prefix in _INTERNAL_STAMPED_SOURCE_PREFIXES:
            assert not source.startswith(prefix)

    @pytest.mark.parametrize("source", _all_never_stamped())
    def test_the_source_derives_the_human_msg_type(self, source):
        """``msg_type`` stays HUMAN (the stamp table's "NEVER / HUMAN"
        row) — the enqueue lane classifies a source as AGENT / SYSTEM
        only when it is one of the internal prefixes."""
        from daemon.services.instance_messaging import MessageType as _MT

        assert _MT.HUMAN.value == "human"
        # The classification itself is pinned behaviourally below (the
        # persisted row's ``type``); this assertion documents the
        # expected value the row must carry.
        assert MessageType.HUMAN.value == "human"


@pytest.fixture
def messaging_service(engine) -> InstanceMessagingService:
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


class TestNeverStampedSourcesDeriveHuman:
    @pytest.mark.parametrize("source", _all_never_stamped())
    async def test_the_persisted_row_is_a_human_message(
        self, engine, messaging_service, source
    ):
        """The enqueue lane persists the message as a HUMAN row — the
        never-stamped sources never become AGENT / SYSTEM traffic."""
        instance_id = f"inst-{source.replace(':', '-')}"
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
        assert rows[0].source == source
