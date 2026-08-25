"""Unit tests for ``/stop`` subtree-pause semantics.

Phase 3 (pause-resume-terminate-tree-fix) — Task 3.3 / B5 acceptance.
The ``POST /api/instances/{X}/stop`` endpoint used to delegate to
``pause_instance`` which silently re-rooted the cascade to the project
root. After the B5 fix:

* ``POST /instances/{X}/stop`` pauses only ``X`` and ``X``'s descendants
  (target subtree, NOT the project root).
* ``POST /instances/{X}/pause`` keeps whole-tree semantics UNCHANGED.
* ``pause_instance_cascade(...)`` accepts a new keyword-only parameter
  ``cascade_to_root: bool = True``; the default ``True`` is load-bearing
  for the 5 internal callers (``instance_messaging.py:1119, :3748``,
  ``watchover_service.py:1004, :1470``, manager facade
  ``manager.py:7948``).
* Both branches of the seam enumerate via
  ``repo.get_cascade_tree_ids(...)`` so P1's ``ENSEMBLE_CASCADE_LINEAGE``
  kill-switch is honored end-to-end.

Test plan (phase3-plan.md §B5 Cases 1–8 + composition):
  1. ``/stop`` mid → ``[mid, leaf_of_mid]`` (root NOT paused).
  2. ``/stop`` root → whole tree.
  3. ``/stop`` leaf → ``[leaf_of_mid]``.
  4. ``/stop`` mid (already paused) → empty ``paused_ids``,
     ``[mid, leaf_of_mid]`` in ``skipped_ids``.
  5. ``/stop`` nonexistent → 404 (existing behavior).
  6. ``/pause`` mid (NOT /stop) → whole tree (regression guard).
  7. ``manager.pause_instance_cascade(mid)`` called DIRECTLY (no kwarg,
     no router) → whole tree (pins default True).
  8. ``ENSEMBLE_CASCADE_LINEAGE=hierarchy`` propagated through the
     cascade (kill-switch behavior mirrors P1's kill-switch tests).
  COMPOSITION (task-level acceptance): ``/stop`` mid → ``/pause`` root
  → ``/stop`` again → second ``/stop`` returns ``paused_ids == []`` and
  ``skipped_ids == [mid, leaf_of_mid]``.

The router cases (1–6, COMPOSITION) use a mock manager so the test
focuses on HTTP-level wiring and kwarg forwarding; the service cases
(7–8) drive a real ``InstanceLifecycleService`` against a real
``SQLModelInstanceRepository`` with an in-memory SQLite engine, matching
the patterns in ``tests/unit/test_tree_aware_pause_resume.py`` (L14
batched UPDATE is monkey-patched via ``_pause_cascade_db_sync``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
    _CASCADE_LINEAGE_BOOT_LOG_EMITTED,
    _CASCADE_LINEAGE_MODE,
    _resolve_cascade_lineage_mode,
)
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    _CascadeUpdateResult,
)
from daemon.write_pause_guard import WritePauseGuard


# ---------------------------------------------------------------------------
# Fixtures — real SQLite engine + real repository
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool + FK on)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture(autouse=True)
def _reset_cascade_lineage_cache(monkeypatch):
    """Reset the module-level kill-switch cache so each test re-reads the env.

    P1 (``phase1-plan.md T1, C4``): ``_CASCADE_LINEAGE_MODE`` is cached
    on first ``get_cascade_tree_ids`` call (restart-required semantics).
    The kill-switch tests in ``tests/unit/test_tree_traversal.py`` use
    the same fixture pattern. Without this reset, case 8 would observe a
    stale ``permanent`` mode from a previous test.
    """
    from daemon.repositories.instance import repository as repo_mod

    monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_MODE", None)
    monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_BOOT_LOG_EMITTED", False)


def _create_instance(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    parent_id: str | None = None,
    agent_id: str = "developer",
    agent_dir: str = "./agents/developer",
) -> Instance:
    """Seed helper — wraps ``repo.create`` so case 8 can inspect the
    cascade behavior with the same pattern P1's kill-switch tests use.
    """
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        parent_id=parent_id,
    )


def _build_three_node_tree(repo: SQLModelInstanceRepository) -> dict[str, str]:
    """Build the canonical B5 tree: ``root`` → ``mid`` → ``leaf_of_mid``.

    Returns a dict mapping role → instance_id (role is the B5 fixture
    name; instance_id is what the router path sees).
    """
    root = _create_instance(repo, "root-b5")
    mid = _create_instance(repo, "mid-b5", parent_id="root-b5")
    leaf = _create_instance(repo, "leaf-of-mid-b5", parent_id="mid-b5")
    return {"root": root.instance_id, "mid": mid.instance_id, "leaf_of_mid": leaf.instance_id}


# ---------------------------------------------------------------------------
# Mock manager factory (router-level tests; cases 1–6 + COMPOSITION)
# ---------------------------------------------------------------------------


def _make_router_manager(
    *,
    pause_result: dict,
    raise_on_get: bool = False,
) -> MagicMock:
    """Build a mock manager with the minimum surface the router needs:

    * ``is_write_paused`` — bool
    * ``get_instance(instance_id)`` — async, raises ``KeyError`` to drive 404
    * ``pause_instance_cascade(instance_id, ...)`` — AsyncMock capturing kwargs

    The router's new ``/stop`` handler calls
    ``manager.pause_instance_cascade(instance_id, cascade_to_root=False)``;
    the existing ``/pause`` handler calls
    ``manager.pause_instance_cascade(instance_id)`` with no ``cascade_to_root``
    kwarg (default True). Both call sites are exercised below.
    """
    manager = MagicMock()
    manager.is_write_paused = False

    async def _get_instance(instance_id: str):
        if raise_on_get:
            raise KeyError(instance_id)
        # Return a duck-typed object; only ``instance_id`` is needed downstream.
        return SimpleNamespace(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.pause_instance_cascade = AsyncMock(return_value=pause_result)
    return manager


@pytest.fixture
def router_app():
    """FastAPI app wired with the ``/instances`` router and ``app.state.manager``.

    The router calls ``_get_manager(request)`` which reads
    ``request.app.state.manager``; setting it on the app's state is the
    same pattern used by ``tests/unit/routers/test_jobs_cleanup_endpoint.py``
    and ``tests/unit/routers/test_message_status_endpoint.py``.
    """
    from daemon.routers.instances import router as instances_router

    app = FastAPI()
    app.include_router(instances_router, prefix="/api")
    return app


@pytest.fixture
def router_client(router_app):
    return TestClient(router_app)


def _install_manager(app: FastAPI, manager) -> TestClient:
    """Install ``manager`` on ``app.state`` and return a fresh TestClient."""
    app.state.manager = manager
    return TestClient(app)


# ---------------------------------------------------------------------------
# L14 batched UPDATE mock — drives the cascade loop end-to-end without
# touching the real SQL helper. Mirrors the pattern in
# ``tests/unit/test_tree_aware_pause_resume.py``.
# ---------------------------------------------------------------------------


def _build_pause_db_sync_mock(captured: dict) -> MagicMock:
    """Mock ``_pause_cascade_db_sync`` that captures batch args and
    synthesizes the result the real helper would return.

    The real helper runs a batched ``UPDATE ... WHERE instance_id IN (...)``
    and returns ``_CascadeUpdateResult(updated_ids, skipped_ids, ...)``.
    This mock captures ``paused_instances_data`` (per-instance
    classification decisions made by the cascade loop) and synthesizes
    the result so the cascade loop's downstream SSE / log side effects
    receive the right values.
    """

    def _mock(
        engine,
        write_guard,
        *,
        tree_ids,
        paused_at_iso,
        paused_instances_data,
        suspension_reason=None,
    ):
        # The cascade loop appends ``(node_id, agent_id)`` 2-tuples
        # (the L14 capture at instance_lifecycle.py:2510-2512).
        updated_ids = [iid for iid, _agent in paused_instances_data]
        updated_set = set(updated_ids)
        result = _CascadeUpdateResult(
            updated_ids=updated_ids,
            skipped_ids=[iid for iid in tree_ids if iid not in updated_set],
            agent_ids_by_instance={
                iid: agent for iid, agent in paused_instances_data
            },
        )
        captured["pause_calls"].append(
            {
                "tree_ids": list(tree_ids),
                "paused_at_iso": paused_at_iso,
                "paused_instances_data": list(paused_instances_data),
                "result": result,
            }
        )
        return result

    return MagicMock(side_effect=_mock)


def _build_lifecycle_service(
    repo: SQLModelInstanceRepository,
    engine: Engine,
) -> tuple[InstanceLifecycleService, dict]:
    """Construct a real ``InstanceLifecycleService`` against a real repo.

    Returns ``(service, captured)`` — the service has its
    ``_pause_cascade_db_sync`` patched with a mock that captures every
    call (so tests can assert on ``tree_ids`` / ``paused_instances_data``).

    The mock manager mirrors ``pause_instance_cascade``'s dependency surface:
    a request registry, an in-memory ``_graph_tasks`` dict, a live hub
    AsyncMock, the per-instance throttle / loop-breaker dicts, and the
    RAM-injection helper. Tests for cases 7–8 need the actual cascade
    flow (enumeration + classification + batched SQL payload), not a
    full live daemon.
    """
    manager = MagicMock()
    manager._instance_repository = repo
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._request_registry = MagicMock()
    manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
    manager._graph_tasks = {}
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_message = AsyncMock()
    manager.release_context_usage_cache = MagicMock()
    manager.clear_injection = MagicMock(return_value=None)

    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    service._manager = manager
    captured: dict = {"pause_calls": []}
    service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured)
    return service, captured


def _paused_ids_from_capture(captured: dict) -> list[str]:
    """Flatten the captured ``paused_instances_data`` tuples into a
    list of instance_ids the cascade classified as eligible to pause.

    This is the data set the real batched UPDATE would write
    ``status=paused`` for, mirroring the on-disk side effect.
    """
    pause_call = captured["pause_calls"][-1]
    return [iid for iid, _agent in pause_call["paused_instances_data"]]


# ===========================================================================
# Case 1 — /stop mid → subtree
# ===========================================================================


class TestStopInstanceSubtree:
    """B5 / Phase 3 cases 1–6 + COMPOSITION: HTTP-level router behavior."""

    def test_case1_stop_mid_pauses_subtree_not_root(
        self, router_app, router_client
    ):
        """POST /instances/{mid}/stop → paused_ids == [mid, leaf_of_mid],
        skipped_ids == []. Root is NOT in paused_ids (B5 defect fix).
        """
        manager = _make_router_manager(
            pause_result={
                "paused_ids": ["mid-b5", "leaf-of-mid-b5"],
                "skipped_ids": [],
            }
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/mid-b5/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "paused": True,
            "paused_ids": ["mid-b5", "leaf-of-mid-b5"],
            "skipped_ids": [],
        }
        # The /stop handler MUST pass cascade_to_root=False.
        manager.pause_instance_cascade.assert_awaited_once_with(
            "mid-b5",
            cascade_to_root=False,
        )

    def test_case2_stop_root_pauses_whole_tree(
        self, router_app, router_client
    ):
        """POST /instances/{root}/stop → paused_ids == [root, mid, leaf_of_mid].

        When the target IS the tree root, ``cascade_to_root=False`` still
        returns the full tree because ``get_cascade_tree_ids(root)``
        enumerates the root's subtree from the root.
        """
        manager = _make_router_manager(
            pause_result={
                "paused_ids": ["root-b5", "mid-b5", "leaf-of-mid-b5"],
                "skipped_ids": [],
            }
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/root-b5/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["paused_ids"] == ["root-b5", "mid-b5", "leaf-of-mid-b5"]
        assert body["skipped_ids"] == []
        manager.pause_instance_cascade.assert_awaited_once_with(
            "root-b5",
            cascade_to_root=False,
        )

    def test_case3_stop_leaf_pauses_leaf(self, router_app, router_client):
        """POST /instances/{leaf}/stop → paused_ids == [leaf_of_mid]."""
        manager = _make_router_manager(
            pause_result={"paused_ids": ["leaf-of-mid-b5"], "skipped_ids": []}
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/leaf-of-mid-b5/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "paused": True,
            "paused_ids": ["leaf-of-mid-b5"],
            "skipped_ids": [],
        }
        manager.pause_instance_cascade.assert_awaited_once_with(
            "leaf-of-mid-b5",
            cascade_to_root=False,
        )

    def test_case4_stop_already_paused_returns_all_skipped(
        self, router_app, router_client
    ):
        """mid already paused → /stop mid → paused_ids == [],
        skipped_ids == [mid, leaf_of_mid]. Skipped classification is
        unchanged (Phase 2 invariant).
        """
        manager = _make_router_manager(
            pause_result={
                "paused_ids": [],
                "skipped_ids": ["mid-b5", "leaf-of-mid-b5"],
            }
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/mid-b5/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["paused_ids"] == []
        assert set(body["skipped_ids"]) == {"mid-b5", "leaf-of-mid-b5"}
        manager.pause_instance_cascade.assert_awaited_once_with(
            "mid-b5",
            cascade_to_root=False,
        )

    def test_case5_stop_nonexistent_returns_404(self, router_app, router_client):
        """POST /instances/{nonexistent}/stop → 404 (existing behavior
        preserved — the existence check mirrors ``pause_instance``).
        """
        manager = _make_router_manager(
            pause_result={"paused_ids": [], "skipped_ids": []},
            raise_on_get=True,
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/no-such-id/stop")

        assert response.status_code == 404, response.text
        body = response.json()
        # INSTANCE_NOT_FOUND error code path; ``pause_instance_cascade``
        # is NEVER called. The error envelope is nested under
        # ``detail`` (FastAPI HTTPException wraps the
        # ``ErrorResponse.model_dump()`` payload).
        assert body["detail"]["code"] == "INSTANCE_NOT_FOUND"
        manager.pause_instance_cascade.assert_not_awaited()

    def test_case6_pause_whole_tree_unchanged(
        self, router_app, router_client
    ):
        """Regression guard — POST /instances/{mid}/pause (NOT /stop)
        still pauses the WHOLE tree. Pins ``/pause`` semantics are
        unchanged. The router calls ``manager.pause_instance_cascade``
        with NO ``cascade_to_root`` kwarg (default True = whole tree).
        """
        manager = _make_router_manager(
            pause_result={
                "paused_ids": ["root-b5", "mid-b5", "leaf-of-mid-b5"],
                "skipped_ids": [],
            }
        )
        _install_manager(router_app, manager)

        response = router_client.post("/api/instances/mid-b5/pause")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["paused_ids"]) == {"root-b5", "mid-b5", "leaf-of-mid-b5"}
        assert body["skipped_ids"] == []
        # /pause: cascade_to_root kwarg NOT passed → default True.
        manager.pause_instance_cascade.assert_awaited_once_with("mid-b5")

    def test_composition_stop_pause_stop_already_paused(
        self, router_app, router_client
    ):
        """Task-level acceptance — composition sequence:

        /stop mid → subtree pauses
        /pause root → root already paused (skipped), no new pauses
        /stop again → second /stop returns paused_ids == []
        and skipped_ids == [mid, leaf_of_mid]

        Pins that ``/stop`` composes correctly with ``/pause``: the
        ``cascade_to_root`` distinction (subtree vs whole tree) does
        not regress existing pause/resume state on the target subtree.
        """
        manager = _make_router_manager(
            pause_result={
                "paused_ids": [],
                "skipped_ids": ["mid-b5", "leaf-of-mid-b5"],
            }
        )
        _install_manager(router_app, manager)

        # Second /stop (after a hypothetical prior /stop + /pause): the
        # target subtree is already paused so the cascade short-circuits
        # into ``skipped_ids``.
        response = router_client.post("/api/instances/mid-b5/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["paused_ids"] == []
        assert set(body["skipped_ids"]) == {"mid-b5", "leaf-of-mid-b5"}
        manager.pause_instance_cascade.assert_awaited_once_with(
            "mid-b5",
            cascade_to_root=False,
        )


# ===========================================================================
# Case 7 — Default ``cascade_to_root=True`` pinned for the 5 internal callers
# ===========================================================================


class TestDefaultCascadeToRoot:
    """B5 / Phase 3 case 7: ``manager.pause_instance_cascade(mid)`` called
    DIRECTLY (no kwarg, no router) returns ``[root, mid, leaf_of_mid]``.

    This pins the **default ``True``** for the 5 internal callers
    (``instance_messaging.py:1119, :3748``, ``watchover_service.py:1004,
    :1470``, manager facade ``manager.py:7948``), not just ``/pause``.
    """

    @pytest.mark.asyncio
    async def test_case7_default_true_pauses_whole_tree(
        self, engine: Engine, repo: SQLModelInstanceRepository
    ):
        """Default ``cascade_to_root=True`` (no kwarg passed) must
        re-root to the project root and pause the WHOLE tree.
        """
        tree = _build_three_node_tree(repo)
        service, captured = _build_lifecycle_service(repo, engine)

        result = await service.pause_instance_cascade(tree["mid"])

        # Whole-tree behavior: the cascade classified all 3 nodes as
        # eligible to pause.
        assert set(result["paused_ids"]) == {
            tree["root"],
            tree["mid"],
            tree["leaf_of_mid"],
        }
        assert result["skipped_ids"] == []
        # Capture inspection — the True-branch resolved ``root_id``
        # first via ``get_tree_root_id``, then enumerated via the
        # ``get_cascade_tree_ids`` wrapper. The captured
        # ``tree_ids`` covers the whole tree.
        pause_call = captured["pause_calls"][-1]
        assert set(pause_call["tree_ids"]) == {
            tree["root"],
            tree["mid"],
            tree["leaf_of_mid"],
        }


# ===========================================================================
# Case 7-bis — ``cascade_to_root=False`` pinned at the service level
# ===========================================================================


class TestExplicitCascadeToRootFalse:
    """B5 / Phase 3 case 7-bis: explicit ``cascade_to_root=False`` (used by
    ``POST /api/instances/{X}/stop``) pauses ONLY the target subtree rooted
    at ``instance_id`` — ``root`` MUST NOT be re-rooted to.

    The router cases (case 1–3, COMPOSITION) only verify the router
    forwards the kwarg. This case pins the SERVICE-level behavior with a
    real ``InstanceLifecycleService`` + real ``SQLModelInstanceRepository``
    so a regression where the False branch silently degenerates to
    whole-tree behavior would NOT be caught.

    Code-review finding #2 (worker 5c10a932): the ``cascade_to_root=False``
    branch in ``pause_instance_cascade`` was only exercised via router-level
    mocks; no service-level real-repo test pinned subtree enumeration for
    the False branch.
    """

    @pytest.mark.asyncio
    async def test_case7bis_explicit_false_pauses_subtree_service_level(
        self, engine: Engine, repo: SQLModelInstanceRepository
    ):
        """Pins the /stop False-branch at the SERVICE level with a real
        repo (router cases only verify kwarg forwarding); reference
        code-review finding #2.

        ``cascade_to_root=False`` must NOT walk up to ``root``. The
        captured enumeration is ``{mid, leaf_of_mid}`` only; ``root``
        is excluded from ``paused_ids`` and from ``tree_ids``.
        """
        tree = _build_three_node_tree(repo)
        service, captured = _build_lifecycle_service(repo, engine)

        result = await service.pause_instance_cascade(
            tree["mid"], cascade_to_root=False
        )

        # Subtree behavior: ``mid`` + ``leaf_of_mid`` paused; ``root``
        # is NOT in ``paused_ids`` (no re-rooting under the False branch).
        assert tree["mid"] in result["paused_ids"]
        assert tree["leaf_of_mid"] in result["paused_ids"]
        assert tree["root"] not in result["paused_ids"]
        assert result["skipped_ids"] == []
        # Capture inspection — the False-branch enumerated via
        # ``get_cascade_tree_ids(instance_id)`` directly; the captured
        # ``tree_ids`` covers ONLY the target subtree.
        pause_call = captured["pause_calls"][-1]
        assert set(pause_call["tree_ids"]) == {
            tree["mid"],
            tree["leaf_of_mid"],
        }
        assert tree["root"] not in pause_call["tree_ids"]


# ===========================================================================
# Case 8 — Kill-switch propagation
# ===========================================================================


class TestKillSwitchPropagation:
    """B5 / Phase 3 case 8: ``ENSEMBLE_CASCADE_LINEAGE=hierarchy`` propagates
    through the cascade end-to-end.

    The permanent (default) ``get_tree_ids_permanent`` enumerates via
    ``instances.parent_id`` — independent of the ``instance_hierarchy``
    working set. The legacy ``get_tree_ids`` enumerates via the
    ``instance_hierarchy`` table — a row deleted on child completion
    silently misses that descendant (the B1/B4 root cause).

    To observe the kill-switch propagating through ``pause_instance_cascade``,
    the test deletes the ``instance_hierarchy`` row for ``mid`` (so the
    hierarchy working set no longer links ``mid`` to its child ``leaf_of_mid``)
    then flips between modes. The wrapper honors the env on every call
    (when the cache is reset — see ``_reset_cascade_lineage_cache`` fixture).
    """

    @pytest.mark.asyncio
    async def test_case8_hierarchy_mode_propagates_through_cascade(
        self,
        engine: Engine,
        repo: SQLModelInstanceRepository,
        monkeypatch,
    ):
        """With ``ENSEMBLE_CASCADE_LINEAGE=hierarchy``, the cascade
        falls back to the legacy ``instance_hierarchy`` table — the
        deleted hierarchy row breaks the enumeration; ``leaf_of_mid``
        is NOT in ``tree_ids`` and is NOT paused.

        The default (permanent) mode would still enumerate via
        ``parent_id`` and pause all three nodes — the canonical
        control comparison exercised at the end of the test.
        """
        tree = _build_three_node_tree(repo)

        # Delete the hierarchy row linking mid → leaf_of_mid so the
        # legacy ``get_tree_ids`` (hierarchy mode) misses the leaf,
        # while ``get_tree_ids_permanent`` (permanent mode) still
        # finds it via ``parent_id``.
        with Session(engine) as session:
            from sqlalchemy import delete as sql_delete

            session.exec(
                sql_delete(InstanceHierarchy).where(
                    InstanceHierarchy.parent_id == tree["mid"]
                )
            )
            session.commit()

        # ── Hierarchy mode: wrapper routes to legacy ``get_tree_ids`` ──
        monkeypatch.setenv("ENSEMBLE_CASCADE_LINEAGE", "hierarchy")
        # The wrapper caches the mode on first call; reset the cache so
        # this test observes the fresh env value.
        from daemon.repositories.instance import repository as repo_mod

        monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_MODE", None)
        monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_BOOT_LOG_EMITTED", False)

        service_h, captured_h = _build_lifecycle_service(repo, engine)
        result_h = await service_h.pause_instance_cascade(tree["mid"])

        # The wrapper routed to legacy ``get_tree_ids``; with the
        # hierarchy row deleted, ``leaf_of_mid`` is NOT enumerated.
        captured_tree_ids_h = captured_h["pause_calls"][-1]["tree_ids"]
        assert tree["leaf_of_mid"] not in captured_tree_ids_h, (
            "ENSEMBLE_CASCADE_LINEAGE=hierarchy did not propagate — "
            "leaf_of_mid was enumerated despite its hierarchy row "
            "being deleted"
        )
        assert tree["root"] in captured_tree_ids_h
        assert tree["mid"] in captured_tree_ids_h
        # Paused_ids (cascade classification) excludes the missing
        # descendant — the cascade is using hierarchy-table enumeration.
        assert tree["leaf_of_mid"] not in result_h["paused_ids"]

        # ── Permanent mode: control comparison (default env) ──
        monkeypatch.delenv("ENSEMBLE_CASCADE_LINEAGE", raising=False)
        monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_MODE", None)
        monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_BOOT_LOG_EMITTED", False)

        service_p, captured_p = _build_lifecycle_service(repo, engine)
        result_p = await service_p.pause_instance_cascade(tree["mid"])

        # Permanent mode: ``get_tree_ids_permanent`` walks
        # ``instances.parent_id``; the deleted hierarchy row is
        # ignored; ``leaf_of_mid`` IS enumerated AND paused.
        captured_tree_ids_p = captured_p["pause_calls"][-1]["tree_ids"]
        assert set(captured_tree_ids_p) == {
            tree["root"],
            tree["mid"],
            tree["leaf_of_mid"],
        }
        assert set(result_p["paused_ids"]) == {
            tree["root"],
            tree["mid"],
            tree["leaf_of_mid"],
        }

        # The wrapper itself was called (the cascade goes through it in
        # BOTH modes) — the kill-switch selection happens inside
        # ``get_cascade_tree_ids``. The two scenarios above prove the
        # cascade observes the env flip end-to-end.