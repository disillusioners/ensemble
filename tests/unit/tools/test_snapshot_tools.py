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

Size rationale (2026-10-04): the file is ~1350 lines because it pins the
Wave-2b + R18 + D8 contracts at the TOOL seam (47+ classes / 100+
test methods) on top of FakeManager / FakeSearchService /
FakeCaptureService / FakeInstanceRepo / FakeAsyncMessageResult
fakes + a ResultKeys / RESULT_KEYS contract constant. The >3000 refactor watch band is documented here so the next hygiene pass knows
when the fixture divergence justifies a split: split if a SINGLE test
class exceeds 700 lines, or if a fake's import list exceeds 12 names —
neither is close today.
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
    _denied_result,
    create_snapshot_tools,
    is_snapshot_create_enabled,
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
    """Records the spawn / metadata-write ordering (R6b) + R18 enqueue."""

    def __init__(self, rows: dict[str, Any], repo: SnapshotRepository):
        self._instance_repository = FakeInstanceRepo(rows)
        self._snapshot_repo = repo
        self._snapshot_service = FakeCaptureService()
        self._snapshot_search_service = FakeSearchService()
        self._project_repository = None
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
        # Silent-failure fix (2026-10-04 E2E finding #1): an
        # authorization denial MUST return ``started: "blocked"``,
        # NOT ``"cold"`` — the previous shape let callers confuse
        # "spawned cold" with "spawn refused". The error field
        # already carried the membership message; the
        # ``started == "blocked"`` value is the loud, machine-
        # readable differentiator the contract was missing.
        assert result["started"] == "blocked"
        assert result["instance_id"] is None
        assert result["snapshot_id"] is None
        assert result["staleness"] == {}
        assert "Permission denied" in result["hint"]
        assert "is not in caller's team" in result["error"]
        assert manager.events == []  # nothing spawned

    def test_auth_failure_result_keys_match_cold_contract(
        self, tools, monkeypatch
    ):
        """R14 6-key contract (§4.3) is preserved on the BLOCKED path."""
        self._auth_denied(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert set(result.keys()) == RESULT_KEYS

    def test_auth_failure_hint_is_actionable(
        self, tools, monkeypatch
    ):
        """The hint must name the refusal + how to resolve it (caller
        cannot otherwise self-correct from the result alone)."""
        self._auth_denied(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        # The actionable direction: add the requested agent to the
        # caller's team_members. Caller-facing language, not internal
        # implementation detail.
        assert "team_members" in result["hint"]
        # The underlying error is surfaced so the caller knows WHO
        # is missing the requested agent.
        assert "is not in caller's team" in result["hint"]

    def test_denied_result_helper_shape(self):
        """Unit-level pin on ``_denied_result`` itself — guards the
        helper against future drift independent of the tool's wiring."""
        result = _denied_result(
            error="Agent 'leader' is not allowed to spawn 'worker'. "
            "Allowed team members: []"
        )
        assert result == {
            "instance_id": None,
            "started": "blocked",
            "snapshot_id": None,
            "staleness": {},
            "hint": (
                "Permission denied — spawn blocked "
                "(reason: permission-denied). Nothing was spawned. "
                "Resolve the authorization problem (e.g. add the "
                "requested agent to the caller's team_members) and "
                "retry. Detail: Agent 'leader' is not allowed to "
                "spawn 'worker'. Allowed team members: []"
            ),
            "error": (
                "Agent 'leader' is not allowed to spawn 'worker'. "
                "Allowed team members: []"
            ),
        }

    def test_no_caller_returns_blocked_not_cold(self, manager, monkeypatch):
        """Same-class fix for the wiring-bug branch: when
        ``caller_agent_id`` is empty (the tool was created without
        an agent binding), the refusal MUST surface as
        ``started: "blocked"`` too — NOT as a cold-fallback shape
        that pretends the spawn was a snapshot miss.

        The ``create_snapshot_tools`` signature is ``(manager,
        current_instance_id, agent_id, version_tag)`` — we pass an
        empty ``agent_id`` (3rd positional) to trigger the
        ``caller_agent_id = ""`` branch at the wiring guard.
        """
        tools = create_snapshot_tools(manager, "caller-1", "", None)
        # Belt-and-braces: keep auth-ok monkeypatch in place so a
        # regression in the wiring-branch order would still fail
        # this test (auth would mask the no-caller path).
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert result["started"] == "blocked"
        assert result["instance_id"] is None
        assert result["snapshot_id"] is None
        assert result["staleness"] == {}
        assert "Permission denied" in result["hint"]
        assert "wiring/configuration bug" in result["error"]
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
        # R18 (2026-10-04): the hint now carries the auto-dispatch
        # tail AFTER the warm-start lineage text. The tags fragment
        # is still present in the warm-start prefix — assert it as
        # a substring instead of as a hard suffix.
        assert "tags kind:implementation, subsystem:upgrade-pipeline)" in result["hint"]
        assert "auto-dispatched as first turn" in result["hint"]
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
# R18 (2026-10-04) auto-dispatch — the spawn_hot_instance contract trap fix
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
# ritual. The `started` contract is preserved at {warm, cold, blocked}
# — 1090f308's 3-value envelope still holds.
# ============================================================================


class TestR18AutoDispatch:
    """Pins the R18 contract: auto-dispatch + opt-out + failure surface."""

    def _auth_ok(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda caller, requested, tag=None: None
        )

    def _auth_denied(self, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership",
            lambda caller, requested, tag=None: "agent 'x' is not in caller's team",
        )

    def test_default_auto_dispatches_task_as_first_turn(
        self, tools, manager, monkeypatch
    ):
        """Default behavior: task IS enqueued as the child's first turn.

        Pre-R18 trap: the row was created but the child never received
        the task. R18 default = the trap is structurally impossible.
        """
        self._auth_ok(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do the thing"}))
        # The spawn succeeded (started=cold; no-hit default), and the
        # task was auto-dispatched.
        assert result["started"] == "cold"
        assert result["error"] is None
        assert manager.enqueue_calls, "task was NOT auto-dispatched (the trap)"
        kwargs = manager.enqueue_calls[0]
        assert kwargs["instance_id"] == "new-inst-1"
        assert kwargs["message"] == "do the thing"
        # Provenance: matches send_message's source marker
        # (instance.py:3566 — f"internal_agent:{caller}").
        assert kwargs["source"] == "internal_agent:caller-1"
        # The hint states the dispatch outcome at-a-glance.
        assert "auto-dispatched as first turn" in result["hint"]
        # R6b ordering: spawn → enqueue (no metadata write on cold).
        assert manager.events == ["spawn", "enqueue"]

    def test_auto_dispatch_warm_writes_metadata_then_enqueues(
        self, engine, caller_rows, monkeypatch
    ):
        """R6b ordering is preserved: spawn → metadata → enqueue.

        The metadata write MUST happen BEFORE the enqueue so the
        snapshot digest is available to ``assemble_context_messages``
        when the worker picks up the first-turn message. Audit
        flagged this as a potential race — the order below proves it
        is held.
        """
        manager = FakeManager(caller_rows, SnapshotRepository(engine))
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot(snapshot_id="snap-1", target="inst-1"))
        tools = create_snapshot_tools(manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        result = _run(
            tools[2].ainvoke({"agent_id": "worker", "task": "warm task", "snapshot_id": "snap-1"})
        )
        assert result["started"] == "warm"
        # The exact R6b+R18 ordering: spawn, metadata stamp, enqueue.
        assert manager.events == ["spawn", "metadata", "enqueue"]
        assert manager.enqueue_calls
        assert manager.enqueue_calls[0]["instance_id"] == "new-inst-1"
        assert manager.enqueue_calls[0]["message"] == "warm task"
        assert manager.enqueue_calls[0]["source"] == "internal_agent:leader-1"
        # The hint carries BOTH the warm-start lineage AND the
        # auto-dispatch note — callers can read both from a single
        # string.
        assert "Warm-started from snapshot snap-1" in result["hint"]
        assert "auto-dispatched as first turn" in result["hint"]

    def test_auto_dispatch_false_skips_enqueue(
        self, tools, manager, monkeypatch
    ):
        """Opt-out: auto_dispatch=False restores the legacy two-step ritual.

        Caller is now responsible for the follow-up send_message.
        The hint explicitly states the requirement so the trap is at
        least loudly visible (vs. pre-R18's silent omission).
        """
        self._auth_ok(monkeypatch)
        result = _run(
            tools[2].ainvoke(
                {"agent_id": "worker", "task": "do it", "auto_dispatch": False}
            )
        )
        assert result["started"] == "cold"
        assert result["error"] is None
        assert manager.enqueue_calls == [], "opt-out was IGNORED — auto-enqueued anyway"
        # The hint carries the explicit next-step instruction so
        # callers who opt out can still see what they must do.
        assert "auto_dispatch=False" in result["hint"]
        assert "caller MUST call send_message" in result["hint"]
        # Order: spawn only (no metadata on cold, no enqueue on opt-out).
        assert manager.events == ["spawn"]

    def test_auto_dispatch_skipped_when_task_empty(
        self, tools, manager, monkeypatch
    ):
        """Empty/whitespace task: no enqueue, hint says so.

        Backward compat: a caller who passes ``task=""`` (e.g. for a
        snapshot-verify-only test shape) should NOT trigger an
        enqueue — the row is created but the caller is told they
        MUST send the first message themselves.
        """
        self._auth_ok(monkeypatch)
        for empty_task in ("", "   ", "\n\t  \n"):
            result = _run(tools[2].ainvoke({"agent_id": "worker", "task": empty_task}))
            assert result["started"] == "cold", f"task={empty_task!r} should be cold"
            assert result["error"] is None, f"task={empty_task!r} should be error-free"
            assert manager.enqueue_calls == [], (
                f"empty task={empty_task!r} should NOT enqueue; got "
                f"{manager.enqueue_calls}"
            )
            assert "auto-dispatch SKIPPED: task was empty" in result["hint"]
            assert "caller MUST call send_message" in result["hint"]
            # Reset recorder for the next iteration.
            manager.enqueue_calls.clear()
            manager.events.clear()

    def test_auto_dispatch_failure_surfaces_as_error_not_silent(
        self, tools, manager, monkeypatch
    ):
        """Enqueue failure after successful spawn: NEVER silent.

        Audit requirement (item A, failure-mode clause): "enqueue
        fails after row created → surface as `blocked` or clear
        error, never silent death". R18 chose: keep the spawn
        result truthful (started reflects what actually happened in
        the DB), but populate ``error`` with the loud failure and
        name the manual recovery path in the hint.
        """
        self._auth_ok(monkeypatch)
        manager.enqueue_raise = RuntimeError("enqueue lane down (test injected)")

        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))

        # Spawn succeeded (truthful result); started preserves the
        # actual DB outcome — NOT downgraded to "blocked" (the
        # spawn DID happen). The error field carries the loud failure.
        assert result["started"] == "cold"
        assert result["error"] is not None
        assert "auto-dispatch failed" in result["error"]
        assert "RuntimeError" in result["error"]
        assert "send_message" in result["error"]
        # The hint carries the recovery path AND the failure detail.
        assert "auto-dispatch ERROR" in result["hint"]
        assert "new-inst-1" in result["hint"]

    def test_auto_dispatch_failure_preserves_six_key_contract(
        self, tools, manager, monkeypatch
    ):
        """The §4.3 6-key contract is preserved even on enqueue failure.

        Adding the error field MUST NOT introduce a 7th top-level
        key. The contract is exactly 6 keys; failure detail rides
        the existing ``error`` field and the ``hint`` text.
        """
        self._auth_ok(monkeypatch)
        manager.enqueue_raise = RuntimeError("enqueue lane down")
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))
        assert set(result.keys()) == RESULT_KEYS
        assert result["error"] is not None
        # No top-level "auto_dispatch" key — the result shape is
        # backward compatible with the pre-R18 6-key envelope.
        assert "auto_dispatch" not in result
        assert "auto_dispatch_status" not in result

    def test_auto_dispatch_failure_logs_warning_with_instance_id(
        self, tools, manager, monkeypatch, caplog
    ):
        """Review F1 — enqueue failure MUST emit a WARNING log line.

        Pre-F1: the ``except`` block surfaced the failure to the
        caller's ``error`` field but logged NOTHING — operators
        triaging a stranded child from logs alone had no breadcrumb.
        Post-F1: a ``[Snapshot] spawn_hot_instance auto-dispatch
        enqueue failed for <id>: <exc>`` line lands in the
        ``daemon.tools.snapshot_tools`` logger at WARNING. Same logger
        + sibling f-string style as the internal-search failure path
        (:1066) so log tooling treats both uniformly.
        """
        self._auth_ok(monkeypatch)
        manager.enqueue_raise = RuntimeError(
            "enqueue lane down (test injected)"
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.tools.snapshot_tools"
        ):
            result = _run(
                tools[2].ainvoke({"agent_id": "worker", "task": "do it"})
            )

        # Sanity: the failure still surfaces in the loud lane.
        assert result["error"] is not None
        assert "auto-dispatch failed" in result["error"]

        # The log assertion: at least one warning carries the
        # `[Snapshot]` prefix and names the target instance so an
        # operator can grep from logs alone. The full message format
        # is anchored so the test fails if the log text drifts from
        # the F1 contract.
        matched = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "[Snapshot] spawn_hot_instance auto-dispatch "
            "enqueue failed for new-inst-1" in rec.getMessage()
        ]
        assert matched, (
            "F1 regression: expected a `[Snapshot] spawn_hot_instance "
            "auto-dispatch enqueue failed for new-inst-1: ...` WARNING; "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )
        # The exception text MUST ride the message so operators can
        # diagnose from logs alone (the sibling pattern at :1066
        # embeds ``str(exc)`` verbatim — matches the F1 contract:
        # f"[Snapshot] spawn_hot_instance auto-dispatch enqueue
        # failed for {new_instance_id}: {exc}"). The class name rides
        # the loud ``error`` field on the result, not the log line.
        assert (
            "enqueue lane down (test injected)" in matched[0].getMessage()
        )

    def test_auto_dispatch_does_not_run_on_blocked_spawn(
        self, tools, manager, monkeypatch
    ):
        """Blocked spawn (auth denial) MUST NOT trigger an enqueue.

        The team-membership check runs BEFORE the spawn — there is
        no new instance to enqueue against. R18 must not regress
        this invariant.
        """
        self._auth_denied(monkeypatch)
        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))
        assert result["started"] == "blocked"
        assert result["instance_id"] is None
        # The FakeManager MUST not have seen any spawn or enqueue
        # call — assert on the closed-over manager directly (the
        # ``manager`` fixture shares the FakeManager instance with
        # ``tools``).
        assert manager.enqueue_calls == [], (
            f"enqueue called on a blocked spawn: {manager.enqueue_calls}"
        )
        assert manager.events == [], (
            f"manager.events should be empty on a blocked spawn; "
            f"got {manager.events}"
        )
        # Belt-and-braces: the blocked result shape itself proves
        # the auth gate fired first.
        assert "Permission denied" in result["hint"]
        assert result["error"] is not None

    def test_3_value_started_contract_preserved(
        self, tools, manager, monkeypatch, engine, caller_rows
    ):
        """R18 does NOT regress the 3-value ``started`` envelope
        (1090f308, 2026-10-04). All three values are still reachable
        via the documented paths.
        """
        # cold (no-hit default)
        self._auth_ok(monkeypatch)
        r_cold = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert r_cold["started"] == "cold"
        # warm (explicit snapshot) — needs its own manager+fresh tools
        warm_manager = FakeManager(caller_rows, SnapshotRepository(engine))
        warm_manager._snapshot_repo.create_with_embeddings(
            _snapshot(snapshot_id="snap-warm", target="inst-1")
        )
        warm_tools = create_snapshot_tools(warm_manager, "leader-1", "leader", None)
        self._auth_ok(monkeypatch)
        r_warm = _run(
            warm_tools[2].ainvoke({"agent_id": "worker", "task": "t", "snapshot_id": "snap-warm"})
        )
        assert r_warm["started"] == "warm"
        # blocked (auth denial)
        self._auth_denied(monkeypatch)
        r_blocked = _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        assert r_blocked["started"] == "blocked"
        # All three values are reachable; the 1090f308 3-value
        # envelope is preserved.
        assert {r_cold["started"], r_warm["started"], r_blocked["started"]} == {
            "cold",
            "warm",
            "blocked",
        }
        # The R18 default is auto_dispatch=True, so the warm and
        # cold paths both made the enqueue call; the blocked path
        # did not.
        assert len(manager.enqueue_calls) >= 1  # cold
        assert len(warm_manager.enqueue_calls) == 1  # warm

    def test_r18_invariant_pin_non_queued_result_routes_failure_surface(
        self, tools, manager, monkeypatch
    ):
        """Commit 1 (2026-10-04) — R18 result-inspection invariant pin.

       Verification proved ``manager.enqueue_message`` cannot carry a
        non-queued non-raising signal (the only return path hardcodes
        ``status='queued'`` and defaults ``queued=False``), so the
        escape hatch applies: we DO NOT branch on ``result.queued``
        (dead code); we DO pin the result invariants via an assertion
        that fires the loud lane on a future regression. This test
        exercises the regression tripwire by injecting a fake that
        returns a non-queued non-raising result — the loud surface
        MUST fire (started preserved; error populated; hint names the
        manual recovery path), exactly like the exception case.
        """
        self._auth_ok(monkeypatch)
        # Inject a fake AsyncMessageResult that mimics a future
        # non-raising non-queued branch (status != "queued", no message_id).
        # The defensive block MUST route this into the same failure surface
        # as exceptions — never silent success.
        manager.enqueue_result = _FakeAsyncMessageResult(
            message_id="", queued=False, status="rejected"
        )

        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))

        # Spawn succeeded (truthful result); started preserved.
        assert result["started"] == "cold"
        # Loud lane fires — the F1-style error surfaces the regression with
        # the audit-trail wording (RuntimeError + tripwire text) plus the
        # manual-recovery hint the same as the exception path.
        assert result["error"] is not None
        assert "auto-dispatch failed" in result["error"]
        assert "RuntimeError" in result["error"]
        assert "non-queued result" in result["error"]
        assert "status='rejected'" in result["error"]
        assert "send_message" in result["error"]
        # The hint carries the recovery path AND the failure detail.
        assert "auto-dispatch ERROR" in result["hint"]
        assert "new-inst-1" in result["hint"]
        # The §4.3 6-key contract is preserved.
        assert set(result.keys()) == RESULT_KEYS

    def test_r18_invariant_pin_enqueue_returning_none_routes_failure_surface(
        self, tools, manager, monkeypatch
    ):
        """Defensive sibling: a None return (the escape hatch's "must not
        happen but we pin it anyway" branch) MUST surface the loud lane.

        The real path NEVER returns None (a successful enqueue always
        returns a populated ``AsyncMessageResult``), but a defensive
        assertion guards against a regression that accidentally drops
        the return.
        """
        self._auth_ok(monkeypatch)
        manager.enqueue_result = None

        result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "do it"}))

        assert result["started"] == "cold"
        assert result["error"] is not None
        assert "auto-dispatch failed" in result["error"]
        assert "RuntimeError" in result["error"]
        assert "R18 invariant is non-None on success" in result["error"]
        assert "send_message" in result["error"]
        assert "auto-dispatch ERROR" in result["hint"]
        assert set(result.keys()) == RESULT_KEYS

    def test_r18_f2_log_carries_authoritative_message_id(
        self, tools, manager, monkeypatch, caplog
    ):
        """F2 INFO log carries the daemon-minted ``message_id``.

        The forensic-audit methodology counts enqueue log lines and
        parents reuse the same ``task`` across many children, so the
        task text alone is not enough to correlate census. Commit 1
        surfaces the authoritative ``message_id`` the daemon minted
        on the enqueue so downstream tooling (grep / census) can
        cross-reference the same way it already does for the warm-spawn line.
        """
        import json as _json

        self._auth_ok(monkeypatch)
        # Pin a deterministic message_id so the log-line assertion is
        # stable; the fake's default 'msg-auto-1' would also work but
        # a custom value makes the correlation explicit.
        manager.enqueue_result = _FakeAsyncMessageResult(
            message_id="mid-pinned-001"
        )

        with caplog.at_level(logging.INFO, logger="daemon.tools.snapshot_tools"):
            result = _run(tools[2].ainvoke({"agent_id": "worker", "task": "x"}))

        # The success path still produces a started=cold result; the F2 log
        # is the observability surface, not the result envelope.
        assert result["started"] == "cold"
        assert result["error"] is None
        matched = [
            rec for rec in caplog.records
            if rec.levelno == logging.INFO
            and rec.getMessage().startswith("[SnapshotAutoDispatch] ")
        ]
        assert len(matched) == 1, (
            f"expected exactly one [SnapshotAutoDispatch] INFO line; "
            f"got {len(matched)}: {[r.getMessage() for r in matched]}"
        )
        payload = _json.loads(
            matched[0].getMessage()[len("[SnapshotAutoDispatch] "):]
        )
        assert payload["event"] == "spawn_hot_auto_dispatch"
        assert payload["caller_iid"] == "caller-1"
        assert payload["target_iid"] == "new-inst-1"
        assert payload["content_len"] == 1  # "x"
        # The Commit 1 F2 message_id surfacing — the authoritative daemon-


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
        # BEFORE the instance_id is returned. R18 (2026-10-04) added
        # the auto-dispatch enqueue as the THIRD step (the digest
        # stamp MUST land before the enqueue so the first turn sees
        # the digest — see test_r18_warm_writes_metadata_then_enqueues).
        assert manager.events == ["spawn", "metadata", "enqueue"]
        assert result["instance_id"] == "new-inst-1"
        instance_id, updates = manager.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert updates["spawned_from_snapshot_id"] == "snap-1"
        assert updates["snapshot_digest"] == {"task_summary_text": "did the thing"}
        # The R18 enqueue ran third; the metadata write did land
        # before the enqueue (the order assertion above proves it).

    def test_cold_path_writes_no_stamp(self, tools, manager, monkeypatch):
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership", lambda c, r, tag=None: None
        )
        _run(tools[2].ainvoke({"agent_id": "worker", "task": "t"}))
        # R18 (2026-10-04): cold path still writes no metadata
        # stamp; the only events on a cold spawn are "spawn" (the
        # row) + "enqueue" (the auto-dispatched first turn). The
        # NO-METADATA-WRITE invariant is preserved — assert it
        # explicitly (it was the load-bearing claim of the original
        # R6b test, and it must not regress).
        assert manager.events == ["spawn", "enqueue"]  # no "metadata" on cold
        assert manager.metadata_calls == []  # NO metadata write on cold


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
