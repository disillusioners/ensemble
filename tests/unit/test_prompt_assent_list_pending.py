"""Phase-1 ``list_pending`` tests (task 1.9).

``repo.list_pending()`` clones ``count_pending``'s CORE-only predicate,
returns the ROWS ordered by ``created_at``, bounded (default 50), with
NO agent filter (DECIDED-2 — the relay lane must be able to approve any
agent's row). The FE's ``GET /api/prompts/versions`` and
``pending-count`` are untouched (separate repo methods, same predicate).
"""

from __future__ import annotations

import pytest

from daemon.repositories.prompt_version.repository import (
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    pv_repo,
    sqlite_engine,
)


def _pending(pv_repo, agent_id, artifact, content):  # noqa: F811
    sub = actor_from_instance(f"inst-{agent_id}", agent_id)
    row = pv_repo.create_draft(
        agent_id=agent_id, version_tag="", artifact=artifact,
        content=content, actor=sub,
    )
    return pv_repo.submit_for_approval(row.version_id, sub)


class TestListPending:
    def test_returns_core_pending_rows_across_agents_ordered(
        self, pv_repo  # noqa: F811
    ):
        a = _pending(pv_repo, "ari", "rule", "ari rule\n")
        b = _pending(pv_repo, "developer", "workflow", "dev workflow\n")
        c = _pending(pv_repo, "ari", "soul", "ari soul\n")

        rows = pv_repo.list_pending()
        ids = [r.version_id for r in rows]
        assert set(ids) == {a.version_id, b.version_id, c.version_id}
        # NO agent filter (DECIDED-2): a cross-agent set came back.
        assert {r.agent_id for r in rows} == {"ari", "developer"}
        # Ordered by created_at (stable, ascending).
        created = [r.created_at for r in rows]
        assert created == sorted(created)

    def test_memory_class_rows_excluded(self, pv_repo):  # noqa: F811
        sub = actor_from_instance("inst-1", "ari")
        pv_repo.create_memory_version(
            agent_id="ari",
            version_tag="",
            artifact="memory",
            content="memory body\n",
            actor=sub,
        )
        _pending(pv_repo, "ari", "rule", "core pending\n")
        rows = pv_repo.list_pending()
        assert [r.artifact for r in rows] == ["rule"]
        assert all(r.artifact_class == "CORE" for r in rows)

    def test_terminal_states_excluded(self, pv_repo):  # noqa: F811
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            AUTH_METHOD_SESSION,
        )

        human = ActorContext(subject="h", auth_method=AUTH_METHOD_SESSION)
        approved = _pending(pv_repo, "ari", "rule", "to approve\n")
        pv_repo.approve(approved.version_id, human)
        rejected = _pending(pv_repo, "ari", "soul", "to reject\n")
        pv_repo.reject(rejected.version_id, human)
        still = _pending(pv_repo, "ari", "tools_note", "still pending\n")

        rows = pv_repo.list_pending()
        assert [r.version_id for r in rows] == [still.version_id]
        assert all(r.state == "pending_approval" for r in rows)

    def test_bounded_by_limit(self, pv_repo):  # noqa: F811
        for i in range(4):
            _pending(pv_repo, "ari", f"artifact{i}", f"body {i}\n")
        assert len(pv_repo.list_pending(limit=2)) == 2
        assert len(pv_repo.list_pending()) == 4

    def test_count_pending_agreement(self, pv_repo):  # noqa: F811
        for i in range(3):
            _pending(pv_repo, "ari", f"artifact{i}", f"body {i}\n")
        assert pv_repo.count_pending() == len(pv_repo.list_pending())

    async def test_service_wrapper(self, pv_repo, sqlite_engine):  # noqa: F811
        row = _pending(pv_repo, "ari", "rule", "svc pending\n")
        svc = PromptVersionService(pv_repo)
        rows = await svc.list_pending()
        assert [r.version_id for r in rows] == [row.version_id]
        counts = await svc.count_pending()
        assert counts == 1
