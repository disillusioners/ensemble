"""Agent Snapshot tool-surface tests (Wave 2b, PR6 + unify-spawn-tools).

Covers the commissioned Wave 2b contracts at the TOOL level, updated
for unify-spawn-tools (2026-10-05 — ``spawn_hot_instance`` removed;
the warm-start flow rides the unified ``spawn_instance`` tool, gated
by the TARGET agent's ``snapshot_enabled`` meta flag):

* **R15 gate** — ``snapshot_create`` disabled by default (single
  helper seam ``is_snapshot_create_enabled``); ``snapshot_search`` and
  the spawn-consumption path are NEVER R15-gated (rider (i)
  isolation).
* **R9 search-before-create** — the four verdicts (REUSE / SUPERSEDE /
  NEW / CREATE-FRESH) decided deterministically inside the tool.
* **R12 tool-level supersession** — SUPERSEDE threads
  ``supersedes_snapshot_id`` into ``capture_async`` (create-mints-
  successor); REUSE returns the existing id without a capture. The
  lane-level atomic flip (executor terminal write →
  ``create_successor``) is pinned at the repository boundary.
* **R14 auto-fallback via the unified spawn** — expired → cold,
  stale-not-expired → warm + drift warnings, no-hit → cold,
  verify-fail → cold + warning; the fail-soft invariant holds: never
  an error on miss. The former 6-key dict contract is now the
  ``[snapshot] started: warm|cold — …`` citation LINE appended to
  spawn_instance's STRING return.
* **Unified-spawn gate** — TARGET-agent ``snapshot_enabled`` consult
  (fail-closed; steering params on a gate-off target are ignored with
  ONE informational line; the versioned developer[v2] shape resolves).
* **R6b warm-path ordering** — ``spawn_instance`` THEN the atomic
  ``set_metadata_many({snapshot_digest, spawned_from_snapshot_id})``
  BEFORE the R18 enqueue; the cold path writes nothing.
* **R18 auto-dispatch** — default-on enqueue of ``task`` as the
  child's first turn, opt-out, empty-task loud skip, enqueue-failure
  loud ERROR tail, and the non-queued/None tripwire pins.
* **Registration chain** (rider (d)) — both snapshot names in
  ``DYNAMIC_TOOL_NAMES`` (``spawn_hot_instance`` GONE), the
  ``snapshot`` category module entry, the loader warm-list, the
  ``instance.py`` factory call, and ``KNOWN_TOOL_NAMES``/source-
  discovery agreement.

Rider (e): every regex/grep assertion on the removed hot-spawn tool
name uses word-boundary forms — substring matching collides with
``snapshot`` / ``shot``.

The spawn-path tests drive the REAL ``create_instance_tools``
factory (via ``tests.helpers.send_message_fixtures.patch_heavy_helpers``,
the proven send_message/spawn test seam) with a MagicMock manager
that carries the snapshot seams + spawn/metadata/enqueue recorders
(``_unified_spawn_manager``) — so the ordering and output-shape pins
exercise the production call path, not a re-implementation.
"""

from __future__ import annotations

import asyncio
import logging
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
    SNAPSHOT_STEERING_IGNORED_LINE,
    create_snapshot_tools,
    is_snapshot_create_enabled,
)
from tests.helpers.send_message_fixtures import (
    make_spawn_manager,
    patch_heavy_helpers,
)
from tests.unit.tools._fakes import FakeAsyncMessageResult

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
    """Records ``capture_async`` kwargs; serves a canned staleness map.

    ``capture_status`` defaults to RUNNING — the real
    ``SnapshotExecutor.capture_async`` contract (inserts the running
    ledger row, captures in background; ACTIVE only lands on
    completion). Parameterized so terminal-state scenarios can opt in
    explicitly instead of hard-picking one canned status.
    """

    def __init__(
        self,
        staleness: dict[str, Any] | None = None,
        capture_status: str = SNAPSHOT_STATUS_RUNNING,
    ):
        self.calls: list[dict[str, Any]] = []
        self.capture_status = capture_status
        self.staleness = staleness or {
            "snapshot_age_days": 1.0,
            "freshness": "fresh",
            "warnings": [],
            "repo_state": None,
        }

    async def capture_async(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"snapshot_id": "snap-new", "status": self.capture_status, "error": None}

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


class _FakeAsyncMessageResult(FakeAsyncMessageResult):
    """Wave-2b alias — see :class:`tests.unit.tools._fakes.FakeAsyncMessageResult`.

    Kept as a thin subclass (not a verbatim duplicate) so the Wave-2b
    suite continues to use its convention (``_FakeAsyncMessageResult``)
    and the shared fake stays importable from the canonical module.
    The default ``message_id`` stays ``'msg-auto-1'`` for Wave-2b tests
    that pre-date the R18 tripwire field.
    """

    def __init__(
        self,
        message_id: str = "msg-auto-1",
        queued: bool = True,
        status: str = "queued",
    ) -> None:
        super().__init__(message_id=message_id, queued=queued, status=status)


class FakeManager:
    """Records the spawn / metadata-write ordering (R6b) + R18 enqueue.

    Used directly by the snapshot_create / snapshot_search tool tests.
    The unified ``spawn_instance`` tests use :func:`_unified_spawn_manager`
    instead (MagicMock baseline — the real ``create_instance_tools``
    factory touches a wider manager surface).
    """

    def __init__(self, rows: dict[str, Any], repo: SnapshotRepository):
        from types import SimpleNamespace

        self._instance_repository = FakeInstanceRepo(rows)
        self._snapshot_repo = repo
        self._snapshot_service = FakeCaptureService()
        self._snapshot_search_service = FakeSearchService()
        self._project_repository = None
        # Unified-spawn surface (child cap + tier config + fallback
        # notice) — inert for the create/search tools, required when
        # this fake is adapted for spawn tests.
        self.config = SimpleNamespace(
            llm=SimpleNamespace(allowed_models=["agentic"]),
            limits=SimpleNamespace(max_children_per_instance=50),
        )
        self._lifecycle_service = SimpleNamespace(
            _format_model_fallback_notice=lambda model, validated: ""
        )
        self.events: list[str] = []
        self.spawn_calls: list[dict[str, Any]] = []
        self.metadata_calls: list[tuple[str, dict[str, Any]]] = []
        # R18 enqueue recorder — mirrors the manager's
        # ``enqueue_message`` async surface so the test can assert
        # on-call (kwargs), opt-out (auto_dispatch=False), and the
        # failure-injection path (set ``enqueue_raise`` to a BaseException).
        # The default result is a fake AsyncMessageResult with message_id
        # "msg-auto-1"; tests can swap it.
        self.enqueue_calls: list[dict[str, Any]] = []
        self.enqueue_raise: BaseException | None = None
        self.enqueue_result: Any = _FakeAsyncMessageResult()

    async def enqueue_message(self, **kwargs: Any) -> Any:
        self.events.append("enqueue")
        self.enqueue_calls.append(kwargs)
        if self.enqueue_raise is not None:
            raise self.enqueue_raise
        return self.enqueue_result

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


# ============================================================================
# Unified-spawn helpers (unify-spawn-tools) — real factory, recorded manager
# ============================================================================


def _unified_spawn_manager(
    rows: dict[str, Any],
    repo: SnapshotRepository,
) -> Any:
    """MagicMock manager wired for the UNIFIED ``spawn_instance`` tool.

    Baseline: ``tests.helpers.send_message_fixtures.make_spawn_manager``
    (the proven surface against the real ``create_instance_tools``
    factory). Snapshot seams + the instance repo ride over in
    FakeManager shapes; the spawn / metadata / enqueue recorders
    append to ``m.events`` so the R6b+R18 ordering pins
    (spawn → metadata → enqueue) stay assertable.
    """
    m = make_spawn_manager()
    m._snapshot_repo = repo
    m._snapshot_service = FakeCaptureService()
    m._snapshot_search_service = FakeSearchService()
    m._snapshot_metrics_service = None
    m._instance_repository = FakeInstanceRepo(rows)
    m._project_repository = None
    m.events = []
    m.spawn_calls = []
    m.metadata_calls = []
    m.enqueue_calls = []
    m.enqueue_raise: BaseException | None = None
    m.enqueue_result: Any = _FakeAsyncMessageResult()

    def _spawn(**kw: Any) -> tuple[str, str | None]:
        m.events.append("spawn")
        m.spawn_calls.append(dict(kw))
        return ("new-inst-1", None)

    def _meta(instance_id: str, updates: dict[str, Any]) -> None:
        m.events.append("metadata")
        m.metadata_calls.append((instance_id, dict(updates)))

    async def _enqueue(**kw: Any) -> Any:
        m.events.append("enqueue")
        m.enqueue_calls.append(dict(kw))
        if m.enqueue_raise is not None:
            raise m.enqueue_raise
        return m.enqueue_result

    m.spawn_instance = _spawn
    m.set_metadata_many = _meta
    m.enqueue_message = _enqueue
    return m


def _spawn_tool(manager: Any, caller_id: str = "caller-1", agent_id: str = "coder") -> Any:
    """Build the unified ``spawn_instance`` tool bound to ``manager``.

    Drives the REAL ``create_instance_tools`` factory under the shared
    heavy-helper patch stack (RAG / MCP / project / job / … factories
    disabled) and returns just the spawn_instance tool.
    """
    from daemon.tools.instance import create_instance_tools

    patches = patch_heavy_helpers()
    for _p in patches:
        _p.start()
    try:
        all_tools = create_instance_tools(
            manager, caller_id, agent_id=agent_id, version_tag=None
        )
    finally:
        for _p in reversed(patches):
            _p.stop()
    for _t in all_tools:
        if getattr(_t, "name", None) == "spawn_instance":
            return _t
    raise RuntimeError("spawn_instance tool not found")


def _snapshot_gate(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """Steer the TARGET-agent snapshot gate at its real seam.

    The unified spawn body consults
    ``daemon.tools.instance._target_snapshot_enabled``; tests steer
    that module attribute directly. Gate-OFF tests that do NOT call
    this helper go through the REAL registry consult (worker has no
    ``snapshot_enabled`` → fail-closed False).
    """
    monkeypatch.setattr(
        "daemon.tools.instance._target_snapshot_enabled",
        lambda agent_id, version_tag=None: enabled,
    )


def _gate(monkeypatch, enabled: bool) -> None:
    """Steer the R15 gate at its REAL seam.

    Since the sync-bridge fix, ``snapshot_create`` awaits
    ``daemon.tools.snapshot_tools.get_snapshot_create_enabled``
    directly — patch THAT (async) name; the old sync
    ``is_snapshot_create_enabled`` stub is no longer on the tool path.
    """
    async def _fake_enabled(repo=None):
        return enabled

    monkeypatch.setattr(
        "daemon.tools.snapshot_tools.get_snapshot_create_enabled",
        _fake_enabled,
    )


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
        # No manager → constant fallback (fail-closed)
        assert is_snapshot_create_enabled() is False
        # No manager kwarg → still False
        assert is_snapshot_create_enabled(manager=None) is False

    def test_disabled_result_shape_exact(self, tools):
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result == {"disabled": True, "error": "snapshot_create disabled by settings toggle"}

    def test_on_proceeds_to_capture(self, tools, manager, monkeypatch):
        _gate(monkeypatch, True)
        result = _run(tools[0].ainvoke({"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}))
        assert result["error"] is None
        assert len(manager._snapshot_service.calls) == 1

    def test_search_never_gated(self, tools):
        """R15 OFF must NOT block the read-only search (rider (i))."""
        result = _run(tools[1].ainvoke({"query": "anything"}))
        assert result == {"results": [], "error": None}

    def test_spawn_consumption_never_r15_gated(self, engine, caller_rows, monkeypatch):
        """R15 OFF must NOT block consumption (rider (i), renamed for
        unify-spawn-tools): with the R15 toggle steered OFF at its real
        seam and the TARGET gate ON, an explicit-id warm start still
        succeeds — consumption is gated by ``snapshot_enabled`` only.
        """
        _gate(monkeypatch, False)  # R15 OFF (capture disabled)
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-r15", target="inst-1")
        )
        _snapshot_gate(monkeypatch, True)
        _auth = lambda c, r, tag=None: None  # noqa: E731 — auth open
        monkeypatch.setattr("daemon.tools.instance._check_team_membership", _auth)
        result = _run(
            _spawn_tool(m).ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-r15",
                }
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "[snapshot] started: warm" in result


# ============================================================================
# R9 verdicts inside snapshot_create
# ============================================================================


class TestR9Verdicts:
    def _enable(self, monkeypatch):
        _gate(monkeypatch, True)

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
# Unified-spawn snapshot flow — R14 auto-fallback contract (rider (h)
# fail-soft), carried by ``spawn_instance`` since unify-spawn-tools.
# The former 6-key dict contract (RESULT_KEYS) is gone with the
# removed tool; the live surface is the ``[snapshot] started: …``
# citation line.
# ============================================================================


class TestUnifiedSpawnSnapshot:
    """The R14 warm/cold state machine, now riding the unified
    ``spawn_instance`` tool behind the TARGET-agent ``snapshot_enabled``
    gate (unify-spawn-tools, 2026-10-05).

    The former 6-key dict contract is gone with the removed tool; the
    observable surface is the STRING return with the ``[snapshot]
    started: warm|cold — …`` citation line. Auth denials are the
    tool's plain ERROR strings (the ``started: "blocked"`` dict shape
    is gone too).
    """

    def _auth_ok(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda caller, requested, tag=None: None
        )

    def _auth_denied(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership",
            lambda caller, requested, tag=None: "agent 'x' is not in caller's team",
        )

    # ── gate OFF (the default for most agents) ────────────────────────

    def test_gate_off_plain_cold_no_snapshot_work(self, engine, caller_rows, monkeypatch):
        """Gate OFF (real registry consult — worker has no flag) ⇒
        exactly the pre-unification plain cold spawn: NO snapshot
        search, NO repo reads, NO stamp, NO [snapshot] line."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "[snapshot]" not in result
        assert m._snapshot_search_service.calls == []  # no snapshot search
        assert m.metadata_calls == []  # no stamp
        assert m.spawn_calls, "the spawn itself must still happen"

    def test_gate_off_steering_params_ignored_with_one_line(self, engine, caller_rows, monkeypatch):
        """snapshot_id/tags on a gate-OFF target: ONE informational
        line, then a plain cold spawn (fail-closed, never an error)."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                    "tags": ["kind:implementation"],
                }
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert SNAPSHOT_STEERING_IGNORED_LINE in result
        assert "[snapshot] started:" not in result
        assert m._snapshot_search_service.calls == []  # never searched
        assert m.metadata_calls == []

    # ── auth (unchanged spawn_instance ERROR behavior — NOT a dict) ──

    def test_auth_failure_is_plain_error_string_no_spawn(self, engine, caller_rows, monkeypatch):
        """Membership denial: the unified tool's plain ERROR string
        (NOT the removed ``started: "blocked"`` dict); nothing spawns."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_denied(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert result.startswith("ERROR:")
        assert "is not in caller's team" in result
        assert m.events == []  # nothing spawned, no enqueue

    def test_no_caller_is_wiring_error_string(self, engine, caller_rows, monkeypatch):
        """Empty caller agent_id: the tool's wiring-bug ERROR string."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m, agent_id="").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert result.startswith("ERROR: spawn_instance invoked without a caller agent_id")
        assert m.events == []

    # ── warm paths (gate ON) ─────────────────────────────────────────

    def test_explicit_warm_citation_line(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(
            _snapshot(
                snapshot_id="snap-1",
                tags=["kind:implementation", "subsystem:upgrade-pipeline"],
            )
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                }
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert (
            "[snapshot] started: warm — Warm-started from snapshot snap-1 "
            "(age 1.0d; tags kind:implementation, subsystem:upgrade-pipeline)"
            in result
        )
        assert "auto-dispatched as first turn" in result  # task given

    def test_explicit_missing_snapshot_cold_verify_failed(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "nope",
                }
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "[snapshot] started: cold" in result
        assert "reason: verify-failed" in result

    def test_superseded_never_spawns_warm(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-old", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-old",
                }
            )
        )
        assert "[snapshot] started: cold" in result
        assert "reason: verify-failed" in result
        assert m.metadata_calls == []  # no digest stamp on cold

    def test_project_mismatch_cold_verify_failed(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-other", project_id="other-project")
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-other",
                }
            )
        )
        assert "[snapshot] started: cold" in result
        assert "reason: verify-failed" in result

    def test_cross_project_optin_consumes_warm(self, engine, caller_rows, monkeypatch):
        """D8 explicit opt-in: allow_cross_project=True consumes the
        foreign snapshot; the citation line records the consent."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-xp", project_id="p2")
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-xp",
                    "allow_cross_project": True,
                }
            )
        )
        assert "[snapshot] started: warm" in result
        assert "cross-project consume from p2" in result
        assert "(allow_cross_project=True)" in result

    def test_expired_top_match_cold(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-stale", target="inst-1"))
        m._snapshot_search_service = FakeSearchService(
            [_candidate("snap-stale", [], freshness="expired", age=40.0)]
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert "[snapshot] started: cold" in result
        assert "reason: expired" in result

    def test_internal_search_hit_warms(self, engine, caller_rows, monkeypatch):
        """Gate ON + no explicit id: the task-based internal search
        top hit warms the spawn (the R14 default path)."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-hit", target="inst-1"))
        m._snapshot_search_service = FakeSearchService(
            [_candidate("snap-hit", [], freshness="fresh", age=1.0)]
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "find it", "project_id": "p1"}
            )
        )
        assert "[snapshot] started: warm — Warm-started from snapshot snap-hit" in result
        # The search consumed the task as its query, scoped to the
        # caller's project, limit=1 (R14 internal-search contract).
        call = m._snapshot_search_service.calls[0]
        assert call["query"] == "find it"
        assert call["limit"] == 1

    def test_internal_search_no_hit_cold(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m._snapshot_search_service = FakeSearchService([])
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert "[snapshot] started: cold" in result
        assert "reason: no-hit" in result
        assert "No matching snapshot" in result

    def test_stale_warm_carries_drift_note_in_citation(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        m._snapshot_service.staleness = {
            "snapshot_age_days": 9.0,
            "freshness": "stale",
            "warnings": [],
            "repo_state": None,
        }
        m._snapshot_search_service = FakeSearchService(
            [_candidate("snap-1", [], freshness="stale", age=9.0)]
        )
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert "[snapshot] started: warm" in result
        assert "snapshot is STALE" in result
        assert "verify digest assumptions" in result

    def test_git_verify_warning_surfaces_in_citation(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
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
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                    "verify": "git",
                }
            )
        )
        assert "[snapshot] started: warm" in result
        assert "repo diverged 3 file(s)" in result

    def test_internal_search_crash_still_spawns_cold(self, engine, caller_rows, monkeypatch):
        """Rider (h): a search-system fault must NEVER raise — cold spawn."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))

        class ExplodingSearch(FakeSearchService):
            async def search(self, *a: Any, **kw: Any) -> dict[str, Any]:
                raise RuntimeError("search backend down")

        m._snapshot_search_service = ExplodingSearch()
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "[snapshot] started: cold" in result
        assert "reason: no-hit" in result
        # fail-soft: the fault is contained (search-level try inside
        # resolve_spawn_snapshot) — the spawn proceeds, no error text.

    def test_spawn_system_fault_reports_error_string(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))

        def boom(**kw: Any):
            raise ValueError("Agent not found")

        m.spawn_instance = boom
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "ghost", "task": "t", "project_id": "p1"}
            )
        )
        assert result.startswith("ERROR:")
        assert "Agent not found" in result
        assert "[snapshot]" not in result  # nothing spawned — no citation

    def test_stamp_failure_downgrades_warm_to_cold_in_citation(
        self, engine, caller_rows, monkeypatch
    ):
        """R6b: a failed digest stamp degrades the warm start to cold
        (verify-failed) in the citation line — the spawn still
        succeeds, the failure is a warning, never an error."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))

        def stamp_boom(instance_id, updates):
            m.events.append("metadata")
            raise RuntimeError("metadata write down")

        m.set_metadata_many = stamp_boom
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                }
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "[snapshot] started: cold" in result
        assert "reason: verify-failed" in result
        assert "digest stamp write failed" in result


# ============================================================================
# R18 (2026-10-04) auto-dispatch — now carried by the unified
# ``spawn_instance`` tool (unify-spawn-tools, 2026-10-05). The
# forensic-audit lineage (audit-doc 6e75621b) is unchanged: default
# ``auto_dispatch=True`` enqueues ``task`` as the child's first turn
# INSIDE the tool; opt-out restores the two-step ritual; an empty
# task is a loud skip; the non-queued/None tripwires route into the
# loud ERROR tail.
# ============================================================================


class TestR18AutoDispatch:
    """Pins the R18 contract on the unified spawn tool."""

    def _auth_ok(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda caller, requested, tag=None: None
        )

    def test_default_auto_dispatches_task_as_first_turn(
        self, engine, caller_rows, monkeypatch
    ):
        """Default behavior: task IS enqueued as the child's first turn."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "do the thing", "project_id": "p1"}
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert m.enqueue_calls, "task was NOT auto-dispatched (the trap)"
        kwargs = m.enqueue_calls[0]
        assert kwargs["instance_id"] == "new-inst-1"
        assert kwargs["message"] == "do the thing"
        # Provenance: matches send_message's source marker.
        assert kwargs["source"] == "internal_agent:caller-1"
        assert "auto-dispatched as first turn" in result
        assert "do NOT call send_message again" in result
        # R6b ordering: spawn → enqueue (no metadata write on cold).
        assert m.events == ["spawn", "enqueue"]

    def test_auto_dispatch_warm_writes_metadata_then_enqueues(
        self, engine, caller_rows, monkeypatch
    ):
        """R6b ordering is preserved: spawn → metadata → enqueue."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        self._auth_ok(monkeypatch)
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "warm task",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                }
            )
        )
        assert "[snapshot] started: warm" in result
        # The exact R6b+R18 ordering: spawn, metadata stamp, enqueue.
        assert m.events == ["spawn", "metadata", "enqueue"]
        assert m.enqueue_calls[0]["instance_id"] == "new-inst-1"
        assert m.enqueue_calls[0]["message"] == "warm task"
        assert m.enqueue_calls[0]["source"] == "internal_agent:leader-1"
        assert "Warm-started from snapshot snap-1" in result
        assert "auto-dispatched as first turn" in result

    def test_auto_dispatch_false_skips_enqueue(
        self, engine, caller_rows, monkeypatch
    ):
        """Opt-out: auto_dispatch=False restores the legacy two-step ritual."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {
                    "agent_id": "worker",
                    "task": "do it",
                    "project_id": "p1",
                    "auto_dispatch": False,
                }
            )
        )
        assert m.enqueue_calls == [], "opt-out was IGNORED — auto-enqueued anyway"
        assert "auto_dispatch=False" in result
        assert "caller MUST call" in result
        assert "send_message" in result
        assert m.events == ["spawn"]

    def test_task_absent_is_legacy_two_step_no_tail(
        self, engine, caller_rows, monkeypatch
    ):
        """task absent (None): exactly today's two-step behavior — no
        enqueue, NO dispatch tail, byte-stable success prefix."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "project_id": "p1"}
            )
        )
        assert m.enqueue_calls == []
        assert "auto-dispatch" not in result
        assert "auto_dispatch" not in result
        # The success prefix is byte-identical to the pre-unification
        # contract (modulo the child-cap line from the mock baseline).
        assert "Successfully spawned instance: new-inst-1" in result
        assert 'To communicate with this instance, use: send_message(instance_id="new-inst-1"' in result
        assert m.events == ["spawn"]
        assert "enqueue" not in m.events

    def test_auto_dispatch_skipped_when_task_empty(
        self, engine, caller_rows, monkeypatch
    ):
        """Explicitly empty/whitespace task: no enqueue, loud SKIPPED note."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_ok(monkeypatch)
        for empty_task in ("", "   ", "\n\t  \n"):
            result = _run(
                _spawn_tool(m).ainvoke(
                    {
                        "agent_id": "worker",
                        "task": empty_task,
                        "project_id": "p1",
                    }
                )
            )
            assert m.enqueue_calls == [], (
                f"empty task={empty_task!r} should NOT enqueue"
            )
            assert "auto-dispatch SKIPPED: task was empty" in result
            assert "caller MUST call send_message" in result
            m.enqueue_calls.clear()
            m.events.clear()

    def test_auto_dispatch_failure_surfaces_as_error_not_silent(
        self, engine, caller_rows, monkeypatch
    ):
        """Enqueue failure after successful spawn: NEVER silent."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m.enqueue_raise = RuntimeError("enqueue lane down (test injected)")
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "do it", "project_id": "p1"}
            )
        )
        # The spawn succeeded; the failure rides the loud tail.
        assert "Successfully spawned instance: new-inst-1" in result
        assert "auto-dispatch ERROR" in result
        assert "auto-dispatch failed" in result
        assert "RuntimeError" in result
        assert "send_message" in result
        assert "new-inst-1" in result

    def test_auto_dispatch_does_not_run_on_auth_denial(
        self, engine, caller_rows, monkeypatch
    ):
        """Auth denial MUST NOT trigger an enqueue (no child exists)."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        self._auth_denied = None  # unused; local patch below
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership",
            lambda caller, requested, tag=None: "agent 'x' is not in caller's team",
        )
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "do it", "project_id": "p1"}
            )
        )
        assert result.startswith("ERROR:")
        assert m.enqueue_calls == []
        assert m.events == []

    def test_r18_invariant_pin_non_queued_result_routes_failure_surface(
        self, engine, caller_rows, monkeypatch
    ):
        """R18 result-inspection invariant: a non-raising non-queued
        enqueue result MUST route into the loud lane (tripwire)."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m.enqueue_result = _FakeAsyncMessageResult(
            message_id="", queued=False, status="rejected"
        )
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "do it", "project_id": "p1"}
            )
        )
        assert "Successfully spawned instance: new-inst-1" in result
        assert "auto-dispatch ERROR" in result
        assert "auto-dispatch failed" in result
        assert "RuntimeError" in result
        assert "non-queued result" in result
        assert "status='rejected'" in result
        assert "send_message" in result

    def test_r18_invariant_pin_enqueue_returning_none_routes_failure_surface(
        self, engine, caller_rows, monkeypatch
    ):
        """Defensive sibling: a None enqueue return surfaces the loud lane."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m.enqueue_result = None
        self._auth_ok(monkeypatch)
        result = _run(
            _spawn_tool(m).ainvoke(
                {"agent_id": "worker", "task": "do it", "project_id": "p1"}
            )
        )
        assert "auto-dispatch ERROR" in result
        assert "auto-dispatch failed" in result
        assert "RuntimeError" in result
        assert "R18 invariant is non-None on success" in result
        assert "send_message" in result

    def test_dispatch_log_carries_authoritative_message_id(
        self, engine, caller_rows, monkeypatch, caplog
    ):
        """F2 INFO log carries the daemon-minted ``message_id`` (event
        renamed to ``spawn_instance_auto_dispatch`` per decision 7)."""
        import json as _json

        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m.enqueue_result = _FakeAsyncMessageResult(message_id="mid-pinned-001")
        self._auth_ok(monkeypatch)

        with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
            result = _run(
                _spawn_tool(m).ainvoke(
                    {"agent_id": "worker", "task": "x", "project_id": "p1"}
                )
            )

        assert "Successfully spawned instance: new-inst-1" in result
        matched = [
            rec for rec in caplog.records
            if rec.levelno == logging.INFO
            and rec.getMessage().startswith("[SpawnAutoDispatch] ")
        ]
        assert len(matched) == 1, (
            f"expected exactly one [SpawnAutoDispatch] INFO line; "
            f"got {len(matched)}: {[r.getMessage() for r in matched]}"
        )
        payload = _json.loads(
            matched[0].getMessage()[len("[SpawnAutoDispatch] "):]
        )
        assert payload["event"] == "spawn_instance_auto_dispatch"
        assert payload["caller_iid"] == "caller-1"
        assert payload["target_iid"] == "new-inst-1"
        assert payload["content_len"] == 1  # "x"
        assert payload["message_id"] == "mid-pinned-001"

    def test_dispatch_failure_logs_warning_with_instance_id(
        self, engine, caller_rows, monkeypatch, caplog
    ):
        """F1 — enqueue failure MUST emit a WARNING log line naming
        the target instance (log lives on the unified module now)."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        m.enqueue_raise = RuntimeError("enqueue lane down (test injected)")
        self._auth_ok(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="daemon.tools.instance"):
            result = _run(
                _spawn_tool(m).ainvoke(
                    {"agent_id": "worker", "task": "do it", "project_id": "p1"}
                )
            )

        assert "auto-dispatch ERROR" in result
        matched = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "[Spawn] spawn_instance auto-dispatch "
            "enqueue failed for new-inst-1" in rec.getMessage()
        ]
        assert matched, (
            "F1 regression: expected the auto-dispatch enqueue-failure "
            f"WARNING; got records: {[r.getMessage() for r in caplog.records]}"
        )
        assert "enqueue lane down (test injected)" in matched[0].getMessage()


# ============================================================================
# R6b warm-path ordering + stamp content (unified spawn)
# ============================================================================


class TestWarmPathOrdering:
    def test_metadata_written_after_spawn_before_enqueue(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = m._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-1",
                }
            )
        )
        # R6b ordering: spawn FIRST, atomic metadata write SECOND,
        # enqueue THIRD (the digest stamp MUST land before the enqueue
        # so the first turn sees the digest).
        assert m.events == ["spawn", "metadata", "enqueue"]
        assert "Successfully spawned instance: new-inst-1" in result
        instance_id, updates = m.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert updates["spawned_from_snapshot_id"] == "snap-1"
        assert updates["snapshot_digest"] == {"task_summary_text": "did the thing"}

    def test_cold_path_writes_no_stamp(self, engine, caller_rows, monkeypatch):
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        _snapshot_gate(monkeypatch, True)
        _run(
            _spawn_tool(m, caller_id="leader-1", agent_id="leader").ainvoke(
                {"agent_id": "worker", "task": "t", "project_id": "p1"}
            )
        )
        # Cold path writes no metadata stamp: spawn + enqueue only.
        assert m.events == ["spawn", "enqueue"]
        assert m.metadata_calls == []  # NO metadata write on cold


# ============================================================================
# R18 (2026-10-04) auto-dispatch — the contract trap fix (born on the
# removed spawn_hot_instance; now carried by the unified spawn tool)
#
# The forensic audit (2026-10-04, evidence file
# .agents/tester/RESULTS/2026-10-04-v01612-spawn-enqueue-forensic-audit.md)
# proved the pre-R18 contract was "accepted-but-never-delivered":
# `task` was consumed by snapshot search (snapshot_tools.py:995) and
# hint text (:989) but never forwarded to the child. Agents uniformly
# missed the follow-up `send_message` requirement — three children
# sat idle 6-23 minutes before being manually POSTed (audit §3, §4).
#
# R18 default: `auto_dispatch=True` enqueues `task` as the child's
# first turn INSIDE the tool. The trap is structurally impossible.
# Opt-out: `auto_dispatch=False` restores the legacy two-step
# ritual. The citation line is {warm, cold}; auth denials surface as
# plain ERROR strings (the 1090f308 dict shape is gone with the removed tool).
# ============================================================================


# ============================================================================
# R6b warm-path ordering + stamp content
# ============================================================================


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
        # Warm spawn from the completed row → clean metadata stamp
        # (via the unified spawn_instance tool, gate ON).
        manager = _unified_spawn_manager(caller_rows, repo)
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        _snapshot_gate(monkeypatch, True)
        result = _run(
            _spawn_tool(manager, caller_id="leader-1", agent_id="leader").ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "succ",
                }
            )
        )
        assert "[snapshot] started: warm" in result
        instance_id, metadata_updates = manager.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert metadata_updates["spawned_from_snapshot_id"] == "succ"
        assert "supersedes_snapshot_id" not in metadata_updates["snapshot_digest"]
        assert metadata_updates["snapshot_digest"] == {"decisions": ["d1"]}


# ============================================================================
# Registration chain (rider (d)) — the 6+1-file chain
# ============================================================================


class TestRegistrationChain:
    """Registration chain after unify-spawn-tools: TWO snapshot tools
    + the gate flag on the TARGET agents' meta.json."""

    def test_dynamic_tool_names_contains_both(self):
        from daemon.tools._tool_registry import DYNAMIC_TOOL_NAMES

        assert "snapshot_create" in DYNAMIC_TOOL_NAMES
        assert "snapshot_search" in DYNAMIC_TOOL_NAMES
        # unify-spawn-tools: the standalone hot-spawn tool is GONE.
        assert "spawn_hot_instance" not in DYNAMIC_TOOL_NAMES

    def test_category_modules_entry(self):
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert CATEGORY_MODULES["snapshot"] == "daemon.tools.snapshot_tools"

    def test_factory_returns_two_tools_with_categories(self):
        tools = create_snapshot_tools(None, "", "", None)
        by_name = {t.name: getattr(t, "_tool_category", None) for t in tools}
        assert by_name == {
            "snapshot_create": "snapshot",
            "snapshot_search": "snapshot",
        }

    def test_source_discovery_matches_word_boundary(self):
        """Rider (e): source-discovery + \\b-bounded grep, never substring."""
        from daemon.tools._tool_registry import (
            KNOWN_TOOL_NAMES,
            discover_source_only_tool_names,
        )

        discovered = discover_source_only_tool_names()
        for name in ("snapshot_create", "snapshot_search"):
            assert name in discovered
            assert name in KNOWN_TOOL_NAMES
        source = (TOOLS_DIR / "snapshot_tools.py").read_text(encoding="utf-8")
        # The removed tool name survives ONLY as historical prose —
        # as a callable/registered surface it is gone.
        assert not re.search(r"async def spawn_hot_instance\b", source)
        assert "spawn_hot_instance" not in KNOWN_TOOL_NAMES
        assert "spawn_hot_instance" not in discovered

    def test_loader_warm_list_wires_snapshot_factory(self):
        """Warm-list step: the scan registers the snapshot categories
        (the empty-category cache-pin hazard)."""
        from daemon.loader import _ensure_tool_metadata_populated
        from daemon.tools._tool_registry import _tool_metadata

        _ensure_tool_metadata_populated()
        assert _tool_metadata["snapshot_create"]["category"] == "snapshot"
        assert _tool_metadata["snapshot_search"]["category"] == "snapshot"
        assert "spawn_hot_instance" not in _tool_metadata

    def test_instance_factory_call_present(self):
        """Chain step 3: create_instance_tools extends with the factory."""
        source = (TOOLS_DIR / "instance.py").read_text(encoding="utf-8")
        assert re.search(r"\bcreate_snapshot_tools\b", source)
        assert "snapshot_tool_list" in source
        # And consumes the unified-spawn helpers directly.
        assert "resolve_spawn_snapshot" in source
        assert "finalize_spawn_snapshot" in source
        assert "format_snapshot_citation" in source
        assert "_target_snapshot_enabled" in source

    def test_grants_meta_json_allow_entries(self):
        import json

        agents_dir = TOOLS_DIR.parents[1] / "agents"
        for agent in ("worker", "coder", "tester"):
            meta = json.loads((agents_dir / agent / "meta.json").read_text(encoding="utf-8"))
            allow = meta["tools"]["allow"]
            assert "snapshot_create" in allow, agent
            assert "snapshot_search" in allow, agent

    def test_gate_flag_enabled_on_targets(self):
        """unify-spawn-tools decision 10: tester + developer[v2] carry
        ``"snapshot_enabled": true``; no other agent meta.json does."""
        import json

        agents_dir = TOOLS_DIR.parents[1] / "agents"
        enabled = []
        for meta_path in sorted(agents_dir.glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("snapshot_enabled") is True:
                enabled.append(meta.get("id") or meta_path.parent.name)
        assert sorted(enabled) == ["developer", "tester"], (
            f"snapshot_enabled targets drifted: {enabled}"
        )
        # The versioned developer[v2] directory is the ONLY developer
        # meta — the flag must live on the versioned variant.
        dev_meta = json.loads(
            (agents_dir / "developer[v2]" / "meta.json").read_text(encoding="utf-8")
        )
        assert dev_meta["snapshot_enabled"] is True

    def test_grants_resolution_matrix(self):
        """worker/coder/tester gain the 2 tools; the removed
        hot-spawn name resolves NOWHERE; ari gains NOTHING."""
        from daemon.loader import _ensure_tool_metadata_populated
        from daemon.registry import get_registry
        from daemon.tools.instance import resolve_tool_filter

        _ensure_tool_metadata_populated()
        registry = get_registry()
        expectations = {
            # (agent): (create, search)
            "worker": (True, True),  # leaf: excluded from consumption
            "coder": (True, True),
            "tester": (True, True),
            "leader": (False, False),  # consumer via the gate, not grants
            "ari": (False, False),  # PERMANENT exclusion
        }
        for agent, (create, search) in expectations.items():
            meta = registry.get_resolved(agent)
            allow = list(meta.tools.allow) if meta and meta.tools and meta.tools.allow else None
            deny = list(meta.tools.deny) if meta and meta.tools and meta.tools.deny else None
            allowed = resolve_tool_filter(allow=allow, deny=deny)
            assert allowed is not None, agent
            assert ("snapshot_create" in allowed) == create, agent
            assert ("snapshot_search" in allowed) == search, agent
            assert "spawn_hot_instance" not in allowed, agent

    def test_instance_category_no_longer_grants_removed_tool(
        self, engine, caller_rows, monkeypatch
    ):
        """The 'instance' category decorates the unified spawn tool —
        and NO tool named spawn_hot_instance exists anywhere in the
        factory output (the removed ride-along is gone).

        Deterministic: ``@register_tool_category('instance')`` sets the
        ``_tool_category`` attribute on the factory-built tool object,
        so the assertion reads the factory output directly instead of
        the ambient global registry (which depends on scan ordering).
        """
        from daemon.tools.instance import create_instance_tools

        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        patches = patch_heavy_helpers()
        for _p in patches:
            _p.start()
        try:
            all_tools = create_instance_tools(
                m, "caller-1", agent_id="coder", version_tag=None
            )
        finally:
            for _p in reversed(patches):
                _p.stop()
        by_name = {getattr(t, "name", None): t for t in all_tools}
        assert "spawn_instance" in by_name
        assert "spawn_hot_instance" not in by_name
        assert getattr(by_name["spawn_instance"], "_tool_category", None) == "instance"


# ============================================================================
# Unified-spawn gate consult (unify-spawn-tools decisions 3 + 11)
# ============================================================================


class TestTargetSnapshotEnabledConsult:
    """The TARGET-agent gate consult: versioned resolution, fail-closed.

    The consult mirrors ``_apply_tool_filter``'s
    ``get_version(agent_id, version_tag) or get_resolved(agent_id)``
    fallback so versioned agents (developer[v2]) gate on THEIR flag.
    """

    def _registry(self):
        from daemon.registry import get_registry

        return get_registry()

    def test_tester_enabled_via_resolved_meta(self):
        from daemon.tools.instance import _target_snapshot_enabled

        assert _target_snapshot_enabled("tester", None) is True

    def test_developer_v2_enabled_via_versioned_resolution(self):
        """developer[v2] shape: the flag lives on the VERSIONED meta —
        the consult must resolve it via the version tag."""
        from daemon.tools.instance import _target_snapshot_enabled

        registry = self._registry()
        versioned = registry.get_version("developer", "v2")
        assert versioned is not None, "developer[v2] must resolve"
        assert getattr(versioned, "snapshot_enabled") is True
        assert _target_snapshot_enabled("developer", "v2") is True

    def test_worker_disabled_fail_closed(self):
        from daemon.tools.instance import _target_snapshot_enabled

        assert _target_snapshot_enabled("worker", None) is False

    def test_unknown_agent_fail_closed(self):
        from daemon.tools.instance import _target_snapshot_enabled

        assert _target_snapshot_enabled("no-such-agent-xyz", None) is False

    def test_end_to_end_gate_off_via_real_registry(self, engine, caller_rows, monkeypatch):
        """Full-path gate OFF: NO monkeypatched gate — the real consult
        resolves worker (no flag) → plain cold spawn, no snapshot work."""
        m = _unified_spawn_manager(caller_rows, SnapshotRepository(engine))
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        result = _run(
            _spawn_tool(m).ainvoke(
                {
                    "agent_id": "worker",
                    "task": "t",
                    "project_id": "p1",
                    "snapshot_id": "snap-ghost",
                }
            )
        )
        assert SNAPSHOT_STEERING_IGNORED_LINE in result
        assert "[snapshot] started:" not in result
        assert m.metadata_calls == []
