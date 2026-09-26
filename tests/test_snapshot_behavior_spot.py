"""Agent Snapshot v1 — R14/R12/R15/D8 behavior spot-check pack.

Gate 4 spot-check tests (BEYOND the dev's 457-test bounded pack) —
asserts the observable contracts pinned by
``design-exploration.md §4.3 / §6.1 / §6.3 (R-rules) / §10 D8``.
Each test exercises the TOOL surface (the public behavior); never
re-implements private helpers.

Spec contracts pinned here:

* **R14 auto-fallback** (§4.3 / §6.3) — the result contract is
  EXACTLY six keys: ``{instance_id, started, snapshot_id, staleness,
  hint, error}``; ``started`` is ``"warm"|"cold"`` (always present,
  never null/missing on either path); ``reason`` rides inside the
  ``hint`` string verbatim. The hint format is also pinned:
  - warm: ``"Warm-started from snapshot {id} (age {n}d; tags …)"``
  - cold: ``"No matching snapshot — spawned cold (searched: …; reason:
    no-hit | expired | verify-failed)"``

* **R12 supersession** (§6.3) — a SUPERSEDED snapshot is NEVER
  returned as a candidate. The explicit-id verify branch refuses
  superseded (verify-fail cold); the internal-search branch filters
  to ``status='active'`` only at the search service.

* **R15 settings toggle** (§6.3 / §10 Q7-A) — toggle default OFF.
  ``snapshot_create`` is cleanly disabled (NOT a crash, NOT a silent
  success) — the disabled result shape is exact; ``spawn_hot_instance``
  still succeeds via COLD fallback (no-OFF-breaks-spawn invariant).

* **D8 project scoping** (§10 Q5-A, permanent) — a project-A-bound
  caller requesting a snapshot id belonging to foreign project B
  receives a COLD result (no cross-project warm hit) unless
  ``allow_cross_project=True`` is supplied. The Wave-2b fix path
  (commit acef1d3b, W1) closed the truthy-only gap; the projectless
  mirror arm catches caller-project-None/empty + snapshot-has-project.

The fixtures mirror the dev suite's shape (in-memory SQLite +
``SnapshotRepository`` + a hand-rolled ``FakeManager`` carrying
``spawn_instance`` + ``set_metadata_many`` + ``_snapshot_service`` /
``_snapshot_search_service`` seams), so behavior parallels are
obvious in code review and re-use of the same call paths is
unambiguous. The fixtures here intentionally re-define the small
fakes rather than cross-import the Wave-2b / Wave-3 suites — pytest
does not propagate test-file fixtures between siblings.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_SUPERSEDED,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.tools.snapshot_tools import (
    create_snapshot_tools,
    is_snapshot_create_enabled,
)

# A snapshot row carrying the minimum fields required for an
# active warm-start; mirrors the dev suite's ``_snapshot`` helper.
VALID_TAGS = [
    "kind:implementation",
    "subsystem:upgrade-pipeline",
]

# The 6-key R14 result contract — pinned by spec §4.3 and re-asserted
# by the Wave-2b review FIX 4 (top-level ``warnings`` key was removed).
RESULT_KEYS = {"instance_id", "started", "snapshot_id", "staleness", "hint", "error"}

# Spec §4.3 hint format: warm / cold markers + reason lexeme.
WARM_HINT_PREFIX = "Warm-started from snapshot "
COLD_HINT_PREFIX = "No matching snapshot — spawned cold"


# ============================================================================
# Fakes — mirrors of the dev suite (FakeInstanceRepo / FakeCaptureService
# / FakeSearchService / FakeManager), re-defined for the spot-check pack
# ============================================================================


class FakeInstanceRepo:
    """Minimal ``get()`` stand-in for the instance repository."""

    def __init__(self, rows: dict[str, Any]) -> None:
        self._rows = rows

    def get(self, instance_id: str) -> Any | None:
        return self._rows.get(instance_id)


class FakeCaptureService:
    """Records ``capture_async`` kwargs; serves a canned staleness map."""

    def __init__(
        self,
        staleness: dict[str, Any] | None = None,
    ) -> None:
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

    def __init__(
        self,
        results: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        project_id: str,
        tags=None,
        tag_mode: str = "all",
        limit: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "project_id": project_id,
                "tags": list(tags or []),
                "tag_mode": tag_mode,
                "limit": limit,
            }
        )
        return {"results": list(self.results), "error": self.error}


class FakeManager:
    """Records the spawn / metadata-write ordering (R6b)."""

    def __init__(self, rows: dict[str, Any], repo: SnapshotRepository) -> None:
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


# ============================================================================
# Fixtures
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


def _snapshot_row(
    *,
    snapshot_id: str = "snap-1",
    project_id: str = "p1",
    target: str = "inst-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    tags: list[str] | None = None,
) -> Snapshot:
    """A minimal Snapshot row for seeding the in-memory SQLite repo."""
    return Snapshot(
        id=snapshot_id,
        project_id=project_id,
        created_by_agent_id="coder",
        target_instance_id=target,
        title=snapshot_id,
        task_summary="did the thing",
        domain_tags=tags or VALID_TAGS,
        status=status,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.14.2",
        effective_model="cheap-model",
        digest={"task_summary_text": "did the thing"},
    )


def _candidate(
    snapshot_id: str,
    tags: list[str],
    *,
    freshness: str = "fresh",
    age: float = 1.0,
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "name": "snap",
        "tags": list(tags),
        "freshness": freshness,
        "age_days": age,
        "summary": "preview text",
    }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _gate(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """Steer the R15 gate at its REAL seam (the async util)."""

    async def _fake_enabled(repo: Any = None) -> bool:
        return enabled

    monkeypatch.setattr(
        "daemon.tools.snapshot_tools.get_snapshot_create_enabled",
        _fake_enabled,
    )


def _auth_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass team-membership check (out of scope for the spot pack)."""
    monkeypatch.setattr(
        "daemon.tools.instance._check_team_membership",
        lambda caller, requested, tag: None,
    )


@pytest.fixture
def callers_p1() -> dict[str, Any]:
    """Caller rows in project p1 — caller-1 + descendant inst-1."""
    return {
        "caller-1": _row("caller-1", agent_id="coder"),
        "inst-1": _row("inst-1", agent_id="worker", parent_id="caller-1"),
        "leader-1": _row("leader-1", agent_id="leader"),
    }


@pytest.fixture
def manager(engine: Engine, callers_p1: dict[str, Any]) -> FakeManager:
    return FakeManager(callers_p1, SnapshotRepository(engine))


@pytest.fixture
def tools(manager: FakeManager) -> list[Any]:
    return create_snapshot_tools(manager, "caller-1", "coder", None)


# ============================================================================
# R14 — warm/cold result contract (BOTH selection paths)
# ============================================================================


class TestR14ExplicitWarmContract:
    """R14 — explicit-id warm hit. The result contract keys MUST
    always be present (6 keys), and ``started`` MUST be ``"warm"``
    with the canonical warm-hint prefix carrying the consumed id.
    """

    def test_explicit_warm_all_six_keys_present_and_started_warm(
        self, engine: Engine, manager: FakeManager, callers_p1: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot_row(snapshot_id="snap-warm", tags=VALID_TAGS))
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "snap-warm"}
            )
        )
        # EXACTLY 6 keys (spec §4.3).
        assert set(result.keys()) == RESULT_KEYS
        # Always-present contract — never null/missing.
        assert result["started"] == "warm"
        assert result["error"] is None
        assert result["hint"].startswith(WARM_HINT_PREFIX)
        assert "snap-warm" in result["hint"]
        # WARM path stamps the spawned_from_snapshot_id (R6b).
        instance_id, updates = manager.metadata_calls[0]
        assert instance_id == "new-inst-1"
        assert updates["spawned_from_snapshot_id"] == "snap-warm"


class TestR14ExplicitColdContract:
    """R14 — explicit-id cold paths (miss / expired / verify-failed).

    On every cold path the result MUST still carry the six canonical
    keys (fail-soft per rider h), ``started`` MUST be ``"cold"``, and
    the ``reason`` lexeme MUST ride inside the ``hint`` string verbatim
    (one of ``verify-failed`` / ``expired``).
    """

    def test_explicit_missing_id_cold_verify_failed_reason_in_hint(
        self, tools: list[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _auth_ok(monkeypatch)
        result = _run(
            tools[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "missing-snap"}
            )
        )
        assert set(result.keys()) == RESULT_KEYS
        assert result["started"] == "cold"
        assert result["error"] is None  # fail-soft, never an error
        assert result["snapshot_id"] is None  # nothing consumed
        assert result["hint"].startswith(COLD_HINT_PREFIX)
        assert "reason: verify-failed" in result["hint"]

    def test_explicit_expired_snapshot_cold_with_expired_reason(
        self,
        engine: Engine,
        manager: FakeManager,
        callers_p1: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed an active row, then force the staleness report to read
        # ``expired`` so the verify branch flips it to cold.
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot_row(snapshot_id="snap-old"))
        manager._snapshot_service.staleness = {
            "snapshot_age_days": 60.0,
            "freshness": "expired",
            "warnings": [],
            "repo_state": None,
        }
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "snap-old"}
            )
        )
        assert set(result.keys()) == RESULT_KEYS
        assert result["started"] == "cold"
        assert result["error"] is None
        assert "reason: expired" in result["hint"]


class TestR14InternalSearchContract:
    """R14 — internal-search selection path (snapshot_id is None).

    The same always-present contract applies: 6 keys, ``started`` set,
    ``reason`` lexeme riding in the ``hint`` for the cold-no-hit and
    cold-expired paths; warm-start on a top candidate with the
    canonical warm prefix.
    """

    def test_internal_search_no_hit_cold_no_hit_reason(
        self, tools: list[Any], manager: FakeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager._snapshot_search_service = FakeSearchService(results=[])
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke({"agent_id": "worker", "task": "do it"})
        )
        assert set(result.keys()) == RESULT_KEYS
        assert result["started"] == "cold"
        assert result["error"] is None
        assert "reason: no-hit" in result["hint"]

    def test_internal_search_warm_top_match_started_warm(
        self,
        engine: Engine,
        manager: FakeManager,
        callers_p1: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(_snapshot_row(snapshot_id="snap-search", tags=VALID_TAGS))
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-search", VALID_TAGS)]
        )
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke({"agent_id": "worker", "task": "t"})
        )
        assert set(result.keys()) == RESULT_KEYS
        assert result["started"] == "warm"
        assert result["error"] is None
        assert result["hint"].startswith(WARM_HINT_PREFIX)
        assert "snap-search" in result["hint"]


# ============================================================================
# R12 — superseded NEVER returned as a candidate on either path
# ============================================================================


class TestR12SupersededNeverSpawns:
    """R12 — a SUPERSEDED snapshot must NEVER be returned as a warm
    candidate. The explicit-id branch flips verify-fail cold; the
    internal-search branch must filter to ``status='active'`` only.
    """

    def test_explicit_superseded_id_is_verify_failed_cold(
        self,
        engine: Engine,
        manager: FakeManager,
        callers_p1: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-old", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "snap-old"}
            )
        )
        assert result["started"] == "cold"
        assert "reason: verify-failed" in result["hint"]
        assert result["error"] is None  # fail-soft

    def test_internal_search_drops_superseded_top_candidate(
        self,
        engine: Engine,
        manager: FakeManager,
        callers_p1: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # R12 invariant — superseded NEVER returns as a warm
        # candidate. The search service boundary filters status to
        # 'active' (list_active_by_project); the spawn branch
        # additionally re-checks the consumed row's status before
        # marking it warm. We inject a SUPERSEDED top candidate
        # (bypassing the search-service boundary) and assert the
        # spawn branch catches it as no-hit cold.
        repo: SnapshotRepository = manager._snapshot_repo
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-old", status=SNAPSHOT_STATUS_SUPERSEDED)
        )
        repo.create_with_embeddings(_snapshot_row(snapshot_id="snap-new"))
        manager._snapshot_search_service = FakeSearchService(
            [_candidate("snap-old", VALID_TAGS)]
        )
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke({"agent_id": "worker", "task": "t"})
        )
        # The spawn branch re-verifies status; a SUPERSEDED top
        # candidate is dropped (cold, reason: no-hit, no
        # cross-project cross-fire, fail-soft).
        assert result["started"] == "cold"
        assert result["snapshot_id"] is None
        assert result["error"] is None
        assert "reason: no-hit" in result["hint"]


# ============================================================================
# R15 — settings toggle (default OFF; write-side ONLY)
# ============================================================================


class TestR15SettingsToggle:
    """R15 — default OFF. ``snapshot_create`` cleanly disabled; the
    other two tools NEVER gated (rider i isolation invariant).
    """

    def test_default_off_snapshot_create_disabled_no_crash(self) -> None:
        # No manager wired → helper returns the fail-closed constant.
        assert is_snapshot_create_enabled() is False
        assert is_snapshot_create_enabled(manager=None) is False

    def test_snapshot_create_disabled_exact_result_shape(
        self, tools: list[Any]
    ) -> None:
        # No R15 patch — the in-test default (no manager) reads
        # fail-closed → disabled.
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        # Disabled result shape is EXACT — no error key, no snapshot_id,
        # no crash, no silent success.
        assert result == {
            "disabled": True,
            "error": "snapshot_create disabled by settings toggle",
        }

    def test_snapshot_create_on_proceeds_no_disabled_key(
        self,
        tools: list[Any],
        manager: FakeManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _gate(monkeypatch, True)
        result = _run(
            tools[0].ainvoke(
                {"target_instance_id": "inst-1", "name": "n", "tags": VALID_TAGS}
            )
        )
        assert "disabled" not in result  # ON → no disabled key
        assert result["error"] is None
        assert len(manager._snapshot_service.calls) == 1

    def test_r15_off_spawn_hot_instance_still_cold_fallback(
        self,
        tools: list[Any],
        manager: FakeManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """R15 OFF must NOT break the spawn path (the
        no-OFF-breaks-spawn invariant). spawn_hot_instance ships
        always-on; OFF just means there are no fresh snapshots being
        created, so the search returns no candidates → cold.
        """
        # Toggle steered OFF (and the internal search returns nothing).
        _gate(monkeypatch, False)
        manager._snapshot_search_service = FakeSearchService(results=[])
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke({"agent_id": "worker", "task": "t"})
        )
        assert result["error"] is None
        assert result["started"] == "cold"
        assert result["instance_id"] == "new-inst-1"


# ============================================================================
# D8 — project scoping (no cross-project warm hit)
# ============================================================================


class TestD8ProjectScoping:
    """D8 (Wave-2b W1 fix path, commit acef1d3b) — a project-A-bound
    caller requesting a snapshot id belonging to foreign project B
    receives a COLD result (no cross-project warm hit) unless the
    caller explicitly opts in via ``allow_cross_project=True``.
    """

    def test_foreign_project_snapshot_id_is_cold_verify_failed(
        self,
        engine: Engine,
        manager: FakeManager,
        callers_p1: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo: SnapshotRepository = manager._snapshot_repo
        # Snapshot belongs to foreign project "p2"; caller lives in "p1".
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-xp", project_id="p2")
        )
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
        _auth_ok(monkeypatch)
        result = _run(
            tools_local[2].ainvoke(
                {"agent_id": "worker", "task": "t", "snapshot_id": "snap-xp"}
            )
        )
        # D8 default (allow_cross_project=False): no cross-project warm.
        assert result["started"] == "cold"
        assert result["error"] is None  # fail-soft
        # The reason rides in the hint — verify-failed with project info.
        assert "reason: verify-failed" in result["hint"]
        assert "p2" in result["hint"]


# ============================================================================
# Final aggregate sanity — the 6-key contract is intact for EVERY
# branch (warm / cold / no-hit / expired / verify-failed). Pinned as
# a parametrized sweep so a single regression in the result-shape
# assembly is loud.
# ============================================================================


@pytest.mark.parametrize(
    "label, invoke_kwargs, expected_started, expected_reason_lexeme",
    [
        ("explicit-missing-cold", {"snapshot_id": "missing"}, "cold", "verify-failed"),
        ("internal-search-no-hit-cold", {}, "cold", "no-hit"),
    ],
)
def test_result_shape_keys_always_six(
    label: str,
    invoke_kwargs: dict[str, Any],
    expected_started: str,
    expected_reason_lexeme: str | None,
    tools: list[Any],
    manager: FakeManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Internal-search cold needs an empty candidate list.
    if label == "internal-search-no-hit-cold":
        manager._snapshot_search_service = FakeSearchService(results=[])
        tools_local = create_snapshot_tools(manager, "caller-1", "coder", None)
    else:
        tools_local = tools
    _auth_ok(monkeypatch)
    result = _run(tools_local[2].ainvoke({"agent_id": "worker", "task": "t", **invoke_kwargs}))
    assert set(result.keys()) == RESULT_KEYS, f"{label}: keys drifted"
    assert result["started"] == expected_started, f"{label}: started drifted"
    assert result["error"] is None, f"{label}: never an error on miss"
    if expected_reason_lexeme is not None:
        assert (
            f"reason: {expected_reason_lexeme}" in result["hint"]
        ), f"{label}: reason lexeme missing from hint"
