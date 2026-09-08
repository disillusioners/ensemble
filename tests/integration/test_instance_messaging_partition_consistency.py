"""Integration pin (c) — DEFECT 2: messaging-path vs tool-path
partition key consistency for the SAME spawned child.

The C1′ plan (Test Strategy §3) requires a single test that pins
the partition-key invariant: for ONE spawned child instance, the
``context_key`` that the **messaging path's** first-turn assembly
reads under MUST equal the ``context_key`` that the **tool path's**
explicit ``shared_meta_kv`` tool reads under.

Why this matters: a child instance can read its tree-root
partition via two routes — implicit, on first turn (the
``[SYSTEM CONTEXT: Related Project]`` block the agent receives as
context), and explicit, via the ``shared_meta_kv`` tool (e.g.
``set_kv=...`` to write KV under its tree root). If the two
routes resolve to different ``context_key``s, an agent's implicit
read sees one partition's KV and its explicit write lands under
another — a silent per-instance state divergence that no test
would catch until an operator noticed the discrepancy.

Both routes use the SAME primitive (``_instance_repository.
get_tree_root_id``), but the test exercises them
independently so a regression that breaks one without the other
(typo, refactor mistake) is loud here:

* **Messaging path**: ``_resolve_tree_root_id`` (context_messages.py
  :998-1039) — used by ``assemble_context_messages`` (the
  orchestrator invoked from the messaging path). The child walks
  ``parent_id`` upward to the tree root.
* **Tool path**: ``manager._instance_repository.get_tree_root_id``
  directly — used by the ``shared_meta_kv`` tool
  (``daemon/tools/shared_meta_kv_tools.py:113``).

The pin seeds the child via REAL instance rows in a REAL
file-backed SQLite database (BLUEPRINT §3 recipe — ``NullPool`` +
``PRAGMA journal_mode=WAL`` + ``busy_timeout=10000``, NOT the
FORBIDDEN ``StaticPool`` pattern). The two resolution paths are
then invoked with the same child id; both MUST resolve to the
tree-root id (``parent.instance_id`` here, since the parent is
its own tree root).

A bonus write-then-read round-trip exercises the practical
scenario end-to-end: a tool-call write to the tree-root partition
from the child lands under the same key the messaging path reads
from (so a future first-turn rebuild sees the value the agent
just wrote).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds
# the full schema (matches the integration recipe).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.shared_meta_kv.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.shared_meta_kv.repository import SharedMetaKVRepository
from daemon.services.context_messages import _resolve_tree_root_id


PARENT_INSTANCE_ID = "iid-parent-root-cons"
CHILD_INSTANCE_ID = "iid-child-cons"
GRANDCHILD_INSTANCE_ID = "iid-grandchild-cons"
PROJECT_ID = "proj-cons-pin"
TREE_ROOT_ID = PARENT_INSTANCE_ID


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ──────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    BLUEPRINT §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON. The
    FORBIDDEN-PATTERN antidote for the QUARANTINE.md StaticPool +
    WriteGuardSession dependency_bus row.
    """
    db_path = tmp_path / "kv_partition_consistency.db"
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


def _seed_chain(engine: Engine) -> None:
    """Seed parent → child → grandchild chain (3 rows)."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=PARENT_INSTANCE_ID,
                agent_id="leader",
                agent_dir="./agents/leader",
                parent_id=None,  # tree root
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                instance_metadata={"project_id": PROJECT_ID},
            )
        )
        session.add(
            Instance(
                instance_id=CHILD_INSTANCE_ID,
                agent_id="explorer",
                agent_dir="./agents/explorer",
                parent_id=PARENT_INSTANCE_ID,
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                instance_metadata={"project_id": PROJECT_ID},
            )
        )
        session.add(
            Instance(
                instance_id=GRANDCHILD_INSTANCE_ID,
                agent_id="worker",
                agent_dir="./agents/worker",
                parent_id=CHILD_INSTANCE_ID,
                status=InstanceStatus.RUNNING.value,
                project_id=PROJECT_ID,
                instance_metadata={"project_id": PROJECT_ID},
            )
        )
        session.commit()


# ─── The pin ──────────────────────────────────────────────────────────────────


class TestPartitionConsistency:
    """Pin the messaging-path vs tool-path partition-key consistency.

    Both routes MUST resolve to the SAME tree-root ``context_key``
    for a given child. The contract under pin is the property that
    makes "implicit ambient block read" and "explicit tool write"
    agree — i.e. an agent that writes via the ``shared_meta_kv``
    tool has the value visible on its next first-turn rebuild.
    """

    def test_partition_consistency_messaging_vs_tool(
        self, engine: Engine
    ):
        """End-to-end consistency pin: messaging-path and tool-path
        resolve to the same ``context_key`` for the same child,
        and a tool write under that key is readable on the next
        pass.

        Steps:
        1. Seed a 3-level chain (parent → child → grandchild) of
           REAL ``Instance`` rows. Parent is its own tree root.
        2. Resolve the messaging-path ``context_key`` for the child
           and grandchild via the REAL
           ``_resolve_tree_root_id`` helper (which the
           ``assemble_context_messages`` orchestrator invokes).
        3. Resolve the tool-path ``context_key`` for the same
           children via the REAL
           ``SQLModelInstanceRepository.get_tree_root_id`` (which
           the ``shared_meta_kv`` tool invokes).
        4. Assert both paths return the SAME tree-root id for
           each child (the property under pin).
        5. Bonus round-trip: write a sentinel KV via the REAL
           ``SharedMetaKVRepository.set_many`` under the
           tool-path ``context_key``; read it back via
           ``get_all_as_dict`` to prove the key is live.
        """
        _seed_chain(engine)

        # REAL repos — the contract under pin.
        instance_repo = SQLModelInstanceRepository(engine)
        kv_repo = SharedMetaKVRepository(engine)

        # ── Messaging path resolution ─────────────────────────────
        # ``_resolve_tree_root_id`` is what
        # ``assemble_context_messages`` calls. It takes the
        # ``parent_id`` the messaging path threads (here we feed
        # it the row's parent_id directly to model the post-fix
        # thread-through; the helper does not inspect the row).
        messaging_path_child = _resolve_tree_root_id(
            instance_id=CHILD_INSTANCE_ID,
            parent_id=PARENT_INSTANCE_ID,  # child's parent_id from the row
            instance_repository=instance_repo,
        )
        messaging_path_grandchild = _resolve_tree_root_id(
            instance_id=GRANDCHILD_INSTANCE_ID,
            parent_id=CHILD_INSTANCE_ID,  # grandchild's parent_id from the row
            instance_repository=instance_repo,
        )

        # ── Tool path resolution ──────────────────────────────────
        # ``manager._instance_repository.get_tree_root_id`` is
        # exactly what ``shared_meta_kv_tools.py:113`` calls.
        tool_path_child = instance_repo.get_tree_root_id(
            CHILD_INSTANCE_ID
        )
        tool_path_grandchild = instance_repo.get_tree_root_id(
            GRANDCHILD_INSTANCE_ID
        )

        # ── Assertion: both paths agree on the tree-root id ──────
        # The parent is its own tree root; both child and
        # grandchild MUST resolve to PARENT_INSTANCE_ID regardless
        # of which path did the walk.
        assert messaging_path_child == tool_path_child, (
            f"messaging-path resolution ({messaging_path_child!r}) "
            f"and tool-path resolution ({tool_path_child!r}) "
            f"disagree for the child — the partition-correctness "
            f"contract is broken; an implicit first-turn rebuild "
            f"would read a different partition than an explicit "
            f"``shared_meta_kv`` tool call."
        )
        assert messaging_path_grandchild == tool_path_grandchild, (
            f"messaging-path resolution "
            f"({messaging_path_grandchild!r}) and tool-path "
            f"resolution ({tool_path_grandchild!r}) disagree for "
            f"the grandchild — multi-level chains amplify any "
            f"walk divergence."
        )
        # Both paths must point at the tree root (the parent's id),
        # NOT at the child's own id (the mispartition shape).
        assert messaging_path_child == PARENT_INSTANCE_ID, (
            f"messaging-path resolution for the child must walk up "
            f"to the tree root (PARENT_INSTANCE_ID); got "
            f"{messaging_path_child!r} — the resolver regressed "
            f"to the child's own partition (the mispartition "
            f"defect)."
        )
        assert tool_path_child == PARENT_INSTANCE_ID, (
            f"tool-path resolution for the child must walk up to "
            f"the tree root (PARENT_INSTANCE_ID); got "
            f"{tool_path_child!r}."
        )
        assert messaging_path_grandchild == PARENT_INSTANCE_ID, (
            f"messaging-path resolution for the grandchild must "
            f"walk up to the tree root (PARENT_INSTANCE_ID), not "
            f"stop at the intermediate child; got "
            f"{messaging_path_grandchild!r} — multi-level walks "
            f"regressed."
        )
        assert tool_path_grandchild == PARENT_INSTANCE_ID, (
            f"tool-path resolution for the grandchild must walk "
            f"up to the tree root (PARENT_INSTANCE_ID), not stop "
            f"at the intermediate child; got "
            f"{tool_path_grandchild!r}."
        )

        # ── Bonus: write-then-read round-trip ─────────────────────
        # A tool-path write under the resolved context_key must be
        # observable on a subsequent tool-path read (the property
        # the consistency pin ultimately protects).
        sentinel_key = "consistency_sentinel"
        sentinel_value = {"round_trip": True, "level": 3}
        kv_repo.set_many(
            tool_path_child,
            {sentinel_key: sentinel_value},
        )
        snapshot = kv_repo.get_all_as_dict(tool_path_child)
        assert snapshot == {sentinel_key: sentinel_value}, (
            f"tool-path write under tool_path_child "
            f"({tool_path_child!r}) must be readable on the next "
            f"pass; got snapshot {snapshot!r} — the KV layer "
            f"disagrees with the resolution layer."
        )

        # And the child's OWN partition stays empty — the
        # write went to the tree root, not to the child's own
        # partition (the mispartition shape would put it there).
        own_partition = kv_repo.get_all_as_dict(CHILD_INSTANCE_ID)
        assert own_partition == {}, (
            f"child's own partition must stay empty — the tool-"
            f"path write landed under {tool_path_child!r} (the "
            f"tree root), not under the child. Got "
            f"{own_partition!r} — the partition was mis-targeted."
        )
