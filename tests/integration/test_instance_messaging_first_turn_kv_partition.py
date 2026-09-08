"""Integration pin for DEFECT 2 — child tree-root KV partition contract
observed through the REAL ``_process_message_with_tracking`` service.

This is pin (a) of the kv-ambient-awareness-fix C1′ VERIFY-AND-PIN
commit. The fix is already landed at base via commit ``80bb61dd``;
this file pins the **observable behavior** through the REAL
messaging service against REAL repositories on a REAL file-backed
SQLite database:

* A child instance's first-turn persistent context block is built
  from the tree-root KV partition (the parent's row), not from the
  child's own empty partition.
* The serialized block content carries the parent's KV payload.
* Exactly ONE project block is emitted — no duplicate from a
  mis-partitioned second read.

The original three landed tests
(``tests/services/test_instance_messaging_parent_resolution.py``)
verify the ``parent_id=`` keyword capture under MOCKED
``assemble_context_messages`` — the fix-shape thread-through is
proven, but the block CONTENT is never observed because the mock
short-circuits the real orchestrator. This pin closes that gap.

DB recipe (BLUEPRINT §3):
* file-backed SQLite at ``tmp_path``
* ``NullPool`` (NOT ``StaticPool`` — that trips the QUARANTINE
  write-corruption pattern)
* ``PRAGMA journal_mode=WAL`` + ``busy_timeout=10000``
* FK enforcement ON

Harness:
* REAL ``SQLModelInstanceRepository`` (read by ``_proj_row = ...get``)
* REAL ``SharedMetaKVRepository`` (read by ``_fetch_kv_metadata``)
* REAL ``SQLModelProjectRepository`` (read by ``_fetch_project_payload``)
* Stubbed graph (``astream`` captures ``graph_input`` then exits)
* Stubbed registry / live hub / skill injection

The contract under pin: ``graph_input['messages']`` MUST contain a
project-context ``HumanMessage`` whose serialized ``content`` is
the parent's tree-root KV payload rendered as a fenced JSON block,
with no second, own-partition duplicate.

See ``.agents/shared/planning/kv-ambient-awareness-fix/phase2-plan.md``
(Test Strategy §1) for the original plan and decisions.md D12 for
the re-adjudication that turned the regression proof into this
pin.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds
# the full schema (matches the integration recipe; without these
# imports the Instance / Project / SharedMetaKV tables are absent
# and the repositories raise ``sqlalchemy.exc.OperationalError``).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.shared_meta_kv.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.repositories.shared_meta_kv.repository import SharedMetaKVRepository
from daemon.services.instance_messaging import InstanceMessagingService


# Sentinel value — a recognizable string the assertion can grep for
# in the rendered project block. The plan calls this the parent's
# ``council_manifest`` value; the exact key name is not load-bearing
# (any value under the tree-root partition proves the read), but we
# stick to the plan's example for reviewer familiarity.
SENTINEL_KEY = "council_manifest"
SENTINEL_VALUE = {"members": ["alpha", "beta", "gamma"], "chair": "alpha"}


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ──────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    BLUEPRINT §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON is the
    FORBIDDEN-PATTERN antidote for the QUARANTINE.md StaticPool +
    WriteGuardSession dependency_bus row.
    """
    db_path = tmp_path / "kv_partition_pin.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ─── Seed helpers (REAL rows; mirrors test_governor_recursion_acceptance_walk) ─


PARENT_INSTANCE_ID = "iid-parent-root-aaaa"
CHILD_INSTANCE_ID = "iid-child-of-parent-aaaa"
TREE_ROOT_ID = PARENT_INSTANCE_ID  # parent is its own tree root
PROJECT_ID = "proj-pin-kv-partition"


def _seed_project(engine: Engine) -> None:
    """Insert the active project row the orchestrator's
    ``_fetch_project_payload`` will read."""
    now_iso = "2026-09-08T00:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Project(
                project_id=PROJECT_ID,
                name="kv-partition-pin",
                project_type="software",
                status=ProjectStatus.ACTIVE.value,
                description="C1' pin project for child tree-root KV partition",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_parent(engine: Engine) -> None:
    """Insert the parent instance row (tree root; parent_id=None)."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=PARENT_INSTANCE_ID,
                agent_id="leader",
                agent_dir="./agents/leader",
                parent_id=None,  # tree root
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                instance_metadata={
                    "project_id": PROJECT_ID,
                    "project_injected": False,
                    "shared_context_injected": False,
                },
            )
        )
        session.commit()


def _seed_child(engine: Engine) -> None:
    """Insert the child instance row (parent_id=parent)."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=CHILD_INSTANCE_ID,
                agent_id="explorer",
                agent_dir="./agents/explorer",
                parent_id=PARENT_INSTANCE_ID,  # the chain the fix relies on
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                instance_metadata={
                    "project_id": PROJECT_ID,
                    "project_injected": False,
                    "shared_context_injected": False,
                },
            )
        )
        session.commit()


# ─── Harness: manager mock with REAL repos ────────────────────────────────────


@asynccontextmanager
async def _null_semaphore():
    yield


class _CapturingGraph:
    """LangGraph mock whose ``astream`` captures the ``graph_input``
    dict handed to it, then ends iteration so the surrounding
    ``async for`` in ``_process_message_with_tracking`` exits
    cleanly.

    The captured ``graph_input['messages']`` is what the test
    inspects — those are the messages LangGraph's ``add_messages``
    reducer would checkpoint (the persistent block + the user
    message). The persistent block is the pin target.
    """

    def __init__(self) -> None:
        self.captured: dict = {}

    async def astream(self, graph_input=None, *args, **kwargs):
        if graph_input is not None:
            self.captured["graph_input"] = graph_input
        return
        yield  # pragma: no cover

    async def aget_state(self, *args, **kwargs):
        # D1 seam heal calls ``graph.aget_state(config)`` to inspect
        # the checkpoint tail; a clean AsyncMock returning None
        # short-circuits the heal (no checkpoint in this pin).
        return None


def _build_manager_mock(engine: Engine):
    """Assemble the manager mock with REAL repositories.

    REAL components (the contract under pin):
    * ``_instance_repository`` → ``SQLModelInstanceRepository``
      (so ``_proj_row.parent_id`` reads from a real row).
    * ``_shared_meta_kv_repo`` → ``SharedMetaKVRepository``
      (so ``_fetch_kv_metadata`` reads the tree-root partition).
    * ``_project_repository`` → ``SQLModelProjectRepository``
      (so ``_fetch_project_payload`` reads the project row).

    Stubbed (external / out-of-scope for this pin):
    * Graph (capturing only).
    * Live hub (SSE sink).
    * Skill injection (None — keeps the test focused on KV partition).
    * Queue repo / graph_tasks (unused on first-turn path).
    * Registry stub (avoids touching the agent registry).
    """
    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()

    # REAL repos — the contract under pin.
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._project_repository = SQLModelProjectRepository(engine)
    manager._shared_meta_kv_repo = SharedMetaKVRepository(engine)

    # No-op / sink stubs (externals).
    manager._instance_repository.set_metadata = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()
    manager._skill_injection_service = None
    manager.message_metadata_repo = None
    manager.clear_injection = MagicMock(return_value=None)
    manager.requeue_injections = MagicMock(return_value=None)
    manager._emitted_message_content = {}
    return manager


def _build_service(manager) -> InstanceMessagingService:
    """Wrap the manager in the REAL ``InstanceMessagingService`` and
    stub the two checkpoint helpers so the body of
    ``_process_message_with_tracking`` takes the first-attempt /
    no-compaction branch.

    Both helpers are infrastructure concerns, NOT the contract under
    pin; the test targets the persistent-context assembly path.
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


def _captured_persistent_messages(graph: _CapturingGraph) -> list:
    """Return the persistent-context ``HumanMessage`` instances the
    messaging path prepended to ``graph_input``.

    Filters out ``RemoveMessage`` sentinels (the auto-load REPLACE
    sweep; not active in this pin) and the trailing user message
    (the last element is the user message — see ``_build_graph_input``
    ordering: ``[persistent..., user]``).
    """
    gi = graph.captured.get("graph_input") or {}
    msgs = gi.get("messages") or []
    # Drop sentinel RemoveMessages; the pin does not exercise the
    # auto-load sweep.
    from langchain_core.messages import RemoveMessage

    real_msgs = [m for m in msgs if not isinstance(m, RemoveMessage)]
    return real_msgs


# ─── The pin ──────────────────────────────────────────────────────────────────


class TestChildFirstTurnKVPartition:
    """Pin: a freshly-spawned child's first-turn persistent context
    block reads from the parent's tree-root KV partition.

    The landed fix (``80bb61dd``) threads ``_proj_row.parent_id``
    from the permanent ``instances`` row into
    ``assemble_context_messages(parent_id=...)``. The orchestrator's
    ``_resolve_tree_root_id`` then walks the ancestor chain and
    computes ``context_key`` = tree-root id. This pin seeds the
    chain via REAL rows, sets a sentinel on the tree-root partition
    via the REAL ``SharedMetaKVRepository``, and asserts the
    rendered block carries the sentinel — proving the read
    happened at the right partition, not the child's own.
    """

    async def test_child_first_turn_kv_partition_real_service(
        self, engine: Engine
    ):
        """End-to-end: child first-turn persistent block is built
        from the tree-root partition the parent wrote to.

        Steps:
        1. Seed a real project + parent (tree root) + child instance
           rows in the same file-backed SQLite database.
        2. Set the sentinel KV on the tree-root partition via the
           REAL ``SharedMetaKVRepository.set_many``.
        3. Invoke the child's first turn via the REAL
           ``_process_message_with_tracking`` — no
           ``assemble_context_messages`` patching.
        4. Read the captured ``graph_input['messages']``.

        Assertions:
        * A project-context ``HumanMessage`` is present.
        * Its serialized ``content`` contains the sentinel value
          (the tree-root partition was actually READ — not the
          child's own empty partition).
        * Exactly ONE project block — no second, own-partition
          duplicate (the fix's anti-double-inject contract).
        """
        _seed_project(engine)
        _seed_parent(engine)
        _seed_child(engine)

        # Set the sentinel on the TREE-ROOT partition (the parent's id)
        # via the REAL repository — this proves the read path is
        # actually hitting the right ``context_key`` (which is
        # ``tree_root_id = parent.instance_id`` for this chain).
        kv_repo = SharedMetaKVRepository(engine)
        kv_repo.set_many(
            TREE_ROOT_ID,
            {SENTINEL_KEY: SENTINEL_VALUE},
        )

        # Sanity guard: the sentinel must NOT exist on the child's
        # own partition (the mispartition shape would find it there
        # if the fix were reverted). Reading the child's own
        # partition via the same repo proves the partitions are
        # distinct and the sentinel only lives under the tree-root.
        assert kv_repo.get_all_as_dict(CHILD_INSTANCE_ID) == {}
        assert kv_repo.get_all_as_dict(TREE_ROOT_ID) == {
            SENTINEL_KEY: SENTINEL_VALUE
        }

        manager = _build_manager_mock(engine)
        graph = _CapturingGraph()
        manager.get_instance = AsyncMock(return_value=graph)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(
                    context_injection_mode="human_messages"
                )
            )
            mock_get_registry.return_value = registry

            svc = _build_service(manager)

            await svc._process_message_with_tracking(
                instance_id=CHILD_INSTANCE_ID,
                message="hello",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # The graph captured the ``graph_input`` dict the messaging
        # path would hand to LangGraph. The persistent block + user
        # message live under ``graph_input['messages']``.
        assert graph.captured, (
            "graph.astream was never invoked — the messaging path "
            "did not reach the graph-input build step; the harness "
            "must wire graph.astream to capture (see _CapturingGraph)."
        )

        persistent_msgs = _captured_persistent_messages(graph)
        # At least one persistent message (the project block) plus
        # the user message at the tail.
        assert len(persistent_msgs) >= 2, (
            f"expected at least the project block + user message; "
            f"got {len(persistent_msgs)} message(s): "
            f"{[type(m).__name__ for m in persistent_msgs]}"
        )

        # ── Assertion 1: there is exactly ONE project block ────────
        # (the contract: no second, own-partition duplicate).
        from daemon.services.context_messages import CONTEXT_KIND_PROJECT

        project_blocks = [
            m
            for m in persistent_msgs
            if (getattr(m, "additional_kwargs", None) or {}).get(
                "context_kind"
            )
            == CONTEXT_KIND_PROJECT
        ]
        assert len(project_blocks) == 1, (
            f"expected exactly ONE project block (no "
            f"own-partition duplicate); got {len(project_blocks)}. "
            f"A second block would indicate the fix regressed and "
            f"the child is reading its own (empty) partition in "
            f"addition to the tree-root one."
        )

        project_msg = project_blocks[0]

        # ── Assertion 2: the project block carries the SENTINEL ────
        # The sentinel value is json.dumps'd under the
        # ``## Shared Context Metadata KV`` subsection by
        # ``_format_kv_metadata_section`` (see context_messages.py).
        # ``json.dumps(..., indent=2)`` pretty-prints multi-line, so
        # we assert on the key value strings (which serialize
        # unambiguously) rather than the full dict shape — any
        # substring match against the sentinel's distinctive
        # values proves the tree-root partition was read.
        serialized_content = (
            project_msg.content
            if isinstance(project_msg.content, str)
            else str(project_msg.content)
        )
        # The "chair" value is unique to the sentinel and
        # json.dumps'd verbatim — proves the actual KV payload
        # (not a coincidental substring) landed in the rendered
        # block.
        assert '"chair": "alpha"' in serialized_content, (
            f"project block must carry the sentinel KV payload "
            f"(rendered from the tree-root partition) — the child's "
            f"first-turn persistent context was built from the "
            f"wrong partition (child's own empty). "
            f"Got content (truncated): {serialized_content[:500]!r}"
        )
        assert SENTINEL_KEY in serialized_content, (
            f"project block must reference the sentinel meta_key "
            f"{SENTINEL_KEY!r}; got content (truncated): "
            f"{serialized_content[:500]!r}"
        )
        # The full list of council members round-trips through the
        # subsection (a stronger proof than the single "chair"
        # value: any partial truncation of the KV payload would
        # drop one of the three members).
        for member in ("alpha", "beta", "gamma"):
            assert f'"{member}"' in serialized_content, (
                f"sentinel member {member!r} missing from the "
                f"rendered project block — partition read returned "
                f"partial / wrong content. Got content (truncated): "
                f"{serialized_content[:500]!r}"
            )

        # ── Observation for OQ2 (system-default edge case) ────────
        # The system-default project is per-project, NOT
        # per-partition. This pin's project (id ``PROJECT_ID``) is
        # NOT the system default, so ``_fetch_project_payload``
        # follows the non-system-default branch and the KV
        # subsection is rendered into the block. If the project
        # WERE the system default, ``context_messages.py:1409-1429``
        # would short-circuit to the ``build_project_scope_guide_message``
        # path and the KV would NOT appear — that is by design
        # (system-default scope guide, no KV), not a regression.
        # This pin's harness therefore exercises the non-system-
        # default branch (the case the fix targets — a child of a
        # caller that has populated its tree-root KV).
        # The system-default edge case is exercised by C2 (phase3
        # suppression removal) and out of C1′ scope (D12).
