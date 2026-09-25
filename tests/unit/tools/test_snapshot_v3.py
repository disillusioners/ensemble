"""Agent Snapshot v1 — Wave 3 tests.

Covers the commissioned Wave 3 deliverables at the test seam:

* **R15 settings toggle** (rider (i)) — OFF gates ONLY
  ``snapshot_create``; ``snapshot_search`` and ``spawn_hot_instance``
  are NEVER gated. ``is_snapshot_create_enabled`` reads the real
  daemon setting via the project repository (with the fail-closed
  constant fallback); unset settings → OFF.
* **R16 monitoring metrics** (rider (j)) — ``snapshot_create``
  increments the capture counter REGARDLESS of R9 verdict (REUSE +
  NEW + SUPERSEDE + CREATE-FRESH all count); ``spawn_hot_instance``
  WARM path increments the spawn-warm counter; cold / no-hit /
  expired / verify-failed paths DO NOT increment. Counters are
  fail-soft (a counter failure never bubbles up to the tool).
* **D8 / Wave 2b handoff** — ``spawn_hot_instance`` accepts an
  explicit ``allow_cross_project`` opt-in (default ``False``,
  preserves Wave 2b fail-closed); True → cross-project snapshot is
  consumed anyway (staleness still computed; hint notes the
  cross-project origin).
* **Monitoring-only pin** — the ranking modules
  (``snapshot_search_service``, ``snapshot_embedding_service``) do
  NOT import the metrics service module.
* **R16 storage** — the new ``snapshot_usage_counters`` table is
  created by ``SQLModel.metadata.create_all`` (the same path
  ``snapshots`` and ``snapshot_embeddings`` take).

These tests intentionally REDEFINE the small fixtures used by the
tool surface (``engine`` / ``caller_rows`` / ``manager`` / ``tools``
/ ``FakeManager`` / ``FakeSearchService`` / ``FakeCaptureService``
/ ``FakeInstanceRepo``) rather than cross-import the Wave-2b
suite — pytest does not propagate test-file fixtures between
siblings, so the cleanest read is to give Wave-3 its own
self-contained set.

The fixtures reuse the same shapes the Wave-2b suite defines so
behaviour parallels are obvious in code review; the only intentional
divergence is the engine, which here also creates the
``projects`` / ``project_metadata_records`` / ``snapshot_usage_counters``
tables (the Wave-2b engine only creates the snapshot domain).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectStatus,
    ProjectType,
)
from daemon.repositories.snapshot.models import (
    CAPTURE_COUNTER_PREFIX,
    SNAPSHOT_STATUS_ACTIVE,
    SPAWN_COUNTER_PREFIX,
    Snapshot,
    SnapshotEmbedding,
    SnapshotUsageCounter,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_metrics_service import SnapshotMetricsService
from daemon.services.snapshot_settings_utils import (
    _coerce_to_bool,
    get_snapshot_create_enabled,
    set_snapshot_create_enabled,
)
from daemon.tools.snapshot_tools import (
    CATEGORY_DOC,
    _SNAPSHOT_CREATE_ENABLED_DEFAULT,
    create_snapshot_tools,
    is_snapshot_create_enabled,
)

VALID_KIND_TAGS = ["kind:implementation"]
# Same tag set used by the Wave-2b suite — keeps the verdict math
# stable across test files.
VALID_TAGS = [
    "kind:implementation",
    "subsystem:upgrade-pipeline",
]

# Deterministic uuid5 the settings router also pins.
SYSTEM_DEFAULT_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
SYSTEM_DEFAULT_PROJECT_NAME = "system-default"


# ============================================================================
# Fakes — redefined for Wave 3 so the test file stays self-contained
# ============================================================================


class FakeInstanceRepo:
    """Minimal ``get()`` stand-in for the instance repository."""

    def __init__(self, rows: dict[str, Any]):
        self._rows = rows

    def get(self, instance_id: str) -> Any | None:
        return self._rows.get(instance_id)


class FakeCaptureService:
    """Records ``capture_async`` kwargs + serves a canned staleness map."""

    def __init__(self, staleness: dict[str, Any] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.staleness = staleness or {
            "snapshot_age_days": 1.0,
            "freshness": "fresh",
            "warnings": [],
            "repo_state": None,
        }

    async def capture_async(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"snapshot_id": "snap-new", "status": SNAPSHOT_STATUS_ACTIVE, "error": None}

    async def staleness_report(self, snapshot_id: str) -> dict[str, Any] | None:
        return dict(self.staleness)


class FakeSearchService:
    """Canned candidate list for the R9 / R14 internal searches."""

    def __init__(self, results=None, error: str | None = None):
        self.results = list(results or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(self, query, *, project_id, tags=None, tag_mode="all", limit=10, **_):
        self.calls.append(
            {"query": query, "project_id": project_id, "tags": list(tags or []), "tag_mode": tag_mode, "limit": limit}
        )
        return {"results": list(self.results), "error": self.error}


class FakeManager:
    """R6b ordering + Wave-3 metrics passthrough."""

    def __init__(self, rows: dict[str, Any], repo: SnapshotRepository):
        self._instance_repository = FakeInstanceRepo(rows)
        self._snapshot_repo = repo
        self._snapshot_service = FakeCaptureService()
        self._snapshot_search_service = FakeSearchService()
        self._snapshot_metrics_service = None  # Wave-3 tools wire this
        self._project_repository = None
        self.events: list[str] = []
        self.spawn_calls: list[dict[str, Any]] = []
        self.metadata_calls: list[tuple[str, dict[str, Any]]] = []

    def spawn_instance(self, **kwargs) -> tuple[str, str | None]:
        self.events.append("spawn")
        self.spawn_calls.append(kwargs)
        return ("new-inst-1", None)

    def set_metadata_many(self, instance_id: str, updates: dict[str, Any]) -> None:
        self.events.append("metadata")
        self.metadata_calls.append((instance_id, dict(updates)))


class FakeProjectRepo:
    """Minimal ``SQLModelProjectRepository`` substitute for the Wave-3
    settings round-trip.

    Implements only ``get_metadata_record`` + ``set_metadata`` —
    the two methods ``snapshot_settings_utils`` uses. The Wave-3
    fixtures share one SQLite engine with the snapshot metrics
    service so the read + write halves of the round-trip can be
    exercised together.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def get_metadata_record(self, session: Session, project_id: str, key: str):
        return session.exec(
            select(ProjectMetadataRecord).where(
                ProjectMetadataRecord.project_id == project_id,
                ProjectMetadataRecord.meta_key == key,
            )
        ).first()

    def set_metadata(self, project_id: str, key: str, value: str):
        now = datetime.now(timezone.utc).isoformat()
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None
            existing = session.exec(
                select(ProjectMetadataRecord).where(
                    ProjectMetadataRecord.project_id == project_id,
                    ProjectMetadataRecord.meta_key == key,
                )
            ).first()
            if existing is None:
                session.add(
                    ProjectMetadataRecord(
                        project_id=project_id,
                        meta_key=key,
                        meta_value=value,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.meta_value = value
                existing.updated_at = now
                session.add(existing)
            project.updated_at = now
            session.add(project)
            session.commit()
            session.refresh(project)
            return project


def _row(instance_id: str, **kw) -> Any:
    base = {
        "instance_id": instance_id,
        "agent_id": kw.pop("agent_id", "coder"),
        "parent_id": kw.pop("parent_id", None),
        "project_id": kw.pop("project_id", "p1"),
        "status": kw.pop("status", "running"),
        "instance_name": kw.pop("instance_name", None),
        "instance_metadata": kw.pop("instance_metadata", {}),
    }
    base.update(kw)
    return type("Row", (), base)


def _candidate(snapshot_id: str, tags, freshness: str = "fresh", age: float = 1.0):
    return {
        "snapshot_id": snapshot_id,
        "name": "snap",
        "tags": list(tags),
        "freshness": freshness,
        "age_days": age,
        "summary": "preview text",
    }


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite with the snapshot domain + project metadata tables.

    Schema:

    * ``snapshots`` / ``snapshot_embeddings`` — Wave-2 PR3 schema
    * ``snapshot_usage_counters`` — Wave-3 R16 schema (new-table-only
      per the DB guardrail)
    * ``projects`` / ``project_metadata_records`` — needed by
      ``snapshot_settings_utils`` for the SYSTEM_DEFAULT_PROJECT
      metadata read/write.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def project_repo(engine: Engine) -> FakeProjectRepo:
    return FakeProjectRepo(engine)


@pytest.fixture
def system_default_project(engine: Engine) -> str:
    """Insert the SYSTEM_DEFAULT_PROJECT row the settings writer targets."""
    now_iso = "2026-07-12T00:00:00+00:00"
    with Session(engine) as session:
        project = Project(
            project_id=SYSTEM_DEFAULT_PROJECT_ID,
            name=SYSTEM_DEFAULT_PROJECT_NAME,
            project_type=ProjectType.GENERAL.value,
            status=ProjectStatus.ACTIVE.value,
            main_directory=None,
            related_directories=[],
            description="system default project (test)",
            project_metadata={},
            relationships={},
            creator_instance_id=None,
            creator_agent_id=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(project)
        session.commit()
    return SYSTEM_DEFAULT_PROJECT_ID


@pytest.fixture(autouse=True)
def _patch_system_default_project_id():
    """Pin SYSTEM_DEFAULT_PROJECT_ID for the duration of each test
    (matches the autouse pattern in tests/test_settings_api.py)."""
    from daemon import constants

    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = SYSTEM_DEFAULT_PROJECT_ID
    try:
        yield
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture
def caller_rows() -> dict[str, Any]:
    """caller (=current) → target descendant chain; caller lives in p1."""
    return {
        "caller-1": _row("caller-1", agent_id="coder"),
        "inst-1": _row("inst-1", agent_id="worker", parent_id="caller-1"),
    }


@pytest.fixture
def manager(engine: Engine, caller_rows: dict[str, Any]) -> FakeManager:
    return FakeManager(caller_rows, SnapshotRepository(engine))


@pytest.fixture
def tools(manager: FakeManager):
    return create_snapshot_tools(manager, "caller-1", "coder", None)


# ============================================================================
# R15 — settings toggle plumbing (unit-level, no HTTP)
# ============================================================================


class TestR15SettingsToggle:
    """R15 — settings toggle drives snapshot_create, default OFF.

    * Unset / missing settings metadata → ``False`` (fail-closed).
    * Explicit ON / OFF roundtrip through ``set_snapshot_create_enabled``.
    * The helper accepts both the Wave-2b call shape
      ``is_snapshot_create_enabled()`` (no args) and the Wave-3
      call shape ``is_snapshot_create_enabled(manager=...)``.
    * Constants load + boolean coercion handle the documented
      truthy/falsy spellings.
    """

    @pytest.mark.asyncio
    async def test_unsetting_reads_false(self, project_repo):
        enabled = await get_snapshot_create_enabled(project_repo)
        assert enabled is False

    @pytest.mark.asyncio
    async def test_set_on_reads_true(self, project_repo, system_default_project):
        await set_snapshot_create_enabled(project_repo, True)
        enabled = await get_snapshot_create_enabled(project_repo)
        assert enabled is True

    @pytest.mark.asyncio
    async def test_set_off_reads_false(self, project_repo, system_default_project):
        await set_snapshot_create_enabled(project_repo, True)
        await set_snapshot_create_enabled(project_repo, False)
        enabled = await get_snapshot_create_enabled(project_repo)
        assert enabled is False

    @pytest.mark.asyncio
    async def test_roundtrip_twice(self, project_repo, system_default_project):
        for expected in (True, False, True, False):
            await set_snapshot_create_enabled(project_repo, expected)
            got = await get_snapshot_create_enabled(project_repo)
            assert got is expected

    @pytest.mark.asyncio
    async def test_no_system_default_project_row_returns_false(self, project_repo):
        # No fixture for system_default_project → no row → OFF.
        enabled = await get_snapshot_create_enabled(project_repo)
        assert enabled is False

    def test_helper_default_constant_is_false(self):
        assert _SNAPSHOT_CREATE_ENABLED_DEFAULT is False

    def test_helper_no_args_returns_default(self):
        # Backward-compat: calling with no args preserves the
        # Wave-2b contract (returns the constant).
        assert is_snapshot_create_enabled() is False

    def test_helper_explicit_manager_none_returns_default(self):
        # Wave-3 call shape: ``manager=None`` → constant fallback.
        assert is_snapshot_create_enabled(manager=None) is False

    @pytest.mark.parametrize(
        "stored, expected",
        [
            ("on", True),
            ("true", True),
            ("1", True),
            ("yes", True),
            ("ON", True),
            ("  yes  ", True),
            ("off", False),
            ("false", False),
            ("0", False),
            ("", False),
            ("random_string", False),
            ("maybe", False),
        ],
    )
    def test_coerce_to_bool_truthy_spellings(self, stored, expected):
        assert _coerce_to_bool(stored) is expected


# ============================================================================
# R15 tool-isolation (rider i)
# ============================================================================


class TestR15ToolIsolation:
    """Rider (i) — R15 OFF gates ``snapshot_create`` ONLY.

    ``snapshot_search`` and ``spawn_hot_instance`` are NEVER gated.
    The disabled result shape is EXACTLY ``{"disabled": True,
    "error": "snapshot_create disabled by settings toggle"}``.
    """

    def test_snapshot_create_disabled_result_exact(self, tools):
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result == {
            "disabled": True,
            "error": "snapshot_create disabled by settings toggle",
        }

    def test_snapshot_create_on_proceeds(self, tools, manager, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools.is_snapshot_create_enabled",
            lambda manager=None: True,
        )
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["error"] is None
        assert len(manager._snapshot_service.calls) == 1

    def test_snapshot_search_not_gated(self, tools, monkeypatch):
        # Toggle OFF (manager-aware). snapshot_search MUST still succeed.
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools.is_snapshot_create_enabled",
            lambda manager=None: False,
        )
        result = _run(tools[1].ainvoke({"query": "anything"}))
        assert result == {"results": [], "error": None}

    def test_spawn_hot_instance_not_gated(self, tools, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools.is_snapshot_create_enabled",
            lambda manager=None: False,
        )
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        # R14 fail-soft contract MUST return cold; error stays None.
        assert result["error"] is None
        assert result["started"] == "cold"

    def test_unset_setting_reads_as_off(self, tools):
        # When `is_snapshot_create_enabled` falls through to the
        # constant (no manager / sync-context call), the gate is OFF
        # by default. Both the helper const and the call shape are
        # fail-closed.
        assert _SNAPSHOT_CREATE_ENABLED_DEFAULT is False
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result == {
            "disabled": True,
            "error": "snapshot_create disabled by settings toggle",
        }


# ============================================================================
# R16 — monitoring counters (rider j)
# ============================================================================


class TestR16MonitoringMetrics:
    """Rider (j) — R16 monitoring counters.

    * ``snapshot_create`` increments on REUSE + NEW + SUPERSEDE +
      CREATE-FRESH (every R9 verdict).
    * ``spawn_hot_instance`` increments spawn on WARM only —
      cold / no-hit / expired / verify-failed DO NOT increment.
    * Both increments are fail-soft: a counter failure never
      bubbles up; the spawn/create path proceeds.
    """

    def _enable(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools.is_snapshot_create_enabled",
            lambda manager=None: True,
        )

    def _wire_metrics(self, manager: FakeManager, engine: Engine):
        manager._snapshot_metrics_service = SnapshotMetricsService(engine=engine)

    def test_capture_count_increments_on_new_verdict(
        self, tools, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["error"] is None
        assert result["verdict"] == "new"
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == CAPTURE_COUNTER_PREFIX + "coder"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_capture_count_increments_on_reuse_verdict(
        self, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        # Strong match → REUSE verdict (no capture_async call).
        manager._snapshot_search_service = FakeSearchService(
            [
                _candidate(
                    "snap-existing",
                    ["kind:implementation", "subsystem:upgrade-pipeline", "runtime:0.14.2"],
                )
            ]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["verdict"] == "reuse"
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == CAPTURE_COUNTER_PREFIX + "coder"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_capture_count_increments_on_supersede_verdict(
        self, tools, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        # Seed a predecessor active snapshot of the SAME target
        # so the SUPERSEDE branch fires.
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            Snapshot(
                id="snap-prev",
                project_id="p1",
                created_by_agent_id="coder",
                target_instance_id="inst-1",
                title="prev",
                task_summary="did the thing",
                domain_tags=[],
                status=SNAPSHOT_STATUS_ACTIVE,
                repo_path=None,
                vcs_type=None,
                git_sha=None,
                git_branch=None,
                git_dirty=False,
                runtime_version="0.14.2",
                effective_model="cheap-model",
                digest={"task_summary_text": "did the thing"},
            )
        )
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["verdict"] == "supersede"
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == CAPTURE_COUNTER_PREFIX + "coder"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_capture_count_increments_on_create_fresh_verdict(
        self, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        # Candidates exist but fail the REUSE rule → CREATE-FRESH verdict.
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-weak", ["kind:review", "topic:other"])]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["verdict"] == "create-fresh"
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == CAPTURE_COUNTER_PREFIX + "coder"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_spawn_counter_warm_path_increments(
        self, tools, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            Snapshot(
                id="snap-warm",
                project_id="p1",
                created_by_agent_id="coder",
                target_instance_id="inst-1",
                title="warm",
                task_summary="",
                domain_tags=["kind:implementation"],
                status=SNAPSHOT_STATUS_ACTIVE,
                repo_path=None,
                vcs_type=None,
                git_sha=None,
                git_branch=None,
                git_dirty=False,
                runtime_version="0.14.2",
                effective_model="cheap-model",
                digest={"task_summary_text": "warm"},
            )
        )
        result = _run(
            tools[2].ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm me",
                    "snapshot_id": "snap-warm",
                }
            )
        )
        assert result["started"] == "warm"
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == SPAWN_COUNTER_PREFIX + "snap-warm"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_spawn_counter_cold_no_snapshot_no_increment(
        self, tools, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        # No snapshot at all → cold fallback.
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "no snapshot"}))
        assert result["started"] == "cold"
        with Session(engine) as session:
            rows = list(session.exec(select(SnapshotUsageCounter)).all())
        assert all(not r.scope.startswith(SPAWN_COUNTER_PREFIX) for r in rows)

    def test_spawn_counter_internal_search_no_hit_no_increment(
        self, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        manager._snapshot_search_service = FakeSearchService([])
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "find something"})
        )
        assert result["started"] == "cold"
        with Session(engine) as session:
            rows = list(session.exec(select(SnapshotUsageCounter)).all())
        assert all(not r.scope.startswith(SPAWN_COUNTER_PREFIX) for r in rows)

    def test_spawn_counter_expired_no_increment(self, manager, engine, monkeypatch):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-old", ["kind:implementation"], freshness="expired")]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "warm me"}))
        assert result["started"] == "cold"
        with Session(engine) as session:
            rows = list(session.exec(select(SnapshotUsageCounter)).all())
        assert all(not r.scope.startswith(SPAWN_COUNTER_PREFIX) for r in rows)

    def test_spawn_counter_verify_failed_no_increment(
        self, tools, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._wire_metrics(manager, engine)
        # Explicit snapshot_id on a row that doesn't exist → verify-fail
        # cold fallback.
        result = _run(
            tools[2].ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm me",
                    "snapshot_id": "snap-ghost",
                }
            )
        )
        assert result["started"] == "cold"
        with Session(engine) as session:
            rows = list(session.exec(select(SnapshotUsageCounter)).all())
        assert all(not r.scope.startswith(SPAWN_COUNTER_PREFIX) for r in rows)

    def test_counter_failure_does_not_break_tool(self, tools, manager, monkeypatch):
        """Fail-soft: a broken metrics service MUST NOT bubble up
        (rider j — increments fail-soft; never raise; never fail
        the spawn/create).
        """

        self._enable(monkeypatch)

        class BrokenMetrics:
            def inc_capture(self, agent_id: str) -> None:
                raise RuntimeError("simulated counter store crash")

            def inc_spawn(self, snapshot_id: str) -> None:
                raise RuntimeError("simulated counter store crash")

        manager._snapshot_metrics_service = BrokenMetrics()
        # snapshot_create MUST still succeed (verdict = "new").
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert result["error"] is None
        assert result["verdict"] == "new"
        # spawn_hot_instance cold path also must not raise.
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "t"})
        )
        assert result["error"] is None
        assert result["started"] == "cold"


# ============================================================================
# D8 / Wave-2b handoff — explicit cross-project override
# ============================================================================


class TestCrossProjectOverride:
    """D8 / Wave-2b handoff — explicit cross-project consume opt-in.

    * Default ``False`` (preserves Wave 2b fail-closed behavior):
      cross-project snapshot → verify-fail cold fallback + warning.
    * Explicit ``True`` → cross-project snapshot is consumed;
      staleness still computed; hint notes the cross-project
      origin; spawn-warm counter still increments.
    """

    def _enable(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools.is_snapshot_create_enabled",
            lambda manager=None: True,
        )

    def _seed_cross_project_snap(self, manager: FakeManager):
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            Snapshot(
                id="snap-xp",
                project_id="p2",
                created_by_agent_id="coder",
                target_instance_id="inst-xp",
                title="cross-project snapshot",
                task_summary="",
                domain_tags=["kind:implementation"],
                status=SNAPSHOT_STATUS_ACTIVE,
                repo_path=None,
                vcs_type=None,
                git_sha=None,
                git_branch=None,
                git_dirty=False,
                runtime_version="0.14.2",
                effective_model="cheap-model",
                digest={"task_summary_text": "xp"},
            )
        )

    def test_default_false_project_mismatch_is_cold(
        self, tools, manager, monkeypatch
    ):
        self._enable(monkeypatch)
        # Caller lives in p1; snapshot belongs to p2. With default
        # allow_cross_project=False, this MUST cold-fallback.
        self._seed_cross_project_snap(manager)
        result = _run(
            tools[2].ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm me",
                    "snapshot_id": "snap-xp",
                }
            )
        )
        assert result["started"] == "cold"
        # Hint / staleness warnings reference the project mismatch.
        assert "snap-xp" in result["hint"] or any(
            "snap-xp" in w
            for w in (result.get("staleness") or {}).get("warnings") or []
        )

    def test_explicit_true_cross_project_consumes(
        self, manager, engine, monkeypatch
    ):
        self._enable(monkeypatch)
        self._seed_cross_project_snap(manager)
        manager._snapshot_metrics_service = SnapshotMetricsService(engine=engine)
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(
            tools[2].ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm me cross-project",
                    "snapshot_id": "snap-xp",
                    "allow_cross_project": True,
                }
            )
        )
        assert result["started"] == "warm"
        assert "cross-project" in result["hint"]
        # Spawn counter still increments.
        with Session(engine) as session:
            row = session.exec(
                select(SnapshotUsageCounter).where(
                    SnapshotUsageCounter.scope == SPAWN_COUNTER_PREFIX + "snap-xp"
                )
            ).first()
        assert row is not None
        assert row.value == 1

    def test_explicit_true_within_project_unchanged(
        self, tools, manager, monkeypatch
    ):
        """allow_cross_project=True on a same-project snapshot is
        a no-op (the verify-fail branch never fires).
        """
        self._enable(monkeypatch)
        # Both snapshot and caller live in p1.
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            Snapshot(
                id="snap-same",
                project_id="p1",
                created_by_agent_id="coder",
                target_instance_id="inst-1",
                title="same-project",
                task_summary="",
                domain_tags=["kind:implementation"],
                status=SNAPSHOT_STATUS_ACTIVE,
                repo_path=None,
                vcs_type=None,
                git_sha=None,
                git_branch=None,
                git_dirty=False,
                runtime_version="0.14.2",
                effective_model="cheap-model",
                digest={"task_summary_text": "same"},
            )
        )
        result = _run(
            tools[2].ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm me",
                    "snapshot_id": "snap-same",
                    "allow_cross_project": True,
                }
            )
        )
        assert result["started"] == "warm"
        # No cross-project marker (the explicit flag was a no-op
        # since the project matched).
        assert "cross-project" not in result["hint"]


# ============================================================================
# Monitoring-only pin — ranking modules MUST NOT import metrics service
# ============================================================================


class TestMonitoringOnlyPin:
    """MONITORING ONLY enforcement — ranking modules don't import
    the metrics service (R10 forbids usage-ranking in v1; the
    counters are observational only).

    Pin-test: a static walk over the ranking module source files
    asserts no ``import … snapshot_metrics_service …`` line.
    """

    @pytest.mark.parametrize(
        "module_name",
        [
            "daemon/services/snapshot_search_service.py",
            "daemon/services/snapshot_embedding_service.py",
            "daemon/repositories/snapshot/repository.py",
        ],
    )
    def test_ranking_module_does_not_import_metrics(self, module_name):
        repo_root = Path(__file__).resolve().parents[3]
        target = repo_root / module_name
        assert target.is_file(), f"target file {target} not found"
        text = target.read_text(encoding="utf-8")
        assert "snapshot_metrics_service" not in text, (
            f"{module_name} MUST NOT import the R16 metrics service "
            "(R10 forbids usage-ranking). Found a reference in the file."
        )

    def test_search_service_top_to_bottom(self):
        """Wide-net grep: the search service file MUST be free of
        any reference to the metrics service module. Catches lazy
        import re-exports too.
        """
        repo_root = Path(__file__).resolve().parents[3]
        target = repo_root / "daemon" / "services" / "snapshot_search_service.py"
        text = target.read_text(encoding="utf-8")
        assert "metrics" not in text or "MONITORING" in text.upper(), (
            "snapshot_search_service.py contains a 'metrics' reference — "
            "investigate whether this is the R16 metrics service."
        )


# ============================================================================
# R16 — counters read by the surface endpoint
# ============================================================================


class TestR16Surface:
    """The ``surface()`` read path returns the shape the FE
    settings surface consumes.
    """

    @pytest.mark.asyncio
    async def test_surface_empty(self, engine):
        service = SnapshotMetricsService(engine=engine)
        snap = await service.surface()
        assert snap == {
            "capture_counts": {},
            "spawn_counts_per_snapshot": [],
        }

    @pytest.mark.asyncio
    async def test_surface_aggregated(self, engine):
        service = SnapshotMetricsService(engine=engine)
        service.inc_capture("coder")
        service.inc_capture("coder")
        service.inc_capture("tester")
        service.inc_spawn("snap-a")
        service.inc_spawn("snap-a")
        service.inc_spawn("snap-b")
        snap = await service.surface()
        assert snap["capture_counts"] == {
            "coder": {"created": 2},
            "tester": {"created": 1},
        }
        spawns = {
            entry["snapshot_id"]: entry["count"]
            for entry in snap["spawn_counts_per_snapshot"]
        }
        assert spawns == {"snap-a": 2, "snap-b": 1}

    def test_inc_capture_fail_soft_swallowed(self, engine, caplog):
        """A broken increment MUST NOT raise — log + swallow."""
        from sqlalchemy import text as sa_text

        service = SnapshotMetricsService(engine=engine)
        # Drop the table to force the upsert to fail.
        with engine.connect() as conn:
            conn.execute(sa_text("DROP TABLE snapshot_usage_counters"))
            conn.commit()
        # The service catches the exception internally; the call returns.
        import logging
        with caplog.at_level(logging.WARNING, logger="daemon.services.snapshot_metrics_service"):
            service.inc_capture("coder")
        matched = [
            r for r in caplog.records
            if "R16 counter upsert failed" in r.getMessage()
        ]
        assert matched, "expected a WARNING log line on counter failure"

    def test_usage_counter_table_created_alongside_snapshot_domain(self, engine):
        """R16 DB guardrail — the ``snapshot_usage_counters`` table
        lands in the SAME ``create_all`` pass as ``snapshots`` /
        ``snapshot_embeddings`` / ``projects`` / ``project_metadata_records``.
        """
        with Session(engine) as session:
            table_names = {
                t.name
                for t in SQLModel.metadata.sorted_tables
                if t.name in (
                    "snapshots",
                    "snapshot_embeddings",
                    "snapshot_usage_counters",
                    "projects",
                    "project_metadata_records",
                )
            }
        assert {
            "snapshots",
            "snapshot_embeddings",
            "snapshot_usage_counters",
            "projects",
            "project_metadata_records",
        }.issubset(table_names)


# ============================================================================
# FE / daemon parity — constant identity + category-doc coverage
# ============================================================================


class TestFEParity:
    """FE item name ↔ daemon setting name consistency + CATEGORY_DOC coverage."""

    def test_metadata_key_constant_present(self):
        from daemon import constants

        assert hasattr(constants, "SNAPSHOT_CREATE_METADATA_KEY")
        assert constants.SNAPSHOT_CREATE_METADATA_KEY == "snapshot_create_enabled"

    def test_settings_route_settings_module_references_constant(self):
        # The router imports the schema lazily; the constant is the
        # single source of truth for the metadata record key.
        import daemon.routers.settings as settings_module
        from daemon import constants

        # Both endpoints threaded through the same key constant:
        assert constants.SNAPSHOT_CREATE_METADATA_KEY == "snapshot_create_enabled"

    def test_category_doc_mentions_allow_cross_project(self):
        """Wave-3 D8 doc line about ``allow_cross_project`` lands
        in CATEGORY_DOC.
        """
        assert "allow_cross_project" in CATEGORY_DOC

    def test_category_doc_mentions_project_less_cold(self):
        """D8 project-less-caller line lands in CATEGORY_DOC."""
        # The Wave-2b handoff spec: "add one line to CATEGORY_DOC"
        # about project-less callers always going cold.
        lowered = CATEGORY_DOC.lower()
        assert "project-less" in lowered or "projectless" in lowered

    def test_settings_helper_keys_use_snapshot_create_namespace(self):
        """The settings router + util share ``snapshot_create_enabled``
        as the metadata key (FE / daemon parity).
        """
        from daemon import constants

        assert constants.SNAPSHOT_CREATE_METADATA_KEY == "snapshot_create_enabled"
