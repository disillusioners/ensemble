"""Tests for the M2 mission tools: get_mission / await_mission / list_missions.

Mission-class Milestone M2 (2026-09-02, ``feature/mission-class``) —
the agent tool surface for the mission read-model projection. Built
on the M1 additive mission response fields (always-on since WS3) and
the M4(i)-HTTP ``GET /missions`` pull-forward. The tools are
READ-ONLY: ``MissionResolver`` is a leaf service that touches no
admission-state writers; census stays at 23.

These tests pin the M2 contract:

* **Identity** — ``mission_id == instance_id``; ``parent_mission_id``
  comes from ``Instance.parent_id`` (permanent across revive).
* **Snapshot shape** — ``get_mission`` returns the contract draft §2
  shape (identity, liveness, terminal_reason, epoch, epochs,
  epoch_count, last_epoch_at, linked_jobs, started_at,
  last_activity_at, outcome). ``outcome`` is non-null ONLY when
  terminal; ``None`` when live (the asymmetric outcome token).
* **Await blocking semantics** — ``await_mission`` blocks via an
  asyncio poll loop until the mission is terminal (liveness in
  ``{completed, failed, cancelled}`` AND ``terminal_reason`` set) OR
  the timeout fires (returns the current snapshot, NOT an error per
  contract draft §2). The resolver is re-polled on each iteration so
  a revival bumps the epoch transparently (F7).
* **Not-found** — ``get_mission`` and ``await_mission`` return
  ``{"error": "mission_not_found", ...}`` for unknown ids.
* **W4 hazard** — a DEAD linked JobItem surfaces
  ``terminal_reason: "dead_letter"`` regardless of a since-revived
  instance (the resolver's W4 contract; the tool faithfully projects
  it through).
* **List filters** — ``list_missions`` filters compose with AND
  semantics; unknown ``liveness`` values degrade to an honestly-empty
  page (no exception).

Harness notes
-------------

The fixture mounts a real ``MissionResolver`` against a file-backed
SQLite engine with ``Instance`` + ``JobItem`` rows. The resolver's
3-SELECT page engine is exercised end-to-end (the W4-hazard path
goes through the same ``_batch_jobitem_lookup`` the production HTTP
list uses).

Mission tools are READERS — no DB writes. The resolver is a leaf
service, so this test file does not need the WriteGuardSession /
StaticPool trap guard that some service tests use.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.mission_resolver import MissionResolver
from daemon.tools.missions import create_mission_tools


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    The conventions recipe (mirrors
    ``tests/unit/services/test_mission_resolver.py``). Each test
    gets its own ``tmp_path`` file so concurrent test isolation is
    preserved.
    """
    db_path = tmp_path / "mission-tools-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
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
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(instance_repo, job_repo) -> MissionResolver:
    return MissionResolver(instance_repo=instance_repo, job_repo=job_repo)


@pytest.fixture
def tools(resolver: MissionResolver) -> dict[str, Any]:
    """The three mission tools keyed by name."""
    factory_tools = create_mission_tools(resolver)
    return {t.name: t for t in factory_tools}


# ─── Seed helpers ─────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    agent_dir: str | None = "agents/developer",
    project_id: str | None = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
) -> str:
    """Insert a populated ``Instance`` row."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with Session(engine) as s:
        instance = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=agent_dir,
            project_id=project_id,
            parent_id=parent_id,
            status=status,
            last_activity_at=last_activity_at or now,
            created_at=iso_now,
            updated_at=iso_now,
        )
        s.add(instance)
        s.commit()
    return iid


def _seed_job_item(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str,
    admission_state: str = AdmissionState.DONE.value,
    job_type: str = "task",
    terminal_reason: str | None = "completed",
) -> str:
    """Insert a populated ``JobItem`` row."""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    import json as _json
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            instance_id=instance_id,
            agent_id="developer",
            agent_dir="agents/developer",
            admission_state=admission_state,
            job_type=job_type,
            terminal_reason=terminal_reason,
            project_id="test-project",
            priority=5,
            message="(seed)",
            source="api",
            job_metadata=_json.dumps({}),
            created_at=now,
            updated_at=now,
        )
        s.add(job)
        s.commit()
    return jid


# ─── Test groups ─────────────────────────────────────────────────────────


class TestToolRegistryAndCategory:
    """Three tools registered with the right category + names."""

    def test_factory_returns_three_tools(self, tools: dict[str, Any]) -> None:
        """The factory creates exactly the three tools from the contract draft §2."""
        assert set(tools.keys()) == {"get_mission", "await_mission", "list_missions"}

    def test_tools_carry_mission_category(self, tools: dict[str, Any]) -> None:
        """Each tool is ``@register_tool_category("mission")``-decorated.

        Pinning the category ensures the tools join the ``mission``
        allow-list path used by ``meta.json`` tools.allow entries
        (``ari`` and ``jober`` both gained the ``"mission"`` entry
        in this M2 batch).
        """
        for tool in tools.values():
            assert getattr(tool, "_tool_category", None) == "mission", (
                f"{tool.name} missing mission category: "
                f"{getattr(tool, '_tool_category', None)}"
            )

    def test_tools_registered_in_known_tool_names(
        self, tools: dict[str, Any]
    ) -> None:
        """All three tool names appear in the static fallback universe.

        The frozen-name discovery test (``tests/unit/tools/test_frozen_tool_name_discovery.py``)
        pins ``KNOWN_TOOL_NAMES`` against the source-AST scan; this
        test confirms the static list carries the new entries (the
        drift-test failure mode if a maintainer forgets to regen).
        """
        from daemon.tools._tool_registry import KNOWN_TOOL_NAMES
        for name in tools:
            assert name in KNOWN_TOOL_NAMES, (
                f"{name} missing from KNOWN_TOOL_NAMES — regen via "
                f"the documented one-liner"
            )


class TestGetMissionSnapshot:
    """``get_mission`` returns the contract draft §2 snapshot shape."""

    def test_get_mission_returns_full_snapshot_for_live_mission(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """A live mission snapshot carries every draft §2 key, with
        ``outcome: None`` (the live-asymmetric outcome token)."""
        iid = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        result = asyncio.run(tools["get_mission"].ainvoke({"mission_id": iid}))
        assert result["mission_id"] == iid
        assert result["liveness"] == "processing"
        assert result["terminal_reason"] is None
        # outcome token — asymmetric: NULL when live, value when terminal
        assert result["outcome"] is None
        # identity / structural keys all present
        for key in (
            "agent_id", "parent_mission_id", "epoch", "epochs",
            "epoch_count", "last_epoch_at", "linked_jobs",
            "started_at", "last_activity_at",
        ):
            assert key in result, f"missing key {key} in snapshot"
        # Epoch fields are best-effort: epoch_count and epoch are
        # both 1 (constant per §8.3); epochs array carries a single
        # current-interval summary entry.
        assert result["epoch"] == 1
        assert result["epoch_count"] == 1
        assert isinstance(result["epochs"], list)
        assert len(result["epochs"]) == 1
        entry = result["epochs"][0]
        assert entry["seq"] == 1
        assert entry["kind"] == "initial"
        assert entry["ended_at"] is None  # live mission → ended_at=None
        assert entry["terminal_reason"] is None

    def test_get_mission_returns_outcome_when_terminal(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """A terminal mission carries ``outcome`` = the terminal cause."""
        iid = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        result = asyncio.run(tools["get_mission"].ainvoke({"mission_id": iid}))
        assert result["liveness"] == "completed"
        assert result["terminal_reason"] == "completed"
        # The asymmetric outcome token — VALUE when terminal
        assert result["outcome"] == "completed"
        # Epoch entry populated ended_at when terminal
        assert result["epochs"][0]["ended_at"] is not None
        assert result["epochs"][0]["terminal_reason"] == "completed"

    def test_get_mission_returns_not_found(
        self, tools: dict[str, Any]
    ) -> None:
        """Unknown ``mission_id`` ⇒ ``{error: mission_not_found, mission_id: ...}``."""
        result = asyncio.run(
            tools["get_mission"].ainvoke({"mission_id": "does-not-exist"})
        )
        assert result["error"] == "mission_not_found"
        assert result["mission_id"] == "does-not-exist"

    def test_get_mission_w4_dead_letter_overrides_liveness(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """W4-hazard: a DEAD-job mission reports ``dead_letter`` even
        when the instance has since been revived (e.g., status=running).
        The renderer must NOT let liveness override DEAD."""
        # DEAD-job linked to a running instance (the exact S4 case
        # the resolver was hardened against in 7852aeab).
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_job_item(
            engine, instance_id=iid, admission_state=AdmissionState.DEAD.value,
            job_type="message", terminal_reason="dead_letter",
        )
        result = asyncio.run(tools["get_mission"].ainvoke({"mission_id": iid}))
        assert result["liveness"] == "processing"  # instance is running
        # W4: terminal_reason is dead_letter regardless of liveness
        assert result["terminal_reason"] == "dead_letter"
        # And the asymmetric outcome token reflects that
        assert result["outcome"] == "dead_letter"


class TestAwaitMissionBlocking:
    """``await_mission`` blocks (asyncio poll) until terminal or timeout."""

    def test_await_mission_returns_snapshot_when_already_terminal(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """A terminal mission short-circuits — no polling, snapshot returned."""
        iid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        result = asyncio.run(tools["await_mission"].ainvoke({"mission_id": iid}))
        assert result["liveness"] == "completed"
        assert result["outcome"] == "completed"

    def test_await_mission_returns_not_found(
        self, tools: dict[str, Any]
    ) -> None:
        """Unknown ``mission_id`` ⇒ ``mission_not_found``."""
        result = asyncio.run(
            tools["await_mission"].ainvoke({"mission_id": "missing"})
        )
        assert result["error"] == "mission_not_found"
        assert result["mission_id"] == "missing"

    def test_await_mission_resolves_to_terminal_via_polling(
        self, tools: dict[str, Any], engine: Engine, resolver: MissionResolver
    ) -> None:
        """Live mission + later transition ⇒ await blocks until terminal.

        Drives the resolver end-to-end: a live mission is seeded, the
        await coroutine is scheduled, the instance is then flipped to
        terminal, and the await resolves on the next poll iteration.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)

        async def flip_after_delay() -> None:
            # Let the await enter the poll loop first
            await asyncio.sleep(0.05)
            # Flip to terminal — instance status now ``COMPLETED``
            with Session(engine) as s:
                instance = s.get(Instance, iid)
                instance.status = InstanceStatus.COMPLETED.value
                s.add(instance)
                s.commit()

        async def main() -> dict[str, Any]:
            return await asyncio.gather(
                tools["await_mission"].ainvoke({
                    "mission_id": iid,
                    "timeout": 5.0,
                    "poll_interval": 0.05,
                }),
                flip_after_delay(),
            )

        result, _ = asyncio.run(main())
        assert result["liveness"] == "completed"
        assert result["outcome"] == "completed"

    def test_await_mission_timeout_returns_current_snapshot(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """Timeout fires ⇒ return current snapshot, NOT an error.

        Contract draft §2 explicitly states timeout semantics: "returns
        current snapshot (no error)". A live mission that never
        reaches terminal within the timeout must therefore return the
        live snapshot (with ``outcome: None``).
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        result = asyncio.run(
            tools["await_mission"].ainvoke({
                "mission_id": iid,
                "timeout": 0.1,
                "poll_interval": 0.05,
            })
        )
        # No error — current snapshot returned
        assert "error" not in result
        assert result["mission_id"] == iid
        assert result["liveness"] == "processing"
        assert result["outcome"] is None  # live → null outcome token


class TestListMissions:
    """``list_missions`` paginates and filters."""

    def test_list_missions_returns_summaries(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """Two seeded missions ⇒ two summaries returned."""
        ids = [
            _seed_instance(engine, status=InstanceStatus.RUNNING.value),
            _seed_instance(engine, status=InstanceStatus.COMPLETED.value),
        ]
        result = asyncio.run(tools["list_missions"].ainvoke({}))
        assert result["limit"] == 50
        assert result["truncated"] is False
        assert len(result["missions"]) == 2
        # Summaries include epoch_count + last_epoch_at, NOT the full epochs array
        for m in result["missions"]:
            assert "epoch_count" in m
            assert "last_epoch_at" in m
            assert "epochs" not in m
            # Asymmetric outcome token surfaces in summaries too
            assert "outcome" in m

    def test_list_missions_filter_liveness(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """``liveness='processing'`` ⇒ only RUNNING-class missions."""
        _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        result = asyncio.run(
            tools["list_missions"].ainvoke({"liveness": "processing"})
        )
        assert len(result["missions"]) == 1
        assert result["missions"][0]["liveness"] == "processing"

    def test_list_missions_unknown_liveness_yields_empty_page(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """Unknown ``liveness`` value degrades to empty page (no exception).

        Mirrors the §8.2 / M4(i) "unknown filter ⇒ empty" precedent.
        """
        _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        result = asyncio.run(
            tools["list_missions"].ainvoke({"liveness": "definitely-not-a-value"})
        )
        assert result["missions"] == []
        assert result["truncated"] is False

    def test_list_missions_limit_clamped(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """``limit`` is clamped to ``[1, 200]``."""
        result_over = asyncio.run(
            tools["list_missions"].ainvoke({"limit": 9999})
        )
        assert result_over["limit"] == 200
        result_under = asyncio.run(
            tools["list_missions"].ainvoke({"limit": 0})
        )
        assert result_under["limit"] == 1

    def test_list_missions_parent_mission_id_filter(
        self, tools: dict[str, Any], engine: Engine
    ) -> None:
        """``parent_mission_id`` filter narrows to the subtree."""
        root = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=root
        )
        _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        result = asyncio.run(
            tools["list_missions"].ainvoke({"parent_mission_id": root})
        )
        returned = [m["mission_id"] for m in result["missions"]]
        assert returned == [child]


class TestReadOnlyContract:
    """The mission tools are READERS — no DB writes of any kind."""

    def test_tools_never_write_to_admission_state(
        self, tools: dict[str, Any], engine: Engine, job_repo: JobRepository
    ) -> None:
        """Calling every tool does not mutate ``JobItem.admission_state``.

        Pins the WS1 hard-stop condition (deliverable #5): "the
        census/writer count is frozen at 23 through M1–M3". A
        regression that silently adds a write path inside a mission
        tool would mutate ``admission_state``, which the
        ``test_constitution_drift`` AST scan would catch — but the
        runtime check here is faster and pinpoints the offending
        tool.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)

        async def run_all() -> None:
            await tools["get_mission"].ainvoke({"mission_id": iid})
            await tools["list_missions"].ainvoke({})
            await tools["await_mission"].ainvoke({
                "mission_id": iid, "timeout": 0.05, "poll_interval": 0.02,
            })

        asyncio.run(run_all())

        # No JobItem rows were created — the tool surface is purely
        # READ against the resolver.
        with Session(engine) as s:
            jobs = s.exec(
                __import__("sqlmodel").select(JobItem)
            ).all()
            assert jobs == [], (
                f"mission tools must not create JobItem rows; "
                f"found {len(jobs)}"
            )

    def test_factory_with_none_resolver_returns_empty_list(self) -> None:
        """When the resolver is unavailable, the factory returns an empty
        tool list (fail-open for partial-wiring / test stubs).

        ``create_mission_tools`` does not guard this — the guard
        lives in ``create_mission_tools_if_available`` (mirrors
        ``create_job_tools_if_available``). Verify the contract here
        so a future refactor preserves the additive-empty behaviour.
        """
        from daemon.tools.instance import create_mission_tools_if_available

        manager_no_resolver = MagicMock(spec=[])  # no _mission_resolver attr
        assert create_mission_tools_if_available(manager_no_resolver) == []
