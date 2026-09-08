"""Integration pin (W6) — ambient KV renders on the system-default
project path through the REAL config stack + REAL repository.

kv-ambient-awareness-fix C2, phase3-plan Test Strategy §3 (W6): the
flag-ON path must be exercised with NO test-side patching of the
resolver — REAL ``load_config`` (env → pure resolver → REAL pydantic
``ContextMessagesConfig`` field → install), REAL
``_resolve_kv_ambient_system_default_enabled`` read, REAL
``InstanceManager``-shaped manager with REAL repositories on a REAL
file-backed SQLite database. A MagicMock KV repo would mask the exact
fetch/render contract this phase exists to prove.

What is pinned:

* A first turn on an instance whose project row is the system-default
  project (detected by NAME — ``__system_default__`` — the prod-real
  shape, no constants patching) emits the standalone
  ``[SYSTEM CONTEXT: Shared Meta KV]`` block AFTER the scope guide.
* The block content is the seeded tree-root partition payload — read
  via the REAL ``SharedMetaKVRepository`` at the resolved tree-root
  ``context_key`` (the instance IS its own root here).
* The block id is the C0 stable id ``kv:{context_key}`` (FULL
  resolved tree-root key — decisions.md D3).
* A decoy row seeded under a DIFFERENT partition key must NOT leak
  into the block — proving the partition binding, not just "any row
  renders".

DB recipe (BLUEPRINT §3, mirrors the C1′ harness):
file-backed SQLite at ``tmp_path`` + ``NullPool`` (NEVER StaticPool —
the QUARANTINE write-corruption pattern) + ``PRAGMA
journal_mode=WAL`` + ``busy_timeout=10000`` + FK ON.

Run only this file::

    pytest tests/integration/test_kv_ambient_real_service_flag_on_through_assembler.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full schema (same recipe as the C1′ pin — without these imports the
# Instance / Project / SharedMetaKV tables are absent and the
# repositories raise ``sqlalchemy.exc.OperationalError``).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.shared_meta_kv.models  # noqa: F401

from daemon.config import (
    _reset_kv_ambient_for_tests,
    _resolve_kv_ambient_system_default_enabled,
    load_config,
)
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.repositories.shared_meta_kv.repository import SharedMetaKVRepository
from daemon.services.context_messages import (
    _stable_id_for,
    assemble_context_messages,
)

TREE_ROOT_ID = "iid-kv-ambient-root-0001"
PROJECT_ID = "proj-kv-ambient-default"

SENTINEL_KEY = "council_manifest"
SENTINEL_VALUE = "chair=alpha;members=alpha,beta,gamma"
DECOY_VALUE = "wrong-partition-decoy"

ENV_NAME = "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED"


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ─────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    NOT StaticPool/:memory: — the QUARANTINE.md StaticPool
    write-corruption pattern. Per-checkout connections + WAL mirror
    production's concurrency shape.
    """
    db_path = tmp_path / "kv_ambient_flag_on.db"
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


# ─── Seed helpers (REAL rows) ────────────────────────────────────────────────


def _seed_default_project(engine: Engine) -> None:
    """Insert the system-default project row — detected by NAME via the
    assembler's name-based check (``project.name ==
    SYSTEM_DEFAULT_PROJECT_NAME``), the prod-real shape."""
    now_iso = "2026-09-08T00:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Project(
                project_id=PROJECT_ID,
                name=SYSTEM_DEFAULT_PROJECT_NAME,
                project_type="software",
                status=ProjectStatus.ACTIVE.value,
                description="system-default project (kv-ambient C2 pin)",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_tree_root(engine: Engine) -> None:
    """Insert the tree-root instance row (parent_id=None → its own root)."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=TREE_ROOT_ID,
                agent_id="leader",
                agent_dir="./agents/leader",
                parent_id=None,
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


# ─── Harness ─────────────────────────────────────────────────────────────────


def _build_manager(engine: Engine):
    """Manager mock with REAL repositories.

    REAL (the contract under pin): ``_shared_meta_kv_repo`` (the
    tree-root partition read), ``_project_repository`` (the
    default-project row lookup + blueprint metadata probe).
    Stubbed: skill injection + blueprint matcher (external concerns
    for this pin; ``agent_meta`` below keeps them inert anyway).
    """
    from unittest.mock import MagicMock

    manager = MagicMock()
    manager._shared_meta_kv_repo = SharedMetaKVRepository(engine)
    manager._project_repository = SQLModelProjectRepository(engine)
    manager._blueprint_matcher = None
    return manager


def _make_agent_meta() -> SimpleNamespace:
    """Minimal REAL-shaped agent metadata: no RAG heuristic, no BM25
    skill search, blueprint opted-out — keeps the pin focused on the
    KV host while exercising the same first-turn assembly path."""
    return SimpleNamespace(
        context_injection=None,
        skill_injection=False,
        blueprint_inactive=True,
    )


@pytest.fixture
def flag_on_through_real_config(tmp_path, monkeypatch):
    """Drive the flag ON through the REAL config stack — zero patching.

    env ``=1`` → REAL ``load_config`` → REAL pure resolver
    (``_resolve_kv_ambient_from_sources``) → REAL pydantic
    ``ContextMessagesConfig`` field → REAL install + boot log → the
    REAL cached accessor the assembler consumes returns ``True``.
    """
    monkeypatch.setenv(ENV_NAME, "1")
    _reset_kv_ambient_for_tests()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'llm:\n'
        '  base_url: "https://api.openai.com/v1"\n'
        '  api_key: "test-key"\n'
        '  model: "gpt-4"\n'
        "\n"
        'persistence:\n'
        '  db_path: "./data/instances.db"\n'
    )
    cfg = load_config(config_path=str(config_file))
    # The REAL field resolved True through the REAL stack.
    assert cfg.context_messages.kv_ambient_system_default_enabled is True
    # The assembler's accessor (the same call the gate makes) is warm-True.
    assert _resolve_kv_ambient_system_default_enabled() is True
    yield
    _reset_kv_ambient_for_tests()


# ─── The pin ─────────────────────────────────────────────────────────────────


class TestKvAmbientRealServiceFlagOn:
    """Flag-ON default-project branch observed through REAL services."""

    async def _assemble(self, engine: Engine, manager) -> list:
        persistent, ephemeral = await assemble_context_messages(
            instance_id=TREE_ROOT_ID,
            user_query="please summarize the council manifest",
            project_id=PROJECT_ID,
            agent_meta=_make_agent_meta(),
            manager=manager,
            instance_repository=SQLModelInstanceRepository(engine),
            parent_id=None,  # the instance IS the tree root
        )
        return list(persistent) + list(ephemeral)

    async def test_flag_on_through_assembler_renders_seeded_tree_root_partition(
        self, engine: Engine, flag_on_through_real_config
    ) -> None:
        """End-to-end: REAL flag stack + REAL repos → KV block renders
        with the seeded tree-root row and the ``kv:{context_key}`` id."""
        _seed_default_project(engine)
        _seed_tree_root(engine)
        manager = _build_manager(engine)
        real_repo: SharedMetaKVRepository = manager._shared_meta_kv_repo

        # Seed the tree-root partition via the REAL write API, plus a
        # decoy row under a DIFFERENT partition key.
        real_repo.set_many(TREE_ROOT_ID, {SENTINEL_KEY: SENTINEL_VALUE})
        real_repo.set_many("iid-some-other-root-9999", {"decoy": DECOY_VALUE})

        # Sanity: the REAL repo really holds the row at the tree root.
        assert real_repo.get_all_as_dict(TREE_ROOT_ID) == {
            SENTINEL_KEY: SENTINEL_VALUE
        }

        result = await self._assemble(engine, manager)
        kinds = [m.additional_kwargs["context_kind"] for m in result]

        # Scope guide still renders (UX choice unchanged) …
        assert "project_scope_guide" in kinds
        # … and the ambient KV block renders beside it.
        assert "shared_meta_kv" in kinds, (
            "flag-ON default-project turn must emit the standalone "
            "Shared Meta KV block when the tree-root partition has rows"
        )
        scope_idx = kinds.index("project_scope_guide")
        kv_idx = kinds.index("shared_meta_kv")
        assert scope_idx < kv_idx, "scope guide must precede the KV block"

        kv_msg = next(
            m for m in result
            if m.additional_kwargs["context_kind"] == "shared_meta_kv"
        )
        # Block content carries the seeded tree-root row …
        assert SENTINEL_KEY in kv_msg.content
        assert SENTINEL_VALUE in kv_msg.content
        # … at the CORRECT tree-root partition (the decoy partition
        # under a different context_key must not leak into the block).
        assert DECOY_VALUE not in kv_msg.content
        # Stable id: kv:{context_key} — the FULL resolved tree-root key.
        assert kv_msg.id == _stable_id_for(
            "shared_meta_kv", context_key=TREE_ROOT_ID
        )
        assert kv_msg.id == f"kv:{TREE_ROOT_ID}"

    async def test_empty_partition_renders_scope_guide_only(
        self, engine: Engine, flag_on_through_real_config
    ) -> None:
        """REAL stack + REAL repo, partition empty → no empty host block."""
        _seed_default_project(engine)
        _seed_tree_root(engine)
        manager = _build_manager(engine)

        result = await self._assemble(engine, manager)
        kinds = [m.additional_kwargs["context_kind"] for m in result]
        assert "project_scope_guide" in kinds
        assert "shared_meta_kv" not in kinds, (
            "an empty tree-root partition must not produce an empty "
            "Shared Meta KV host block"
        )
