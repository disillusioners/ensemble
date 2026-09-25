"""Agent Snapshot tool-surface tests (Wave 2b, PR6).

Covers the commissioned Wave 2b contracts at the TOOL level:

* **R15 gate** — ``snapshot_create`` disabled by default (single
  helper seam ``is_snapshot_create_enabled``); ``snapshot_search`` and
  ``spawn_hot_instance`` NEVER gated (rider (i) isolation).
* **R9 search-before-create** — the four verdicts (REUSE / SUPERSEDE /
  NEW / CREATE-FRESH) decided deterministically inside the tool.
* **R12 tool-level supersession** — SUPERSEDE threads
  ``supersedes_snapshot_id`` into ``capture_async`` (create-mints-
  successor); REUSE returns the existing id without a capture. The
  lane-level atomic flip (executor terminal write →
  ``create_successor``) is pinned at the repository boundary.
* **R14 auto-fallback** — expired → cold, stale-not-expired → warm +
  drift warnings (hint AND ``staleness.warnings``), no-hit → cold,
  verify-fail → cold + warning, and the fail-soft invariant: never an
  error on miss — errors reserved for auth failure / system fault
  (rider (h)). Result-contract shape pinned exactly (§4.3).
* **R6b warm-path ordering** — ``spawn_instance`` THEN the atomic
  ``set_metadata_many({snapshot_digest, spawned_from_snapshot_id})``
  BEFORE the instance_id is returned; the cold path writes nothing.
* **Registration chain** (rider (d)) — all three names in
  ``DYNAMIC_TOOL_NAMES``, the ``snapshot`` category module entry, the
  loader warm-list, the ``instance.py`` factory call, and
  ``KNOWN_TOOL_NAMES``/source-discovery agreement.
* **Grants** — worker/coder/tester gain the two per-tool names;
  ``spawn_hot_instance`` resolves via the ``instance`` category for a
  holder; ari gains NOTHING (permanent exclusion).

Rider (e): every regex/grep assertion on the hot-spawn tool name in
this file uses the ``\\bhot\\b`` / ``\\bspawn_hot_instance\\b``
word-boundary forms — substring matching collides with ``snapshot`` /
``shot``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_RUNNING,
    SNAPSHOT_STATUS_SUPERSEDED,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_executor import SnapshotExecutor
from daemon.tools.snapshot_tools import (
    CATEGORY_NAME,
    create_snapshot_tools,
    is_snapshot_create_enabled,
)

TOOLS_DIR = Path(__file__).resolve().parents[3] / "daemon" / "tools"

VALID_TAGS = ["kind:implementation", "subsystem:upgrade-pipeline"]


# ============================================================================
# Fixtures — real snapshot repository on in-memory SQLite; fake manager
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
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


def _snapshot(
    *,
    snapshot_id: str = "snap-1",
    project_id: str = "p1",
    target: str = "inst-1",
    title: str = "snap-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    tags: list[str] | None = None,
    digest: dict[str, Any] | None = None,
    created_by: str = "coder",
) -> Snapshot:
    return Snapshot(
        id=snapshot_id,
        project_id=project_id,
        created_by_agent_id=created_by,
        target_instance_id=target,
        title=title,
        task_summary="did the thing",
        domain_tags=tags or [],
        status=status,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.14.2",
        effective_model="cheap-model",
        digest=digest if digest is not None else {"task_summary_text": "did the thing"},
    )


class FakeInstanceRepo:
    """Minimal ``get()`` stand-in for the instance repository."""

    def __init__(self, rows: dict[str, Any]):
        self._rows = rows

    def get(self, instance_id: str) -> Any | None:
        return self._rows.get(instance_id)


class FakeCaptureService:
    """Records ``capture_async`` kwargs; serves a canned staleness map."""

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
        return {"snapshot_id": "snap-new", "status": SNAPSHOT_STATUS_RUNNING, "error": None}

    async def staleness_report(self, snapshot_id: str) -> dict[str, Any] | None:
        return dict(self.staleness)


class FakeSearchService:
    """Canned candidate list for the R9 / R14 internal searches."""

    def __init__(self, results: list[dict[str, Any]] | None = None, error: str | None = None):
        self.results = results or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(self, query: str, *, project_id: str, tags=None, tag_mode="all", limit=10, **_: Any) -> dict[str, Any]:
        self.calls.append(
            {"query": query, "project_id": project_id, "tags": list(tags or []), "tag_mode": tag_mode, "limit": limit}
        )
        return {"results": list(self.results), "error": self.error}


class FakeManager:
    """Records the spawn / metadata-write ordering (R6b)."""

    def __init__(self, rows: dict[str, Any], repo: SnapshotRepository):
        self._instance_repository = FakeInstanceRepo(rows)
        self._snapshot_repo = repo
        self._snapshot_service = FakeCaptureService()
        self._snapshot_search_service = FakeSearchService()
        self._project_repository = None
        self.events: list[str] = []
        self.spawn_calls: list[dict[str, Any]] = []
        self.metadata_calls: list[tuple[str, dict[str, Any]]] = []

    def spawn_instance(self, **kwargs: Any) -> tuple[str, str | None]:
        self.events.append("spawn")
        self.spawn_calls.append(kwargs)
        return ("new-inst-1", None)

    def set_metadata_many(self, instance_id: str, updates: dict[str, Any]) -> None:
        self.events.append("metadata")
        self.metadata_calls.append((instance_id, dict(updates)))


@pytest.fixture
def caller_rows() -> dict[str, Any]:
    """caller (=current) → target descendant chain; caller lives in p1."""

    def _row(instance_id: str, **kw: Any) -> Any:
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

    return {
        "caller-1": _row("caller-1", agent_id="coder"),
        "inst-1": _row("inst-1", agent_id="worker", parent_id="caller-1"),
        "leader-1": _row("leader-1", agent_id="leader"),
        "ari-1": _row("ari-1", agent_id="ari"),
    }


@pytest.fixture
def manager(engine: Engine, caller_rows: dict[str, Any]) -> FakeManager:
    return FakeManager(caller_rows, SnapshotRepository(engine))


@pytest.fixture
def tools(manager: FakeManager) -> list[Any]:
    return create_snapshot_tools(manager, "caller-1", "coder", None)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _candidate(snapshot_id: str, tags: list[str], freshness: str = "fresh", age: float = 1.0) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "name": "snap",
        "tags": tags,
        "freshness": freshness,
        "age_days": age,
        "summary": "preview text",
    }


# ============================================================================
# R15 gate (write side ONLY — rider (i) isolation)
# ============================================================================


class TestR15Gate:
    def test_default_is_off(self):
        assert is_snapshot_create_enabled() is False

    def test_disabled_result_shape_exact(self, tools):
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result == {"disabled": True, "error": "snapshot_create disabled by settings toggle"}

    def test_on_proceeds_to_capture(self, tools, manager, monkeypatch):
        monkeypatch.setattr("daemon.tools.snapshot_tools.is_snapshot_create_enabled", lambda: True)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["error"] is None
        assert len(manager._snapshot_service.calls) == 1

    def test_search_never_gated(self, tools):
        """R15 OFF must NOT block the read-only search (rider (i))."""
        result = _run(tools[1].ainvoke({"query": "anything"}))
        assert result == {"results": [], "error": None}

    def test_spawn_never_gated(self, tools, manager):
        """R15 OFF must NOT block consumption — spawn resolves cold."""
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["error"] is None
        assert result["started"] == "cold"


# ============================================================================
# R9 verdicts inside snapshot_create
# ============================================================================


class TestR9Verdicts:
    def _enable(self, monkeypatch):
        monkeypatch.setattr("daemon.tools.snapshot_tools.is_snapshot_create_enabled", lambda: True)

    def test_reuse_strong_match_no_capture(self, tools, manager, monkeypatch):
        self._enable(monkeypatch)
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-existing", ["kind:implementation", "subsystem:upgrade-pipeline", "runtime:0.14.2"])]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["status"] == "reused-existing-snapshot-id"
        assert result["snapshot_id"] == "snap-existing"
        assert result["verdict"] == "reuse"
        assert result["digest_preview"] == "preview text"
        assert manager._snapshot_service.calls == []  # NO create on REUSE

    def test_reuse_requires_same_kind(self, tools, manager, monkeypatch):
        """Kind mismatch → not REUSE even with 2+ overlapping tags."""
        self._enable(monkeypatch)
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-x", ["kind:review", "subsystem:upgrade-pipeline", "feature:f"])]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["status"] != "reused-existing-snapshot-id"
        assert len(manager._snapshot_service.calls) == 1

    def test_expired_candidate_never_reused(self, tools, manager, monkeypatch):
        self._enable(monkeypatch)
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-old", VALID_TAGS + ["runtime:old"], freshness="expired")]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["status"] != "reused-existing-snapshot-id"

    def test_supersede_same_target_threaded_to_capture(self, engine, tools, manager, monkeypatch):
        self._enable(monkeypatch)
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-prev", target="inst-1", status=SNAPSHOT_STATUS_ACTIVE)
        )
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["verdict"] == "supersede"
        capture_kwargs = manager._snapshot_service.calls[0]
        assert capture_kwargs["supersedes_snapshot_id"] == "snap-prev"

    def test_new_when_no_match_no_predecessor(self, tools, manager, monkeypatch):
        self._enable(monkeypatch)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["verdict"] == "new"
        assert manager._snapshot_service.calls[0]["supersedes_snapshot_id"] is None

    def test_create_fresh_when_candidates_exist_but_weak(self, tools, manager, monkeypatch):
        self._enable(monkeypatch)
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-weak", ["kind:review", "topic:other"])]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["verdict"] == "create-fresh"
        assert len(manager._snapshot_service.calls) == 1

    def test_invalid_tags_fail_soft_with_error(self, tools, monkeypatch):
        self._enable(monkeypatch)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": ["not-a-dim"]}))
        assert result["error"].startswith("ERROR: invalid judgment tags")
        assert result["snapshot_id"] is None

    def test_auth_non_descendant_refused(self, engine, caller_rows, monkeypatch):
        self._enable(monkeypatch)
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["status"] == "refused"
        assert "not your transitive descendant" in result["error"]

    def test_auth_self_target_allowed(self, engine, caller_rows, monkeypatch):
        self._enable(monkeypatch)
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        result = _run(tools[0].ainvoke({"target_instance_id": "leader-1", "name": "n", "tags": VALID_TAGS}))
        assert result["error"] is None
        assert len(manager._snapshot_service.calls) == 1


# ============================================================================
# snapshot_search wrapper (read-only)
# ============================================================================


class TestSnapshotSearchTool:
    def test_default_scopes_to_caller_project(self, tools, manager):
        result = _run(tools[1].ainvoke({"query": "q"}))
        assert result == {"results": [], "error": None}
        assert manager._snapshot_search_service.calls[0]["project_id"] == "p1"

    def test_explicit_project_passthrough(self, tools, manager):
        _run(tools[1].ainvoke({"query": "q", "project_id": "p2"}))
        assert manager._snapshot_search_service.calls[0]["project_id"] == "p2"

    def test_limit_clamped_to_50(self, tools, manager):
        _run(tools[1].ainvoke({"query": "q", "limit": 500}))
        assert manager._snapshot_search_service.calls[0]["limit"] == 50

    def test_freshness_post_filter(self, tools, manager):
        manager._snapshot_search_service = FakeSearchService(
            [
                _candidate("fresh-1", [], freshness="fresh", age=1.0),
                _candidate("old-1", [], freshness="stale", age=20.0),
            ]
        )
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[1].ainvoke({"query": "q", "freshness_max_age_days": 7}))
        ids = [r["snapshot_id"] for r in result["results"]]
        assert ids == ["fresh-1"]

    def test_service_error_surfaced_not_raised(self, tools, manager):
        manager._snapshot_search_service = FakeSearchService(error="boom")
        tools = create_snapshot_tools(manager, "caller-1", "coder", None)
        result = _run(tools[1].ainvoke({"query": "q"}))
        assert result["error"] == "boom"


# ============================================================================
# spawn_hot_instance — R14 auto-fallback contract (rider (h) fail-soft)
# ============================================================================


RESULT_KEYS = {"instance_id", "started", "snapshot_id", "staleness", "hint", "error"}


class TestSpawnHotInstance:
    def _auth_ok(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda caller, requested, tag=None: None
        )

    def _auth_denied(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership",
            lambda caller, requested, tag=None: "agent 'x' is not in caller's team",
        )

    def test_result_contract_keys_exact(self, tools, monkeypatch):
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))
        assert set(result.keys()) == RESULT_KEYS

    def test_no_hit_cold_with_reason(self, tools, monkeypatch):
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))
        assert result["started"] == "cold"
        assert result["snapshot_id"] is None
        assert result["instance_id"] == "new-inst-1"
        assert "reason: no-hit" in result["hint"]
        assert "No matching snapshot — spawned cold" in result["hint"]
        assert result["error"] is None

    def test_auth_failure_is_an_error_not_a_spawn(self, tools, manager, monkeypatch):
        self._auth_denied(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["error"] is not None
        assert result["instance_id"] is None
        assert manager.events == []  # nothing spawned

    def test_explicit_warm(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            _snapshot(
                snapshot_id="snap-1",
                tags=["kind:implementation", "subsystem:upgrade-pipeline"],
            )
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-1"}))
        assert result["started"] == "warm"
        assert result["snapshot_id"] == "snap-1"
        assert result["instance_id"] == "new-inst-1"
        assert result["hint"].startswith("Warm-started from snapshot snap-1")
        assert result["hint"].endswith("tags kind:implementation, subsystem:upgrade-pipeline)")
        assert result["error"] is None

    def test_explicit_missing_snapshot_cold_verify_failed(self, tools, manager, monkeypatch):
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "nope"}))
        assert result["started"] == "cold"
        assert "reason: verify-failed" in result["hint"]
        assert result["error"] is None  # fail-soft, NEVER an error

    def test_superseded_never_spawns(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        manager._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-old", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-old"}))
        assert result["started"] == "cold"
        assert "reason: verify-failed" in result["hint"]

    def test_project_mismatch_cold_verify_failed(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        manager._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-other", project_id="other-project")
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-other"}))
        assert result["started"] == "cold"
        assert "reason: verify-failed" in result["hint"]

    def test_expired_top_match_cold(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-stale", target="inst-1"))
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-stale", [], freshness="expired", age=40.0)]
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "cold"
        assert "reason: expired" in result["hint"]

    def test_stale_warm_carries_drift_warnings(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        manager._snapshot_service.staleness = {
            "snapshot_age_days": 9.0,
            "freshness": "stale",
            "warnings": [],
            "repo_state": None,
        }
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-1", [], freshness="stale", age=9.0)]
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "warm"
        assert "STALE" in result["hint"]
        assert result["staleness"]["freshness"] == "stale"
        assert any("stale" in w for w in result["staleness"]["warnings"])

    # ── Wave 2b review FIX 3 — §4.3: staleness is ALWAYS a dict ────

    def test_staleness_is_dict_on_cold_no_hit(self, tools, monkeypatch):
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "cold"
        assert isinstance(result["staleness"], dict)

    def test_staleness_is_dict_on_cold_expired(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-1", [], freshness="expired", age=30.0)]
        )
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "cold"
        assert "expired" in result["hint"]
        assert isinstance(result["staleness"], dict)

    def test_staleness_is_dict_on_cold_verify_failed(self, tools, manager, monkeypatch):
        self._auth_ok(monkeypatch)
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "missing"})
        )
        assert result["started"] == "cold"
        assert isinstance(result["staleness"], dict)

    def test_staleness_is_dict_when_service_unavailable(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        manager._snapshot_service = None  # staleness seam unavailable
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-1"})
        )
        assert result["started"] == "warm"  # warm start, no staleness report
        assert isinstance(result["staleness"], dict)
        assert result["staleness"] == {}

    # ── Wave 2b review FIX 4 — exactly 6 keys; warnings ride
    #    ``staleness.warnings`` on the warm + verify=git path ────────

    def test_git_verify_warnings_surface_in_staleness_no_top_level_key(
        self, engine, caller_rows, monkeypatch
    ):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        monkeypatch.setattr(
            "daemon.tools.snapshot_tools._git_repo_state",
            lambda repo_path, git_sha: {
                "snapshot_head": git_sha,
                "current_head": "def5678",
                "diverged_files": 3,
            },
        )
        result = _run(
            tools[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "snap-1", "verify": "git"}
            )
        )
        assert result["started"] == "warm"
        # §4.3 contract: EXACTLY 6 keys — no top-level ``warnings``.
        assert set(result.keys()) == RESULT_KEYS
        assert result["staleness"]["repo_state"]["diverged_files"] == 3
        # The git-anchor warning surfaces via staleness.warnings.
        assert any(
            "repo diverged" in w for w in result["staleness"]["warnings"]
        )

    def test_internal_search_crash_still_spawns_cold(self, engine, caller_rows, monkeypatch):
        """Rider (h): a search-system fault must NEVER raise — cold spawn."""
        manager = FakeManager(caller_rows, SnapshotRepository(engine))

        class ExplodingSearch(FakeSearchService):
            async def search(self, *a: Any, **kw: Any) -> dict[str, Any]:
                raise RuntimeError("search backend down")

        manager._snapshot_search_service = ExplodingSearch()
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "cold"
        assert result["instance_id"] == "new-inst-1"
        assert result["error"] is None

    def test_spawn_system_fault_reports_error(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))

        def boom(**kw: Any):
            raise ValueError("Agent not found")

        manager.spawn_instance = boom  # type: ignore[method-assign]
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "ghost", "task": "t"}))
        assert result["error"] is not None
        assert "spawn failed" in result["error"]


# ============================================================================
# R6b warm-path ordering + stamp content
# ============================================================================


class TestWarmPathOrdering:
    def test_metadata_written_after_spawn_before_return(self, engine, caller_rows, monkeypatch):
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-1"}))
        # R6b ordering: spawn FIRST, atomic metadata write SECOND, both
        # BEFORE the instance_id is returned.
        assert manager.events == ["spawn", "metadata"]
        assert result["instance_id"] == "new-inst-1"
        instance_id, updates = manager.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert updates["spawned_from_snapshot_id"] == "snap-1"
        assert updates["snapshot_digest"] == {"task_summary_text": "did the thing"}

    def test_cold_path_writes_no_stamp(self, tools, manager, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert manager.events == ["spawn"]  # NO metadata write on cold


# ============================================================================
# R12 create-mints-successor at the capture lane
# ============================================================================


class TestR12LaneMint:
    def test_finish_row_routes_active_supersede_through_create_successor(self, engine: Engine):
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(_snapshot(snapshot_id="prev", target="inst-1"))
        successor = _snapshot(
            snapshot_id="succ",
            target="inst-1",
            status=SNAPSHOT_STATUS_RUNNING,
            digest={"supersedes_snapshot_id": "prev"},
        )
        repo.create_with_embeddings(successor)
        executor = SnapshotExecutor(None, repo)

        updated = asyncio.run(
            executor._finish_row(
                successor,
                status=SNAPSHOT_STATUS_ACTIVE,
                digest={"decisions": ["d1"]},
                effective_model="m",
                started_monotonic=0.0,
            )
        )
        # Successor ACTIVE with the R12 pointer + merged digest; the
        # predecessor flipped superseded in the SAME transaction.
        # Wave 2b review FIX 1: the pointer rides the COLUMN only —
        # the stash key is stripped from the persisted digest.
        assert updated.status == SNAPSHOT_STATUS_ACTIVE
        assert updated.supersedes_snapshot_id == "prev"
        assert "supersedes_snapshot_id" not in updated.digest
        assert updated.digest["decisions"] == ["d1"]
        assert repo.get("prev").status == SNAPSHOT_STATUS_SUPERSEDED

    def test_finish_row_plain_active_keeps_predecessor_active(self, engine: Engine):
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(_snapshot(snapshot_id="unrelated", target="inst-2"))
        plain = _snapshot(snapshot_id="plain", target="inst-1", status=SNAPSHOT_STATUS_RUNNING)
        repo.create_with_embeddings(plain)
        executor = SnapshotExecutor(None, repo)
        updated = asyncio.run(
            executor._finish_row(
                plain,
                status=SNAPSHOT_STATUS_ACTIVE,
                digest={"decisions": []},
                effective_model="m",
                started_monotonic=0.0,
            )
        )
        assert updated.status == SNAPSHOT_STATUS_ACTIVE
        assert repo.get("unrelated").status == SNAPSHOT_STATUS_ACTIVE

    def test_completed_successor_digest_clean_and_warm_metadata_inherits(
        self, engine: Engine, caller_rows: dict[str, Any], monkeypatch
    ):
        """Wave 2b review FIX 1 — end-to-end digest hygiene.

        A capture minted WITH a supersedes pointer completes with the
        pointer in the COLUMN only; the warm spawn's
        ``instance_metadata["snapshot_digest"]`` write (the
        ``consumed.digest`` flow that feeds the LLM injection) carries
        no ``supersedes_snapshot_id`` key.
        """
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(_snapshot(snapshot_id="prev", target="inst-1"))
        successor = _snapshot(
            snapshot_id="succ",
            target="inst-1",
            status=SNAPSHOT_STATUS_RUNNING,
            digest={"supersedes_snapshot_id": "prev"},
        )
        repo.create_with_embeddings(successor)
        executor = SnapshotExecutor(None, repo)
        updated = asyncio.run(
            executor._finish_row(
                successor,
                status=SNAPSHOT_STATUS_ACTIVE,
                digest={"decisions": ["d1"]},
                effective_model="m",
                started_monotonic=0.0,
            )
        )
        # Terminal row: column carries the pointer, digest does not.
        assert updated.supersedes_snapshot_id == "prev"
        assert "supersedes_snapshot_id" not in updated.digest
        # Warm spawn from the completed row → clean metadata stamp.
        manager = FakeManager(caller_rows, repo)
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "succ"})
        )
        assert result["started"] == "warm"
        instance_id, metadata_updates = manager.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert metadata_updates["spawned_from_snapshot_id"] == "succ"
        assert "supersedes_snapshot_id" not in metadata_updates["snapshot_digest"]
        assert metadata_updates["snapshot_digest"] == {"decisions": ["d1"]}


# ============================================================================
# Registration chain (rider (d)) — the 6+1-file chain
# ============================================================================


class TestRegistrationChain:
    def test_dynamic_tool_names_contains_all_three(self):
        from daemon.tools._tool_registry import DYNAMIC_TOOL_NAMES

        for name in ("snapshot_create", "snapshot_search", "spawn_hot_instance"):
            assert name in DYNAMIC_TOOL_NAMES

    def test_category_modules_entry(self):
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert CATEGORY_MODULES["snapshot"] == "daemon.tools.snapshot_tools"

    def test_factory_returns_three_tools_with_categories(self):
        tools = create_snapshot_tools(None, "", "", None)
        by_name = {t.name: getattr(t, "_tool_category", None) for t in tools}
        assert by_name == {
            "snapshot_create": "snapshot",
            "snapshot_search": "snapshot",
            # Consumption ships via the instance category (auto-grant).
            "spawn_hot_instance": "instance",
        }

    def test_source_discovery_includes_all_three_word_boundary(self):
        """Rider (e): source-discovery + \\b-bounded grep, never substring."""
        from daemon.tools._tool_registry import (
            KNOWN_TOOL_NAMES,
            discover_source_only_tool_names,
        )

        discovered = discover_source_only_tool_names()
        for name in ("snapshot_create", "snapshot_search", "spawn_hot_instance"):
            assert name in discovered
            assert name in KNOWN_TOOL_NAMES
        source = (TOOLS_DIR / "snapshot_tools.py").read_text(encoding="utf-8")
        # \b-bounded greps ONLY (rider (e)): substring 'hot' collides
        # with 'snapshot' / 'shot' — the bounded forms are the
        # discipline this suite pins.
        assert re.search(r"\bspawn_hot_instance\b", source)

    def test_loader_warm_list_wires_snapshot_factory(self):
        """Warm-list step: the scan registers the snapshot categories
        (the empty-category cache-pin hazard)."""
        from daemon.loader import _ensure_tool_metadata_populated
        from daemon.tools._tool_registry import _tool_metadata

        _ensure_tool_metadata_populated()
        assert _tool_metadata["snapshot_create"]["category"] == "snapshot"
        assert _tool_metadata["snapshot_search"]["category"] == "snapshot"
        assert _tool_metadata["spawn_hot_instance"]["category"] == "instance"

    def test_instance_factory_call_present(self):
        """Chain step 3: create_instance_tools extends with the factory."""
        source = (TOOLS_DIR / "instance.py").read_text(encoding="utf-8")
        assert re.search(r"\bcreate_snapshot_tools\b", source)
        assert "snapshot_tool_list" in source

    def test_grants_meta_json_allow_entries(self):
        import json

        agents_dir = TOOLS_DIR.parents[1] / "agents"
        for agent in ("worker", "coder", "tester"):
            meta = json.loads((agents_dir / agent / "meta.json").read_text(encoding="utf-8"))
            allow = meta["tools"]["allow"]
            assert "snapshot_create" in allow, agent
            assert "snapshot_search" in allow, agent

    def test_grants_resolution_matrix(self):
        """worker/coder/tester gain the 2 tools; spawn ships via the
        instance category for holders; ari gains NOTHING."""
        from daemon.loader import _ensure_tool_metadata_populated
        from daemon.registry import get_registry
        from daemon.tools.instance import resolve_tool_filter

        _ensure_tool_metadata_populated()
        registry = get_registry()
        expectations = {
            # (agent): (create, search, hot-spawn)
            "worker": (True, True, False),  # leaf: excluded architecturally
            "coder": (True, True, True),
            "tester": (True, True, True),
            "leader": (False, False, True),  # consumer only
            "ari": (False, False, False),  # PERMANENT exclusion
        }
        for agent, (create, search, hot) in expectations.items():
            meta = registry.get_resolved(agent)
            allow = list(meta.tools.allow) if meta and meta.tools and meta.tools.allow else None
            deny = list(meta.tools.deny) if meta and meta.tools and meta.tools.deny else None
            allowed = resolve_tool_filter(allow=allow, deny=deny)
            assert allowed is not None, agent
            assert ("snapshot_create" in allowed) == create, agent
            assert ("snapshot_search" in allowed) == search, agent
            assert ("spawn_hot_instance" in allowed) == hot, agent
