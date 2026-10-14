"""Phase B T5 endpoint-12 (audit list) — repo-level filter matrix tests.

Drives the new :meth:`PromptVersionRepository.list_audit_events`
method through its full filter surface against a real file-SQLite
engine. The repo's indexes are query-friendly by design (Phase A —
``idx_prompt_audit_events_key_created`` / ``_version`` / ``_type``)
so the filter matrix exercises both the simple and the
multi-clause WHERE shapes.

Coverage:

* Empty result + total = 0
* Single-column filter (agent_id, version_tag, artifact,
  version_id, event_type, actor_subject)
* Multi-column combined AND filter
* Pagination (limit / offset) + total stays unfiltered-by-pagination
* Newest-first ordering (created_at DESC)
* ``version_tag=None`` and ``version_tag=""`` normalize identically
  (F9 / A7 contract)

A minimal ``ActorContext`` fixture mints events through the
``create_and_submit`` composite (the most common producer) so the
test rows match what production sees — no hand-stamped audit rows
out of band. The repo methods are SYNC (per the project's standard
pattern — the service facade is the async layer); the tests call
them directly.

Auth-OFF posture is covered by ``tests/test_prompts_router_auth_off.py``;
the router-level end-to-end with the mocked-manager seam is covered
in ``tests/test_prompts_router.py::TestAuditEndpoint``.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import SQLModel

from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_INTERNAL,
    ActorContext,
    PromptVersionRepository,
    actor_from_instance,
)


# ─────────────────────────────────────────────────────────────────────────────
# SQLite engine fixture — file-per-test under ``tempfile.mktemp``
# (FAKE_HOME escape-guard; never live-shape).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    yield path
    for ext in ("", "-wal", "-shm"):
        try:
            os.unlink(path + ext)
        except FileNotFoundError:
            pass


@pytest.fixture
def engine(db_path):
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> PromptVersionRepository:
    return PromptVersionRepository(engine)


# ─────────────────────────────────────────────────────────────────────────────
# Actor fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def human_actor() -> ActorContext:
    """HTTP session lane — human operator."""
    return ActorContext(subject="op@example.com", auth_method="session")


@pytest.fixture
def internal_actor() -> ActorContext:
    """In-process submitter — the row's ``actor_subject`` = the
    instance id (D10 internal lane)."""
    return actor_from_instance("inst-1", "developer", "")


# ─────────────────────────────────────────────────────────────────────────────
# Seed helpers
# ─────────────────────────────────────────────────────────────────────────────


def _seed_one(
    repo: PromptVersionRepository,
    *,
    agent_id: str = "developer",
    version_tag: str = "",
    artifact: str = "soul",
    actor: ActorContext,
) -> PromptVersion:
    """Insert one prompt-version row + its audit trail (CREATED +
    SUBMITTED). Returns the inserted :class:`PromptVersion`."""
    return repo.create_and_submit(
        agent_id, version_tag, artifact, f"content-{artifact}",
        actor,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestListAuditEventsEmpty:
    def test_empty_db_returns_zero(self, repo):
        items, total = repo.list_audit_events()
        assert items == []
        assert total == 0


class TestListAuditEventsFilters:
    def test_filter_by_agent_id(self, repo, internal_actor):
        # developer (1 row × 2 audit events) + writer (1 row × 2).
        _seed_one(repo, agent_id="developer", artifact="soul", actor=internal_actor)
        _seed_one(repo, agent_id="developer", artifact="rule", actor=internal_actor)
        _seed_one(repo, agent_id="writer", artifact="soul", actor=internal_actor)

        items, total = repo.list_audit_events(agent_id="developer")
        assert total == 4  # 2 rows × 2 events (CREATED + SUBMITTED)
        assert len(items) == 4
        # Every returned row's agent_id matches.
        for r in items:
            assert r.agent_id == "developer"

    def test_filter_by_version_tag_normalizes_none_to_empty(
        self, repo, internal_actor
    ):
        """F9 / A7 contract — ``None`` and ``""`` both normalize to
        ``""`` so the query semantics are stable across drivers."""
        _seed_one(repo, agent_id="developer", version_tag="", actor=internal_actor)

        items_none, total_none = repo.list_audit_events(
            agent_id="developer", version_tag=None,
        )
        items_empty, total_empty = repo.list_audit_events(
            agent_id="developer", version_tag="",
        )
        assert total_none == total_empty
        assert len(items_none) == len(items_empty)

    def test_filter_by_artifact(self, repo, internal_actor):
        _seed_one(repo, agent_id="developer", artifact="soul", actor=internal_actor)
        _seed_one(repo, agent_id="developer", artifact="rule", actor=internal_actor)
        items, total = repo.list_audit_events(
            agent_id="developer", artifact="soul",
        )
        assert total == 2  # 1 row × 2 audit events
        for r in items:
            assert r.artifact == "soul"

    def test_filter_by_event_type(self, repo, internal_actor):
        _seed_one(repo, agent_id="developer", actor=internal_actor)
        # ``create_and_submit`` emits CREATED + SUBMITTED.
        items, total = repo.list_audit_events(
            agent_id="developer", event_type="SUBMITTED",
        )
        assert total == 1
        assert items[0].event_type == "SUBMITTED"

    def test_filter_by_version_id(self, repo, internal_actor):
        v = _seed_one(repo, agent_id="developer", actor=internal_actor)
        items, total = repo.list_audit_events(
            version_id=v.version_id,
        )
        # CREATED + SUBMITTED on the same version_id.
        assert total == 2
        for r in items:
            assert r.version_id == v.version_id

    def test_filter_by_actor_subject(self, repo, internal_actor, human_actor):
        """D10 lane-discrimination filter — finds every event stamped
        by a given identity (instance id for internal lane; subject
        for HTTP lane)."""
        v = _seed_one(repo, agent_id="developer", actor=internal_actor)
        # Approve so an HTTP session subject lands in the audit trail.
        repo.approve(v.version_id, human_actor)

        items, total = repo.list_audit_events(actor_subject="op@example.com")
        assert total == 1
        assert items[0].event_type == "APPROVED"

        items_int, total_int = repo.list_audit_events(actor_subject="inst-1")
        # CREATED + SUBMITTED both stamped with the instance id.
        assert total_int == 2
        for r in items_int:
            assert r.actor_subject == "inst-1"
            assert r.actor_auth_method == AUTH_METHOD_INTERNAL

    def test_multi_filter_combines_with_and(self, repo, internal_actor):
        v_dev = _seed_one(
            repo, agent_id="developer", artifact="soul", actor=internal_actor,
        )
        _seed_one(
            repo, agent_id="developer", artifact="rule", actor=internal_actor,
        )
        _seed_one(
            repo, agent_id="writer", artifact="soul", actor=internal_actor,
        )
        # agent_id=developer + artifact=soul → only the dev/soul
        # row's audit events.
        items, total = repo.list_audit_events(
            agent_id="developer", artifact="soul",
        )
        assert total == 2  # CREATED + SUBMITTED on dev/soul
        for r in items:
            assert r.agent_id == "developer"
            assert r.artifact == "soul"

        # Add event_type=SUBMITTED → 1 row.
        items, total = repo.list_audit_events(
            agent_id="developer", artifact="soul", event_type="SUBMITTED",
        )
        assert total == 1
        assert items[0].event_type == "SUBMITTED"
        assert items[0].version_id == v_dev.version_id


class TestListAuditEventsPagination:
    def test_limit_caps_items_but_total_reflects_all(
        self, repo, internal_actor
    ):
        # 3 rows × 2 events = 6 audit events.
        for i in range(3):
            _seed_one(
                repo, agent_id=f"agent-{i}", artifact="soul",
                actor=internal_actor,
            )
        items, total = repo.list_audit_events(limit=2, offset=0)
        assert total == 6
        assert len(items) == 2

    def test_offset_pagination_advances(
        self, repo, internal_actor
    ):
        for i in range(3):
            _seed_one(
                repo, agent_id=f"agent-{i}", artifact="soul",
                actor=internal_actor,
            )
        page_1, total_1 = repo.list_audit_events(limit=2, offset=0)
        page_2, total_2 = repo.list_audit_events(limit=2, offset=2)
        assert total_1 == total_2 == 6
        assert len(page_1) == 2
        assert len(page_2) == 2
        # No overlap between pages.
        ids_1 = {r.event_id for r in page_1}
        ids_2 = {r.event_id for r in page_2}
        assert ids_1.isdisjoint(ids_2)


class TestListAuditEventsOrdering:
    def test_newest_first_by_created_at(self, repo, internal_actor):
        # Insert three rows in known order; expect reverse order back.
        ids = []
        for i in range(3):
            v = _seed_one(
                repo, agent_id=f"agent-{i}", artifact="soul",
                actor=internal_actor,
            )
            ids.append(v.version_id)
        items, total = repo.list_audit_events(limit=100)
        assert total == 6
        # Newest first — the LAST seeded row's events come first.
        # We can't assert exact created_at without a clock, but
        # version_id order is deterministic.
        # Each row emits CREATED + SUBMITTED; the third row's pair
        # must come before the second's, which must come before
        # the first's.
        version_id_order = [r.version_id for r in items]
        # Group events by version_id, preserve their relative order.
        groups: dict[str, list[PromptAuditEvent]] = {}
        for r in items:
            groups.setdefault(r.version_id, []).append(r)
        # The version_ids must appear in DESC order of their last
        # event's created_at — equivalent to DESC by the row's
        # last event.
        last_seen: list[str] = []
        for r in items:
            if r.version_id not in last_seen:
                last_seen.append(r.version_id)
        assert last_seen == [ids[2], ids[1], ids[0]]


class TestListAuditEventsBadEventType:
    def test_unknown_event_type_raises(self, repo):
        """The repo validates ``event_type`` against
        :data:`PROMPT_AUDIT_EVENT_TYPES` to keep query semantics
        aligned with the mutating methods."""
        with pytest.raises(ValueError, match="event_type must be one of"):
            repo.list_audit_events(event_type="bogus")


# Sanity: the model metadata includes the three indexes Phase A
# built for query-friendliness. This is a documentation/contract
# assertion (the repo relies on the indexes for the filtered shape).
def test_audit_event_indexes_present():
    table = PromptAuditEvent.__table__
    index_names = {idx.name for idx in table.indexes}
    assert "idx_prompt_audit_events_key_created" in index_names
    assert "idx_prompt_audit_events_version" in index_names
    assert "idx_prompt_audit_events_type" in index_names
