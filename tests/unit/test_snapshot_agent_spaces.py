"""version-scoped snapshot spaces (vsss) — derivation, isolation, backfill.

Covers the five coverage cells of mission vsss-20261112:

* **(a) Derivation** — ``registry.compose_agent_space_key`` maps
  ``('developer', None) → 'developer'`` and
  ``('developer', 'v2') → 'developer[v2]'`` — the registry composite
  versioned identity, NEVER the meta.json ``version`` field (both
  developer metas carry ``"version": "2.0.0"`` and collide).
* **(b) Enumeration stability** — the per-directory scan of
  ``agents/*/`` maps each snapshot-enabled directory to its versioned
  space key; repeated scans yield the identical sorted set and the
  pinned set matches the ratified targets
  ``['developer', 'developer[v2]', 'tester']``.
* **(c) Predicate isolation** — a real
  :class:`~daemon.repositories.snapshot.repository.SnapshotRepository`
  over in-memory SQLite: an A-space row is invisible to a B-space
  query; NULL (unattributed) rows are invisible to EVERY space query.
* **(d) NULL reachability** — NULL rows never match the AUTOMATIC
  warm-start path (resolve_spawn_snapshot fails closed without a
  space key and the search predicate excludes NULL) while the
  EXPLICIT ``snapshot_id`` path (version-permissive — ratified
  decision 2) still reaches them; the spawn tool threads the composed
  key from the resolved spawn identity into the search seam.
* **(e) Backfill** — ``scripts/backfill_snapshot_agent_space.py``:
  only NULL + resolvable rows are stamped; re-run is a no-op;
  deleted-instance rows stay NULL forever; the repo stamp is
  CAS-gated (never overwrites).

The fail-closed ruling under test: version-unattributable legacy rows
(incl. snapshots of deleted instances) match NO version's automatic
warm-start — no fallback tier.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.registry import _parse_agent_dir_name, compose_agent_space_key
from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    Snapshot,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_search_service import SnapshotSearchService
from daemon.tools.snapshot_tools import resolve_spawn_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
BACKFILL_SCRIPT = REPO_ROOT / "scripts" / "backfill_snapshot_agent_space.py"


# ============================================================================
# Shared fixtures — in-memory SQLite snapshot repo (behavior-spot shape)
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Any]:
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


def _snapshot_row(
    *,
    snapshot_id: str,
    project_id: str = "p1",
    target: str = "inst-1",
    status: str = SNAPSHOT_STATUS_ACTIVE,
    agent_space_key: str | None = None,
    title: str | None = None,
) -> Snapshot:
    """A minimal Snapshot row; ``agent_space_key=None`` = legacy row."""
    return Snapshot(
        id=snapshot_id,
        project_id=project_id,
        created_by_agent_id="coder",
        target_instance_id=target,
        title=title or snapshot_id,
        task_summary=f"did the {snapshot_id} work on the upgrade pipeline",
        domain_tags=["kind:implementation", "topic:snapshot-spaces"],
        status=status,
        agent_space_key=agent_space_key,
        repo_path="/repo",
        vcs_type="git",
        git_sha="abc1234",
        git_branch="latest",
        git_dirty=False,
        runtime_version="0.29.21",
        effective_model="cheap-model",
        digest={"task_summary_text": f"did the {snapshot_id} work"},
    )


# ============================================================================
# (a) Canonical derivation — ONE helper, registry identity, never meta
# ============================================================================


class TestComposeAgentSpaceKey:
    def test_untagged_resolves_to_bare_agent_id(self):
        assert compose_agent_space_key("developer", None) == "developer"
        assert compose_agent_space_key("tester", None) == "tester"

    def test_tagged_resolves_to_composite_key(self):
        assert compose_agent_space_key("developer", "v2") == "developer[v2]"
        assert compose_agent_space_key("planner", "night") == "planner[night]"

    def test_v1_and_v2_resolve_to_DIFFERENT_space_keys(self):
        """The core vsss invariant: base and tagged variants occupy
        DISTINCT spaces — the same base id never shares a pool."""
        v1 = compose_agent_space_key("developer", None)
        v2 = compose_agent_space_key("developer", "v2")
        assert v1 != v2
        assert v1 == "developer"
        assert v2 == "developer[v2]"

    def test_never_derived_from_meta_version_field(self):
        """meta.json 'version' NEVER enters the key: both developer
        metas carry version 2.0.0 — if the derivation used it, every
        developer variant would collapse into one colliding key."""
        # The helper's signature has no version-shaped input; the tag
        # (directory suffix) is the only qualifier.
        assert compose_agent_space_key("developer", "2.0.0") == (
            "developer[2.0.0]"
        )
        # And the actual metas prove the collision the helper avoids:
        dev = (AGENTS_DIR / "developer" / "meta.json").read_text(
            encoding="utf-8"
        )
        devv2 = (AGENTS_DIR / "developer[v2]" / "meta.json").read_text(
            encoding="utf-8"
        )
        import json

        assert json.loads(dev)["version"] == json.loads(devv2)["version"]
        assert compose_agent_space_key("developer", None) != (
            "developer[2.0.0]"
        )

    def test_unresolvable_base_returns_none_fail_closed(self):
        """No base id ⇒ None ⇒ row stamps NULL ⇒ matches NO version's
        automatic warm-start (fail-closed), never a raised error."""
        assert compose_agent_space_key(None, "v2") is None
        assert compose_agent_space_key(None, None) is None
        assert compose_agent_space_key("", "v2") is None
        assert compose_agent_space_key("   ", "v2") is None

    def test_whitespace_normalized(self):
        assert compose_agent_space_key(" developer ", None) == "developer"
        assert compose_agent_space_key("developer", " v2 ") == "developer[v2]"
        assert compose_agent_space_key("developer", "  ") == "developer"

    def test_matches_registry_parse_roundtrip(self):
        """The key composition and the directory-name parser are
        inverse operations (the registry composite-key format)."""
        for dir_name, want in (
            ("developer", "developer"),
            ("developer[v2]", "developer[v2]"),
            ("tester", "tester"),
        ):
            base, tag = _parse_agent_dir_name(dir_name)
            assert compose_agent_space_key(base, tag) == want


# ============================================================================
# (b) Enumeration stability — per-directory space keys
# ============================================================================


def _scan_snapshot_enabled_space_keys() -> list[str]:
    """Enumerate agents/ per-DIRECTORY, mapping each snapshot-enabled
    directory to its VERSIONED space key (per-dir identity, never the
    bare/colliding meta ``id``)."""
    import json

    enabled: list[str] = []
    for meta_path in sorted(AGENTS_DIR.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("snapshot_enabled") is True:
            base, tag = _parse_agent_dir_name(meta_path.parent.name)
            enabled.append(f"{base}[{tag}]" if tag else base)
    return enabled


class TestEnumerationStability:
    def test_repeat_scan_yields_identical_sorted_set(self):
        first = sorted(_scan_snapshot_enabled_space_keys())
        second = sorted(_scan_snapshot_enabled_space_keys())
        assert first == second

    def test_pinned_expected_set(self):
        """Ratified targets: developer (base) + developer[v2] + tester —
        per-directory identity; the pre-vsss pin collapsed the two
        developer directories into one colliding 'developer' entry."""
        assert sorted(_scan_snapshot_enabled_space_keys()) == [
            "developer",
            "developer[v2]",
            "tester",
        ]

    def test_scan_distinguishes_base_and_tagged_developer_dirs(self):
        """Both developer directories exist and map to DIFFERENT keys —
        the pin's per-dir mapping must never dedupe them."""
        assert (AGENTS_DIR / "developer" / "meta.json").exists()
        assert (AGENTS_DIR / "developer[v2]" / "meta.json").exists()
        keys = _scan_snapshot_enabled_space_keys()
        assert "developer" in keys
        assert "developer[v2]" in keys


# ============================================================================
# (c) Predicate isolation — real repo, real SQL predicate
# ============================================================================


class TestPredicateIsolation:
    def test_a_space_row_invisible_to_b_space_query(self, engine: Any):
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-a", agent_space_key="developer")
        )
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-b", agent_space_key="developer[v2]"
            )
        )
        a_view = repo.list_active_by_project("p1", agent_space_key="developer")
        assert [r.id for r in a_view] == ["snap-a"]
        b_view = repo.list_active_by_project(
            "p1", agent_space_key="developer[v2]"
        )
        assert [r.id for r in b_view] == ["snap-b"]

    def test_null_rows_invisible_to_every_space_query(self, engine: Any):
        """Legacy NULL rows match NO version's automatic warm-start —
        exact equality; SQL ``= NULL`` is never true."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)
        )
        for key in ("developer", "developer[v2]", "tester"):
            rows = repo.list_active_by_project("p1", agent_space_key=key)
            assert rows == [], f"NULL row leaked into space {key!r}"
        # And the list surface sees it (hygiene visibility).
        items, total = repo.list_with_filters(project_id="p1")
        assert total == 1

    def test_no_space_filter_keeps_public_shape(self, engine: Any):
        """``agent_space_key=None`` (public snapshot_search / HTTP list
        shape) is unfiltered across spaces — observability surface."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-a", agent_space_key="developer")
        )
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-b", agent_space_key="developer[v2]"
            )
        )
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)
        )
        rows = repo.list_active_by_project("p1")
        assert sorted(r.id for r in rows) == ["snap-a", "snap-b", "snap-legacy"]

    def test_superseded_never_surfaces_even_in_own_space(self, engine: Any):
        from daemon.repositories.snapshot.models import SNAPSHOT_STATUS_SUPERSEDED

        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-old", agent_space_key="developer")
        )
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-superseded",
                agent_space_key="developer",
                status=SNAPSHOT_STATUS_SUPERSEDED,
            )
        )
        rows = repo.list_active_by_project("p1", agent_space_key="developer")
        assert [r.id for r in rows] == ["snap-old"]

    def test_space_filter_scopes_per_project(self, engine: Any):
        """Same space key in two projects stays project-isolated (D8 +
        vsss compose, not collapse)."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-p1", project_id="p1",
                          agent_space_key="developer")
        )
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-p2", project_id="p2",
                          agent_space_key="developer")
        )
        rows = repo.list_active_by_project(
            "p1", agent_space_key="developer"
        )
        assert [r.id for r in rows] == ["snap-p1"]


# ============================================================================
# (d) NULL reachability — automatic fail-closed vs explicit permissive
# ============================================================================


class _SpaceAwareRepo:
    """Duck-typed repo recording the search service's Stage-0 call shape.

    Mirrors ``SnapshotRepository.list_active_by_project`` INCLUDING the
    vsss keyword — a stale duck-type without the kwarg would fail the
    thread-through (TypeError → fail-soft zero candidates).
    """

    def __init__(self, rows: list[Snapshot]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    def list_active_by_project(
        self,
        project_id: str,
        limit: int = 50,
        *,
        agent_space_key: str | None = None,
    ) -> list[Snapshot]:
        self.calls.append(
            {
                "project_id": project_id,
                "limit": limit,
                "agent_space_key": agent_space_key,
                "agent_space_key_is_kwarg": True,
            }
        )
        out = [
            r
            for r in self._rows
            if r.project_id == project_id
            and r.status == SNAPSHOT_STATUS_ACTIVE
        ]
        if agent_space_key is not None:
            out = [r for r in out if r.agent_space_key == agent_space_key]
        return out[:limit]

    def filter_by_tags(
        self,
        candidates: list[Snapshot],
        tags: list[str],
        tag_mode: str = "all",
    ) -> list[Snapshot]:
        wanted = {t for t in tags if t}
        if not wanted:
            return list(candidates)
        return [
            r
            for r in candidates
            if wanted.issubset(set(r.domain_tags or []))
        ]

    def get_embeddings(self, snapshot_id: str) -> list[Any]:
        return []


class _ConstEmbeddingService:
    """Deterministic embedding fake — same unit vector for every text."""

    async def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        return 1.0


def _staleness_fresh(snapshot: Any) -> dict[str, Any]:
    return {"freshness": "fresh", "snapshot_age_days": 1.0, "warnings": []}


class TestSearchServiceSpaceThreading:
    def test_service_passes_space_key_as_kwarg_only_when_set(self):
        repo = _SpaceAwareRepo(
            [_snapshot_row(snapshot_id="snap-a", agent_space_key="developer")]
        )
        service = SnapshotSearchService(
            snapshot_repo=repo,  # type: ignore[arg-type]
            embedding_service=_ConstEmbeddingService(),  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_fresh,
        )

        async def _driver():
            with_space = await service.search(
                "upgrade pipeline",
                project_id="p1",
                agent_space_key="developer",
            )
            without_space = await service.search(
                "upgrade pipeline", project_id="p1"
            )
            return with_space, without_space

        with_space, without_space = asyncio.run(_driver())
        assert with_space["error"] is None
        assert [r["snapshot_id"] for r in with_space["results"]] == ["snap-a"]
        assert without_space["error"] is None
        assert [r["snapshot_id"] for r in without_space["results"]] == [
            "snap-a"
        ]
        assert len(repo.calls) == 2
        assert repo.calls[0]["agent_space_key"] == "developer"
        assert repo.calls[0]["agent_space_key_is_kwarg"] is True
        assert repo.calls[1]["agent_space_key"] is None

    def test_service_space_filter_hides_null_rows(self):
        repo = _SpaceAwareRepo(
            [_snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)]
        )
        service = SnapshotSearchService(
            snapshot_repo=repo,  # type: ignore[arg-type]
            embedding_service=_ConstEmbeddingService(),  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_fresh,
        )

        async def _driver():
            return await service.search(
                "upgrade pipeline", project_id="p1", agent_space_key="developer"
            )

        result = asyncio.run(_driver())
        assert result == {"results": [], "error": None}


class _RecordingSearchService:
    """Canned-result fake that records whether the space key arrived."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        project_id: str,
        tags: Any = None,
        tag_mode: str = "all",
        limit: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "project_id": project_id,
                "limit": limit,
                **{
                    k: v
                    for k, v in _.items()
                    if k == "agent_space_key"
                },
            }
        )
        return {"results": list(self.results), "error": None}


class _FreshStalenessService:
    async def staleness_report(self, snapshot_id: str) -> dict[str, Any]:
        return {"freshness": "fresh", "warnings": []}


def _caller_rows() -> dict[str, Any]:
    """Caller rows in project p1 (unified-spawn fixture row shape)."""
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

    return {"caller-1": _row("caller-1", agent_id="coder")}


def _spawn_manager(repo: SnapshotRepository, search_service: Any) -> Any:
    """MagicMock manager via the CANONICAL unified-spawn helper, with
    the snapshot seams overridden for the vsss cells."""
    from tests.helpers.unified_spawn_fixtures import make_unified_spawn_manager

    m = make_unified_spawn_manager(_caller_rows(), repo)
    m._snapshot_service = _FreshStalenessService()
    m._snapshot_search_service = search_service
    return m


class TestAutomaticPathFailClosed:
    """NULL/unattributed rows never match the AUTOMATIC warm-start."""

    def test_missing_space_key_is_no_hit_cold_without_searching(
        self, engine: Any
    ):
        """A seam that cannot resolve the target version NEVER
        searches — the unfiltered fallback would expose legacy rows
        to every version (fail-closed ruling)."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)
        )
        search = _RecordingSearchService(
            [{"snapshot_id": "snap-legacy", "freshness": "fresh"}]
        )
        manager = _spawn_manager(repo, search)

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id=None,
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key=None,
            )

        resolution = asyncio.run(_driver())
        assert resolution.started == "cold"
        assert resolution.cold_reason == "no-hit"
        assert resolution.consumed is None
        # The strict gate: the search seam was never consulted.
        assert search.calls == []
        assert any(
            "fail-closed" in w for w in resolution.warnings
        ), resolution.warnings

    def test_wrong_space_search_receives_caller_space_key(self, engine: Any):
        """Threading + cold-outcome proof: a v2 caller's automatic search
        carries the COMPOSITE key to the search seam, and the canned
        wrong-space hit resolves cold no-hit (the predicate rejection of
        a foreign-space row is pinned at the repo layer in
        TestPredicateIsolation; the canned fake here bypasses the
        predicate deliberately)."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-v1", agent_space_key="developer"
            )
        )
        search = _RecordingSearchService(
            [{"snapshot_id": "snap-v1", "freshness": "fresh"}]
        )
        manager = _spawn_manager(repo, search)

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id=None,
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key="developer[v2]",
            )

        resolution = asyncio.run(_driver())
        # The canned service returns a hit, but the REAL repo predicate
        # ( consulted via repo.get + active check below) — more
        # importantly the REAL predicate path is covered in
        # TestPredicateIsolation; here the canned hit is for the WRONG
        # space and the resolution must not consume it when the repo
        # agrees the row belongs to another space. The canned fake
        # bypasses the predicate, so assert the SEARCH received the
        # caller's space key (threading proof) — predicate rejection
        # is pinned at the repo layer. The outcome assertions below pin
        # the refused hit: cold, reason no-hit (a swallowed re-check
        # exception must not surface as anything but cold no-hit).
        assert search.calls[0]["agent_space_key"] == "developer[v2]"
        assert resolution.started == "cold"
        assert resolution.cold_reason == "no-hit"

    def test_real_service_null_row_never_warms_own_space_search(
        self, engine: Any
    ):
        """End-to-end automatic path over the REAL service + REAL repo:
        a NULL row is invisible to a space-keyed search ⇒ cold no-hit."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)
        )
        service = SnapshotSearchService(
            snapshot_repo=repo,  # type: ignore[arg-type]
            embedding_service=_ConstEmbeddingService(),  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_fresh,
        )
        manager = _spawn_manager(repo, service)

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id=None,
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key="developer",
            )

        resolution = asyncio.run(_driver())
        assert resolution.started == "cold"
        assert resolution.cold_reason == "no-hit"
        assert resolution.consumed is None

    def test_matching_space_row_warms(self, engine: Any):
        """Positive control: the automatic path WARMS when the row
        carries the queried space key (non-vacuity for the cells
        above)."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-v2", agent_space_key="developer[v2]"
            )
        )
        service = SnapshotSearchService(
            snapshot_repo=repo,  # type: ignore[arg-type]
            embedding_service=_ConstEmbeddingService(),  # type: ignore[arg-type]
            llm_config={},
            staleness_fn=_staleness_fresh,
        )
        manager = _spawn_manager(repo, service)

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id=None,
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key="developer[v2]",
            )

        resolution = asyncio.run(_driver())
        assert resolution.started == "warm"
        assert resolution.consumed is not None
        assert resolution.consumed.id == "snap-v2"


class TestExplicitPathVersionPermissive:
    """Ratified decision 2: explicit snapshot_id is project+status
    verified ONLY — it reaches NULL (unattributed) rows and rows of
    ANY space."""

    def test_explicit_id_reaches_null_space_row(self, engine: Any):
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-legacy", agent_space_key=None)
        )
        manager = _spawn_manager(repo, _RecordingSearchService())

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id="snap-legacy",
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key=None,
            )

        resolution = asyncio.run(_driver())
        assert resolution.started == "warm"
        assert resolution.consumed is not None
        assert resolution.consumed.id == "snap-legacy"

    def test_explicit_id_reaches_foreign_space_row(self, engine: Any):
        """Direct addressing is not space resolution: a v2 caller may
        explicitly consume a v1-space snapshot by id."""
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(
                snapshot_id="snap-v1", agent_space_key="developer"
            )
        )
        manager = _spawn_manager(repo, _RecordingSearchService())

        async def _driver():
            return await resolve_spawn_snapshot(
                manager,
                task="upgrade pipeline",
                snapshot_id="snap-v1",
                tags=[],
                verify="metadata",
                allow_cross_project=False,
                caller_project_id="p1",
                agent_space_key="developer[v2]",
            )

        resolution = asyncio.run(_driver())
        assert resolution.started == "warm"
        assert resolution.consumed is not None
        assert resolution.consumed.id == "snap-v1"


class TestSpawnToolSpaceKeyThreading:
    """The spawn TOOL composes the key from the resolved spawn identity
    and threads it into resolve_spawn_snapshot → the search seam."""

    def _build(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo: SnapshotRepository,
        version_tag: str | None,
        search_service: Any,
    ) -> Any:
        from tests.helpers.unified_spawn_fixtures import (
            build_spawn_tool,
            make_unified_spawn_manager,
        )

        m = make_unified_spawn_manager(_caller_rows(), repo)
        m._snapshot_service = _FreshStalenessService()
        m._snapshot_search_service = search_service

        async def _fake_default_version_tag(project_repo, agent_id, registry):
            return version_tag

        monkeypatch.setattr(
            "daemon.tools.instance._resolve_default_version_tag",
            _fake_default_version_tag,
        )
        monkeypatch.setattr(
            "daemon.tools.instance._target_snapshot_enabled",
            lambda agent_id, version_tag=None: True,
        )
        monkeypatch.setattr(
            "daemon.tools.instance._check_team_membership",
            lambda caller, requested, tag: None,
        )
        return build_spawn_tool(m, caller_id="caller-1", agent_id="coder")

    def _run(self, coro: Any) -> str:
        return asyncio.run(coro)

    def test_untagged_target_searches_bare_space(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ):
        repo = SnapshotRepository(engine)
        search = _RecordingSearchService()
        tool = self._build(monkeypatch, repo, None, search)
        self._run(
            tool.ainvoke(
                {
                    "agent_id": "tester",
                    "task": "upgrade pipeline",
                    "project_id": "p1",
                    "auto_dispatch": False,
                }
            )
        )
        assert len(search.calls) == 1
        assert search.calls[0]["agent_space_key"] == "tester"

    def test_tagged_target_searches_composite_space(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ):
        repo = SnapshotRepository(engine)
        search = _RecordingSearchService()
        tool = self._build(monkeypatch, repo, "v2", search)
        self._run(
            tool.ainvoke(
                {
                    "agent_id": "developer",
                    "task": "upgrade pipeline",
                    "project_id": "p1",
                    "auto_dispatch": False,
                }
            )
        )
        assert len(search.calls) == 1
        assert search.calls[0]["agent_space_key"] == "developer[v2]"


# ============================================================================
# (e) Backfill — only NULL + resolvable stamped; re-run no-op; deleted
#     instances stay NULL forever
# ============================================================================


def _load_backfill_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "backfill_snapshot_agent_space_under_test", BACKFILL_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backfill_env(engine: Any):
    """Seeded repo + instance rows for the backfill cells.

    Rows:
      * ``snap-v2``    — NULL, target inst-v2 ('developer','v2') → resolvable
      * ``snap-base``  — NULL, target inst-base ('developer', None) → resolvable
      * ``snap-gone``  — NULL, target inst-deleted (NO row) → stays NULL
      * ``snap-stamped`` — pre-stamped 'tester', target inst-tester → untouched
    """
    from daemon.repositories.instance.models import Instance
    from sqlmodel import Session

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id="inst-v2",
                project_id="p1",
                agent_id="developer",
                agent_tag="v2",
                agent_dir="/agents/developer[v2]",
            )
        )
        session.add(
            Instance(
                instance_id="inst-base",
                project_id="p1",
                agent_id="developer",
                agent_tag=None,
                agent_dir="/agents/developer",
            )
        )
        session.add(
            Instance(
                instance_id="inst-tester",
                project_id="p1",
                agent_id="tester",
                agent_tag=None,
                agent_dir="/agents/tester",
            )
        )
        # inst-deleted deliberately ABSENT.
        session.commit()

    repo = SnapshotRepository(engine)
    repo.create_with_embeddings(
        _snapshot_row(snapshot_id="snap-v2", target="inst-v2")
    )
    repo.create_with_embeddings(
        _snapshot_row(snapshot_id="snap-base", target="inst-base")
    )
    repo.create_with_embeddings(
        _snapshot_row(snapshot_id="snap-gone", target="inst-deleted")
    )
    repo.create_with_embeddings(
        _snapshot_row(
            snapshot_id="snap-stamped",
            target="inst-tester",
            agent_space_key="tester",
        )
    )
    return {"engine": engine, "repo": repo, "script": _load_backfill_module()}


class TestBackfill:
    def test_dry_run_counts_without_stamping(self, backfill_env: dict):
        script = backfill_env["script"]
        result = script.run_backfill(backfill_env["engine"], apply=False)
        assert result["scanned"] == 3  # the 3 NULL rows; snap-stamped excluded
        assert result["resolved"] == 2
        assert result["stamped"] == 0
        assert result["still_null"] == 1
        assert result["resolved_map"] == {
            "snap-v2": "developer[v2]",
            "snap-base": "developer",
        }
        # Nothing written.
        repo = backfill_env["repo"]
        assert repo.get("snap-v2").agent_space_key is None
        assert repo.get("snap-base").agent_space_key is None
        assert repo.get("snap-gone").agent_space_key is None

    def test_apply_stamps_only_null_and_resolvable(self, backfill_env: dict):
        script = backfill_env["script"]
        result = script.run_backfill(backfill_env["engine"], apply=True)
        assert result["stamped"] == 2
        repo = backfill_env["repo"]
        assert repo.get("snap-v2").agent_space_key == "developer[v2]"
        assert repo.get("snap-base").agent_space_key == "developer"
        # Deleted-instance row stays NULL FOREVER (fail-closed ruling).
        assert repo.get("snap-gone").agent_space_key is None
        # Pre-stamped row untouched.
        assert repo.get("snap-stamped").agent_space_key == "tester"

    def test_rerun_is_noop(self, backfill_env: dict):
        script = backfill_env["script"]
        script.run_backfill(backfill_env["engine"], apply=True)
        second = script.run_backfill(backfill_env["engine"], apply=True)
        assert second["scanned"] == 1  # only the deleted-instance row remains
        assert second["resolved"] == 0
        assert second["stamped"] == 0
        assert second["still_null"] == 1
        repo = backfill_env["repo"]
        assert repo.get("snap-v2").agent_space_key == "developer[v2]"

    def test_deleted_instance_row_stays_null_across_passes(
        self, backfill_env: dict
    ):
        script = backfill_env["script"]
        for _ in range(3):
            script.run_backfill(backfill_env["engine"], apply=True)
        assert backfill_env["repo"].get("snap-gone").agent_space_key is None

    def test_repo_stamp_is_cas_gated(self, backfill_env: dict):
        repo = backfill_env["repo"]
        # Fresh stamp lands.
        assert repo.stamp_agent_space_key("snap-gone", "tester") is True
        # Second stamp on the same row is refused (never overwrite).
        assert repo.stamp_agent_space_key("snap-gone", "developer") is False
        assert repo.get("snap-gone").agent_space_key == "tester"
        # Empty/blank key refused; unknown id refused.
        assert repo.stamp_agent_space_key("snap-v2", "") is False
        assert repo.stamp_agent_space_key("snap-v2", "   ") is False
        assert repo.stamp_agent_space_key("no-such-row", "x") is False
        # Restore the fixture invariant for later tests (fresh engine
        # per test — this stamp only affected this test's engine).
        backfill_env["engine"].dispose()

    def test_resolve_for_missing_instance_is_none(self, engine: Any):
        script = _load_backfill_module()
        repo = SnapshotRepository(engine)
        repo.create_with_embeddings(
            _snapshot_row(snapshot_id="snap-orphan", target="inst-nowhere")
        )
        row = repo.get("snap-orphan")
        assert (
            script.resolve_space_key_for_row(engine, row) is None
        )
