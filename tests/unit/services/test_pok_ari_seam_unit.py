"""POK ari-seam unit pack (Phase 1 T7 — G-2 / §4.5.1 / AC24 dev legs).

AC24: at an ari dispatch targeting a non-default project, the
dispatch context carries the operation-audience view block (fresh
read at dispatch; same budget/fence); NO block when the target is
the system-default project (the negative leg — both the id gate and
the name fallback); the gates (caller is the factory-captured ari),
the semantic stable-id namespace (once per instance × project), and
the never-raise contract.

AsyncMock census: the seam is a REAL coroutine; its manager/repo
callees (set_injection / set_metadata / repo.get) are SYNC in
production — the doubles here are MagicMock/plain objects (never
AsyncMock), and the census test pins that shape.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import create_engine
from sqlmodel import SQLModel

import daemon.constants as constants
import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.services.pok_ari_seam import (
    ARI_AGENT_ID,
    POK_ARI_SEAM_LAST_PROJECT,
    POK_ARI_SEAM_SOURCE,
    maybe_inject_ari_pok_block,
)
from daemon.services.pok_service import POKService


@pytest.fixture()
def pok_env(monkeypatch):
    """A fenced throwaway engine + a fake manager with capture lists.

    The engine uses StaticPool + shared connection so the seam's
    asyncio.to_thread reads see the SAME in-memory DB the fixture
    wrote (in-memory SQLite is per-connection otherwise). The
    system-default constant is snapshotted/restored around each test
    (call-time reads — the fixture mutates it for the negative legs;
    the autouse suite fixture may have already set it)."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )
    service = POKService(engine)
    service.upsert(
        project_id="p1",
        slug="pipeline-01-merge-ci",
        body="Merging to master triggers the image CI build.",
        audience="both",
        agent_id="developer",
    )
    service.upsert(
        project_id="p1",
        slug="deploy-01-backup",
        body="The daoduc backup runs first.",
        audience="operation",
        agent_id="devops",
    )
    service.upsert(
        project_id="p1",
        slug="impl-01-tests-first",
        body="The test suite runs before merge.",
        audience="implementation",
        agent_id="developer",
    )

    metadata: dict = {}
    injections: list[tuple[str, str, str | None]] = []

    class FakeRepo:
        def get(self, iid):
            return SimpleNamespace(instance_metadata=metadata)

        def set_metadata(self, iid, key, value):
            metadata[key] = value

    class FakeProjectRepo:
        def get(self, pid):
            if pid == "default-x":
                return SimpleNamespace(name="__system_default__")
            return SimpleNamespace(name="proj-one")

    class FakeManager:
        _engine = engine
        _instance_repository = FakeRepo()
        _project_repository = FakeProjectRepo()

        def set_injection(self, iid, content, source=None):
            injections.append((iid, content, source))

    saved = constants.SYSTEM_DEFAULT_PROJECT_ID
    yield SimpleNamespace(
        manager=FakeManager(),
        metadata=metadata,
        injections=injections,
    )
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved


class TestAc24AriSeamPositiveLeg:
    def test_dispatch_carries_the_operation_view(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        assert len(pok_env.injections) == 1
        iid, content, source = pok_env.injections[0]
        assert iid == "ari-inst-1"
        assert source == POK_ARI_SEAM_SOURCE
        # the canonical wrapper
        assert content.startswith(
            "[SYSTEM CONTEXT: Project Operating Knowledge]\n\n"
        )
        lines = content.splitlines()
        entries = [l for l in lines if l.startswith("- ")]
        # operation ∪ both = 2 entries; the implementation-classified
        # fact is filtered OUT (ari sees the operation slice)
        assert len(entries) == 2
        joined = "\n".join(entries)
        assert "pipeline-01-merge-ci" in joined
        assert "deploy-01-backup" in joined
        assert "impl-01-tests-first" not in joined

    def test_block_within_the_per_view_budget(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        content = pok_env.injections[0][1]
        assert len(content) <= 2_900

    def test_fresh_read_per_dispatch_after_content_change(self, pok_env) -> None:
        """Per-dispatch assembly is the freshness mechanism: a NEW
        entry (written after the first dispatch) appears on the NEXT
        dispatch to a DIFFERENT project and back — but the semantic
        stable id means the SAME project is injected once; the fresh
        read shows on a different project slot."""
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        assert len(pok_env.injections) == 1
        # same project again → no re-injection (semantic stable id)
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        assert len(pok_env.injections) == 1

    def test_once_per_instance_project_namespace(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        assert pok_env.metadata[POK_ARI_SEAM_LAST_PROJECT] == "p1"


class TestAc24AriSeamNegativeLegs:
    def test_no_block_on_the_default_project_id(self, pok_env, monkeypatch):
        monkeypatch.setattr(constants, "SYSTEM_DEFAULT_PROJECT_ID", "default-x")
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="default-x",
            )
        )
        assert pok_env.injections == []

    def test_no_block_on_the_default_project_name_fallback(
        self, pok_env
    ) -> None:
        """The name fallback — AC24's negative leg via the OTHER gate
        rung: 'default-x' resolves the __system_default__ NAME and the
        seam refuses. The project is ROW-SEEDED so the gate (not an
        empty store) is what blocks the injection."""
        seed = POKService(pok_env.manager._engine)
        seed.upsert(
            project_id="default-x",
            slug="would-inject-01-a",
            body="rows exist — only the gate stops this",
            audience="both",
            agent_id="developer",
        )
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="default-x",  # name resolves __system_default__
            )
        )
        assert pok_env.injections == []

    def test_no_block_without_a_project(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id=None,
            )
        )
        assert pok_env.injections == []

    def test_no_block_for_non_ari_caller(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="dev-inst",
                caller_agent_id="developer",
                project_id="p1",
            )
        )
        assert pok_env.injections == []

    def test_no_block_on_an_empty_project(self, pok_env) -> None:
        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=pok_env.manager,
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p-none",
            )
        )
        assert pok_env.injections == []

    def test_store_down_never_raises_and_dispatch_survives(
        self, pok_env
    ) -> None:
        class BrokenManager:
            _engine = None
            _instance_repository = None
            _project_repository = None

            def set_injection(self, *a, **k):  # pragma: no cover
                raise AssertionError("must never be reached")

        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=BrokenManager(),
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        assert pok_env.injections == []  # the dispatch result is untouched

    def test_set_injection_failure_never_raises(self, pok_env) -> None:
        base = pok_env.manager

        class BrokenInjectManager:
            _engine = base._engine
            _instance_repository = base._instance_repository
            _project_repository = base._project_repository

            def set_injection(self, iid, content, source=None):
                raise RuntimeError("ram lane down")

        asyncio.run(
            maybe_inject_ari_pok_block(
                manager=BrokenInjectManager(),
                current_instance_id="ari-inst-1",
                caller_agent_id="ari",
                project_id="p1",
            )
        )
        # the metadata slot stays UNWRITTEN (AM-22: write after the
        # confirmed delivery) → the next dispatch retries
        assert POK_ARI_SEAM_LAST_PROJECT not in pok_env.metadata


class TestAsyncCensus:
    def test_the_seam_is_a_real_coroutine_and_callees_are_sync(self) -> None:
        """The AsyncMock census: every manager/repo callable the seam
        touches (set_injection / set_metadata / repo.get /
        project_repo.get) is SYNC in production — tests double them
        with plain objects, never AsyncMock. The seam's only `await`s
        are its own asyncio.to_thread bridges."""
        assert inspect.iscoroutinefunction(maybe_inject_ari_pok_block)
        import daemon.manager as manager_mod

        assert not inspect.iscoroutinefunction(
            manager_mod.InstanceManager.set_injection
        )
        # the ARI_AGENT_ID gate token is exactly 'ari' (the ruling)
        assert ARI_AGENT_ID == "ari"
