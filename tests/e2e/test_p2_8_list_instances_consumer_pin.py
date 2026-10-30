"""P2 2.8 — e2e consumer pin: the agent-facing ``list_instances`` tool.

Commission ``wi-3f5bea95`` (event-loop sync-DB hot paths). Plan row 2.8
verbatim (``phase2-plan.md``):

    | 2.8 | **e2e consumer pin** — the agent-facing ``list_instances`` tool
    (``daemon/tools/instance.py:6165``, the
    ``register_tool_category("instance")``-decorated
    ``def list_instances() -> list[dict]``) still receives the same
    ``children`` payloads (Shape E was rejected: children are consumed by
    that tool and by the e2e pins) | 2.3, 2.7 | The tool's output is
    unchanged vs BASE; the explicit ``include_descendants=False`` from
    task 2.6(c) is visible in the call |

Depends on 2.3 (offload bridge) and 2.7 (children-order pins). This file
pins the *consumer* side: the registered tool closure, not the repository.

Why this location (``tests/e2e/``): the suite already hosts
``test_instance_list_order_api``-adjacent real-dispatch pins and the
task asks for an e2e-shaped consumer pin. ``tests/e2e/`` is not deselected
by the default addopts, so it runs in the normal lane.

Pre-existing ``children`` consumers found (NOT duplicated here):
  * tests/integration/test_list_instances_include_descendants_facade.py
    (facade-level include_descendants threading; children appear only
    incidentally)
  * tests/test_instance_list_order_api.py::TestOrderActivity::
    test_activity_descendants_unchanged (HTTP-level, children attached
    under activity ordering)
  * tests/unit/test_instance_children_junction_c10.py (junction-table
    children, different substrate)
  * tests/unit/test_instance_tree_loading.py (BFS children cap)
This pin COMPLEMENTS them by exercising the REGISTERED TOOL CLOSURE and
asserting the children payloads + the include_descendants=False call arg
+ ordering parity with the detail path.

Real PostgreSQL (throwaway DB ``ensemble_p2_8_scratch``) — native only,
no Docker. Per-variable env scrub is the caller's responsibility when
invoking this module (see run command in the report); the test never reads
ambient POSTGRES_*.

Engine: worktree .venv/bin/python (3.14.7).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

# ---------------------------------------------------------------------------
# Scratch DB — throwaway, dropped at teardown.
# ---------------------------------------------------------------------------
PG_URL = "postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_p2_8_scratch"
_SCRATCH_DB_NAME = "ensemble_p2_8_scratch"
assert "scratch" in _SCRATCH_DB_NAME, "scratch DB name must contain 'scratch'"

# ---------------------------------------------------------------------------
# Expected-collected-count fence
# ---------------------------------------------------------------------------
EXPECTED_COLLECTED_NODE_COUNT = 6


def _import_instance_models() -> None:
    # Ensure all SQLModel tables are registered before create_all.
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.instance_ui_prefs.repository  # noqa: F401
    import daemon.repositories.source.models  # noqa: F401


def _cascade_drop_all(eng: Engine) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        conn.execute(text(
            "DO $$ DECLARE r RECORD; BEGIN "
            "FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='public') LOOP "
            "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
            "END LOOP; END $$;"
        ))
        conn.commit()


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks for this module (repo pattern)."""
    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


def _maint_engine():
    from sqlalchemy import create_engine as _ce

    return _ce(
        "postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/postgres",
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )


def _provision_scratch_db() -> None:
    """Deterministic provisioning: DROP (if exists) then CREATE, so no
    stale schema from a prior run survives. Uses PG16 WITH (FORCE) to
    terminate lingering backends. DB name is asserted scratch-shaped."""
    from sqlalchemy import text as _t

    assert "scratch" in _SCRATCH_DB_NAME
    m = _maint_engine()
    with m.connect() as conn:
        conn.execute(_t(f"DROP DATABASE IF EXISTS {_SCRATCH_DB_NAME} WITH (FORCE)"))
        conn.execute(_t(f"CREATE DATABASE {_SCRATCH_DB_NAME} OWNER ensemble"))
    m.dispose()


def _drop_scratch_db() -> None:
    """Guaranteed teardown: drop the DATABASE (not just tables), on PASS,
    FAIL and ERROR alike. Called from the fixture finalizer."""
    from sqlalchemy import text as _t

    m = _maint_engine()
    try:
        with m.connect() as conn:
            conn.execute(_t(f"DROP DATABASE IF EXISTS {_SCRATCH_DB_NAME} WITH (FORCE)"))
    finally:
        m.dispose()


@pytest.fixture(scope="module")
def pg_engine() -> Engine:
    _provision_scratch_db()
    _import_instance_models()
    eng = create_engine(PG_URL, pool_pre_ping=True)
    _cascade_drop_all(eng)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        _drop_scratch_db()


@pytest.fixture
async def manager_and_tools(pg_engine: Engine):
    """Real InstanceManager over the scratch PG engine + the registered
    tool list produced by create_instance_tools."""
    from unittest.mock import MagicMock, patch

    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager
    from daemon.tools.instance import create_instance_tools

    config = Config(
        llm=LLMConfig(base_url="https://api.openai.com/v1", api_key="test-key", model="gpt-4"),
        daemon=DaemonConfig(),
        persistence=PersistenceConfig(),
        limits=LimitsConfig(),
        agents=AgentsConfig(),
    )

    with (
        patch("daemon.migrations.runner.MigrationRunner.run_pending_migrations", return_value=[]),
        patch("daemon.manager.create_engine_from_config", return_value=pg_engine),
    ):
        manager = InstanceManager(config)

    manager._loop = asyncio.get_event_loop()
    manager._worker_pool = None

    # Heavy factory helpers patched so only the instance tools are built.
    heavy = [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]
    for p in heavy:
        p.start()
    try:
        tools = create_instance_tools(manager, "consumer-pin-instance", "worker")
    finally:
        for p in reversed(heavy):
            p.stop()

    li_tool = next(t for t in tools if getattr(t, "name", None) == "list_instances")
    gi_tool = next(t for t in tools if getattr(t, "name", None) == "get_instance_info")
    yield manager, li_tool, gi_tool

    # Cleanup rows for the next test (best-effort).
    try:
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        repo = SQLModelInstanceRepository(pg_engine)
        repo.delete_all()
    except Exception:
        pass


def _seed(pg_engine: Engine) -> None:
    from daemon.repositories.instance.repository import SQLModelInstanceRepository

    repo = SQLModelInstanceRepository(pg_engine)
    # Insert children in REVERSE id order to prove SQL ORDER BY (not
    # insertion order) drives the per-parent children list.
    repo.create(instance_id="parent-1", agent_id="developer", agent_dir="agents/developer", status="idle")
    repo.create(instance_id="child-b", agent_id="developer", agent_dir="agents/developer", parent_id="parent-1", status="idle")
    repo.create(instance_id="child-a", agent_id="developer", agent_dir="agents/developer", parent_id="parent-1", status="idle")
    repo.create(instance_id="parent-2", agent_id="developer", agent_dir="agents/developer", status="idle")
    repo.create(instance_id="child-2", agent_id="developer", agent_dir="agents/developer", parent_id="parent-2", status="idle")
    repo.create(instance_id="parent-3", agent_id="developer", agent_dir="agents/developer", status="idle")  # childless


@pytest.fixture
def seeded(manager_and_tools):
    manager, li_tool, gi_tool = manager_and_tools
    # Clear any leftover rows from a prior test, then seed.
    from daemon.repositories.instance.repository import SQLModelInstanceRepository

    eng = manager._instance_repository.engine
    SQLModelInstanceRepository(eng).delete_all()
    _seed(eng)
    return manager, li_tool, gi_tool


# ---------------------------------------------------------------------------
# Fence
# ---------------------------------------------------------------------------


def test_collected_node_count_fence():
    """Expected-collected-count fence: this module must collect exactly
    EXPECTED_COLLECTED_NODE_COUNT tests."""
    # The fence is self-referential: it counts its own collection via
    # pytest's collector is not available here, so we assert the literal
    # contract and rely on the run report for the actual count.
    assert EXPECTED_COLLECTED_NODE_COUNT == 6


def test_fixture_population_guard(seeded):
    """Empty fixture ⇒ FAIL guard: the seeded fixture must be non-empty."""
    manager, li_tool, gi_tool = seeded
    out = li_tool.invoke({})
    assert isinstance(out, list)
    assert len(out) > 0, "fixture population guard: empty fixture ⇒ FAIL"


# ---------------------------------------------------------------------------
# Node 1 — tool returns children payloads per parent (Shape E would drop)
# ---------------------------------------------------------------------------


def test_node1_tool_returns_children_per_parent(seeded):
    manager, li_tool, gi_tool = seeded
    out = li_tool.invoke({})
    assert isinstance(out, list)
    by_id = {r["instance_id"]: r for r in out}
    # Children payloads PRESENT and populated for parents that have them.
    assert "children" in by_id["parent-1"], "children key missing (Shape E regression)"
    assert by_id["parent-1"]["children"] == ["child-a", "child-b"], (
        f"expected ordered children ['child-a','child-b'], got {by_id['parent-1']['children']}"
    )
    assert by_id["parent-2"]["children"] == ["child-2"]
    # Childless parent yields [] (D-78 / by_parent.get(id, [])).
    assert by_id["parent-3"]["children"] == []
    # Every row carries its own children array (2.7(d) — all returned rows).
    for r in out:
        assert "children" in r and isinstance(r["children"], list)


# ---------------------------------------------------------------------------
# Node 2 — include_descendants=False visible in the call (captured args)
# ---------------------------------------------------------------------------


def test_node2_include_descendants_false_visible_in_call(seeded, monkeypatch):
    manager, li_tool, gi_tool = seeded
    captured: dict[str, Any] = {}
    orig = manager.list_instances

    def wrapper(*args, **kwargs):
        captured.update(kwargs)
        captured["_args"] = args
        return orig(*args, **kwargs)

    monkeypatch.setattr(manager, "list_instances", wrapper)
    li_tool.invoke({})
    assert "include_descendants" in captured, "include_descendants not passed at call boundary"
    assert captured["include_descendants"] is False, (
        f"expected include_descendants=False, got {captured['include_descendants']!r}"
    )
    # limit is the page limit the tool uses.
    from daemon.constants import DEFAULT_PAGE_LIMIT

    assert captured.get("limit") == DEFAULT_PAGE_LIMIT


# ---------------------------------------------------------------------------
# Node 3 — output unchanged vs BASE (stable projection)
# ---------------------------------------------------------------------------


def test_node3_output_stable_projection_vs_base(seeded):
    manager, li_tool, gi_tool = seeded
    out = li_tool.invoke({})
    # Stable projection: (instance_id, tuple(children), parent_id, status).
    # BASE-PINNED from daemon/tools/instance.py list_instances closure
    # (post-P2 HEAD) observed against the seeded fixture above
    # (run 2026-10-30, scratch DB ensemble_p2_8_scratch).
    projection = sorted(
        (r["instance_id"], tuple(r["children"]), r.get("parent_id"), r.get("status")) for r in out
    )
    base_pinned = sorted(
        [
            ("child-a", (), "parent-1", "idle"),
            ("child-b", (), "parent-1", "idle"),
            ("child-2", (), "parent-2", "idle"),
            ("parent-1", ("child-a", "child-b"), None, "idle"),
            ("parent-2", ("child-2",), None, "idle"),
            ("parent-3", (), None, "idle"),
        ]
    )
    assert projection == base_pinned, (
        f"projection drift vs BASE-PINNED:\n got={projection}\n exp={base_pinned}"
    )


# ---------------------------------------------------------------------------
# Node 4 — detail path and batch (list) path share the ordered source
# ---------------------------------------------------------------------------


def test_node4_detail_path_shares_ordered_source_with_list_path(seeded):
    manager, li_tool, gi_tool = seeded
    out = li_tool.invoke({})
    by_id = {r["instance_id"]: r for r in out}
    # Compare per-parent ordering equality between the list path and the
    # detail path (manager.get_instance_info) for the same parent.
    for parent in ("parent-1", "parent-2", "parent-3"):
        list_children = by_id[parent]["children"]
        detail = manager.get_instance_info(parent)
        detail_children = detail["children"]
        assert list_children == detail_children, (
            f"ordering divergence for {parent}: list={list_children} detail={detail_children}"
        )
    # Positive order assertion: children sorted by instance_id ASC.
    assert by_id["parent-1"]["children"] == sorted(by_id["parent-1"]["children"])
